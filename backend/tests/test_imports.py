"""Synthetic fixtures authored by the team; never copy organizer/jury profiles."""

from __future__ import annotations

import copy
import csv
import io
import json
import sqlite3
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.database import Database
from backend.app.errors import APIError
from backend.app.imports import HISTORY_FIELDS, ImportService


def source_file(name: str, content: object) -> dict:
    return {
        "source_filename": name,
        "source_format": name.rsplit(".", 1)[1],
        "content": json.dumps(content) if name.endswith(".json") else content,
    }


def synthetic_employee(employee_id: str = "SYNTH_EMP_1", **changes) -> dict:
    return {
        "employee_id": employee_id,
        "full_name": "Synthetic employee",
        "department": "Synthetic department",
        "role": "Synthetic role",
        "grade": "Junior",
        "manager_id": None,
        "hire_date": "2025-01-01",
        "tenure_months": 21,
        "work_format": "remote",
        "preferred_language": "ru",
        "career_goal": None,
        "skills": {"SYNTH_SKILL": 1},
        "last_review_date": "2026-09-01",
        **changes,
    }


def synthetic_record(record_id: str = "SYNTH_RECORD_1", **changes) -> dict:
    return {
        "record_id": record_id,
        "employee_id": "SYNTH_EMP_1",
        "event_id": "SYNTH_EVENT_1",
        "date": "2026-09-02",
        "due_date": "",
        "status": "completed",
        "completion_pct": 100,
        "score": "",
        "feedback_rating": "",
        "assigned_by": "self",
        **changes,
    }


def history_file(records: list[dict]) -> dict:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=HISTORY_FIELDS)
    writer.writeheader()
    writer.writerows(records)
    return source_file("activity_history.csv", buffer.getvalue())


META = {"dataset": "Synthetic Career Quest", "version": "1.0", "as_of_date": "2026-10-01"}


def synthetic_files() -> list[dict]:
    skills = {
        "meta": META,
        "proficiency_scale": {str(i): f"Synthetic level {i}" for i in range(6)},
        "skills": [
            {
                "skill_id": "SYNTH_SKILL",
                "name": "Synthetic skill",
                "type": "hard",
                "category": "Synthetic",
                "description": "Synthetic test skill",
            }
        ],
        "role_profiles": [
            {
                "role": "Synthetic role",
                "grade": grade,
                "required_skills": {"SYNTH_SKILL": level},
                "critical_skills": ["SYNTH_SKILL"],
            }
            for grade, level in [("Junior", 1), ("Middle", 3), ("Senior", 4), ("Lead", 5)]
        ],
    }
    event = {
        "event_id": "SYNTH_EVENT_1",
        "title": "Synthetic event",
        "description": "Synthetic test event",
        "type": "course",
        "format": "self_paced",
        "duration_hours": 2,
        "mandatory": False,
        "target_roles": ["Synthetic role"],
        "target_grades": ["Junior", "Middle", "Senior", "Lead"],
        "develops_skills": [{"skill_id": "SYNTH_SKILL", "gain": 1, "max_level": 4}],
        "prerequisites": {},
        "upcoming_sessions": [],
    }
    return [
        source_file("skills.json", skills),
        source_file("events.json", {"meta": META, "events": [event]}),
        source_file("employees.json", {"meta": META, "employees": [synthetic_employee()]}),
        history_file([synthetic_record()]),
    ]


@pytest.fixture
def service(tmp_path: Path) -> ImportService:
    db = Database(Settings(database_path=tmp_path / "import.sqlite3", app_env="test"))
    db.migrate()
    return ImportService(db)


def import_batch(service: ImportService, files: list[dict]) -> dict:
    preview = service.preview(files)
    return service.commit(files, preview["preview_token"])


def state(service: ImportService) -> tuple[int, int, int]:
    with service.db.connect() as conn:
        return (
            conn.execute("SELECT revision FROM dataset_state").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM employee_profiles").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM activity_history").fetchone()[0],
        )


def test_preview_commit_restart_and_noop_reimport(service: ImportService):
    files = synthetic_files()
    preview = service.preview(files)
    assert preview["revision"] == 0
    assert preview["imported_records"] == 8
    assert state(service) == (0, 0, 0)
    result = service.commit(files, preview["preview_token"])
    assert result["revision"] == 1
    assert state(service) == (1, 1, 1)
    service.db.migrate()
    assert state(service) == (1, 1, 1)
    assert import_batch(service, files)["imported_records"] == 0
    assert state(service) == (1, 1, 1)
    with service.db.connect() as conn:
        row = json.loads(conn.execute("SELECT record_json FROM activity_history").fetchone()[0])
        assert row["effective_date"] == "2026-09-02"
        assert row["date_source"] == "historical_proxy"
        assert row["completed_at"] is None
        assert row["mode"] == "import"


