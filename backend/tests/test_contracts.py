"""Portable tests of team-authored fixtures, never of private source profiles."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app import contracts as c

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples"
MODELS = {
    "health": c.HealthResponse,
    "readiness": c.ReadinessResponse,
    "version": c.VersionResponse,
    "error": c.ErrorResponse,
    "login_request": c.LoginRequest,
    "login_response": c.SessionResponse,
    "me": c.UserIdentity,
    "logout": c.LogoutResponse,
    "catalog": c.CatalogResponse,
    "employees": c.EmployeeListResponse,
    "employee": c.EmployeeDetailResponse,
    "goal_request": c.GoalUpdateRequest,
    "recommendation_request": c.RecommendationRequest,
    "ai_context": c.RecommendationContext,
    "ai_result": c.RecommendationResult,
    "ai_not_configured": c.RecommendationResult,
    "ai_no_candidates": c.RecommendationResult,
    "recommendation_response": c.RecommendationResponse,
    "preview_request": c.PreviewRequest,
    "preview_response": c.PreviewResponse,
    "completion_request": c.CompletionRequest,
    "completion_response": c.CompletionResponse,
    "hr_summary": c.HRSummaryResponse,
    "import_request": c.ImportRequest,
    "import_response": c.ImportResponse,
}


def fixture(name):
    return json.loads((EXAMPLES / f"{name}.synthetic.json").read_text())


def test_every_example_has_a_schema():
    assert {p.name for p in EXAMPLES.glob("*.json")} == {f"{name}.synthetic.json" for name in MODELS}


@pytest.mark.parametrize("name,model", MODELS.items())
def test_synthetic_examples_match_v1_schemas(name, model):
    payload = (EXAMPLES / f"{name}.synthetic.json").read_text()
    model.model_validate_json(payload)
    # FastAPI has already decoded request JSON before validating models.
    model.model_validate(json.loads(payload))


def test_fastapi_accepts_iso_date_request_json():
    app = FastAPI()

    @app.post("/recommendations")
    def recommendations(body: c.RecommendationRequest):
        return body

    client = TestClient(app)
    payload = fixture("recommendation_request")
    response = client.post("/recommendations", json=payload)
    assert response.status_code == 200
    assert response.json() == payload
    assert client.post("/recommendations", json={**payload, "limit": "3"}).status_code == 422


def test_ai_fixture_references_only_allowed_candidates_and_facts():
    context = c.RecommendationContext.model_validate(fixture("ai_context"))
    result = c.RecommendationResult.model_validate(fixture("ai_result"))
    assert len(result.recommendations) <= context.limit
    candidates = {candidate.event_id for candidate in context.eligible_candidates}
    evidence = {fact.evidence_id for fact in context.facts}
    for selected in result.recommendations:
        assert selected.event_id in candidates
        assert set(selected.explanation.evidence_ids) <= evidence
    assert "employee_id" not in c.AnonymizedProfile.model_fields
    assert "full_name" not in c.AnonymizedProfile.model_fields


@pytest.mark.parametrize(
    "overrides",
    [
        {"engine": "oleg"},
        {"status": "no_candidates"},
        {"recommendations": fixture("ai_result")["recommendations"]},
    ],
)
def test_disabled_ai_is_not_an_empty_success_or_no_candidates(overrides):
    with pytest.raises(ValidationError):
        c.RecommendationResult.model_validate({**fixture("ai_not_configured"), **overrides})


def test_result_rejects_duplicate_event_ids_and_more_than_three_results():
    payload = fixture("ai_result")
    item = payload["recommendations"][0]
    with pytest.raises(ValidationError):
        c.RecommendationResult.model_validate({**payload, "recommendations": [item, item]})
    with pytest.raises(ValidationError):
        c.RecommendationResult.model_validate(
            {
                **payload,
                "recommendations": [{**item, "event_id": f"synthetic-{i}"} for i in range(4)],
            }
        )


def test_context_rejects_unknown_evidence_references():
    payload = fixture("ai_context")
    payload["eligible_candidates"][0]["evidence_ids"] = ["unknown-evidence"]
    with pytest.raises(ValidationError):
        c.RecommendationContext.model_validate(payload)


def test_login_cannot_choose_a_role_and_does_not_repr_the_password():
    payload = fixture("login_request")
    login = c.LoginRequest.model_validate(payload)
    assert payload["password"] not in repr(login)
    with pytest.raises(ValidationError):
        c.LoginRequest.model_validate({**payload, "role": "hr"})


def test_source_scale_and_grade_are_preserved():
    assert c.SkillLevel(skill_id="synthetic", level=5.0).level == 5
    with pytest.raises(ValidationError):
        c.SkillLevel(skill_id="synthetic", level=5.1)
    with pytest.raises(ValidationError):
        c.Goal(target_role="Synthetic Analyst", target_grade="Principal")


def test_simulation_is_explicit_and_preview_cannot_claim_persistence():
    payload = fixture("completion_request")
    del payload["mode"]
    with pytest.raises(ValidationError):
        c.CompletionRequest.model_validate(payload)
    with pytest.raises(ValidationError):
        c.PreviewResponse.model_validate({**fixture("preview_response"), "persisted": True})


def test_import_transport_uses_verified_names_without_claiming_source_validation():
    payload = fixture("import_request")
    with pytest.raises(ValidationError):
        c.ImportRequest.model_validate({**payload, "source_filename": "invented.csv"})
    with pytest.raises(ValidationError):
        c.ImportRequest.model_validate({**payload, "source_format": "json"})
    # Source parsing is deliberately absent; transport validation is not an importer.
    assert c.ImportRequest.model_validate({**payload, "content": "opaque"}).content == "opaque"
