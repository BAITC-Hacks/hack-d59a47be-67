"""Deterministic skill arithmetic and eligibility; no database, network or model calls.

Inputs are source-shaped dictionaries. A replay uses the assessed snapshot and only
completed history strictly after that assessment through the scenario date. The
historical ``date`` is a completion proxy, not an asserted completion timestamp;
new actions supply ``effective_date`` separately from their real ``completed_at``.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime, timezone
from math import isfinite
from typing import Any

GRADES = ("Junior", "Middle", "Senior", "Lead")
REPEATABLE_EVENT_ID = "EV_036"
DEMO_DATE = date(2026, 10, 1)


class DomainError(ValueError):
    """Invalid source references or arithmetic inputs, with a safe machine code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _day(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DomainError("invalid_date", "Expected an ISO calendar date") from exc


def _level(value: Any, *, gain: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise DomainError("invalid_level", "Skill levels and gains must be finite numbers")
    if value < 0 or (not gain and value > 5):
        raise DomainError("invalid_level", "Skill levels must use 0–5 and gains must be nonnegative")
    return float(value)


def _known(skill_id: str, skills: Mapping[str, float]) -> None:
    if skill_id not in skills:
        raise DomainError("unknown_skill", "An unknown skill_id was referenced")


def effective_date(record: Mapping[str, Any]) -> date:
    """Return the scenario date; actual audit timestamps never replace this date."""
    if record.get("date_source") == "completed_at":
        try:
            completed_at = datetime.fromisoformat(record["completed_at"])
            if completed_at.tzinfo is None or completed_at.utcoffset() is None:
                raise ValueError("An aware timestamp is required")
        except (TypeError, ValueError, KeyError) as exc:
            raise DomainError("invalid_date", "A new completion requires an aware completed_at") from exc
        completed_on = completed_at.date()
        if record.get("effective_date") and _day(record["effective_date"]) != completed_on:
            raise DomainError("invalid_date", "Completion effective_date conflicts with completed_at")
        return completed_on
    return _day(record.get("effective_date") or record.get("date"))


def _completion_order(record: Mapping[str, Any]) -> tuple[date, int, datetime, str]:
    """Keep proxy history deterministic, then replay exact facts in time order.

    Event caps are not commutative: random completion IDs must never reorder
    exact same-day actions. Imported proxies sort before exact actions on that
    date; without a historical completion time, only record_id can break ties.
    """
    day = effective_date(record)
    exact = record.get("date_source") == "completed_at"
    timestamp = (
        datetime.fromisoformat(record["completed_at"]).astimezone(timezone.utc)
        if exact
        else datetime.min.replace(tzinfo=timezone.utc)
    )
    return day, int(exact), timestamp, record["record_id"]


def apply_event(skills: Mapping[str, float], event: Mapping[str, Any]) -> tuple[dict, list[dict]]:
    """Apply each declared gain once, keeping already-high levels unchanged.

    ``skills`` must contain the complete catalog, including zero-valued skills.
    Effects follow ``SkillEffect`` in contracts.py and include capped zero gains.
    """
    projected = {skill_id: _level(value) for skill_id, value in skills.items()}
    effects = []
    seen = set()
    for development in event.get("develops_skills", []):
        skill_id = development["skill_id"]
        _known(skill_id, projected)
        if skill_id in seen:
            raise DomainError("duplicate_skill", "An event develops the same skill more than once")
        seen.add(skill_id)
        before = projected[skill_id]
        gain = _level(development["gain"], gain=True)
        cap = _level(development["max_level"])
        after = max(before, min(before + gain, cap, 5.0))
        after = round(after, 10)
        projected[skill_id] = after
        effects.append(
            {"skill_id": skill_id, "before": before, "after": after, "delta": round(after - before, 10)}
        )
    return projected, effects


def replay(
    employee: Mapping[str, Any],
    events: Mapping[str, Mapping[str, Any]],
    history: Sequence[Mapping[str, Any]],
    skill_ids: Collection[str],
    as_of: date,
) -> dict[str, float]:
    """Replay without mutating or deduplicating source history records.

    Each source record is a distinct fact. Even repeated employee/event/date
    triples are replayed; import uses record_id as identity. Nonrepeatability is
    enforced for new completions and recommendations, never by deleting history.
    """
    cutoff = _day(as_of)
    review_date = _day(employee["last_review_date"])
    if cutoff < review_date:
        raise DomainError("before_assessment", "Cannot reconstruct skills before their assessment")
    levels = dict.fromkeys(skill_ids, 0.0)
    for skill_id, value in employee["skills"].items():
        _known(skill_id, levels)
        levels[skill_id] = _level(value)
    records = [
        row
        for row in history
        if row["employee_id"] == employee["employee_id"] and row["status"] == "completed"
    ]
    records.sort(key=_completion_order)
    for record in records:
        completed_on = effective_date(record)
        if completed_on > cutoff:
            continue
        event_id = record["event_id"]
        if event_id not in events:
            raise DomainError("unknown_event", "History references an unknown event_id")
        event = events[event_id]
        if completed_on > review_date:
            levels, _ = apply_event(levels, event)
    return levels


def _target(goal: Mapping[str, Any], role_profiles: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    for profile in role_profiles:
        if profile["role"] == goal["target_role"] and profile["grade"] == goal["target_grade"]:
            return profile
    raise DomainError("unknown_goal", "The target role and grade have no catalog profile")


def resolve_goal(
    employee: Mapping[str, Any], role_profiles: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, str] | None, str]:
    """An explicit goal wins; otherwise use the same role's next grade."""
    if employee.get("career_goal") is not None:
        goal = dict(employee["career_goal"])
        _target(goal, role_profiles)
        return goal, "explicit"
    grade = employee["grade"]
    if grade not in GRADES:
        raise DomainError("unknown_grade", "The current grade is not in the catalog scale")
    if grade == "Lead":
        return None, "no_target"
    goal = {"target_role": employee["role"], "target_grade": GRADES[GRADES.index(grade) + 1]}
    _target(goal, role_profiles)
    return goal, "next_grade"


def gaps(
    skills: Mapping[str, float],
    goal: Mapping[str, Any] | None,
    role_profiles: Sequence[Mapping[str, Any]],
) -> list[dict]:
    """Return positive gaps in the shared SkillGap shape."""
    if goal is None:
        return []
    result = []
    for skill_id, required in sorted(_target(goal, role_profiles)["required_skills"].items()):
        _known(skill_id, skills)
        current = _level(skills[skill_id])
        required = _level(required)
        if required > current:
            result.append(
                {
                    "skill_id": skill_id,
                    "current_level": current,
                    "target_level": required,
                    "gap": round(required - current, 10),
                }
            )
    return result


def progress(
    skills: Mapping[str, float],
    goal: Mapping[str, Any] | None,
    role_profiles: Sequence[Mapping[str, Any]],
) -> dict | None:
    """Measure attained required skill levels; critical readiness is independent.

    Extra levels cannot compensate for a missing required or critical skill.
    A missing target has no progress percentage (``None``).
    """
    if goal is None:
        return None
    target = _target(goal, role_profiles)
    requirements = target["required_skills"]
    missing = gaps(skills, goal, role_profiles)
    required_total = sum(_level(value) for value in requirements.values())
    if required_total == 0:
        return None
    achieved_total = required_total - sum(item["gap"] for item in missing)
    for skill_id in target["critical_skills"]:
        _known(skill_id, skills)
        if skill_id not in requirements:
            raise DomainError("invalid_critical_skill", "A critical skill has no target requirement")
    return {
        "percent": round(100 * achieved_total / required_total, 2),
        "met_skills": len(requirements) - len(missing),
        "total_skills": len(requirements),
        "critical_met": sum(
            skills[skill_id] >= requirements[skill_id] for skill_id in target["critical_skills"]
        ),
        "critical_total": len(target["critical_skills"]),
    }


def is_available(
    employee: Mapping[str, Any], skills: Mapping[str, float], event: Mapping[str, Any], as_of: date
) -> bool:
    """Audience/prerequisite/calendar gate, independent of target usefulness.

    Completion services additionally check assignment, history and session
    identity inside their write transaction. Mandatory events are allowed here.
    """
    apply_event(skills, event)
    prerequisites_met = True
    for skill_id, minimum in event.get("prerequisites", {}).items():
        _known(skill_id, skills)
        if skills[skill_id] < _level(minimum):
            prerequisites_met = False
    return (
        employee["role"] in event["target_roles"]
        and employee["grade"] in event["target_grades"]
        and prerequisites_met
        and (
            event["format"] == "self_paced"
            or any(_day(session) >= _day(as_of) for session in event.get("upcoming_sessions", []))
        )
    )


def eligible_events(
    employee: Mapping[str, Any],
    skills: Mapping[str, float],
    history: Sequence[Mapping[str, Any]],
    events: Mapping[str, Mapping[str, Any]],
    as_of: date,
    goal: Mapping[str, Any] | None,
    role_profiles: Sequence[Mapping[str, Any]],
) -> list[dict]:
    """Return only allowed voluntary events with a positive target-gap reduction.

    Audience always uses the current role/grade, never the desired role. A
    self-paced event needs no session; a scheduled event needs a session on or
    after the scenario date. Active participation is a continuation, not a new
    recommendation (team architecture policy). No invented events are generated.
    """
    cutoff = _day(as_of)
    current_gaps = {item["skill_id"]: item["gap"] for item in gaps(skills, goal, role_profiles)}
    completed = {
        row["event_id"]
        for row in history
        if row["employee_id"] == employee["employee_id"]
        and row["status"] == "completed"
        and effective_date(row) <= cutoff
    }
    active = {
        row["event_id"]
        for row in history
        if row["employee_id"] == employee["employee_id"]
        and row["status"] == "in_progress"
        and effective_date(row) <= cutoff
    }
    candidates = []
    for event_id, event in sorted(events.items()):
        # Validate skill references even when another eligibility rule would
        # exclude this event: an unknown catalog skill is never treated as zero.
        projected, effects = apply_event(skills, event)
        available = is_available(employee, skills, event, cutoff)
        if event.get("mandatory", False):
            continue
        if event_id in completed and event_id != REPEATABLE_EVENT_ID:
            continue
        if event_id in active or not available:
            continue
        if event_id == REPEATABLE_EVENT_ID:
            completed_dates = {
                _day(row.get("session_date") or effective_date(row))
                for row in history
                if row["employee_id"] == employee["employee_id"]
                and row["status"] == "completed"
                and row["event_id"] == event_id
                and effective_date(row) <= cutoff
            }
            if event["format"] == "self_paced":
                if cutoff in completed_dates:
                    continue
            elif not any(
                _day(session) >= cutoff and _day(session) not in completed_dates
                for session in event.get("upcoming_sessions", [])
            ):
                continue
        reduction = sum(
            min(current_gaps.get(skill_id, 0), projected[skill_id] - skills[skill_id]) for skill_id in skills
        )
        if reduction > 0:
            candidates.append({**event, "effects": effects, "gap_reduction": round(reduction, 10)})
    return candidates
