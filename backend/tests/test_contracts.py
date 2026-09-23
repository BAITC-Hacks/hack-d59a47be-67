"""Portable tests of team-authored fixtures, never of private source profiles."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app import contracts as c
from scripts.check_contracts import check as check_generated_contracts

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
    "employee_no_target": c.EmployeeDetailResponse,
    "goal_request": c.GoalUpdateRequest,
    "recommendation_request": c.RecommendationRequest,
    "ai_context": c.RecommendationContext,
    "ai_result": c.RecommendationResult,
    "ai_enriched_context": c.RecommendationContext,
    "ai_enriched_result": c.RecommendationResult,
    "ai_not_configured": c.RecommendationResult,
    "ai_no_candidates": c.RecommendationResult,
    "recommendation_response": c.RecommendationResponse,
    "recommendation_stale": c.RecommendationResponse,
    "recommendation_no_target": c.RecommendationResponse,
    "recommendation_no_candidates": c.RecommendationResponse,
    "preview_request": c.PreviewRequest,
    "preview_response": c.PreviewResponse,
    "completion_request": c.CompletionRequest,
    "completion_assigned_request": c.CompletionRequest,
    "completion_response": c.CompletionResponse,
    "hr_summary": c.HRSummaryResponse,
    "import_request": c.ImportRequest,
    "batch_import_request": c.BatchImportRequest,
    "batch_import_commit": c.BatchImportRequest,
    "import_response": c.ImportResponse,
}


def fixture(name):
    return json.loads((EXAMPLES / f"{name}.synthetic.json").read_text())


def test_every_example_has_a_schema():
    aliases = {"recommendation-context.json", "recommendation-result.json"}
    assert {p.name for p in EXAMPLES.glob("*.json")} == {
        f"{name}.synthetic.json" for name in MODELS
    } | aliases


def test_generated_schemas_and_compatibility_examples_match_canonical_models():
    assert check_generated_contracts() == 9


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


@pytest.mark.parametrize(
    "context_name,result_name", [("ai_context", "ai_result"), ("ai_enriched_context", "ai_enriched_result")]
)
def test_ai_fixture_references_only_allowed_candidates_and_facts(context_name, result_name):
    context = c.RecommendationContext.model_validate(fixture(context_name))
    result = c.RecommendationResult.model_validate(fixture(result_name))
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


def test_import_transport_uses_verified_names_with_separate_source_validation():
    payload = fixture("import_request")
    with pytest.raises(ValidationError):
        c.ImportRequest.model_validate({**payload, "source_filename": "invented.csv"})
    with pytest.raises(ValidationError):
        c.ImportRequest.model_validate({**payload, "source_format": "json"})
    # Source parsing belongs to the importer; transport validation cannot validate source data.
    assert c.ImportRequest.model_validate({**payload, "content": "opaque"}).content == "opaque"


def test_batch_import_requires_unique_nonempty_original_files():
    payload = fixture("batch_import_request")
    with pytest.raises(ValidationError):
        c.BatchImportRequest.model_validate({**payload, "files": []})
    with pytest.raises(ValidationError):
        c.BatchImportRequest.model_validate({**payload, "files": [payload["files"][0]] * 2})


def test_history_preserves_distinct_records_and_date_provenance():
    employee = c.EmployeeDetailResponse.model_validate(fixture("employee"))
    assert len(employee.history) == 2
    assert employee.history[0].event_id == employee.history[1].event_id
    assert employee.history[0].record_id != employee.history[1].record_id
    record = employee.history[0].model_dump(mode="json")
    with pytest.raises(ValidationError):
        c.ActivityRecord.model_validate({**record, "date_source": "completed_at"})
    with pytest.raises(ValidationError):
        c.ActivityRecord.model_validate({**record, "completed_at": "2026-10-01T12:00:00Z"})
    c.ActivityRecord.model_validate(
        {**record, "date_source": "completed_at", "completed_at": "2026-10-01T12:00:00Z"}
    )
    with pytest.raises(ValidationError):
        c.ActivityRecord.model_validate(
            {**record, "date_source": "completed_at", "completed_at": "2026-10-01T12:00:00"}
        )


@pytest.mark.parametrize("status", ["no_target", "no_candidates"])
def test_backend_can_return_empty_reason_without_calling_ai(status):
    payload = fixture("recommendation_no_target")
    response = c.RecommendationResponse.model_validate({**payload, "status": status})
    assert response.engine == "none"
    with pytest.raises(ValidationError):
        c.RecommendationResponse.model_validate(
            {
                **payload,
                "status": status,
                "recommendations": fixture("recommendation_response")["recommendations"],
            }
        )


def test_backend_http_extensions_do_not_change_ai_result_contract():
    with pytest.raises(ValidationError):
        c.RecommendationResult(status="no_target", engine="none", recommendations=[])
    with pytest.raises(ValidationError):
        c.RecommendationResult(status="no_candidates", engine="none", recommendations=[])
    payload = fixture("recommendation_no_target")
    with pytest.raises(ValidationError):
        c.RecommendationResponse.model_validate({**payload, "engine": "oleg"})


@pytest.mark.parametrize(
    "changes",
    [
        {"percent": 101.0},
        {"met_skills": 2},
        {"critical_total": 2},
        {"critical_met": 1},
    ],
)
def test_progress_rejects_invalid_counts_and_percent(changes):
    with pytest.raises(ValidationError):
        c.Progress.model_validate({**fixture("employee")["progress"], **changes})


def test_catalog_events_include_source_rules_not_client_authority():
    event = c.CatalogResponse.model_validate(fixture("catalog")).events[0]
    assert event.develops_skills[0].gain == 1.0
    assert event.prerequisites == {"SYNTHETIC_SKILL_001": 1.0}
    assert event.upcoming_sessions == []
    payload = fixture("catalog")
    payload["events"][0]["develops_skills"][0]["max_level"] = 5.1
    with pytest.raises(ValidationError):
        c.CatalogResponse.model_validate(payload)
