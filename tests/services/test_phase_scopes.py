"""
Tests for services/phase_scopes.py — the backend-authoritative applicability
table for (phase_id × design_scope).

Per v23 policy (2026-04-20), the scope is advisory only: every phase applies
to every scope. These tests lock that contract in place so a future
regression can't silently start gating phases again without an intentional
code change.
"""
from __future__ import annotations

import pytest

from services.phase_scopes import (
    PHASE_APPLICABLE_SCOPES,
    is_phase_applicable,
)


ALL_PHASE_IDS = [
    "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P7a", "P8a", "P8b", "P8c",
]
ALL_SCOPES = ["full", "front-end", "downconversion", "dsp"]


# ---------------------------------------------------------------------------
# Table shape
# ---------------------------------------------------------------------------

def test_phase_applicable_scopes_covers_all_11_phases():
    assert set(PHASE_APPLICABLE_SCOPES.keys()) == set(ALL_PHASE_IDS)


def test_every_phase_is_applicable_to_every_scope_v23_policy():
    """v23: scope is advisory — every phase runs under every scope."""
    for phase_id in ALL_PHASE_IDS:
        for scope in ALL_SCOPES:
            assert is_phase_applicable(phase_id, scope), (
                f"{phase_id} × {scope} must be applicable under v23 policy"
            )


# ---------------------------------------------------------------------------
# is_phase_applicable behaviour
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phase_id", ALL_PHASE_IDS)
def test_is_phase_applicable_true_for_full_scope(phase_id: str):
    assert is_phase_applicable(phase_id, "full")


@pytest.mark.parametrize("scope", ALL_SCOPES)
def test_is_phase_applicable_true_for_p1_across_all_scopes(scope: str):
    assert is_phase_applicable("P1", scope)


def test_is_phase_applicable_fails_open_for_unknown_phase():
    """Unknown phase IDs should be treated as applicable (fail-open) so a
    newly-added phase isn't silently rejected before the table is updated."""
    assert is_phase_applicable("P99", "full") is True
    assert is_phase_applicable("nonsense", "front-end") is True


def test_is_phase_applicable_rejects_unknown_scope_for_known_phase():
    """Unknown scope on a known phase → False (scope must be a valid option)."""
    assert is_phase_applicable("P1", "invalid-scope") is False
    assert is_phase_applicable("P8c", "") is False


# ---------------------------------------------------------------------------
# Regression: synchrony with the model layer (VALID_DESIGN_SCOPES)
# ---------------------------------------------------------------------------

def test_scope_table_values_match_valid_design_scopes():
    """phase_scopes's scope alphabet must match the service's allowed list,
    else scope validation at creation and gate checks drift apart."""
    from services.project_service import VALID_DESIGN_SCOPES
    # Every declared scope in the table must be a valid design scope.
    for phase_id, scopes in PHASE_APPLICABLE_SCOPES.items():
        drift = scopes - VALID_DESIGN_SCOPES
        assert not drift, (
            f"{phase_id} references scopes not in VALID_DESIGN_SCOPES: {drift}"
        )
