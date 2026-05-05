"""Tests for services/project_reset.py — E1 Judge-mode wipe-state helper.

These are pure-Python unit tests: no SQLAlchemy, no FastAPI, no DB. The
DB-touching `ProjectService.reset_state()` is validated against the same
contract by using these helpers as its source of truth.
"""
from __future__ import annotations

import pytest

from services.project_reset import (
    IDENTITY_COLUMNS,
    RESETTABLE_COLUMNS,
    reset_payload,
    summarise_reset,
)


def _full_row() -> dict:
    """A project row populated with data that must survive / not survive."""
    return {
        # identity — must be preserved
        "id": 7,
        "name": "x-band tt&c demo",
        "description": "SCR-720 rehearsal",
        "design_type": "rf",
        "output_dir": "/tmp/outputs/7",
        "created_at": "2026-04-01T10:00:00",
        # mutable — must be cleared
        "phase_statuses": {
            "P1": {"status": "completed",
                   "requirements_hash_at_completion": "abc123"},
            "P2": {"status": "completed",
                   "requirements_hash_at_completion": "abc123"},
        },
        "conversation_history": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
        "design_parameters": {"nf_db": 3.0},
        "requirements_hash": "abc123",
        "requirements_frozen_at": "2026-04-01T11:00:00",
        "requirements_locked_json": '{"frozen": true}',
        "current_phase": "P4",
    }


# ── reset_payload ────────────────────────────────────────────────────────────

def test_reset_payload_clears_all_mutable_columns():
    out = reset_payload(_full_row())
    assert out["phase_statuses"] == {}
    assert out["conversation_history"] == []
    assert out["design_parameters"] == {}
    assert out["requirements_hash"] is None
    assert out["requirements_frozen_at"] is None
    assert out["requirements_locked_json"] is None


def test_reset_payload_preserves_identity_columns():
    row = _full_row()
    out = reset_payload(row)
    for col in IDENTITY_COLUMNS:
        assert out[col] == row[col], f"identity column {col} was mutated"


def test_reset_payload_resets_current_phase_to_p1():
    out = reset_payload(_full_row())
    assert out["current_phase"] == "P1"


def test_reset_payload_is_pure_does_not_mutate_input():
    row = _full_row()
    snapshot = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                for k, v in row.items()}
    reset_payload(row)
    assert row == snapshot, "reset_payload mutated its input"


def test_reset_payload_is_idempotent_on_empty_row():
    empty = {"id": 1, "name": "x", "phase_statuses": {},
             "conversation_history": [], "design_parameters": {},
             "requirements_hash": None, "requirements_frozen_at": None,
             "requirements_locked_json": None, "current_phase": "P1"}
    assert reset_payload(empty) == reset_payload(empty)


def test_reset_payload_passes_through_unknown_keys():
    row = {"id": 1, "name": "x", "my_extra_column": 42}
    out = reset_payload(row)
    assert out["my_extra_column"] == 42


def test_reset_payload_rejects_none():
    with pytest.raises(ValueError):
        reset_payload(None)  # type: ignore[arg-type]


def test_resettable_and_identity_columns_are_disjoint():
    assert not set(RESETTABLE_COLUMNS) & set(IDENTITY_COLUMNS)


# ── summarise_reset ──────────────────────────────────────────────────────────

def test_summarise_populated_project_reports_non_empty():
    before = _full_row()
    after = {"current_phase": "P1"}
    s = summarise_reset(before, after)
    assert s["was_non_empty"] is True
    assert s["counts"]["phase_statuses"] == 2
    assert s["counts"]["conversation_history"] == 2
    assert s["counts"]["design_parameters"] == 1
    assert s["cleared_columns"] == list(RESETTABLE_COLUMNS)
    assert s["current_phase"] == "P1"


def test_summarise_empty_project_reports_empty():
    before = {"phase_statuses": {}, "conversation_history": [],
              "design_parameters": {}, "requirements_hash": None}
    after = {"current_phase": "P1"}
    s = summarise_reset(before, after)
    assert s["was_non_empty"] is False
    assert s["counts"] == {"phase_statuses": 0, "conversation_history": 0,
                           "design_parameters": 0}


def test_summarise_only_lock_counts_as_non_empty():
    """A project that has a frozen lock but nothing else is still non-empty."""
    before = {"phase_statuses": {}, "conversation_history": [],
              "design_parameters": {}, "requirements_hash": "abc123"}
    after = {"current_phase": "P1"}
    s = summarise_reset(before, after)
    assert s["was_non_empty"] is True
