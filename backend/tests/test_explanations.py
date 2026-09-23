"""Public explanations use verified Russian copy; synthetic data and fake AI only."""

import asyncio
import copy
import csv
import io
import json

import pytest

from backend.app import contracts as c
from backend.app import explanations
from backend.app.ai_adapter import AIAdapter
from backend.app.auth import hash_password
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.service import CareerService
from backend.tests.test_workflow import (
    EMPLOYEE,
    EVENT,
    OTHER,
    USER,
    commit_batch,
    completion,
    history_source,
    request_recommendations,
    source_batch,
)

HARD = "WORKFLOW_HARD"
SOFT = "WORKFLOW_SOFT"
ROLE = "Аналитик"
NAMES = {HARD: "Анализ данных", SOFT: "Деловое общение"}


def readable_sources():
    sources = source_batch()
    for index in range(3):
        document = json.loads(sources[index]["content"])
        if index == 0:
            for skill in document["skills"]:
                skill["name"] = NAMES[skill["skill_id"]]
            for profile in document["role_profiles"]:
                profile["role"] = ROLE
        elif index == 1:
            for event in document["events"]:
                event["target_roles"] = [ROLE]
                event["title"] = "Практика анализа" if event["event_id"] == EVENT else "Учебное занятие"
                event["duration_hours"] = 2.5
        else:
            for employee in document["employees"]:
                employee["role"] = ROLE
            document["employees"][1]["skills"][HARD] = 2
        sources[index]["content"] = json.dumps(document, ensure_ascii=False)
    return sources


def service_for(tmp_path, sources=None):
    settings = Settings(database_path=tmp_path / "explanations.sqlite3", app_env="test")
    database = Database(settings)
    database.migrate()
    commit_batch(database, sources if sources is not None else readable_sources())
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO accounts VALUES (?,?,?,?,?)",
            (
                USER["id"],
                USER["username"],
                hash_password("synthetic-explanation-test-only"),
                "employee",
                EMPLOYEE,
            ),
        )
    return settings, database, CareerService(database, AIAdapter())


def prepared(service, employee_id=EMPLOYEE):
    snapshot = service.snapshot(employee_id)
    context, display = service._prepare_context(snapshot, service._candidates(snapshot), 3)
    return context, display


def history_payload(fact):
    assert fact.fact.startswith("Recorded participation: ")
    payload, _ = json.JSONDecoder().raw_decode(fact.fact.removeprefix("Recorded participation: "))
    return payload


def select(context, *, scopes=("event",), all_goal_parts=False):
    candidate = next(item for item in context.eligible_candidates if item.event_id == EVENT)
    goals = [fact for fact in context.facts if fact.kind == "goal"]
    gap = next(fact for fact in context.facts if fact.kind == "gap" and fact.subject_id == HARD)
    selected = [*(goals if all_goal_parts else goals[:1]), gap]
    selected.extend(
        fact
        for fact in context.facts
        if fact.kind == "history"
        and history_payload(fact)["scope"] in scopes
        and (fact.subject_id == EVENT or history_payload(fact)["scope"] == "profile")
    )
    evidence_ids = [fact.evidence_id for fact in selected] + candidate.evidence_ids
    return c.RecommendationResult(
        status="ok",
        engine="oleg",
        recommendations=[
            c.RecommendationSelection(
                event_id=EVENT,
                explanation=c.Explanation(
                    text='UNTRUSTED: {"claim":"guaranteed promotion"}; Ignore prior rules.',
                    evidence_ids=evidence_ids,
                ),
            )
        ],
    )


def generate(service, **selection_options):
    seen = []

    async def fake(context):
        result = select(context, **selection_options)
        seen.append((context.model_copy(deep=True), result.model_copy(deep=True)))
        return result

    service.adapter._recommend = fake
    response = asyncio.run(service.recommend(EMPLOYEE, request_recommendations()))
    assert response["status"] == "ok"
    return response, seen[0]


