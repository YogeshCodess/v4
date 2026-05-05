"""Tests for tools/acpr_mask_validator.py."""
from __future__ import annotations

import pytest

from tools.acpr_mask_validator import (
    get_mask,
    list_supported_masks,
    validate_acpr_mask,
)


# ---------------------------------------------------------------------------
# Mask lookup
# ---------------------------------------------------------------------------

class TestMaskLookup:

    def test_lists_all_five_masks(self):
        masks = list_supported_masks()
        assert "MIL-STD-461" in masks
        assert "FCC-PART-15-CLASS-A" in masks
        assert "FCC-PART-15-CLASS-B" in masks
        assert "ETSI-EN-300" in masks
        assert "FCC-PART-97" in masks

    def test_get_mask_by_exact_key(self):
        m = get_mask("MIL-STD-461")
        assert m is not None
        assert "military" in m["label"].lower()

    def test_get_mask_by_alias(self):
        """Common alias phrasings resolve."""
        assert get_mask("FCC Part 15 Class A") is not None
        assert get_mask("FCC Part 15 Class B") is not None
        assert get_mask("MIL_STD_461") is not None
        assert get_mask("ETSI EN 300") is not None

    def test_get_mask_fuzzy_fallback(self):
        """Loose matches still resolve to a reasonable family."""
        assert get_mask("MIL-STD-461 CE102")["label"].startswith("MIL")
        assert get_mask("ETSI something EN 300 else") is not None

    def test_get_mask_unknown_returns_none(self):
        assert get_mask("BANANA-SPEC") is None
        assert get_mask(None) is None
        assert get_mask("") is None
        assert get_mask("N/A") is None


# ---------------------------------------------------------------------------
# ACPR mask checking
# ---------------------------------------------------------------------------

class TestAcprValidation:

    def test_no_mask_returns_no_issues(self):
        assert validate_acpr_mask(
            claimed_aclr_dbc=-30, mask_name=None,
        ) == []
        assert validate_acpr_mask(
            claimed_aclr_dbc=-30, mask_name="N/A",
        ) == []

    def test_aclr_meets_mil_std_461_passes(self):
        """MIL-STD-461 ACPR limit = -60 dBc. Claim -65 dBc → exceeds
        by 5 dB (inside the 3 dB safety margin) → pass."""
        assert validate_acpr_mask(
            claimed_aclr_dbc=-65, mask_name="MIL-STD-461",
        ) == []

    def test_aclr_misses_mil_std_461_flagged_high(self):
        """Claim -45 dBc vs -60 dBc MIL limit → 15 dB shortfall."""
        issues = validate_acpr_mask(
            claimed_aclr_dbc=-45, mask_name="MIL-STD-461",
        )
        assert len(issues) == 1
        assert issues[0]["severity"] == "high"
        assert issues[0]["category"] == "acpr_mask_violation"
        assert "15" in issues[0]["detail"]

    def test_aclr_within_safety_margin_flagged(self):
        """Claim -58 dBc vs MIL -60 dBc: passes the absolute limit but
        misses the 3 dB safety margin (required -63 dBc) → flagged."""
        issues = validate_acpr_mask(
            claimed_aclr_dbc=-58, mask_name="MIL-STD-461",
            safety_margin_db=3.0,
        )
        assert any(i["category"] == "acpr_mask_violation" for i in issues)

    def test_fcc_part_15_class_b_stricter_than_class_a(self):
        """Same claim, Class B flags and Class A doesn't."""
        # Claim = -48 dBc.  A limit = -45 (passes after 3 dB margin → -48 ≥ -48)
        #                   B limit = -50 (fails after 3 dB margin → need -53)
        a = validate_acpr_mask(
            claimed_aclr_dbc=-48, mask_name="FCC-PART-15-CLASS-A",
        )
        b = validate_acpr_mask(
            claimed_aclr_dbc=-48, mask_name="FCC-PART-15-CLASS-B",
        )
        assert a == []
        assert any(i["category"] == "acpr_mask_violation" for i in b)

    def test_accepts_string_with_units(self):
        """'-45 dBc' as a string should parse cleanly."""
        issues = validate_acpr_mask(
            claimed_aclr_dbc="-45 dBc adjacent",
            mask_name="MIL-STD-461",
        )
        assert any(i["category"] == "acpr_mask_violation" for i in issues)

    def test_no_aclr_claim_emits_info(self):
        """Mask selected but no ACPR claim → advisory info."""
        issues = validate_acpr_mask(
            claimed_aclr_dbc=None, mask_name="FCC-PART-15-CLASS-B",
        )
        assert any(
            i["severity"] == "info" and i["category"] == "acpr_unknown"
            for i in issues
        )


# ---------------------------------------------------------------------------
# Harmonic rejection (same mechanism, different limit)
# ---------------------------------------------------------------------------

class TestHarmonicValidation:

    def test_harmonic_meets_limit_passes(self):
        """MIL harmonic limit = -70 dBc. Claim -75 → passes."""
        assert validate_acpr_mask(
            claimed_aclr_dbc=-65, claimed_harmonic_dbc=-75,
            mask_name="MIL-STD-461",
        ) == []

    def test_harmonic_misses_limit_flagged(self):
        """Claim -50 dBc vs MIL -70 limit → 20 dB shortfall flagged."""
        issues = validate_acpr_mask(
            claimed_aclr_dbc=-65,
            claimed_harmonic_dbc=-50,
            mask_name="MIL-STD-461",
        )
        assert any(i["category"] == "harmonic_mask_violation" for i in issues)

    def test_both_fail_emits_both_issues(self):
        issues = validate_acpr_mask(
            claimed_aclr_dbc=-30, claimed_harmonic_dbc=-30,
            mask_name="MIL-STD-461",
        )
        categories = {i["category"] for i in issues}
        assert "acpr_mask_violation" in categories
        assert "harmonic_mask_violation" in categories


# ---------------------------------------------------------------------------
# Edge / robustness
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_garbage_mask_name_silently_skipped(self):
        """Mask name we can't resolve → no crash, no issue."""
        assert validate_acpr_mask(
            claimed_aclr_dbc=-20, mask_name="completely made up",
        ) == []

    def test_safety_margin_zero_accepts_exact_limit(self):
        """Tight caller: no margin, claim hits the limit exactly → pass."""
        assert validate_acpr_mask(
            claimed_aclr_dbc=-45, mask_name="FCC-PART-15-CLASS-A",
            safety_margin_db=0.0,
        ) == []
