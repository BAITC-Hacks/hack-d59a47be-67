#!/usr/bin/env python3
"""Check schema artifacts and FIX_ examples, not production recommendation input.

These checks make the architecture examples reproducible. They do not implement
the runtime semantic validator described in docs/ai-recommendations.md.
Run with the development dependencies installed: python scripts/check_contracts.py
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"


def load_json(relative_path: str) -> dict:
    return json.loads((CONTRACTS / relative_path).read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    """Keep fixture assertions enabled even when Python runs with -O."""
    if not condition:
        raise AssertionError(message)


def check_fixture_semantics(context: dict, result: dict) -> None:
    """Check only the published, wholly synthetic input/output example pair."""
    for field in ("context_id", "data_revision", "policy_version"):
        require(result[field] == context[field], f"Mismatched {field}")

    facts = {fact["evidence_id"]: fact for fact in context["evidence"]}
    candidates = {item["event_id"]: item for item in context["candidates"]}
    require(len(facts) == len(context["evidence"]), "Duplicate evidence IDs")
    require(len(candidates) == len(context["candidates"]), "Duplicate candidate IDs")

    for rank, recommendation in enumerate(result["recommendations"], start=1):
        require(recommendation["rank"] == rank, "Non-contiguous recommendation ranks")
        candidate = candidates[recommendation["event_id"]]
        require(
            set(recommendation["evidence_ids"]) <= set(candidate["evidence_ids"]),
            "Recommendation references evidence outside its candidate",
        )
        selected_facts = [facts[key] for key in recommendation["evidence_ids"]]
        require(
            all(fact["event_id"] == recommendation["event_id"] for fact in selected_facts),
            "Evidence belongs to a different event",
        )
        require(
            len({fact["category"] for fact in selected_facts}) >= 3,
            "Recommendation has fewer than three factor categories",
        )

    first = candidates[result["recommendations"][0]["event_id"]]
    require(first["features"]["critical_gap_gain"] > 0, "Fixture must address critical gap")

    status_fields = ("completed", "in_progress", "dropped", "no_show", "declined", "overdue")
    for fact in context["evidence"]:
        if fact["category"] == "history":
            require(
                sum(fact[key] for key in status_fields) == fact["sample_size"],
                "History sample size does not match status counts",
            )
        if fact["category"] == "skill_gap":
            require(
                fact["gap"] == max(0, fact["required_level"] - fact["current_level"]),
                "Incorrect fixture skill gap",
            )
            require(
                fact["target_gap_reduction"] == min(fact["gap"], fact["effective_gain"]),
                "Incorrect fixture gap reduction",
            )

    for candidate in context["candidates"]:
        candidate_facts = [facts[key] for key in candidate["evidence_ids"]]
        history = next(fact for fact in candidate_facts if fact["category"] == "history")
        expected_history_fit = (
            history["completed"] - history["dropped"] - history["no_show"] - history["declined"]
        ) / (history["sample_size"] + 2)
        require(candidate["features"]["history_fit"] == expected_history_fit, "History fit mismatch")
        require(
            candidate["features"]["effort_cost"] == min(candidate["duration_hours"] / 40, 1),
            "Effort cost mismatch",
        )
        for gain in candidate["projected_gains"]:
            require(
                gain["result_level"] - gain["current_level"] == gain["effective_gain"],
                "Projected gain mismatch",
            )
            require(
                gain["current_level"] == context["employee"]["current_skills"][gain["skill_id"]],
                "Projected current skill mismatch",
            )


def main() -> None:
    context_schema = load_json("recommendation-context.schema.json")
    result_schema = load_json("recommendation-result.schema.json")
    context = load_json("examples/recommendation-context.json")
    result = load_json("examples/recommendation-result.json")

    # Schema self-validation is separate from the 16 fixture check groups below.
    Draft202012Validator.check_schema(context_schema)
    Draft202012Validator.check_schema(result_schema)
    context_validator = Draft202012Validator(context_schema, format_checker=FormatChecker())
    result_validator = Draft202012Validator(result_schema, format_checker=FormatChecker())

    context_validator.validate(context)
    result_validator.validate(result)
    checks = 2

    for state in ("target_satisfied", "no_target", "no_candidates", "insufficient_evidence"):
        empty_context = copy.deepcopy(context)
        empty_context.update(planning_state=state, candidates=[], evidence=[])
        if state == "no_target":
            empty_context["target"] = None
            empty_context["employee"]["grade"] = "Lead"
        if state == "target_satisfied":
            empty_context["employee"]["current_skills"] = empty_context["target"]["required_skills"].copy()
        context_validator.validate(empty_context)

        empty_result = copy.deepcopy(result)
        empty_result.update(
            status="empty", source="none", fallback_reason=None, empty_reason=state, recommendations=[]
        )
        result_validator.validate(empty_result)
        checks += 2

    malformed = copy.deepcopy(result)
    malformed["recommendations"][0]["explanation"] = "Untrusted narrative"
    require(not result_validator.is_valid(malformed), "Unexpected narrative must be rejected")
    checks += 1

    malformed = copy.deepcopy(result)
    malformed["source"] = "llm"
    require(not result_validator.is_valid(malformed), "LLM source cannot carry fallback reason")
    checks += 1

    malformed = copy.deepcopy(result)
    malformed["recommendations"][0]["evidence_ids"] = malformed["recommendations"][0]["evidence_ids"][:2]
    require(not result_validator.is_valid(malformed), "Two evidence references must be rejected")
    checks += 1

    malformed = copy.deepcopy(context)
    malformed["as_of_date"] = "2026-99-01"
    require(not context_validator.is_valid(malformed), "Invalid calendar date must be rejected")
    checks += 1

    model_validator = Draft202012Validator(
        {
            "$schema": result_schema["$schema"],
            "$ref": "#/$defs/modelOutput",
            "$defs": result_schema["$defs"],
        }
    )
    model_validator.validate(
        {"context_id": result["context_id"], "recommendations": result["recommendations"]}
    )
    checks += 1

    check_fixture_semantics(context, result)
    checks += 1
    print(f"PASS: both Draft 2020-12 schemas and {checks} artifact/fixture check groups.")
    print("Production semantic validation and live AI behavior are not implemented or tested here.")


if __name__ == "__main__":
    main()
