"""Independent HR assertions on small synthetic cohorts, never organiser data."""

import asyncio
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.app import contracts as c
from backend.app.ai_adapter import AIAdapter
from backend.app.auth import hash_password
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.errors import APIError
from backend.app.hr_analytics import build_hr_analytics
from backend.app.main import create_app
from backend.app.service import CareerService
from backend.tests.test_workflow import (
    EMPLOYEE,
    EVENT,
    META,
    ORIGIN,
    OTHER,
    PASSWORD,
    commit_batch,
    completion,
    history_source,
    json_source,
    profile,
    request_recommendations,
    source_batch,
    valid_ai_result,
)


def record(record_id, employee_id=EMPLOYEE, event_id=EVENT, day="2026-09-15", status="completed"):
    return {
        "record_id": record_id,
        "employee_id": employee_id,
        "event_id": event_id,
        "date": day,
        "due_date": "",
        "status": status,
        "completion_pct": {"completed": 100, "dropped": 50, "in_progress": 50}.get(status, 0),
        "score": "",
        "feedback_rating": "",
        "assigned_by": "self",
    }


@pytest.fixture
def workspace(tmp_path):
    settings = Settings(database_path=tmp_path / "hr-synthetic.sqlite3", app_env="test")
    db = Database(settings)
    db.migrate()
    return settings, db, CareerService(db, AIAdapter())


def load(db, profiles=None, records=None, mutate_catalog=None):
    sources = source_batch()[:2]
    event_catalog = json.loads(sources[1]["content"])
    scheduled = {row["event_id"] for row in records or [] if row["status"] == "no_show"}
    for event in event_catalog["events"]:
        if event["event_id"] in scheduled:
            event.update(format="online", upcoming_sessions=["2026-10-01"])
    sources[1] = json_source("events.json", event_catalog)
    if mutate_catalog:
        catalog = json.loads(sources[0]["content"])
        mutate_catalog(catalog)
        sources[0] = json_source("skills.json", catalog)
    sources.append(
        json_source(
            "employees.json",
            {"meta": META, "employees": [profile(), profile(OTHER)] if profiles is None else profiles},
        )
    )
    sources.append(history_source(records or []))
    commit_batch(db, sources)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO accounts(id,username,password_hash,role,employee_id) VALUES (?,?,?,?,?)",
            ("hr-testactor", "synthetic.actor", "unused-test-only-hash", "hr", None),
        )


def test_gaps_use_resolved_target_and_skill_specific_denominator(workspace):
    _, db, career = workspace
    a, b, lead = profile(), profile(OTHER), profile("HR_SYNTH_LEAD")
    a["skills"] = {"WORKFLOW_HARD": 2, "WORKFLOW_SOFT": 3}
    b["career_goal"] = {"target_role": "Synthetic Other Role", "target_grade": "Middle"}
    lead["grade"] = "Lead"

    def add_target(catalog):
        catalog["role_profiles"].append(
            {
                "role": "Synthetic Other Role",
                "grade": "Middle",
                "required_skills": {"WORKFLOW_HARD": 4},
                "critical_skills": [],
            }
        )

    load(db, profiles=[a, b, lead], records=[record("HR_GROWTH")], mutate_catalog=add_target)
    result = c.HRAnalyticsResponse.model_validate(build_hr_analytics(career))
    gaps = {row.skill_id: row for row in result.skill_gaps}
    assert result.employee_count == 3 and result.employees_with_goal == 2
    assert gaps["WORKFLOW_HARD"].model_dump() == {
        "skill_id": "WORKFLOW_HARD",
        "skill_name": "WORKFLOW_HARD",
        "employees_requiring": 2,
        "employees_with_gap": 1,
        "gap_percent": 50.0,
        "average_gap": 3.0,
        "critical_gap_count": 0,
    }
    assert gaps["WORKFLOW_SOFT"].employees_requiring == 1
    assert gaps["WORKFLOW_SOFT"].employees_with_gap == 0
    assert gaps["WORKFLOW_SOFT"].average_gap == 0
    assert (
        next(item for item in result.attention if item.profile.employee_id == lead["employee_id"])
        .reasons[-1]
        .code
        == "no_target"
    )


