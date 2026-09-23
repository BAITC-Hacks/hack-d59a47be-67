"""Isolated, expiring server demonstrations using only our fictional seed.

The context variable selects a database per request, including worker threads.
No mutable global 'current user' or shared writable demo dataset is used.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response

from . import contracts as c
from .auth import COOKIE, digest, require_origin
from .database import Database
from .demo_seed import EMPLOYEE_ID, import_examples, synthetic_kit
from .errors import APIError
from .imports import ImportService

WORKSPACE_COOKIE = "cq_workspace"
MAX_WORKSPACES = 32
MAX_BODY_BYTES = 256_000


class WorkspaceDatabase(Database):
    def __init__(self, settings):
        super().__init__(settings)
        self.current: ContextVar[tuple[str, Database] | None] = ContextVar(
            "career_quest_workspace", default=None
        )

    def connect(self):
        selected = self.current.get()
        return selected[1].connect() if selected else super().connect()


class PublicWorkspaces:
    def __init__(self, database: WorkspaceDatabase):
        self.database = database
        self.settings = database.settings
        # Registry connections must never follow the visitor's context variable.
        self.registry = Database(self.settings)
        self.directory = self.settings.database_path.resolve().parent / "public-workspaces"
        self.creation_lock = threading.Lock()

    def initialize(self):
        # Fail closed instead of accidentally exposing an existing team's database.
        with self.registry.connect() as conn:
            if (
                conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
                or conn.execute("SELECT skills_json FROM dataset_state WHERE id=1").fetchone()[0]
            ):
                raise ValueError("Public demo requires a separate, empty registry database")
        self.directory.mkdir(parents=True, exist_ok=True)

    def workspace_database(self, key: str) -> Database:
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Invalid workspace identifier")
        path = (self.directory / f"{key}.sqlite3").resolve()
        if path.parent != self.directory.resolve():
            raise ValueError("Workspace path must stay inside its managed directory")
        return Database(replace(self.settings, database_path=path, sqlite_wal=False))

    def resolve(self, token: str | None):
        if not token or len(token) > 256:
            return None
        key = digest(token)
        with self.registry.connect() as conn:
            row = conn.execute(
                "SELECT expires_at FROM public_workspaces WHERE token_hash=? AND expires_at>?",
                (key, int(time.time())),
            ).fetchone()
        db = self.workspace_database(key)
        return (key, db) if row and db.settings.database_path.is_file() else None

    def cleanup(self, now: int):
        # Only exact, validated files registered by this service are removed.
        # Grace time lets already running requests finish before expired DB removal.
        with self.registry.connect() as conn:
            expired = conn.execute(
                "SELECT token_hash FROM public_workspaces WHERE expires_at<?", (now - 60,)
            ).fetchall()
            for row in expired:
                path = self.workspace_database(row[0]).settings.database_path
                for suffix in ("", "-journal", "-wal", "-shm"):
                    path.with_name(path.name + suffix).unlink(missing_ok=True)
                conn.execute("DELETE FROM public_workspaces WHERE token_hash=?", (row[0],))

    def create(self):
        with self.creation_lock:
            now = int(time.time())
            self.cleanup(now)
            with self.registry.connect() as conn:
                active = conn.execute("SELECT COUNT(*) FROM public_workspaces").fetchone()[0]
                recent = conn.execute(
                    "SELECT COUNT(*) FROM public_workspaces WHERE created_at>?", (now - 60,)
                ).fetchone()[0]
                if active >= MAX_WORKSPACES or recent >= 6:
                    raise APIError(429, "DEMO_CAPACITY", "Demo capacity reached; retry later.")
                token = secrets.token_urlsafe(32)
                key = digest(token)
                expires = now + self.settings.session_ttl_seconds
                conn.execute(
                    "INSERT INTO public_workspaces(token_hash,created_at,expires_at) VALUES (?,?,?)",
                    (key, now, expires),
                )
            db = self.workspace_database(key)
            # A collision never overwrites an existing file, even if unregistered.
            with db.settings.database_path.open("xb"):
                pass
            db.migrate()
            importer = ImportService(db)
            files = synthetic_kit()
            preview = importer.preview(files)
            importer.commit(files, preview["preview_token"])
            with db.connect() as conn:
                for role in ("employee", "hr"):
                    conn.execute(
                        "INSERT INTO accounts(id,username,password_hash,role,employee_id) VALUES (?,?,?,?,?)",
                        (
                            role,
                            f"demo.{role}",
                            "public-session-only",
                            role,
                            EMPLOYEE_ID if role == "employee" else None,
                        ),
                    )
            return token, (key, db), expires

    def charge(self, *, ai=False, importing=False):
        selected = self.database.current.get()
        if not selected:
            raise APIError(401, "PUBLIC_DEMO_REQUIRED", "Start your personal demonstration first.")
        now = int(time.time())
        with self.registry.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM public_workspaces WHERE token_hash=? AND expires_at>?", (selected[0], now)
            ).fetchone()
            if row is None:
                raise APIError(401, "UNAUTHENTICATED", "Demo session expired.")
            if row["mutation_requests"] >= 100 or (importing and row["import_requests"] >= 10):
                raise APIError(429, "DEMO_RATE_LIMITED", "Demo operation allowance reached.")
            if ai:
                budget = conn.execute("SELECT * FROM public_ai_budget WHERE id=1").fetchone()
                if not budget or budget["window_start"] <= now - 3600:
                    conn.execute("INSERT OR REPLACE INTO public_ai_budget VALUES (1,?,0)", (now,))
                    count = 0
                else:
                    count = budget["requests"]
                if row["ai_requests"] >= 10 or count >= 60:
                    raise APIError(429, "DEMO_RATE_LIMITED", "Demo AI allowance reached; retry later.")
                conn.execute("UPDATE public_ai_budget SET requests=requests+1 WHERE id=1")
            conn.execute(
                "UPDATE public_workspaces SET mutation_requests=mutation_requests+1, "
                "ai_requests=ai_requests+?, import_requests=import_requests+? WHERE token_hash=?",
                (int(ai), int(importing), selected[0]),
            )

    def session(self, request: Request, response: Response, role: str):
        require_origin(request)
        selected = self.database.current.get()
        workspace_token = request.cookies.get(WORKSPACE_COOKIE)
        if not selected:
            workspace_token, selected, expires = self.create()
        else:
            self.charge()
            with self.registry.connect() as conn:
                expires = conn.execute(
                    "SELECT expires_at FROM public_workspaces WHERE token_hash=?", (selected[0],)
                ).fetchone()[0]
        db = selected[1]
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with db.connect() as conn:
            # A role change invalidates all earlier sessions for this visitor only.
            conn.execute("DELETE FROM sessions")
            conn.execute(
                "INSERT INTO sessions(token_hash,account_id,csrf_hash,expires_at) VALUES (?,?,?,?)",
                (digest(token), role, digest(csrf), expires),
            )
        for name, value in ((WORKSPACE_COOKIE, workspace_token), (COOKIE, token)):
            response.set_cookie(
                name,
                value,
                httponly=True,
                secure=self.settings.cookie_secure,
                samesite="lax",
                path="/api",
                max_age=max(0, expires - int(time.time())),
            )
        return {
            "user": {
                "id": role,
                "username": f"demo.{role}",
                "role": role,
                "employee_id": EMPLOYEE_ID if role == "employee" else None,
            },
            "csrf_token": csrf,
            "expires_at": datetime.fromtimestamp(expires, timezone.utc),
        }


def register_public_routes(app: FastAPI, manager: PublicWorkspaces | None):
    @app.get("/api/public/examples/{filename}", tags=["implemented"])
    def example_file(filename: str):
        if manager:
            for file in import_examples():
                if filename == file["source_filename"]:
                    return Response(
                        file["content"],
                        media_type="application/json" if filename.endswith(".json") else "text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                    )
        raise APIError(404, "NOT_FOUND", "Example not found.")

    @app.get("/api/public/config", response_model=c.PublicDemoConfig, tags=["implemented"])
    def public_config():
        return {
            "enabled": manager is not None,
            "session_ttl_seconds": app.state.settings.session_ttl_seconds,
            "ai_enabled": app.state.settings.ai_enabled,
        }

    @app.post("/api/public/session", response_model=c.SessionResponse, tags=["implemented"])
    def public_session(payload: c.PublicSessionRequest, request: Request, response: Response):
        if manager is None:
            raise APIError(404, "NOT_FOUND", "Public demonstration is disabled.")
        return manager.session(request, response, payload.role)
