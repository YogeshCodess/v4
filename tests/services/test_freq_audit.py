"""Tests for services.freq_audit — frequency-discipline gate.

Covers the user-reported bug: project specifies 6-18 GHz but BOM has
parts spec'd for a different band (e.g. 6-7 GHz LO).
"""
from __future__ import annotations

import pytest

from services.freq_audit import (
    extract_design_freq_range,
    extract_part_freq_range,
    freq_band_relationship,
    parse_freq_range,
    run_frequency_audit,
)


# ---------------------------------------------------------------------------
# parse_freq_range
# ---------------------------------------------------------------------------

class TestParseFreqRange:
    @pytest.mark.parametrize("text, expected", [
        ("6-18 GHz", (6.0, 18.0)),
        ("6 to 18 GHz", (6.0, 18.0)),
        ("6–18 GHz", (6.0, 18.0)),  # en-dash
        ("6—18GHz", (6.0, 18.0)),    # em-dash, no space
        ("0.4-2.0 GHz", (0.4, 2.0)),
        ("18-6 GHz", (6.0, 18.0)),    # reversed → corrected
    ])
    def test_simple_ghz_ranges(self, text, expected):
        assert parse_freq_range(text) == expected

    def test_mixed_units(self):
        # "57 MHz - 14 GHz" → (0.057 GHz, 14.0 GHz)
        r = parse_freq_range("57 MHz - 14 GHz")
        assert r is not None
        assert abs(r[0] - 0.057) < 1e-6
        assert r[1] == 14.0

    def test_band_names(self):
        assert parse_freq_range("X-band") == (8.0, 12.0)
        assert parse_freq_range("S-band") == (2.0, 4.0)
        assert parse_freq_range("Ku-band") == (12.0, 18.0)

    def test_band_with_explicit_range_prefers_explicit(self):
        """When both a band name and an explicit range are present, the
        explicit range wins (more precise)."""
        # "X-band 8-12 GHz" should give (8.0, 12.0) not the default
        # X-band bounds (which happen to be the same here, but principle
        # matters for "X-band 9-10 GHz" type inputs).
        r = parse_freq_range("X-band 9-10 GHz")
        assert r == (9.0, 10.0)

    def test_returns_none_on_garbage(self):
        assert parse_freq_range("") is None
        assert parse_freq_range(None) is None
        assert parse_freq_range("frequency unspecified") is None
        assert parse_freq_range("low band") is None  # no canonical name match


# ---------------------------------------------------------------------------
# extract_design_freq_range
# ---------------------------------------------------------------------------

class TestExtractDesignFreqRange:
    def test_picks_up_frequency_range_ghz_key(self):
        dp = {"frequency_range_ghz": "6-18 GHz"}
        assert extract_design_freq_range(dp) == (6.0, 18.0)

    def test_picks_up_band_name(self):
        dp = {"frequency_range": "X-band"}
        assert extract_design_freq_range(dp) == (8.0, 12.0)

    def test_picks_up_numeric_pair(self):
        dp = {"min_freq_ghz": 6, "max_freq_ghz": 18}
        assert extract_design_freq_range(dp) == (6.0, 18.0)

    def test_returns_none_when_absent(self):
        assert extract_design_freq_range({}) is None
        assert extract_design_freq_range({"unrelated": "data"}) is None
        assert extract_design_freq_range(None) is None


# ---------------------------------------------------------------------------
# extract_part_freq_range — pulls freq from BOM rows
# ---------------------------------------------------------------------------

class TestExtractPartFreqRange:
    def test_picks_up_pll_min_max_hz(self):
        """Curated specs library uses pll_min_output_freq_hz/pll_max..."""
        row = {
            "part_number": "HMC1063",
            "pll_min_output_freq_hz": 6_000_000_000,
            "pll_max_output_freq_hz": 7_000_000_000,
        }
        assert extract_part_freq_range(row) == (6.0, 7.0)

    def test_picks_up_min_max_freq_ghz(self):
        row = {"part_number": "X", "min_freq_ghz": 2, "max_freq_ghz": 18}
        assert extract_part_freq_range(row) == (2.0, 18.0)

    def test_picks_up_description_text(self):
        """LLM-emitted BOM rows often only have description, no structured
        frequency fields. The fallback parser pulls the range out."""
        row = {
            "part_number": "ADF5610",
            "description": "57 MHz - 14 GHz wideband synthesizer + VCO",
        }
        r = extract_part_freq_range(row)
        assert r is not None
        assert abs(r[0] - 0.057) < 1e-6
        assert r[1] == 14.0

    def test_picks_up_nested_key_specs(self):
        row = {
            "part_number": "X",
            "key_specs": {"min_freq_ghz": 6, "max_freq_ghz": 18},
        }
        assert extract_part_freq_range(row) == (6.0, 18.0)

    def test_returns_none_on_missing(self):
        assert extract_part_freq_range({"part_number": "X"}) is None
        assert extract_part_freq_range({}) is None


