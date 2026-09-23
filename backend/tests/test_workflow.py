"""Acceptance workflow against independent synthetic source files and a fake AI.

The fake provider proves integration semantics only; these tests make no claims
about Oleg's module or any live model. No organizer profiles are copied here.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app import contracts as c
from backend.app.ai_adapter import AIAdapter
from backend.app.auth import hash_password
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.errors import APIError
from backend.app.imports import ImportService
from backend.app.main import create_app
from backend.app.service import CareerService

EMPLOYEE = "WORKFLOW_SYNTH_1"
OTHER = "WORKFLOW_SYNTH_2"
EVENT = "WORKFLOW_COURSE"
PASSWORD = "workflow-synthetic-test-only"
ORIGIN = "http://127.0.0.1:8000"
META = {"dataset": "independent-workflow-fixture", "version": "synthetic-v1", "as_of_date": "2026-10-01"}
USER = {
    "id": "workflow-account",
    "username": "workflow.employee",
    "role": "employee",
    "employee_id": EMPLOYEE,
}
CSV_FIELDS = [
    "record_id",
    "employee_id",
    "event_id",
    "date",
    "due_date",
    "status",
    "completion_pct",
    "score",
    "feedback_rating",
    "assigned_by",
]


def profile(employee_id=EMPLOYEE):
    return {
        "employee_id": employee_id,
        "full_name": "Synthetic Workflow Person",
        "department": "Synthetic Department",
        "role": "Synthetic Role",
        "grade": "Junior",
        "manager_id": None,
        "hire_date": "2026-01-01",
        "tenure_months": 9,
        "work_format": "remote",
        "preferred_language": "en",
        "career_goal": None,
        "skills": {"WORKFLOW_HARD": 1},
        "last_review_date": "2026-09-01",
    }


def json_source(filename, document):
    return {"source_filename": filename, "source_format": "json", "content": json.dumps(document)}


def history_source(rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return {"source_filename": "activity_history.csv", "source_format": "csv", "content": buffer.getvalue()}


def source_batch():
    skills = {
        "meta": META,
        "proficiency_scale": {str(i): f"Synthetic level {i}" for i in range(6)},
        "skills": [
            {
                "skill_id": skill,
                "name": skill,
                "type": kind,
                "category": "Synthetic",
                "description": "Synthetic fixture skill",
            }
            for skill, kind in [("WORKFLOW_HARD", "hard"), ("WORKFLOW_SOFT", "soft")]
        ],
        "role_profiles": [
            {
                "role": "Synthetic Role",
                "grade": grade,
                "required_skills": {"WORKFLOW_HARD": level, "WORKFLOW_SOFT": level},
                "critical_skills": ["WORKFLOW_HARD"],
            }
            for grade, level in [("Junior", 1), ("Middle", 3), ("Senior", 4), ("Lead", 5)]
        ],
    }
    events = {
        "meta": META,
        "events": [
            {
                "event_id": event_id,
                "title": "Synthetic " + event_id,
                "description": "Independent synthetic event",
                "type": "course",
                "format": "self_paced",
                "duration_hours": 2,
                "mandatory": mandatory,
                "target_roles": ["Synthetic Role"],
                "target_grades": ["Junior", "Middle", "Senior", "Lead"],
                "develops_skills": [{"skill_id": skill_id, "gain": 1, "max_level": 5}],
                "prerequisites": {},
                "upcoming_sessions": [],
            }
            for event_id, skill_id, mandatory in [
                (EVENT, "WORKFLOW_HARD", False),
                ("WORKFLOW_SOFT_COURSE", "WORKFLOW_SOFT", False),
                ("WORKFLOW_MANDATORY", "WORKFLOW_HARD", True),
            ]
        ],
    }
    rows = [
        {
            "record_id": "WORKFLOW_ASSIGNED",
            "employee_id": EMPLOYEE,
            "event_id": "WORKFLOW_MANDATORY",
            "date": "2026-09-15",
            "due_date": "2026-10-10",
            "status": "in_progress",
            "completion_pct": 50,
            "score": "",
            "feedback_rating": "",
            "assigned_by": "hr",
        }
    ]
    return [
        json_source("skills.json", skills),
        json_source("events.json", events),
        json_source("employees.json", {"meta": META, "employees": [profile(), profile(OTHER)]}),
        history_source(rows),
    ]


def commit_batch(db, sources):
    importer = ImportService(db)
    preview = importer.preview(sources)
    return importer.commit(sources, preview["preview_token"])


def completion(revision=1, event_id=EVENT, **kwargs):
    return c.CompletionRequest(
        expected_state_version=revision, event_id=event_id, mode="completion", **kwargs
    )


def request_recommendations():
    return c.RecommendationRequest.model_validate({"scenario_date": "2026-10-01", "limit": 3})


def goal_update(revision):
    return c.GoalUpdateRequest(
        expected_state_version=revision, goal=c.Goal(target_role="Synthetic Role", target_grade="Senior")
    )


def valid_ai_result(context):
    candidate = context.eligible_candidates[0]
    facts = {fact.kind: fact.evidence_id for fact in context.facts if fact.kind in {"goal", "gap", "history"}}
    facts["gap"] = next(
        fact.evidence_id
        for fact in context.facts
        if fact.kind == "gap"
        and fact.subject_id in {effect.skill_id for effect in candidate.effects if effect.delta > 0}
    )
    facts["history"] = next(
        fact.evidence_id for fact in context.facts if fact.kind == "history" and fact.subject_id == "profile"
    )
    evidence = [candidate.evidence_ids[0], facts["goal"], facts["gap"], facts["history"]]
    return c.RecommendationResult(
        status="ok",
        engine="oleg",
        recommendations=[
            c.RecommendationSelection(
                event_id=candidate.event_id,
                explanation=c.Explanation(text="UNTRUSTED MODEL TEXT", evidence_ids=evidence),
            )
        ],
    )


@pytest.fixture(scope="session")
def workflow_password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def workflow(tmp_path, workflow_password_hash):
    settings = Settings(database_path=tmp_path / "synthetic-workflow.sqlite3", app_env="test")
    db = Database(settings)
    db.migrate()
    commit_batch(db, source_batch())
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO accounts VALUES (?,?,?,?,?)",
            [
                (USER["id"], USER["username"], workflow_password_hash, "employee", EMPLOYEE),
                ("workflow-other", "workflow.other", workflow_password_hash, "employee", OTHER),
                ("workflow-hr", "workflow.hr", workflow_password_hash, "hr", None),
            ],
        )
    adapter = AIAdapter()

    async def fake_recommend(context):
        return valid_ai_result(context)

    adapter._recommend = fake_recommend
    return settings, db, CareerService(db, adapter)


def assert_api_error(status, code, fn):
    with pytest.raises(APIError) as exc:
        fn()
    assert (exc.value.status, exc.value.code) == (status, code)


def database_counts(db):
    with db.connect() as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in [
                "activity_history",
                "completion_requests",
                "recommendation_runs",
                "employee_profiles",
            ]
        } | {"revision": conn.execute("SELECT revision FROM dataset_state").fetchone()[0]}


def test_lost_successful_completion_response_replays_exact_result_after_revision_changes(workflow):
    _, db, service = workflow
    payload = completion()
    first_response = service.complete(EMPLOYEE, USER, payload, "lost-response-key")
    service.set_goal(EMPLOYEE, goal_update(first_response["state_version"]))
    after_unrelated_write = database_counts(db)
    # The caller retries the original request because the successful response was lost.
    replayed_response = service.complete(EMPLOYEE, USER, payload, "lost-response-key")
    assert replayed_response == first_response
    assert replayed_response["state_version"] < after_unrelated_write["revision"]
    assert database_counts(db) == after_unrelated_write
    assert service.snapshot(EMPLOYEE)["skills"]["WORKFLOW_HARD"] == 2


def test_idempotency_different_body_conflicts_and_new_key_cannot_duplicate_gain(workflow):
    _, db, service = workflow
    first = service.complete(EMPLOYEE, USER, completion(), "key-one")
    before = database_counts(db)
    assert_api_error(
        409,
        "IDEMPOTENCY_CONFLICT",
        lambda: service.complete(EMPLOYEE, USER, completion(event_id="WORKFLOW_SOFT_COURSE"), "key-one"),
    )
    assert_api_error(
        409,
        "ALREADY_COMPLETED",
        lambda: service.complete(EMPLOYEE, USER, completion(revision=first["state_version"]), "key-two"),
    )
    assert database_counts(db) == before
    assert service.snapshot(EMPLOYEE)["skills"]["WORKFLOW_HARD"] == 2


def test_completion_authorization_happens_before_cached_idempotency_replay(workflow):
    _, db, service = workflow
    service.complete(EMPLOYEE, USER, completion(), "secret-key")
    before = database_counts(db)
    impostor = {**USER, "employee_id": OTHER}
    assert_api_error(
        403, "FORBIDDEN", lambda: service.complete(EMPLOYEE, impostor, completion(), "secret-key")
    )
    assert database_counts(db) == before


def test_completion_transaction_rolls_back_when_saving_idempotency_result_fails(workflow):
    _, db, service = workflow
    before = database_counts(db)
    initial_skills = service.snapshot(EMPLOYEE)["skills"]
    with db.connect() as conn:
        conn.execute(
            "CREATE TRIGGER synthetic_failure BEFORE INSERT ON completion_requests "
            "BEGIN SELECT RAISE(ABORT, 'synthetic injected failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic injected failure"):
        service.complete(EMPLOYEE, USER, completion(), "retry-after-rollback")
    assert database_counts(db) == before
    assert service.snapshot(EMPLOYEE)["skills"] == initial_skills
    with db.connect() as conn:
        conn.execute("DROP TRIGGER synthetic_failure")
    result = service.complete(EMPLOYEE, USER, completion(), "retry-after-rollback")
    assert result["state_version"] == before["revision"] + 1


def test_existing_assignment_completed_in_place_and_cannot_gain_twice(workflow):
    _, db, service = workflow
    before = database_counts(db)
    payload = completion(event_id="WORKFLOW_MANDATORY", record_id="WORKFLOW_ASSIGNED")
    result = service.complete(EMPLOYEE, USER, payload, "assigned-key")
    assert result["completion"]["completion_id"] == "WORKFLOW_ASSIGNED"
    assert database_counts(db)["activity_history"] == before["activity_history"]
    assert service.snapshot(EMPLOYEE)["skills"]["WORKFLOW_HARD"] == 2
    assert_api_error(
        409,
        "INVALID_ACTIVITY_STATE",
        lambda: service.complete(
            EMPLOYEE,
            USER,
            completion(result["state_version"], "WORKFLOW_MANDATORY", record_id="WORKFLOW_ASSIGNED"),
            "new-assigned-key",
        ),
    )


def test_repeatable_club_uses_next_session_and_idempotent_retry_never_reapplies_gain(tmp_path):
    settings = Settings(database_path=tmp_path / "synthetic-repeatable.sqlite3", app_env="test")
    db = Database(settings)
    db.migrate()
    sources = source_batch()
    events = json.loads(sources[1]["content"])
    club = {
        **events["events"][0],
        "event_id": "EV_036",
        "title": "Synthetic Repeatable Club",
        "format": "online",
        "upcoming_sessions": ["2026-10-05", "2026-10-12"],
    }
    events["events"].append(club)
    sources[1] = json_source("events.json", events)
    commit_batch(db, sources)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO accounts VALUES (?,?,?,?,?)",
            (USER["id"], USER["username"], "unused-service-only-password-hash", "employee", EMPLOYEE),
        )
    service = CareerService(db, AIAdapter())
    first_payload = c.CompletionRequest(expected_state_version=1, event_id="EV_036", mode="demo_simulation")
    first = service.complete(EMPLOYEE, USER, first_payload, "club-first-session")
    second_payload = c.CompletionRequest(
        expected_state_version=first["state_version"], event_id="EV_036", mode="demo_simulation"
    )
    second = service.complete(EMPLOYEE, USER, second_payload, "club-second-session")
    rows = [row for row in service.snapshot(EMPLOYEE)["history"] if row["event_id"] == "EV_036"]
    assert sorted(row["session_date"] for row in rows) == ["2026-10-05", "2026-10-12"]
    assert service.snapshot(EMPLOYEE)["skills"]["WORKFLOW_HARD"] == 3
    before_retry = database_counts(db)
    assert service.complete(EMPLOYEE, USER, first_payload, "club-first-session") == first
    assert service.complete(EMPLOYEE, USER, second_payload, "club-second-session") == second
    assert database_counts(db) == before_retry
    assert service.snapshot(EMPLOYEE)["skills"]["WORKFLOW_HARD"] == 3
    assert_api_error(
        409,
        "NO_SESSION",
        lambda: service.complete(
            EMPLOYEE,
            USER,
            c.CompletionRequest(
                expected_state_version=second["state_version"], event_id="EV_036", mode="demo_simulation"
            ),
            "club-third-session",
        ),
    )


def test_preview_is_read_only_and_matches_completed_projection(workflow):
    _, db, service = workflow
    before = database_counts(db)
    preview = service.preview(
        EMPLOYEE,
        c.PreviewRequest.model_validate(
            {"expected_state_version": 1, "scenario_date": "2026-10-01", "event_ids": [EVENT]}
        ),
    )
    assert preview["persisted"] is False
    assert database_counts(db) == before
    completed = service.complete(EMPLOYEE, USER, completion(), "after-preview")
    assert preview["effects"] == completed["completion"]["effects"]
    assert preview["projected_skills"] == service.detail(EMPLOYEE)["current_skills"]


def test_saved_recommendation_full_cards_survive_restart_and_hide_untrusted_model_text(workflow):
    settings, db, service = workflow
    saved = asyncio.run(service.recommend(EMPLOYEE, request_recommendations()))
    assert saved["status"] == "ok"
    assert saved["recommendations"][0]["effects"]
    assert len(saved["recommendations"][0]["explanation"]["evidence_ids"]) >= 3
    assert "UNTRUSTED MODEL TEXT" not in saved["recommendations"][0]["explanation"]["text"]
    assert service.latest(EMPLOYEE) == saved
    db.migrate()
    restarted = CareerService(Database(settings), AIAdapter())
    assert restarted.latest(EMPLOYEE) == saved


@pytest.mark.parametrize("mutation", ["goal", "completion", "import"])
def test_saved_recommendations_stale_after_each_data_mutation(workflow, mutation):
    _, db, service = workflow
    saved = asyncio.run(service.recommend(EMPLOYEE, request_recommendations()))
    if mutation == "goal":
        service.set_goal(EMPLOYEE, goal_update(saved["state_version"]))
    elif mutation == "completion":
        service.complete(EMPLOYEE, USER, completion(saved["state_version"]), "invalidate-on-completion")
    else:
        commit_batch(
            db, [json_source("employees.json", {"meta": META, "employees": [profile("WORKFLOW_NEW")]})]
        )
    latest = service.latest(EMPLOYEE)
    assert latest["stale"] is True
    assert latest["recommendations"] == saved["recommendations"]
    assert latest["state_version"] == saved["state_version"]


def test_inflight_ai_holds_no_database_lock_and_stale_result_is_not_saved(workflow):
    _, db, service = workflow
    did_write = []

    async def mutating_fake(context):
        await asyncio.sleep(0)
        with db.connect() as conn:
            conn.execute("PRAGMA busy_timeout=30")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE dataset_state SET revision=revision+1 WHERE id=1")
        did_write.append(True)
        return valid_ai_result(context)

    service.adapter._recommend = mutating_fake
    assert_api_error(
        409, "REVISION_CONFLICT", lambda: asyncio.run(service.recommend(EMPLOYEE, request_recommendations()))
    )
    assert did_write == [True]
    assert database_counts(db)["recommendation_runs"] == 0
    assert database_counts(db)["revision"] == 2


def test_ai_disabled_no_candidate_and_no_target_are_distinct(workflow):
    _, db, service = workflow
    service.adapter = AIAdapter()
    assert asyncio.run(service.recommend(EMPLOYEE, request_recommendations()))["status"] == "not_configured"
    service.complete(EMPLOYEE, USER, completion(), "all-hard")
    service.complete(EMPLOYEE, USER, completion(2, "WORKFLOW_SOFT_COURSE"), "all-soft")
    assert asyncio.run(service.recommend(EMPLOYEE, request_recommendations()))["status"] == "no_candidates"
    lead = {**profile("WORKFLOW_LEAD"), "grade": "Lead"}
    commit_batch(db, [json_source("employees.json", {"meta": META, "employees": [lead]})])
    result = asyncio.run(service.recommend(lead["employee_id"], request_recommendations()))
    assert result["status"] == "no_target"
    assert result["engine"] == "none"
    assert result["recommendations"] == []


def test_invalid_joint_import_writes_neither_profiles_nor_history(workflow):
    _, db, _ = workflow
    before = database_counts(db)
    new_profile = profile("WORKFLOW_BATCH_NEW")
    rows = [
        {
            "record_id": "WORKFLOW_BATCH_GOOD",
            "employee_id": new_profile["employee_id"],
            "event_id": EVENT,
            "date": "2026-09-20",
            "due_date": "",
            "status": "completed",
            "completion_pct": 100,
            "score": 90,
            "feedback_rating": 4,
            "assigned_by": "self",
        }
    ]
    rows.append({**rows[0], "record_id": "WORKFLOW_BATCH_BAD", "event_id": "WORKFLOW_MISSING"})
    sources = [
        json_source("employees.json", {"meta": META, "employees": [new_profile]}),
        history_source(rows),
    ]
    assert_api_error(422, "invalid_source", lambda: ImportService(db).preview(sources))
    assert database_counts(db) == before
    assert db.readiness()


def login(client, username="workflow.employee"):
    result = client.post(
        "/api/auth/login", headers={"Origin": ORIGIN}, json={"username": username, "password": PASSWORD}
    )
    assert result.status_code == 200, result.text
    return {"Origin": ORIGIN, "X-CSRF-Token": result.json()["csrf_token"]}


def test_http_foreign_profile_and_hr_access_are_server_enforced(workflow):
    settings, db, _ = workflow
    before = database_counts(db)
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        assert client.get(f"/api/employees/{EMPLOYEE}").status_code == 401
        headers = login(client)
        assert client.get(f"/api/employees/{EMPLOYEE}").status_code == 200
        for url in [
            f"/api/employees/{OTHER}",
            f"/api/employees/{OTHER}/recommendations/latest",
            "/api/employees",
            "/api/hr/summary",
        ]:
            assert client.get(url).status_code == 403
        for suffix, payload in [
            ("completions", completion().model_dump(mode="json")),
            ("recommendations", request_recommendations().model_dump(mode="json")),
            ("preview", {"expected_state_version": 1, "scenario_date": "2026-10-01", "event_ids": [EVENT]}),
        ]:
            result = client.post(
                f"/api/employees/{OTHER}/{suffix}",
                headers={**headers, "Idempotency-Key": "foreign"},
                json=payload,
            )
            assert result.status_code == 403, result.text
        assert (
            client.patch(
                f"/api/employees/{OTHER}/goal", headers=headers, json=goal_update(1).model_dump(mode="json")
            ).status_code
            == 403
        )
        # A forged identity header cannot upgrade the server-side account role.
        assert client.get("/api/hr/summary", headers={"X-Role": "hr"}).status_code == 403
        assert (
            client.post(
                "/api/hr/import", headers=headers, json={"dry_run": True, "files": source_batch()}
            ).status_code
            == 403
        )
    assert database_counts(db) == before


def test_http_frontend_flow_preview_completion_retry_and_restart(workflow):
    settings, _, _ = workflow
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        headers = login(client)
        assert client.get("/api/catalog").status_code == 200
        initial = client.get(f"/api/employees/{EMPLOYEE}").json()
        c.EmployeeDetailResponse.model_validate(initial)
        assert initial["scenario_date"] == "2026-10-01"
        payload = completion(initial["state_version"]).model_dump(mode="json")
        preview = client.post(
            f"/api/employees/{EMPLOYEE}/preview",
            headers=headers,
            json={
                "expected_state_version": initial["state_version"],
                "scenario_date": "2026-10-01",
                "event_ids": [EVENT],
            },
        )
        assert preview.status_code == 200, preview.text
        completion_headers = {**headers, "Idempotency-Key": "frontend-lost-response"}
        result = client.post(
            f"/api/employees/{EMPLOYEE}/completions", headers=completion_headers, json=payload
        )
        assert result.status_code == 200, result.text
        completed = result.json()
        assert (
            client.post(
                f"/api/employees/{EMPLOYEE}/completions", headers=completion_headers, json=payload
            ).json()
            == completed
        )
        assert client.post("/api/auth/logout", headers=headers).status_code == 200
        assert client.get("/api/me").status_code == 401
    with TestClient(create_app(settings), base_url=ORIGIN) as restarted:
        headers = login(restarted)
        replayed = restarted.post(
            f"/api/employees/{EMPLOYEE}/completions",
            headers={**headers, "Idempotency-Key": "frontend-lost-response"},
            json=payload,
        )
        assert replayed.status_code == 200
        assert replayed.json() == completed
        detail = restarted.get(f"/api/employees/{EMPLOYEE}").json()
        assert detail["current_skills"] == preview.json()["projected_skills"]
        exact = next(
            row for row in detail["history"] if row["record_id"] == completed["completion"]["completion_id"]
        )
        assert exact["date_source"] == "completed_at"
        assert exact["completed_at"].startswith("2026-10-01")


def test_http_latest_returns_complete_saved_cards_after_app_restart(workflow):
    settings, _, _ = workflow
    app = create_app(settings)

    async def fake(context):
        return valid_ai_result(context)

    app.state.ai_adapter._recommend = fake
    with TestClient(app, base_url=ORIGIN) as client:
        headers = login(client)
        result = client.post(
            f"/api/employees/{EMPLOYEE}/recommendations",
            headers=headers,
            json=request_recommendations().model_dump(mode="json"),
        )
        assert result.status_code == 200, result.text
        saved = result.json()
        assert saved["recommendations"]
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        login(client)
        latest = client.get(f"/api/employees/{EMPLOYEE}/recommendations/latest")
        assert latest.status_code == 200, latest.text
        assert latest.json() == saved


def test_ai_single_factor_is_not_published_as_valid_recommendation(workflow):
    _, _, service = workflow

    async def single_factor(context):
        result = valid_ai_result(context)
        result.recommendations[0].explanation.evidence_ids = [context.eligible_candidates[0].evidence_ids[0]]
        return result

    service.adapter._recommend = single_factor
    result = asyncio.run(service.recommend(EMPLOYEE, request_recommendations()))
    assert result["status"] == "unavailable"
    assert result["recommendations"] == []


def test_http_hr_joint_preview_and_commit(workflow):
    settings, db, _ = workflow
    files = [json_source("employees.json", {"meta": META, "employees": [profile("WORKFLOW_JURY")]})]
    before = database_counts(db)
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        headers = login(client, "workflow.hr")
        preview = client.post("/api/hr/import", headers=headers, json={"dry_run": True, "files": files})
        assert preview.status_code == 200, preview.text
        assert preview.json()["status"] == "validated"
        assert database_counts(db) == before
        response = client.post(
            "/api/hr/import",
            headers=headers,
            json={"dry_run": False, "files": files, "preview_token": preview.json()["preview_token"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["counts"]["employees"] == 1
        assert client.get("/api/hr/summary").json()["employee_count"] == 3
        assert client.get("/api/employees?limit=1&offset=1").json()["total"] == 3
