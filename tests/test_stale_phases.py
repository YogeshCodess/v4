"""Tests for services.project_service.compute_stale_phase_ids — A2.1."""
from __future__ import annotations

from services.project_service import compute_stale_phase_ids


def test_no_hash_means_no_stale():
    """If the project has never been locked, nothing is stale."""
    statuses = {
        "P2": {"status": "completed",
               "requirements_hash_at_completion": "abc123"},
    }
    assert compute_stale_phase_ids(statuses, current_hash=None) == []


def test_pending_phase_not_stale():
    statuses = {
        "P2": {"status": "pending"},
    }
    assert compute_stale_phase_ids(statuses, current_hash="abc123") == []


def test_completed_phase_with_same_hash_not_stale():
    statuses = {
        "P2": {"status": "completed",
               "requirements_hash_at_completion": "abc123"},
        "P3": {"status": "completed",
               "requirements_hash_at_completion": "abc123"},
    }
    assert compute_stale_phase_ids(statuses, current_hash="abc123") == []


def test_completed_phase_with_old_hash_is_stale():
    statuses = {
        "P2": {"status": "completed",
               "requirements_hash_at_completion": "old111"},
        "P3": {"status": "completed",
               "requirements_hash_at_completion": "abc123"},
        "P4": {"status": "completed",
               "requirements_hash_at_completion": "old111"},
    }
    stale = compute_stale_phase_ids(statuses, current_hash="abc123")
    assert set(stale) == {"P2", "P4"}
    # Preserves downstream canonical order
    assert stale == ["P2", "P4"]


def test_missing_hash_on_completed_is_not_stale():
    """If a phase was completed BEFORE the lock-stamping feature shipped, it
    has no `requirements_hash_at_completion`. We treat it as 'unknown', not
    'stale', to avoid a false-positive rerun storm on upgrade."""
    statuses = {"P2": {"status": "completed"}}
    assert compute_stale_phase_ids(statuses, current_hash="abc123") == []


def test_p1_is_not_tracked_as_stale():
    """P1 owns the lock itself — it's never flagged stale against its own hash."""
    statuses = {
        "P1": {"status": "completed",
               "requirements_hash_at_completion": "old111"},
    }
    assert compute_stale_phase_ids(statuses, current_hash="abc123") == []


def test_all_downstream_phases_considered():
    """Make sure every downstream AI phase is in the watchlist. P7 is
    included alongside P7a — FPGA RTL is automated now and resets with
    the rest when the P1 lock is re-frozen."""
    statuses = {
        pid: {"status": "completed", "requirements_hash_at_completion": "old"}
        for pid in ("P2", "P3", "P4", "P6", "P7", "P7a", "P8a", "P8b", "P8c")
    }
    stale = compute_stale_phase_ids(statuses, current_hash="new")
    assert set(stale) == {"P2", "P3", "P4", "P6", "P7", "P7a", "P8a", "P8b", "P8c"}