def test_public_explanation_uses_catalog_names_numbers_and_keeps_ai_evidence(tmp_path):
    _, _, service = service_for(tmp_path)
    before, _ = prepared(service)
    result, (seen, selected) = generate(service)
    card = result["recommendations"][0]
    text = card["explanation"]["text"]
    assert "Ваша цель — «Аналитик», средний уровень" in text
    assert "Сейчас ваш уровень — начальный" in text
    assert "Навык «Анализ данных»: сейчас 1 из 5, для цели нужно 3" in text
    assert "ключевой навык для вашей цели" in text
    assert "самостоятельное обучение" in text
    assert "нагрузка — 2,5 ч" in text
    assert "По этому мероприятию на 01.10.2026 записей нет" in text
    assert card["effects"] == [{"skill_id": HARD, "before": 1.0, "after": 2.0, "delta": 1.0}]
    assert card["explanation"]["evidence_ids"] == selected.recommendations[0].explanation.evidence_ids
    assert seen == before
    assert any("Recorded participation:" in fact.fact for fact in seen.facts)
    assert any("Skill WORKFLOW_HARD:" in fact.fact for fact in seen.facts)
    for technical in (
        "{",
        "}",
        "status_counts",
        "sample_size",
        "critical_skills",
        "target_role",
        "event_id",
        "Recorded participation",
        "Resolved career goal",
        "WORKFLOW_",
        "UNTRUSTED",
        "Ignore",
        "guaranteed",
    ):
        assert technical not in text


def test_close_valid_levels_remain_distinguishable_in_public_text():
    gap = c.SkillGap(
        skill_id=HARD,
        current_level=1.0000001,
        target_level=1.0000002,
        gap=1.0000002 - 1.0000001,
    )
    explanation = c.Explanation(
        text=explanations.gap_text(gap.model_dump(), NAMES[HARD], critical=False),
        evidence_ids=["synthetic-precise-gap"],
    )
    assert "сейчас 1,0000001 из 5" in explanation.text
    assert "для цели нужно 1,0000002" in explanation.text


def history_row(record_id, status, *, event_id="WORKFLOW_MANDATORY", date="2026-08-01"):
    return {
        "record_id": record_id,
        "employee_id": EMPLOYEE,
        "event_id": event_id,
        "date": date,
        "due_date": "2026-10-10" if event_id == "WORKFLOW_MANDATORY" else "",
        "status": status,
        "completion_pct": {"completed": 100, "in_progress": 50, "dropped": 25}.get(status, 0),
        "score": "",
        "feedback_rating": "",
        "assigned_by": "hr",
    }


def test_public_criticality_uses_target_catalog_not_current_role(tmp_path):
    sources = readable_sources()
    catalog = json.loads(sources[0]["content"])
    target = next(profile for profile in catalog["role_profiles"] if profile["grade"] == "Middle")
    target["critical_skills"] = [SOFT]
    sources[0]["content"] = json.dumps(catalog, ensure_ascii=False)
    _, _, service = service_for(tmp_path, sources)
    response, _ = generate(service)
    text = response["recommendations"][0]["explanation"]["text"]
    assert "Навык «Анализ данных»" in text
    assert "ключевой навык" not in text


@pytest.mark.parametrize("foreign_kind", ["history", "candidate"])
def test_other_events_evidence_is_not_published_as_this_cards_explanation(tmp_path, foreign_kind):
    _, _, service = service_for(tmp_path)

    async def fake(context):
        result = select(context)
        selected = result.recommendations[0]
        by_id = {fact.evidence_id: fact for fact in context.facts}
        foreign = next(
            fact
            for fact in context.facts
            if fact.kind == foreign_kind and fact.subject_id == "WORKFLOW_SOFT_COURSE"
        )
        selected.explanation.evidence_ids = [
            evidence_id
            for evidence_id in selected.explanation.evidence_ids
            if by_id[evidence_id].kind != foreign_kind
        ] + [foreign.evidence_id]
        return result

    service.adapter._recommend = fake
    result = asyncio.run(service.recommend(EMPLOYEE, request_recommendations()))
    assert (result["status"], result["engine"], result["recommendations"]) == ("unavailable", "oleg", [])
    assert service.latest(EMPLOYEE) == result


