"""Opt-in recommendation-quality evaluation using wholly synthetic contexts.

Default CLI mode validates fixtures only; it never creates a provider, loads an
.env file, or assigns an AI quality score. ``--live`` explicitly loads the local
environment and exercises the exported recommendation entry point. Reports
contain fixture IDs, selected event IDs, timings and fixed verdict codes only.

The rubric tests choice, not wording: a critical target gap beats a weaker
noncritical skill, equal gains use relevant participation/format evidence, and
an excluded high-gain event must never displace a useful eligible candidate.
These authored contexts alone do not prove backend integration. The separate
backend_evaluation module exercises contexts prepared by the backend from PR #4.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from backend.app.ai.prompts import PROMPT_VERSION
from backend.app.ai.validation import validate_context
from backend.app.contracts import RecommendationContext, RecommendationResult

Recommend = Callable[[RecommendationContext], Awaitable[RecommendationResult]]
DEFAULT_CASES = Path(__file__).with_name("evaluation_cases.json")
_SAFE_FIXTURE_ID = re.compile(r"^[a-z0-9_-]{1,80}$")


@dataclass(frozen=True)
class EvaluationCase:
    """Fixture metadata only; domain models remain the shared Pydantic contracts."""

    case_id: str
    context: RecommendationContext
    expected_top_event_ids: tuple[str, ...]
    requires_enriched_facts: bool
    rubric: str
    excluded_event_ids: tuple[str, ...]


def load_cases(path: Path = DEFAULT_CASES) -> list[EvaluationCase]:
    """Load independent authored scenarios and validate their shared contracts."""
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
        if source["synthetic_only"] is not True or source["schema_version"] != 1:
            raise ValueError
        raw_cases = source["cases"]
        if not isinstance(raw_cases, list) or not raw_cases:
            raise ValueError
        cases: list[EvaluationCase] = []
        seen: set[str] = set()
        for raw in raw_cases:
            case_id = raw["case_id"]
            if not isinstance(case_id, str) or not _SAFE_FIXTURE_ID.fullmatch(case_id) or case_id in seen:
                raise ValueError
            seen.add(case_id)
            context = RecommendationContext.model_validate_json(json.dumps(raw["context"]))
            validate_context(context)
            expected = raw["expected_top_event_ids"]
            excluded = raw.get("excluded_event_ids", [])
            if (
                not isinstance(expected, list)
                or not expected
                or not all(isinstance(x, str) for x in expected)
            ):
                raise ValueError
            if not isinstance(excluded, list) or not all(isinstance(x, str) for x in excluded):
                raise ValueError
            eligible = {candidate.event_id for candidate in context.eligible_candidates}
            if not set(expected) <= eligible or set(excluded) & eligible:
                raise ValueError
            enriched = raw["requires_enriched_facts"]
            rubric = raw["rubric"]
            if not isinstance(enriched, bool) or not isinstance(rubric, str) or not rubric.strip():
                raise ValueError
            cases.append(EvaluationCase(case_id, context, tuple(expected), enriched, rubric, tuple(excluded)))
        return cases
    except (OSError, ValueError, TypeError, KeyError):
        # Pydantic messages can include full facts. Never expose those via CLI.
        raise ValueError("Synthetic evaluation fixtures are invalid or unreadable.") from None


def fixture_report(cases: Sequence[EvaluationCase]) -> dict[str, Any]:
    """Schema/semantic validation is not an evaluation of model quality."""
    return {
        "mode": "offline_fixture_validation",
        "synthetic_only": True,
        "quality_evaluated": False,
        "prompt_version": PROMPT_VERSION,
        "case_count": len(cases),
        "fixtures_valid": True,
        "cases": [
            {"case_id": case.case_id, "requires_enriched_facts": case.requires_enriched_facts}
            for case in cases
        ],
    }


def _verdict(case: EvaluationCase, result: RecommendationResult) -> tuple[bool, str]:
    if result.status != "ok":
        return False, "engine_not_ok"
    selected = [selection.event_id for selection in result.recommendations]
    eligible = {candidate.event_id: candidate for candidate in case.context.eligible_candidates}
    if len(selected) > case.context.limit or not set(selected) <= set(eligible):
        return False, "invalid_selection"
    if selected[0] not in case.expected_top_event_ids:
        return False, "unexpected_top_choice"
    known = {fact.evidence_id: fact for fact in case.context.facts}
    gaps = {gap.skill_id: gap for gap in case.context.gaps}
    profile_subjects = {"profile", case.context.profile.profile_ref}
    for selection in result.recommendations:
        evidence = selection.explanation.evidence_ids
        if len(evidence) != len(set(evidence)):
            return False, "duplicate_evidence"
        if not set(evidence) <= set(known):
            return False, "unsupported_evidence"
        candidate = eligible[selection.event_id]
        affected = {
            effect.skill_id
            for effect in candidate.effects
            if effect.delta > 0
            and effect.skill_id in gaps
            and gaps[effect.skill_id].gap > 0
            and min(effect.after, gaps[effect.skill_id].target_level) > effect.before
        }
        allowed_subjects = {
            "goal": profile_subjects,
            "gap": affected,
            "skill": affected,
            "history": profile_subjects | {candidate.event_id},
            "candidate": {candidate.event_id},
        }
        for evidence_id in evidence:
            fact = known[evidence_id]
            if fact.subject_id not in allowed_subjects[fact.kind]:
                return False, "irrelevant_evidence"
            if fact.kind == "candidate" and evidence_id not in candidate.evidence_ids:
                return False, "unlinked_candidate_evidence"
        if not {"goal", "gap", "history", "candidate"} <= {
            known[evidence_id].kind for evidence_id in evidence
        }:
            return False, "insufficient_evidence"
    return True, "passed"


async def evaluate_cases(
    recommend: Recommend,
    cases: Sequence[EvaluationCase],
    *,
    timeout_seconds: float = 7.0,
) -> dict[str, Any]:
    """Evaluate an injected engine; no provider is constructed by this function.

    ``expected_top_event_ids`` and the rubric are never supplied to the engine.
    Evidence wording is deliberately not matched to a fixture string.
    """
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Evaluation timeout must be positive and finite.")
    reports = []
    for case in cases:
        start = perf_counter()
        selected: list[str] = []
        status = "error"
        try:
            result = await asyncio.wait_for(
                recommend(case.context.model_copy(deep=True)), timeout=timeout_seconds
            )
            # The engine is injectable, including malformed test implementations.
            if not isinstance(result, RecommendationResult):
                raise ValueError("Invalid result type.")
            result = RecommendationResult.model_validate(result.model_dump(warnings=False))
            status = result.status
            eligible_ids = {candidate.event_id for candidate in case.context.eligible_candidates}
            selected = [
                selection.event_id if selection.event_id in eligible_ids else "<unrecognized_event>"
                for selection in result.recommendations
            ]
            passed, verdict = _verdict(case, result)
        except TimeoutError:
            passed, verdict = False, "timeout"
        except Exception:
            # Provider errors can contain requests, personal facts or secrets.
            passed, verdict = False, "call_failed"
        reports.append(
            {
                "case_id": case.case_id,
                "passed": passed,
                "verdict": verdict,
                "status": status,
                "latency_ms": round((perf_counter() - start) * 1000, 2),
                "selected_event_ids": selected,
                "requires_enriched_facts": case.requires_enriched_facts,
            }
        )
    passed_count = sum(report["passed"] for report in reports)
    return {
        "mode": "engine_evaluation",
        "synthetic_only": True,
        "quality_evaluated": True,
        "prompt_version": PROMPT_VERSION,
        "case_count": len(reports),
        "passed_count": passed_count,
        "failed_count": len(reports) - passed_count,
        "cases": reports,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly call AI on synthetic fixtures only.")
    parser.add_argument("--env-file", type=Path, help="Local dotenv path, permitted only with --live.")
    args = parser.parse_args(argv)
    if args.env_file is not None and not args.live:
        parser.error("--env-file requires --live")
    try:
        cases = load_cases()
        if not args.live:
            report = fixture_report(cases)
        else:
            from dotenv import load_dotenv

            # No shell evaluation and no printing values. Existing environment wins.
            env_file = args.env_file or Path.cwd() / ".env"
            load_dotenv(dotenv_path=env_file, override=False)
            from backend.app.ai import recommend
            from backend.app.ai.config import AISettings

            settings = AISettings.from_env()
            report = asyncio.run(evaluate_cases(recommend, cases))
            report["mode"] = "live_synthetic_evaluation"
            report["model"] = settings.model
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report.get("failed_count", 0) else 0
    except Exception:
        print(json.dumps({"error": "evaluation_failed", "synthetic_only": True}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
