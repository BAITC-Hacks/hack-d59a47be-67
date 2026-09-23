#!/usr/bin/env python3
"""Check generated schemas and synthetic examples against the canonical Python models.

Run ``.venv/bin/python scripts/check_contracts.py --write`` after an approved
change to backend/app/contracts.py; the default invocation only reads files.
No independent JSON-schema definitions or live AI calls are maintained here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.contracts import RecommendationContext, RecommendationResult  # noqa: E402

CONTRACTS = ROOT / "contracts"
GENERATED = (
    ("recommendation-context", "ai_context", RecommendationContext),
    ("recommendation-result", "ai_result", RecommendationResult),
)


def load_json(relative_path: str) -> dict:
    return json.loads((CONTRACTS / relative_path).read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    """Keep checks enabled even when Python runs with -O."""
    if not condition:
        raise AssertionError(message)


def schema_for(model: type[RecommendationContext] | type[RecommendationResult]) -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$comment": f"Generated from backend.app.contracts.{model.__name__}; do not edit by hand.",
        **model.model_json_schema(),
    }


def check_fixture_semantics(context: RecommendationContext, result: RecommendationResult) -> None:
    """Check the published synthetic pair without inventing another result contract."""
    require(len(result.recommendations) <= context.limit, "Result exceeds context limit")
    candidates = {item.event_id for item in context.eligible_candidates}
    evidence = {fact.evidence_id for fact in context.facts}
    for recommendation in result.recommendations:
        require(recommendation.event_id in candidates, "Result selects an unknown event")
        require(
            set(recommendation.explanation.evidence_ids) <= evidence,
            "Result references unknown evidence",
        )
    if result.status == "no_candidates":
        require(not candidates, "no_candidates contradicts the supplied candidates")


def write_generated() -> None:
    for stem, fixture_name, model in GENERATED:
        source = CONTRACTS / "examples" / f"{fixture_name}.synthetic.json"
        model.model_validate_json(source.read_text(encoding="utf-8"))
        (CONTRACTS / f"{stem}.schema.json").write_text(
            json.dumps(schema_for(model), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        # Keep pre-existing consumer paths as generated aliases, never independent contracts.
        (CONTRACTS / "examples" / f"{stem}.json").write_text(
            source.read_text(encoding="utf-8"), encoding="utf-8"
        )


def check() -> int:
    checks = 0
    for stem, fixture_name, model in GENERATED:
        require(
            load_json(f"{stem}.schema.json") == schema_for(model),
            f"Stale generated schema: {stem}; run scripts/check_contracts.py --write",
        )
        payload = load_json(f"examples/{fixture_name}.synthetic.json")
        model.model_validate(payload)
        alias = load_json(f"examples/{stem}.json")
        model.model_validate(alias)
        require(alias == payload, f"Stale generated example alias: {stem}")
        checks += 3
    for fixture_name in ("ai_not_configured", "ai_no_candidates"):
        RecommendationResult.model_validate(load_json(f"examples/{fixture_name}.synthetic.json"))
        checks += 1
    context = RecommendationContext.model_validate(load_json("examples/ai_context.synthetic.json"))
    result = RecommendationResult.model_validate(load_json("examples/ai_result.synthetic.json"))
    check_fixture_semantics(context, result)
    return checks + 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Regenerate schemas and example aliases")
    args = parser.parse_args()
    if args.write:
        write_generated()
    print(f"PASS: {check()} generated-schema, canonical-fixture and reference checks.")
    print("Schema source: backend/app/contracts.py. No live AI call was made.")


if __name__ == "__main__":
    main()
