"""Wholly invented source kits for evaluating backend-prepared AI contexts.

These are import inputs, not hand-authored RecommendationContext/facts. Opaque
event IDs and neutral titles do not reveal the external evaluation rubric.
"""

from __future__ import annotations

import csv
import io
import json

from backend.app.imports import HISTORY_FIELDS

EMPLOYEE_ID = "SYNTH_BE_EMPLOYEE"
SKILL_X = "SYNTH_BE_SKILL_X"
SKILL_Y = "SYNTH_BE_SKILL_Y"
EVENT_A = "SYNTH_BE_EVENT_A"
EVENT_B = "SYNTH_BE_EVENT_B"
EVENT_C = "SYNTH_BE_EVENT_C"
EVENT_D = "SYNTH_BE_EVENT_D"
ROLE = "Synthetic Current Role"
TARGET = "Synthetic Target Role"
CASE_IDS = (
    "backend_critical_over_lowest",
    "backend_repeated_format_nonparticipation",
    "backend_no_history",
)


def _event(event_id: str, skill_id: str, *, gain: int = 1, event_format: str = "self_paced") -> dict:
    return {
        "event_id": event_id,
        "title": "Synthetic activity " + event_id[-1],
        "description": "Independent synthetic evaluation activity.",
        "type": "course",
        "format": event_format,
        "duration_hours": 2.0,
        "mandatory": False,
        "target_roles": [ROLE],
        "target_grades": ["Junior"],
        "develops_skills": [{"skill_id": skill_id, "gain": gain, "max_level": 5}],
        "prerequisites": {},
        "upcoming_sessions": [] if event_format == "self_paced" else ["2026-10-02"],
    }


def _history_row(index: int, event_id: str, status: str) -> dict:
    return {
        "record_id": f"SYNTH_BE_RECORD_{index}",
        "employee_id": EMPLOYEE_ID,
        "event_id": event_id,
        "date": f"2026-08-{index:02d}",
        "due_date": "",
        "status": status,
        "completion_pct": {"completed": 100, "no_show": 0, "dropped": 20}[status],
        "score": "",
        "feedback_rating": "",
        "assigned_by": "self",
    }


def synthetic_sources(case_id: str) -> list[dict]:
    """Return a fresh kit selected exclusively from a fixed synthetic case list."""
    if case_id not in CASE_IDS:
        raise ValueError("Unknown synthetic backend case.")
    critical_case = case_id == CASE_IDS[0]
    meta = {
        "dataset": "independent-backend-ai-evaluation",
        "version": "synthetic-backend-v1",
        "as_of_date": "2026-10-01",
    }
    skills = {
        "meta": meta,
        "proficiency_scale": {str(level): f"Synthetic level {level}" for level in range(6)},
        "skills": [
            {
                "skill_id": skill_id,
                "name": "Synthetic skill " + skill_id[-1],
                "type": "hard",
                "category": "Synthetic",
                "description": "Invented evaluation skill.",
            }
            for skill_id in (SKILL_X, SKILL_Y)
        ],
        "role_profiles": [
            {
                "role": ROLE,
                "grade": "Junior",
                "required_skills": {SKILL_X: 1, SKILL_Y: 1},
                "critical_skills": [SKILL_X],
            },
            {
                "role": TARGET,
                "grade": "Senior",
                "required_skills": {SKILL_X: 4, SKILL_Y: 4},
                "critical_skills": [SKILL_Y if critical_case else SKILL_X],
            },
        ],
    }
    employee = {
        "employee_id": EMPLOYEE_ID,
        "full_name": "Invented Evaluation Person",
        "department": "Synthetic",
        "role": ROLE,
        "grade": "Junior",
        "manager_id": None,
        "hire_date": "2026-01-01",
        "tenure_months": 9,
        "work_format": "hybrid",
        "preferred_language": "en",
        "career_goal": {"target_role": TARGET, "target_grade": "Senior"},
        "skills": {SKILL_X: 0, SKILL_Y: 3} if critical_case else {SKILL_X: 2, SKILL_Y: 4},
        "last_review_date": "2026-09-01",
    }
    rows = []
    if critical_case:
        # A develops the lowest noncritical skill more; B closes the critical gap.
        events = [_event(EVENT_A, SKILL_X, gain=2), _event(EVENT_B, SKILL_Y)]
    elif case_id == CASE_IDS[1]:
        # Same gains/load/type, with a clear format difference. Historical
        # completions predate review and do not inflate the current skill level.
        events = [
            _event(EVENT_A, SKILL_X, event_format="offline"),
            _event(EVENT_B, SKILL_X),
            _event(EVENT_C, SKILL_X, event_format="offline"),
            _event(EVENT_D, SKILL_X),
        ]
        events[2]["upcoming_sessions"] = ["2026-08-03"]
        rows = [
            _history_row(1, EVENT_A, "no_show"),
            _history_row(2, EVENT_A, "dropped"),
            _history_row(3, EVENT_C, "no_show"),
            _history_row(4, EVENT_C, "dropped"),
            _history_row(5, EVENT_D, "completed"),
        ]
    else:
        # Equally useful formats without history: either is acceptable. The
        # test must not reward an invented preference unsupported by evidence.
        events = [_event(EVENT_A, SKILL_X, event_format="offline"), _event(EVENT_B, SKILL_X)]
    documents = {
        "skills.json": skills,
        "events.json": {"meta": meta, "events": events},
        "employees.json": {"meta": meta, "employees": [employee]},
    }
    sources = [
        {"source_filename": name, "source_format": "json", "content": json.dumps(document)}
        for name, document in documents.items()
    ]
    history = io.StringIO()
    writer = csv.DictWriter(history, fieldnames=HISTORY_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    sources.append(
        {"source_filename": "activity_history.csv", "source_format": "csv", "content": history.getvalue()}
    )
    return sources