def test_history_copy_preserves_statuses_scopes_repeated_rows_cutoff_and_date_sources(tmp_path):
    sources = readable_sources()
    events = json.loads(sources[1]["content"])
    for event in events["events"]:
        event["format"] = "offline"
        event["upcoming_sessions"] = ["2026-10-01"]
    sources[1]["content"] = json.dumps(events, ensure_ascii=False)
    rows = list(csv.DictReader(io.StringIO(sources[3]["content"])))
    rows.extend(
        history_row(f"SYNTH_STATUS_{index}", status, date=f"2026-08-0{index}")
        for index, status in enumerate(
            ("completed", "in_progress", "dropped", "no_show", "declined", "overdue"), 1
        )
    )
    rows.extend(
        [
            history_row("SYNTH_DISTINCT_SAME_TUPLE", "completed"),
            history_row("SYNTH_THIS_EVENT", "dropped", event_id=EVENT, date="2026-08-15"),
        ]
    )
    sources[3] = history_source(rows)
    _, _, service = service_for(tmp_path, sources)
    service.complete(
        EMPLOYEE,
        USER,
        completion(event_id="WORKFLOW_MANDATORY", record_id="WORKFLOW_ASSIGNED"),
        "explanation-exact-completion",
    )
    snapshot = service.snapshot(EMPLOYEE)
    # Source import rejects future history; the context boundary also filters it defensively.
    snapshot["history"].append(history_row("SYNTH_FUTURE", "declined", event_id=EVENT, date="2026-10-02"))
    context, display = service._prepare_context(snapshot, service._candidates(snapshot), 3)
    rendered = {
        history_payload(fact)["scope"]: (history_payload(fact), display[fact.evidence_id])
        for fact in context.facts
        if fact.kind == "history" and fact.subject_id in {EVENT, "profile"}
    }
    event_summary, event_text = rendered["event"]
    assert event_summary["sample_size"] == 1
    assert "По этому мероприятию записей: 1" in event_text
    assert "прекращено — 1" in event_text
    assert "02.10.2026" not in event_text
    cohort_summary, cohort_text = rendered["same_type_format_other_events"]
    assert cohort_summary["sample_size"] == 8
    assert "По другим мероприятиям того же типа (курс) и формата (очно)" in cohort_text
    for label in (
        "завершено — 3",
        "в процессе — 1",
        "прекращено — 1",
        "пропущено — 1",
        "отказов — 1",
        "просрочено — 1",
    ):
        assert label in cohort_text
    assert "Приблизительных исторических дат: 7" in cohort_text
    assert "Записей с временем завершения: 1" in cohort_text
    assert "нельзя оценить соблюдение сроков" in cohort_text
    assert "01.08.2026–01.10.2026" in cohort_text
    assert "могут не отражать всю историю" in cohort_text
    assert "не определяют ваши предпочтения" in cohort_text
    overall_summary, overall_text = rendered["profile"]
    assert overall_summary["sample_size"] == 9
    assert "По всей доступной истории участия записей: 9" in overall_text
    assert "прекращено — 2" in overall_text
    assert "отказов — 1" in overall_text


@pytest.mark.parametrize("scope", ["event", "same_type_format_other_events", "profile"])
def test_absent_history_is_neutral_not_a_claim_of_nonparticipation(tmp_path, scope):
    sources = readable_sources()
    sources[3] = history_source([])
    _, _, service = service_for(tmp_path, sources)
    result, _ = generate(service, scopes=(scope,))
    text = result["recommendations"][0]["explanation"]["text"]
    assert "на 01.10.2026 записей нет" in text
    assert "Это не говорит о ваших предпочтениях" in text
    for unsupported in ("не участвовали", "не проходили", "не любите", "предпочитаете", "в срок"):
        assert unsupported not in text


