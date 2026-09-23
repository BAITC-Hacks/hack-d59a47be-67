import sys

import pytest

from backend.app import cli
from backend.app.auth import verify_password
from backend.app.config import Settings
from backend.app.database import Database


def test_operator_provisioning_and_duplicate_rollback(tmp_path, monkeypatch, capsys):
    path = tmp_path / "synthetic.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "synthetic-only-password")
    args = [
        "cli",
        "create-account",
        "--username",
        "synthetic",
        "--role",
        "employee",
        "--employee-id",
        "SYNTH_A",
        "--synthetic",
    ]
    monkeypatch.setattr(sys, "argv", args)
    cli.main()
    db = Database(Settings(database_path=path))
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM accounts").fetchone()
        assert row["employee_id"] == "SYNTH_A" and row["role"] == "employee"
        assert verify_password("synthetic-only-password", row["password_hash"])
    # A failed duplicate account cannot leave its newly-created profile behind.
    monkeypatch.setattr(sys, "argv", ["SYNTH_B" if arg == "SYNTH_A" else arg for arg in args])
    with pytest.raises(SystemExit):
        cli.main()
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
    captured = capsys.readouterr()
    assert "synthetic-only-password" not in captured.out + captured.err


@pytest.mark.parametrize(
    "extra",
    [
        ["--role", "hr", "--employee-id", "SYNTH_A"],
        ["--role", "employee", "--employee-id", "REAL_1", "--synthetic"],
    ],
)
def test_operator_rejects_invalid_role_or_mislabelled_synthetic(tmp_path, monkeypatch, extra):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "synthetic.sqlite3"))
    monkeypatch.setattr(sys, "argv", ["cli", "create-account", "--username", "synthetic", *extra])
    with pytest.raises(SystemExit):
        cli.main()
