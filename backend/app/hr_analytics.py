"""Explainable HR projections from the same snapshots as employee profiles.

No model is called. One SQLite read transaction fixes the revision for the entire
cohort. Imported history dates are proxies and never establish punctuality,
motivation or a prediction about an employee. Simulations are not participation.
"""

from collections import Counter, defaultdict
from datetime import date, timedelta

from . import domain
from .contracts import RecommendationResponse
from .service import CareerService

STATUSES = ("completed", "in_progress", "dropped", "no_show", "declined", "overdue")


def _recommendation_reason(latest, revision: int) -> dict | None:
    """Explain a missing next step from stored outcomes, without invoking AI."""
    if latest is None:
        return {
            "code": "recommendation_missing",
            "message": "Есть полезные доступные активности, но подбор следующего шага ещё не выполнен.",
        }
    if latest["revision"] != revision:
        return {
            "code": "recommendation_stale",
            "message": "Сохранённый подбор устарел после изменения данных. Нужно обновить рекомендации.",
        }
    try:
        response = RecommendationResponse.model_validate_json(latest["response_json"])
    except ValueError:
        response = None
    if response is not None and response.status == "not_configured":
        return {
            "code": "ai_not_configured",
            "message": "Последний подбор не выполнен: AI не настроен. Доступные активности есть в каталоге.",
        }
    if response is not None and response.status == "ok" and response.recommendations:
        return None
    return {
        "code": "recommendation_unavailable",
        "message": "Последний подбор не дал проверенной рекомендации. Доступные активности есть; "
        "можно повторить подбор или обсудить следующий шаг.",
    }


