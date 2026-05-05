"""Tests for migrations/__init__.py — idempotency and correctness."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from migrations import apply_all


def _make_projects_table(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT)")
    conn.commit()
    conn.close()


def test_apply_all_adds_lock_columns():
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        _make_projects_table(db)
        result = apply_all(db)
        assert result["001_requirements_lock"] is True

        conn = sqlite3.connect(db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
        conn.close()
        assert "requirements_hash" in cols
        assert "requirements_frozen_at" in cols
        assert "requirements_locked_json" in cols


def test_apply_all_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        _make_projects_table(db)
        apply_all(db)
        # Second run must not raise and must report no changes on 001.
        result = apply_all(db)
        assert result["001_requirements_lock"] is False


def test_apply_all_creates_pipeline_runs_and_llm_calls():
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        _make_projects_table(db)
        apply_all(db)
        conn = sqlite3.connect(db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "pipeline_runs" in tables
        assert "llm_calls" in tables


def test_apply_all_handles_missing_projects_table_gracefully():
    """If projects doesn't exist yet, 001 should no-op and 002 should still run."""
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        # No projects table created.
        result = apply_all(db)
        assert result["001_requirements_lock"] is False
        assert result["002_pipeline_runs_llm_calls"] is True


# ---------------------------------------------------------------------------
# 003 — design_scope column (was previously only tested by presence)
# ---------------------------------------------------------------------------

def test_apply_all_adds_design_scope_column():
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        _make_projects_table(db)
        result = apply_all(db)
        assert result["003_design_scope"] is True

        conn = sqlite3.connect(db)
        try:
            cols = {r[1]: r for r in conn.execute("PRAGMA table_info(projects)")}
            assert "design_scope" in cols
            # NOT NULL + default 'full' (the 4th and 5th PRAGMA fields)
            notnull = cols["design_scope"][3]
            default_val = cols["design_scope"][4]
            assert notnull == 1, "design_scope must be NOT NULL"
            assert default_val == "'full'", (
                f"design_scope default must be 'full', got {default_val!r}"
            )
        finally:
            conn.close()


def test_003_is_idempotent_on_rerun():
    """Running migration 003 twice must report no-change on the second call
    and leave the column intact (not duplicated, not dropped)."""
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        _make_projects_table(db)

        first = apply_all(db)
        second = apply_all(db)
        third = apply_all(db)

        assert first["003_design_scope"] is True
        assert second["003_design_scope"] is False
        assert third["003_design_scope"] is False

        # Column still exists exactly once
        conn = sqlite3.connect(db)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)")]
            assert cols.count("design_scope") == 1
        finally:
            conn.close()


def test_003_noop_when_projects_table_missing():
    """003 needs the projects table — if it's missing, 003 must no-op gracefully,
    not raise."""
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        result = apply_all(db)
        assert result["003_design_scope"] is False


def test_003_backfills_default_full_for_existing_rows():
    """When 003 runs on a populated projects table, existing rows must pick
    up the 'full' default automatically — no NOT NULL violation and no
    manual UPDATE required."""
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO projects (name) VALUES ('legacy-proj-1')")
        conn.execute("INSERT INTO projects (name) VALUES ('legacy-proj-2')")
        conn.commit()
        conn.close()

        result = apply_all(db)
        assert result["003_design_scope"] is True

        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT name, design_scope FROM projects ORDER BY id"
            ).fetchall()
            assert rows == [
                ("legacy-proj-1", "full"),
                ("legacy-proj-2", "full"),
            ]
        finally:
            conn.close()


def test_apply_all_full_pipeline_idempotent():
    """End-to-end: run every migration twice, every 'changed?' flag must flip
    to False on the second run."""
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "t.db")
        _make_projects_table(db)
        first = apply_all(db)
        second = apply_all(db)
        for migration, changed in second.items():
            assert changed is False, f"{migration} not idempotent: {second}"
        # And the first run must have actually done something everywhere.
        assert any(first.values())