def test_jury_profiles_and_history_are_validated_together(service: ImportService):
    import_batch(service, synthetic_files())
    files = [
        source_file("employees.json", {"meta": META, "employees": [synthetic_employee("SYNTH_JURY")]}),
        history_file([synthetic_record("SYNTH_JURY_RECORD", employee_id="SYNTH_JURY")]),
    ]
    assert import_batch(service, files)["imported_records"] == 2
    assert state(service) == (2, 2, 2)


def test_error_in_middle_of_batch_leaves_no_partial_data(service: ImportService):
    import_batch(service, synthetic_files())
    files = [
        source_file("employees.json", {"meta": META, "employees": [synthetic_employee("SYNTH_NEW")]}),
        history_file(
            [
                synthetic_record("SYNTH_GOOD", employee_id="SYNTH_NEW"),
                synthetic_record("SYNTH_BAD", employee_id="SYNTH_NEW", event_id="UNKNOWN"),
                synthetic_record("SYNTH_LAST", employee_id="SYNTH_NEW"),
            ]
        ),
    ]
    with pytest.raises(APIError) as error:
        service.preview(files)
    assert error.value.status == 422
    assert state(service) == (1, 1, 1)


def test_sql_failure_during_commit_rolls_back_profiles_history_and_revision(service: ImportService):
    import_batch(service, synthetic_files())
    files = [
        source_file("employees.json", {"meta": META, "employees": [synthetic_employee("SYNTH_NEW")]}),
        history_file(
            [
                synthetic_record("SYNTH_GOOD", employee_id="SYNTH_NEW"),
                synthetic_record("SYNTH_FAIL", employee_id="SYNTH_NEW"),
            ]
        ),
    ]
    preview = service.preview(files)
    with service.db.connect() as conn:
        conn.execute(
            "CREATE TRIGGER synthetic_failure BEFORE INSERT ON activity_history WHEN NEW.record_id='SYNTH_FAIL' BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic failure"):
        service.commit(files, preview["preview_token"])
    assert state(service) == (1, 1, 1)
    with service.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM employees WHERE id='SYNTH_NEW'").fetchone()[0] == 0


def test_different_record_ids_preserve_repeated_mandatory_assignments(service: ImportService):
    files = synthetic_files()
    events = json.loads(files[1]["content"])
    events["events"][0]["mandatory"] = True
    files[1] = source_file("events.json", events)
    files[3] = history_file(
        [
            synthetic_record("SYNTH_ASSIGNMENT_1", due_date="2026-09-20", assigned_by="hr"),
            synthetic_record("SYNTH_ASSIGNMENT_2", due_date="2026-09-20", assigned_by="hr"),
        ]
    )
    import_batch(service, files)
    assert state(service) == (1, 1, 2)


def test_duplicate_record_id_within_batch_is_rejected(service: ImportService):
    files = synthetic_files()
    files[3] = history_file([synthetic_record(), synthetic_record()])
    with pytest.raises(APIError) as error:
        service.preview(files)
    assert error.value.status == 422
    assert state(service) == (0, 0, 0)


def test_existing_record_id_cannot_be_silently_overwritten(service: ImportService):
    import_batch(service, synthetic_files())
    with pytest.raises(APIError) as error:
        service.preview([history_file([synthetic_record(score=95)])])
    assert error.value.status == 409
    assert error.value.code == "history_conflict"
    assert state(service) == (1, 1, 1)


def test_changed_and_expired_preview_cannot_commit(service: ImportService):
    files = synthetic_files()
    token = service.preview(files)["preview_token"]
    changed = copy.deepcopy(files)
    changed[3] = history_file([synthetic_record("SYNTH_OTHER")])
    with pytest.raises(APIError) as error:
        service.commit(changed, token)
    assert error.value.code == "preview_mismatch"
    with service.db.connect() as conn:
        conn.execute("UPDATE import_previews SET expires_at=0")
    with pytest.raises(APIError) as error:
        service.commit(files, token)
    assert error.value.code == "preview_required"
    assert state(service) == (0, 0, 0)


def test_concurrent_data_change_requires_fresh_preview(service: ImportService):
    files = synthetic_files()
    preview = service.preview(files)
    with service.db.connect() as conn:
        conn.execute("UPDATE dataset_state SET revision=revision+1")
    with pytest.raises(APIError) as error:
        service.commit(files, preview["preview_token"])
    assert error.value.code == "stale_preview"
    assert state(service) == (1, 0, 0)


