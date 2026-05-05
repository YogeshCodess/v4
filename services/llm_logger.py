"""
LLM call logger — B1.3.

Pure-stdlib SQLite writer; no SQLAlchemy session coupling so it can be invoked
from any async context (inside `BaseAgent.call_llm`) without needing a session
threaded through. Takes the hashes of prompt / response so we never persist the
raw payloads (privacy + size), while still being able to verify byte-level
reproducibility via `scripts/reproduce_run.py` (D2.2).

Usage from an agent:

    from services.llm_logger import log_llm_call, current_run_id

    log_llm_call(
        pipeline_run_id=current_run_id(),
        model=fallback_model,
        model_version=None,
        prompt=canonical_prompt,
        response=content_text,
        tokens_in=usage["input_tokens"],
        tokens_out=usage["output_tokens"],
        latency_ms=elapsed_ms,
        tool_calls=tool_calls,
        temperature=0.0,
    )

`current_run_id` is a contextvar set by `pipeline_run_context(...)`; if nothing
has set it the row is still inserted with pipeline_run_id=NULL so we never drop
a call.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger(__name__)

# ContextVar so async tasks can thread a pipeline_run_id through without
# arg plumbing (copied automatically across `await` boundaries).
_PIPELINE_RUN_ID: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "pipeline_run_id", default=None
)

_DB_WRITE_LOCK = threading.Lock()


def current_run_id() -> Optional[int]:
    return _PIPELINE_RUN_ID.get()


@contextmanager
def pipeline_run_context(run_id: int) -> Iterator[int]:
    """Scope `current_run_id()` inside this block (async-safe via contextvars)."""
    token = _PIPELINE_RUN_ID.set(run_id)
    try:
        yield run_id
    finally:
        _PIPELINE_RUN_ID.reset(token)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _resolve_db_path() -> Optional[str]:
    """Pull sqlite path out of settings.database_url; return None for non-sqlite."""
    try:
        from config import settings  # local import to avoid circular
    except Exception:
        return None
    url = getattr(settings, "database_url", "")
    if not url.startswith("sqlite:///"):
        return None
    p = url[len("sqlite:///"):]
    if p.startswith("./"):
        import os
        p = os.path.join(os.getcwd(), p[2:])
    return p


def start_pipeline_run(
    project_id: str,
    phase_id: str,
    requirements_hash: Optional[str] = None,
    model: Optional[str] = None,
    model_version: Optional[str] = None,
) -> Optional[int]:
    """Insert a new `pipeline_runs` row and return its id (or None on failure)."""
    db = _resolve_db_path()
    if not db:
        return None
    try:
        with _DB_WRITE_LOCK:
            conn = sqlite3.connect(db, timeout=5.0)
            conn.execute("PRAGMA busy_timeout=3000")
            cur = conn.execute(
                "INSERT INTO pipeline_runs "
                "(project_id, phase_id, started_at, status, "
                " requirements_hash_at_run, model, model_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(project_id), phase_id, datetime.utcnow().isoformat(),
                    "running", requirements_hash, model, model_version,
                ),
            )
            run_id = cur.lastrowid
            conn.commit()
            conn.close()
            return run_id
    except sqlite3.Error as e:
        log.debug("llm_logger.start_run_failed: %s", e)
        return None


def finish_pipeline_run(
    run_id: int,
    status: str = "completed",
    wall_clock_ms: Optional[int] = None,
    total_tokens_in: Optional[int] = None,
    total_tokens_out: Optional[int] = None,
) -> None:
    if run_id is None:
        return
    db = _resolve_db_path()
    if not db:
        return
    try:
        with _DB_WRITE_LOCK:
            conn = sqlite3.connect(db, timeout=5.0)
            conn.execute("PRAGMA busy_timeout=3000")
            conn.execute(
                "UPDATE pipeline_runs SET "
                "finished_at = ?, status = ?, wall_clock_ms = ?, "
                "total_tokens_in = COALESCE(?, total_tokens_in), "
                "total_tokens_out = COALESCE(?, total_tokens_out) "
                "WHERE id = ?",
                (
                    datetime.utcnow().isoformat(), status, wall_clock_ms,
                    total_tokens_in, total_tokens_out, run_id,
                ),
            )
            conn.commit()
            conn.close()
    except sqlite3.Error as e:
        log.debug("llm_logger.finish_run_failed: %s", e)


def log_llm_call(
    pipeline_run_id: Optional[int],
    model: str,
    prompt: str,
    response: str,
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
    latency_ms: Optional[int] = None,
    tool_calls: Optional[list] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    model_version: Optional[str] = None,
) -> Optional[int]:
    """Write a single llm_calls row. Returns the row id or None if skipped."""
    db = _resolve_db_path()
    if not db:
        return None
    try:
        with _DB_WRITE_LOCK:
            conn = sqlite3.connect(db, timeout=5.0)
            conn.execute("PRAGMA busy_timeout=3000")
            cur = conn.execute(
                "INSERT INTO llm_calls "
                "(pipeline_run_id, timestamp, model, model_version, "
                " temperature, top_p, prompt_sha256, response_sha256, "
                " tokens_in, tokens_out, latency_ms, tool_calls_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pipeline_run_id, datetime.utcnow().isoformat(),
                    model, model_version,
                    temperature, top_p,
                    _sha256(prompt) if prompt is not None else None,
                    _sha256(response) if response is not None else None,
                    tokens_in, tokens_out, latency_ms,
                    json.dumps(tool_calls) if tool_calls else None,
                ),
            )
            row_id = cur.lastrowid
            conn.commit()
            conn.close()
            return row_id
    except sqlite3.Error as e:
        log.debug("llm_logger.log_call_failed: %s", e)
        return None


def canonical_prompt(messages: list[dict], system: str = "") -> str:
    """Build a stable string for hashing the prompt (messages + system)."""
    try:
        payload = {"system": system or "", "messages": messages}
        return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, default=str)
    except Exception:
        return (system or "") + "\n" + str(messages)


def now_ms() -> int:
    return int(time.time() * 1000)


# Expose a tiny read-API used by scripts/reproduce_run.py.
def list_runs_for_project(project_id: str) -> list[dict[str, Any]]:
    db = _resolve_db_path()
    if not db or not Path(db).exists():
        return []
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM pipeline_runs WHERE project_id = ? ORDER BY id",
            (str(project_id),),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
