"""Synthetic infrastructure fixtures; not compatible-import claims or organiser data."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.database import Database, MigrationError


def make_database(tmp_path: Path, **settings: object) -> Database:
    return Database(Settings(database_path=tmp_path / "nested" / "test.sqlite3", app_env="test", **settings))


def test_migrations_preserve_data_across_repeated_startup(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    assert database.readiness() is False
    database.migrate()
    assert database.readiness() is True
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO employees (id, display_name) VALUES (?, ?)",
            ("synthetic-employee-1", "Synthetic Employee One"),
        )
    restarted = Database(database.settings)
    restarted.migrate()
    with restarted.connect() as connection:
        assert (
            connection.execute("SELECT display_name FROM employees").fetchone()[0] == "Synthetic Employee One"
        )
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == len(database._migrations())
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_foreign_keys_and_role_relationship_are_enforced(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    database.migrate()
    with pytest.raises(sqlite3.IntegrityError):
        with database.connect() as connection:
            connection.execute(
                "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                ("synthetic-account", "synthetic-user", "not-a-real-hash", "employee", "missing"),
            )
    with pytest.raises(sqlite3.IntegrityError):
        with database.connect() as connection:
            connection.execute(
                "INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                ("synthetic-account", "synthetic-user", "not-a-real-hash", "employee", None),
            )
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_connection_rolls_back_a_failed_unit_of_work(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    database.migrate()
    with pytest.raises(RuntimeError):
        with database.connect() as connection:
            connection.execute("INSERT INTO employees VALUES ('synthetic', 'Synthetic')")
            raise RuntimeError("synthetic failure")
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0


def test_applied_migration_edit_is_rejected(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    migrations = tmp_path / "migrations"
    shutil.copytree(database.migrations_path, migrations)
    database = Database(database.settings, migrations)
    database.migrate()
    initial = migrations / "001_initial.sql"
    initial.write_text(initial.read_text() + "\n-- synthetic checksum change\n")
    with pytest.raises(MigrationError, match="checksum"):
        database.migrate()
    assert database.readiness() is False


def test_failed_migration_is_atomic(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    migrations = tmp_path / "migrations"
    shutil.copytree(database.migrations_path, migrations)
    database = Database(database.settings, migrations)
    database.migrate()
    (migrations / "099_broken.sql").write_text(
        "CREATE TABLE synthetic_partial (id INTEGER);\nINSERT INTO missing_table VALUES (1);\n"
    )
    with pytest.raises(sqlite3.OperationalError):
        database.migrate()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == len(database._migrations()) - 1
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name = 'synthetic_partial'").fetchone()
            is None
        )
    assert database.readiness() is False


def test_readiness_rejects_missing_table(tmp_path: Path) -> None:
    database = make_database(tmp_path)
    database.migrate()
    with database.connect() as connection:
        connection.execute("DROP TABLE login_attempts")
    assert database.readiness() is False


def test_wal_requires_explicit_local_disk_acknowledgement(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="local disk"):
        make_database(tmp_path, sqlite_wal=True)
    database = make_database(tmp_path, sqlite_wal=True, sqlite_local_disk=True)
    database.migrate()
    with database.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_settings_env_and_secure_staging_defaults(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "APP_ENV": "staging",
            "DATABASE_PATH": str(tmp_path / "staging.sqlite3"),
            "ALLOWED_ORIGINS": '["https://synthetic.example"]',
            "SESSION_TTL_SECONDS": "900",
            "AI_ENABLED": "false",
            "COMMIT_SHA": "abcdef0",
        }
    )
    assert settings.cookie_secure is True
    assert settings.ai_enabled is False
    assert settings.session_ttl_seconds == 900
    assert settings.allowed_origins == ("https://synthetic.example",)
    assert settings.commit_sha == "abcdef0"
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        Settings.from_env({"APP_ENV": "staging", "COOKIE_SECURE": "false"})


@pytest.mark.parametrize(
    "environment",
    [
        {"APP_ENV": "production-typo"},
        {"COOKIE_SECURE": "maybe"},
        {"ALLOWED_ORIGINS": '"https://synthetic.example"'},
        {"ALLOWED_ORIGINS": '["*"]'},
        {"ALLOWED_ORIGINS": '["http://synthetic.example/path"]'},
        {"ALLOWED_ORIGINS": '["http://user:password@synthetic.example"]'},
        {"ALLOWED_ORIGINS": '["http://synthetic.example:99999"]'},
        {"ALLOWED_ORIGINS": "[]"},
        {"SESSION_TTL_SECONDS": "0"},
        {"SESSION_TTL_SECONDS": "not-an-integer"},
        {"DATABASE_PATH": ":memory:"},
        {"COMMIT_SHA": "not a commit"},
    ],
)
def test_invalid_settings_fail_closed(environment: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        Settings.from_env(environment)
