"""Regression tests for the 6-bug netlist/schematic pipeline fix pass:

Bug #1 — run_schematic_drc wired into netlist_agent (both LLM-success and
         BOM-fallback paths now produce `schematic_drc.json`).
Bug #2 — floating-pin closure covers 5 termination cases (diff-N / status
         output / active-low control / generic input / generic output).
Bug #3 — IF cross-sheet OPC gives each polarity its own pin.
Bug #4 — LO cross-sheet OPC gives each polarity its own pin.
Bug #5 — `pin_multiple_nets` DRC rule + `flatten_schematic_to_netlist` adapter.
Bug #6 — topology `_role_of` no longer force-classifies passives / RF blocks
         as ICs (so they don't get auto-VCC/GND edges).

These tests focus on the DRC layer + small agent helpers that are pure
functions, so they run without an LLM round-trip.
"""
from __future__ import annotations

import pytest

from tools.netlist_drc import (
    flatten_schematic_to_netlist,
    run_drc,
    run_schematic_drc,
)


def _v(result, rule):
    return [x for x in result["violations"] if x["rule"] == rule]


# ---------------------------------------------------------------------------
# Bug #5 — pin_multiple_nets DRC rule (signal-level short hunt)
# ---------------------------------------------------------------------------

class TestPinMultipleNets:

    def test_pin_on_two_signal_nets_flagged_high(self):
        """A single pin sees two distinct signal nets — classic short
        that the old power-only rule missed."""
        nl = {
            "nodes": [{"id": "U1"}, {"id": "U2"}, {"id": "U3"}],
            "edges": [
                # IF_OUT_P net lands on U_OPC pin 1
                {"net_name": "IF_OUT_P", "from_instance": "U1",
                 "from_pin": "2", "to_instance": "U_OPC", "to_pin": "1",
                 "signal_type": "signal"},
                # IF_OUT_N net lands on the SAME U_OPC pin 1 — aliased
                {"net_name": "IF_OUT_N", "from_instance": "U1",
                 "from_pin": "3", "to_instance": "U_OPC", "to_pin": "1",
                 "signal_type": "signal"},
            ],
        }
        r = run_drc(nl)
        hits = _v(r, "pin_multiple_nets")
        assert len(hits) == 1
        assert hits[0]["severity"] == "high"
        assert "U_OPC.1" in hits[0]["location"]
        assert "IF_OUT_P" in hits[0]["detail"]
        assert "IF_OUT_N" in hits[0]["detail"]

    def test_fanout_on_single_net_does_not_fire(self):
        """A single net can fan-out to N endpoints on one pin — that
        is not a short; only multiple distinct NETS is."""
        nl = {
            "nodes": [{"id": "U1"}, {"id": "U2"}, {"id": "U3"}],
            "edges": [
                # CLK driven by OSC1, reaches U1 and U2 from the same net.
                {"net_name": "CLK", "from_instance": "OSC1", "from_pin": "1",
                 "to_instance": "U1", "to_pin": "7",
                 "signal_type": "clock"},
                {"net_name": "CLK", "from_instance": "OSC1", "from_pin": "1",
                 "to_instance": "U2", "to_pin": "7",
                 "signal_type": "clock"},
            ],
        }
        r = run_drc(nl)
        assert _v(r, "pin_multiple_nets") == []

    def test_power_collision_suppresses_pin_multiple_nets(self):
        """When two power rails short on a pin, the `power_collision`
        rule (critical) owns it — the generic `pin_multiple_nets` rule
        must not double-flag."""
        nl = {
            "nodes": [{"id": "U1"}],
            "edges": [
                {"net_name": "VCC_3V3", "from_instance": "PWR1",
                 "from_pin": "1", "to_instance": "U1", "to_pin": "11",
                 "signal_type": "power"},
                {"net_name": "VCC_5V0", "from_instance": "PWR2",
                 "from_pin": "1", "to_instance": "U1", "to_pin": "11",
                 "signal_type": "power"},
            ],
        }
        r = run_drc(nl)
        assert _v(r, "power_collision") != []
        # Generic rule must NOT double-flag the same pin.
        assert _v(r, "pin_multiple_nets") == []

    def test_checks_run_advertises_new_rule(self):
        r = run_drc({})
        assert "pin_multiple_nets" in r["checks_run"]


