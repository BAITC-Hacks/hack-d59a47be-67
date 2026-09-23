"""Backend-prepared AI evidence, using independent synthetic data only.

The JSON fragments below are inspected as test assertions about factual content;
they are not a second runtime schema or a parser for AI consumers. The optional
plugin test replaces its provider before any call and never accesses a real key.
"""

import copy
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.app import contracts as c
from backend.app.ai_adapter import AIAdapter
from backend.app.auth import hash_password
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.service import CareerService
from backend.tests.test_workflow import (
    EMPLOYEE,
    EVENT,
    ORIGIN,
    OTHER,
    PASSWORD,
    USER,
    commit_batch,
    login,
    source_batch,
)

SOFT = "WORKFLOW_SOFT"
HARD = "WORKFLOW_HARD"
TARGET_ROLE = "Synthetic Target Role"


def service_with_target(tmp_path, *, explicit=True, critical_skills=None):
    sources = source_batch()
    catalog = json.loads(sources[0]["content"])
    employees = json.loads(sources[2]["content"])
    critical = [SOFT] if critical_skills is None else critical_skills
    # The lowest skill is noncritical; the critical target gap is smaller.
    employees["employees"][0]["skills"] = {HARD: 0, SOFT: 3}
    for skill in critical:
        if skill not in {item["skill_id"] for item in catalog["skills"]}:
            catalog["skills"].append(
                {
                    "skill_id": skill,
                    "name": "Synthetic critical skill",
                    "type": "hard",
                    "category": "Synthetic",
                    "description": "Independent boundary fixture",
                }
            )
    target = {
        "role": TARGET_ROLE if explicit else "Synthetic Role",
        "grade": "Senior" if explicit else "Middle",
        "required_skills": {HARD: 4, SOFT: 4, **dict.fromkeys(critical, 4)},
        "critical_skills": critical,
    }
    if explicit:
        catalog["role_profiles"].append(target)
        employees["employees"][0]["career_goal"] = {
            "target_role": TARGET_ROLE,
            "target_grade": "Senior",
        }
    else:
        catalog["role_profiles"][1] = target
    # The current role explicitly disagrees with the target's critical skills.
    assert catalog["role_profiles"][0]["critical_skills"] == [HARD]
    employees["employees"][0]["manager_id"] = OTHER
    employees["employees"][1]["grade"] = "Lead"
    sources[0]["content"] = json.dumps(catalog)
    sources[2]["content"] = json.dumps(employees)
    settings = Settings(database_path=tmp_path / "synthetic-context.sqlite3", app_env="test")
    db = Database(settings)
    db.migrate()
    commit_batch(db, sources)
    return settings, db, CareerService(db, AIAdapter())


def context_for(service, snap=None):
    snapshot = service.snapshot(EMPLOYEE) if snap is None else snap
    return service._context(snapshot, service._candidates(snapshot), 3)


def factual_json(fact, prefix):
    assert fact.fact.startswith(prefix)
    value, end = json.JSONDecoder().raw_decode(fact.fact[len(prefix) :])
    assert fact.fact[len(prefix) + end :].startswith(". ")
    return value


def history_facts(context):
    return {
        (fact.subject_id, payload["scope"]): (fact, payload)
        for fact in context.facts
        if fact.kind == "history"
        for payload in [factual_json(fact, "Recorded participation: ")]
    }


@pytest.mark.parametrize("explicit", [True, False], ids=["explicit-target", "next-grade"])
def test_goal_and_gap_criticality_come_from_resolved_target(tmp_path, explicit):
    _, _, service = service_with_target(tmp_path, explicit=explicit)
    context = context_for(service)
    goal = factual_json(next(f for f in context.facts if f.kind == "goal"), "Resolved career goal: ")
    assert goal == {
        "current_role": "Synthetic Role",
        "current_grade": "Junior",
        "target_role": TARGET_ROLE if explicit else "Synthetic Role",
        "target_grade": "Senior" if explicit else "Middle",
        "critical_skills": [SOFT],
        "critical_skills_part": 1,
        "critical_skills_parts": 1,
    }
    gaps = {fact.subject_id: fact.fact for fact in context.facts if fact.kind == "gap"}
    assert "critical for target: True" in gaps[SOFT]
    assert "critical for target: False" in gaps[HARD]


def test_target_without_critical_skills_does_not_inherit_current_role(tmp_path):
    _, _, service = service_with_target(tmp_path, critical_skills=[])
    context = context_for(service)
    goals = [factual_json(f, "Resolved career goal: ") for f in context.facts if f.kind == "goal"]
    assert len(goals) == 1
    assert goals[0]["critical_skills"] == []
    assert all("critical for target: False" in fact.fact for fact in context.facts if fact.kind == "gap")


