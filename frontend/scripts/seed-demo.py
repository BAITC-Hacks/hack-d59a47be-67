"""Create an isolated, synthetic UI demo database without touching existing data.

Run from the repository root with an explicit absolute --database path. Random
credentials are written once to an ignored local file, never printed. Repeating
the command only verifies this script's existing database and credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.auth import hash_password, verify_password  # noqa: E402
from backend.app.config import Settings  # noqa: E402
from backend.app.database import Database  # noqa: E402
from backend.app.demo_seed import (  # noqa: E402, F401
    EMPLOYEE_ID,
    EVENT_ID,
    MARKER,
    META,
    OTHER_EMPLOYEE_ID,
    ROLE,
    history_source,
    profile,
    source,
    synthetic_kit,
)
from backend.app.imports import ImportService  # noqa: E402


def verify_existing(database_path: Path, credentials_path: Path) -> dict:
    """Read-only verification; never migrate, reseed or reset an existing file."""
    try:
        credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
        if credentials["seed"] != MARKER or Path(credentials["database_path"]) != database_path:
            raise ValueError
        with sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True) as connection:
            catalog = connection.execute("SELECT skills_json FROM dataset_state WHERE id=1").fetchone()
            if not catalog or json.loads(catalog[0])["meta"]["dataset"] != MARKER:
                raise ValueError
            for role in ("employee", "hr"):
                account = credentials[role]
                row = connection.execute(
                    "SELECT password_hash,role,employee_id FROM accounts WHERE username=?",
                    (account["username"],),
                ).fetchone()
                if (
                    not row
                    or row[1] != role
                    or row[2] != account["employee_id"]
                    or not verify_password(account["password"], row[0])
                ):
                    raise ValueError
        return credentials
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        raise ValueError(
            "Existing database or credentials are not this verified demo; nothing changed."
        ) from exc


def seed(database_path: Path, credentials_path: Path) -> tuple[dict, bool]:
    if not database_path.is_absolute():
        raise ValueError("--database must be an explicit absolute path to a separate SQLite file.")
    database_path, credentials_path = database_path.resolve(), credentials_path.resolve()
    if database_path == credentials_path or database_path.suffix.lower() not in {
        ".sqlite3",
        ".sqlite",
        ".db",
    }:
        raise ValueError("Use a separate .sqlite3, .sqlite or .db database path.")
    if database_path.exists():
        return verify_existing(database_path, credentials_path), False
    if credentials_path.exists():
        raise ValueError("Credentials file already exists; no file will be overwritten.")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    # Reserve only a new, explicitly named file; never reuse another database.
    descriptor = os.open(database_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    db = Database(Settings(database_path=database_path, app_env="development", ai_enabled=False))
    db.migrate()
    importer = ImportService(db)
    files = synthetic_kit()
    preview = importer.preview(files)
    importer.commit(files, preview["preview_token"])
    credentials = {"seed": MARKER, "database_path": str(database_path), "scenario_date": META["as_of_date"]}
    with db.connect() as connection:
        for role, username, employee_id in [
            ("employee", "demo.employee", EMPLOYEE_ID),
            ("hr", "demo.hr", None),
        ]:
            password = secrets.token_urlsafe(24)
            connection.execute(
                "INSERT INTO accounts(id,username,password_hash,role,employee_id) VALUES (?,?,?,?,?)",
                (secrets.token_hex(16), username, hash_password(password), role, employee_id),
            )
            credentials[role] = {"username": username, "password": password, "employee_id": employee_id}
    descriptor = os.open(credentials_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(credentials, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return credentials, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", required=True, type=Path, help="Absolute path to a new, isolated SQLite file"
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=ROOT / "frontend" / ".local-demo.json",
        help="Local credentials output; never commit this file",
    )
    args = parser.parse_args()
    try:
        _, created = seed(args.database, args.credentials)
    except Exception as exc:
        # Import errors can contain source content; do not dump exceptions or credentials.
        message = (
            str(exc) if isinstance(exc, ValueError) else "Demo setup failed; existing files were preserved."
        )
        print(message, file=sys.stderr)
        return 1
    print(
        "Created isolated synthetic demo."
        if created
        else "Verified existing demo; no data or credentials changed."
    )
    print(f"Database: {args.database.resolve()}")
    print(f"Credentials saved locally: {args.credentials.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
