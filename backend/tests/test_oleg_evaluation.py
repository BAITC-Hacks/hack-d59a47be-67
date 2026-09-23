"""Evaluation harness tests use fake engines, never a network or API key."""

import asyncio
import json
from pathlib import Path

import pytest

from backend.app.ai import evaluation
from backend.app.ai.prompts import PROMPT_VERSION
from backend.app.ai.validation import build_result
from backend.app.contracts import EvidenceFact, Explanation, RecommendationResult, RecommendationSelection


@pytest.fixture
def cases():
    return evaluation.load_cases()


async def first_candidate(context):
    return build_result(context, [context.eligible_candidates[0].event_id])


async def last_candidate(context):
    return build_result(context, [context.eligible_candidates[-1].event_id])


async def largest_raw_gain(context):
    candidate = max(
        context.eligible_candidates, key=lambda item: sum(effect.delta for effect in item.effects)
    )
    return build_result(context, [candidate.event_id])


def test_three_independent_synthetic_fixtures(cases):
    assert len(cases) == 3
    assert [case.requires_enriched_facts for case in cases] == [True, True, False]
    for case in cases:
        assert case.context.data_version == "synthetic-evaluation-v1"
        assert case.rubric
        assert case.context.limit == 1
        payload = case.context.model_dump_json()
        assert "expected_top_event_ids" not in payload
        assert "requires_enriched_facts" not in payload
        for excluded in case.excluded_event_ids:
            assert excluded not in payload


def test_default_cli_validates_without_env_or_ai_call(monkeypatch, capsys):
    import dotenv

    def forbidden_load(*args, **kwargs):
        pytest.fail("Offline evaluation must not load credentials")

    monkeypatch.setattr(dotenv, "load_dotenv", forbidden_load)
    assert evaluation.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "offline_fixture_validation"
    assert report["quality_evaluated"] is False
    assert report["fixtures_valid"] is True
    assert report["prompt_version"] == PROMPT_VERSION
    assert "model" not in report
    assert "passed_count" not in report
    assert "selected_event_ids" not in report["cases"][0]


def test_offline_rejects_env_file(capsys):
    with pytest.raises(SystemExit) as error:
        evaluation.main(["--env-file", "should-not-be-read.env"])
    assert error.value.code == 2
    assert "requires --live" in capsys.readouterr().err


def test_quality_rubric_rejects_valid_but_bad_choices(cases):
    report = asyncio.run(evaluation.evaluate_cases(first_candidate, cases))
    assert report["failed_count"] == 3
    assert all(case["verdict"] == "unexpected_top_choice" for case in report["cases"])
    assert all(case["status"] == "ok" for case in report["cases"])


def test_simple_largest_gain_cannot_pass_multifactor_rubric(cases):
    report = asyncio.run(evaluation.evaluate_cases(largest_raw_gain, cases))
    assert report["passed_count"] == 1
    assert report["failed_count"] == 2
    assert [case["passed"] for case in report["cases"]] == [False, False, True]


def test_known_choices_validate_harness_without_model_score_claim(cases):
    report = asyncio.run(evaluation.evaluate_cases(last_candidate, cases))
    assert report["mode"] == "engine_evaluation"
    assert report["passed_count"] == 3
    assert all(case["latency_ms"] >= 0 for case in report["cases"])
    serialized = json.dumps(report)
    assert "current_skills" not in serialized
    assert "synthetic-profile" not in serialized
    assert "explanation" not in serialized


def test_explanation_wording_not_used_as_quality_oracle(cases):
    async def paraphrased(context):
        result = await last_candidate(context)
        result.recommendations[0].explanation.text = "Иная формулировка с теми же проверяемыми ссылками."
        return result

    assert asyncio.run(evaluation.evaluate_cases(paraphrased, cases))["passed_count"] == 3


def test_engine_only_receives_context_and_cannot_mutate_fixture(cases):
    original = cases[0].context.model_dump_json()

    async def mutating(context):
        result = await last_candidate(context)
        context.eligible_candidates.clear()
        return result

    report = asyncio.run(evaluation.evaluate_cases(mutating, cases[:1]))
    assert report["passed_count"] == 1
    assert cases[0].context.model_dump_json() == original