def test_history_counts_keep_repeated_records_and_distinguish_event_from_cohort(tmp_path):
    _, _, service = service_with_target(tmp_path)
    snap = service.snapshot(EMPLOYEE)
    template = copy.deepcopy(snap["history"][0])
    template.pop("effective_date", None)
    snap["history"].extend(
        {
            **template,
            "record_id": f"SYNTH_EVENT_{index}",
            "event_id": EVENT,
            "status": status,
            "date": f"2026-08-0{index}",
        }
        for index, status in enumerate(["dropped", "no_show", "declined", "overdue"], 1)
    )
    # Identical employee/event/date, distinct record_id: neither assignment disappears.
    snap["history"].extend(
        {**template, "record_id": record_id, "status": "completed", "date": "2026-08-06"}
        for record_id in ("SYNTH_REPEAT_1", "SYNTH_REPEAT_2")
    )
    snap["history"].append(
        {
            **template,
            "record_id": "SYNTH_EXACT_COMPLETION",
            "status": "completed",
            "date": "2026-08-07",
            "date_source": "completed_at",
            "completed_at": "2026-10-01T10:30:00+05:00",
            "effective_date": "2026-10-01",
        }
    )
    snap["events"]["SYNTH_OTHER_TYPE"] = {
        **snap["events"][EVENT],
        "event_id": "SYNTH_OTHER_TYPE",
        "type": "workshop",
    }
    snap["events"]["SYNTH_OTHER_FORMAT"] = {
        **snap["events"][EVENT],
        "event_id": "SYNTH_OTHER_FORMAT",
        "format": "online",
        "upcoming_sessions": [],
    }
    snap["history"].extend(
        [
            {
                **template,
                "record_id": "SYNTH_FUTURE",
                "event_id": EVENT,
                "status": "declined",
                "date": "2026-10-02",
            },
            {
                **template,
                "record_id": "SYNTH_OTHER_COHORT",
                "event_id": "SYNTH_OTHER_TYPE",
                "status": "completed",
                "date": "2026-07-01",
            },
            {
                **template,
                "record_id": "SYNTH_OTHER_FORMAT_RECORD",
                "event_id": "SYNTH_OTHER_FORMAT",
                "status": "completed",
                "date": "2026-07-02",
            },
        ]
    )
    facts = history_facts(context_for(service, snap))
    _, same_event = facts[(EVENT, "event")]
    _, comparable = facts[(EVENT, "same_type_format_other_events")]
    _, overall = facts[("profile", "profile")]
    assert same_event == {
        "scope": "event",
        "as_of": "2026-10-01",
        "observed_from": "2026-08-01",
        "observed_to": "2026-08-04",
        "sample_size": 4,
        "status_counts": {
            "completed": 0,
            "in_progress": 0,
            "dropped": 1,
            "no_show": 1,
            "declined": 1,
            "overdue": 1,
        },
        "date_sources": {"historical_proxy": 4, "completed_at": 0},
    }
    assert comparable == {
        "scope": "same_type_format_other_events",
        "event_type": "course",
        "event_format": "self_paced",
        "as_of": "2026-10-01",
        "observed_from": "2026-08-06",
        "observed_to": "2026-10-01",
        "sample_size": 4,
        "status_counts": {
            "completed": 3,
            "in_progress": 1,
            "dropped": 0,
            "no_show": 0,
            "declined": 0,
            "overdue": 0,
        },
        "date_sources": {"historical_proxy": 3, "completed_at": 1},
    }
    assert overall["sample_size"] == 10
    assert overall["observed_from"] == "2026-07-01"
    assert overall["observed_to"] == "2026-10-01"
    assert overall["status_counts"] == {
        "completed": 5,
        "in_progress": 1,
        "dropped": 1,
        "no_show": 1,
        "declined": 1,
        "overdue": 1,
    }
    assert overall["date_sources"] == {"historical_proxy": 9, "completed_at": 1}


def test_no_history_is_explicit_neutral_evidence_for_each_candidate(tmp_path):
    _, _, service = service_with_target(tmp_path)
    snap = service.snapshot(EMPLOYEE)
    snap["history"] = []
    context = context_for(service, snap)
    facts = history_facts(context)
    assert len(facts) == 1 + 2 * len(context.eligible_candidates)
    for fact, summary in facts.values():
        assert summary["sample_size"] == 0
        assert summary["observed_from"] is None
        assert summary["observed_to"] is None
        assert summary["as_of"] == "2026-10-01"
        assert not any(summary["status_counts"].values())
        assert summary["date_sources"] == {"historical_proxy": 0, "completed_at": 0}
        assert "zero samples, do not establish stable preferences" in fact.fact
        assert "not proof of timeliness" in fact.fact


