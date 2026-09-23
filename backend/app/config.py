"""Small, explicit environment configuration; no secrets or data are read implicitly."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_DEFAULT_ORIGINS = ("http://127.0.0.1:8000", "http://localhost:8000", "http://localhost:5173")


def _boolean(name: str, value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True)
class Settings:
    database_path: Path = Path("var/career_quest.sqlite3")
    app_env: str = "development"
    allowed_origins: tuple[str, ...] = _DEFAULT_ORIGINS
    cookie_secure: bool | None = None
    session_ttl_seconds: int = 3600
    sqlite_wal: bool = False
    sqlite_local_disk: bool = False
    ai_enabled: bool = False
    commit_sha: str = "unknown"
    public_demo_mode: bool = False
    static_files_path: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "database_path", Path(self.database_path))
        if self.static_files_path is not None:
            object.__setattr__(self, "static_files_path", Path(self.static_files_path))
        object.__setattr__(self, "allowed_origins", tuple(self.allowed_origins))
        if self.app_env not in {"development", "test", "staging"}:
            raise ValueError("APP_ENV must be development, test, or staging")
        if self.database_path == Path(":memory:"):
            raise ValueError("DATABASE_PATH must be a persistent SQLite file")
        if not 60 <= self.session_ttl_seconds <= 86400:
            raise ValueError("SESSION_TTL_SECONDS must be between 60 and 86400")
        secure = self.app_env == "staging" if self.cookie_secure is None else self.cookie_secure
        object.__setattr__(self, "cookie_secure", secure)
        if self.app_env == "staging" and not secure:
            raise ValueError("HTTPS staging requires COOKIE_SECURE=true")
        if self.sqlite_wal and not self.sqlite_local_disk:
            raise ValueError("WAL requires SQLITE_LOCAL_DISK=true on one host's local disk")
        if not self.allowed_origins:
            raise ValueError("ALLOWED_ORIGINS must include at least one explicit origin")
        for origin in self.allowed_origins:
            if not isinstance(origin, str):
                raise ValueError("ALLOWED_ORIGINS entries must be strings")
            parsed = urlsplit(origin)
            try:
                valid_port = parsed.port
            except ValueError as exc:
                raise ValueError("ALLOWED_ORIGINS contains an invalid port") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "*" in origin
                or any(character.isspace() for character in origin)
                or (valid_port is not None and not 1 <= valid_port <= 65535)
            ):
                raise ValueError("ALLOWED_ORIGINS must contain exact HTTP(S) origins without paths")
        if self.commit_sha != "unknown" and (
            not 7 <= len(self.commit_sha) <= 64
            or any(character not in "0123456789abcdefABCDEF" for character in self.commit_sha)
        ):
            raise ValueError("COMMIT_SHA must be a hexadecimal commit ID or unknown")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ
        raw_origins = env.get("ALLOWED_ORIGINS")
        origins = _DEFAULT_ORIGINS
        if raw_origins is not None:
            try:
                parsed_origins = json.loads(raw_origins)
            except json.JSONDecodeError as exc:
                raise ValueError("ALLOWED_ORIGINS must be a JSON array") from exc
            if not isinstance(parsed_origins, list):
                raise ValueError("ALLOWED_ORIGINS must be a JSON array")
            origins = tuple(parsed_origins)
        public_origin = env.get("PUBLIC_BASE_URL") or env.get("RENDER_EXTERNAL_URL")
        if public_origin:
            origins = (*origins, public_origin)
        try:
            ttl = int(env.get("SESSION_TTL_SECONDS", "3600"))
        except ValueError as exc:
            raise ValueError("SESSION_TTL_SECONDS must be an integer") from exc
        return cls(
            database_path=Path(env.get("DATABASE_PATH", "var/career_quest.sqlite3")),
            app_env=env.get("APP_ENV", "development"),
            allowed_origins=origins,
            cookie_secure=(
                _boolean("COOKIE_SECURE", env.get("COOKIE_SECURE"), False) if "COOKIE_SECURE" in env else None
            ),
            session_ttl_seconds=ttl,
            sqlite_wal=_boolean("SQLITE_WAL", env.get("SQLITE_WAL"), False),
            sqlite_local_disk=_boolean("SQLITE_LOCAL_DISK", env.get("SQLITE_LOCAL_DISK"), False),
            ai_enabled=_boolean("AI_ENABLED", env.get("AI_ENABLED"), False),
            commit_sha=(
                env.get("RENDER_GIT_COMMIT", "unknown")
                if env.get("COMMIT_SHA", "unknown") == "unknown"
                else env["COMMIT_SHA"]
            ),
            public_demo_mode=_boolean("PUBLIC_DEMO_MODE", env.get("PUBLIC_DEMO_MODE"), False),
            static_files_path=Path(env["STATIC_FILES_PATH"]) if env.get("STATIC_FILES_PATH") else None,
        )