def test_attention_separates_missing_history_recent_hire_and_observed_signals(workspace):
    _, db, career = workspace
    newcomer = profile("HR_SYNTH_NEW")
    newcomer.update(hire_date="2026-09-10", last_review_date="2026-09-10")
    load(
        db,
        profiles=[profile(), profile(OTHER), newcomer],
        records=[
            record("HR_OLD", day="2026-06-01"),
            record("HR_MISSED_1", day="2026-07-04", status="no_show"),
            record("HR_MISSED_2", day="2026-10-01", status="no_show"),
            record("HR_NEW_PROGRESS", employee_id=newcomer["employee_id"], status="in_progress"),
        ],
    )
    result = build_hr_analytics(career)
    people = {item["profile"]["employee_id"]: item for item in result["attention"]}
    assert {row["code"] for row in people[EMPLOYEE]["reasons"]} == {
        "no_recent_completion",
        "repeated_no_show",
        "recommendation_missing",
    }
    assert people[EMPLOYEE]["last_completed_date"] == date(2026, 6, 1)
    assert people[EMPLOYEE]["no_show_in_period"] == 2
    assert [row["code"] for row in people[OTHER]["reasons"]] == ["no_history", "recommendation_missing"]
    assert [row["code"] for row in people[newcomer["employee_id"]]["reasons"]] == ["recommendation_missing"]
    assert result["employees_with_history"] == 2
    assert any("недостаток данных" in note for note in result["notes"])


def test_no_candidates_is_catalog_gap_not_achieved_goal(workspace):
    _, db, career = workspace
    blocked, achieved = profile(), profile(OTHER)
    # Both available voluntary courses have been completed before assessment.
    # The assessed low baseline still requires further development for the first person.
    achieved["skills"] = {"WORKFLOW_HARD": 3, "WORKFLOW_SOFT": 3}
    rows = [
        record("HR_DONE_HARD", day="2026-07-01"),
        record("HR_DONE_SOFT", event_id="WORKFLOW_SOFT_COURSE", day="2026-07-02"),
        record("HR_SATISFIED", employee_id=OTHER),
    ]
    load(db, profiles=[blocked, achieved], records=rows)
    result = build_hr_analytics(career)
    people = {item["profile"]["employee_id"]: item for item in result["attention"]}
    assert "no_candidates" in {row["code"] for row in people[EMPLOYEE]["reasons"]}
    assert people[EMPLOYEE]["eligible_event_count"] == 0
    assert OTHER not in people


def test_participation_boundaries_statuses_repeated_assignments_and_unique_people(workspace):
    _, db, career = workspace
    mandatory = "WORKFLOW_MANDATORY"
    rows = [
        record("HR_OUTSIDE", event_id=mandatory, day="2026-07-03"),
        record("HR_LEFT_BOUNDARY", event_id=mandatory, day="2026-07-04"),
        record("HR_RIGHT_BOUNDARY", event_id=mandatory, day="2026-10-01"),
        record("HR_OTHER_PERSON", employee_id=OTHER, event_id=mandatory, status="no_show"),
        record("HR_PROGRESS", status="in_progress"),
        record("HR_DROP", status="dropped"),
        record("HR_DECLINED", status="declined"),
        record("HR_OVERDUE", event_id=mandatory, status="overdue"),
    ]
    load(db, records=rows)
    result = build_hr_analytics(career)
    assert result["period_start"] == date(2026, 7, 4)
    assert result["period_end"] == result["scenario_date"] == date(2026, 10, 1)
    assert result["history_records_in_period"] == result["historical_proxy_records_in_period"] == 7
    assert result["employees_with_completion_in_period"] == 1
    events = {row["event_id"]: row for row in result["participation"]}
    assert events[mandatory]["mandatory"]
    assert events[mandatory]["record_count"] == 4
    assert events[mandatory]["participant_count"] == 2
    assert events[mandatory]["completed"] == 2
    assert events[mandatory]["no_show"] == 1
    assert events[mandatory]["overdue"] == 1
    for status in ("in_progress", "dropped", "declined"):
        assert events[EVENT][status] == 1
    assert build_hr_analytics(career, 1)["history_records_in_period"] == 1


def test_real_completion_refreshes_revision_gaps_and_participation_without_double_count(workspace):
    _, db, career = workspace
    load(db)
    user = {"id": "hr-testactor", "role": "employee", "employee_id": EMPLOYEE}
    before = build_hr_analytics(career)
    request = completion()
    career.complete(EMPLOYEE, user, request, "hr-real-completion")
    after = build_hr_analytics(career)
    gaps = {item["skill_id"]: item for item in after["skill_gaps"]}
    assert after["state_version"] == before["state_version"] + 1
    assert after["data_version"] != before["data_version"]
    assert gaps["WORKFLOW_HARD"]["average_gap"] == 1.5
    assert gaps["WORKFLOW_HARD"]["critical_gap_count"] == 2
    assert after["employees_with_completion_in_period"] == 1
    assert after["history_records_in_period"] == 1
    assert after["historical_proxy_records_in_period"] == 0
    assert after["participation"][0]["completed"] == 1
    career.complete(EMPLOYEE, user, request, "hr-real-completion")
    assert build_hr_analytics(career) == after


