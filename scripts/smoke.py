"""Real TCP smoke + restart, using only isolated synthetic profiles and credentials."""

import argparse
import importlib.util
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import explanations  # noqa: E402
from backend.app.auth import hash_password  # noqa: E402
from backend.app.config import Settings  # noqa: E402
from backend.app.database import Database  # noqa: E402
from backend.app.imports import ImportService  # noqa: E402
from backend.app.service import CareerService  # noqa: E402
from backend.tests.test_workflow import EMPLOYEE, EVENT, OTHER, source_batch  # noqa: E402


def assert_recommendation_evidence(card, context, display_facts):
    """Validate the small smoke fixture's citations and backend-rendered UI text."""
    explanation = card["explanation"]
    evidence_ids = explanation["evidence_ids"]
    facts = {fact.evidence_id: fact for fact in context.facts}
    assert 4 <= len(evidence_ids) <= 50
    assert len(evidence_ids) == len(set(evidence_ids))
    assert set(evidence_ids) <= facts.keys()
    evidence = [facts[evidence_id] for evidence_id in evidence_ids]
    candidates = {item.event_id: item for item in context.eligible_candidates}
    assert card["event_id"] in candidates
    candidate = candidates[card["event_id"]]
    positive_gaps = {gap.skill_id for gap in context.gaps if gap.gap > 0}
    affected = {effect.skill_id for effect in candidate.effects if effect.delta > 0} & positive_gaps
    profile_subjects = {"profile", context.profile.profile_ref}
    assert {"goal", "gap", "history", "candidate"} <= {fact.kind for fact in evidence}
    for fact in evidence:
        assert (
            (fact.kind == "goal" and fact.subject_id in profile_subjects)
            or (fact.kind == "gap" and fact.subject_id in affected)
            or (fact.kind == "history" and fact.subject_id in profile_subjects | {candidate.event_id})
            or (
                fact.kind == "candidate"
                and fact.subject_id == candidate.event_id
                and fact.evidence_id in candidate.evidence_ids
            )
        )
    # This small fixture supplies event and same-type/format history; both fit the text budget.
    event_history = {
        fact.evidence_id
        for fact in context.facts
        if fact.kind == "history" and fact.subject_id == candidate.event_id
    }
    assert len(event_history) == 2
    assert event_history <= set(evidence_ids)
    assert explanation["text"] == explanations.render(evidence, display_facts)
    assert 1 <= len(explanation["text"]) <= 2000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ai", action="store_true", help="Use the real optional AI module with synthetic selection only"
    )
    args = parser.parse_args()
    if args.ai and importlib.util.find_spec("backend.app.ai") is None:
        parser.error("AI module is absent; run this check in the combined integration snapshot")
    with tempfile.TemporaryDirectory(prefix="career-quest-synthetic-smoke-") as directory:
        path = Path(directory) / "smoke.sqlite3"
        db = Database(Settings(database_path=path))
        db.migrate()
        importer = ImportService(db)
        batch = source_batch()
        preview = importer.preview(batch)
        importer.commit(batch, preview["preview_token"])
        password = secrets.token_urlsafe(24)
        with db.connect() as connection:
            connection.execute(
                "INSERT INTO accounts VALUES (?,?,?,?,?)",
                ("SYNTH_ACCOUNT", "synthetic", hash_password(password), "employee", EMPLOYEE),
            )
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        environment = {
            **os.environ,
            "DATABASE_PATH": str(path),
            "APP_ENV": "test",
            "SESSION_TTL_SECONDS": "3600",
            "ALLOWED_ORIGINS": '["http://127.0.0.1:8000"]',
            "AI_ENABLED": "true" if args.ai else "false",
            "OPENAI_API_KEY": "synthetic-tcp-never-sent" if args.ai else "",
            "OPENAI_MODEL": "gpt-4.1-mini-2025-04-14",
            "AI_TIMEOUT_SECONDS": "6.0",
            "COMMIT_SHA": "unknown",
            "SQLITE_WAL": "false",
            "SQLITE_LOCAL_DISK": "false",
            "COOKIE_SECURE": "false",
        }
        origin = {"Origin": "http://127.0.0.1:8000"}
        with httpx.Client(base_url=base, trust_env=False, timeout=3) as client:
            for cycle in range(2):
                command = (
                    [sys.executable, "-m", "backend.tests.ai_smoke_server", "--port", str(port)]
                    if args.ai
                    else [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "backend.app.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--workers",
                        "1",
                        "--no-proxy-headers",
                        "--no-access-log",
                    ]
                )
                process = subprocess.Popen(
                    command,
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    for _ in range(100):
                        if process.poll() is not None:
                            raise RuntimeError("Smoke server exited before readiness")
                        try:
                            if client.get("/api/health/ready").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(0.05)
                    else:
                        raise RuntimeError("Smoke server did not become ready")
                    assert client.get("/api/health").status_code == 200
                    assert client.get("/api/health").json()["capabilities"]["ai"] == (
                        {"status": "configured", "engine": "oleg"}
                        if args.ai
                        else {"status": "not_configured", "engine": "none"}
                    )
                    assert client.get("/api/version").json()["api_version"] == "1.0.0"
                    if cycle == 0:
                        response = client.post(
                            "/api/auth/login",
                            headers=origin,
                            json={"username": "synthetic", "password": password},
                        )
                        assert response.status_code == 200
                        csrf = response.json()["csrf_token"]
                        detail = client.get(f"/api/employees/{EMPLOYEE}").json()
                        if args.ai:
                            recommended = client.post(
                                f"/api/employees/{EMPLOYEE}/recommendations",
                                headers={**origin, "X-CSRF-Token": csrf},
                                json={"scenario_date": detail["scenario_date"], "limit": 3},
                            )
                            assert recommended.status_code == 200
                            recommendation = recommended.json()
                            assert (recommendation["status"], recommendation["engine"]) == ("ok", "oleg")
                            assert recommendation["recommendations"][0]["event_id"] == EVENT
                            service = CareerService(db, None)
                            snap = service.snapshot(EMPLOYEE)
                            context, display_facts = service._prepare_context(
                                snap, service._candidates(snap), 3
                            )
                            assert_recommendation_evidence(
                                recommendation["recommendations"][0], context, display_facts
                            )
                            assert recommendation["recommendations"][0]["effects"]
                        payload = {
                            "expected_state_version": detail["state_version"],
                            "event_id": EVENT,
                            "mode": "demo_simulation",
                        }
                        mutation = {**origin, "X-CSRF-Token": csrf, "Idempotency-Key": "tcp-lost-response"}
                        response = client.post(
                            f"/api/employees/{EMPLOYEE}/completions", headers=mutation, json=payload
                        )
                        assert response.status_code == 200
                        saved = response.json()
                        assert client.get(f"/api/employees/{OTHER}").status_code == 403
                        assert client.get("/api/hr/summary").status_code == 403
                        assert client.post("/api/auth/logout", headers=origin).status_code == 403
                    else:
                        assert client.get("/api/me").json()["employee_id"] == EMPLOYEE
                        replay = client.post(
                            f"/api/employees/{EMPLOYEE}/completions", headers=mutation, json=payload
                        )
                        assert replay.json() == saved
                        if args.ai:
                            latest = client.get(f"/api/employees/{EMPLOYEE}/recommendations/latest").json()
                            assert latest == {**recommendation, "stale": True}
                        assert (
                            client.post(
                                "/api/auth/logout", headers={**origin, "X-CSRF-Token": csrf}
                            ).status_code
                            == 200
                        )
                        assert client.get("/api/me").status_code == 401
                    print(f"TCP smoke cycle {cycle + 1}: health/readiness/version/auth/access OK")
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
        with db.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 2
            assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == len(
                db._migrations()
            )
        print(
            "Restart preserved profiles, migrations, session and exact completion result after lost response; logout revoked session."
        )
        if args.ai:
            print(
                "Real AI module over TCP: validated facts/cards persisted across restart; remote selection was synthetic, no live model call."
            )


if __name__ == "__main__":
    main()
