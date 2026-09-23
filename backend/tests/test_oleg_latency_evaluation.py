"""Latency evaluator checks are fully offline and use invented contexts only."""

import asyncio
import json

import httpx
import pytest

from backend.app.ai import latency_evaluation as latency
from backend.app.ai import provider
from backend.app.ai.config import AISettings
from backend.app.ai.evaluation import load_cases
from backend.app.ai.service import RecommendationEngine
from backend.app.ai.validation import build_result
from backend.app.contracts import RecommendationResult


@pytest.fixture
def cases():
    return load_cases()


@pytest.fixture
def offline_cli(monkeypatch, cases):
    monkeypatch.setattr(latency, "build_cases", lambda: cases)
    monkeypatch.setattr(latency, "provenance", lambda _cases: {"contexts": []})


def test_offline_cli_does_not_load_credentials_or_call_engine(monkeypatch, capsys, offline_cli):
    import dotenv

    import backend.app.ai

    def forbidden(*args, **kwargs):
        pytest.fail("Offline benchmark reached credentials or engine")

    monkeypatch.setattr(dotenv, "load_dotenv", forbidden)
    monkeypatch.setattr(AISettings, "from_env", forbidden)
    monkeypatch.setattr(backend.app.ai, "recommend", forbidden)
    assert latency.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["quality_evaluated"] is False
    assert report["latency_measured"] is False
    assert report["planned_attempt_count"] == 9
    assert "summary" not in report


@pytest.mark.parametrize("rounds", ["0", "11", "-1", "NaN", "1.5"])
def test_cli_rejects_unbounded_or_noninteger_rounds(rounds, monkeypatch):
    monkeypatch.setattr(latency, "build_cases", lambda: pytest.fail("Invalid args reached fixtures"))
    with pytest.raises(SystemExit) as error:
        latency.main(["--rounds", rounds])
    assert error.value.code == 2


def test_cli_rejects_offline_env_file():
    with pytest.raises(SystemExit) as error:
        latency.main(["--env-file", "never-open.env"])
    assert error.value.code == 2


def test_old_backend_fails_before_loading_env(monkeypatch, capsys):
    import dotenv

    def unsupported():
        raise latency.UnsupportedBackend("private-context-in-error")

    monkeypatch.setattr(latency, "build_cases", unsupported)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda **kwargs: pytest.fail("Read credentials"))
    assert latency.main(["--live"]) == 2
    captured = capsys.readouterr()
    assert "private-context" not in captured.err
    assert json.loads(captured.err)["error"] == "backend_context_not_enriched"


