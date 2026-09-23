CREATE TABLE completion_requests (
    account_id TEXT NOT NULL REFERENCES accounts(id),
    employee_id TEXT NOT NULL REFERENCES employees(id),
    idempotency_key TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    PRIMARY KEY (account_id, employee_id, idempotency_key)
);

CREATE TABLE recommendation_runs (
    recommendation_id TEXT PRIMARY KEY,
    employee_id TEXT NOT NULL REFERENCES employees(id),
    revision INTEGER NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX recommendation_employee_idx ON recommendation_runs(employee_id, created_at);
