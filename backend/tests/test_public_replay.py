"""Completion replay survives exhausted demo quota without weakening access."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from backend.app.auth import digest
from backend.app.config import Settings
from backend.app.demo_seed import EMPLOYEE_ID, EVENT_ID
from backend.app.main import create_app

ORIGIN = "http://localhost:8000"
EMPLOYEE_URL = f"/api/employees/{EMPLOYEE_ID}"


@pytest.fixture
def app(tmp_path):
    return create_app(
        Settings(database_path=tmp_path / "public.sqlite3", app_env="test", public_demo_mode=True)
    )


def enter(client):
    response = client.post("/api/public/session", json={"role": "employee"}, headers={"Origin": ORIGIN})
    assert response.status_code == 200
    return {
        "Origin": ORIGIN,
        "X-CSRF-Token": response.json()["csrf_token"],
        "Idempotency-Key": "last-allowed-completion",
    }


def quota(app, client, value=None):
    key = digest(client.cookies.get("cq_workspace"))
    with app.state.public_demo.registry.connect() as conn:
        if value is not None:
            conn.execute("UPDATE public_workspaces SET mutation_requests=? WHERE token_hash=?", (value, key))
        return conn.execute(
            "SELECT mutation_requests FROM public_workspaces WHERE token_hash=?", (key,)
        ).fetchone()[0]


def body(client):
    return {
        "expected_state_version": client.get(EMPLOYEE_URL).json()["state_version"],
        "event_id": EVENT_ID,
        "mode": "completion",
    }


@pytest.mark.parametrize("suffix", ["/completions", "/completions/", "/%63ompletions"])
def test_last_allowed_write_replays_at_limit_but_new_write_does_not(app, suffix):
    with TestClient(app, base_url=ORIGIN) as client:
        headers = enter(client)
        payload = body(client)
        before = client.get(EMPLOYEE_URL).json()
        quota(app, client, 99)
        first = client.post(EMPLOYEE_URL + suffix, json=payload, headers=headers)
        assert first.status_code == 200, first.text
        assert quota(app, client) == 100
        after = client.get(EMPLOYEE_URL).json()
        assert after["state_version"] == before["state_version"] + 1
        assert len(after["history"]) == len(before["history"]) + 1

        for _ in range(2):
            replay = client.post(EMPLOYEE_URL + suffix, json=payload, headers=headers)
            assert replay.status_code == 200, replay.text
            assert replay.content == first.content

        conflict = client.post(
            EMPLOYEE_URL + suffix,
            json={**payload, "mode": "demo_simulation"},
            headers=headers,
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
        fresh = client.post(
            EMPLOYEE_URL + suffix,
            json={**payload, "expected_state_version": after["state_version"]},
            headers={**headers, "Idempotency-Key": "new-operation"},
        )
        assert fresh.status_code == 429
        assert fresh.json()["code"] == "DEMO_RATE_LIMITED"
        assert quota(app, client) == 100
        assert client.get(EMPLOYEE_URL).json() == after


def test_parallel_retries_charge_once_and_return_identical_saved_response(app):
    with TestClient(app, base_url=ORIGIN) as client:
        headers = enter(client)
        payload = body(client)
        quota(app, client, 99)
        start = Barrier(2)

        def complete():
            start.wait(timeout=5)
            return client.post(EMPLOYEE_URL + "/completions", json=payload, headers=headers)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(complete) for _ in range(2)]
            first, second = [future.result(timeout=10) for future in futures]
        assert first.status_code == second.status_code == 200
        assert first.content == second.content
        assert quota(app, client) == 100
        after = client.get(EMPLOYEE_URL).json()
        assert after["state_version"] == payload["expected_state_version"] + 1
        assert sum(item["event_id"] == EVENT_ID for item in after["history"]) == 1


def test_replay_still_requires_session_origin_csrf_and_employee_scope(app):
    with TestClient(app, base_url=ORIGIN) as client:
        headers = enter(client)
        payload = body(client)
        quota(app, client, 99)
        url = EMPLOYEE_URL + "/completions"
        assert client.post(url, json=payload, headers=headers).status_code == 200
        after = client.get(EMPLOYEE_URL).json()
        for invalid_headers, expected_code in (
            ({**headers, "Origin": "https://evil.example"}, "ORIGIN_FORBIDDEN"),
            ({key: value for key, value in headers.items() if key != "Origin"}, "ORIGIN_FORBIDDEN"),
            ({**headers, "X-CSRF-Token": "invalid"}, "CSRF_FAILED"),
            ({key: value for key, value in headers.items() if key != "X-CSRF-Token"}, "CSRF_FAILED"),
        ):
            denied = client.post(url, json=payload, headers=invalid_headers)
            assert denied.status_code == 403
            assert denied.json()["code"] == expected_code
        forbidden = client.post(
            "/api/employees/UI_SYNTH_EMPLOYEE_02/completions", json=payload, headers=headers
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "FORBIDDEN"
        assert quota(app, client) == 100
        assert client.get(EMPLOYEE_URL).json() == after
        # A workspace capability alone is not an authenticated session.
        client.cookies.delete("cq_session")
        assert client.post(url, json=payload, headers=headers).status_code == 401


def test_replay_never_crosses_workspace_or_session_boundaries(app):
    with TestClient(app, base_url=ORIGIN) as first, TestClient(app, base_url=ORIGIN) as second:
        first_headers, second_headers = enter(first), enter(second)
        payload = body(first)
        second_before = second.get(EMPLOYEE_URL).json()
        quota(app, first, 99)
        url = EMPLOYEE_URL + "/completions"
        saved = first.post(url, json=payload, headers=first_headers)
        assert saved.status_code == 200
        quota(app, second, 100)
        denied = second.post(url, json=payload, headers=second_headers)
        assert denied.status_code == 429
        assert second.get(EMPLOYEE_URL).json() == second_before

        mixed_cookie = (
            f"cq_workspace={second.cookies.get('cq_workspace')}; cq_session={first.cookies.get('cq_session')}"
        )
        assert (
            second.post(url, json=payload, headers={**first_headers, "Cookie": mixed_cookie}).status_code
            == 401
        )
        # The same account ID/key in another visitor's database starts a new write.
        quota(app, second, 99)
        independent = second.post(url, json=payload, headers=second_headers)
        assert independent.status_code == 200
        assert (
            independent.json()["completion"]["completion_id"] != saved.json()["completion"]["completion_id"]
        )
        assert quota(app, first) == quota(app, second) == 100