# ---------------------------------------------------------------------------
# Bug #5 (partner) — flatten_schematic_to_netlist adapter
# ---------------------------------------------------------------------------

class TestFlattenSchematic:

    def test_star_net_becomes_nminus1_edges(self):
        """A net with N endpoints unfolds into N-1 edges that share the
        same `net_name` so DRC can key on it."""
        schem = {
            "sheets": [{
                "id": "SH1",
                "components": [
                    {"ref": "U1", "pins": []},
                    {"ref": "U2", "pins": []},
                    {"ref": "U3", "pins": []},
                ],
                "nets": [{
                    "name": "DATA",
                    "type": "signal",
                    "endpoints": [
                        {"ref": "U1", "pin": "1"},
                        {"ref": "U2", "pin": "2"},
                        {"ref": "U3", "pin": "3"},
                    ],
                }],
            }],
        }
        flat = flatten_schematic_to_netlist(schem)
        data_edges = [e for e in flat["edges"] if e["net_name"] == "DATA"]
        # 3 endpoints → 2 edges (star anchored on first endpoint).
        assert len(data_edges) == 2
        # All edges share the same net_name so pin_to_nets groups them.
        assert all(e["net_name"] == "DATA" for e in data_edges)

    def test_single_endpoint_net_becomes_half_edge(self):
        """A net with exactly one endpoint becomes a half-edge so the
        floating-net rule still fires over the flattened form."""
        schem = {
            "sheets": [{
                "id": "SH1",
                "components": [{"ref": "U1", "pins": []}],
                "nets": [{
                    "name": "GPIO_UNUSED",
                    "type": "signal",
                    "endpoints": [{"ref": "U1", "pin": "4"}],
                }],
            }],
        }
        flat = flatten_schematic_to_netlist(schem)
        half = [e for e in flat["edges"] if e["net_name"] == "GPIO_UNUSED"]
        assert len(half) == 1
        assert half[0]["from_instance"] == "U1"
        assert half[0]["to_instance"] is None

    def test_power_and_ground_nets_classified(self):
        schem = {
            "sheets": [{
                "id": "SH1",
                "components": [
                    {"ref": "U1", "pins": []}, {"ref": "C1", "pins": []},
                ],
                "nets": [
                    {"name": "VCC_3V3", "type": "power",
                     "endpoints": [{"ref": "U1", "pin": "11"},
                                   {"ref": "C1", "pin": "1"}]},
                    {"name": "GND", "type": "ground",
                     "endpoints": [{"ref": "U1", "pin": "12"},
                                   {"ref": "C1", "pin": "2"}]},
                ],
            }],
        }
        flat = flatten_schematic_to_netlist(schem)
        assert "VCC_3V3" in flat["power_nets"]
        assert "GND" in flat["ground_nets"]

    def test_empty_schematic_flattens_cleanly(self):
        flat = flatten_schematic_to_netlist({})
        assert flat == {
            "nodes": [], "edges": [], "power_nets": [], "ground_nets": [],
        }


# ---------------------------------------------------------------------------
# Bug #1 — run_schematic_drc wrapper: source tag + sheet count + pass-through
# ---------------------------------------------------------------------------

