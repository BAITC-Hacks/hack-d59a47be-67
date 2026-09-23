"""Hand-written synthetic domain fixtures, not organizer profiles or event data."""

from __future__ import annotations

from typing import Any


def employee(**overrides: Any) -> dict:
    return {
        "employee_id": "SYNTH_EMPLOYEE_1",
        "full_name": "Synthetic Employee One",
        "department": "Synthetic Engineering",
        "role": "Synthetic Engineer",
        "grade": "Junior",
        "manager_id": None,
        "hire_date": "2026-01-01",
        "tenure_months": 9,
        "work_format": "remote",
        "preferred_language": "en",
        "career_goal": None,
        "skills": {"SYNTH_PYTHON": 1.0},
        "last_review_date": "2026-09-01",
        **overrides,
    }


def event(event_id: str = "SYNTH_EVENT_1", **overrides: Any) -> dict:
    return {
        "event_id": event_id,
        "title": "Synthetic Python Practice",
        "description": "A hand-written synthetic fixture",
        "type": "course",
        "format": "self_paced",
        "duration_hours": 2.0,
        "mandatory": False,
        "target_roles": ["Synthetic Engineer"],
        "target_grades": ["Junior", "Middle"],
        "develops_skills": [{"skill_id": "SYNTH_PYTHON", "gain": 1.0, "max_level": 4.0}],
        "prerequisites": {},
        "upcoming_sessions": [],
        **overrides,
    }


def history(record_id: str = "SYNTH_RECORD_1", **overrides: Any) -> dict:
    return {
        "record_id": record_id,
        "employee_id": "SYNTH_EMPLOYEE_1",
        "event_id": "SYNTH_EVENT_1",
        "date": "2026-09-15",
        "date_source": "historical_proxy",
        "due_date": None,
        "status": "completed",
        "completion_pct": 100,
        "score": None,
        "feedback_rating": None,
        "assigned_by": "self",
        **overrides,
    }


def role_profiles() -> list[dict]:
    return [
        {
            "role": role,
            "grade": grade,
            "required_skills": {"SYNTH_PYTHON": level, "SYNTH_TEAMWORK": level},
            "critical_skills": ["SYNTH_PYTHON"],
        }
        for role in ("Synthetic Engineer", "Synthetic Analyst")
        for grade, level in (("Junior", 1.0), ("Middle", 3.0), ("Senior", 4.0), ("Lead", 5.0))
    ]


SKILL_IDS = ("SYNTH_PYTHON", "SYNTH_TEAMWORK")
