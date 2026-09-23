"""Authoritative backend projections and transactional writes; no LLM implementation."""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import contracts as c
from . import domain
from .errors import APIError
from .imports import HISTORY_FIELDS


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def revision(conn):
    return conn.execute("SELECT revision FROM dataset_state WHERE id=1").fetchone()[0]


def check_revision(expected, actual):
    if expected != actual:
        raise APIError(
            409, "REVISION_CONFLICT", "State changed; reload before retrying.", {"state_version": actual}
        )


class CareerService:
    def __init__(self, database, adapter):
        self.db = database
        self.adapter = adapter

    def capabilities(self):
        with self.db.connect() as conn:
            state = conn.execute("SELECT * FROM dataset_state WHERE id=1").fetchone()
            loaded = bool(state and state["skills_json"] and state["events_json"])
            return {
                "status": "loaded" if loaded else "not_loaded",
                "version": self.version(state) if loaded else None,
            }

    @staticmethod
    def version(state):
        return f"{state['data_version']}:r{state['revision']}"

    @staticmethod
    def _state(conn):
        state = conn.execute("SELECT * FROM dataset_state WHERE id=1").fetchone()
        if not state or not state["skills_json"] or not state["events_json"]:
            raise APIError(503, "DATASET_NOT_LOADED", "Load and validate the starter kit first.")
        return dict(state), json.loads(state["skills_json"]), json.loads(state["events_json"])

    def _snapshot(self, conn, employee_id):
        state, catalog, event_catalog = self._state(conn)
        row = conn.execute(
            "SELECT profile_json FROM employee_profiles WHERE employee_id=?", (employee_id,)
        ).fetchone()
        if not row:
            raise APIError(404, "PROFILE_NOT_LOADED", "Employee profile has not been imported.")
        employee = json.loads(row[0])
        history = [
            json.loads(item[0])
            for item in conn.execute(
                "SELECT record_json FROM activity_history WHERE employee_id=? ORDER BY record_id",
                (employee_id,),
            )
        ]
        events = {event["event_id"]: event for event in event_catalog["events"]}
        as_of = c.RecommendationRequest.model_validate(
            {"scenario_date": state["scenario_date"]}
        ).scenario_date
        skills = domain.replay(employee, events, history, {s["skill_id"] for s in catalog["skills"]}, as_of)
        goal, target_status = domain.resolve_goal(employee, catalog["role_profiles"])
        return {
            "state": state,
            "catalog": catalog,
            "events": events,
            "employee": employee,
            "history": history,
            "as_of": as_of,
            "skills": skills,
            "goal": goal,
            "target_status": target_status,
            "gaps": domain.gaps(skills, goal, catalog["role_profiles"]),
            "progress": domain.progress(skills, goal, catalog["role_profiles"]) if goal else None,
        }

    def snapshot(self, employee_id):
        with self.db.connect() as conn:
            conn.execute("BEGIN")
            return self._snapshot(conn, employee_id)

    def catalog(self):
        with self.db.connect() as conn:
            state, catalog, events = self._state(conn)
        return {
            "data_version": self.version(state),
            "proficiency_scale": catalog["proficiency_scale"],
            "skills": catalog["skills"],
            "role_profiles": catalog["role_profiles"],
            "events": events["events"],
        }

    @staticmethod
    def _profile(employee):
        return {key: employee[key] for key in ("employee_id", "full_name", "department", "role", "grade")}

    def employees(self, limit, offset):
        with self.db.connect() as conn:
            self._state(conn)
            total = conn.execute("SELECT COUNT(*) FROM employee_profiles").fetchone()[0]
            rows = conn.execute(
                "SELECT profile_json FROM employee_profiles ORDER BY employee_id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return {
            "items": [self._profile(json.loads(row[0])) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def detail(self, employee_id):
        snap = self.snapshot(employee_id)
        return self._detail(snap)

    def _detail(self, snap):
        history = [
            {
                "record_id": r["record_id"],
                "event_id": r["event_id"],
                "status": r["status"],
                "activity_date": r.get("effective_date", r["date"]),
                "date_source": r.get("date_source", "historical_proxy"),
                "completed_at": r.get("completed_at"),
                "mode": r.get("mode", "import"),
                "effects": r.get("effects", []),
            }
            for r in snap["history"]
        ]
        return {
            "data_version": self.version(snap["state"]),
            "state_version": snap["state"]["revision"],
            "scenario_date": snap["as_of"],
            "profile": self._profile(snap["employee"]),
            "goal": snap["goal"],
            "target_status": snap["target_status"],
            "current_skills": self._levels(snap["skills"]),
            "gaps": snap["gaps"],
            "progress": snap["progress"],
            "history": history,
        }

    @staticmethod
    def _levels(skills):
        return [{"skill_id": key, "level": value} for key, value in sorted(skills.items())]

    def set_goal(self, employee_id, payload):
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            snap = self._snapshot(conn, employee_id)
            check_revision(payload.expected_state_version, snap["state"]["revision"])
            employee = snap["employee"]
            employee["career_goal"] = payload.goal.model_dump()
            try:
                domain.resolve_goal(employee, snap["catalog"]["role_profiles"])
            except ValueError:
                raise APIError(422, "UNKNOWN_GOAL", "Goal must exist in the role catalog.") from None
            conn.execute(
                "UPDATE employee_profiles SET profile_json=? WHERE employee_id=?",
                (encoded(employee), employee_id),
            )
            conn.execute("UPDATE dataset_state SET revision=revision+1 WHERE id=1")
            return self._detail(self._snapshot(conn, employee_id))

    @staticmethod
    def _scenario(snap, value):
        if value != snap["as_of"]:
            raise APIError(
                422,
                "SCENARIO_DATE_MISMATCH",
                "Use the scenario date from the kit.",
                {"scenario_date": snap["as_of"].isoformat()},
            )

    def _candidates(self, snap):
        return domain.eligible_events(
            snap["employee"],
            snap["skills"],
            snap["history"],
            snap["events"],
            snap["as_of"],
            snap["goal"],
            snap["catalog"]["role_profiles"],
        )

    def preview(self, employee_id, payload):
        snap = self.snapshot(employee_id)
        check_revision(payload.expected_state_version, snap["state"]["revision"])
        self._scenario(snap, payload.scenario_date)
        if len(set(payload.event_ids)) != len(payload.event_ids):
            raise APIError(422, "DUPLICATE_EVENT", "Preview event IDs must be unique.")
        before = snap["skills"].copy()
        working = snap["skills"].copy()
        for event_id in payload.event_ids:
            candidates = {item["event_id"]: item for item in self._candidates({**snap, "skills": working})}
            if event_id not in candidates:
                raise APIError(409, "EVENT_NOT_ELIGIBLE", "Event is not a useful eligible candidate.")
            working, _ = domain.apply_event(working, snap["events"][event_id])
        return {
            "data_version": self.version(snap["state"]),
            "state_version": snap["state"]["revision"],
            "scenario_date": snap["as_of"],
            "persisted": False,
            "effects": self._effects(before, working),
            "projected_skills": self._levels(working),
            "projected_gaps": domain.gaps(working, snap["goal"], snap["catalog"]["role_profiles"]),
        }

    @staticmethod
    def _effects(before, after):
        return [
            {"skill_id": key, "before": before[key], "after": value, "delta": round(value - before[key], 8)}
            for key, value in sorted(after.items())
            if value != before[key]
        ]

    def complete(self, employee_id, user, payload, key):
        if user["role"] != "hr" and user["employee_id"] != employee_id:
            raise APIError(403, "FORBIDDEN", "This employee is outside your access scope.")
        if not key or not 1 <= len(key) <= 128 or any(ord(ch) < 33 or ord(ch) > 126 for ch in key):
            raise APIError(422, "IDEMPOTENCY_KEY_REQUIRED", "Provide a 1..128 character Idempotency-Key.")
        body_hash = hashlib.sha256(encoded(payload.model_dump(mode="json")).encode()).hexdigest()
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cached = conn.execute(
                "SELECT * FROM completion_requests WHERE account_id=? AND employee_id=? AND idempotency_key=?",
                (user["id"], employee_id, key),
            ).fetchone()
            if cached:
                if cached["body_hash"] != body_hash:
                    raise APIError(409, "IDEMPOTENCY_CONFLICT", "This key was used with a different request.")
                return json.loads(cached["response_json"])
            snap = self._snapshot(conn, employee_id)
            check_revision(payload.expected_state_version, snap["state"]["revision"])
            event = snap["events"].get(payload.event_id)
            if not event:
                raise APIError(404, "EVENT_NOT_FOUND", "Event not found.")
            prior = (
                next((row for row in snap["history"] if row["record_id"] == payload.record_id), None)
                if payload.record_id
                else None
            )
            if payload.record_id and not prior:
                raise APIError(404, "ACTIVITY_NOT_FOUND", "Activity not found in this employee's history.")
            if prior and (
                prior["event_id"] != payload.event_id or prior["status"] not in {"in_progress", "overdue"}
            ):
                raise APIError(
                    409, "INVALID_ACTIVITY_STATE", "Activity cannot be completed in its current state."
                )
            if event["mandatory"] and not prior:
                raise APIError(
                    409, "ASSIGNMENT_REQUIRED", "Complete an existing mandatory assignment by record_id."
                )
            if (
                not event["mandatory"]
                and event["event_id"] != "EV_036"
                and any(
                    row["event_id"] == event["event_id"] and row["status"] == "completed"
                    for row in snap["history"]
                )
            ):
                raise APIError(409, "ALREADY_COMPLETED", "This voluntary event cannot be completed again.")
            if not prior and any(
                row["event_id"] == event["event_id"] and row["status"] == "in_progress"
                for row in snap["history"]
            ):
                raise APIError(
                    409, "ACTIVE_PARTICIPATION", "Complete the existing participation by record_id."
                )
            # Current audience and prerequisites apply to real and explicitly simulated actions.
            if (
                snap["employee"]["role"] not in event["target_roles"]
                or snap["employee"]["grade"] not in event["target_grades"]
                or any(snap["skills"][skill] < required for skill, required in event["prerequisites"].items())
            ):
                raise APIError(
                    409, "EVENT_NOT_ELIGIBLE", "Current role, grade or prerequisites do not allow this event."
                )
            session_date = prior["date"] if prior else None
            completed_sessions = {
                row.get("session_date") or row["date"]
                for row in snap["history"]
                if row["event_id"] == event["event_id"] and row["status"] == "completed"
            }
            if event["format"] != "self_paced":
                if not session_date:
                    upcoming = sorted(
                        day
                        for day in event["upcoming_sessions"]
                        if day >= snap["as_of"].isoformat()
                        and (event["event_id"] != "EV_036" or day not in completed_sessions)
                    )
                    if not upcoming:
                        raise APIError(409, "NO_SESSION", "There is no available session.")
                    session_date = upcoming[0]
                if payload.mode == "completion" and session_date > snap["as_of"].isoformat():
                    raise APIError(
                        409, "SESSION_IN_FUTURE", "Use explicit demo_simulation for a future session."
                    )
                if event["event_id"] == "EV_036" and any(
                    row["event_id"] == "EV_036"
                    and row["status"] == "completed"
                    and row.get("session_date", row["date"]) == session_date
                    for row in snap["history"]
                ):
                    raise APIError(
                        409,
                        "SESSION_ALREADY_COMPLETED",
                        "This repeatable event session was already completed.",
                    )
            elif event["event_id"] == "EV_036" and snap["as_of"].isoformat() in completed_sessions:
                raise APIError(
                    409,
                    "SESSION_ALREADY_COMPLETED",
                    "This repeatable event was already completed on the demo date.",
                )
            now = datetime.now(timezone.utc)
            completed_at = datetime.combine(
                snap["as_of"], now.astimezone(ZoneInfo("Asia/Almaty")).timetz()
            ).isoformat()
            record_id = prior["record_id"] if prior else "CQ_" + uuid.uuid4().hex
            row = {
                **(prior or {}),
                "record_id": record_id,
                "employee_id": employee_id,
                "event_id": event["event_id"],
                "date": session_date or snap["as_of"].isoformat(),
                "effective_date": snap["as_of"].isoformat(),
                "date_source": "completed_at",
                "completed_at": completed_at,
                "recorded_at": now.isoformat(),
                "session_date": session_date,
                "mode": payload.mode,
                "status": "completed",
                "completion_pct": 100,
                "assigned_by": prior.get("assigned_by", "self") if prior else "self",
            }
            if prior and prior.get("mode", "import") == "import":
                row["source_record"] = {field: prior.get(field) for field in HISTORY_FIELDS}
            conn.execute(
                "INSERT INTO activity_history(record_id,employee_id,event_id,record_json) VALUES (?,?,?,?) "
                "ON CONFLICT(record_id) DO UPDATE SET record_json=excluded.record_json",
                (record_id, employee_id, event["event_id"], encoded(row)),
            )
            after = self._snapshot(conn, employee_id)
            effects = self._effects(snap["skills"], after["skills"])
            row["effects"] = effects
            conn.execute(
                "UPDATE activity_history SET record_json=? WHERE record_id=?", (encoded(row), record_id)
            )
            conn.execute("UPDATE dataset_state SET revision=revision+1 WHERE id=1")
            response = {
                "state_version": revision(conn),
                "completion": {
                    "completion_id": record_id,
                    "event_id": event["event_id"],
                    "mode": payload.mode,
                    "completed_at": completed_at,
                    "effects": effects,
                },
            }
            # Validate before commit, so a contract mismatch cannot leave an unacknowledged write.
            response = c.CompletionResponse.model_validate(response).model_dump(mode="json")
            conn.execute(
                "INSERT INTO completion_requests VALUES (?,?,?,?,?)",
                (user["id"], employee_id, key, body_hash, encoded(response)),
            )
            return response

    @staticmethod
    def _history_evidence(evidence_id, subject_id, rows, as_of, *, scope, **cohort):
        dates = [domain.effective_date(row) for row in rows]
        statuses = dict.fromkeys(("completed", "in_progress", "dropped", "no_show", "declined", "overdue"), 0)
        date_sources = {"historical_proxy": 0, "completed_at": 0}
        for row in rows:
            statuses[row["status"]] += 1
            date_sources["completed_at" if row.get("completed_at") else "historical_proxy"] += 1
        summary = {
            "scope": scope,
            **cohort,
            "as_of": as_of.isoformat(),
            "observed_from": min(dates).isoformat() if dates else None,
            "observed_to": max(dates).isoformat() if dates else None,
            "sample_size": len(rows),
            "status_counts": statuses,
            "date_sources": date_sources,
        }
        return c.EvidenceFact(
            evidence_id=evidence_id,
            kind="history",
            subject_id=subject_id,
            fact="Recorded participation: "
            + encoded(summary)
            + ". Observed dates are not a coverage guarantee; imported dates are proxies, not proof of "
            "timeliness. Counts, including zero samples, do not establish stable preferences.",
        )

    @staticmethod
    def _goal_evidence(snap, target):
        # Normally one compact fact. Large valid catalogs are split without losing
        # identifiers or exceeding EvidenceFact's bound; the model sees every part.
        parts, current = [], []
        for skill_id in target["critical_skills"]:
            if current and len(encoded([*current, skill_id])) > 600:
                parts.append(current)
                current = []
            current.append(skill_id)
        parts.append(current)
        return [
            c.EvidenceFact(
                evidence_id="profile-grade" if index == 0 else f"profile-goal-part-{index + 1}",
                kind="goal",
                subject_id="profile",
                fact="Resolved career goal: "
                + encoded(
                    {
                        "current_role": snap["employee"]["role"],
                        "current_grade": snap["employee"]["grade"],
                        "target_role": target["role"],
                        "target_grade": target["grade"],
                        "critical_skills": skills,
                        "critical_skills_part": index + 1,
                        "critical_skills_parts": len(parts),
                    }
                )
                + ". Criticality comes from the target role/grade catalog; all parts form the full list.",
            )
            for index, skills in enumerate(parts)
        ]

    def _context(self, snap, candidates, limit):
        target = next(
            profile
            for profile in snap["catalog"]["role_profiles"]
            if (profile["role"], profile["grade"])
            == (snap["goal"]["target_role"], snap["goal"]["target_grade"])
        )
        facts = self._goal_evidence(snap, target)
        for gap in snap["gaps"]:
            facts.append(
                c.EvidenceFact(
                    evidence_id="gap-" + hashlib.sha256(gap["skill_id"].encode()).hexdigest()[:24],
                    kind="gap",
                    subject_id=gap["skill_id"],
                    fact=f"Skill {gap['skill_id']}: current level {gap['current_level']}; "
                    f"target {gap['target_level']}; gap {gap['gap']}; "
                    f"critical for target: {gap['skill_id'] in target['critical_skills']}.",
                )
            )
        history = [row for row in snap["history"] if domain.effective_date(row) <= snap["as_of"]]
        facts.append(
            self._history_evidence("history-summary", "profile", history, snap["as_of"], scope="profile")
        )
        prepared = []
        for event in candidates:
            suffix = hashlib.sha256(event["event_id"].encode()).hexdigest()[:24]
            evidence_id = "candidate-" + suffix
            same_event = [row for row in history if row["event_id"] == event["event_id"]]
            comparable = [
                row
                for row in history
                if row["event_id"] != event["event_id"]
                and (snap["events"][row["event_id"]]["type"], snap["events"][row["event_id"]]["format"])
                == (event["type"], event["format"])
            ]
            facts.extend(
                [
                    self._history_evidence(
                        "history-event-" + suffix, event["event_id"], same_event, snap["as_of"], scope="event"
                    ),
                    self._history_evidence(
                        "history-format-" + suffix,
                        event["event_id"],
                        comparable,
                        snap["as_of"],
                        scope="same_type_format_other_events",
                        event_type=event["type"],
                        event_format=event["format"],
                    ),
                ]
            )
            facts.append(
                c.EvidenceFact(
                    evidence_id=evidence_id,
                    kind="candidate",
                    subject_id=event["event_id"],
                    fact=f"Eligible {event['type']} / {event['format']} event; "
                    f"duration {event['duration_hours']} hours. Backend checked current role/grade, "
                    "prerequisites, schedule and history; projected effects calculated by backend.",
                )
            )
            prepared.append(
                c.EligibleCandidate(
                    event_id=event["event_id"],
                    title=event["title"],
                    effects=event["effects"],
                    evidence_ids=[evidence_id],
                )
            )
        return c.RecommendationContext(
            data_version=self.version(snap["state"]),
            state_version=snap["state"]["revision"],
            scenario_date=snap["as_of"],
            profile=c.AnonymizedProfile(
                profile_ref="anonymous-profile",
                role=snap["employee"]["role"],
                grade=snap["employee"]["grade"],
            ),
            goal=c.Goal(**snap["goal"]),
            current_skills=self._levels(snap["skills"]),
            gaps=snap["gaps"],
            eligible_candidates=prepared,
            facts=facts,
            limit=limit,
        )

    async def recommend(self, employee_id, payload):
        snap = self.snapshot(employee_id)  # This connection/transaction closes before any AI call.
        self._scenario(snap, payload.scenario_date)
        candidates = self._candidates(snap) if snap["goal"] else []
        result_status, engine, cards = "no_target", "none", []
        if snap["goal"] and not candidates:
            result_status = "no_candidates"
        elif candidates:
            context = self._context(snap, candidates, payload.limit)
            result = await self.adapter.recommend(context)
            result_status, engine = result.status, result.engine
            by_id = {item["event_id"]: item for item in candidates}
            by_evidence = {fact.evidence_id: fact for fact in context.facts}
            for item in result.recommendations:
                event = by_id[item.event_id]
                evidence = [by_evidence[key] for key in dict.fromkeys(item.explanation.evidence_ids)]
                affected = {effect["skill_id"] for effect in event["effects"] if effect["delta"] > 0}
                if not {"goal", "gap", "history"} <= {fact.kind for fact in evidence} or not any(
                    fact.kind == "gap" and fact.subject_id in affected for fact in evidence
                ):
                    result_status, engine, cards = "unavailable", "oleg", []
                    break
                # Render trusted backend facts, never unsupported free-form model claims.
                resolved_text = " ".join(fact.fact for fact in evidence)
                if len(resolved_text) > 2000:
                    result_status, engine, cards = "unavailable", "oleg", []
                    break
                explanation = {
                    "text": resolved_text,
                    "evidence_ids": [fact.evidence_id for fact in evidence],
                }
                cards.append(
                    {
                        "event_id": item.event_id,
                        "title": event["title"],
                        "effects": event["effects"],
                        "explanation": explanation,
                    }
                )
        response = c.RecommendationResponse(
            data_version=self.version(snap["state"]),
            state_version=snap["state"]["revision"],
            scenario_date=snap["as_of"],
            status=result_status,
            engine=engine,
            recommendations=cards,
            recommendation_id=uuid.uuid4().hex,
            stale=False,
        ).model_dump(mode="json")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            check_revision(snap["state"]["revision"], revision(conn))
            conn.execute(
                "INSERT INTO recommendation_runs VALUES (?,?,?,?,?)",
                (
                    response["recommendation_id"],
                    employee_id,
                    snap["state"]["revision"],
                    encoded(response),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return response

    def latest(self, employee_id):
        with self.db.connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT * FROM recommendation_runs WHERE employee_id=? ORDER BY rowid DESC LIMIT 1",
                (employee_id,),
            ).fetchone()
            if not row:
                raise APIError(404, "RECOMMENDATION_NOT_FOUND", "No saved recommendation exists yet.")
            response = json.loads(row["response_json"])
            response["stale"] = row["revision"] != revision(conn)
            return response

    def summary(self):
        with self.db.connect() as conn:
            conn.execute("BEGIN")
            state, _, _ = self._state(conn)
            profiles = conn.execute(
                "SELECT employee_id FROM employee_profiles ORDER BY employee_id"
            ).fetchall()
            goals = sum(self._snapshot(conn, row[0])["goal"] is not None for row in profiles)
            history = [json.loads(row[0]) for row in conn.execute("SELECT record_json FROM activity_history")]
            return {
                "data_version": self.version(state),
                "employee_count": len(profiles),
                "employees_with_goal": goals,
                "completion_count": sum(row["status"] == "completed" for row in history),
                "demo_simulation_count": sum(row.get("mode") == "demo_simulation" for row in history),
            }