def test_unavailable_is_failed_not_automatically_accepted(cases):
    async def unavailable(context):
        return RecommendationResult(status="unavailable", engine="oleg", recommendations=[])

    report = asyncio.run(evaluation.evaluate_cases(unavailable, cases))
    assert report["passed_count"] == 0
    assert all(row["verdict"] == "engine_not_ok" for row in report["cases"])


def test_errors_and_unrecognized_ids_are_redacted(cases):
    async def raises_private(context):
        raise RuntimeError("secret-token-and-private-context")

    report = asyncio.run(evaluation.evaluate_cases(raises_private, cases[:1]))
    assert report["cases"][0]["verdict"] == "call_failed"
    assert "secret-token" not in json.dumps(report)

    async def unknown_id(context):
        return RecommendationResult(
            status="ok",
            engine="oleg",
            recommendations=[
                RecommendationSelection(
                    event_id="secret-token-and-private-context",
                    explanation=Explanation(text="Private fact", evidence_ids=["secret-evidence"]),
                )
            ],
        )

    report = asyncio.run(evaluation.evaluate_cases(unknown_id, cases[:1]))
    assert report["cases"][0]["verdict"] == "invalid_selection"
    assert report["cases"][0]["selected_event_ids"] == ["<unrecognized_event>"]
    assert "secret-token" not in json.dumps(report)


def test_fabricated_evidence_fails_quality_acceptance(cases):
    async def unsupported(context):
        result = await last_candidate(context)
        result.recommendations[0].explanation.evidence_ids = ["invented-fact"]
        return result

    report = asyncio.run(evaluation.evaluate_cases(unsupported, cases[:1]))
    assert report["cases"][0]["verdict"] == "unsupported_evidence"


def test_expected_event_with_known_but_unrelated_gap_fails(cases):
    async def wrong_gap(context):
        result = await last_candidate(context)
        facts = {fact.evidence_id: fact for fact in context.facts}
        wrong_gap_id = next(
            fact.evidence_id
            for fact in context.facts
            if fact.kind == "gap" and fact.subject_id == "SYNTH_DOCUMENTATION"
        )
        explanation = result.recommendations[0].explanation
        explanation.evidence_ids = [
            wrong_gap_id if facts[evidence_id].kind == "gap" else evidence_id
            for evidence_id in explanation.evidence_ids
        ]
        return result

    report = asyncio.run(evaluation.evaluate_cases(wrong_gap, cases[:1]))
    assert report["cases"][0]["selected_event_ids"] == list(cases[0].expected_top_event_ids)
    assert report["cases"][0]["verdict"] == "irrelevant_evidence"
    assert report["failed_count"] == 1


@pytest.mark.parametrize("kind", ["goal", "history", "candidate"])
def test_foreign_subject_cannot_support_expected_event(cases, kind):
    case = cases[0]
    case.context.facts.append(
        EvidenceFact(evidence_id="foreign-fact", kind=kind, subject_id="foreign-subject", fact="Чужой факт.")
    )

    async def wrong_subject(context):
        result = await last_candidate(context)
        facts = {fact.evidence_id: fact for fact in context.facts}
        explanation = result.recommendations[0].explanation
        explanation.evidence_ids = [
            "foreign-fact" if facts[evidence_id].kind == kind else evidence_id
            for evidence_id in explanation.evidence_ids
        ]
        return result

    report = asyncio.run(evaluation.evaluate_cases(wrong_subject, [case]))
    assert report["cases"][0]["verdict"] == "irrelevant_evidence"


def test_candidate_fact_must_be_linked_by_selected_candidate(cases):
    case = cases[0]
    case.context.facts.append(
        EvidenceFact(
            evidence_id="unlinked-fact",
            kind="candidate",
            subject_id=case.expected_top_event_ids[0],
            fact="Дополнительный факт без ссылки кандидата.",
        )
    )

    async def unlinked(context):
        result = await last_candidate(context)
        facts = {fact.evidence_id: fact for fact in context.facts}
        explanation = result.recommendations[0].explanation
        explanation.evidence_ids = [
            "unlinked-fact" if facts[evidence_id].kind == "candidate" else evidence_id
            for evidence_id in explanation.evidence_ids
        ]
        return result

    report = asyncio.run(evaluation.evaluate_cases(unlinked, [case]))
    assert report["cases"][0]["verdict"] == "unlinked_candidate_evidence"


