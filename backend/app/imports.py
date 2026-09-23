"""Validated, staged, atomic kit imports; never evaluate paths supplied by callers."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .database import Database
from .errors import APIError
from .source_models import SourceEmployees, SourceEvents, SourceHistory, SourceSkills

HISTORY_FIELDS = (
    "record_id",
    "employee_id",
    "event_id",
    "date",
    "due_date",
    "status",
    "completion_pct",
    "score",
    "feedback_rating",
    "assigned_by",
)
PREVIEW_TTL_SECONDS = 900


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _invalid(message: str, field: str | None = None) -> None:
    raise APIError(422, "invalid_source", message, {"field": field} if field else None)


def _unique(items: list[dict], key: str) -> dict[str, dict]:
    result = {}
    for item in items:
        if item[key] in result:
            _invalid("Duplicate identifiers inside the imported batch", key)
        result[item[key]] = item
    return result


def _decode_json(content: str) -> Any:
    def pairs(entries: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in entries:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = value
        return result

    return json.loads(content.lstrip("\ufeff"), object_pairs_hook=pairs)


def _parse(files: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    if not files or len(files) > 4:
        _invalid("Supply between 1 and 4 source files")
    documents: dict[str, dict] = {}
    history: list[dict] = []
    names = set()
    for index, source in enumerate(files):
        try:
            name, fmt, content = source["source_filename"], source["source_format"], source["content"]
            if not isinstance(name, str) or not name.endswith("." + fmt) or name in names:
                _invalid("Source names must be unique and match their format")
            names.add(name)
            if name not in {"skills.json", "events.json", "employees.json", "activity_history.csv"}:
                _invalid("Use the documented logical source filename for each file")
            if not isinstance(content, str) or not 1 <= len(content) <= 2_000_000:
                _invalid("Source content must contain 1 to 2000000 characters")
            if fmt == "json":
                raw = _decode_json(content)
                if not isinstance(raw, dict):
                    _invalid("JSON requires the original object wrapper")
                kind = [key for key in ("skills", "events", "employees") if key in raw]
                if len(kind) != 1 or kind[0] in documents:
                    _invalid("Supply at most one source document of each kind")
                if name != kind[0] + ".json":
                    _invalid("Source filename does not match its JSON wrapper")
                model = {"skills": SourceSkills, "events": SourceEvents, "employees": SourceEmployees}[
                    kind[0]
                ]
                documents[kind[0]] = model.model_validate(raw).model_dump(mode="json")
            elif fmt == "csv":
                reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")), strict=True)
                if tuple(reader.fieldnames or []) != HISTORY_FIELDS:
                    _invalid("History CSV header must exactly match the documented source header")
                for row in reader:
                    if set(row) != set(HISTORY_FIELDS) or any(value is None for value in row.values()):
                        _invalid("History CSV row has an incorrect number of columns")
                    for field in ("completion_pct", "score", "feedback_rating"):
                        value = row[field]
                        if value == "" and field != "completion_pct":
                            row[field] = None
                        elif value.isascii() and value.isdecimal():
                            row[field] = int(value)
                        else:
                            _invalid(
                                "History numeric columns require whole numbers or allowed empty values", field
                            )
                    row["due_date"] = row["due_date"] or None
                    normalized = SourceHistory.model_validate(row).model_dump(mode="json")
                    normalized.update(
                        effective_date=normalized["date"],
                        date_source="historical_proxy",
                        completed_at=None,
                        mode="import",
                    )
                    history.append(normalized)
            else:
                _invalid("Only JSON source wrappers and history CSV are supported")
        except ValidationError as exc:
            # Pydantic's default error payload includes the employee's input. Never expose it.
            fields = [{"location": list(error["loc"]), "type": error["type"]} for error in exc.errors()]
            raise APIError(
                422,
                "invalid_source",
                "Source fields failed validation",
                {"file_index": index, "errors": fields},
            ) from None
        except (KeyError, TypeError, ValueError, csv.Error):
            raise APIError(422, "invalid_source", "Malformed source file", {"file_index": index}) from None
    _unique(history, "record_id")
    return documents, history


@dataclass
class StagedImport:
    revision: int
    data_version: str
    scenario_date: str
    skills: dict | None
    events: dict | None
    employees: dict[str, dict]
    history: dict[str, dict]
    counts: dict[str, int]

    @property
    def count(self) -> int:
        return sum(self.counts.values())

    def summary(self) -> dict:
        return {
            "revision": self.revision,
            "data_version": self.data_version,
            "imported_records": self.count,
            "counts": self.counts,
            "warnings": [],
        }


class ImportService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _stage(self, conn: sqlite3.Connection, files: list[dict]) -> StagedImport:
        documents, input_history = _parse(files)
        state = conn.execute("SELECT * FROM dataset_state WHERE id=1").fetchone()
        old_skills = json.loads(state["skills_json"]) if state["skills_json"] else None
        old_events = json.loads(state["events_json"]) if state["events_json"] else None
        skills_doc = documents.get("skills", old_skills)
        events_doc = documents.get("events", old_events)
        if skills_doc is None:
            _invalid("Import the skill catalog in this batch or before importing other records")
        meta = skills_doc["meta"]
        for document in [*documents.values(), *([events_doc] if events_doc else [])]:
            if document["meta"] != meta:
                _invalid("All source metadata must agree on dataset, version and scenario date", "meta")
        if state["scenario_date"] and meta["as_of_date"] != state["scenario_date"]:
            _invalid("Source scenario date does not match the installed dataset", "meta.as_of_date")
        skills = _unique(skills_doc["skills"], "skill_id")
        role_profiles = {}
        for profile in skills_doc["role_profiles"]:
            key = (profile["role"], profile["grade"])
            if key in role_profiles:
                _invalid("Role and grade combinations must be unique", "role_profiles")
            role_profiles[key] = profile
            if len(profile["critical_skills"]) != len(set(profile["critical_skills"])):
                _invalid("Critical skills must not contain duplicate identifiers", "critical_skills")
            if not set(profile["required_skills"]) <= skills.keys():
                _invalid("Role requirements contain unknown skill identifiers", "required_skills")
            if not set(profile["critical_skills"]) <= profile["required_skills"].keys():
                _invalid("Critical skills must be part of role requirements", "critical_skills")
        events = _unique(events_doc["events"], "event_id") if events_doc else {}
        roles = {key[0] for key in role_profiles}
        for event in events.values():
            _unique(event["develops_skills"], "skill_id")
            if not set(event["prerequisites"]) <= skills.keys() or any(
                effect["skill_id"] not in skills for effect in event["develops_skills"]
            ):
                _invalid("Event refers to unknown skills", "events")
            if not set(event["target_roles"]) <= roles:
                _invalid("Event audience refers to an unknown role", "target_roles")
        existing_profiles = {
            row["employee_id"]: json.loads(row["profile_json"])
            for row in conn.execute("SELECT * FROM employee_profiles")
        }
        input_profiles = _unique(documents.get("employees", {}).get("employees", []), "employee_id")
        if any(
            key in existing_profiles and existing_profiles[key] != value
            for key, value in input_profiles.items()
        ):
            raise APIError(
                409,
                "employee_conflict",
                "An existing employee_id has different contents; replacement is not supported",
            )
        all_profiles = existing_profiles | input_profiles
        for profile in all_profiles.values():
            if not set(profile["skills"]) <= skills.keys():
                _invalid("Employee profile refers to unknown skills", "employees.skills")
            if (profile["role"], profile["grade"]) not in role_profiles:
                _invalid("Employee role and grade have no catalog profile", "employees.role")
            goal = profile["career_goal"]
            if goal and (goal["target_role"], goal["target_grade"]) not in role_profiles:
                _invalid("Employee goal has no catalog profile", "employees.career_goal")
            if not profile["hire_date"] <= profile["last_review_date"] <= meta["as_of_date"]:
                _invalid("Employee review must be between hire and scenario dates", "last_review_date")
            manager_id = profile["manager_id"]
            if manager_id:
                manager = all_profiles.get(manager_id)
                if not manager or manager_id == profile["employee_id"]:
                    _invalid(
                        "Employee manager must refer to another existing or batched profile", "manager_id"
                    )
                if manager["grade"] != "Lead" or manager["department"] != profile["department"]:
                    _invalid("Manager must be a Lead in the same department", "manager_id")
        existing_history = {
            row["record_id"]: json.loads(row["record_json"])
            for row in conn.execute("SELECT * FROM activity_history")
        }
        pending_history = {}
        for record in input_history:
            existing = existing_history.get(record["record_id"])
            if existing is not None:
                if {key: existing.get(key) for key in HISTORY_FIELDS} != {
                    key: record[key] for key in HISTORY_FIELDS
                }:
                    raise APIError(409, "history_conflict", "An existing record_id has different contents")
            else:
                pending_history[record["record_id"]] = record
        for record in (existing_history | pending_history).values():
            if record["employee_id"] not in all_profiles or record["event_id"] not in events:
                _invalid("History must reference known profiles and events in the resulting batch", "history")
            # New completions use completed_at; imported rows retain their approximate date.
            if record.get("mode", "import") != "import":
                continue
            event = events[record["event_id"]]
            if record["date"] > meta["as_of_date"]:
                _invalid("Historical activity date cannot be after the scenario date", "date")
            if record["due_date"] is not None and not event["mandatory"]:
                _invalid("Only mandatory history can have a due date", "due_date")
            if record["status"] == "no_show" and event["format"] == "self_paced":
                _invalid("Self-paced events cannot have no_show history", "status")
            if record["status"] == "overdue" and not event["mandatory"]:
                _invalid("Only mandatory assignments can be overdue", "status")
            if record["score"] is not None and event["type"] not in {"course", "certification", "compliance"}:
                _invalid("Score is only allowed for courses, certifications and compliance", "score")
        pending_profiles = {
            key: value for key, value in input_profiles.items() if existing_profiles.get(key) != value
        }
        new_skills = skills_doc if skills_doc != old_skills else None
        new_events = events_doc if events_doc != old_events else None
        counts = {
            "skills": len(skills_doc["skills"]) if new_skills else 0,
            "role_profiles": len(skills_doc["role_profiles"]) if new_skills else 0,
            "events": len(events_doc["events"]) if new_events else 0,
            "employees": len(pending_profiles),
            "history": len(pending_history),
        }
        return StagedImport(
            state["revision"],
            meta["version"],
            meta["as_of_date"],
            new_skills,
            new_events,
            pending_profiles,
            pending_history,
            counts,
        )

    def preview(self, files: list[dict]) -> dict:
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            staged = self._stage(conn, files)
            token = secrets.token_urlsafe(32)
            result = staged.summary() | {"preview_token": token, "dry_run": True, "status": "validated"}
            conn.execute("DELETE FROM import_previews WHERE expires_at < ?", (int(time.time()),))
            conn.execute(
                "INSERT INTO import_previews VALUES (?, ?, ?, ?, ?)",
                (
                    _hash(token),
                    _hash(canonical(files)),
                    staged.revision,
                    int(time.time()) + PREVIEW_TTL_SECONDS,
                    canonical(staged.summary()),
                ),
            )
            return result

    def commit(self, files: list[dict], preview_token: str) -> dict:
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            preview = conn.execute(
                "SELECT * FROM import_previews WHERE token_hash=?", (_hash(preview_token),)
            ).fetchone()
            if preview is None or preview["expires_at"] <= int(time.time()):
                raise APIError(409, "preview_required", "A current successful import preview is required")
            if preview["payload_hash"] != _hash(canonical(files)):
                raise APIError(409, "preview_mismatch", "The source batch differs from its validated preview")
            revision = conn.execute("SELECT revision FROM dataset_state WHERE id=1").fetchone()[0]
            if preview["revision"] != revision:
                raise APIError(409, "stale_preview", "Data changed after validation; preview the batch again")
            staged = self._stage(conn, files)
            for profile in staged.employees.values():
                conn.execute(
                    "INSERT INTO employees(id,display_name) VALUES (?,?) "
                    "ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name",
                    (profile["employee_id"], profile["full_name"]),
                )
                conn.execute(
                    "INSERT INTO employee_profiles VALUES (?,?) "
                    "ON CONFLICT(employee_id) DO UPDATE SET profile_json=excluded.profile_json",
                    (profile["employee_id"], canonical(profile)),
                )
            for record in staged.history.values():
                conn.execute(
                    "INSERT INTO activity_history VALUES (?,?,?,?)",
                    (record["record_id"], record["employee_id"], record["event_id"], canonical(record)),
                )
            changed = bool(staged.count or staged.skills is not None or staged.events is not None)
            if changed:
                conn.execute(
                    "UPDATE dataset_state SET revision=revision+1,data_version=?,scenario_date=?,"
                    "skills_json=COALESCE(?,skills_json),events_json=COALESCE(?,events_json) WHERE id=1",
                    (
                        staged.data_version,
                        staged.scenario_date,
                        canonical(staged.skills) if staged.skills is not None else None,
                        canonical(staged.events) if staged.events is not None else None,
                    ),
                )
            return staged.summary() | {
                "revision": revision + int(changed),
                "dry_run": False,
                "status": "imported",
                "preview_token": preview_token,
            }