def build_hr_analytics(career: CareerService, window_days: int = 90) -> dict:
    if not 1 <= window_days <= 365:
        raise ValueError("window_days must be between 1 and 365")
    with career.db.connect() as conn:
        conn.execute("BEGIN")
        state, catalog, event_catalog = career._state(conn)
        employees = conn.execute("SELECT employee_id FROM employee_profiles ORDER BY employee_id").fetchall()
        snapshots = [career._snapshot(conn, row[0]) for row in employees]
        # Use the same insertion order as CareerService.latest and the SAME read
        # transaction as skills/goals. Concurrent completions/imports must not
        # mix a later recommendation or revision into this HR snapshot.
        latest_runs = {
            row["employee_id"]: row
            for row in conn.execute(
                "SELECT employee_id, revision, response_json FROM recommendation_runs "
                "WHERE rowid IN (SELECT MAX(rowid) FROM recommendation_runs GROUP BY employee_id)"
            )
        }
    as_of = date.fromisoformat(state["scenario_date"])
    start = as_of - timedelta(days=window_days - 1)
    events = {item["event_id"]: item for item in event_catalog["events"]}
    skills = {item["skill_id"]: item for item in catalog["skills"]}
    targets = {(item["role"], item["grade"]): item for item in catalog["role_profiles"]}
    skill_stats = defaultdict(lambda: {"required": 0, "gaps": [], "critical": 0})
    event_counts = defaultdict(Counter)
    event_participants = defaultdict(set)
    attention = []
    employees_with_goal = employees_with_history = employees_with_completion = 0
    history_count = proxy_count = excluded_simulations = 0

    for snap in snapshots:
        goal = snap["goal"]
        if goal:
            employees_with_goal += 1
            target = targets[(goal["target_role"], goal["target_grade"])]
            gaps = {item["skill_id"]: item["gap"] for item in snap["gaps"]}
            for skill_id in target["required_skills"]:
                stats = skill_stats[skill_id]
                stats["required"] += 1
                if skill_id in gaps:
                    stats["gaps"].append(gaps[skill_id])
                    stats["critical"] += int(skill_id in target["critical_skills"])

        records = []
        period_records = []
        for record in snap["history"]:
            day = domain.effective_date(record)
            if day > as_of:
                continue
            if record.get("mode") == "demo_simulation":
                excluded_simulations += int(day >= start)
                continue
            records.append((day, record))
            if day < start:
                continue
            period_records.append(record)
            history_count += 1
            proxy_count += int(record.get("date_source", "historical_proxy") == "historical_proxy")
            event_counts[record["event_id"]][record["status"]] += 1
            event_participants[record["event_id"]].add(snap["employee"]["employee_id"])

        counts = Counter(row["status"] for row in period_records)
        employees_with_history += int(bool(records))
        employees_with_completion += int(counts["completed"] > 0)
        completed_dates = [day for day, row in records if row["status"] == "completed"]
        reasons = []
        if not records:
            reasons.append(
                {"code": "no_history", "message": "Нет истории участия на дату среза: недостаточно данных."}
            )
        elif not counts["completed"] and date.fromisoformat(snap["employee"]["hire_date"]) <= start:
            reasons.append(
                {
                    "code": "no_recent_completion",
                    "message": f"За {window_days} дней в истории нет завершённых активностей. "
                    "Нужно уточнить полноту данных и план развития.",
                }
            )
        if counts["no_show"] >= 2:
            reasons.append(
                {
                    "code": "repeated_no_show",
                    "message": f"За период отмечено неявок: {counts['no_show']}. "
                    "Причины пропусков в данных не указаны.",
                }
            )
        candidates = career._candidates(snap) if goal and snap["gaps"] else []
        if not goal:
            reasons.append(
                {"code": "no_target", "message": "Нет карьерной цели или следующего грейда в каталоге."}
            )
        elif snap["gaps"] and not candidates:
            reasons.append(
                {
                    "code": "no_candidates",
                    "message": "Есть разрывы до цели, но в каталоге нет полезного доступного шага. "
                    "Проверьте покрытие каталога и условия участия.",
                }
            )
        elif candidates:
            reason = _recommendation_reason(
                latest_runs.get(snap["employee"]["employee_id"]), state["revision"]
            )
            if reason:
                reasons.append(reason)
        if reasons:
            attention.append(
                {
                    "profile": career._profile(snap["employee"]),
                    "reasons": reasons,
                    "last_completed_date": max(completed_dates) if completed_dates else None,
                    "history_records_in_period": len(period_records),
                    "completed_in_period": counts["completed"],
                    "no_show_in_period": counts["no_show"],
                    "eligible_event_count": len(candidates),
                    "progress_percent": snap["progress"]["percent"] if snap["progress"] else None,
                }
            )

    skill_gaps = [
        {
            "skill_id": skill_id,
            "skill_name": skills[skill_id]["name"],
            "employees_requiring": stats["required"],
            "employees_with_gap": len(stats["gaps"]),
            "gap_percent": round(100 * len(stats["gaps"]) / stats["required"], 2),
            "average_gap": round(sum(stats["gaps"]) / len(stats["gaps"]), 2) if stats["gaps"] else 0.0,
            "critical_gap_count": stats["critical"],
        }
        for skill_id, stats in skill_stats.items()
    ]
    skill_gaps.sort(key=lambda row: (-row["employees_with_gap"], -row["average_gap"], row["skill_id"]))
    participation = [
        {
            "event_id": event_id,
            "title": events[event_id]["title"],
            "type": events[event_id]["type"],
            "format": events[event_id]["format"],
            "mandatory": events[event_id]["mandatory"],
            "record_count": sum(counts.values()),
            "participant_count": len(event_participants[event_id]),
            **{status: counts[status] for status in STATUSES},
        }
        for event_id, counts in sorted(event_counts.items())
    ]
    return {
        "data_version": career.version(state),
        "state_version": state["revision"],
        "scenario_date": as_of,
        "period_start": start,
        "period_end": as_of,
        "window_days": window_days,
        "employee_count": len(snapshots),
        "employees_with_goal": employees_with_goal,
        "employees_with_history": employees_with_history,
        "employees_with_completion_in_period": employees_with_completion,
        "history_records_in_period": history_count,
        "historical_proxy_records_in_period": proxy_count,
        "excluded_simulations": excluded_simulations,
        "skill_gaps": skill_gaps,
        "attention": attention,
        "participation": participation,
        "notes": [
            "Период включает обе граничные даты и заканчивается датой сценария набора, а не датой компьютера.",
            "Процент разрывов: сотрудники с разрывом / сотрудники, чья цель требует этот навык. "
            "Средний разрыв рассчитан только среди сотрудников с разрывом; навыки — текущий серверный срез.",
            "Участники — уникальные сотрудники по мероприятию; статусы и знаменатель — записи участия "
            "за период. Повторные назначения сохраняются. Обязательные мероприятия отмечены отдельно.",
            "Историческая дата — приблизительная дата события, а не доказательство точного срока завершения.",
            "Симуляции исключены из участия и сигналов по истории; их число показано за выбранный период. "
            "Если симуляция изменила профиль, это отражено в серверных навыках и разрывах.",
            "Отсутствие истории означает недостаток данных, а не нежелание развиваться. Неявки и отсутствие "
            "завершений — повод уточнить обстоятельства, а не оценка мотивации или рейтинг сотрудника.",
            "Нет завершений: сотрудник нанят не позднее начала периода, есть история, но за период нет "
            "завершений. Повторные неявки: не менее двух записей no_show за период.",
            "Следующий шаг проверяется по последнему сохранённому подбору в этой же версии данных. "
            "Наличие подходящей активности в каталоге само по себе не означает наличие рекомендации. "
            "HR-аналитика не вызывает AI; при достигнутой цели новый шаг не требуется.",
        ],
    }
