-- Infrastructure schema only. No organiser data and no demo passwords are seeded.
CREATE TABLE employees (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL
);

CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('employee', 'hr')),
    employee_id TEXT REFERENCES employees(id) ON DELETE RESTRICT,
    CHECK ((role = 'employee' AND employee_id IS NOT NULL)
        OR (role = 'hr' AND employee_id IS NULL))
);

CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    csrf_hash TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX sessions_expires_at_idx ON sessions(expires_at);
CREATE INDEX sessions_account_id_idx ON sessions(account_id);

CREATE TABLE login_attempts (
    bucket TEXT PRIMARY KEY,
    failures INTEGER NOT NULL CHECK (failures >= 0),
    window_start INTEGER NOT NULL,
    blocked_until INTEGER NOT NULL DEFAULT 0
);
