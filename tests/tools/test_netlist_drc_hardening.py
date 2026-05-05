"""P1.5 + P2.7 — regression tests for the new DRC rules:

  - `dangling_power_rail` — rail with <2 endpoints (nothing driving it)
  - `power_rail_no_driver` — rail has endpoints but no PWR*/REG*/LDO*/CONN* ref
  - `cdc_boundary_undeclared` — ≥2 clock domains with no CDC cell in BOM
"""
from __future__ import annotations

import pytest

from tools.netlist_drc import run_drc


def _v(result, rule):
    return [x for x in result["violations"] if x["rule"] == rule]


# ---------------------------------------------------------------------------
# P1.5 — dangling_power_rail
# ---------------------------------------------------------------------------

class TestDanglingPowerRail:

    def test_single_endpoint_power_rail_flagged_high(self):
        """An IC with a VCC pin connected to a net that no regulator
        drives — classic integration bug."""
        nl = {
            "edges": [{
                "net_name": "VCC_3V3", "from_instance": "U1",
                "from_pin": "11", "signal_type": "power",
            }],
            "power_nets": ["VCC_3V3"],
        }
        r = run_drc(nl)
        danglings = _v(r, "dangling_power_rail")
        assert len(danglings) == 1
        assert danglings[0]["severity"] == "high"
        assert "VCC_3V3" in danglings[0]["detail"]

    def test_two_endpoints_with_driver_passes(self):
        """Regulator output drives the IC — legitimate."""
        nl = {
            "nodes": [{"id": "U1"}, {"id": "PWR1"}],
            "edges": [
                {"net_name": "VCC_3V3", "from_instance": "PWR1",
                 "from_pin": "1", "to_instance": "U1", "to_pin": "11",
                 "signal_type": "power"},
                {"net_name": "VCC_3V3", "from_instance": "U1",
                 "from_pin": "11", "to_instance": "C1", "to_pin": "1",
                 "signal_type": "power"},
            ],
        }
        r = run_drc(nl)
        assert _v(r, "dangling_power_rail") == []
        assert _v(r, "power_rail_no_driver") == []

    def test_inferred_rail_without_signal_type(self):
        """A net NAMED like a power rail (VCC_*) but without
        signal_type set should still be checked."""
        nl = {
            "edges": [{
                "net_name": "VCC_5V0", "from_instance": "U1",
                "from_pin": "1",
                # no signal_type
            }],
        }
        r = run_drc(nl)
        assert any(
            x["rule"] == "dangling_power_rail" and "VCC_5V0" in x["detail"]
            for x in r["violations"]
        )


class TestPowerRailNoDriver:

    def test_no_driver_ref_flagged_medium(self):
        """Rail has two IC endpoints — no PWR*/REG*/CONN* reference."""
        nl = {
            "edges": [
                {"net_name": "VCC_3V3", "from_instance": "U1",
                 "from_pin": "11", "to_instance": "U2", "to_pin": "22",
                 "signal_type": "power"},
            ],
        }
        r = run_drc(nl)
        drivers = _v(r, "power_rail_no_driver")
        assert len(drivers) == 1
        assert drivers[0]["severity"] == "medium"

    def test_connector_ref_counts_as_driver(self):
        nl = {
            "nodes": [{"id": "U1"}, {"id": "CONN1"}],
            "edges": [
                {"net_name": "VCC_3V3", "from_instance": "CONN1",
                 "from_pin": "1", "to_instance": "U1", "to_pin": "11",
                 "signal_type": "power"},
            ],
        }
        r = run_drc(nl)
        assert _v(r, "power_rail_no_driver") == []


# ---------------------------------------------------------------------------
# P2.7 — CDC boundary undeclared
# ---------------------------------------------------------------------------

class TestCdcBoundary:

    def test_two_clock_domains_no_cdc_cell_flagged_medium(self):
        """Design references 2 distinct clock nets — no FIFO / 2FF sync
        in the BOM. Metastability risk."""
        nl = {
            "nodes": [
                {"id": "U1", "part_number": "STM32F407", "component_name": "MCU"},
                {"id": "U2", "part_number": "AD9208", "component_name": "ADC"},
            ],
            "edges": [
                {"net_name": "CLK_50MHZ", "from_instance": "OSC1",
                 "from_pin": "1", "to_instance": "U1", "to_pin": "7",
                 "signal_type": "clock"},
                {"net_name": "CLK_100MHZ", "from_instance": "PLL1",
                 "from_pin": "1", "to_instance": "U2", "to_pin": "A1",
                 "signal_type": "clock"},
                {"net_name": "DATA", "from_instance": "U2",
                 "from_pin": "A2", "to_instance": "U1", "to_pin": "8",
                 "signal_type": "signal"},
            ],
        }
        r = run_drc(nl)
        cdcs = _v(r, "cdc_boundary_undeclared")
        assert len(cdcs) == 1
        assert cdcs[0]["severity"] == "medium"
        assert "CLK_50MHZ" in cdcs[0]["detail"]
        assert "CLK_100MHZ" in cdcs[0]["detail"]

    def test_fifo_in_bom_suppresses_warning(self):
        """If a CDC FIFO / synchroniser is in the BOM, the advisory
        doesn't fire."""
        nl = {
            "nodes": [
                {"id": "U1", "part_number": "STM32F407", "component_name": "MCU"},
                {"id": "U2", "part_number": "AD9208", "component_name": "ADC"},
                {"id": "U3", "part_number": "SN74LVC1G80",
                 "description": "D-type flip flop CDC 2FF synchronizer"},
            ],
            "edges": [
                {"net_name": "CLK_A", "from_instance": "OSC1", "from_pin": "1",
                 "to_instance": "U1", "to_pin": "1", "signal_type": "clock"},
                {"net_name": "CLK_B", "from_instance": "OSC2", "from_pin": "1",
                 "to_instance": "U2", "to_pin": "1", "signal_type": "clock"},
            ],
        }
        r = run_drc(nl)
        assert _v(r, "cdc_boundary_undeclared") == []

    def test_single_clock_domain_does_not_fire(self):
        nl = {
            "edges": [
                {"net_name": "CLK", "from_instance": "OSC1", "from_pin": "1",
                 "to_instance": "U1", "to_pin": "1", "signal_type": "clock"},
            ],
        }
        r = run_drc(nl)
        assert _v(r, "cdc_boundary_undeclared") == []

    def test_clock_nets_detected_by_name_pattern(self):
        """Nets named CLK*/SCLK/MCLK count as clock domains even when
        signal_type wasn't populated."""
        nl = {
            "nodes": [
                {"id": "U1", "part_number": "A"},
                {"id": "U2", "part_number": "B"},
            ],
            "edges": [
                # signal_type omitted — must still be detected as a clock
                {"net_name": "SCLK", "from_instance": "OSC1", "from_pin": "1",
                 "to_instance": "U1", "to_pin": "1"},
                {"net_name": "MCLK", "from_instance": "OSC2", "from_pin": "1",
                 "to_instance": "U2", "to_pin": "1"},
            ],
        }
        r = run_drc(nl)
        assert _v(r, "cdc_boundary_undeclared") != []


# ---------------------------------------------------------------------------
# checks_run list advertises the new rules
# ---------------------------------------------------------------------------

def test_checks_run_lists_new_rules():
    r = run_drc({})
    assert "dangling_power_rail" in r["checks_run"]
    assert "power_rail_no_driver" in r["checks_run"]
    assert "cdc_boundary_undeclared" in r["checks_run"]
