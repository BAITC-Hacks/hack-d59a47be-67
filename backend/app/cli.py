"""Local operator commands. Credentials are read with getpass, never CLI arguments."""

import argparse
import getpass
import secrets
import sqlite3

from dotenv import load_dotenv

from .auth import hash_password
from .config import Settings
from .database import Database


def main():
    load_dotenv(override=False)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    account = sub.add_parser("create-account")
    account.add_argument("--username", required=True)
    account.add_argument("--role", choices=["employee", "hr"], required=True)
    account.add_argument("--employee-id")
    account.add_argument(
        "--synthetic", action="store_true", help="Create an explicitly synthetic employee for a local demo"
    )
    args = parser.parse_args()
    if args.command == "create-account":
        if not 1 <= len(args.username) <= 80:
            parser.error("username must contain 1..80 characters")
        if args.employee_id is not None and not 1 <= len(args.employee_id) <= 128:
            parser.error("employee-id must contain 1..128 characters")
    db = Database(Settings.from_env())
    db.migrate()
    if args.command == "migrate":
        print("Migrations applied; existing data preserved.")
        return
    if (args.role == "employee") != bool(args.employee_id):
        parser.error("employee requires --employee-id; HR must not have one")
    if args.synthetic and not args.employee_id:
        parser.error("--synthetic requires --employee-id")
    if args.synthetic and not args.employee_id.startswith("SYNTH_"):
        parser.error("synthetic employee IDs must start with SYNTH_")
    password = getpass.getpass("Password (at least 12 characters): ")
    if len(password) < 12 or len(password) > 256:
        parser.error("password must contain 12..256 characters")
    if password != getpass.getpass("Repeat password: "):
        parser.error("passwords do not match")
    try:
        with db.connect() as conn:
            if args.synthetic:
                conn.execute(
                    "INSERT INTO employees(id,display_name) VALUES (?,?) ON CONFLICT(id) DO NOTHING",
                    (args.employee_id, "Synthetic demo employee"),
                )
            conn.execute(
                "INSERT INTO accounts(id,username,password_hash,role,employee_id) VALUES (?,?,?,?,?)",
                (secrets.token_hex(16), args.username, hash_password(password), args.role, args.employee_id),
            )
    except sqlite3.IntegrityError:
        parser.exit(1, "Account already exists or employee is missing; nothing was changed.\n")
    print("Server-managed demo account created.")


if __name__ == "__main__":
    main()
