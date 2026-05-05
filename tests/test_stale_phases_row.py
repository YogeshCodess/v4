"""Tests for services/stale_phases.py — A2.1 / A2.2 row-oriented API.

Distinct from tests/test_stale_phases.py, which covers the older
project_service.compute_stale_phase_ids() predicate. This module covers the
row-based helpers that FastAPI and batch scripts consume.
"""
from __future__ import annotations

from services.stale_phases import (
    AI_PHASES,
    MANUAL_PHASES,
    phase_status_summary,
    rerun_plan,
    stale_phase_ids,
)


def _project(
    *,
    requirements_hash: str | None = "hash-v1",
    phase_statuses: dict | None = None,
) -> dict:
    return {
        "id": 1,
        "name": "test",
        "requirements_hash": requirements_hash,
        "phase_statuses": phase_statuses or {},
    }


def _completed(hash_at: str | None) -> dict:
    entry = {"status": "completed"}
    if hash_at is not None:
        entry["requirements_hash_at_completion"] = hash_at
    return entry


def test_no_lock_no_stale_phases():
    row = _project(requirements_hash=None, phase_statuses={
        "P1": _completed(None),
        "P2": _completed(None),
    })
    assert stale_phase_ids(row) == []


def test_fresh_phases_not_stale():
    row = _project(phase_statuses={
        "P1": _completed("hash-v1"),
        "P2": _completed("hash-v1"),
    })
    assert stale_phase_ids(row) == []


def test_phase_with_old_hash_is_stale():
    row = _project(phase_statuses={
        "P1": _completed("hash-v1"),
        "P2": _completed("old-hash"),
        "P4": _completed("old-hash"),
    })
    assert stale_phase_ids(row) == ["P2", "P4"]


def test_completed_without_hash_is_stale_once_lock_exists():
    row = _project(phase_statuses={
        "P1": _completed("hash-v1"),
        "P2": _completed(None),           # legacy completion
    })
    assert stale_phase_ids(row) == ["P2"]


def test_pending_and_inprogress_phases_ignored():
    row = _project(phase_statuses={
        "P1": _completed("hash-v1"),
        "P2": {"status": "pending"},
        "P3": {"status": "in_progress"},
        "P4": {"status": "failed"},
    })
    assert stale_phase_ids(row) == []


def test_manual_phases_excluded_by_default():
    # P5 (PCB layout) is the only manual phase left — P7 was promoted to
    # an automated AI phase and must now surface as stale under the lock.
    row = _project(phase_statuses={
        "P5": _completed("old-hash"),
    })
    assert stale_phase_ids(row) == []


def test_p7_is_now_an_ai_phase_not_manual():
    # Regression guard: P7 was moved out of MANUAL_PHASES when FpgaAgent
    # started running under PipelineService.AUTO_PHASES. A stale P7
    # completion hash must surface in `stale_phase_ids`.
    row = _project(phase_statuses={
        "P7": _completed("old-hash"),
        "P7a": _completed("old-hash"),
    })
    stale = stale_phase_ids(row)
    assert "P7" in stale
    assert "P7a" in stale


def test_manual_phases_included_when_requested():
    row = _project(phase_statuses={
        "P4": _completed("old-hash"),
        "P5": _completed("old-hash"),
    })
    out = stale_phase_ids(row, include_manual=True)
    assert "P4" in out
    assert "P5" in out
    assert out.index("P5") > out.index("P4")


def test_phase_order_is_canonical():
    row = _project(phase_statuses={
        "P8c": _completed("old-hash"),
        "P2": _completed("old-hash"),
        "P4": _completed("old-hash"),
    })
    assert stale_phase_ids(row) == ["P2", "P4", "P8c"]


def test_rerun_plan_empty_when_no_stale():
    row = _project(phase_statuses={
        "P1": _completed("hash-v1"),
        "P2": _completed("hash-v1"),
    })
    plan = rerun_plan(row)
    assert plan["stale"] == []
    assert plan["order"] == []
    assert plan["blocked_by_manual"] == []
    assert "No stale" in plan["summary"]


def test_rerun_plan_lists_order_and_summary():
    row = _project(phase_statuses={
        "P3": _completed("old-hash"),
        "P2": _completed("old-hash"),
    })
    plan = rerun_plan(row)
    assert plan["stale"] == ["P2", "P3"]
    assert plan["order"] == ["P2", "P3"]
    assert "P2 -> P3" in plan["summary"]


def test_rerun_plan_flags_manual_rework_when_upstream_stale():
    row = _project(phase_statuses={
        "P4": _completed("old-hash"),
        "P5": _completed("old-hash"),
        "P6": _completed("hash-v1"),
    })
    plan = rerun_plan(row, include_manual=True)
    assert "P4" in plan["stale"]
    assert "P5" in plan["order"]
    assert "P5" in plan["blocked_by_manual"]
    assert "manual rework" in plan["summary"]


def test_phase_status_summary_labels():
    row = _project(phase_statuses={
        "P1": _completed("hash-v1"),
        "P2": _completed("old-hash"),
        "P3": {"status": "in_progress"},
        "P4": {"status": "failed"},
    })
    s = phase_status_summary(row)
    assert s["P1"] == "fresh"
    assert s["P2"] == "stale"
    assert s["P3"] == "in_progress"
    assert s["P4"] == "failed"
    assert s["P6"] == "pending"
    assert s["P5"] == "manual"
    # P7 is an AI phase now — with no entry in phase_statuses it defaults
    # to pending like any other uncompleted AI phase.
    assert s["P7"] == "pending"
    assert s["P7a"] == "pending"


def test_ai_phases_list_excludes_manual():
    for m in MANUAL_PHASES:
        assert m not in AI_PHASES


def test_accepts_object_with_attributes_not_just_dict():
    class FakeRow:
        requirements_hash = "hash-v1"
        phase_statuses = {
            "P1": {"status": "completed",
                   "requirements_hash_at_completion": "hash-v1"},
            "P2": {"status": "completed",
                   "requirements_hash_at_completion": "old"},
        }

    assert stale_phase_ids(FakeRow) == ["P2"]


def test_legacy_bare_string_phase_status():
    row = _project(phase_statuses={
        "P1": "completed",
    })
    assert stale_phase_ids(row) == ["P1"]
