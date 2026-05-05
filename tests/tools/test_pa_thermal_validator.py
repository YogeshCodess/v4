"""Tests for tools/pa_thermal_validator.py."""
from __future__ import annotations

import pytest

from tools.pa_thermal_validator import validate_pa_thermal


def _pa(pn: str, *, tech: str | None = None, pdc: float | None = None,
        pae: float | None = None, pout_dbm: float | None = None,
        theta_jc: float | None = None, category: str = "RF-PA"):
    ks: dict = {}
    if tech: ks["technology"] = tech
    if pdc is not None: ks["pdc_w"] = pdc
    if pae is not None: ks["pae_pct"] = pae
    if pout_dbm is not None: ks["pout_dbm"] = pout_dbm
    if theta_jc is not None: ks["theta_jc"] = theta_jc
    return {"part_number": pn, "category": category, "key_specs": ks}


# ---------------------------------------------------------------------------
# Happy path — cool PA passes
# ---------------------------------------------------------------------------

class TestPassingCases:

    def test_empty_component_list_returns_no_issues(self):
        assert validate_pa_thermal([]) == []

    def test_non_pa_components_ignored(self):
        """LNA + filter + capacitor — nothing to thermally check."""
        bom = [
            {"part_number": "LNA1", "category": "RF-LNA", "key_specs": {"nf_db": 1.5}},
            {"part_number": "BPF1", "category": "RF-Filter", "key_specs": {}},
            {"part_number": "C1",   "category": "Passive-Cap", "key_specs": {}},
        ]
        assert validate_pa_thermal(bom) == []

    def test_cool_gan_pa_with_heatsink_passes(self):
        """10 W PA at 45 % PAE dissipates 5.5 W. θ_jc=1.5, θ_sa=2.0
        → ΔT = 5.5 × 3.5 = 19.25 °C, Tj = 25 + 19 = 44 °C. Safe."""
        issues = validate_pa_thermal(
            [_pa("CMPA801B025", tech="GaN", pdc=10.0, pae=45.0, pout_dbm=37)],
            ambient_temp_c=25.0, heatsink_theta_sa=2.0,
        )
        assert issues == []


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

class TestFailureModes:

    def test_gan_pa_no_heatsink_overruns_tj_max(self):
        """GaN PA, Pdc=50 W, PAE=40 % → 30 W dissipated. With the
        default 10 °C/W external θ, ΔT = 30 × (1.5 + 10) = 345 °C.
        Tj = 25 + 345 = 370 °C. Way over 200 °C Tj_max — critical."""
        issues = validate_pa_thermal(
            [_pa("QPD1013", tech="GaN", pdc=50.0, pae=40.0)],
        )
        overrun = [i for i in issues if i["category"] == "pa_thermal_overrun"]
        assert len(overrun) == 1
        assert overrun[0]["severity"] == "critical"
        assert "QPD1013" in overrun[0]["location"]

    def test_gaas_pa_near_tj_max_flagged_high_derating(self):
        """GaAs pHEMT, Tj_max=150 °C. Pdc=2 W, PAE=20 % → 1.6 W loss.
        θ_jc=4, θ_sa=80 (terrible sink) → ΔT = 1.6 × 84 = 134 °C.
        Tj = 25 + 134 = 159 °C. Over Tj_max=150 → critical."""
        issues = validate_pa_thermal(
            [_pa("AMMP-6232", tech="GaAs", pdc=2.0, pae=20.0)],
            heatsink_theta_sa=80.0,
        )
        assert any(i["category"] == "pa_thermal_overrun" for i in issues)

    def test_warm_gan_in_derating_margin_high(self):
        """GaN, Tj_max=200. Put Tj in the 180-200 window → high-severity
        derating flag (not critical)."""
        # Target Tj ≈ 190 °C → ΔT = 165 °C from 25 °C ambient.
        # θ_total = 1.5 + x. Pdc choose to hit 165 °C rise.
        # P_diss × θ_total = 165. Use Pdc=10 W, PAE=50 % → P_diss = 5 W.
        # Need θ_total = 33 → θ_sa = 31.5.
        issues = validate_pa_thermal(
            [_pa("QPD1006", tech="GaN", pdc=10.0, pae=50.0)],
            heatsink_theta_sa=31.5,
            derating_margin_c=20.0,
        )
        assert any(
            i["severity"] == "high" and i["category"] == "pa_thermal_derating"
            for i in issues
        )

    def test_missing_data_emits_info(self):
        """PA with no pdc_w / pae_pct / pout_dbm — can't compute, but
        flag so the operator knows."""
        issues = validate_pa_thermal([_pa("UNKNOWN_PA", tech="GaN")])
        info = [i for i in issues if i["category"] == "pa_thermal_unknown"]
        assert len(info) == 1
        assert info[0]["severity"] == "info"


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------