def test_simulation_is_not_participation_and_no_history_does_not_become_inactivity(workspace):
    _, db, career = workspace
    load(db)
    request = c.CompletionRequest(expected_state_version=1, event_id=EVENT, mode="demo_simulation")
    career.complete(EMPLOYEE, {"id": "hr-testactor", "role": "hr"}, request, "hr-simulation")
    result = build_hr_analytics(career)
    assert result["excluded_simulations"] == 1
    assert result["history_records_in_period"] == result["employees_with_history"] == 0
    assert result["employees_with_completion_in_period"] == 0
    assert not result["participation"]
    person = next(item for item in result["attention"] if item["profile"]["employee_id"] == EMPLOYEE)
    assert [row["code"] for row in person["reasons"]] == ["no_history", "recommendation_missing"]
    assert person["last_completed_date"] is None


def test_empty_loaded_cohort_and_deterministic_order(workspace):
    _, db, career = workspace
    load(db, profiles=[])
    result = build_hr_analytics(career)
    c.HRAnalyticsResponse.model_validate(result)
    assert result["employee_count"] == result["employees_with_goal"] == 0
    assert result["skill_gaps"] == result["attention"] == result["participation"] == []
    assert build_hr_analytics(career) == result


def test_analytics_does_not_call_ai_or_write_database(workspace):
    _, db, career = workspace
    load(db)

    def unexpected_call(*args, **kwargs):
        raise AssertionError("Analytics must not call AI")

    career.adapter.recommend = unexpected_call
    before = db.settings.database_path.read_bytes()
    first = build_hr_analytics(career)
    assert build_hr_analytics(career) == first
    assert db.settings.database_path.read_bytes() == before
    ids = [item["profile"]["employee_id"] for item in first["attention"]]
    assert ids == sorted(ids)


def save_recommendation(career, status="ok"):
    async def select(context):
        if status == "ok":
            return valid_ai_result(context)
        return c.RecommendationResult(status=status, engine="oleg", recommendations=[])

    career.adapter._recommend = None if status == "not_configured" else select
    return asyncio.run(career.recommend(EMPLOYEE, request_recommendations()))


def attention_codes(career, employee_id=EMPLOYEE):
    result = c.HRAnalyticsResponse.model_validate(build_hr_analytics(career))
    return {
        reason.code
        for person in result.attention
        if person.profile.employee_id == employee_id
        for reason in person.reasons
    }


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (None, {"recommendation_missing"}),
        ("ok", set()),
        ("unavailable", {"recommendation_unavailable"}),
        ("not_configured", {"ai_not_configured"}),
    ],
)
def test_next_step_uses_saved_result_even_with_recent_completion_and_candidates(workspace, status, expected):
    _, db, career = workspace
    load(db, profiles=[profile()], records=[record("HR_RECENT", event_id="WORKFLOW_MANDATORY")])
    assert career._candidates(career.snapshot(EMPLOYEE))
    if status:
        save_recommendation(career, status)
    assert attention_codes(career) == expected


def test_latest_failure_is_not_hidden_by_previous_success_or_created_at_order(workspace):
    _, db, career = workspace
    load(db, profiles=[profile()], records=[record("HR_RECENT", event_id="WORKFLOW_MANDATORY")])
    first = save_recommendation(career)
    assert attention_codes(career) == set()
    second = save_recommendation(career, "unavailable")
    # Match /latest's insertion ordering even if timestamps tie or a clock moves.
    with db.connect() as conn:
        conn.execute(
            "UPDATE recommendation_runs SET created_at=? WHERE recommendation_id=?",
            ("2099-01-01T00:00:00+00:00", first["recommendation_id"]),
        )
    assert career.latest(EMPLOYEE)["recommendation_id"] == second["recommendation_id"]
    assert attention_codes(career) == {"recommendation_unavailable"}


@pytest.mark.parametrize("mutation", ["completion", "import", "goal"])
def test_saved_recommendation_becomes_stale_after_state_change(workspace, mutation):
    _, db, career = workspace
    load(db, profiles=[profile()], records=[record("HR_RECENT", event_id="WORKFLOW_MANDATORY")])
    saved = save_recommendation(career)
    user = {"id": "hr-testactor", "role": "hr"}
    if mutation == "completion":
        career.complete(EMPLOYEE, user, completion(), "hr-invalidates-next-step")
    elif mutation == "import":
        commit_batch(db, [json_source("employees.json", {"meta": META, "employees": [profile(OTHER)]})])
    else:
        career.set_goal(
            EMPLOYEE,
            c.GoalUpdateRequest(
                expected_state_version=1,
                goal=c.Goal(target_role="Synthetic Role", target_grade="Senior"),
            ),
        )
    assert career.latest(EMPLOYEE)["state_version"] == saved["state_version"]
    assert attention_codes(career) == {"recommendation_stale"}
    save_recommendation(career)
    assert attention_codes(career) == set()


