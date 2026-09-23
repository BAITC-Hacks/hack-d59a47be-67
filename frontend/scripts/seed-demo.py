"""Create an isolated, synthetic UI demo database without touching existing data.

Run from the repository root with an explicit absolute --database path. Random
credentials are written once to an ignored local file, never printed. Repeating
the command only verifies this script's existing database and credentials.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import secrets
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.auth import hash_password, verify_password  # noqa: E402
from backend.app.config import Settings  # noqa: E402
from backend.app.database import Database  # noqa: E402
from backend.app.imports import HISTORY_FIELDS, ImportService  # noqa: E402

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


def verify_existing(database_path: Path, credentials_path: Path) -> dict:
    """Read-only verification; never migrate, reseed or reset an existing file."""
    try:
        credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
        if credentials["seed"] != MARKER or Path(credentials["database_path"]) != database_path:
            raise ValueError
        with sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True) as connection:
            catalog = connection.execute("SELECT skills_json FROM dataset_state WHERE id=1").fetchone()
            if not catalog or json.loads(catalog[0])["meta"]["dataset"] != MARKER:
                raise ValueError
            for role in ("employee", "hr"):
                account = credentials[role]
                row = connection.execute(
                    "SELECT password_hash,role,employee_id FROM accounts WHERE username=?",
                    (account["username"],),
                ).fetchone()
                if (
                    not row
                    or row[1] != role
                    or row[2] != account["employee_id"]
                    or not verify_password(account["password"], row[0])
                ):
                    raise ValueError
        return credentials
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        raise ValueError(
            "Existing database or credentials are not this verified demo; nothing changed."
        ) from exc


def seed(database_path: Path, credentials_path: Path) -> tuple[dict, bool]:
    if not database_path.is_absolute():
        raise ValueError("--database must be an explicit absolute path to a separate SQLite file.")
    database_path, credentials_path = database_path.resolve(), credentials_path.resolve()
    if database_path == credentials_path or database_path.suffix.lower() not in {
        ".sqlite3",
        ".sqlite",
        ".db",
    }:
        raise ValueError("Use a separate .sqlite3, .sqlite or .db database path.")
    if database_path.exists():
        return verify_existing(database_path, credentials_path), False
    if credentials_path.exists():
        raise ValueError("Credentials file already exists; no file will be overwritten.")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    # Reserve only a new, explicitly named file; never reuse another database.
    descriptor = os.open(database_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    db = Database(Settings(database_path=database_path, app_env="development", ai_enabled=False))
    db.migrate()
    importer = ImportService(db)
    files = synthetic_kit()
    preview = importer.preview(files)
    importer.commit(files, preview["preview_token"])
    credentials = {"seed": MARKER, "database_path": str(database_path), "scenario_date": META["as_of_date"]}
    with db.connect() as connection:
        for role, username, employee_id in [
            ("employee", "demo.employee", EMPLOYEE_ID),
            ("hr", "demo.hr", None),
        ]:
            password = secrets.token_urlsafe(24)
            connection.execute(
                "INSERT INTO accounts(id,username,password_hash,role,employee_id) VALUES (?,?,?,?,?)",
                (secrets.token_hex(16), username, hash_password(password), role, employee_id),
            )
            credentials[role] = {"username": username, "password": password, "employee_id": employee_id}
    descriptor = os.open(credentials_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(credentials, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return credentials, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", required=True, type=Path, help="Absolute path to a new, isolated SQLite file"
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=ROOT / "frontend" / ".local-demo.json",
        help="Local credentials output; never commit this file",
    )
    args = parser.parse_args()
    try:
        _, created = seed(args.database, args.credentials)
    except Exception as exc:
        # Import errors can contain source content; do not dump exceptions or credentials.
        message = (
            str(exc) if isinstance(exc, ValueError) else "Demo setup failed; existing files were preserved."
        )
        print(message, file=sys.stderr)
        return 1
    print(
        "Created isolated synthetic demo."
        if created
        else "Verified existing demo; no data or credentials changed."
    )
    print(f"Database: {args.database.resolve()}")
    print(f"Credentials saved locally: {args.credentials.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
