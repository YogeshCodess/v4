"""Tests for tools/phase_noise_validator.py — P2.8."""
from __future__ import annotations

import pytest

from tools.phase_noise_validator import validate_phase_noise


def _pll(pn: str, phase_noise: float | None, *, cat: str = "RF-PLL"):
    c = {"part_number": pn, "category": cat}
    if phase_noise is not None:
        c["key_specs"] = {"phase_noise_dbchz": phase_noise}
    return c


def _lna(pn: str = "HMC8410"):
    """Non-LO component, should be ignored by the validator."""
    return {"part_number": pn, "category": "RF-LNA",
            "key_specs": {"nf_db": 1.4}}


# ---------------------------------------------------------------------------
# Happy paths — no issues raised
# ---------------------------------------------------------------------------

def test_no_claim_returns_no_issues():
    assert validate_phase_noise(None, components=[_pll("LMX2594", -150)]) == []


def test_no_components_returns_no_issues():
    assert validate_phase_noise(-140, components=[]) == []


def test_no_lo_components_returns_no_issues():
    """BOM has LNAs + mixer but no PLL — can't check, no issue."""
    issues = validate_phase_noise(
        -140, components=[_lna(), _lna("ADL8107")],
    )
    assert issues == []


def test_lo_meets_claim_with_margin():
    """Claim -120 dBc/Hz, LO is -145 dBc/Hz → 25 dB better than claim
    → passes the 3 dB margin check."""
    issues = validate_phase_noise(
        -120, components=[_pll("LMX2594", -145)],
    )
    assert issues == []


# ---------------------------------------------------------------------------
# Violations
# ---------------------------------------------------------------------------

def test_lo_worse_than_claim_raises_high():
    """The inverted case the RF review flagged: claim says -140 but
    the selected LO is only -115 → 25 dB worse than the claim."""
    issues = validate_phase_noise(
        -140, components=[_pll("LMX2594", -115)],
    )
    assert len(issues) == 1
    assert issues[0]["severity"] == "high"
    assert issues[0]["category"] == "phase_noise_budget"
    assert "LMX2594" in issues[0]["detail"]
    assert "-115" in issues[0]["detail"]
    assert "-140" in issues[0]["detail"]


def test_lo_within_margin_still_fails():
    """Claim -140, LO -142 → LO is only 2 dB better than claim, but
    default margin requires 3 dB headroom → still fails."""
    issues = validate_phase_noise(
        -140, components=[_pll("LMX2594", -142)],
    )
    assert any(i["category"] == "phase_noise_budget" for i in issues)


def test_custom_margin_accepted():
    """Same cascade, margin relaxed to 1 dB → 2 dB headroom now
    satisfies the check, no issue raised."""
    issues = validate_phase_noise(
        -140, components=[_pll("LMX2594", -142)], margin_db=1.0,
    )
    assert issues == []


def test_worst_lo_dominates_when_multiple_present():
    """Design with two PLLs — the worse one (higher / less negative)
    sets the budget."""
    issues = validate_phase_noise(
        -140,
        components=[
            _pll("ADF4371", -148),        # better
            _pll("BAD-PART", -120),       # worse — should dominate
        ],
    )
    assert len(issues) == 1
    assert "BAD-PART" in issues[0]["detail"]


# ---------------------------------------------------------------------------
# Unknown / missing-spec handling
# ---------------------------------------------------------------------------

def test_lo_without_phase_noise_spec_raises_medium():
    """A PLL entry with no phase-noise key should raise a warning, not
    be silently ignored."""
    issues = validate_phase_noise(
        -140, components=[_pll("LMX2594", None)],
    )
    assert any(
        i["severity"] == "medium"
        and i["category"] == "phase_noise_unknown"
        for i in issues
    )


def test_lo_detected_by_part_number_prefix():
    """Even without a `category` tag, `LMX*` / `ADF*` prefixes count as
    LO candidates. Missing spec → phase_noise_unknown."""
    issues = validate_phase_noise(
        -130,
        components=[{"part_number": "LMX2820", "key_specs": {}}],
    )
    assert any(i["category"] == "phase_noise_unknown" for i in issues)


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

def test_accepts_string_phase_noise_value():
    """Spec values sometimes ship as '-115 dBc/Hz' strings."""
    issues = validate_phase_noise(
        -140,
        components=[{
            "part_number": "LMX2594", "category": "RF-PLL",
            "key_specs": {"phase_noise_dbchz": "-115 dBc/Hz"},
        }],
    )
    assert any(i["category"] == "phase_noise_budget" for i in issues)


def test_accepts_nonfloat_claim_without_crash():
    """Garbage in the claim field must not raise."""
    assert validate_phase_noise(
        "nonsense",  # type: ignore[arg-type]
        components=[_pll("LMX2594", -145)],
    ) == []
