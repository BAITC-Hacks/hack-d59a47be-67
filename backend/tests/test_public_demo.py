"""Public access exercises real services; no fake recommendation success."""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.database import Database
from backend.app.demo_seed import EMPLOYEE_ID, EVENT_ID, synthetic_kit
from backend.app.imports import ImportService
from backend.app.main import create_app

ORIGIN = "http://localhost:8000"


@pytest.fixture
def settings(tmp_path):
    return Settings(database_path=tmp_path / "public.sqlite3", app_env="test", public_demo_mode=True)


def enter(client, role="employee"):
    response = client.post("/api/public/session", json={"role": role}, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


def test_private_mode_never_exposes_public_login(settings):
    with TestClient(create_app(replace(settings, public_demo_mode=False)), base_url=ORIGIN) as client:
        assert client.get("/api/public/config").json()["enabled"] is False
        assert client.post("/api/public/session", json={"role": "hr"}).status_code == 404


def test_refuses_existing_private_data(settings):
    db = Database(settings)
    db.migrate()
    importer = ImportService(db)
    files = synthetic_kit()
    importer.commit(files, importer.preview(files)["preview_token"])
    before = settings.database_path.read_bytes()
    with pytest.raises(ValueError, match="separate, empty"):
        with TestClient(create_app(settings)):
            pass
    assert settings.database_path.read_bytes() == before


def test_isolation_real_completion_roles_and_restart(settings):
    app = create_app(settings)
    with TestClient(app, base_url=ORIGIN) as first, TestClient(app, base_url=ORIGIN) as second:
        assert first.get("/api/me").status_code == 401
        headers = enter(first)
        enter(second)
        url = f"/api/employees/{EMPLOYEE_ID}"
        before = first.get(url).json()
        assert first.get("/api/hr/analytics").status_code == 403
        assert first.get("/api/employees/UI_SYNTH_EMPLOYEE_02").status_code == 403
        assert (
            first.post(
                url + "/recommendations", json={"scenario_date": before["scenario_date"]}, headers=headers
            ).json()["status"]
            == "not_configured"
        )
        body = {"expected_state_version": before["state_version"], "event_id": EVENT_ID, "mode": "completion"}
        completion = first.post(
            url + "/completions", json=body, headers={**headers, "Idempotency-Key": "public-real-completion"}
        )
        assert completion.status_code == 200, completion.text
        assert (
            first.post(
                url + "/completions",
                json=body,
                headers={**headers, "Idempotency-Key": "public-real-completion"},
            ).json()
            == completion.json()
        )
        after = first.get(url).json()
        assert after["state_version"] == before["state_version"] + 1
        assert after["current_skills"] != before["current_skills"]
        assert second.get(url).json() == before
        old_session = first.cookies.get("cq_session")
        hr_headers = enter(first, "hr")
        analytics = first.get("/api/hr/analytics").json()
        assert analytics["state_version"] == after["state_version"]
        assert analytics["employee_count"] == 3
        assert (
            first.post("/api/hr/import", json={"files": [], "dry_run": True}, headers=headers).status_code
            == 403
        )
        assert first.get("/api/me", headers={"Cookie": f"cq_session={old_session}"}).status_code == 401
        cookies = dict(first.cookies)
    with TestClient(create_app(settings), base_url=ORIGIN, cookies=cookies) as restarted:
        assert restarted.get(url).json()["current_skills"] == after["current_skills"]
        assert restarted.get("/api/hr/analytics").status_code == 200
        assert restarted.post("/api/auth/logout", headers=hr_headers).status_code == 200
        assert restarted.get(url).status_code == 401


def test_origin_csrf_upload_and_unforgeable_workspace(settings):
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        for headers in ({}, {"Origin": "https://evil.example"}):
            assert client.post("/api/public/session", json={"role": "hr"}, headers=headers).status_code == 403
        assert (
            client.post("/api/public/session", json={"role": "admin"}, headers={"Origin": ORIGIN}).status_code
            == 422
        )
        client.cookies.set("cq_workspace", "../private.db")
        assert client.get("/api/me").status_code == 401
        client.cookies.clear()
        headers = enter(client, "hr")
        assert client.post("/api/hr/import", json={}, headers={"Origin": ORIGIN}).status_code == 403
        response = client.post("/api/hr/import", content=b"x" * 256_001, headers=headers)
        assert response.status_code == 413
        assert response.json()["code"] == "DEMO_UPLOAD_LIMIT"
        assert (
            client.post("/api/auth/login", json={"username": "demo.hr", "password": "x"}).status_code == 403
        )
        # Session token copied without its workspace capability cannot read any data.
        assert (
            client.get(
                "/api/me", headers={"Cookie": f"cq_session={client.cookies.get('cq_session')}"}
            ).status_code
            == 401
        )


def test_two_parallel_requests_keep_database_context_separate(settings):
    app = create_app(settings)
    with TestClient(app, base_url=ORIGIN) as first, TestClient(app, base_url=ORIGIN) as second:
        enter(first, "hr")
        enter(second)

        def read(client, path):
            return [client.get(path).status_code for _ in range(10)]

        with ThreadPoolExecutor(max_workers=2) as pool:
            hr = pool.submit(read, first, "/api/hr/analytics")
            employee = pool.submit(read, second, "/api/hr/analytics")
            assert hr.result() == [200] * 10
            assert employee.result() == [403] * 10


def test_expiry_cleanup_and_capacity_are_bounded(settings):
    app = create_app(settings)
    with TestClient(app, base_url=ORIGIN) as client:
        enter(client)
        manager = app.state.public_demo
        with manager.registry.connect() as conn:
            key = conn.execute("SELECT token_hash FROM public_workspaces").fetchone()[0]
            conn.execute("UPDATE public_workspaces SET expires_at=?", (int(time.time()) - 61,))
        assert client.get("/api/me").status_code == 401
        neighbor = manager.directory.parent / "preserved.sqlite3"
        neighbor.write_text("do not delete")
        manager.cleanup(int(time.time()))
        assert not manager.workspace_database(key).settings.database_path.exists()
        assert neighbor.read_text() == "do not delete"
        for _ in range(6):
            client.cookies.clear()
            enter(client)
        client.cookies.clear()
        assert (
            client.post("/api/public/session", json={"role": "hr"}, headers={"Origin": ORIGIN}).status_code
            == 429
        )


def test_mutation_and_ai_budget_are_server_enforced(settings):
    app = create_app(settings)
    with TestClient(app, base_url=ORIGIN) as client:
        headers = enter(client, "hr")
        with app.state.public_demo.registry.connect() as conn:
            conn.execute("UPDATE public_workspaces SET mutation_requests=100")
        assert client.post("/api/hr/import", json={}, headers=headers).status_code == 429
        assert client.get("/api/hr/analytics").status_code == 200
        assert client.post("/api/auth/logout", headers=headers).status_code == 200
        assert client.get("/api/me").status_code == 401


def test_imports_three_new_profiles_and_keeps_other_visitors_unchanged(settings):
    app = create_app(settings)
    with TestClient(app, base_url=ORIGIN) as client, TestClient(app, base_url=ORIGIN) as other:
        headers = enter(client, "hr")
        enter(other, "hr")
        files = []
        for filename in ("employees.json", "activity_history.csv"):
            response = client.get(f"/api/public/examples/{filename}")
            assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
            files.append(
                {
                    "source_filename": filename,
                    "source_format": filename.rsplit(".", 1)[1],
                    "content": response.text,
                }
            )
        preview = client.post("/api/hr/import", json={"files": files, "dry_run": True}, headers=headers)
        assert preview.status_code == 200, preview.text
        result = client.post(
            "/api/hr/import",
            headers=headers,
            json={"files": files, "dry_run": False, "preview_token": preview.json()["preview_token"]},
        )
        assert result.status_code == 200, result.text
        assert client.get("/api/hr/analytics").json()["employee_count"] == 6
        assert other.get("/api/hr/analytics").json()["employee_count"] == 3
        assert client.get("/api/employees/PUBLIC_IMPORT_01").status_code == 200


def test_ai_budget_limits_per_visitor_and_global_without_calling_provider(settings):
    app = create_app(replace(settings, ai_enabled=True))
    with TestClient(app, base_url=ORIGIN) as client:
        headers = enter(client)
        manager = app.state.public_demo
        with manager.registry.connect() as conn:
            conn.execute("UPDATE public_workspaces SET ai_requests=10")
        url = f"/api/employees/{EMPLOYEE_ID}/recommendations"
        payload = {"scenario_date": "2026-10-01"}
        assert client.post(url, json=payload, headers=headers).status_code == 429
        with manager.registry.connect() as conn:
            conn.execute("UPDATE public_workspaces SET ai_requests=0")
            conn.execute("INSERT OR REPLACE INTO public_ai_budget VALUES (1,?,60)", (int(time.time()),))
        assert client.post(url, json=payload, headers=headers).status_code == 429


def test_fullstack_static_build_and_same_origin_configuration(settings, tmp_path):
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>Career Quest</title>")
    with TestClient(create_app(replace(settings, static_files_path=static)), base_url=ORIGIN) as client:
        assert "Career Quest" in client.get("/").text
        assert client.get("/api/missing").status_code == 404
        assert client.get("/../public.sqlite3").status_code == 404
        assert client.get("/api/public/config").json()["enabled"] is True
    env = {"RENDER_EXTERNAL_URL": "https://career-quest.onrender.com", "PUBLIC_DEMO_MODE": "true"}
    assert env["RENDER_EXTERNAL_URL"] in Settings.from_env(env).allowed_origins
    with pytest.raises(ValueError):
        Settings.from_env({"PUBLIC_BASE_URL": "https://evil.example/path"})