@pytest.mark.parametrize("status", ["unavailable", "not_configured"])
def test_stale_status_takes_precedence_over_previous_failure(workspace, status):
    _, db, career = workspace
    load(db, profiles=[profile()], records=[record("HR_RECENT", event_id="WORKFLOW_MANDATORY")])
    save_recommendation(career, status)
    career.complete(EMPLOYEE, {"id": "hr-testactor", "role": "hr"}, completion(), "hr-stale-failure")
    assert attention_codes(career) == {"recommendation_stale"}


@pytest.mark.parametrize("corrupt", ["empty_ok", "malformed_json"])
def test_invalid_current_saved_response_is_not_a_next_step(workspace, corrupt):
    _, db, career = workspace
    load(db, profiles=[profile()], records=[record("HR_RECENT", event_id="WORKFLOW_MANDATORY")])
    saved = save_recommendation(career)
    saved["recommendations"] = []
    payload = json.dumps(saved) if corrupt == "empty_ok" else "{invalid"
    with db.connect() as conn:
        conn.execute(
            "UPDATE recommendation_runs SET response_json=? WHERE recommendation_id=?",
            (payload, saved["recommendation_id"]),
        )
    assert attention_codes(career) == {"recommendation_unavailable"}


def test_achieved_goal_needs_no_new_recommendation(workspace):
    _, db, career = workspace
    achieved = profile()
    achieved["skills"] = {"WORKFLOW_HARD": 3, "WORKFLOW_SOFT": 3}
    load(db, profiles=[achieved], records=[record("HR_RECENT", event_id="WORKFLOW_MANDATORY")])
    assert attention_codes(career) == set()
    response = save_recommendation(career, "not_configured")
    assert response["status"] == "no_candidates"
    assert attention_codes(career) == set()


def test_latest_and_profiles_share_snapshot_during_concurrent_completion(tmp_path, monkeypatch):
    settings = Settings(
        database_path=tmp_path / "hr-concurrent.sqlite3",
        app_env="test",
        sqlite_wal=True,
        sqlite_local_disk=True,
    )
    db = Database(settings)
    db.migrate()
    career = CareerService(db, AIAdapter())
    load(db, profiles=[profile()], records=[record("HR_RECENT", event_id="WORKFLOW_MANDATORY")])
    original_snapshot = career._snapshot
    changed = False

    def snapshot_with_concurrent_write(conn, employee_id):
        nonlocal changed
        snapshot = original_snapshot(conn, employee_id)
        if not changed:
            changed = True
            career.complete(EMPLOYEE, {"id": "hr-testactor", "role": "hr"}, completion(), "hr-concurrent")
            save_recommendation(career)
        return snapshot

    monkeypatch.setattr(career, "_snapshot", snapshot_with_concurrent_write)
    first = c.HRAnalyticsResponse.model_validate(build_hr_analytics(career))
    assert first.state_version == 1
    assert {reason.code for person in first.attention for reason in person.reasons} == {
        "recommendation_missing"
    }
    current = c.HRAnalyticsResponse.model_validate(build_hr_analytics(career))
    assert current.state_version == 2
    assert current.attention == []


@pytest.fixture(scope="module")
def password_hash():
    return hash_password(PASSWORD)


def test_hr_route_enforces_session_role_valid_window_and_schema(workspace, password_hash):
    settings, db, _ = workspace
    load(db)
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO accounts(id,username,password_hash,role,employee_id) VALUES (?,?,?,?,?)",
            [
                ("hr-employee", "hr.employee", password_hash, "employee", EMPLOYEE),
                ("hr-reviewer", "hr.reviewer", password_hash, "hr", None),
            ],
        )
    app = create_app(settings)
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/api/hr/analytics")
        assert response.status_code == 401
        client.post(
            "/api/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "hr.employee", "password": PASSWORD},
        ).raise_for_status()
        assert client.get("/api/hr/analytics").status_code == 403
        client.post(
            "/api/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "hr.reviewer", "password": PASSWORD},
        ).raise_for_status()
        response = client.get("/api/hr/analytics?window_days=30")
        assert response.status_code == 200
        payload = c.HRAnalyticsResponse.model_validate(response.json())
        assert payload.window_days == 30 and payload.period_start == date(2026, 9, 2)
        assert response.headers["Cache-Control"] == "no-store"
        for invalid in (0, 366, "bad", "2.5"):
            assert client.get(f"/api/hr/analytics?window_days={invalid}").status_code == 422
        assert "/api/hr/analytics" in client.get("/openapi.json").json()["paths"]


def test_missing_dataset_fails_honestly(workspace):
    _, _, career = workspace
    with pytest.raises(APIError) as exc:
        build_hr_analytics(career)
    assert exc.value.status == 503 and exc.value.code == "DATASET_NOT_LOADED"
