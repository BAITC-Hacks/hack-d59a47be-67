"""Exercise the real AI plugin through the backend, replacing only remote selection."""

from fastapi.testclient import TestClient

from backend.app import ai
from backend.app.auth import hash_password
from backend.app.config import Settings
from backend.app.main import create_app
from backend.tests.test_workflow import (
    EMPLOYEE,
    EVENT,
    ORIGIN,
    PASSWORD,
    USER,
    commit_batch,
    source_batch,
)


class SyntheticSelection:
    def __init__(self):
        self.payloads = []

    async def select_events(self, payload, *, instructions):
        self.payloads.append(payload)
        assert all(
            candidate["event_id"] != "WORKFLOW_MANDATORY" for candidate in payload["eligible_candidates"]
        )
        return [payload["eligible_candidates"][0]["event_id"]]


def test_real_plugin_profile_recommendation_completion_and_persisted_stale(tmp_path, monkeypatch):
    provider = SyntheticSelection()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-no-network")
    monkeypatch.setattr(ai, "AsyncOpenAIProvider", lambda **kwargs: provider)
    settings = Settings(database_path=tmp_path / "ai-integration.sqlite3", app_env="test", ai_enabled=True)
    app = create_app(settings)
    with TestClient(app, base_url=ORIGIN) as client:
        commit_batch(app.state.database, source_batch())
        with app.state.database.connect() as connection:
            connection.execute(
                "INSERT INTO accounts VALUES (?,?,?,?,?)",
                (USER["id"], USER["username"], hash_password(PASSWORD), "employee", EMPLOYEE),
            )
        login = client.post(
            "/api/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": USER["username"], "password": PASSWORD},
        )
        assert login.status_code == 200
        headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]}
        profile_url = f"/api/employees/{EMPLOYEE}"
        response = client.post(
            profile_url + "/recommendations",
            headers=headers,
            json={"scenario_date": "2026-10-01", "limit": 3},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "ok" and result["engine"] == "oleg"
        assert result["recommendations"][0]["event_id"] == EVENT
        assert len(result["recommendations"][0]["explanation"]["evidence_ids"]) >= 4
        assert client.get(profile_url + "/recommendations/latest").json() == result
        profile_before = client.get(profile_url).json()
        completion = client.post(
            profile_url + "/completions",
            headers={**headers, "Idempotency-Key": "synthetic-ai-complete"},
            json={
                "expected_state_version": profile_before["state_version"],
                "event_id": EVENT,
                "mode": "completion",
            },
        )
        assert completion.status_code == 200
        assert client.get(profile_url + "/recommendations/latest").json()["stale"] is True
        assert client.get(profile_url).json()["state_version"] > profile_before["state_version"]
        cookies = dict(client.cookies)
    # A new application instance reloads full trusted explanations from SQLite.
    with TestClient(create_app(settings), base_url=ORIGIN) as restarted:
        restarted.cookies.update(cookies)
        latest = restarted.get(profile_url + "/recommendations/latest").json()
        assert latest["recommendations"] == result["recommendations"]
        assert latest["stale"] is True
    assert len(provider.payloads) == 1