class TestInputHandling:

    def test_ambient_temp_affects_headroom(self):
        """Same PA, +85 °C ambient should push Tj ~ 60 °C higher than 25 °C."""
        cool = validate_pa_thermal(
            [_pa("PA1", tech="GaN", pdc=10.0, pae=40.0, pout_dbm=37)],
            ambient_temp_c=25.0, heatsink_theta_sa=5.0,
        )
        hot = validate_pa_thermal(
            [_pa("PA1", tech="GaN", pdc=10.0, pae=40.0, pout_dbm=37)],
            ambient_temp_c=125.0, heatsink_theta_sa=5.0,
        )
        # Cool: Tj = 25 + 6 × 6.5 = 64  → safe
        # Hot:  Tj = 125 + 6 × 6.5 = 164 → still below GaN Tj_max=200,
        #       but with 15 °C default derating margin could be tight.
        assert len(cool) == 0
        # 125 + 39 = 164 °C, below 200 but in derating zone — headroom 36
        # default margin 15 → no flag. Increase margin to force a flag:
        hot_strict = validate_pa_thermal(
            [_pa("PA1", tech="GaN", pdc=10.0, pae=40.0, pout_dbm=37)],
            ambient_temp_c=125.0, heatsink_theta_sa=5.0,
            derating_margin_c=50.0,
        )
        assert any(i["category"] == "pa_thermal_derating" for i in hot_strict)

    def test_technology_inferred_from_part_number(self):
        """No explicit tech key, but QPD prefix → GaN."""
        issues = validate_pa_thermal(
            [{"part_number": "QPD1013",
              "category": "RF-PA",
              "key_specs": {"pdc_w": 50.0, "pae_pct": 40.0}}],
        )
        # Will overrun with default 10 °C/W external sink; the detail
        # string should include "GAN" since the tech was inferred.
        over = [i for i in issues if i["category"] == "pa_thermal_overrun"]
        assert len(over) == 1
        assert "GAN" in over[0]["detail"] or "GaN" in over[0]["detail"]

    def test_explicit_theta_jc_overrides_default(self):
        """Custom θ_jc from datasheet must beat our technology defaults."""
        # With explicit θ_jc=0.3 (exotic copper coin), Tj drops sharply.
        issues = validate_pa_thermal(
            [_pa("LUXURY_PA", tech="GaN", pdc=50.0, pae=40.0, theta_jc=0.3)],
            heatsink_theta_sa=1.0,
        )
        # P_diss=30, θ_total=1.3 → ΔT=39, Tj=64 → well under 200.
        assert issues == []

    def test_amplifier_under_10dbm_skipped(self):
        """An RF-Amplifier labelled as such but with only +5 dBm Pout
        is almost certainly a gain block — don't thermally flag."""
        issues = validate_pa_thermal(
            [{"part_number": "GAIN_BLOCK", "category": "RF-Amplifier",
              "key_specs": {"pout_dbm": 5.0, "pdc_w": 50.0}}],  # noisy pdc
        )
        assert issues == []
