"""
Lightweight idempotent SQLite migrations for Silicon to Software (S2S) V2.

Each migration is a Python function that returns True if it changed the DB.
`apply_all(db_path)` walks them in order on every FastAPI startup; they're
designed so re-running is a no-op.

Bug-fix history:
- 007 (2026-05-01): pipeline_runs.project_id was TEXT NOT NULL; should be
  INTEGER REFERENCES projects(id). Without this, JOINs require CASTs and
  there's no referential integrity. The migration rebuilds the table.
- 006 (2026-05-01): output_dir backfill now uses the canonical
  services.storage.safe_project_dirname slugifier (with a defensive
  fallback) instead of a hand-rolled subset that drifted.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema introspection helpers
# ---------------------------------------------------------------------------


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _column_type(conn: sqlite3.Connection, table: str, column: str) -> str | None:
    """Return the declared type of `table.column` (e.g. 'INTEGER', 'TEXT'),
    or None if the column does not exist. Used to detect whether the
    project_id type-fix migration has run yet."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    for r in rows:
        if r[1] == column:
            return (r[2] or "").upper()
    return None


def _has_foreign_key(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """True iff `table.column` has any FOREIGN KEY constraint declared."""
    rows = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    return any(r[3] == column for r in rows)


# ---------------------------------------------------------------------------
# Slugifier - prefers the canonical services.storage.safe_project_dirname,
# falls back to a stdlib-only implementation when storage isn't importable
# (e.g. unit tests that exercise migrations in isolation).
# ---------------------------------------------------------------------------

_FALLBACK_SLUG_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _slug(name: str) -> str:
    try:
        from services.storage import safe_project_dirname  # type: ignore
        return safe_project_dirname(name)
    except Exception:
        s = _FALLBACK_SLUG_RE.sub("_", name or "").replace(" ", "_").lower().rstrip(". ")
        return s or "project"


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------


def _apply_001(conn: sqlite3.Connection) -> bool:
    changed = False
    if not _table_exists(conn, "projects"):
        return False
    needed = [
        ("requirements_hash", "TEXT"),
        ("requirements_frozen_at", "DATETIME"),
        ("requirements_locked_json", "TEXT"),
    ]
    for col, ddl in needed:
        if not _column_exists(conn, "projects", col):
            conn.execute(f"ALTER TABLE projects ADD COLUMN {col} {ddl}")
            changed = True
    return changed


def _apply_002(conn: sqlite3.Connection) -> bool:
    changed = False
    if not _table_exists(conn, "pipeline_runs"):
        # Bug-fix 007 (2026-05-01) folded into 002 for fresh DBs:
        # project_id is INTEGER (not TEXT) and carries an FK to projects(id).
        conn.execute("""
            CREATE TABLE pipeline_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id),
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
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_runs_project "
            "ON pipeline_runs(project_id, phase_id)"
        )
        changed = True
    if not _table_exists(conn, "llm_calls"):
        conn.execute("""
            CREATE TABLE llm_calls (
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
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_calls_run "
            "ON llm_calls(pipeline_run_id)"
        )
        changed = True
    return changed


def _apply_003(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, "projects"):
        return False
    if _column_exists(conn, "projects", "design_scope"):
        return False
    conn.execute(
        "ALTER TABLE projects ADD COLUMN design_scope TEXT NOT NULL DEFAULT 'full'"
    )
    return True


def _apply_004(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, "projects"):
        return False
    if _column_exists(conn, "projects", "project_type"):
        return False
    conn.execute(
        "ALTER TABLE projects ADD COLUMN project_type TEXT NOT NULL DEFAULT 'receiver'"
    )
    return True


def _apply_005(conn: sqlite3.Connection) -> bool:
    """Normalise phase_statuses entries from string to dict shape."""
    if not _table_exists(conn, "projects"):
        return False
    if not _column_exists(conn, "projects", "phase_statuses"):
        return False
    rows = conn.execute(
        "SELECT id, phase_statuses FROM projects "
        "WHERE phase_statuses IS NOT NULL AND phase_statuses != '' "
        "AND phase_statuses != '{}'"
    ).fetchall()
    n_changed = 0
    for pid, raw in rows:
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        row_changed = False
        for phase_id, val in list(data.items()):
            if isinstance(val, str):
                data[phase_id] = {"status": val}
                row_changed = True
        if row_changed:
            conn.execute(
                "UPDATE projects SET phase_statuses = ? WHERE id = ?",
                (json.dumps(data), pid),
            )
            n_changed += 1
    if n_changed:
        log.info("migration.005 normalised %d project rows", n_changed)
    return n_changed > 0


def _apply_006(conn: sqlite3.Connection) -> bool:
    """Backfill empty output_dir columns on legacy projects.

    Uses the canonical services.storage.safe_project_dirname slugifier so
    the directory layout matches what the live storage adapter would
    create. Falls back to a stdlib-only slugifier if the import fails
    (e.g. unit tests that exercise migrations in isolation).
    """
    if not _table_exists(conn, "projects"):
        return False
    if not _column_exists(conn, "projects", "output_dir"):
        return False
    if not _column_exists(conn, "projects", "name"):
        return False
    rows = conn.execute(
        "SELECT id, name FROM projects "
        "WHERE output_dir IS NULL OR output_dir = ''"
    ).fetchall()
    if not rows:
        return False
    base = "./output"
    for pid, name in rows:
        slug = _slug(str(name or f"project_{pid}")) or f"project_{pid}"
        conn.execute(
            "UPDATE projects SET output_dir = ? WHERE id = ?",
            (f"{base}/{slug}", pid),
        )
    log.info("migration.006 backfilled output_dir on %d project rows", len(rows))
    return True


def _apply_007(conn: sqlite3.Connection) -> bool:
    """Fix pipeline_runs.project_id type (TEXT -> INTEGER) + add FK to projects(id).

    SQLite can't ALTER COLUMN to change a type or add an FK in-place, so
    we do the canonical table-rebuild dance:
        1. Create pipeline_runs_new with the corrected schema
        2. Copy rows, CAST(project_id AS INTEGER)
        3. Drop the old table, rename the new one
        4. Recreate the index

    Idempotent: if pipeline_runs already declares project_id as INTEGER,
    we exit early. Skipped entirely if the table doesn't exist yet
    (fresh DBs go straight through 002 with the corrected schema).
    """
    if not _table_exists(conn, "pipeline_runs"):
        return False
    declared_type = _column_type(conn, "pipeline_runs", "project_id") or ""
    has_fk = _has_foreign_key(conn, "pipeline_runs", "project_id")
    if "INT" in declared_type and has_fk:
        # Already corrected (either by a prior run of 007 or by 002 on a
        # fresh DB).
        return False

    # Disable FK enforcement during the rebuild - we're about to drop the
    # parent of llm_calls. Restore at the end.
    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    if fk_was_on:
        conn.execute("PRAGMA foreign_keys = OFF")

    try:
        conn.execute("""
            CREATE TABLE pipeline_runs_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id),
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
            )
        """)
        # Copy rows. CAST handles the existing TEXT values like "1", "2"
        # cleanly; if any non-numeric project_id leaked in, CAST returns 0
        # which we filter out so we don't insert orphan rows.
        rows = conn.execute(
            "SELECT id, project_id, phase_id, started_at, finished_at, status, "
            "requirements_hash_at_run, model, model_version, total_tokens_in, "
            "total_tokens_out, wall_clock_ms FROM pipeline_runs"
        ).fetchall()
        n_copied = 0
        n_dropped = 0
        for r in rows:
            try:
                pid_int = int(r[1])
            except (TypeError, ValueError):
                n_dropped += 1
                continue
            conn.execute(
                "INSERT INTO pipeline_runs_new "
                "(id, project_id, phase_id, started_at, finished_at, status, "
                " requirements_hash_at_run, model, model_version, "
                " total_tokens_in, total_tokens_out, wall_clock_ms) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (r[0], pid_int, *r[2:]),
            )
            n_copied += 1

        conn.execute("DROP INDEX IF EXISTS idx_pipeline_runs_project")
        conn.execute("DROP TABLE pipeline_runs")
        conn.execute("ALTER TABLE pipeline_runs_new RENAME TO pipeline_runs")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_runs_project "
            "ON pipeline_runs(project_id, phase_id)"
        )
        log.info(
            "migration.007 rebuilt pipeline_runs: %d rows copied, %d dropped "
            "(non-numeric project_id), FK to projects(id) added",
            n_copied, n_dropped,
        )
    finally:
        if fk_was_on:
            conn.execute("PRAGMA foreign_keys = ON")

    return True


_MIGRATIONS = [
    ("001_requirements_lock", _apply_001),
    ("002_pipeline_runs_llm_calls", _apply_002),
    ("003_design_scope", _apply_003),
    ("004_project_type", _apply_004),
    ("005_phase_status_dict_shape", _apply_005),
    ("006_output_dir_backfill", _apply_006),
    ("007_pipeline_runs_project_id_int", _apply_007),
]


def apply_all(db_path: str) -> dict[str, bool]:
    """Apply every pending migration. Returns {name: changed_bool}.

    Safe to call on every FastAPI startup - all migrations are idempotent.
    """
    results: dict[str, bool] = {}
    conn = sqlite3.connect(db_path)
    try:
        for name, fn in _MIGRATIONS:
            results[name] = fn(conn)
        conn.commit()
    finally:
        conn.close()
    return results
