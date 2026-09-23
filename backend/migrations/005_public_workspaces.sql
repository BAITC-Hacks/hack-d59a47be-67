-- Empty in private installs. Public demo stores only random-token digests here.
CREATE TABLE public_workspaces (
    token_hash TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    ai_requests INTEGER NOT NULL DEFAULT 0,
    mutation_requests INTEGER NOT NULL DEFAULT 0,
    import_requests INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE public_ai_budget (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    window_start INTEGER NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0
);