def test_context_omits_personal_identifiers_and_raw_records(tmp_path):
    _, _, service = service_with_target(tmp_path)
    snap = service.snapshot(EMPLOYEE)
    context = context_for(service, snap)
    serialized = context.model_dump_json()
    assert context.profile.profile_ref == "anonymous-profile"
    for private in (
        "employee_id",
        "full_name",
        "manager_id",
        "record_id",
        EMPLOYEE,
        OTHER,
        snap["employee"]["full_name"],
        snap["history"][0]["record_id"],
    ):
        assert private not in serialized
    assert c.RecommendationContext.model_validate_json(serialized) == context


def test_large_target_critical_list_is_complete_and_bounded(tmp_path):
    critical = [f"SYNTH_CRITICAL_{number:03d}_" + "x" * 109 for number in range(30)]
    assert all(len(skill) <= 128 for skill in critical)
    _, _, service = service_with_target(tmp_path, critical_skills=critical)
    context = context_for(service)
    goals = [factual_json(f, "Resolved career goal: ") for f in context.facts if f.kind == "goal"]
    assert len(goals) > 1
    assert [skill for part in goals for skill in part["critical_skills"]] == critical
    assert [part["critical_skills_part"] for part in goals] == list(range(1, len(goals) + 1))
    assert all(part["critical_skills_parts"] == len(goals) for part in goals)
    assert all(len(fact.fact) <= 2000 for fact in context.facts)
    assert len({fact.evidence_id for fact in context.facts}) == len(context.facts)
    c.RecommendationContext.model_validate_json(context.model_dump_json())


def test_optional_real_plugin_uses_backend_context_and_persists_cards(tmp_path, monkeypatch):
    plugin = pytest.importorskip("backend.app.ai", reason="AI PR is an optional separate integration")
    settings, db, service = service_with_target(tmp_path)
    seen = []

    class SyntheticProvider:
        async def select_events(self, payload, *, instructions):
            assert instructions
            seen.append(payload)
            return ["WORKFLOW_SOFT_COURSE"]

    fake_settings = plugin.AISettings(api_key=SecretStr("synthetic-never-sent"))
    monkeypatch.setattr(plugin.AISettings, "from_env", classmethod(lambda cls: fake_settings))
    monkeypatch.setattr(plugin, "AsyncOpenAIProvider", lambda **kwargs: SyntheticProvider())
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO accounts VALUES (?,?,?,?,?)",
            (USER["id"], USER["username"], hash_password(PASSWORD), "employee", EMPLOYEE),
        )
    app = create_app(replace(settings, ai_enabled=True))
    with TestClient(app, base_url=ORIGIN) as client:
        headers = login(client)
        response = client.post(
            f"/api/employees/{EMPLOYEE}/recommendations",
            headers=headers,
            json={"scenario_date": "2026-10-01", "limit": 3},
        )
        assert response.status_code == 200, response.text
        result = c.RecommendationResponse.model_validate(response.json())
        assert (result.status, result.engine) == ("ok", "oleg")
        assert len(seen) == 1
        context = c.RecommendationContext.model_validate(seen[0])
        goal = factual_json(next(f for f in context.facts if f.kind == "goal"), "Resolved career goal: ")
        assert goal["critical_skills"] == [SOFT]
        assert goal["target_role"] == TARGET_ROLE
        levels = {skill.skill_id: skill.level for skill in context.current_skills}
        gaps = {gap.skill_id: gap.gap for gap in context.gaps}
        assert levels[HARD] < levels[SOFT]
        assert gaps[HARD] > gaps[SOFT] > 0
        card = result.recommendations[0]
        assert card.event_id == "WORKFLOW_SOFT_COURSE"
        assert card.title and card.effects[0].skill_id == SOFT
        facts = {fact.evidence_id: fact for fact in context.facts}
        selected_facts = [facts[key] for key in card.explanation.evidence_ids]
        assert {fact.kind for fact in selected_facts} == {"goal", "gap", "history", "candidate"}
        assert card.explanation.text == " ".join(fact.fact for fact in selected_facts)
        assert client.get(f"/api/employees/{OTHER}").status_code == 403
        assert (
            client.post(
                f"/api/employees/{OTHER}/recommendations",
                headers=headers,
                json={"scenario_date": "2026-10-01", "limit": 3},
            ).status_code
            == 403
        )
        assert client.get(f"/api/employees/{OTHER}/recommendations/latest").status_code == 403
        assert len(seen) == 1
    # Reconstructed application reads complete saved cards without a provider call.
    with TestClient(create_app(settings), base_url=ORIGIN) as restarted:
        login(restarted)
        latest = restarted.get(f"/api/employees/{EMPLOYEE}/recommendations/latest")
        assert latest.status_code == 200
        assert latest.json() == response.json()
    assert service.latest(EMPLOYEE) == response.json()
    assert len(seen) == 1