class TestRunSchematicDrc:

    def test_result_is_tagged_post_synthesis(self):
        r = run_schematic_drc({"sheets": []})
        assert r["source"] == "schematic_post_synthesis"
        assert r["sheet_count"] == 0

    def test_sheet_count_reflects_input(self):
        schem = {
            "sheets": [
                {"id": "SH1", "components": [], "nets": []},
                {"id": "SH2", "components": [], "nets": []},
                {"id": "SH3", "components": [], "nets": []},
            ]
        }
        r = run_schematic_drc(schem)
        assert r["sheet_count"] == 3

    def test_aliased_opc_pin_surfaces_in_schematic_drc(self):
        """End-to-end: the Bug-#3/#4 aliasing pattern (IF_OUT_P + IF_OUT_N
        on the same OPC pin) must trip `pin_multiple_nets` when the
        schematic is flattened + validated."""
        schem = {
            "sheets": [{
                "id": "SH1",
                "components": [
                    {"ref": "U_MIXER", "pins": []},
                    {"ref": "U_OPC", "pins": []},
                ],
                "nets": [
                    {"name": "IF_OUT_P", "type": "signal",
                     "endpoints": [
                         {"ref": "U_MIXER", "pin": "5"},
                         {"ref": "U_OPC", "pin": "1"},  # shared — bug!
                     ]},
                    {"name": "IF_OUT_N", "type": "signal",
                     "endpoints": [
                         {"ref": "U_MIXER", "pin": "6"},
                         {"ref": "U_OPC", "pin": "1"},  # shared — bug!
                     ]},
                ],
            }],
        }
        r = run_schematic_drc(schem)
        assert r["overall_pass"] is False
        hits = _v(r, "pin_multiple_nets")
        assert any("U_OPC.1" in h["location"] for h in hits)
        # Detail must mention both aliased nets to guide the engineer.
        assert any("IF_OUT_P" in h["detail"] and "IF_OUT_N" in h["detail"]
                   for h in hits)


# ---------------------------------------------------------------------------
# Bug #3 / #4 fixed shape — separate P/N pins pass DRC cleanly
# ---------------------------------------------------------------------------

class TestDiffPairSeparatePins:

    def test_if_pair_on_separate_pins_passes_pin_check(self):
        """IF_OUT_P lands on OPC pin 1, IF_OUT_N on OPC pin 2 — no
        pin_multiple_nets violation. This is the fixed topology."""
        schem = {
            "sheets": [{
                "id": "SH1",
                "components": [
                    {"ref": "U_MIXER", "pins": []},
                    {"ref": "U_OPC", "pins": []},
                ],
                "nets": [
                    {"name": "IF_OUT_P", "type": "signal",
                     "endpoints": [
                         {"ref": "U_MIXER", "pin": "5"},
                         {"ref": "U_OPC", "pin": "1"},
                     ]},
                    {"name": "IF_OUT_N", "type": "signal",
                     "endpoints": [
                         {"ref": "U_MIXER", "pin": "6"},
                         {"ref": "U_OPC", "pin": "2"},
                     ]},
                ],
            }],
        }
        r = run_schematic_drc(schem)
        assert _v(r, "pin_multiple_nets") == []

    def test_lo_pair_on_separate_pins_passes_pin_check(self):
        schem = {
            "sheets": [{
                "id": "SH1",
                "components": [
                    {"ref": "U_SYNTH", "pins": []},
                    {"ref": "U_OPC_LO", "pins": []},
                ],
                "nets": [
                    {"name": "LO_P", "type": "signal",
                     "endpoints": [
                         {"ref": "U_SYNTH", "pin": "10"},
                         {"ref": "U_OPC_LO", "pin": "1"},
                     ]},
                    {"name": "LO_N", "type": "signal",
                     "endpoints": [
                         {"ref": "U_SYNTH", "pin": "11"},
                         {"ref": "U_OPC_LO", "pin": "2"},
                     ]},
                ],
            }],
        }
        r = run_schematic_drc(schem)
        assert _v(r, "pin_multiple_nets") == []


# ---------------------------------------------------------------------------
# Bug #6 — topology pass no longer injects VCC/GND edges for passives/RF blocks
# ---------------------------------------------------------------------------
# `_role_of` is a local closure inside `_enforce_power_ground_topology`; we
# exercise its behaviour by running the @staticmethod on a crafted netlist
# and checking the output `power_map` and emitted `edges`.

