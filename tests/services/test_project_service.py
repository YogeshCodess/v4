"""
Tests for services/project_service.py — the authoritative source of truth
for project + phase lifecycle.

Focus areas:
  - flag_modified() JSON column writes (guards Gotcha #8 / B4)
  - design_scope validation + persistence
  - phase_statuses round-trip including requirements_hash_at_completion
  - downstream phase reset on P1 re-completion
  - conversation_history append semantics
  - reset_state wipes only mutable columns
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Session-wide DB fixture: isolate tests from the real hardware_pipeline.db
# by pointing DATABASE_URL at a per-test tempfile BEFORE any service module
# is imported. Each test gets a fresh engine via `_reset_engines`.
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "ps.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

    # Force a fresh settings + engine per test so env vars take effect.
    import importlib
    import config as _config
    importlib.reload(_config)
    import database.models as _models
    # Swap the settings reference without reloading the module — reload
    # would re-register SQLAlchemy mappers and pollute other test files
    # that import ProjectDB / ComponentCacheDB from the original namespace.
    _models.settings = _config.settings
    _models._engine = None
    _models._SessionLocal = None
    _models._async_engine = None
    _models._AsyncSessionLocal = None
    _models._resolved_db_url = None
    # Reload the services that cache a module-level settings import
    import services.storage as _storage
    importlib.reload(_storage)
    import services.project_service as _ps
    importlib.reload(_ps)
    yield db_path
    # cleanup: dispose engines so file handles release
    try:
        if _models._engine is not None:
            _models._engine.dispose()
    except Exception:
        pass


@pytest.fixture
def svc(tmp_db):
    from services.project_service import ProjectService
    return ProjectService()


# ---------------------------------------------------------------------------
# create + design_scope validation
# ---------------------------------------------------------------------------

def test_create_project_persists_design_scope(svc):
    p = svc.create(name="ScopeTest", design_scope="front-end")
    assert p["design_scope"] == "front-end"
    reloaded = svc.get(p["id"])
    assert reloaded["design_scope"] == "front-end"


def test_create_project_defaults_design_scope_to_full(svc):
    p = svc.create(name="DefaultScope")
    assert p["design_scope"] == "full"


def test_create_project_rejects_invalid_design_scope(svc):
    with pytest.raises(ValueError, match="Invalid design_scope"):
        svc.create(name="Bad", design_scope="kitchen-sink")


def test_create_project_lowercases_and_trims_scope(svc):
    p = svc.create(name="Trimmer", design_scope="  FULL  ")
    assert p["design_scope"] == "full"


# ---------------------------------------------------------------------------
# set_design_scope
# ---------------------------------------------------------------------------

def test_set_design_scope_persists_update(svc):
    p = svc.create(name="Switcher")
    svc.set_design_scope(p["id"], "dsp")
    assert svc.get(p["id"])["design_scope"] == "dsp"


def test_set_design_scope_rejects_invalid(svc):
    p = svc.create(name="RejectTest")
    with pytest.raises(ValueError, match="Invalid design_scope"):
        svc.set_design_scope(p["id"], "bogus")


def test_set_design_scope_raises_for_missing_project(svc):
    with pytest.raises(ValueError, match="not found"):
        svc.set_design_scope(999999, "full")


# ---------------------------------------------------------------------------
# set_phase_status + flag_modified (JSON column mutation tracking)
# ---------------------------------------------------------------------------

def test_set_phase_status_persists_to_json_column(svc):
    """Regression for B4 — without flag_modified, JSON col writes are lost."""
    p = svc.create(name="JsonMut")
    svc.set_phase_status(p["id"], "P1", "in_progress")

    reloaded = svc.get(p["id"])
    assert reloaded["phase_statuses"]["P1"]["status"] == "in_progress"


def test_set_phase_status_overwrites_previous_entry(svc):
    p = svc.create(name="Overwrite")
    svc.set_phase_status(p["id"], "P1", "in_progress")
    svc.set_phase_status(p["id"], "P1", "completed")

    reloaded = svc.get(p["id"])
    assert reloaded["phase_statuses"]["P1"]["status"] == "completed"


def test_p1_completion_advances_current_phase_to_p2(svc):
    p = svc.create(name="Advance")
    assert svc.get(p["id"])["current_phase"] == "P1"
    svc.set_phase_status(p["id"], "P1", "completed")
    assert svc.get(p["id"])["current_phase"] == "P2"


def test_p1_recompletion_resets_downstream_phases_to_pending(svc):
    """When P1 re-completes (user updated requirements), every downstream
    AI phase that had a recorded status must flip back to pending."""
    p = svc.create(name="DownReset")
    # Seed: P1 + P2 + P4 completed
    svc.set_phase_status(p["id"], "P1", "completed")
    svc.set_phase_status(p["id"], "P2", "completed")
    svc.set_phase_status(p["id"], "P4", "completed")

    # Re-complete P1 → downstream phases reset
    svc.set_phase_status(p["id"], "P1", "completed")

    statuses = svc.get(p["id"])["phase_statuses"]
    assert statuses["P1"]["status"] == "completed"
    assert statuses["P2"]["status"] == "pending"
    assert statuses["P4"]["status"] == "pending"


def test_set_phase_status_stamps_requirements_hash_at_completion(svc):
    p = svc.create(name="HashStamp")
    svc.save_requirements_lock(p["id"], {
        "requirements_hash": "abc123",
        "requirements_frozen_at": "2026-04-20T10:00:00Z",
        "requirements_locked_json": "{}",
    })
    svc.set_phase_status(p["id"], "P2", "completed")
    entry = svc.get(p["id"])["phase_statuses"]["P2"]
    assert entry.get("requirements_hash_at_completion") == "abc123"


def test_set_phase_status_ignores_hash_when_none(svc):
    p = svc.create(name="NoHash")
    svc.set_phase_status(p["id"], "P2", "completed")
    entry = svc.get(p["id"])["phase_statuses"]["P2"]
    assert "requirements_hash_at_completion" not in entry


def test_get_phase_status_defaults_to_pending(svc):
    p = svc.create(name="Pending")
    assert svc.get_phase_status(p["id"], "P4") == "pending"


def test_set_phase_status_extra_fields_persist(svc):
    p = svc.create(name="Extra")
    svc.set_phase_status(p["id"], "P4", "completed", extra={"duration_seconds": 12.3})
    entry = svc.get(p["id"])["phase_statuses"]["P4"]
    assert entry["duration_seconds"] == 12.3


# ---------------------------------------------------------------------------
# append_conversation (JSON list column mutation)
# ---------------------------------------------------------------------------

def test_append_conversation_round_trips_history(svc):
    p = svc.create(name="Chat")
    svc.append_conversation(p["id"], "user", "Hello")
    svc.append_conversation(p["id"], "assistant", "Hi there")
    history = svc.get(p["id"])["conversation_history"]
    assert history == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]


def test_append_conversation_merges_design_parameters(svc):
    p = svc.create(name="Params")
    svc.append_conversation(
        p["id"], "assistant", "ok",
        design_parameters={"voltage": "3.3V"},
    )
    svc.append_conversation(
        p["id"], "assistant", "more",
        design_parameters={"current": "100mA"},
    )
    params = svc.get(p["id"])["design_parameters"]
    assert params == {"voltage": "3.3V", "current": "100mA"}


# ---------------------------------------------------------------------------
# reset_state
# ---------------------------------------------------------------------------

def test_reset_state_clears_mutable_columns(svc):
    p = svc.create(name="Reset")
    svc.set_phase_status(p["id"], "P1", "completed")
    svc.append_conversation(p["id"], "user", "hi")
    svc.save_requirements_lock(p["id"], {
        "requirements_hash": "abc123",
        "requirements_frozen_at": "2026-04-20T10:00:00Z",
        "requirements_locked_json": "{}",
    })

    svc.reset_state(p["id"])

    after = svc.get(p["id"])
    assert after["phase_statuses"] == {}
    assert after["conversation_history"] == []
    assert after["design_parameters"] == {}
    assert after["requirements_hash"] is None
    assert after["current_phase"] == "P1"
    # Identity preserved
    assert after["name"] == "Reset"


def test_reset_state_idempotent(svc):
    p = svc.create(name="Idemp")
    svc.reset_state(p["id"])
    svc.reset_state(p["id"])  # must not raise
    after = svc.get(p["id"])
    assert after["phase_statuses"] == {}


# ---------------------------------------------------------------------------
# Stale phase detection
# ---------------------------------------------------------------------------

def test_get_stale_phase_ids_empty_when_no_hash(svc):
    p = svc.create(name="NoStale")
    svc.set_phase_status(p["id"], "P2", "completed")
    assert svc.get_stale_phase_ids(p["id"]) == []


def test_get_stale_phase_ids_flags_outdated_completions(svc):
    p = svc.create(name="Stale")
    # Freeze v1 hash, complete P2 under v1
    svc.save_requirements_lock(p["id"], {
        "requirements_hash": "v1-hash",
        "requirements_frozen_at": "2026-04-20T10:00:00Z",
        "requirements_locked_json": "{}",
    })
    svc.set_phase_status(p["id"], "P2", "completed")

    # Now freeze v2 hash — P2 is now stale
    svc.save_requirements_lock(p["id"], {
        "requirements_hash": "v2-hash",
        "requirements_frozen_at": "2026-04-20T11:00:00Z",
        "requirements_locked_json": "{}",
    })

    stale = svc.get_stale_phase_ids(p["id"])
    assert "P2" in stale
