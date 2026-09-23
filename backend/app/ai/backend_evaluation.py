"""Opt-in AI quality checks using real backend contexts from synthetic imports.

Requires the enriched backend context from PR #4 (event and cohort history).
Default mode uses temporary SQLite only: no dotenv, credentials or network.
With --live, only the three fixed invented kits below reach the existing AI
entry point. Contexts are obtained from CareerService unchanged. No source kit,
raw fact text, request, provider error, or secret is printed by the CLI.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.ai.backend_evaluation_sources import (
    CASE_IDS,
    EMPLOYEE_ID,
    EVENT_A,
    EVENT_B,
    EVENT_C,
    EVENT_D,
    synthetic_sources,
)
from backend.app.ai.evaluation import EvaluationCase, evaluate_cases, fixture_report
from backend.app.ai.validation import validate_context
from backend.app.ai_adapter import AIAdapter
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.imports import ImportService
from backend.app.service import CareerService

_RUBRICS = (
    "The target's critical gap takes priority over the lowest noncritical skill with a larger raw gain.",
    "Equal gains and workload: repeated offline no_show/dropped favor the self-paced alternative, "
    "supported by separate event and other-event format evidence.",
    "Without history, choose either equally useful eligible activity and cite zero-sample evidence; "
    "do not assume a stable format preference.",
)


class UnsupportedBackend(ValueError):
    """The installed backend cannot supply this evaluation's required evidence."""


def build_cases() -> list[EvaluationCase]:
    """Import fresh fixed kits and derive unmodified contexts in temporary DBs.

    No backend test helper, authored context, or live application DB is used.
    Case metadata and expected choices stay outside the context sent to AI.
    """
    cases = []
    with TemporaryDirectory(prefix="career-quest-synthetic-ai-") as directory:
        for index, case_id in enumerate(CASE_IDS):
            db = Database(Settings(database_path=Path(directory) / f"case-{index}.sqlite3", app_env="test"))
            db.migrate()
            importer = ImportService(db)
            sources = synthetic_sources(case_id)
            preview = importer.preview(sources)
            importer.commit(sources, preview["preview_token"])
            service = CareerService(db, AIAdapter())
            snapshot = service.snapshot(EMPLOYEE_ID)
            context = service._context(snapshot, service._candidates(snapshot), 1)
            # PR #4 emits separate event and other-event cohort facts with the
            # candidate as subject. The old backend only has a profile summary.
            # Do not interpret opaque prose/ID prefixes or score a reduced input
            # as if the new evidence had reached the model.
            for candidate in context.eligible_candidates:
                related_history = [
                    fact
                    for fact in context.facts
                    if fact.kind == "history" and fact.subject_id == candidate.event_id
                ]
                if len(related_history) < 2:
                    raise UnsupportedBackend("Backend PR #4 event and cohort history facts are required.")
            validate_context(context)
            expected = (EVENT_A, EVENT_B) if index == 2 else (EVENT_B,)
            eligible = {candidate.event_id for candidate in context.eligible_candidates}
            if eligible != {EVENT_A, EVENT_B}:
                raise ValueError("Synthetic backend candidate set changed.")
            cases.append(
                EvaluationCase(
                    case_id=case_id,
                    context=context,
                    expected_top_event_ids=expected,
                    requires_enriched_facts=True,
                    rubric=_RUBRICS[index],
                    excluded_event_ids=(EVENT_C, EVENT_D) if index == 1 else (),
                )
            )
    return cases


def provenance(cases: Sequence[EvaluationCase]) -> dict:
    """Safe, deterministic identifiers for the exact context/source code checked."""
    return {
        "context_origin": "synthetic source import -> SQLite -> CareerService._context",
        "backend_service_sha256": hashlib.sha256(
            Path(sys.modules[CareerService.__module__].__file__).read_bytes()
        ).hexdigest(),
        "contexts": [
            {
                "case_id": case.case_id,
                "sha256": hashlib.sha256(case.context.model_dump_json().encode()).hexdigest(),
                "candidate_count": len(case.context.eligible_candidates),
                "fact_count": len(case.context.facts),
            }
            for case in cases
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Call AI using only fixed synthetic backend cases."
    )
    parser.add_argument("--env-file", type=Path, help="Local dotenv path; allowed only with --live.")
    args = parser.parse_args(argv)
    if args.env_file is not None and not args.live:
        parser.error("--env-file requires --live")
    try:
        cases = build_cases()
        if not args.live:
            report = fixture_report(cases)
            report["mode"] = "offline_backend_fixture_validation"
        else:
            from dotenv import load_dotenv

            load_dotenv(dotenv_path=args.env_file or Path.cwd() / ".env", override=False)
            from backend.app.ai import recommend
            from backend.app.ai.config import AISettings

            settings = AISettings.from_env()
            report = asyncio.run(evaluate_cases(recommend, cases))
            report["mode"] = "live_synthetic_backend_evaluation"
            report["model"] = settings.model
        report.update(provenance(cases))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report.get("failed_count", 0) else 0
    except UnsupportedBackend:
        print(
            json.dumps(
                {
                    "error": "backend_context_not_enriched",
                    "requires": "backend PR #4 event and cohort history facts",
                    "synthetic_only": True,
                    "quality_evaluated": False,
                }
            ),
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(json.dumps({"error": "backend_evaluation_failed", "synthetic_only": True}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