# ---------------------------------------------------------------------------
# freq_band_relationship
# ---------------------------------------------------------------------------

class TestFreqBandRelationship:
    def test_covers(self):
        # spec 2-18 GHz covers 6-12 GHz request
        assert freq_band_relationship(2, 18, 6, 12) == "covers"

    def test_exact_match_is_covers(self):
        assert freq_band_relationship(6, 18, 6, 18) == "covers"

    def test_partial(self):
        # spec 6-7 GHz partially covers 6-18 GHz request
        assert freq_band_relationship(6, 7, 6, 18) == "partial"

    def test_no_overlap_below(self):
        assert freq_band_relationship(0.4, 2, 6, 18) == "no_overlap"

    def test_no_overlap_above(self):
        assert freq_band_relationship(20, 30, 6, 18) == "no_overlap"

    def test_unknown_when_spec_missing(self):
        assert freq_band_relationship(None, 18, 6, 18) == "unknown"
        assert freq_band_relationship(6, None, 6, 18) == "unknown"

    def test_tolerance_absorbs_decimal_drift(self):
        """A 0.04 GHz drift should still be 'covers' due to default
        tolerance of 0.05 GHz."""
        assert freq_band_relationship(6.04, 17.96, 6.0, 18.0) == "covers"


# ---------------------------------------------------------------------------
# run_frequency_audit — the regression target
# ---------------------------------------------------------------------------

