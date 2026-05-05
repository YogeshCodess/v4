"""Tests for services/llm_logger.py — B1.3.

These tests drive the pure-SQLite writer with a tmpdir database URL so we
never touch the real project DB. We check:

  1. start_pipeline_run + finish_pipeline_run write a complete row.
  2. log_llm_call inserts SHA256 prompt/response hashes.
  3. pipeline_run_context propagates current_run_id() across async awaits.
  4. Absent-run-id calls do not raise and insert with run_id NULL.
"""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

import pytest

from migrations import apply_all


@pytest.fixture
def temp_db(monkeypatch):
    """Point the llm_logger at a disposable SQLite file that has the
    pipeline_runs / llm_calls tables created via migrations.
    """
    tmpdir = tempfile.mkdtemp()
    db_path = str(Path(tmpdir) / "t.db")
    # Create the tables via our migration helper
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    apply_all(db_path)

    # Patch services.llm_logger._resolve_db_path to return our tmp db
    import services.llm_logger as ll
    monkeypatch.setattr(ll, "_resolve_db_path", lambda: db_path)
    yield db_path


def test_start_and_finish_pipeline_run(temp_db):
    from services.llm_logger import start_pipeline_run, finish_pipeline_run
    rid = start_pipeline_run(
        project_id="proj-1", phase_id="P1",
        requirements_hash="deadbeef", model="claude-haiku",
    )
    assert rid is not None
    finish_pipeline_run(rid, status="completed", wall_clock_ms=1234,
                        total_tokens_in=100, total_tokens_out=500)
    conn = sqlite3.connect(temp_db)
    row = conn.execute(
        "SELECT project_id, phase_id, status, wall_clock_ms, requirements_hash_at_run "
        "FROM pipeline_runs WHERE id=?", (rid,)).fetchone()
    conn.close()
    assert row == ("proj-1", "P1", "completed", 1234, "deadbeef")


def test_log_llm_call_hashes_prompt_and_response(temp_db):
    from services.llm_logger import log_llm_call
    row_id = log_llm_call(
        pipeline_run_id=None,
        model="claude-haiku",
        prompt="hello",
        response="world",
        tokens_in=1, tokens_out=2, latency_ms=42,
        temperature=0.0,
    )
    assert row_id is not None
    conn = sqlite3.connect(temp_db)
    r = conn.execute(
        "SELECT model, prompt_sha256, response_sha256, tokens_in, tokens_out, latency_ms "
        "FROM llm_calls WHERE id=?", (row_id,)).fetchone()
    conn.close()
    model, p_hash, r_hash, ti, to, lm = r
    assert model == "claude-haiku"
    # SHA-256 of "hello" is a known constant
    assert p_hash == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert r_hash and len(r_hash) == 64 and r_hash != p_hash
    assert (ti, to, lm) == (1, 2, 42)


def test_pipeline_run_context_threads_run_id(temp_db):
    from services.llm_logger import pipeline_run_context, current_run_id

    async def inner():
        return current_run_id()

    async def runner():
        with pipeline_run_context(99):
            got = await inner()
            return got

    got = asyncio.run(runner())
    assert got == 99


def test_log_call_tolerates_missing_run_id(temp_db):
    from services.llm_logger import log_llm_call
    row_id = log_llm_call(
        pipeline_run_id=None, model="glm-4.7",
        prompt="p", response="r",
    )
    assert row_id is not None
    conn = sqlite3.connect(temp_db)
    run = conn.execute("SELECT pipeline_run_id FROM llm_calls WHERE id=?",
                       (row_id,)).fetchone()[0]
    conn.close()
    assert run is None