def test_cli_redacts_configuration_errors(monkeypatch, capsys, offline_cli):
    import dotenv

    def broken(*args, **kwargs):
        raise RuntimeError("unit-secret-DO-NOT-PRINT")

    monkeypatch.setattr(dotenv, "load_dotenv", broken)
    assert latency.main(["--live"]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "unit-secret" not in captured.err
    assert json.loads(captured.err)["error"] == "latency_evaluation_failed"


def test_round_order_rotates_and_first_call_is_retained(cases):
    seen = []
    originals = [case.context.model_dump_json() for case in cases]
    by_context = {case.context.model_dump_json(): case for case in cases}

    async def recommend(context):
        case = by_context[context.model_dump_json()]
        seen.append(case.case_id)
        assert "expected_top_event_ids" not in context.model_dump_json()
        assert case.rubric not in context.model_dump_json()
        result = build_result(context, [case.expected_top_event_ids[0]])
        context.eligible_candidates.clear()
        return result

    report = asyncio.run(latency.evaluate_latency(recommend, cases, rounds=3))
    ids = [case.case_id for case in cases]
    assert seen == ids + ids[1:] + ids[:1] + ids[2:] + ids[:2]
    assert [row["first_call"] for row in report["attempts"]] == [True] + [False] * 8
    assert report["summary"]["attempt_count"] == report["summary"]["passed_count"] == 9
    assert [case.context.model_dump_json() for case in cases] == originals
    assert "not an SLA" in report["percentile_method"]


@pytest.mark.parametrize("rounds", [True, False, 0, 11, 1.2])
def test_programmatic_bounds_prevent_extra_network_calls(cases, rounds):
    async def forbidden(context):
        pytest.fail("Invalid rounds called engine")

    with pytest.raises(ValueError):
        asyncio.run(latency.evaluate_latency(forbidden, cases, rounds=rounds))


def test_summary_keeps_failures_and_distinguishes_quality_from_availability():
    attempts = [
        {"status": "ok", "passed": True, "timed_out": False, "total_ms": 10, "provider_ms": 8},
        {"status": "ok", "passed": False, "timed_out": False, "total_ms": 30, "provider_ms": 28},
        {"status": "unavailable", "passed": False, "timed_out": True, "total_ms": 6001},
        {"status": "error", "passed": False, "timed_out": False, "total_ms": 10001},
    ]
    result = latency.summarize(attempts)
    assert result["attempt_count"] == 4
    assert result["successful_response_count"] == 2
    assert result["passed_count"] == 1
    assert result["failed_count"] == 3
    assert result["timeout_count"] == result["unavailable_count"] == 1
    assert result["above_requirement_limit_count"] == 1
    assert result["total_latency_all_attempts"] == {
        "count": 4,
        "median_ms": 3015.5,
        "sample_p95_ms": 10001,
        "max_ms": 10001,
    }
    assert result["total_latency_successful_responses"]["median_ms"] == 20
    assert result["provider_latency_all_observed_calls"]["count"] == 2
    assert latency._distribution(list(range(1, 21)))["sample_p95_ms"] == 19
    assert latency._distribution([])["sample_p95_ms"] is None


def test_exception_and_outer_timeout_remain_in_attempts(cases):
    count = 0

    async def failing(context):
        nonlocal count
        count += 1
        if count == 1:
            raise RuntimeError("unit-secret-DO-NOT-PRINT")
        if count == 2:
            await asyncio.sleep(1)
        return RecommendationResult(status="unavailable", engine="oleg", recommendations=[])

    report = asyncio.run(latency.evaluate_latency(failing, cases, rounds=1, timeout_seconds=0.001))
    assert [row["verdict"] for row in report["attempts"]] == ["call_failed", "timeout", "engine_not_ok"]
    assert report["summary"]["attempt_count"] == report["summary"]["failed_count"] == 3
    assert report["summary"]["timeout_count"] == 1
    assert "unit-secret" not in json.dumps(report)


def test_provider_timeout_hidden_by_engine_is_counted(cases, monkeypatch):
    async def timeout(self, *args, **kwargs):
        raise provider.ProviderError("timeout")

    monkeypatch.setattr(provider.AsyncOpenAIProvider, "select_events", timeout)
    engine = RecommendationEngine(provider.AsyncOpenAIProvider("unit-secret", "model"))
    report = asyncio.run(latency.evaluate_latency(engine.recommend, cases[:1], rounds=1))
    assert report["summary"]["unavailable_count"] == report["summary"]["timeout_count"] == 1
    assert report["attempts"][0]["provider_error"] == "timeout"
    assert report["attempts"][0]["provider_calls"] == 1


def test_engine_deadline_cancellation_is_counted(cases, monkeypatch):
    async def slow(self, *args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(provider.AsyncOpenAIProvider, "select_events", slow)
    engine = RecommendationEngine(provider.AsyncOpenAIProvider("unit-secret", "model"), 0.001)
    report = asyncio.run(latency.evaluate_latency(engine.recommend, cases[:1], rounds=1))
    assert report["summary"]["unavailable_count"] == report["summary"]["timeout_count"] == 1
    assert report["attempts"][0]["provider_cancelled"] is True


def test_external_cancellation_propagates_and_restores_wrappers(cases, monkeypatch):
    started = asyncio.Event()

    async def slow(self, *args, **kwargs):
        started.set()
        await asyncio.sleep(1)

    monkeypatch.setattr(provider.AsyncOpenAIProvider, "select_events", slow)
    original_send = httpx.AsyncClient.send
    original_selection = provider._selection
    engine = RecommendationEngine(provider.AsyncOpenAIProvider("unit-secret", "model"))

    async def cancel_run():
        task = asyncio.create_task(latency.evaluate_latency(engine.recommend, cases[:1], rounds=1))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_run())
    assert provider.AsyncOpenAIProvider.select_events is slow
    assert provider._selection is original_selection
    assert httpx.AsyncClient.send is original_send


def test_numeric_usage_rejects_untrusted_types_and_ignores_free_text():
    raw = json.dumps(
        {
            "usage": {
                "input_tokens": True,
                "output_tokens": -1,
                "input_tokens_details": {"cached_tokens": "unit-secret"},
                "unexpected": "private context",
            }
        }
    ).encode()
    assert latency._numeric_usage(raw) == {}
    assert latency._numeric_usage(b"invalid unit-secret") == {}
    assert latency._numeric_usage(b"x" * (provider.MAX_RESPONSE_BYTES + 1)) == {}


def test_real_provider_instrumentation_collects_only_safe_counts_and_restores(cases):
    request_sizes = []

    async def handler(request):
        request_sizes.append(len(request.content))
        body = json.loads(request.content)
        schema = body["text"]["format"]["schema"]["properties"]
        # The identical harness also runs against the baseline event-ID wire format.
        if "event_indices" in schema:
            choice = {"event_indices": [len(schema["event_indices"]["items"]["enum"]) - 1]}
        else:
            choice = {"event_ids": [schema["event_ids"]["items"]["enum"][-1]]}
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(choice)}],
                    }
                ],
                "usage": {
                    "input_tokens": 1234,
                    "output_tokens": 12,
                    "input_tokens_details": {"cached_tokens": 1024},
                    "private": "unit-secret-DO-NOT-PRINT",
                },
            },
        )

    original_select = provider.AsyncOpenAIProvider.select_events
    original_send = httpx.AsyncClient.send
    original_selection = provider._selection
    instance = provider.AsyncOpenAIProvider("unit-secret", "model", transport=httpx.MockTransport(handler))
    report = asyncio.run(latency.evaluate_latency(RecommendationEngine(instance).recommend, cases, rounds=1))
    assert report["summary"]["passed_count"] == 3
    for row, size in zip(report["attempts"], request_sizes, strict=True):
        assert row["provider_calls"] == row["http_requests"] == 1
        assert row["request_bytes"] == size
        assert row["response_bytes"] > 0
        assert (row["input_tokens"], row["output_tokens"], row["cached_tokens"]) == (1234, 12, 1024)
        assert row["total_ms"] >= row["provider_ms"] >= row["network_to_headers_ms"] >= 0
        assert row["local_overhead_ms"] >= 0
    assert "unit-secret" not in json.dumps(report)
    assert "Authorization" not in json.dumps(report)
    assert provider.AsyncOpenAIProvider.select_events is original_select
    assert provider._selection is original_selection
    assert httpx.AsyncClient.send is original_send
