"""Persistent SQLite connections and checksum-verified, transactional migrations."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import Settings


class MigrationError(RuntimeError):
    """The database cannot be safely brought to the checked-in schema."""


class Database:
    def __init__(self, settings: Settings, migrations_path: Path | None = None) -> None:
        self.settings = settings
        self.migrations_path = migrations_path or Path(__file__).resolve().parents[1] / "migrations"

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.settings.database_path, timeout=5)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrations(self) -> list[tuple[int, str, str, str]]:
        paths = sorted(self.migrations_path.glob("*.sql"))
        if not paths:
            raise MigrationError("No migration files are installed")
        migrations: list[tuple[int, str, str, str]] = []
        seen: set[int] = set()
        for path in paths:
            match = re.fullmatch(r"([0-9]{3})_[a-z0-9_]+\.sql", path.name)
            if match is None:
                raise MigrationError("Invalid migration filename")
            version = int(match.group(1))
            if version in seen or version < 1:
                raise MigrationError("Migration versions must be unique positive integers")
            seen.add(version)
            content = path.read_bytes()
            migrations.append(
                (version, path.name, hashlib.sha256(content).hexdigest(), content.decode("utf-8"))
            )
        return migrations

    @staticmethod
    def _verify_applied(
        connection: sqlite3.Connection, migrations: list[tuple[int, str, str, str]]
    ) -> set[int]:
        expected = {version: (name, checksum) for version, name, checksum, _ in migrations}
        applied: set[int] = set()
        for row in connection.execute("SELECT version, name, checksum FROM schema_migrations"):
            if expected.get(row["version"]) != (row["name"], row["checksum"]):
                raise MigrationError("Applied migration is missing or its checksum has changed")
            applied.add(row["version"])
        # A missing older migration alongside a newer one signals a damaged history.
        if applied and any(version < max(applied) and version not in applied for version in expected):
            raise MigrationError("Applied migration history has a gap")
        return applied

    @staticmethod
    def _execute_sql(connection: sqlite3.Connection, content: str) -> None:
        # executescript() implicitly commits; execute complete statements to preserve
        # one atomic transaction for the migration and its version record.
        statement = ""
        for line in content.splitlines(keepends=True):
            for character in line:
                statement += character
                if character == ";" and sqlite3.complete_statement(statement):
                    connection.execute(statement)
                    statement = ""
        if statement.strip() and not all(
            not line.strip() or line.lstrip().startswith("--") for line in statement.splitlines()
        ):
            raise MigrationError("Migration contains an incomplete SQL statement")

    def migrate(self) -> None:
        migrations = self._migrations()
        with self.connect() as connection:
            # WAL requires explicit acknowledgement of a local disk on one host.
            # Never choose WAL based on a guessed filesystem type.
            journal = "WAL" if self.settings.sqlite_wal else "DELETE"
            actual_journal = connection.execute(f"PRAGMA journal_mode = {journal}").fetchone()[0]
            if actual_journal.lower() != journal.lower():
                raise MigrationError("Requested SQLite journal mode is unavailable")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
                "checksum TEXT NOT NULL, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            applied = self._verify_applied(connection, migrations)
            for version, name, checksum, content in migrations:
                if version in applied:
                    continue
                self._execute_sql(connection, content)
                connection.execute(
                    "INSERT INTO schema_migrations(version, name, checksum) VALUES (?, ?, ?)",
                    (version, name, checksum),
                )

    def readiness(self) -> bool:
        try:
            migrations = self._migrations()
            with self.connect() as connection:
                applied = self._verify_applied(connection, migrations)
                if len(applied) != len(migrations):
                    return False
                connection.execute("SELECT id, display_name FROM employees LIMIT 0")
                connection.execute(
                    "SELECT id, username, password_hash, role, employee_id FROM accounts LIMIT 0"
                )
                connection.execute(
                    "SELECT token_hash, account_id, csrf_hash, expires_at FROM sessions LIMIT 0"
                )
                connection.execute(
                    "SELECT bucket, failures, window_start, blocked_until FROM login_attempts LIMIT 0"
                )
                connection.execute(
                    "SELECT revision, scenario_date, skills_json, events_json FROM dataset_state LIMIT 0"
                )
                connection.execute(
                    "SELECT employee_id, profile_json, source_json FROM employee_profiles LIMIT 0"
                )
                connection.execute("SELECT record_id, record_json FROM activity_history LIMIT 0")
                connection.execute("SELECT token_hash, payload_hash, revision FROM import_previews LIMIT 0")
                connection.execute("SELECT idempotency_key, response_json FROM completion_requests LIMIT 0")
                connection.execute("SELECT recommendation_id, response_json FROM recommendation_runs LIMIT 0")
                return connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        except (OSError, sqlite3.Error, MigrationError, UnicodeError):
            return False