class TestRunFrequencyAudit:
    def test_user_reported_bug_6_to_18_ghz_with_6_to_7_ghz_lo(self):
        """User scenario: project spec'd for 6-18 GHz, BOM contains
        HMC1063 (a 6-7 GHz LO). Pre-fix: bug shipped silently. Post-fix:
        audit raises a `frequency_partial_coverage_advisory` (LO role
        tolerates partial coverage but operator should confirm)."""
        bom = [
            {
                "part_number": "HMC1063",
                "role": "lo",
                "description": "Low-noise PLL synthesizer 6-7 GHz",
                "pll_min_output_freq_hz": 6_000_000_000,
                "pll_max_output_freq_hz": 7_000_000_000,
            }
        ]
        dp = {"frequency_range_ghz": "6-18 GHz"}
        issues = run_frequency_audit(bom, dp)
        assert len(issues) == 1
        assert issues[0].category == "frequency_partial_coverage_advisory"
        assert issues[0].severity == "medium"
        assert "HMC1063" in issues[0].detail

    def test_lna_partial_coverage_is_high_severity(self):
        """LNA covering only 6-7 GHz against a 6-18 GHz project is a
        hard correctness failure — receiver fails above 7 GHz."""
        bom = [
            {
                "part_number": "PARTIAL_LNA",
                "role": "lna",
                "description": "Wideband LNA 6-7 GHz NF 1.5 dB",
            }
        ]
        dp = {"frequency_range_ghz": "6-18 GHz"}
        issues = run_frequency_audit(bom, dp)
        assert len(issues) == 1
        assert issues[0].category == "frequency_partial_coverage"
        assert issues[0].severity == "high"

    def test_no_overlap_is_critical(self):
        """Part has zero overlap with the requested band — hard fail."""
        bom = [
            {
                "part_number": "WRONG_BAND_LNA",
                "role": "lna",
                "description": "LNA 0.4-2 GHz",
            }
        ]
        dp = {"frequency_range_ghz": "6-18 GHz"}
        issues = run_frequency_audit(bom, dp)
        assert len(issues) == 1
        assert issues[0].category == "frequency_no_overlap"
        assert issues[0].severity == "critical"

    def test_full_coverage_no_issue(self):
        """When every part fully covers the requested band, no issues."""
        bom = [
            {
                "part_number": "WIDEBAND_LNA",
                "role": "lna",
                "description": "Wideband LNA 2-20 GHz",
            },
            {
                "part_number": "GOOD_LNA",
                "role": "lna",
                "description": "Wideband LNA 2-18 GHz",
            },
        ]
        dp = {"frequency_range_ghz": "6-12 GHz"}
        issues = run_frequency_audit(bom, dp)
        assert issues == []

    def test_unknown_part_freq_is_silent(self):
        """Parts with no freq info don't trigger issues — let the LLM/
        operator handle them informed by the spec_hint context."""
        bom = [{"part_number": "X", "role": "lna"}]  # no freq fields
        dp = {"frequency_range_ghz": "6-18 GHz"}
        assert run_frequency_audit(bom, dp) == []

    def test_missing_design_freq_emits_advisory(self):
        """Project with no frequency_range_ghz: surface a single advisory
        so the operator knows the audit didn't run."""
        bom = [{"part_number": "X", "role": "lna", "description": "LNA 0.1-1 GHz"}]
        issues = run_frequency_audit(bom, {})
        assert len(issues) == 1
        assert issues[0].category == "freq_audit_skipped"

    def test_empty_bom_no_issues(self):
        assert run_frequency_audit([], {"frequency_range_ghz": "6-18 GHz"}) == []

    def test_multiple_offending_parts_all_flagged(self):
        bom = [
            {"part_number": "BAD1", "role": "lna", "description": "LNA 0.4-2 GHz"},
            {"part_number": "BAD2", "role": "filter", "description": "BPF 24-30 GHz"},
            {"part_number": "OK1",  "role": "lna", "description": "LNA 2-18 GHz"},
        ]
        dp = {"frequency_range_ghz": "6-18 GHz"}
        issues = run_frequency_audit(bom, dp)
        flagged = {i.location.split("/")[-1] for i in issues}
        assert "BAD1" in flagged
        assert "BAD2" in flagged
        assert "OK1" not in flagged

    def test_band_name_user_input(self):
        """User typed 'X-band' instead of 8-12 GHz — should still work."""
        bom = [
            {"part_number": "BAD", "role": "lna", "description": "LNA 1-3 GHz"},
        ]
        dp = {"frequency_range_ghz": "X-band"}
        issues = run_frequency_audit(bom, dp)
        assert len(issues) == 1
        assert issues[0].category == "frequency_no_overlap"


# ---------------------------------------------------------------------------
# Integration — verify rf_audit.run_all picks up the freq audit
# ---------------------------------------------------------------------------

class TestRfAuditIntegration:
    def test_rf_audit_run_all_includes_freq_issues(self):
        """The freq audit is wired as rule #12 in rf_audit.run_all. Verify
        it runs and surfaces issues alongside the existing rules."""
        from services.rf_audit import run_all
        tool_input = {
            "component_recommendations": [
                {"part_number": "BAD_LNA", "role": "lna",
                 "description": "LNA 0.4-2 GHz"},
            ],
            "design_parameters": {"frequency_range_ghz": "6-18 GHz"},
        }
        _ti, issues = run_all(tool_input, architecture="superhet")
        cats = {i.category for i in issues}
        assert "frequency_no_overlap" in cats, (
            f"freq_audit not running inside rf_audit.run_all. "
            f"Categories seen: {cats}"
        )

    def test_rf_audit_run_all_includes_nf_and_supply_issues(self):
        """NF + supply-voltage audits are also wired (rule 12b/12c)."""
        from services.rf_audit import run_all
        tool_input = {
            "component_recommendations": [
                # LNA with NF 4 dB > system NF target 2 dB → critical
                {"part_number": "BAD_LNA", "role": "lna",
                 "description": "LNA 2-18 GHz", "nf_db": 4.0,
                 "supply_voltage": 5.0},
            ],
            # 5V part but the project only has 3.3V available
            "design_parameters": {
                "frequency_range_ghz": "6-18 GHz",
                "noise_figure_db": 2.0,
                "supply_rails_v": [3.3, 1.8],
            },
        }
        _ti, issues = run_all(tool_input, architecture="superhet")
        cats = {i.category for i in issues}
        assert "lna_nf_exceeds_system" in cats
        assert "supply_voltage_mismatch" in cats