@pytest.mark.parametrize(
    "field,value",
    [
        ("skills", {"UNKNOWN": 1}),
        ("skills", {"SYNTH_SKILL": 6}),
        ("skills", {"SYNTH_SKILL": True}),
        ("last_review_date", "2026-10-02"),
        ("manager_id", "UNKNOWN"),
        ("grade", "Invented"),
    ],
)
def test_bad_profiles_fail_without_leaking_inputs(service: ImportService, field, value):
    files = synthetic_files()
    profile = synthetic_employee(**{field: value, "full_name": "PRIVATE_SENTINEL"})
    files[2] = source_file("employees.json", {"meta": META, "employees": [profile]})
    with pytest.raises(APIError) as error:
        service.preview(files)
    assert error.value.status == 422
    assert "PRIVATE_SENTINEL" not in str(error.value.details)
    assert state(service) == (0, 0, 0)


def test_manager_can_be_in_same_batch_later(service: ImportService):
    files = synthetic_files()
    files[2] = source_file(
        "employees.json",
        {
            "meta": META,
            "employees": [
                synthetic_employee(manager_id="SYNTH_MANAGER"),
                synthetic_employee("SYNTH_MANAGER", grade="Lead"),
            ],
        },
    )
    import_batch(service, files)
    assert state(service) == (1, 2, 1)


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "completed", "completion_pct": 95},
        {"status": "in_progress", "completion_pct": 100},
        {"status": "dropped", "completion_pct": 0},
        {"status": "declined", "completion_pct": 1},
        {"status": "no_show", "completion_pct": 0},
        {"score": 101},
        {"feedback_rating": 6},
        {"date": "2026-10-02"},
        {"due_date": "2026-10-03"},
    ],
)
def test_history_constraints(service: ImportService, changes):
    files = synthetic_files()
    files[3] = history_file([synthetic_record(**changes)])
    with pytest.raises(APIError) as error:
        service.preview(files)
    assert error.value.status == 422
    assert state(service) == (0, 0, 0)


def test_csv_bom_accepted_but_wrong_headers_and_wrapper_rejected(service: ImportService):
    files = synthetic_files()
    files[3]["content"] = "\ufeff" + files[3]["content"]
    assert service.preview(files)["status"] == "validated"
    files[3]["content"] = files[3]["content"].replace("record_id", "record")
    with pytest.raises(APIError):
        service.preview(files)
    files = synthetic_files()
    files[2] = source_file("employees.json", [synthetic_employee()])
    with pytest.raises(APIError):
        service.preview(files)


def test_source_metadata_and_duplicate_json_keys_rejected(service: ImportService):
    files = synthetic_files()
    files[2] = source_file(
        "employees.json", {"meta": META | {"as_of_date": "2026-10-02"}, "employees": [synthetic_employee()]}
    )
    with pytest.raises(APIError):
        service.preview(files)
    files[2]["content"] = '{"employees": [], "employees": [], "meta": {}}'
    with pytest.raises(APIError):
        service.preview(files)


def test_existing_employee_replacement_requires_separate_explicit_feature(service: ImportService):
    import_batch(service, synthetic_files())
    files = [
        source_file(
            "employees.json",
            {"meta": META, "employees": [synthetic_employee(full_name="Changed synthetic name")]},
        )
    ]
    with pytest.raises(APIError) as error:
        service.preview(files)
    assert error.value.code == "employee_conflict"
    assert state(service) == (1, 1, 1)


def test_json_utf8_bom_is_supported(service: ImportService):
    files = synthetic_files()
    for source in files[:3]:
        source["content"] = "\ufeff" + source["content"]
    assert import_batch(service, files)["revision"] == 1


@pytest.mark.parametrize("filename", ["employees.json", "invented.json", "../skills.json"])
def test_logical_filename_must_match_source_wrapper(service: ImportService, filename):
    files = synthetic_files()
    files[0]["source_filename"] = filename
    with pytest.raises(APIError) as error:
        service.preview(files)
    assert error.value.status == 422


@pytest.mark.parametrize(
    "file_index,path,length",
    [
        (0, ("skills", 0, "name"), 201),
        (0, ("skills", 0, "category"), 201),
        (0, ("skills", 0, "description"), 1001),
        (0, ("role_profiles", 0, "role"), 129),
        (0, ("meta", "version"), 81),
        (1, ("events", 0, "title"), 201),
        (1, ("events", 0, "description"), 4001),
        (2, ("employees", 0, "full_name"), 201),
        (2, ("employees", 0, "department"), 201),
        (2, ("employees", 0, "role"), 129),
    ],
)
def test_import_rejects_values_too_long_for_http_responses(service: ImportService, file_index, path, length):
    files = synthetic_files()
    document = json.loads(files[file_index]["content"])
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = "x" * length
    files[file_index]["content"] = json.dumps(document)
    with pytest.raises(APIError) as error:
        service.preview(files)
    assert error.value.status == 422
    assert state(service) == (0, 0, 0)
