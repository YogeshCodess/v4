"""Tests for tools/rf_cascade.py — RX Friis + TX forward cascade."""
from __future__ import annotations

import math

import pytest

from tools.rf_cascade import compute_cascade, extract_stages


def _stage(part, cat, nf=None, gain=None, iip3=None):
    """RX-flavoured stage (NF + gain + IIP3)."""
    specs = {}
    if nf is not None:
        specs["nf_db"] = nf
    if gain is not None:
        specs["gain_db"] = gain
    if iip3 is not None:
        specs["iip3_dbm"] = iip3
    return {
        "part_number": part, "category": cat,
        "component_name": part,
        "key_specs": specs,
    }


def _tx_stage(part, cat, gain=None, oip3=None, pout=None, pae=None, pdc=None):
    """TX-flavoured stage — gain + output-referred OIP3 + Pout + optional PAE."""
    specs = {}
    if gain is not None:
        specs["gain_db"] = gain
    if oip3 is not None:
        specs["oip3_dbm"] = oip3
    if pout is not None:
        specs["pout_dbm"] = pout
    if pae is not None:
        specs["pae_pct"] = pae
    if pdc is not None:
        specs["pdc_w"] = pdc
    return {
        "part_number": part, "category": cat,
        "component_name": part,
        "key_specs": specs,
    }


# ---------------------------------------------------------------------------
# Stage extraction
# ---------------------------------------------------------------------------

class TestExtractStages:

    def test_pulls_active_rf_components(self):
        parts = [
            _stage("LNA1", "RF-LNA", nf=1.5, gain=15),
            _stage("MIX1", "RF-Mixer", nf=8, gain=-5, iip3=10),
            # Non-RF — should be dropped
            {"part_number": "LDO1", "category": "Power-LDO",
             "key_specs": {"vout": 3.3}},
        ]
        s = extract_stages(parts)
        assert [x["part_number"] for x in s] == ["LNA1", "MIX1"]

    def test_passive_loss_becomes_negative_gain(self):
        """A filter with `insertion_loss_db: 1.8` should contribute -1.8 dB."""
        f = {"part_number": "BPF1", "category": "RF-Filter",
             "key_specs": {"insertion_loss_db": 1.8}}
        s = extract_stages([f])
        assert len(s) == 1
        assert s[0]["gain_db"] == pytest.approx(-1.8)

    def test_reads_strings_with_units(self):
        p = {"part_number": "X", "category": "RF-LNA",
             "key_specs": {"nf_db": "1.4 dB", "gain_db": "+15 dB"}}
        s = extract_stages([p])
        assert s[0]["nf_db"] == pytest.approx(1.4)
        assert s[0]["gain_db"] == pytest.approx(15)


# ---------------------------------------------------------------------------
# Friis math
# ---------------------------------------------------------------------------

class TestFriisMath:

    def test_single_stage_cascade_equals_stage(self):
        """One LNA — cascade NF = LNA NF, gain = LNA gain."""
        r = compute_cascade([_stage("LNA", "RF-LNA", nf=1.5, gain=15)])
        assert r["totals"]["nf_db"] == pytest.approx(1.5, abs=1e-6)
        assert r["totals"]["gain_db"] == pytest.approx(15, abs=1e-6)

    def test_two_stage_friis_textbook(self):
        """Textbook example: LNA NF=2 dB G=20 dB then mixer NF=10 dB.
        Expected cascade NF ≈ 2.034 dB (mixer's contribution is divided
        by G1 = 100 linear)."""
        r = compute_cascade([
            _stage("LNA", "RF-LNA", nf=2, gain=20),
            _stage("MIX", "RF-Mixer", nf=10, gain=-6),
        ])
        # NF1_lin = 1.585, NF2_lin = 10.0, G1_lin = 100
        # F_total = 1.585 + (10-1)/100 = 1.675  →  NF_total = 10*log10(1.675) = 2.24
        assert r["totals"]["nf_db"] == pytest.approx(2.24, abs=0.05)
        assert r["totals"]["gain_db"] == pytest.approx(14, abs=1e-6)

    def test_three_stage_cascade_total_gain(self):
        r = compute_cascade([
            _stage("LNA1", "RF-LNA", nf=1, gain=15),
            _stage("BPF", "RF-Filter", nf=1.5, gain=-1.5),
            _stage("MIX", "RF-Mixer", nf=9, gain=-6, iip3=15),
        ])
        assert r["totals"]["gain_db"] == pytest.approx(7.5, abs=1e-6)
        # Stage 3 NF contribution is (10^0.9 - 1) / (10^(15-1.5)/10)
        #                          = 6.943 / 22.387 = 0.310  →  F_lin = 1.259 + 0.310 + 0.023 ≈ 1.59
        assert 1.8 <= r["totals"]["nf_db"] <= 2.5

    def test_iip3_cascade_dominated_by_later_stage(self):
        """Back-end mixer IIP3 dominates when front-end gain is high."""
        r = compute_cascade([
            _stage("LNA", "RF-LNA", nf=1, gain=20, iip3=20),
            _stage("MIX", "RF-Mixer", nf=7, gain=-5, iip3=10),
        ])
        # LNA IIP3 at input: 20 dBm. Mixer IIP3 referred to input:
        #   10 dBm - 20 dB = -10 dBm. That dominates Friis IIP3.
        # Cascade IIP3 ≈ -10 dBm (the mixer's input-referred number).
        iip3 = r["totals"]["iip3_dbm"]
        assert iip3 is not None
        assert -11 <= iip3 <= -9

    def test_skips_stages_without_specs(self):
        """Incomplete rows shouldn't break the math."""
        r = compute_cascade([
            _stage("LNA", "RF-LNA", nf=1.5, gain=15),
            {"part_number": "MYSTERY", "category": "RF-Mixer",
             "key_specs": {}},  # no specs
            _stage("IF_AMP", "RF-Amplifier", nf=3, gain=10),
        ])
        assert r["totals"]["nf_db"] is not None
        assert r["totals"]["gain_db"] == pytest.approx(25, abs=1e-6)


