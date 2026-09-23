"""Exercise the real backend over HTTP using a disposable synthetic SQLite file.

No running application, existing database, starter kit or AI provider is used.
The server is restarted to verify persistence. Credentials never reach stdout.
"""

from __future__ import annotations

import hashlib
import json
import os
import runpy
import socket
import subprocess
import sys
import tempfile
import time
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
DEMO = runpy.run_path(str(Path(__file__).with_name("seed-demo.py")))


class Client:
    def __init__(self, origin: str):
        self.origin = origin
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.csrf = ""

    def request(self, method: str, path: str, body=None, *, expected=200, headers=None):
        request_headers = {"Origin": self.origin}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if self.csrf:
            request_headers["X-CSRF-Token"] = self.csrf
        request_headers.update(headers or {})
        request = Request(
            self.origin + path,
            method=method,
            headers=request_headers,
            data=None if body is None else json.dumps(body).encode(),
        )
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            status = response.status
            payload = json.load(response)
        if status != expected:
            raise AssertionError(
                f"{method} {path}: expected HTTP {expected}, got {status}, code={payload.get('code')}"
            )
        return payload

    def login(self, credentials: dict):
        result = self.request(
            "POST", "/api/auth/login", {key: credentials[key] for key in ("username", "password")}
        )
        self.csrf = result["csrf_token"]
        return result["user"]


def start_server(database: Path, port: int) -> subprocess.Popen:
    origin = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "DATABASE_PATH": str(database),
        "APP_ENV": "test",
        "AI_ENABLED": "false",
        "COOKIE_SECURE": "false",
        "ALLOWED_ORIGINS": json.dumps([origin]),
        "COMMIT_SHA": "unknown",
        "SQLITE_WAL": "false",
        "SQLITE_LOCAL_DISK": "false",
    }
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
            "--no-access-log",
            "--log-level",
            "error",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    client = Client(origin)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Local backend stopped before becoming ready.")
        try:
            ready = client.request("GET", "/api/health/ready")
            if ready["status"] == "ready":
                return process
        except (URLError, TimeoutError, ConnectionError, AssertionError):
            pass
        time.sleep(0.1)
    stop_server(process)
    raise RuntimeError("Local backend did not become ready within 20 seconds.")


def stop_server(process: subprocess.Popen):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def check(condition: bool, description: str):
    if not condition:
        raise AssertionError(description)


