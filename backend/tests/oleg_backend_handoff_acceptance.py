"""Explicit acceptance checks requiring AI PR #3 plus enriched backend PR #4.

Excluded from default pytest discovery so the AI branch works independently
before backend PR #4 is merged. Run explicitly in the combined checkout:
``python -m pytest backend/tests/oleg_backend_handoff_acceptance.py``.
All checks are offline, including the opt-in CLI test with a replaced provider.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.app.ai import backend_evaluation as evaluation
from backend.app.ai.backend_evaluation_sources import (
    CASE_IDS,
    EMPLOYEE_ID,
    EVENT_A,
    EVENT_B,
    EVENT_C,
    EVENT_D,
    ROLE,
    SKILL_X,
    SKILL_Y,
    TARGET,
    synthetic_sources,
)
from backend.app.ai.config import AISettings
from backend.app.ai.evaluation import _verdict, evaluate_cases
from backend.app.ai.validation import build_result


@pytest.fixture(scope="module")
def cases():
    return evaluation.build_cases()


def factual_json(fact, prefix):
    # Inspection of deterministic backend fixture output, not a runtime schema.
    assert fact.fact.startswith(prefix)
    return json.JSONDecoder().raw_decode(fact.fact[len(prefix) :])[0]


def histories(case):
    return {
        (fact.subject_id, summary["scope"]): (fact, summary)
        for fact in case.context.facts
        if fact.kind == "history"
        for summary in [factual_json(fact, "Recorded participation: ")]
    }


def test_criticality_is_resolved_target_not_lowest_or_current_role(cases):
    case = cases[0]
    sources = {source["source_filename"]: source["content"] for source in synthetic_sources(CASE_IDS[0])}
    catalog = json.loads(sources["skills.json"])
    assert catalog["role_profiles"][0]["role"] == ROLE
    assert catalog["role_profiles"][0]["critical_skills"] == [SKILL_X]
    assert catalog["role_profiles"][1]["role"] == TARGET
    assert catalog["role_profiles"][1]["critical_skills"] == [SKILL_Y]
    goal = next(fact for fact in case.context.facts if fact.kind == "goal")
    assert factual_json(goal, "Resolved career goal: ")["critical_skills"] == [SKILL_Y]
    skills = {skill.skill_id: skill.level for skill in case.context.current_skills}
    assert skills[SKILL_X] == 0 < skills[SKILL_Y] == 3
    gaps = {fact.subject_id: fact.fact for fact in case.context.facts if fact.kind == "gap"}
    assert "critical for target: False" in gaps[SKILL_X]
    assert "critical for target: True" in gaps[SKILL_Y]
    candidates = {item.event_id: item for item in case.context.eligible_candidates}
    assert candidates[EVENT_A].effects[0].delta == 2
    assert candidates[EVENT_B].effects[0].delta == 1
    assert case.expected_top_event_ids == (EVENT_B,)


def test_history_is_imported_and_disjoint_for_event_and_other_format_events(cases):
    case = cases[1]
    facts = histories(case)
    same_event = facts[(EVENT_A, "event")][1]
    other_offline = facts[(EVENT_A, "same_type_format_other_events")][1]
    alternative_event = facts[(EVENT_B, "event")][1]
    alternative_format = facts[(EVENT_B, "same_type_format_other_events")][1]
    global_history = facts[("profile", "profile")][1]
    assert same_event["sample_size"] == other_offline["sample_size"] == 2
    assert same_event["observed_from"] == "2026-08-01"
    assert same_event["observed_to"] == "2026-08-02"
    assert other_offline["observed_from"] == "2026-08-03"
    assert other_offline["observed_to"] == "2026-08-04"
    for summary in (same_event, other_offline):
        assert summary["status_counts"]["dropped"] == 1
        assert summary["status_counts"]["no_show"] == 1
        assert summary["date_sources"] == {"historical_proxy": 2, "completed_at": 0}
    assert alternative_event["sample_size"] == 0
    assert alternative_format["sample_size"] == 1
    assert alternative_format["status_counts"]["completed"] == 1
    assert global_history["sample_size"] == 5
    assert global_history["status_counts"]["dropped"] == 2
    assert global_history["status_counts"]["no_show"] == 2
    assert case.excluded_event_ids == (EVENT_C, EVENT_D)
    a, b = case.context.eligible_candidates
    assert a.event_id == EVENT_A and b.event_id == EVENT_B
    assert a.effects == b.effects
    assert a.effects[0].before == 2  # historical completion predates review
    assert case.expected_top_event_ids == (EVENT_B,)


def test_no_history_requires_neutral_evidence_and_allows_either_equal_option(cases):
    case = cases[2]
    facts = histories(case)
    assert len(facts) == 5
    for fact, summary in facts.values():
        assert summary["sample_size"] == 0
        assert not any(summary["status_counts"].values())
        assert summary["observed_from"] is None
        assert summary["observed_to"] is None
        assert "zero samples, do not establish stable preferences" in fact.fact
    assert set(case.expected_top_event_ids) == {EVENT_A, EVENT_B}
    for event_id in case.expected_top_event_ids:
        result = build_result(case.context, [event_id])
        assert _verdict(case, result) == (True, "passed")
        assert (
            "zero samples, do not establish stable preferences" in result.recommendations[0].explanation.text
        )


@pytest.mark.parametrize("case_index", range(3))
def test_explanations_cite_both_event_and_cohort_history_when_they_fit(cases, case_index):
    case = cases[case_index]
    known = {fact.evidence_id: fact for fact in case.context.facts}
    for candidate in case.context.eligible_candidates:
        result = build_result(case.context, [candidate.event_id])
        explanation = result.recommendations[0].explanation
        cited_history = [
            known[evidence_id]
            for evidence_id in explanation.evidence_ids
            if known[evidence_id].kind == "history"
        ]
        assert len(explanation.text) <= 2000
        assert len(cited_history) == 2
        assert {fact.subject_id for fact in cited_history} == {candidate.event_id}
        assert {factual_json(fact, "Recorded participation: ")["scope"] for fact in cited_history} == {
            "event",
            "same_type_format_other_events",
        }


@pytest.mark.parametrize("remaining_facts", [0, 1])
@pytest.mark.parametrize("missing_subject", [EVENT_A, EVENT_B])
def test_insufficient_backend_enrichment_fails_before_live_configuration_or_network(
    monkeypatch, capsys, remaining_facts, missing_subject
):
    import dotenv

    import backend.app.ai

    original = evaluation.CareerService._context

    def old_backend(self, *args, **kwargs):
        context = original(self, *args, **kwargs)
        related = [
            fact for fact in context.facts if fact.kind == "history" and fact.subject_id == missing_subject
        ]
        removed = {fact.evidence_id for fact in related[remaining_facts:]}
        context.facts = [fact for fact in context.facts if fact.evidence_id not in removed]
        return context

    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported backend reached live configuration or provider.")

    monkeypatch.setattr(evaluation.CareerService, "_context", old_backend)
    monkeypatch.setattr(dotenv, "load_dotenv", forbidden)
    monkeypatch.setattr(AISettings, "from_env", forbidden)
    monkeypatch.setattr(backend.app.ai, "recommend", forbidden)
    with pytest.raises(evaluation.UnsupportedBackend):
        evaluation.build_cases()
    assert evaluation.main(["--live", "--env-file", "unused.env"]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err) == {
        "error": "backend_context_not_enriched",
        "requires": "backend PR #4 event and cohort history facts",
        "synthetic_only": True,
        "quality_evaluated": False,
    }


def test_all_cases_have_anonymized_unmodified_backend_contexts(cases, monkeypatch):
    original = evaluation.CareerService._context
    produced = []

    def capture(self, *args, **kwargs):
        context = original(self, *args, **kwargs)
        produced.append(context.model_dump_json())
        return context

    monkeypatch.setattr(evaluation.CareerService, "_context", capture)
    rebuilt = evaluation.build_cases()
    assert produced == [case.context.model_dump_json() for case in rebuilt]
    assert [case.context for case in rebuilt] == [case.context for case in cases]
    for case in cases:
        serialized = case.context.model_dump_json()
        assert case.context.limit == 1
        assert case.context.state_version == 1
        assert case.context.profile.profile_ref == "anonymous-profile"
        for absent in (EMPLOYEE_ID, "Invented Evaluation Person", "record_id", "expected_top", "rubric"):
            assert absent not in serialized


def test_wrong_lowest_skill_and_repeated_format_choices_fail(cases):
    for case in cases[:2]:
        assert _verdict(case, build_result(case.context, [EVENT_A])) == (False, "unexpected_top_choice")


def test_mocked_engine_receives_only_shared_context_and_never_rubric(cases):
    captured = []

    async def engine(context):
        captured.append(context.model_dump_json())
        return build_result(context, [EVENT_B])

    report = asyncio.run(evaluate_cases(engine, cases))
    assert report["passed_count"] == 3
    assert captured == [case.context.model_dump_json() for case in cases]
    assert "expected_top_event_ids" not in "".join(captured)


def test_default_cli_does_not_load_env_construct_settings_or_call_ai(monkeypatch, capsys):
    import dotenv

    import backend.app.ai

    def forbidden(*args, **kwargs):
        pytest.fail("Offline fixture validation tried to use runtime AI configuration.")

    monkeypatch.setattr(dotenv, "load_dotenv", forbidden)
    monkeypatch.setattr(AISettings, "from_env", forbidden)
    monkeypatch.setattr(backend.app.ai, "recommend", forbidden)
    assert evaluation.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "offline_backend_fixture_validation"
    assert report["quality_evaluated"] is False
    assert report["fixtures_valid"] is True
    assert report["case_count"] == 3
    assert len(report["backend_service_sha256"]) == 64
    assert all(len(context["sha256"]) == 64 for context in report["contexts"])


def test_provenance_is_deterministic_and_has_no_raw_context(cases):
    report = evaluation.provenance(cases)
    assert report == evaluation.provenance(evaluation.build_cases())
    text = json.dumps(report)
    for absent in (EMPLOYEE_ID, "critical_skills", "status_counts", "Resolved career goal"):
        assert absent not in text


def test_live_cli_is_explicit_uses_exported_entry_and_reports_only_safe_metadata(monkeypatch, capsys):
    import dotenv

    import backend.app.ai

    loads = []

    def load_dotenv(**kwargs):
        loads.append(kwargs)

    async def fake_recommend(context):
        return build_result(context, [EVENT_B])

    monkeypatch.setattr(dotenv, "load_dotenv", load_dotenv)
    monkeypatch.setattr(AISettings, "from_env", lambda: SimpleNamespace(model="synthetic-model"))
    monkeypatch.setattr(backend.app.ai, "recommend", fake_recommend)
    assert evaluation.main(["--live", "--env-file", "local-test.env"]) == 0
    assert len(loads) == 1
    assert str(loads[0]["dotenv_path"]) == "local-test.env"
    assert loads[0]["override"] is False
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["mode"] == "live_synthetic_backend_evaluation"
    assert report["model"] == "synthetic-model"
    assert report["passed_count"] == 3
    assert "local-test.env" not in output
    assert "critical_skills" not in output


def test_cli_requires_live_before_env_path(capsys):
    with pytest.raises(SystemExit) as exc:
        evaluation.main(["--env-file", "unused.env"])
    assert exc.value.code == 2
    assert "--env-file requires --live" in capsys.readouterr().err


def test_cli_sanitizes_backend_exceptions(monkeypatch, capsys):
    def broken():
        raise RuntimeError("synthetic-secret-that-must-not-appear")

    monkeypatch.setattr(evaluation, "build_cases", broken)
    assert evaluation.main([]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err) == {"error": "backend_evaluation_failed", "synthetic_only": True}


def test_unknown_source_case_rejected():
    with pytest.raises(ValueError, match="Unknown synthetic backend case"):
        synthetic_sources("external-data-path")
