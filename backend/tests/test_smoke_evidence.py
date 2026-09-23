"""Smoke assertions reject invalid citations and technical text without any network calls."""

import pytest

from backend.app import explanations
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.service import CareerService
from backend.tests.test_workflow import EMPLOYEE, EVENT, commit_batch, source_batch
from scripts.smoke import assert_recommendation_evidence


@pytest.fixture
def smoke_card(tmp_path):
    database = Database(Settings(database_path=tmp_path / "smoke-evidence.sqlite3", app_env="test"))
    database.migrate()
    commit_batch(database, source_batch())
    service = CareerService(database, None)
    snapshot = service.snapshot(EMPLOYEE)
    context, display_facts = service._prepare_context(snapshot, service._candidates(snapshot), 3)
    evidence = [
        fact
        for fact in context.facts
        if fact.kind == "goal"
        or (fact.kind == "gap" and fact.subject_id == "WORKFLOW_HARD")
        or (fact.kind in {"history", "candidate"} and fact.subject_id == EVENT)
    ]
    card = {
        "event_id": EVENT,
        "explanation": {
            "evidence_ids": [fact.evidence_id for fact in evidence],
            "text": explanations.render(evidence, display_facts),
        },
    }
    return card, context, display_facts


def test_smoke_accepts_five_citations_with_both_history_scopes_and_russian_text(smoke_card):
    card, context, display_facts = smoke_card
    assert len(card["explanation"]["evidence_ids"]) == 5
    assert "Ваша цель" in card["explanation"]["text"]
    assert "status_counts" not in card["explanation"]["text"]
    assert_recommendation_evidence(card, context, display_facts)


@pytest.mark.parametrize("mutation", ["duplicate", "unknown_id", "unknown_event"])
def test_smoke_rejects_invalid_identifiers(smoke_card, mutation):
    card, context, display_facts = smoke_card
    ids = card["explanation"]["evidence_ids"]
    if mutation == "duplicate":
        ids.append(ids[0])
    elif mutation == "unknown_id":
        ids.append("SYNTH_UNKNOWN_EVIDENCE")
    else:
        card["event_id"] = "SYNTH_UNKNOWN_EVENT"
    with pytest.raises(AssertionError):
        assert_recommendation_evidence(card, context, display_facts)


@pytest.mark.parametrize("kind", ["goal", "gap", "history", "candidate"])
def test_smoke_rejects_missing_required_category(smoke_card, kind):
    card, context, display_facts = smoke_card
    facts = {fact.evidence_id: fact for fact in context.facts}
    card["explanation"]["evidence_ids"] = [
        evidence_id for evidence_id in card["explanation"]["evidence_ids"] if facts[evidence_id].kind != kind
    ]
    with pytest.raises(AssertionError):
        assert_recommendation_evidence(card, context, display_facts)


@pytest.mark.parametrize("kind", ["gap", "history", "candidate"])
def test_smoke_rejects_evidence_for_another_candidate_or_unaffected_skill(smoke_card, kind):
    card, context, display_facts = smoke_card
    foreign = next(
        fact
        for fact in context.facts
        if fact.kind == kind
        and fact.subject_id == ("WORKFLOW_SOFT" if kind == "gap" else "WORKFLOW_SOFT_COURSE")
    )
    card["explanation"]["evidence_ids"].append(foreign.evidence_id)
    with pytest.raises(AssertionError):
        assert_recommendation_evidence(card, context, display_facts)


def test_smoke_requires_both_history_facts_in_the_small_fixture(smoke_card):
    card, context, display_facts = smoke_card
    history = next(fact for fact in context.facts if fact.kind == "history" and fact.subject_id == EVENT)
    card["explanation"]["evidence_ids"].remove(history.evidence_id)
    assert len(card["explanation"]["evidence_ids"]) == 4
    with pytest.raises(AssertionError):
        assert_recommendation_evidence(card, context, display_facts)


@pytest.mark.parametrize("text_source", ["machine_facts", "unsupported_claim"])
def test_smoke_rejects_technical_or_invented_public_text(smoke_card, text_source):
    card, context, display_facts = smoke_card
    facts = {fact.evidence_id: fact for fact in context.facts}
    card["explanation"]["text"] = (
        " ".join(facts[evidence_id].fact for evidence_id in card["explanation"]["evidence_ids"])
        if text_source == "machine_facts"
        else "Это мероприятие гарантирует повышение."
    )
    with pytest.raises(AssertionError):
        assert_recommendation_evidence(card, context, display_facts)