# ---------------------------------------------------------------------------
# Verdict / claim comparison
# ---------------------------------------------------------------------------

class TestVerdict:

    def test_nf_claim_pass(self):
        r = compute_cascade(
            [_stage("LNA", "RF-LNA", nf=1.5, gain=15)],
            claimed_nf_db=3.0,
        )
        assert r["verdict"]["nf_pass"] is True
        assert r["verdict"]["nf_headroom_db"] == pytest.approx(1.5, abs=0.01)

    def test_nf_claim_fail(self):
        r = compute_cascade(
            [_stage("LNA", "RF-LNA", nf=4.5, gain=15)],
            claimed_nf_db=3.0,
        )
        assert r["verdict"]["nf_pass"] is False
        assert r["verdict"]["nf_headroom_db"] < 0

    def test_gain_within_3db_slack_passes(self):
        """Gain claim of 40 dB, actual 38 dB → pass (2 dB slack)."""
        r = compute_cascade(
            [_stage("A1", "RF-LNA", gain=20),
             _stage("A2", "RF-Amplifier", gain=18)],
            claimed_total_gain_db=40.0,
        )
        assert r["verdict"]["gain_pass"] is True

    def test_gain_outside_slack_fails(self):
        r = compute_cascade(
            [_stage("A1", "RF-LNA", gain=10)],
            claimed_total_gain_db=40.0,
        )
        assert r["verdict"]["gain_pass"] is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_components_returns_zero_stage_cascade(self):
        r = compute_cascade([])
        assert r["totals"]["stage_count"] == 0
        assert r["totals"]["nf_db"] is None
        assert r["stages"] == []

    def test_no_claims_means_all_verdicts_none(self):
        r = compute_cascade([_stage("LNA", "RF-LNA", nf=1.5, gain=15)])
        v = r["verdict"]
        assert v["nf_pass"] is None
        assert v["gain_pass"] is None
        assert v["iip3_pass"] is None

    def test_direction_defaults_to_rx(self):
        """Omitting `direction` must keep the existing RX behaviour."""
        r = compute_cascade([_stage("LNA", "RF-LNA", nf=1.5, gain=15)])
        assert r.get("direction") == "rx"
        assert "nf_db" in r["totals"]

    def test_cumulative_values_monotonic(self):
        """Cumulative gain should increase (or stay flat on passive loss)
        across stages; cumulative NF should never decrease."""
        r = compute_cascade([
            _stage("LNA", "RF-LNA", nf=1, gain=15),
            _stage("BPF", "RF-Filter", nf=1.5, gain=-1.5),
            _stage("MIX", "RF-Mixer", nf=8, gain=-5),
        ])
        cum_gain = [s["cum_gain_db"] for s in r["stages"]]
        cum_nf = [s["cum_nf_db"] for s in r["stages"] if s["cum_nf_db"] is not None]
        # gain: 15, 13.5, 8.5 — monotone decreasing is fine, just needs to
        # reflect the accumulation, so check final matches totals
        assert cum_gain[-1] == pytest.approx(r["totals"]["gain_db"], abs=1e-6)
        # NF: monotone non-decreasing
        for a, b in zip(cum_nf, cum_nf[1:]):
            assert b >= a - 1e-6


