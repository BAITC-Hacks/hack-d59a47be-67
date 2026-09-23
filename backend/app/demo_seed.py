"""Own fictional sample data for local and isolated public demonstrations.

No source-kit records or personal data are included. All changes still go through
the real importer, projection, completion and recommendation services.
"""

import csv
import io
import json

from .imports import HISTORY_FIELDS

MARKER = "career-quest-frontend-synthetic-v1"
EMPLOYEE_ID = "UI_SYNTH_EMPLOYEE_01"
OTHER_EMPLOYEE_ID = "UI_SYNTH_EMPLOYEE_02"
ROLE = "Synthetic Software Engineer"
EVENT_ID = "UI_SYNTH_SYSTEMS_COURSE"
META = {"dataset": MARKER, "version": "ui-synthetic-v1", "as_of_date": "2026-10-01"}


def profile(employee_id: str = EMPLOYEE_ID, **changes) -> dict:
    return {
        "employee_id": employee_id,
        "full_name": "Демо: сотрудник разработки",
        "department": "Синтетическая команда продукта",
        "role": ROLE,
        "grade": "Middle",
        "manager_id": None,
        "hire_date": "2024-10-01",
        "tenure_months": 24,
        "work_format": "hybrid",
        "preferred_language": "ru",
        "career_goal": {"target_role": ROLE, "target_grade": "Senior"},
        "skills": {"UI_SYSTEMS": 2, "UI_PYTHON": 3, "UI_COMMUNICATION": 1, "UI_TEAMWORK": 2},
        "last_review_date": "2026-09-01",
        **changes,
    }


def source(filename: str, document) -> dict:
    return {
        "source_filename": filename,
        "source_format": filename.rsplit(".", 1)[1],
        "content": json.dumps(document, ensure_ascii=False) if filename.endswith(".json") else document,
    }


def history_source(rows: list[dict]) -> dict:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=HISTORY_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return source("activity_history.csv", buffer.getvalue())


def synthetic_kit() -> list[dict]:
    skill_specs = [
        ("UI_SYSTEMS", "Проектирование систем", "hard"),
        ("UI_PYTHON", "Разработка на Python", "hard"),
        ("UI_COMMUNICATION", "Публичные выступления", "soft"),
        ("UI_TEAMWORK", "Командная работа", "soft"),
    ]
    skills = {
        "meta": META,
        "proficiency_scale": {str(level): f"Синтетический уровень {level}" for level in range(6)},
        "skills": [
            {
                "skill_id": sid,
                "name": name,
                "type": kind,
                "category": "Synthetic UI demo",
                "description": "Вымышленный навык для локальной проверки интерфейса.",
            }
            for sid, name, kind in skill_specs
        ],
        "role_profiles": [
            {
                "role": ROLE,
                "grade": grade,
                "required_skills": {sid: level for sid, _, _ in skill_specs},
                "critical_skills": ["UI_SYSTEMS", "UI_PYTHON"],
            }
            for grade, level in [("Junior", 1), ("Middle", 2), ("Senior", 4), ("Lead", 5)]
        ],
    }
    event_specs = [
        (EVENT_ID, "Практикум по проектированию систем", "UI_SYSTEMS", False),
        ("UI_SYNTH_PYTHON_COURSE", "Надёжные сервисы на Python", "UI_PYTHON", False),
        ("UI_SYNTH_SPEAKING_COURSE", "Истории о продукте: выступления", "UI_COMMUNICATION", False),
        ("UI_SYNTH_TEAMWORK", "Совместная работа над решением", "UI_TEAMWORK", False),
        ("UI_SYNTH_MANDATORY", "Обязательное вводное обучение", "UI_TEAMWORK", True),
    ]
    events = [
        {
            "event_id": eid,
            "title": title,
            "description": "Синтетическая активность, созданная для UI demo.",
            "type": "onboarding" if mandatory else "course",
            "format": "self_paced",
            "duration_hours": 2.0,
            "mandatory": mandatory,
            "target_roles": [ROLE],
            "target_grades": ["Junior", "Middle", "Senior", "Lead"],
            "develops_skills": [{"skill_id": sid, "gain": 1, "max_level": 4}],
            "prerequisites": {},
            "upcoming_sessions": [],
        }
        for eid, title, sid, mandatory in event_specs
    ]
    rows = [
        {
            "record_id": "UI_SYNTH_HISTORY_TEAMWORK",
            "employee_id": EMPLOYEE_ID,
            "event_id": "UI_SYNTH_TEAMWORK",
            "date": "2026-09-12",
            "due_date": "",
            "status": "completed",
            "completion_pct": 100,
            "score": 90,
            "feedback_rating": 4,
            "assigned_by": "self",
        },
        {
            "record_id": "UI_SYNTH_HISTORY_SPEAKING",
            "employee_id": EMPLOYEE_ID,
            "event_id": "UI_SYNTH_SPEAKING_COURSE",
            "date": "2026-09-18",
            "due_date": "",
            "status": "declined",
            "completion_pct": 0,
            "score": "",
            "feedback_rating": "",
            "assigned_by": "self",
        },
    ]
    employees = [
        profile(),
        profile(OTHER_EMPLOYEE_ID, full_name="Демо: коллега", grade="Junior", career_goal=None),
        profile("UI_SYNTH_EMPLOYEE_03", full_name="Демо: руководитель", grade="Lead", career_goal=None),
    ]
    return [
        source("skills.json", skills),
        source("events.json", {"meta": META, "events": events}),
        source("employees.json", {"meta": META, "employees": employees}),
        history_source(rows),
    ]


def import_examples() -> list[dict]:
    """Three additional fictional profiles, with matching CSV for a real import."""
    employees = [
        profile(f"PUBLIC_IMPORT_{number:02}", full_name=f"Учебный профиль {number}") for number in range(1, 4)
    ]
    rows = [
        {
            "record_id": f"PUBLIC_IMPORT_HISTORY_{number:02}",
            "employee_id": employee["employee_id"],
            "event_id": "UI_SYNTH_TEAMWORK",
            "date": "2026-09-15",
            "due_date": "",
            "status": "completed",
            "completion_pct": 100,
            "score": 85,
            "feedback_rating": 4,
            "assigned_by": "self",
        }
        for number, employee in enumerate(employees, 1)
    ]
    return [source("employees.json", {"meta": META, "employees": employees}), history_source(rows)]