class TestTopologyNoSyntheticSupplyForPassives:

    def _edges_for(self, netlist, ref):
        return [e for e in netlist.get("edges", [])
                if e.get("to_instance") == ref
                or e.get("from_instance") == ref]

    def test_resistor_ref_no_synthetic_vcc_gnd(self):
        """R12 has no explicit power/ground pins. The pre-fix code
        invented VCC + GND edges for it; the fix must NOT."""
        from agents.netlist_agent import NetlistAgent
        nl = {
            "nodes": [
                {"instance_id": "REG1", "component_name": "LDO regulator",
                 "part_number": "LP5907"},
                {"instance_id": "R12", "component_name": "0603 resistor",
                 "part_number": "R_10K"},
            ],
            "edges": [],
        }
        out = NetlistAgent._enforce_power_ground_topology(nl)
        # R12 must not be in the auto-generated power_map.
        assert "R12" not in out.get("power_map", {})
        # No power or ground edge should mention R12.
        r_edges = self._edges_for(out, "R12")
        assert all(e.get("signal_type") not in ("power", "ground")
                   for e in r_edges)

    def test_capacitor_ref_no_synthetic_vcc_gnd(self):
        from agents.netlist_agent import NetlistAgent
        nl = {
            "nodes": [
                {"instance_id": "REG1", "component_name": "LDO regulator",
                 "part_number": "LP5907"},
                {"instance_id": "C5", "component_name": "0603 capacitor",
                 "part_number": "C_100N"},
            ],
            "edges": [],
        }
        out = NetlistAgent._enforce_power_ground_topology(nl)
        assert "C5" not in out.get("power_map", {})

    def test_rf_lna_description_no_synthetic_vcc_gnd(self):
        """An LNA block without explicit power pins should NOT get
        auto-VCC/GND edges fabricated."""
        from agents.netlist_agent import NetlistAgent
        nl = {
            "nodes": [
                {"instance_id": "REG1", "component_name": "LDO regulator",
                 "part_number": "LP5907"},
                {"instance_id": "U_LNA1",
                 "component_name": "LNA low noise amplifier",
                 "part_number": "HMC1049"},
            ],
            "edges": [],
        }
        out = NetlistAgent._enforce_power_ground_topology(nl)
        assert "U_LNA1" not in out.get("power_map", {})

    def test_mixer_description_no_synthetic_vcc_gnd(self):
        from agents.netlist_agent import NetlistAgent
        nl = {
            "nodes": [
                {"instance_id": "REG1", "component_name": "LDO regulator",
                 "part_number": "LP5907"},
                {"instance_id": "U_MX1",
                 "component_name": "Mixer downconverter",
                 "part_number": "HMC558"},
            ],
            "edges": [],
        }
        out = NetlistAgent._enforce_power_ground_topology(nl)
        assert "U_MX1" not in out.get("power_map", {})

    def test_generic_ic_still_gets_synthetic_vcc_gnd(self):
        """A generic IC with no pin list is still expected to get
        auto-VCC/GND defaults — the fix is scoped to passives/RF blocks."""
        from agents.netlist_agent import NetlistAgent
        nl = {
            "nodes": [
                {"instance_id": "REG1", "component_name": "LDO regulator",
                 "part_number": "LP5907"},
                {"instance_id": "U1",
                 "component_name": "microcontroller",
                 "part_number": "STM32F407"},
            ],
            "edges": [],
        }
        out = NetlistAgent._enforce_power_ground_topology(nl)
        pm = out.get("power_map", {})
        # Pre-existing behaviour preserved: IC gets default VCC + GND bind.
        assert "U1" in pm
        assert "VCC" in pm["U1"]
        assert "GND" in pm["U1"]

    def test_active_mixer_with_declared_vcc_pin_still_bound(self):
        """Edge case: an active mixer that DOES declare a VCC pin in
        its pin list should still get its power edge bound — the
        skip-rule only fires when vcc_pins AND gnd_pins are both empty."""
        from agents.netlist_agent import NetlistAgent
        nl = {
            "nodes": [
                {"instance_id": "REG1", "component_name": "LDO regulator",
                 "part_number": "LP5907"},
                {"instance_id": "U_MX_ACTIVE",
                 "component_name": "active mixer",
                 "part_number": "HMC558",
                 "pins": [
                     {"pin_name": "VCC"},
                     {"pin_name": "GND"},
                     {"pin_name": "RF_IN"},
                 ]},
            ],
            "edges": [],
        }
        out = NetlistAgent._enforce_power_ground_topology(nl)
        pm = out.get("power_map", {})
        assert "U_MX_ACTIVE" in pm
        assert "VCC" in pm["U_MX_ACTIVE"]