def smoke(workdir: Path):
    database, credentials_file = workdir / "smoke.sqlite3", workdir / ".local-demo.json"
    credentials, created = DEMO["seed"](database, credentials_file)
    check(created, "An isolated database was created")
    digest = hashlib.sha256(database.read_bytes()).digest()
    repeated, created_again = DEMO["seed"](database, credentials_file)
    check(not created_again and repeated == credentials, "Repeated setup preserves credentials")
    check(
        hashlib.sha256(database.read_bytes()).digest() == digest, "Repeated setup never writes the database"
    )
    unrelated = workdir / "existing.sqlite3"
    unrelated.write_bytes(b"existing user file must remain intact")
    try:
        DEMO["seed"](unrelated, workdir / "unrelated.json")
        raise AssertionError("Setup accepted an unrelated existing file")
    except ValueError:
        check(
            unrelated.read_bytes() == b"existing user file must remain intact",
            "Existing files remain unchanged",
        )
    print("PASS isolated setup, safe repetition and refusal to overwrite an existing file")

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    process = start_server(database, port)
    try:
        employee, hr = Client(origin), Client(origin)
        anonymous = Client(origin)
        anonymous.request("GET", "/api/me", expected=401)
        check(employee.login(credentials["employee"])["role"] == "employee", "Employee login")
        check(hr.login(credentials["hr"])["role"] == "hr", "HR login")
        eid, event = DEMO["EMPLOYEE_ID"], DEMO["EVENT_ID"]
        path = f"/api/employees/{eid}"
        initial = employee.request("GET", path)
        check(
            initial["progress"]["percent"] > 0 and len(initial["history"]) == 2,
            "Profile has progress and history",
        )
        employee.request("GET", f"/api/employees/{DEMO['OTHER_EMPLOYEE_ID']}", expected=403)
        employee.request("GET", "/api/hr/summary", expected=403)
        check(len(employee.request("GET", "/api/catalog")["events"]) >= 4, "Synthetic catalog")
        recommendation = employee.request(
            "POST", path + "/recommendations", {"scenario_date": initial["scenario_date"], "limit": 3}
        )
        check(
            recommendation["status"] == "not_configured"
            and recommendation["engine"] == "none"
            and not recommendation["recommendations"],
            "No-AI status is explicit",
        )
        employee.request(
            "POST",
            path + "/preview",
            {
                "expected_state_version": initial["state_version"],
                "scenario_date": initial["scenario_date"],
                "event_ids": [event],
            },
            expected=403,
            headers={"X-CSRF-Token": "invalid-synthetic-csrf"},
        )
        preview = employee.request(
            "POST",
            path + "/preview",
            {
                "expected_state_version": initial["state_version"],
                "scenario_date": initial["scenario_date"],
                "event_ids": [event],
            },
        )
        check(preview["persisted"] is False and preview["effects"][0]["delta"] > 0, "Server-side preview")
        check(employee.request("GET", path) == initial, "Preview is not persisted")
        print("PASS real session auth, access restrictions, profile, catalog, no-AI state and preview")

        command = {
            "expected_state_version": initial["state_version"],
            "event_id": event,
            "mode": "completion",
        }
        completed = employee.request(
            "POST", path + "/completions", command, headers={"Idempotency-Key": "ui-live-smoke-complete"}
        )
        repeated_completion = employee.request(
            "POST", path + "/completions", command, headers={"Idempotency-Key": "ui-live-smoke-complete"}
        )
        check(repeated_completion == completed, "Idempotent retry returns the saved result")
        after = employee.request("GET", path)
        check(len(after["history"]) == len(initial["history"]) + 1, "Exactly one completion recorded")
        check(
            after["progress"]["percent"] > initial["progress"]["percent"], "Progress increased on the server"
        )
        latest = employee.request("GET", path + "/recommendations/latest")
        check(latest["stale"] is True, "Persisted recommendations become stale")
        employee.request(
            "PATCH",
            path + "/goal",
            {"expected_state_version": initial["state_version"], "goal": initial["goal"]},
            expected=409,
        )
        print("PASS completion, persisted progress, idempotency, stale recommendations and revision conflict")

        imported_id = "UI_SYNTH_IMPORTED_SMOKE"
        imported = DEMO["profile"](imported_id, full_name="Демо: новый импортированный сотрудник")
        imported_history = {
            "record_id": "UI_SYNTH_IMPORTED_HISTORY",
            "employee_id": imported_id,
            "event_id": event,
            "date": "2026-09-20",
            "due_date": "",
            "status": "completed",
            "completion_pct": 100,
            "score": 85,
            "feedback_rating": 4,
            "assigned_by": "self",
        }
        files = [
            DEMO["source"]("employees.json", {"meta": DEMO["META"], "employees": [imported]}),
            DEMO["history_source"]([imported_history]),
        ]
        before_summary = hr.request("GET", "/api/hr/summary")
        imported_preview = hr.request("POST", "/api/hr/import", {"dry_run": True, "files": files})
        check(imported_preview["status"] == "validated", "Import preview")
        check(hr.request("GET", "/api/hr/summary") == before_summary, "Import preview writes no profiles")
        result = hr.request(
            "POST",
            "/api/hr/import",
            {"dry_run": False, "files": files, "preview_token": imported_preview["preview_token"]},
        )
        check(result["status"] == "imported", "Import commit")
        check(
            hr.request("GET", "/api/hr/summary")["employee_count"] == before_summary["employee_count"] + 1,
            "Imported profile appears in HR",
        )
        check(
            len(hr.request("GET", f"/api/employees/{imported_id}")["history"]) == 1,
            "JSON profile and CSV history are imported together",
        )
        invalid_profile = DEMO["profile"]("UI_SYNTH_INVALID", skills={"UI_SYSTEMS": 8})
        invalid_files = [
            DEMO["source"]("employees.json", {"meta": DEMO["META"], "employees": [invalid_profile]})
        ]
        hr.request("POST", "/api/hr/import", {"dry_run": True, "files": invalid_files}, expected=422)
        before_restart = employee.request("GET", path)
        check(
            before_restart["state_version"] == result["revision"], "Rejected import did not mutate revision"
        )
        print("PASS HR import preview, commit, new profile and invalid-source rejection")

        stop_server(process)
        process = start_server(database, port)
        check(employee.request("GET", path) == before_restart, "Profile and session survive restart")
        check(
            employee.request("GET", path + "/recommendations/latest")["stale"] is True,
            "Latest response survives restart",
        )
        hr.request("GET", f"/api/employees/{imported_id}")
        check(
            employee.request(
                "POST", path + "/completions", command, headers={"Idempotency-Key": "ui-live-smoke-complete"}
            )
            == completed,
            "Idempotency result survives restart",
        )
        print("PASS backend restart persistence for sessions, profile, imported data and idempotency")
    finally:
        stop_server(process)


def main() -> int:
    try:
        with tempfile.TemporaryDirectory(prefix="career-quest-frontend-smoke-") as directory:
            smoke(Path(directory).resolve())
    except Exception as exc:
        # Assertions contain only route/status metadata, never credential payloads.
        message = str(exc) if isinstance(exc, (AssertionError, RuntimeError)) else type(exc).__name__
        print(f"FAIL {message}", file=sys.stderr)
        return 1
    print("Real backend HTTP smoke passed; AI remained disabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