def test_three_categories_without_candidate_evidence_are_insufficient(cases):
    async def missing_candidate(context):
        result = await last_candidate(context)
        facts = {fact.evidence_id: fact for fact in context.facts}
        explanation = result.recommendations[0].explanation
        explanation.evidence_ids = [
            evidence_id for evidence_id in explanation.evidence_ids if facts[evidence_id].kind != "candidate"
        ]
        return result

    report = asyncio.run(evaluation.evaluate_cases(missing_candidate, cases[:1]))
    assert report["cases"][0]["verdict"] == "insufficient_evidence"


def test_duplicate_evidence_is_rejected(cases):
    async def duplicate(context):
        result = await last_candidate(context)
        evidence = result.recommendations[0].explanation.evidence_ids
        evidence.append(evidence[0])
        return result

    report = asyncio.run(evaluation.evaluate_cases(duplicate, cases[:1]))
    assert report["cases"][0]["verdict"] == "duplicate_evidence"


@pytest.mark.parametrize("non_improvement", ["zero_delta", "zero_gap", "target_already_met"])
def test_gap_evidence_requires_positive_target_reduction(cases, non_improvement):
    case = cases[0]
    result = asyncio.run(last_candidate(case.context))
    candidate = case.context.eligible_candidates[-1]
    effect = candidate.effects[0]
    gap = next(gap for gap in case.context.gaps if gap.skill_id == effect.skill_id)
    if non_improvement == "zero_delta":
        effect.delta = 0
    elif non_improvement == "zero_gap":
        gap.gap = 0
    else:
        gap.target_level = effect.before

    async def unsupported_reduction(context):
        return result

    report = asyncio.run(evaluation.evaluate_cases(unsupported_reduction, [case]))
    assert report["cases"][0]["verdict"] == "irrelevant_evidence"


def test_timeout_is_bounded_and_sanitized(cases):
    async def stalled(context):
        await asyncio.sleep(1)
        return await last_candidate(context)

    report = asyncio.run(evaluation.evaluate_cases(stalled, cases[:1], timeout_seconds=0.001))
    assert report["cases"][0]["verdict"] == "timeout"
    assert report["failed_count"] == 1


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_rejected(cases, timeout):
    with pytest.raises(ValueError, match="positive and finite"):
        asyncio.run(evaluation.evaluate_cases(last_candidate, cases[:1], timeout_seconds=timeout))


def test_invalid_fixture_errors_do_not_expose_content(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text('{"private": "secret"}', encoding="utf-8")
    with pytest.raises(ValueError) as error:
        evaluation.load_cases(path)
    assert "secret" not in str(error.value)


def test_cli_live_is_explicit_and_uses_exported_entrypoint(monkeypatch, capsys):
    import dotenv

    import backend.app.ai

    loaded = []
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key-for-isolated-test")
    monkeypatch.setenv("OPENAI_MODEL", "synthetic-model-test")
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "6")
    monkeypatch.setattr(dotenv, "load_dotenv", lambda **kwargs: loaded.append(kwargs))
    monkeypatch.setattr(backend.app.ai, "recommend", last_candidate, raising=False)
    assert evaluation.main(["--live", "--env-file", "synthetic-local.env"]) == 0
    assert loaded == [{"dotenv_path": Path("synthetic-local.env"), "override": False}]
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "live_synthetic_evaluation"
    assert report["model"] == "synthetic-model-test"
    assert report["prompt_version"] == PROMPT_VERSION
    assert report["passed_count"] == 3
    assert "synthetic-key" not in json.dumps(report)


def test_live_metadata_requires_validated_configuration(monkeypatch, capsys):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key-for-isolated-test")
    monkeypatch.setenv("OPENAI_MODEL", "invalid model with private text")
    assert evaluation.main(["--live"]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err) == {"error": "evaluation_failed", "synthetic_only": True}
    assert "private" not in output.err
