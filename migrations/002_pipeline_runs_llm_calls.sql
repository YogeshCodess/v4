-- 002_pipeline_runs_llm_calls.sql
-- Creates reproducibility-harness tables. Idempotent application is handled by
-- migrations/__init__.py:_apply_002.

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    phase_id TEXT NOT NULL,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME,
    status TEXT NOT NULL DEFAULT 'running',
    requirements_hash_at_run TEXT,
    model TEXT,
    model_version TEXT,
    total_tokens_in INTEGER DEFAULT 0,
    total_tokens_out INTEGER DEFAULT 0,
    wall_clock_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_project
    ON pipeline_runs(project_id, phase_id);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    model TEXT NOT NULL,
    model_version TEXT,
    temperature REAL,
    top_p REAL,
    prompt_sha256 TEXT,
    response_sha256 TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    latency_ms INTEGER,
    tool_calls_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_run
    ON llm_calls(pipeline_run_id);