# ---------------------------------------------------------------------------
# NF budget audit
# ---------------------------------------------------------------------------

class TestNfBudgetAudit:
    def test_lna_nf_exceeds_system_is_critical(self):
        from services.freq_audit import run_nf_budget_audit
        bom = [{"part_number": "X", "role": "lna", "nf_db": 3.5}]
        dp = {"noise_figure_db": 2.0}
        issues = run_nf_budget_audit(bom, dp)
        assert len(issues) == 1
        assert issues[0].category == "lna_nf_exceeds_system"
        assert issues[0].severity == "critical"
        assert "3.50 dB" in issues[0].detail
        assert "2.00 dB" in issues[0].detail

    def test_lna_nf_too_high_is_high(self):
        """LNA NF > 70% of target is "high" (cascade math may close, but tight)."""
        from services.freq_audit import run_nf_budget_audit
        # Target 2 dB, 70% = 1.4 dB. LNA at 1.6 dB is in the warning zone.
        bom = [{"part_number": "X", "role": "lna", "nf_db": 1.6}]
        dp = {"noise_figure_db": 2.0}
        issues = run_nf_budget_audit(bom, dp)
        assert len(issues) == 1
        assert issues[0].category == "lna_nf_too_high"
        assert issues[0].severity == "high"

    def test_lna_nf_below_70pct_is_silent(self):
        from services.freq_audit import run_nf_budget_audit
        # Target 2 dB, LNA at 1.0 dB (< 70% of 2 = 1.4) → silent
        bom = [{"part_number": "X", "role": "lna", "nf_db": 1.0}]
        dp = {"noise_figure_db": 2.0}
        assert run_nf_budget_audit(bom, dp) == []

    def test_no_target_no_issues(self):
        from services.freq_audit import run_nf_budget_audit
        bom = [{"part_number": "X", "role": "lna", "nf_db": 99.0}]
        assert run_nf_budget_audit(bom, {}) == []

    def test_only_front_end_roles_checked(self):
        """A mixer at NF 8 dB doesn't violate this rule — only front-end
        stages contribute their full NF to the cascade."""
        from services.freq_audit import run_nf_budget_audit
        bom = [{"part_number": "MIX", "role": "mixer", "nf_db": 8.0}]
        dp = {"noise_figure_db": 2.0}
        # Mixer NF cascades behind LNA gain, so 8 dB is fine — silent.
        assert run_nf_budget_audit(bom, dp) == []


# ---------------------------------------------------------------------------
# Supply-voltage audit
# ---------------------------------------------------------------------------

class TestSupplyVoltageAudit:
    def test_5v_part_in_3v3_only_design_flagged(self):
        from services.freq_audit import run_supply_voltage_audit
        bom = [{"part_number": "X", "supply_voltage": 5.0}]
        dp = {"supply_rails_v": [3.3, 1.8]}
        issues = run_supply_voltage_audit(bom, dp)
        assert len(issues) == 1
        assert issues[0].category == "supply_voltage_mismatch"
        assert issues[0].severity == "high"

    def test_3v3_part_with_3v27_rail_passes(self):
        """0.03 V drift is well within the 0.15 V tolerance."""
        from services.freq_audit import run_supply_voltage_audit
        bom = [{"part_number": "X", "supply_voltage": 3.3}]
        dp = {"supply_rails_v": [3.27, 1.8]}
        assert run_supply_voltage_audit(bom, dp) == []

    def test_free_text_rails_parsed(self):
        from services.freq_audit import run_supply_voltage_audit
        bom = [{"part_number": "X", "supply_voltage": 12.0}]
        dp = {"available_rails": "5V, 3.3V, 1.8V"}  # no 12 V
        issues = run_supply_voltage_audit(bom, dp)
        assert len(issues) == 1
        assert issues[0].category == "supply_voltage_mismatch"

    def test_silent_when_no_rails_specified(self):
        from services.freq_audit import run_supply_voltage_audit
        bom = [{"part_number": "X", "supply_voltage": 5.0}]
        assert run_supply_voltage_audit(bom, {}) == []

    def test_silent_when_part_has_no_supply_field(self):
        from services.freq_audit import run_supply_voltage_audit
        bom = [{"part_number": "X"}]  # no supply_voltage
        dp = {"supply_rails_v": [3.3]}
        assert run_supply_voltage_audit(bom, dp) == []
