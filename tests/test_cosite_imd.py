"""Tests for agents.red_team_audit.check_cosite_imd — B2.4."""
from __future__ import annotations

from agents.red_team_audit import audit, check_cosite_imd


def test_in_band_imd_flagged():
    """Two UHF aggressors at 420 and 410 MHz produce 2*420-410 = 430 MHz.

    If the receiver sits at 425-435 MHz, 430 MHz is IN the band.
    """
    issues = check_cosite_imd(
        freq_range_mhz=(425.0, 435.0),
        cosite_emitters_mhz=[420.0, 410.0],
        receiver_iip3_dbm=0.0,
        antenna_isolation_db=30.0,
    )
    assert issues, "In-band IMD3 product must be detected"
    assert any("430" in i.location or "430" in i.detail for i in issues)
    assert all(i.category == "cosite_imd" for i in issues)


def test_out_of_band_imd_not_flagged():
    """Widely-separated emitters produce no products in the receiver band."""
    issues = check_cosite_imd(
        freq_range_mhz=(2400.0, 2500.0),
        cosite_emitters_mhz=[100.0, 200.0],
        receiver_iip3_dbm=10.0,
        antenna_isolation_db=40.0,
    )
    assert issues == []


def test_imd_power_heuristic_raises_severity():
    """Low antenna isolation + high Tx power => blocker-class (critical)."""
    issues = check_cosite_imd(
        freq_range_mhz=(425.0, 435.0),
        cosite_emitters_mhz=[420.0, 410.0],
        receiver_iip3_dbm=-10.0,
        antenna_isolation_db=10.0,     # poor isolation
        emitter_power_dbm=40.0,         # 10 W aggressor
    )
    assert issues
    severities = {i.severity for i in issues}
    assert "critical" in severities


def test_audit_surfaces_cosite_issue_and_blocks_pass():
    """Audit with a cosite_context containing an in-band IMD3 should fail overall_pass."""
    good_bom = [
        {"name": "LNA", "gain_db": 24.0, "nf_db": 1.8, "iip3_dbm": 30.0,
         "p1db_dbm": 18.0, "kind": "LNA"},
        {"name": "Filter", "gain_db": -2.0, "nf_db": 2.0, "kind": "filter"},
    ]
    rep = audit(
        phase_id="P1",
        bom_stages=good_bom,
        claimed_cascade={},
        citations=[],
        claimed_parts=[],
        cosite_context={
            "freq_range_mhz": (425.0, 435.0),
            "cosite_emitters_mhz": [420.0, 410.0],
            "receiver_iip3_dbm": 0.0,
            "antenna_isolation_db": 20.0,
            "emitter_power_dbm": 40.0,
        },
    )
    assert rep.overall_pass is False
    cosite_issues = [i for i in rep.issues if i.category == "cosite_imd"]
    assert cosite_issues, "Expected at least one cosite_imd issue on the report"


def test_single_emitter_no_issue():
    """Only one emitter => no pair, no product."""
    issues = check_cosite_imd(
        freq_range_mhz=(425.0, 435.0),
        cosite_emitters_mhz=[430.0],
        receiver_iip3_dbm=0.0,
        antenna_isolation_db=30.0,
    )
    assert issues == []
