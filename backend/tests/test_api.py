"""HTTP/security checks with explicitly synthetic identities, never organiser data."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app.auth import COOKIE, digest, hash_password
from backend.app.config import Settings
from backend.app.contracts import ErrorResponse
from backend.app.main import create_app

ORIGIN = "http://127.0.0.1:8000"
PASSWORD = "synthetic-test-only-password"
EMPLOYEE = "SYNTH_EMPLOYEE_1"
OTHER_EMPLOYEE = "SYNTH_EMPLOYEE_2"


@pytest.fixture(scope="session")
def synthetic_password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def settings(tmp_path: Path):
    return Settings(database_path=tmp_path / "synthetic.sqlite3", app_env="test")


def seed_accounts(app, password_hash: str) -> None:
    with app.state.database.connect() as connection:
        connection.executemany(
            "INSERT INTO employees(id,display_name) VALUES (?,?)",
            [(EMPLOYEE, "Synthetic Employee One"), (OTHER_EMPLOYEE, "Synthetic Employee Two")],
        )
        connection.executemany(
            "INSERT INTO accounts(id,username,password_hash,role,employee_id) VALUES (?,?,?,?,?)",
            [
                ("synthetic-user-1", "synthetic.employee", password_hash, "employee", EMPLOYEE),
                ("synthetic-user-2", "synthetic.other", password_hash, "employee", OTHER_EMPLOYEE),
                ("synthetic-hr", "synthetic.hr", password_hash, "hr", None),
            ],
        )


@pytest.fixture
def client(settings, synthetic_password_hash):
    app = create_app(settings)

    @app.get("/test-only/conflict")
    def conflict():
        raise HTTPException(status_code=409, detail="private database detail must not leak")

    @app.get("/test-only/storage-failure")
    def storage_failure():
        raise sqlite3.OperationalError("synthetic private database path")

    @app.get("/test-only/unhandled-failure")
    def unhandled_failure():
        raise RuntimeError("synthetic private credential")

    with TestClient(app, base_url=ORIGIN) as test_client:
        seed_accounts(app, synthetic_password_hash)
        yield test_client


def sign_in(client, username="synthetic.employee"):
    response = client.post(
        "/api/auth/login",
        headers={"Origin": ORIGIN},
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response


def mutation_headers(login_response):
    return {"Origin": ORIGIN, "X-CSRF-Token": login_response.json()["csrf_token"]}


def assert_error(response, status: int, code: str):
    assert response.status_code == status, response.text
    body = ErrorResponse.model_validate(response.json())
    assert body.code == code
    assert body.request_id == response.headers["X-Request-ID"]
    UUID(body.request_id)
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    return body


# These are API contract examples, not representations of organisers' source files.
DOMAIN_REQUESTS = [
    ("GET", "/api/catalog", None),
    ("GET", "/api/employees?limit=20&offset=0", None),
    ("GET", f"/api/employees/{EMPLOYEE}", None),
    (
        "PATCH",
        f"/api/employees/{EMPLOYEE}/goal",
        {
            "expected_state_version": 0,
            "goal": {"target_role": "Synthetic Analyst", "target_grade": "Middle"},
        },
    ),
    (
        "POST",
        f"/api/employees/{EMPLOYEE}/recommendations",
        {
            "scenario_date": "2026-09-23",
            "limit": 3,
        },
    ),
    (
        "POST",
        f"/api/employees/{EMPLOYEE}/preview",
        {
            "expected_state_version": 0,
            "scenario_date": "2026-09-23",
            "event_ids": ["SYNTH_EVENT_1"],
        },
    ),
    (
        "POST",
        f"/api/employees/{EMPLOYEE}/completions",
        {
            "expected_state_version": 0,
            "event_id": "SYNTH_EVENT_1",
            "mode": "demo_simulation",
        },
    ),
    ("GET", "/api/hr/summary", None),
    (
        "POST",
        "/api/hr/import",
        {
            "dry_run": True,
            "source_filename": "employees.json",
            "source_format": "json",
            "content": '{"synthetic_test_fixture": true}',
        },
    ),
]


def test_health_readiness_version_and_database_failure(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "capabilities": {
            "ai": {"status": "not_configured", "engine": "none"},
            "dataset": {"status": "not_loaded", "version": None},
        },
    }
    assert client.get("/api/health/ready").json()["database"] == "ready"
    assert client.get("/api/version").json() == {"api_version": "1.0.0", "commit_sha": "unknown"}
    with client.app.state.database.connect() as connection:
        connection.execute("DROP TABLE login_attempts")
    failure = assert_error(client.get("/api/health/ready"), 503, "NOT_READY")
    assert failure.details["database"] == "unavailable"
    assert failure.details["capabilities"]["ai"]["status"] == "not_configured"
    assert client.get("/api/health").status_code == 200


def test_login_stores_only_hashes_logout_revokes_session(client):
    assert_error(client.get("/api/me"), 401, "UNAUTHENTICATED")
    response = sign_in(client)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/api" in cookie
    assert "Max-Age=3600" in cookie and "Secure" not in cookie
    token = client.cookies.get(COOKIE)
    csrf = response.json()["csrf_token"]
    with client.app.state.database.connect() as connection:
        session = connection.execute("SELECT * FROM sessions").fetchone()
        account = connection.execute("SELECT * FROM accounts WHERE username='synthetic.employee'").fetchone()
        assert session["token_hash"] == digest(token) and session["token_hash"] != token
        assert session["csrf_hash"] == digest(csrf) and session["csrf_hash"] != csrf
        assert account["password_hash"].startswith("pbkdf2_sha256$")
        assert PASSWORD not in account["password_hash"]
    assert client.get("/api/me").json() == {
        "id": "synthetic-user-1",
        "username": "synthetic.employee",
        "role": "employee",
        "employee_id": EMPLOYEE,
    }
    logout = client.post("/api/auth/logout", headers=mutation_headers(response))
    assert logout.status_code == 200 and logout.json() == {"status": "logged_out"}
    assert "Max-Age=0" in logout.headers["set-cookie"]
    assert_error(client.get("/api/me", headers={"Cookie": f"{COOKIE}={token}"}), 401, "UNAUTHENTICATED")
    with client.app.state.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_secure_cookie_in_staging(tmp_path, synthetic_password_hash):
    origin = "https://synthetic.example"
    app = create_app(
        Settings(
            database_path=tmp_path / "staging.sqlite3",
            app_env="staging",
            allowed_origins=(origin,),
        )
    )
    with TestClient(app, base_url=origin) as client:
        seed_accounts(app, synthetic_password_hash)
        response = client.post(
            "/api/auth/login",
            headers={"Origin": origin},
            json={
                "username": "synthetic.employee",
                "password": PASSWORD,
            },
        )
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
        assert client.get("/api/me").status_code == 200


@pytest.mark.parametrize("origin", [None, "http://attacker.example", "null"])
def test_login_rejects_missing_or_untrusted_origin(client, origin):
    headers = {} if origin is None else {"Origin": origin}
    response = client.post(
        "/api/auth/login",
        headers=headers,
        json={
            "username": "synthetic.employee",
            "password": PASSWORD,
        },
    )
    assert_error(response, 403, "ORIGIN_FORBIDDEN")
    assert COOKIE not in client.cookies


def test_csrf_and_origin_required_for_mutations(client):
    response = sign_in(client)
    assert_error(client.post("/api/auth/logout"), 403, "ORIGIN_FORBIDDEN")
    assert_error(client.post("/api/auth/logout", headers={"Origin": ORIGIN}), 403, "CSRF_FAILED")
    assert_error(
        client.post(
            "/api/auth/logout",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": "wrong-token",
            },
        ),
        403,
        "CSRF_FAILED",
    )
    assert_error(
        client.post(
            "/api/auth/logout",
            headers={
                "Origin": "http://attacker.example",
                "X-CSRF-Token": response.json()["csrf_token"],
            },
        ),
        403,
        "ORIGIN_FORBIDDEN",
    )
    assert client.get("/api/me").status_code == 200


def test_expired_and_tampered_session_rejected(client):
    sign_in(client)
    with client.app.state.database.connect() as connection:
        connection.execute("UPDATE sessions SET expires_at=?", (int(time.time()) - 1,))
    assert_error(client.get("/api/me"), 401, "UNAUTHENTICATED")
    client.cookies.clear()
    assert_error(client.get("/api/me", headers={"Cookie": f"{COOKIE}=tampered"}), 401, "UNAUTHENTICATED")


def test_employee_scope_and_hr_scope(client):
    sign_in(client)
    assert_error(client.get(f"/api/employees/{EMPLOYEE}"), 503, "DATASET_NOT_LOADED")
    assert_error(client.get(f"/api/employees/{OTHER_EMPLOYEE}"), 403, "FORBIDDEN")
    assert_error(client.get("/api/employees/not-present"), 403, "FORBIDDEN")
    assert_error(client.get("/api/employees"), 403, "FORBIDDEN")
    assert_error(client.get("/api/hr/summary"), 403, "FORBIDDEN")
    client.cookies.clear()
    sign_in(client, "synthetic.hr")
    assert_error(client.get(f"/api/employees/{OTHER_EMPLOYEE}"), 503, "DATASET_NOT_LOADED")
    assert_error(client.get("/api/employees/not-present"), 404, "EMPLOYEE_NOT_FOUND")
    assert_error(client.get("/api/employees?limit=0"), 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("method,path,payload", DOMAIN_REQUESTS)
def test_every_domain_route_authenticates_and_requires_valid_data(client, method, path, payload):
    assert_error(client.request(method, path, json=payload), 401, "UNAUTHENTICATED")
    login_response = sign_in(client, "synthetic.hr")
    response = client.request(method, path, json=payload, headers=mutation_headers(login_response))
    if path == "/api/hr/import":
        assert_error(response, 422, "invalid_source")
    elif path.endswith("/completions"):
        assert_error(response, 422, "IDEMPOTENCY_KEY_REQUIRED")
    else:
        assert_error(response, 503, "DATASET_NOT_LOADED")
    with client.app.state.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 2


def test_role_and_employee_mapping_cannot_be_supplied_by_browser(client):
    response = client.post(
        "/api/auth/login",
        headers={"Origin": ORIGIN},
        json={
            "username": "synthetic.employee",
            "password": PASSWORD,
            "role": "hr",
            "employee_id": OTHER_EMPLOYEE,
        },
    )
    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert PASSWORD not in response.text
    assert OTHER_EMPLOYEE not in response.text
    assert all(set(field) == {"location", "type"} for field in error.details["fields"])
    assert COOKIE not in client.cookies


def test_bad_credentials_are_generic_and_sql_injection_is_not_login(client):
    for username in ("synthetic.employee", "missing-synthetic-user", "' OR 1=1 --"):
        response = client.post(
            "/api/auth/login",
            headers={"Origin": ORIGIN},
            json={
                "username": username,
                "password": "synthetic-wrong-password",
            },
        )
        assert_error(response, 401, "INVALID_CREDENTIALS")
        assert "synthetic-wrong-password" not in response.text
        assert COOKIE not in client.cookies


def test_login_throttle_persists_across_application_restart(client, settings):
    for _ in range(5):
        response = client.post(
            "/api/auth/login",
            headers={"Origin": ORIGIN},
            json={
                "username": "synthetic.employee",
                "password": "synthetic-wrong-password",
            },
        )
        assert_error(response, 401, "INVALID_CREDENTIALS")
    with TestClient(create_app(settings), base_url=ORIGIN) as restarted:
        response = restarted.post(
            "/api/auth/login",
            headers={
                "Origin": ORIGIN,
                "X-Forwarded-For": "198.51.100.99",
            },
            json={"username": "synthetic.employee", "password": PASSWORD},
        )
        assert_error(response, 429, "LOGIN_RATE_LIMITED")
        assert response.headers["Retry-After"] == "900"
        with restarted.app.state.database.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


@pytest.mark.parametrize(
    "path,status,code,private_text",
    [
        ("/missing", 404, "NOT_FOUND", ""),
        ("/test-only/conflict", 409, "CONFLICT", "private database detail"),
        ("/test-only/storage-failure", 503, "STORAGE_UNAVAILABLE", "private database path"),
        ("/test-only/unhandled-failure", 500, "INTERNAL_ERROR", "private credential"),
    ],
)
def test_consistent_safe_error_envelopes(client, path, status, code, private_text):
    response = client.get(path, headers={"X-Request-ID": "attacker-controlled-id"})
    error = assert_error(response, status, code)
    assert error.request_id != "attacker-controlled-id"
    if private_text:
        assert private_text not in response.text


def test_openapi_describes_implemented_endpoints_and_cookie_auth(client):
    schema = client.get("/openapi.json").json()
    health = schema["paths"]["/api/health"]["get"]
    assert health["x-implementation-status"] == "implemented"
    for method, path, _ in DOMAIN_REQUESTS:
        normalized = path.split("?")[0].replace(EMPLOYEE, "{employee_id}")
        operation = schema["paths"][normalized][method.lower()]
        assert operation["x-implementation-status"] == "implemented"
        assert "200" in operation["responses"] and "501" not in operation["responses"]
        expected = {"DemoSession": []}
        if method in {"POST", "PATCH"}:
            expected["CsrfToken"] = []
            assert any(item["name"] == "Origin" and item["required"] for item in operation["parameters"])
        assert operation["security"] == [expected]
    assert client.get("/openapi.json").json() == schema


@pytest.mark.parametrize("path", ["/test-only/storage-failure", "/test-only/unhandled-failure"])
def test_frontend_can_read_boundary_errors(client, path):
    response = client.get(path, headers={"Origin": "http://localhost:5173"})
    assert response.status_code in {500, 503}
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    assert "X-Request-ID" in response.headers["Access-Control-Expose-Headers"]
    assert "Origin" in response.headers["Vary"]
    rejected = client.get(path, headers={"Origin": "https://attacker.example"})
    assert "Access-Control-Allow-Origin" not in rejected.headers


def test_session_remains_valid_after_restart(client, settings):
    sign_in(client)
    token = client.cookies.get(COOKIE)
    with TestClient(create_app(settings), base_url=ORIGIN) as restarted:
        response = restarted.get("/api/me", headers={"Cookie": f"{COOKIE}={token}"})
        assert response.status_code == 200
        assert response.json()["employee_id"] == EMPLOYEE


def test_throttle_expires_and_allows_valid_credentials(client, monkeypatch):
    from backend.app import auth

    now = int(time.time())
    monkeypatch.setattr(auth.time, "time", lambda: now)
    for _ in range(5):
        response = client.post(
            "/api/auth/login",
            headers={"Origin": ORIGIN},
            json={
                "username": "synthetic.employee",
                "password": "synthetic-wrong-password",
            },
        )
        assert response.status_code == 401
    monkeypatch.setattr(auth.time, "time", lambda: now + 901)
    assert sign_in(client).status_code == 200


def test_ip_limit_stops_username_spraying_and_ignores_forwarded_headers(client):
    for attempt in range(20):
        response = client.post(
            "/api/auth/login",
            headers={
                "Origin": ORIGIN,
                "X-Forwarded-For": f"198.51.100.{attempt + 1}",
            },
            json={"username": f"synthetic-missing-{attempt}", "password": PASSWORD},
        )
        assert_error(response, 401, "INVALID_CREDENTIALS")
    blocked = client.post(
        "/api/auth/login",
        headers={
            "Origin": ORIGIN,
            "X-Forwarded-For": "203.0.113.123",
        },
        json={"username": "synthetic.hr", "password": PASSWORD},
    )
    assert_error(blocked, 429, "LOGIN_RATE_LIMITED")
