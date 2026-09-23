-- Source files remain outside the repository/image. Store their validated content.
CREATE TABLE dataset_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    data_version TEXT,
    scenario_date TEXT,
    skills_json TEXT,
    events_json TEXT
);
INSERT INTO dataset_state (id) VALUES (1);

CREATE TABLE employee_profiles (
    employee_id TEXT PRIMARY KEY REFERENCES employees(id) ON DELETE RESTRICT,
    profile_json TEXT NOT NULL
);

CREATE TABLE activity_history (
    record_id TEXT PRIMARY KEY,
    employee_id TEXT NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    event_id TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE INDEX activity_history_employee_idx ON activity_history(employee_id);

CREATE TABLE import_previews (
    token_hash TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    revision INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    summary_json TEXT NOT NULL
);
