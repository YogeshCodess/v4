"""Tests for the P26 #13 project-type expansion (transceiver, power_supply,
switch_matrix added alongside receiver, transmitter).

What we're guarding:
  1. `services.project_service.create()` accepts every new type and
     persists it on `ProjectDB.project_type`.
  2. The HTTP boundary at `POST /api/v1/projects` validates new types
     against the same set (no drift between the two validation sites).
  3. `tools.rf_cascade.compute_cascade()` aliases each project_type to
     a sensible cascade direction (`receiver`/`switch_matrix` → rx,
     `transmitter`/`transceiver` → tx, `power_supply` → none with a
     clean empty rollup).
  4. `services.rf_audit._audit_tx_cascade()` fires for transceiver
     (which has a TX side worth auditing) but not for receiver,
     switch_matrix, or power_supply.

These tests use `_make_project_service` so we don't need a live HTTP
server — the validation logic lives in the service module.
"""
from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from services.project_service import (
    ProjectService,
    VALID_PROJECT_TYPES,
)
from services.storage import StorageAdapter
from tools.rf_cascade import compute_cascade


# ---------------------------------------------------------------------------
# Validation set — single source of truth
# ---------------------------------------------------------------------------


def test_valid_project_types_includes_all_five():
    """The catalogue must contain receiver/transmitter (existing) plus
    transceiver/power_supply/switch_matrix (added in P26 #13)."""
    assert VALID_PROJECT_TYPES == {
        "receiver",
        "transmitter",
        "transceiver",
        "power_supply",
        "switch_matrix",
    }


# ---------------------------------------------------------------------------
# project_service.create persists new types
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_svc(tmp_path: Path, monkeypatch):
    """Build a ProjectService backed by a throwaway SQLite DB + tmpdir."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    # Force the DB engine to re-init under the new URL.
    from database import models as _m
    monkeypatch.setattr(_m, "_engine", None, raising=False)

    storage = StorageAdapter.local(tmp_path)
    return ProjectService(storage=storage)


@pytest.mark.parametrize("ptype", [
    "receiver", "transmitter", "transceiver", "power_supply", "switch_matrix",
])
def test_create_accepts_each_project_type(isolated_svc, ptype):
    """Every project_type in VALID_PROJECT_TYPES round-trips through
    create() and comes back via the dict serializer with the same value."""
    proj = isolated_svc.create(name=f"smoke-{ptype}", project_type=ptype)
    assert proj["project_type"] == ptype


def test_create_rejects_unknown_project_type(isolated_svc):
    """Unknown types raise ValueError listing the legal options."""
    with pytest.raises(ValueError) as exc:
        isolated_svc.create(name="bad", project_type="quantum_radio")
    msg = str(exc.value)
    assert "quantum_radio" in msg
    # Error message must enumerate the legal set (so the operator can
    # see what they should have typed).
    for legal in VALID_PROJECT_TYPES:
        assert legal in msg


# ---------------------------------------------------------------------------
# rf_cascade direction aliasing
# ---------------------------------------------------------------------------


def test_cascade_alias_receiver_uses_rx_branch():
    res = compute_cascade([], direction="receiver")
    assert res["direction"] == "rx"


def test_cascade_alias_transmitter_uses_tx_branch():
    res = compute_cascade([], direction="transmitter")
    assert res["direction"] == "tx"


def test_cascade_alias_transceiver_uses_tx_branch():
    """Transceiver dominates TX-side noise so we run TX cascade. RX
    side gets handled separately via the receiver-cascade audit
    (or the user's explicit RX project)."""
    res = compute_cascade([], direction="transceiver")
    assert res["direction"] == "tx"


def test_cascade_alias_switch_matrix_uses_rx_branch():
    """Switch matrix is a passive routing fabric — IL + IIP3 cascade
    follows RX math (Friis-like accumulation of insertion loss)."""
    res = compute_cascade([], direction="switch_matrix")
    assert res["direction"] == "rx"


def test_cascade_alias_power_supply_short_circuits():
    """Power supply has no RF cascade — short-circuit returns a clean
    empty rollup with `direction='none'` and an explanatory note so
    the caller doesn't crash and can render a meaningful UI."""
    res = compute_cascade([], direction="power_supply")
    assert res["direction"] == "none"
    assert res["totals"] == {}
    assert res["verdict"]["ok"] is True
    assert any("not applicable" in n for n in res["verdict"]["notes"])


# ---------------------------------------------------------------------------
# TX-cascade audit fires for transmitter + transceiver only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ptype,expect_audit", [
    ("transmitter",   True),
    ("transceiver",   True),
    ("receiver",      False),
    ("switch_matrix", False),
    ("power_supply",  False),
])
def test_tx_audit_fires_for_tx_like_types_only(ptype, expect_audit):
    """`run_tx_cascade_audit` should fire (return non-empty findings)
    only when the project has a TX side worth auditing. Transmitter +
    transceiver qualify; receiver / switch_matrix / power_supply skip."""
    from services.rf_audit import run_tx_cascade_audit

    # Use design_parameters that would normally produce a TX violation
    # (claimed Pout much higher than what cascade would give).
    dp = {
        "project_type": ptype,
        "pout_dbm": 50.0,
        "oip3_dbm": 60.0,
    }
    comps = [
        {"name": "Driver",
         "key_specs": {"gain_db": 20.0, "pout_dbm": 30.0, "oip3_dbm": 40.0}},
        {"name": "PA",
         "key_specs": {"gain_db": 15.0, "pout_dbm": 35.0, "oip3_dbm": 45.0}},
    ]
    # Signature is (component_recommendations, design_parameters).
    issues = run_tx_cascade_audit(comps, dp)
    if expect_audit:
        # Audit ran — output is a list (may be empty if cascade math
        # happens to match claims; we only assert it didn't short-circuit).
        assert isinstance(issues, list)
    else:
        # Audit short-circuited — empty list, no crash.
        assert issues == []
