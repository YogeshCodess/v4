"""Regression tests for the nine P0/P1/P2 fixes layered on top of the
original RF audit: stripped URLs on hallucinated parts (P0.1), parallel
distributor lookup with overall deadline (P0.2), phase-noise budgeting
(P2.8), BOM↔schematic linkage (P2.9). P0.3 (chat timeout) is covered
in tests/api/test_routes_hardening.py.

Network stubbed throughout.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from tools.digikey_api import PartInfo


def _make_info(pn="X", source="digikey", lifecycle="active", price=5.0):
    return PartInfo(
        part_number=pn, manufacturer="ADI", description="desc",
        datasheet_url="https://www.analog.com/en/products/x.html",
        product_url=None, lifecycle_status=lifecycle,
        unit_price_usd=price if source == "digikey" else None,
        stock_quantity=100, source=source,
        unit_price=price, unit_price_currency="USD",
    )


# ---------------------------------------------------------------------------
# P0.1 — hallucinated parts have their URL + lifecycle stripped
# ---------------------------------------------------------------------------

class TestHallucinatedPartScrub:

    def test_hallucinated_part_strips_datasheet_url(self, monkeypatch):
        monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
        monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
        monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
        from services.rf_audit import run_part_validation_audit
        with patch("services.rf_audit._distributor_lookup", return_value=None):
            enriched, issues = run_part_validation_audit([{
                "part_number": "INVENTED-Q9",
                "manufacturer": "LLM Fictions Inc",
                "datasheet_url": "https://www.analog.com/fake.pdf",
                "lifecycle_status": "active",
                "unit_price_usd": 4.99,
            }])
        # Issue raised
        assert any(i.category == "hallucinated_part" for i in issues)
        # Component kept in enriched list (so the audit report shows it)
        assert len(enriched) == 1
        c = enriched[0]
        # BUT: the LLM's fabricated URL + lifecycle + price are scrubbed
        # so a reviewer reading the audit doesn't get mislead by a
        # plausible-looking `www.analog.com` URL next to the hallucination flag.
        assert c.get("datasheet_url") is None
        assert c.get("lifecycle_status") is None
        assert c.get("unit_price_usd") is None
        # The canonical `_hallucinated` sentinel is set so downstream
        # renderers can gray-out / highlight the entry.
        assert c.get("_hallucinated") is True

    def test_airgap_mode_does_not_strip(self, monkeypatch):
        """When no distributor is configured, we can't distinguish
        hallucination from 'no oracle' — leave the component intact."""
        monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
        monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("MOUSER_API_KEY", raising=False)
        monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
        from services.rf_audit import run_part_validation_audit
        with patch("services.rf_audit._distributor_lookup", return_value=None):
            enriched, _ = run_part_validation_audit([{
                "part_number": "MAYBE-REAL",
                "datasheet_url": "https://example.com/ds.pdf",
                "lifecycle_status": "active",
            }])
        # Nothing stripped — no oracle, no accusation.
        c = enriched[0]
        assert c.get("datasheet_url") == "https://example.com/ds.pdf"
        assert "_hallucinated" not in c


# ---------------------------------------------------------------------------
# P0.2 — parallel distributor lookup + overall deadline
# ---------------------------------------------------------------------------

class TestParallelPartValidation:

    def test_parallel_lookup_under_deadline(self, monkeypatch):
        """10 parts that each take 100 ms to resolve should finish well
        under a 5 s deadline when run in parallel."""
        monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
        monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
        monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
        from services.rf_audit import run_part_validation_audit

        def _slow_lookup(pn, **_k):
            time.sleep(0.1)
            return _make_info(pn=pn)

        bom = [{"part_number": f"PN{i}"} for i in range(10)]
        t0 = time.monotonic()
        with patch("services.rf_audit._distributor_lookup", side_effect=_slow_lookup):
            enriched, issues = run_part_validation_audit(
                bom, overall_timeout_s=5.0, max_workers=6,
            )
        elapsed = time.monotonic() - t0
        # Serial would be ~1.0s; parallel with 6 workers should be <0.5s
        assert elapsed < 1.0, f"parallel lookup too slow: {elapsed:.2f}s"
        assert len(enriched) == 10
        assert all(i.category != "part_validation_timeout" for i in issues)

    def test_overall_deadline_flags_remaining_parts(self, monkeypatch):
        """When the overall deadline fires before every lookup finishes,
        the stragglers get a `part_validation_timeout` issue and the
        component is passed through without enrichment."""
        monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
        monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
        monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
        from services.rf_audit import run_part_validation_audit

        def _very_slow(pn, **_k):
            time.sleep(5.0)
            return _make_info(pn=pn)

        bom = [{"part_number": f"PN{i}"} for i in range(5)]
        with patch("services.rf_audit._distributor_lookup", side_effect=_very_slow):
            enriched, issues = run_part_validation_audit(
                bom, overall_timeout_s=0.3, max_workers=2,
            )
        # Every component is still returned (pipeline keeps flowing)
        assert len(enriched) == 5
        # Every component gets a timeout issue (nothing finished in 0.3s)
        timeouts = [i for i in issues if i.category == "part_validation_timeout"]
        assert len(timeouts) == 5


# ---------------------------------------------------------------------------
# P2.8 — phase-noise audit wiring
# ---------------------------------------------------------------------------

class TestPhaseNoiseWiring:

    def test_run_all_runs_phase_noise_when_claim_present(self, monkeypatch):
        monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
        monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
        from services.rf_audit import run_all
        tool_input = {
            "block_diagram_mermaid": (
                "flowchart TD\n"
                " ANT[Antenna] --> BPF[Preselector]\n"
                " BPF --> LNA[LNA]\n"
                " LNA --> MIX[Mixer]\n"
                " LO[LO] --> MIX\n"
                " MIX --> IF[IF Filter]\n"
            ),
            "component_recommendations": [
                {"part_number": "LMX2594", "category": "RF-PLL",
                 "manufacturer": "TI",
                 "datasheet_url": "https://www.ti.com/product/LMX2594",
                 "key_specs": {"phase_noise_dbchz": -115}},
            ],
            "design_parameters": {"phase_noise_dbchz": -140},
        }
        _, issues = run_all(tool_input, architecture="superhet_single")
        assert any(
            i.category == "phase_noise_budget" and i.severity == "high"
            for i in issues
        )

    def test_run_all_skips_phase_noise_without_claim(self, monkeypatch):
        monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
        monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
        from services.rf_audit import run_all
        tool_input = {
            "block_diagram_mermaid": (
                "flowchart TD\n"
                " ANT[Antenna] --> LNA[LNA]\n"
                " LNA --> MIX[Mixer]\n"
                " LO[LO] --> MIX\n"
                " MIX --> IF[IF Filter]\n"
            ),
            "component_recommendations": [
                {"part_number": "LMX2594", "category": "RF-PLL",
                 "manufacturer": "TI",
                 "datasheet_url": "https://www.ti.com/product/LMX2594",
                 "key_specs": {"phase_noise_dbchz": -115}},
            ],
            # no design_parameters
        }
        _, issues = run_all(tool_input, architecture="superhet_single")
        assert not any(i.category == "phase_noise_budget" for i in issues)


# ---------------------------------------------------------------------------
# P2.9 — BOM↔schematic linkage wiring
# ---------------------------------------------------------------------------

class TestTxCascadeAudit:
    """run_tx_cascade_audit → AuditIssue list + run_all wiring."""

    def test_rx_project_returns_no_issues(self, monkeypatch):
        """Pure RX project — must not emit any TX audit issues."""
        from services.rf_audit import run_tx_cascade_audit
        issues = run_tx_cascade_audit(
            [{"part_number": "LNA1", "category": "RF-LNA",
              "key_specs": {"nf_db": 1.5, "gain_db": 15}}],
            {"direction": "rx", "noise_figure_db": 3.0},
        )
        assert issues == []

    def test_no_design_parameters_returns_no_issues(self):
        from services.rf_audit import run_tx_cascade_audit
        assert run_tx_cascade_audit(
            [{"part_number": "PA1", "category": "RF-PA",
              "key_specs": {"gain_db": 30, "pout_dbm": 40, "oip3_dbm": 45}}],
            None,
        ) == []

    def test_pout_shortfall_flagged_high(self):
        """Claim +40 dBm, cascade delivers +20 dBm → high-severity shortfall."""
        from services.rf_audit import run_tx_cascade_audit
        components = [
            {"part_number": "DRV1", "category": "RF-Driver",
             "key_specs": {"gain_db": 10, "pout_dbm": 15, "oip3_dbm": 25}},
            {"part_number": "PA1", "category": "RF-PA",
             "key_specs": {"gain_db": 20, "pout_dbm": 25, "oip3_dbm": 35}},
        ]
        dp = {
            "direction": "tx",
            "pout_dbm": 40,  # claim +40 dBm
            "tx_input_power_dbm": -10.0,
        }
        issues = run_tx_cascade_audit(components, dp)
        # Cascade: -10 + 10 + 20 = +20 dBm. Claim 40 dBm → shortfall 20 dB.
        pout_issues = [i for i in issues if i.category == "tx_pout_shortfall"]
        assert len(pout_issues) == 1
        assert pout_issues[0].severity == "high"
        assert "20" in pout_issues[0].detail or "+20" in pout_issues[0].detail

    def test_oip3_shortfall_flagged_high(self):
        """PA OIP3 well below claim → flagged."""
        from services.rf_audit import run_tx_cascade_audit
        components = [
            {"part_number": "PA_LOW_LIN", "category": "RF-PA",
             "key_specs": {"gain_db": 25, "oip3_dbm": 30, "pout_dbm": 35}},
        ]
        dp = {
            "direction": "tx",
            "oip3_dbm": 45,  # claim +45 dBm OIP3 (PA only delivers 30)
            "tx_input_power_dbm": -10.0,
        }
        issues = run_tx_cascade_audit(components, dp)
        oip3 = [i for i in issues if i.category == "tx_oip3_shortfall"]
        assert len(oip3) == 1
        assert oip3[0].severity == "high"

    def test_compression_warning_becomes_critical(self):
        """PA driven past its Pout spec → tx_compression critical issue."""
        from services.rf_audit import run_tx_cascade_audit
        components = [
            {"part_number": "DRV1", "category": "RF-Driver",
             "key_specs": {"gain_db": 20, "pout_dbm": 20}},
            {"part_number": "PA_SMALL", "category": "RF-PA",
             "key_specs": {"gain_db": 25, "pout_dbm": 30, "oip3_dbm": 40}},
        ]
        # Drive into PA: -10 + 20 = +10 dBm; PA output: +10 + 25 = +35 dBm.
        # PA spec Pout=+30 dBm → 5 dB over spec → compression warning.
        dp = {"direction": "tx", "tx_input_power_dbm": -10.0}
        issues = run_tx_cascade_audit(components, dp)
        comp = [i for i in issues if i.category == "tx_compression"]
        assert len(comp) >= 1
        assert comp[0].severity == "critical"
        assert "PA_SMALL" in comp[0].location

    def test_all_claims_met_emits_no_issues(self):
        from services.rf_audit import run_tx_cascade_audit
        components = [
            {"part_number": "DRV1", "category": "RF-Driver",
             "key_specs": {"gain_db": 15, "pout_dbm": 10, "oip3_dbm": 30}},
            {"part_number": "PA_OK", "category": "RF-PA",
             # Pdc sized so system PAE = Pout(100 mW) / Pdc(0.22 W) ≈ 45 %
             "key_specs": {"gain_db": 25, "pout_dbm": 45, "oip3_dbm": 50,
                           "pae_pct": 45, "pdc_w": 0.22}},
        ]
        # Drive = -20 + 15 + 25 = +20 dBm (100 mW). All within spec.
        dp = {
            "direction": "tx",
            "pout_dbm": 20,
            "oip3_dbm": 40,
            "pae_pct": 40,
            "tx_input_power_dbm": -20.0,
        }
        issues = run_tx_cascade_audit(components, dp)
        assert issues == []

    def test_run_all_integrates_tx_cascade(self, monkeypatch):
        """Full run_all on a TX project surfaces the Pout shortfall."""
        monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
        monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
        from services.rf_audit import run_all
        tool_input = {
            "block_diagram_mermaid": (
                "flowchart LR\n"
                "  BB[Baseband] --> DRV[Driver]\n"
                "  DRV --> PA[Class-AB PA]\n"
                "  PA --> HF[Harmonic Filter] --> ANT[Antenna]\n"
            ),
            "component_recommendations": [
                {"part_number": "DRV1", "category": "RF-Driver",
                 "key_specs": {"gain_db": 10, "pout_dbm": 15}},
                {"part_number": "PA1", "category": "RF-PA",
                 "key_specs": {"gain_db": 20, "pout_dbm": 25, "oip3_dbm": 35}},
            ],
            "design_parameters": {
                "direction": "tx",
                "pout_dbm": 40,  # aggressive claim → will fail
                "tx_input_power_dbm": -10.0,
            },
        }
        _, issues = run_all(tool_input, architecture="tx_driver_pa_classab")
        assert any(i.category == "tx_pout_shortfall" for i in issues)

    def test_run_all_skips_tx_cascade_on_rx_project(self, monkeypatch):
        monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
        monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
        from services.rf_audit import run_all
        tool_input = {
            "block_diagram_mermaid": (
                "flowchart LR\n"
                "  ANT[Antenna] --> LNA[LNA] --> MIX[Mixer]\n"
                "  LO[LO] --> MIX\n"
                "  MIX --> IF[IF Filter] --> ADC[ADC]\n"
            ),
            "component_recommendations": [
                {"part_number": "LNA1", "category": "RF-LNA",
                 "key_specs": {"nf_db": 1.5, "gain_db": 15}},
            ],
            "design_parameters": {
                "direction": "rx",
                "noise_figure_db": 3.0,
            },
        }
        _, issues = run_all(tool_input, architecture="superhet_single")
        assert not any(
            i.category in (
                "tx_pout_shortfall", "tx_oip3_shortfall",
                "tx_pae_shortfall", "tx_compression",
            )
            for i in issues
        )


class TestPaThermalWiring:

    def test_run_all_includes_thermal_overrun_on_tx_project(self, monkeypatch):
        monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
        monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
        from services.rf_audit import run_all
        tool_input = {
            "block_diagram_mermaid": (
                "flowchart LR\n"
                "  BB[Baseband] --> DRV[Driver] --> PA[PA]\n"
                "  PA --> HF[Harmonic Filter] --> ANT[Antenna]\n"
            ),
            "component_recommendations": [
                # GaN 50 W at 40 % PAE with the default (bad) heatsink
                # → computed Tj ~ 370 °C → critical overrun
                {"part_number": "QPD1013", "category": "RF-PA",
                 "key_specs": {"technology": "GaN",
                               "pdc_w": 50.0, "pae_pct": 40.0,
                               "pout_dbm": 43}},
            ],
            "design_parameters": {
                "direction": "tx",
                "ambient_temp_c": 25.0,
            },
        }
        _, issues = run_all(tool_input, architecture="tx_driver_pa_classab")
        assert any(i.category == "pa_thermal_overrun" for i in issues)

    def test_run_all_skips_thermal_on_rx_project(self, monkeypatch):
        monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
        monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
        from services.rf_audit import run_all
        # Even with a "PA"-labelled component, RX direction skips the
        # thermal check (receivers don't have a PA anyway).
        tool_input = {
            "block_diagram_mermaid":
                "flowchart LR\n  ANT[Antenna] --> LNA[LNA] --> OUT[Out]",
            "component_recommendations": [
                {"part_number": "LNA1", "category": "RF-LNA",
                 "key_specs": {"nf_db": 1.5, "gain_db": 15}},
            ],
            "design_parameters": {"direction": "rx"},
        }
        _, issues = run_all(tool_input, architecture="std_lna_filter")
        assert not any(
            i.category in ("pa_thermal_overrun", "pa_thermal_derating",
                           "pa_thermal_unknown")
            for i in issues
        )


class TestAcprMaskWiring:

    def test_run_all_flags_acpr_mask_violation(self, monkeypatch):
        monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
        monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
        from services.rf_audit import run_all
        tool_input = {
            "block_diagram_mermaid": (
                "flowchart LR\n"
                "  BB[Baseband] --> DRV[Driver] --> PA[PA]\n"
                "  PA --> HF[Harmonic Filter] --> ANT[Antenna]\n"
            ),
            "component_recommendations": [
                {"part_number": "PA1", "category": "RF-PA",
                 "key_specs": {"gain_db": 20, "pout_dbm": 40}},
            ],
            "design_parameters": {
                "direction": "tx",
                "aclr_dbc": -30,   # way worse than MIL-STD -60
                "spur_mask": "MIL-STD-461",
            },
        }
        _, issues = run_all(tool_input, architecture="tx_driver_pa_classab")
        assert any(i.category == "acpr_mask_violation" for i in issues)

    def test_run_all_skips_mask_check_when_none_selected(self, monkeypatch):
        monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
        monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
        from services.rf_audit import run_all
        tool_input = {
            "block_diagram_mermaid": (
                "flowchart LR\n"
                "  BB[Baseband] --> DRV[Driver] --> PA[PA]\n"
                "  PA --> HF[Harmonic Filter] --> ANT[Antenna]\n"
            ),
            "component_recommendations": [
                {"part_number": "PA1", "category": "RF-PA",
                 "key_specs": {"gain_db": 20, "pout_dbm": 40}},
            ],
            "design_parameters": {
                "direction": "tx",
                "aclr_dbc": -30,
                # no spur_mask
            },
        }
        _, issues = run_all(tool_input, architecture="tx_driver_pa_classab")
        assert not any(
            i.category in ("acpr_mask_violation", "harmonic_mask_violation")
            for i in issues
        )


class TestBomLinkageWiring:

    def test_run_all_flags_missing_bom_part_when_nodes_supplied(self, monkeypatch):
        monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
        monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
        from services.rf_audit import run_all
        tool_input = {
            "block_diagram_mermaid": (
                "flowchart TD\n"
                " ANT[Antenna] --> LNA[LNA]\n"
                " LNA --> MIX[Mixer]\n"
                " LO[LO] --> MIX\n"
                " MIX --> IF[IF Filter]\n"
            ),
            "component_recommendations": [
                {"part_number": "HMC8410LP2FE",
                 "datasheet_url": "https://www.analog.com/en/products/hmc8410.html",
                 "manufacturer": "ADI"},
                {"part_number": "HMC1049LP5E",
                 "datasheet_url": "https://www.analog.com/en/products/hmc1049.html",
                 "manufacturer": "ADI"},
            ],
        }
        nodes = [
            {"reference_designator": "U1", "part_number": "HMC8410LP2FE"},
            # HMC1049LP5E missing from schematic
        ]
        _, issues = run_all(
            tool_input, architecture="superhet_single",
            netlist_nodes=nodes,
        )
        assert any(
            i.category == "bom_missing_in_schematic"
            and "HMC1049LP5E" in i.detail
            for i in issues
        )

    def test_run_all_skips_linkage_when_no_nodes(self, monkeypatch):
        """P1 / P2 / P3 callers don't have a schematic yet — linkage is
        skipped silently rather than flagging the whole BOM."""
        monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
        monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
        from services.rf_audit import run_all
        tool_input = {
            "block_diagram_mermaid": "flowchart TD\n LNA[LNA] --> OUT[Output]",
            "component_recommendations": [{"part_number": "HMC8410LP2FE"}],
        }
        _, issues = run_all(tool_input, architecture="recommend")
        assert not any(
            i.category in ("bom_missing_in_schematic",
                           "schematic_part_not_in_bom",
                           "schematic_node_missing_mpn")
            for i in issues
        )
