"""Repeated, opt-in latency/quality measurements on fixed synthetic backend inputs.

Offline mode validates inputs without loading credentials or making requests.
Live mode performs 1–10 sequential rounds of three cases, rotates their order,
and retains the first call and every failure. Sample percentiles are descriptive,
not an SLA or a load test. Only approved numeric metadata leaves instrumentation;
requests, responses, credentials and exception messages never enter the report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from typing import Any
from unittest.mock import patch

from backend.app.ai import provider
from backend.app.ai.backend_evaluation import UnsupportedBackend, build_cases, provenance
from backend.app.ai.evaluation import EvaluationCase, Recommend, evaluate_cases, fixture_report
from backend.app.ai.prompts import PROMPT_VERSION

REQUIREMENT_LIMIT_MS = 10_000
EVALUATOR_TIMEOUT_SECONDS = 7.0


def _rounds(value: str) -> int:
    try:
        count = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("rounds must be an integer from 1 to 10") from None
    if not 1 <= count <= 10:
        raise argparse.ArgumentTypeError("rounds must be an integer from 1 to 10")
    return count


def _numeric_usage(raw: bytes) -> dict[str, int]:
    """Extract an explicit allowlist; a missing/malformed value stays unreported."""
    if not isinstance(raw, bytes) or len(raw) > provider.MAX_RESPONSE_BYTES:
        return {}
    try:
        envelope = json.loads(raw)
        usage = envelope.get("usage") if isinstance(envelope, dict) else None
        if not isinstance(usage, dict):
            return {}
        details = usage.get("input_tokens_details")
        candidates = {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cached_tokens": details.get("cached_tokens") if isinstance(details, dict) else None,
        }
        return {
            name: value
            for name, value in candidates.items()
            if type(value) is int and 0 <= value <= 1_000_000_000
        }
    except (ValueError, TypeError, RecursionError):
        return {}


@contextmanager
def _measure_provider() -> Iterator[dict[str, Any]]:
    """Temporary, sequential CLI instrumentation; not a production metrics API.

    The existing provider keeps its timeout, retry policy and bounded body reader.
    Wrappers never retain request/response objects and restore originals on exit.
    """
    import httpx

    metrics: dict[str, Any] = {"provider_calls": 0, "http_requests": 0}
    original_select = provider.AsyncOpenAIProvider.select_events
    original_selection = provider._selection
    original_send = httpx.AsyncClient.send

    async def select(self, *args, **kwargs):
        metrics["provider_calls"] += 1
        start = perf_counter()
        try:
            return await original_select(self, *args, **kwargs)
        except provider.ProviderError as error:
            metrics["provider_error"] = error.code
            raise
        except asyncio.CancelledError:
            # Engine deadline cancellation is observed before it becomes unavailable.
            # External task cancellation still propagates and creates no report.
            metrics["provider_cancelled"] = True
            raise
        finally:
            metrics["provider_ms"] = metrics.get("provider_ms", 0.0) + (perf_counter() - start) * 1000

    async def send(self, request, *args, **kwargs):
        metrics["http_requests"] += 1
        try:
            metrics["request_bytes"] = metrics.get("request_bytes", 0) + len(request.content)
        except httpx.RequestNotRead:
            pass
        start = perf_counter()
        try:
            return await original_send(self, request, *args, **kwargs)
        finally:
            # The provider calls send(stream=True): this ends at response headers,
            # not after the full body. Failed attempts are still counted.
            metrics["network_to_headers_ms"] = (
                metrics.get("network_to_headers_ms", 0.0) + (perf_counter() - start) * 1000
            )

    def selection(raw, *args, **kwargs):
        if isinstance(raw, bytes) and len(raw) <= provider.MAX_RESPONSE_BYTES:
            metrics["response_bytes"] = len(raw)
            metrics.update(_numeric_usage(raw))
        return original_selection(raw, *args, **kwargs)

    with (
        patch.object(provider.AsyncOpenAIProvider, "select_events", select),
        patch.object(provider, "_selection", selection),
        patch.object(httpx.AsyncClient, "send", send),
    ):
        yield metrics


def _distribution(values: Sequence[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "median_ms": round(statistics.median(ordered), 2) if ordered else None,
        "sample_p95_ms": round(ordered[math.ceil(0.95 * len(ordered)) - 1], 2) if ordered else None,
        "max_ms": round(ordered[-1], 2) if ordered else None,
    }


def summarize(attempts: Sequence[dict[str, Any]]) -> dict[str, Any]:
    successful = [row for row in attempts if row["status"] == "ok"]
    return {
        "attempt_count": len(attempts),
        "successful_response_count": len(successful),
        "passed_count": sum(row["passed"] for row in attempts),
        "failed_count": sum(not row["passed"] for row in attempts),
        "unavailable_count": sum(row["status"] == "unavailable" for row in attempts),
        "timeout_count": sum(row["timed_out"] for row in attempts),
        "above_requirement_limit_count": sum(row["total_ms"] > REQUIREMENT_LIMIT_MS for row in attempts),
        "total_latency_all_attempts": _distribution([row["total_ms"] for row in attempts]),
        "total_latency_successful_responses": _distribution([row["total_ms"] for row in successful]),
        "provider_latency_all_observed_calls": _distribution(
            [row["provider_ms"] for row in attempts if "provider_ms" in row]
        ),
        "provider_latency_successful_responses": _distribution(
            [row["provider_ms"] for row in successful if "provider_ms" in row]
        ),
        "network_to_headers_all_observed_calls": _distribution(
            [row["network_to_headers_ms"] for row in attempts if "network_to_headers_ms" in row]
        ),
    }


async def evaluate_latency(
    recommend: Recommend,
    cases: Sequence[EvaluationCase],
    *,
    rounds: int = 3,
    timeout_seconds: float = EVALUATOR_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Measure an injected engine; expectations/rubrics never enter its context."""
    if type(rounds) is not int or not 1 <= rounds <= 10 or not 1 <= len(cases) <= 3:
        raise ValueError("Evaluation requires 1–3 cases and 1–10 rounds.")
    attempts: list[dict[str, Any]] = []
    for round_index in range(rounds):
        offset = round_index % len(cases)
        ordered = list(cases[offset:]) + list(cases[:offset])
        for case in ordered:
            with _measure_provider() as metrics:
                result = await evaluate_cases(recommend, [case], timeout_seconds=timeout_seconds)
            row = result["cases"][0]
            row["total_ms"] = row.pop("latency_ms")
            row.update(metrics)
            if "provider_ms" in row:
                row["provider_ms"] = round(row["provider_ms"], 2)
                row["local_overhead_ms"] = round(max(0.0, row["total_ms"] - row["provider_ms"]), 2)
            if "network_to_headers_ms" in row:
                row["network_to_headers_ms"] = round(row["network_to_headers_ms"], 2)
            row["timed_out"] = (
                row["verdict"] == "timeout"
                or row.get("provider_error") == "timeout"
                or (row.get("provider_cancelled", False) and row["status"] == "unavailable")
            )
            row["round"] = round_index + 1
            row["attempt"] = len(attempts) + 1
            row["first_call"] = not attempts
            attempts.append(row)
    return {
        "mode": "live_synthetic_backend_latency",
        "synthetic_only": True,
        "quality_evaluated": True,
        "prompt_version": PROMPT_VERSION,
        "case_count": len(cases),
        "rounds": rounds,
        "requirement_limit_ms": REQUIREMENT_LIMIT_MS,
        "evaluator_timeout_ms": timeout_seconds * 1000,
        "measurement_scope": "Sequential AI entry point; excludes HTTP backend/frontend and context preparation.",
        "percentile_method": "nearest-rank; includes first call; sample p95 is not an SLA",
        "cold_call_note": "First process call retained; provider cache/cold infrastructure state is unknown.",
        "summary": summarize(attempts),
        "attempts": attempts,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Call AI only with fixed synthetic contexts.")
    parser.add_argument("--env-file", type=Path, help="Local dotenv file; permitted only with --live.")
    parser.add_argument("--rounds", type=_rounds, default=3, help="Sequential rounds, 1–10 (default: 3).")
    args = parser.parse_args(argv)
    if args.env_file is not None and not args.live:
        parser.error("--env-file requires --live")
    try:
        # Reject an old backend before opening a dotenv or contacting a provider.
        cases = build_cases()
        if not args.live:
            report = fixture_report(cases)
            report.update(
                mode="offline_backend_latency_fixture_validation",
                rounds=args.rounds,
                planned_attempt_count=len(cases) * args.rounds,
                latency_measured=False,
            )
        else:
            from dotenv import load_dotenv

            from backend.app.ai import recommend
            from backend.app.ai.config import DEFAULT_MODEL, AISettings

            load_dotenv(dotenv_path=args.env_file or Path.cwd() / ".env", override=False)
            settings = AISettings.from_env()
            if settings.model != DEFAULT_MODEL:
                raise ValueError("The benchmark requires the pinned model.")
            report = asyncio.run(evaluate_latency(recommend, cases, rounds=args.rounds))
            report["model"] = settings.model
            report["ai_timeout_ms"] = settings.timeout_seconds * 1000
        report.update(provenance(cases))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report.get("summary", {}).get("failed_count", 0) else 0
    except UnsupportedBackend:
        error = "backend_context_not_enriched"
    except Exception:
        error = "latency_evaluation_failed"
    print(
        json.dumps({"error": error, "synthetic_only": True, "quality_evaluated": False}),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