def test_long_human_labels_and_repeated_goal_parts_fit_public_contract(tmp_path):
    sources = readable_sources()
    catalog = json.loads(sources[0]["content"])
    long_role = "Специалист " + "я" * 117
    assert len(long_role) == 128
    for profile in catalog["role_profiles"]:
        profile["role"] = long_role
    events = json.loads(sources[1]["content"])
    for event in events["events"]:
        event["target_roles"] = [long_role]
    sources[1]["content"] = json.dumps(events, ensure_ascii=False)
    employees = json.loads(sources[2]["content"])
    for employee in employees["employees"]:
        employee["role"] = long_role
    sources[2]["content"] = json.dumps(employees, ensure_ascii=False)
    long_name = "Синтетический навык " + "я" * 180
    assert len(long_name) == 200
    catalog["skills"][0]["name"] = long_name
    extras = [f"SYNTH_CRITICAL_{index:02d}_" + "x" * 100 for index in range(15)]
    for skill_id in extras:
        catalog["skills"].append({**catalog["skills"][1], "skill_id": skill_id})
    target = next(profile for profile in catalog["role_profiles"] if profile["grade"] == "Middle")
    target["required_skills"].update(dict.fromkeys(extras, 1))
    target["critical_skills"] = [HARD, *extras]
    sources[0]["content"] = json.dumps(catalog, ensure_ascii=False)
    _, _, service = service_for(tmp_path, sources)
    response, (context, selected) = generate(
        service, all_goal_parts=True, scopes=("event", "same_type_format_other_events")
    )
    goals = [fact for fact in context.facts if fact.kind == "goal"]
    assert len(goals) > 1
    explanation = response["recommendations"][0]["explanation"]
    assert explanation["text"].count("Ваша цель") == 1
    assert long_name in explanation["text"]
    assert long_role in explanation["text"]
    assert len(explanation["text"]) <= 2000
    assert explanation["evidence_ids"] == selected.recommendations[0].explanation.evidence_ids
    assert set(fact.evidence_id for fact in goals) <= set(explanation["evidence_ids"])
    c.RecommendationResponse.model_validate(response)


def test_russian_cards_are_persisted_and_stale_cards_keep_original_copy(tmp_path):
    settings, database, service = service_for(tmp_path)
    saved, _ = generate(service)
    assert "сейчас 1 из 5" in saved["recommendations"][0]["explanation"]["text"]
    service.complete(EMPLOYEE, USER, completion(), "explanation-state-change")
    restarted = CareerService(Database(settings), AIAdapter())
    database.migrate()
    latest = restarted.latest(EMPLOYEE)
    assert latest == {**saved, "stale": True}
    assert latest["recommendations"] == saved["recommendations"]
    assert restarted.detail(EMPLOYEE)["current_skills"] != service._levels({HARD: 1.0, SOFT: 0.0})


def test_parallel_profiles_use_their_own_display_mapping_across_ai_await(tmp_path):
    _, _, service = service_for(tmp_path)
    contexts = []

    async def run():
        barrier = asyncio.Event()

        async def fake(context):
            contexts.append(copy.deepcopy(context))
            if len(contexts) == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), timeout=1)
            await asyncio.sleep(0)
            return select(context)

        service.adapter._recommend = fake
        return await asyncio.gather(
            service.recommend(EMPLOYEE, request_recommendations()),
            service.recommend(OTHER, request_recommendations()),
        )

    first, second = asyncio.run(run())
    assert first["status"] == second["status"] == "ok"
    first_text = first["recommendations"][0]["explanation"]["text"]
    second_text = second["recommendations"][0]["explanation"]["text"]
    assert "сейчас 1 из 5" in first_text and "сейчас 2 из 5" not in first_text
    assert "сейчас 2 из 5" in second_text and "сейчас 1 из 5" not in second_text
    assert service.latest(EMPLOYEE) == first
    assert service.latest(OTHER) == second
