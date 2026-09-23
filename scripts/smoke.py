"""Real TCP smoke + restart, using only isolated synthetic profiles and credentials."""

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

from backend.app.auth import hash_password  # noqa: E402
from backend.app.config import Settings  # noqa: E402
from backend.app.database import Database  # noqa: E402
from backend.app.imports import ImportService  # noqa: E402
from backend.tests.test_workflow import EMPLOYEE, EVENT, OTHER, source_batch  # noqa: E402


def main():
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
            "ALLOWED_ORIGINS": '["http://127.0.0.1:8000"]',
            "AI_ENABLED": "false",
            "COMMIT_SHA": "unknown",
            "SQLITE_WAL": "false",
            "COOKIE_SECURE": "false",
        }
        origin = {"Origin": "http://127.0.0.1:8000"}
        with httpx.Client(base_url=base, trust_env=False, timeout=3) as client:
            for cycle in range(2):
                process = subprocess.Popen(
                    [
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
                    ],
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


if __name__ == "__main__":
    main()