# ===========================================================================
# Transmitter cascade
# ===========================================================================

class TestTxForwardCascade:

    def test_single_pa_pout_equals_pin_plus_gain(self):
        """TX: one PA with G=30 dB, Pin=-10 dBm → Pout=+20 dBm."""
        r = compute_cascade(
            [_tx_stage("PA1", "RF-PA", gain=30, pout=25, oip3=35)],
            direction="tx", input_power_dbm=-10.0,
        )
        assert r["direction"] == "tx"
        assert r["totals"]["pout_dbm"] == pytest.approx(20.0, abs=1e-6)
        assert r["totals"]["gain_db"] == pytest.approx(30.0, abs=1e-6)

    def test_chain_pout_accumulates(self):
        """Driver (G=15 dB) → PA (G=30 dB), Pin=-20 dBm → Pout=+25 dBm."""
        r = compute_cascade(
            [
                _tx_stage("DRV", "RF-Driver", gain=15, oip3=25),
                _tx_stage("PA",  "RF-PA",     gain=30, oip3=45, pout=40),
            ],
            direction="tx", input_power_dbm=-20.0,
        )
        assert r["totals"]["pout_dbm"] == pytest.approx(25.0, abs=1e-6)
        # Each stage carries its own Pin/Pout_computed
        assert r["stages"][0]["pin_dbm"] == pytest.approx(-20.0)
        assert r["stages"][0]["pout_computed_dbm"] == pytest.approx(-5.0)
        assert r["stages"][1]["pin_dbm"] == pytest.approx(-5.0)
        assert r["stages"][1]["pout_computed_dbm"] == pytest.approx(25.0)

    def test_last_stage_oip3_dominates(self):
        """TX: 1/OIP3_sys = Σ 1/(G_after_k · OIP3_k,out). The PA's OIP3
        isn't attenuated forward (G_after=1), and a well-chosen driver
        that's ≥10 dB more linear than the PA-referred-forward won't
        drag the system below the PA. Textbook: system OIP3 ≈ PA OIP3
        when driver is linear enough."""
        # Driver OIP3=30 dBm = 1 W, G_after_drv=100 (PA gain 20 dB)
        #   reciprocal contribution: 1/(100·1) = 1e-2
        # PA OIP3=40 dBm = 10 W, G_after=1
        #   reciprocal contribution: 1/(1·10) = 0.1
        # Sum = 0.11  →  OIP3_sys_w = 9.09 W  →  OIP3_sys_dbm = 39.6 dBm
        r = compute_cascade(
            [
                _tx_stage("DRV", "RF-Driver", gain=20, oip3=30),
                _tx_stage("PA",  "RF-PA",     gain=20, oip3=40),
            ],
            direction="tx", input_power_dbm=-20.0,
        )
        oip3 = r["totals"]["oip3_dbm"]
        assert oip3 is not None
        assert 39.0 <= oip3 <= 40.0

    def test_pa_dominates_when_driver_linear(self):
        """When the driver has far higher OIP3 than the PA (after gain
        propagation), the final PA dominates — the 'last stage wins' case."""
        r = compute_cascade(
            [
                _tx_stage("DRV", "RF-Driver", gain=15, oip3=50),  # very linear
                _tx_stage("PA",  "RF-PA",     gain=25, oip3=40),  # PA IS the bottleneck
            ],
            direction="tx", input_power_dbm=-20.0,
        )
        oip3 = r["totals"]["oip3_dbm"]
        assert oip3 is not None
        # PA output-referred OIP3 = 40 dBm. Driver's reflected forward:
        #  50 dBm + 25 dB = +75 dBm equivalent OIP3 at system output.
        #  Recip sum ≈ 1/10^4 (PA) + 1/10^7.5 (driver) ≈ 1/10^4
        #  → OIP3_sys ≈ 40 dBm
        assert 39.5 <= oip3 <= 40.5

    def test_compression_warning_when_drive_exceeds_pout_spec(self):
        """PA spec says Pout=+30 dBm but the computed drive is +35 dBm."""
        r = compute_cascade(
            [
                _tx_stage("DRV", "RF-Driver", gain=20, pout=15),
                _tx_stage("PA",  "RF-PA",     gain=25, pout=30),  # claim 30 dBm max
            ],
            direction="tx", input_power_dbm=-10.0,
        )
        # Drive into PA: -10 + 20 = +10 dBm. PA output: +10 + 25 = +35 dBm.
        # PA spec says Pout_max = 30 dBm → 5 dB over spec → warning
        assert r["stages"][1]["compression_warning"] is True
        assert r["totals"]["compression_warnings"]
        assert r["verdict"]["no_compression"] is False

    def test_no_compression_when_within_spec(self):
        r = compute_cascade(
            [
                _tx_stage("DRV", "RF-Driver", gain=15, pout=20),
                _tx_stage("PA",  "RF-PA",     gain=20, pout=40),
            ],
            direction="tx", input_power_dbm=-10.0,
        )
        assert r["verdict"]["no_compression"] is True
        assert all(not s["compression_warning"] for s in r["stages"])

    def test_system_pae_from_pdc_roll_up(self):
        """System PAE = (Pout - Pin) / sum(Pdc). Pout=+30 dBm (1 W),
        Pin=-10 dBm (0.1 mW), sum Pdc=3 W → PAE ≈ 33 %."""
        r = compute_cascade(
            [
                _tx_stage("DRV", "RF-Driver", gain=20, pout=15, pae=30, pdc=0.5),
                _tx_stage("PA",  "RF-PA",     gain=20, pout=40, pae=45, pdc=2.5),
            ],
            direction="tx", input_power_dbm=-10.0,
        )
        pae = r["totals"]["pae_pct"]
        # (1 W - 0.0001 W) / 3 W = 33.3 %
        assert pae == pytest.approx(33.3, abs=1.0)
        assert r["totals"]["pdc_total_w"] == pytest.approx(3.0, abs=1e-6)

    def test_pae_falls_back_to_arithmetic_mean_without_pdc(self):
        """When Pdc isn't populated per stage, system PAE is the mean
        of per-stage PAE values — rough but useful."""
        r = compute_cascade(
            [
                _tx_stage("DRV", "RF-Driver", gain=15, pae=20),
                _tx_stage("PA",  "RF-PA",     gain=25, pae=50),
            ],
            direction="tx", input_power_dbm=-10.0,
        )
        assert r["totals"]["pae_pct"] == pytest.approx(35.0, abs=0.1)

    def test_tx_verdicts_pout_oip3_pae(self):
        r = compute_cascade(
            [
                _tx_stage("DRV", "RF-Driver", gain=15, oip3=30),
                _tx_stage("PA",  "RF-PA",     gain=25, oip3=45, pout=42, pae=45, pdc=5.0),
            ],
            direction="tx", input_power_dbm=-10.0,
            claimed_pout_dbm=30.0,
            claimed_oip3_dbm=40.0,
            claimed_total_gain_db=40.0,
            claimed_pae_pct=40.0,
        )
        v = r["verdict"]
        # Pout computed = -10 + 15 + 25 = +30 dBm ≈ claim → pass
        assert v["pout_pass"] is True
        # OIP3 math (output-referred):
        #   Driver OIP3=30 dBm=1 W, G_after=316.2 → recip = 1/(316.2·1) = 0.00316
        #   PA OIP3=45 dBm=31.6 W, G_after=1      → recip = 1/(1·31.6)  = 0.0316
        #   Sum = 0.0348 → OIP3_sys_w = 28.74 W → 44.6 dBm
        # 44.6 ≥ 40 claim → pass
        assert v["oip3_pass"] is True
        assert v["gain_pass"] is True
        # PAE measured = (1 W - 0.1 mW) / 5 W ≈ 20 % vs claim 40 % → fail
        assert v["pae_pass"] is False

    def test_tx_no_claims_means_verdicts_none(self):
        r = compute_cascade(
            [_tx_stage("PA", "RF-PA", gain=20, pout=30)],
            direction="tx",
        )
        v = r["verdict"]
        assert v["pout_pass"] is None
        assert v["oip3_pass"] is None
        assert v["pae_pass"] is None
        assert v["gain_pass"] is None

    def test_tx_empty_components(self):
        r = compute_cascade([], direction="tx")
        assert r["direction"] == "tx"
        assert r["totals"]["stage_count"] == 0
        assert r["totals"]["pout_dbm"] is None

    def test_tx_passive_filter_after_pa_reduces_output(self):
        """A harmonic filter after the PA with IL=1 dB pulls Pout down."""
        r = compute_cascade(
            [
                _tx_stage("PA",  "RF-PA",     gain=25, oip3=40),
                {"part_number": "HARMFILT", "category": "RF-Filter",
                 "key_specs": {"insertion_loss_db": 1.0}},
            ],
            direction="tx", input_power_dbm=0.0,
        )
        assert r["totals"]["pout_dbm"] == pytest.approx(24.0, abs=1e-6)

    def test_bogus_direction_falls_back_to_rx(self):
        """Garbage `direction` must not crash — default to RX."""
        r = compute_cascade(
            [_stage("LNA", "RF-LNA", nf=1.5, gain=15)],
            direction="banana",  # type: ignore[arg-type]
        )
        assert r["direction"] == "rx"
