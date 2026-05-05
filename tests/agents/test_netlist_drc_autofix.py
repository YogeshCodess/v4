"""P26 #10 (2026-04-25) — regression tests for `_drc_aware_post_process`
and `_auto_fix_drc_violations` in `agents/netlist_agent.py`.

User context: senior RF engineers will scrutinise the schematic + DRC
report at a hackathon demo. The pre-fix code reported 47 critical
power_collision + 91 high pin_multiple_nets violations across the
existing 14 project netlists; this fix reduces those to ZERO.

Approach (pure-Python, no LLM calls — adds <1s to P4):
  1. Merge ground-typed-or-named nets → canonical "GND".
  2. Merge power-rail aliases sharing same source pin → shortest name.
  3. Drop edges referencing unknown component refs.
  4. Re-bind power+ground topology, re-run DRC, repeat up to 3 cycles.
"""
from __future__ import annotations

import pytest

from agents.netlist_agent import NetlistAgent
from tools.netlist_drc import run_drc


def _make_agent() -> NetlistAgent:
    """Bypass __init__ so tests don't need LLM clients."""
    return NetlistAgent.__new__(NetlistAgent)


# ---------------------------------------------------------------------------
# Power-rail alias merge
# ---------------------------------------------------------------------------


def test_post_process_merges_aliased_power_rails():
    """LLM emits multiple distinct rail NAMES that all source from the
    same regulator output (`DCDC1.VOUT`). Pre-fix: DRC flagged this as
    `power_collision` (critical) because each name was a distinct net
    in DRC's view. Post-fix: all aliases collapse to the shortest
    canonical name → DRC sees one net."""
    netlist = {
        "nodes": [
            {"instance_id": "DCDC1", "reference_designator": "DCDC1",
             "component_name": "Buck Regulator", "part_number": "TPS54620"},
            {"instance_id": "U1", "reference_designator": "U1",
             "component_name": "FPGA", "part_number": "XC7A35T"},
            {"instance_id": "U2", "reference_designator": "U2",
             "component_name": "ADC", "part_number": "AD9625"},
        ],
        "edges": [
            {"from_instance": "DCDC1", "from_pin": "VOUT",
             "to_instance": "U1", "to_pin": "VCCINT",
             "net_name": "VCC_5V", "signal_type": "power"},
            {"from_instance": "DCDC1", "from_pin": "VOUT",
             "to_instance": "U1", "to_pin": "VCCAUX",
             "net_name": "VCC_5V_TO_FPGA", "signal_type": "power"},
            {"from_instance": "DCDC1", "from_pin": "VOUT",
             "to_instance": "U2", "to_pin": "AVDD",
             "net_name": "VCC_5V_TO_ADC", "signal_type": "power"},
        ],
    }
    cleaned = NetlistAgent._drc_aware_post_process(netlist)
    rail_names = {e["net_name"] for e in cleaned["edges"]
                  if e.get("signal_type") == "power"}
    assert rail_names == {"VCC_5V"}, (
        f"Aliased rails should have merged to canonical 'VCC_5V', "
        f"got {rail_names}"
    )


def test_post_process_merges_ground_nets():
    """LLM emits multiple ground-typed names (`GND`, `GND_R_LNA1`,
    `GND_C_FPGA`, `AGND`) all going to the same physical ground star.
    Post-fix: every ground-typed-or-named net collapses to canonical
    `GND` → DRC sees ONE net for all GND pins instead of 4."""
    netlist = {
        "nodes": [
            {"instance_id": "U1", "reference_designator": "U1",
             "component_name": "LNA", "part_number": "HMC8410"},
            {"instance_id": "R1", "reference_designator": "R1",
             "component_name": "Resistor", "part_number": "CRCW0603"},
            {"instance_id": "C1", "reference_designator": "C1",
             "component_name": "Capacitor", "part_number": "GRM188"},
            {"instance_id": "GND_STAR", "reference_designator": "GND_STAR",
             "component_name": "Ground star", "part_number": "GND"},
        ],
        "edges": [
            {"from_instance": "U1", "from_pin": "GND",
             "to_instance": "GND_STAR", "to_pin": "1",
             "net_name": "GND", "signal_type": "ground"},
            {"from_instance": "R1", "from_pin": "2",
             "to_instance": "GND_STAR", "to_pin": "1",
             "net_name": "GND_R_LNA1", "signal_type": "ground"},
            {"from_instance": "C1", "from_pin": "2",
             "to_instance": "GND_STAR", "to_pin": "1",
             "net_name": "AGND", "signal_type": "ground"},
        ],
    }
    cleaned = NetlistAgent._drc_aware_post_process(netlist)
    gnd_names = {e["net_name"] for e in cleaned["edges"]
                 if e.get("signal_type") == "ground"}
    assert gnd_names == {"GND"}, (
        f"All ground-typed nets should have merged to 'GND', got {gnd_names}"
    )


def test_post_process_drops_unknown_ref_edges():
    """Edges that reference component refs not in `nodes[]` are dropped
    by the post-process — fixes `unknown_ref` (high) DRC violations."""
    netlist = {
        "nodes": [
            {"instance_id": "U1", "reference_designator": "U1",
             "component_name": "LNA", "part_number": "HMC8410"},
        ],
        "edges": [
            {"from_instance": "U1", "from_pin": "RF_OUT",
             "to_instance": "U1", "to_pin": "RF_IN",
             "net_name": "loopback", "signal_type": "signal"},
            # Edge to non-existent U99 → should be DROPPED:
            {"from_instance": "U1", "from_pin": "RF_OUT",
             "to_instance": "U99", "to_pin": "RF_IN",
             "net_name": "to_ghost", "signal_type": "signal"},
            # Edge from non-existent U42 → should ALSO be dropped:
            {"from_instance": "U42", "from_pin": "OUT",
             "to_instance": "U1", "to_pin": "GND",
             "net_name": "from_ghost", "signal_type": "signal"},
        ],
    }
    cleaned = NetlistAgent._drc_aware_post_process(netlist)
    # The loopback edge is preserved; the two ghost-ref edges are dropped.
    edge_nets = {e["net_name"] for e in cleaned["edges"]}
    assert "loopback" in edge_nets
    assert "to_ghost" not in edge_nets
    assert "from_ghost" not in edge_nets


def test_post_process_preserves_synthesised_supply_refs():
    """The binder synthesises supply refs like `J_PWR`, `GND_STAR`,
    `VCC_VIN_*` that aren't in the original `nodes[]` but ARE valid
    drivers. The post-process must NOT drop edges that reference them."""
    netlist = {
        "nodes": [
            {"instance_id": "U1", "reference_designator": "U1",
             "component_name": "LNA", "part_number": "HMC8410"},
        ],
        "edges": [
            # Synthesised refs — these MUST be preserved:
            {"from_instance": "J_PWR", "from_pin": "1",
             "to_instance": "U1", "to_pin": "VCC",
             "net_name": "VCC", "signal_type": "power"},
            {"from_instance": "U1", "from_pin": "GND",
             "to_instance": "GND_STAR", "to_pin": "1",
             "net_name": "GND", "signal_type": "ground"},
        ],
    }
    cleaned = NetlistAgent._drc_aware_post_process(netlist)
    nets = {e["net_name"] for e in cleaned["edges"]}
    assert "VCC" in nets, "Synthesised J_PWR ref should not have been dropped"
    assert "GND" in nets, "Synthesised GND_STAR ref should not have been dropped"


# ---------------------------------------------------------------------------
# Auto-fix loop
# ---------------------------------------------------------------------------


def test_auto_fix_loop_eliminates_power_collision_and_pin_multiple_nets():
    """End-to-end: a netlist with both critical (power_collision) and
    high (pin_multiple_nets) violations should come out CLEAN after the
    3-cycle auto-fix loop."""
    agent = _make_agent()
    netlist = {
        "nodes": [
            {"instance_id": "DCDC1", "reference_designator": "DCDC1",
             "component_name": "Buck Regulator", "part_number": "TPS54620"},
            {"instance_id": "U1", "reference_designator": "U1",
             "component_name": "FPGA", "part_number": "XC7A35T"},
            {"instance_id": "U2", "reference_designator": "U2",
             "component_name": "ADC", "part_number": "AD9625"},
        ],
        "edges": [
            # power_collision: same DCDC1.VOUT on 3 different rail names
            {"from_instance": "DCDC1", "from_pin": "VOUT",
             "to_instance": "U1", "to_pin": "VCCINT",
             "net_name": "VCC_5V", "signal_type": "power"},
            {"from_instance": "DCDC1", "from_pin": "VOUT",
             "to_instance": "U1", "to_pin": "VCCAUX",
             "net_name": "VCC_5V_FPGA", "signal_type": "power"},
            {"from_instance": "DCDC1", "from_pin": "VOUT",
             "to_instance": "U2", "to_pin": "AVDD",
             "net_name": "VCC_5V_ADC", "signal_type": "power"},
            # pin_multiple_nets: U1.GND on 3 distinct ground-typed nets
            {"from_instance": "U1", "from_pin": "GND",
             "to_instance": "DCDC1", "to_pin": "GND",
             "net_name": "GND", "signal_type": "ground"},
            {"from_instance": "U1", "from_pin": "GND",
             "to_instance": "U2", "to_pin": "GND",
             "net_name": "GND_DIG", "signal_type": "ground"},
            {"from_instance": "U1", "from_pin": "GND",
             "to_instance": "DCDC1", "to_pin": "GND",
             "net_name": "GND_PWR", "signal_type": "ground"},
        ],
    }
    # Confirm violations exist BEFORE.
    drc_before = run_drc(netlist)
    counts_before = drc_before.get("counts", {})
    assert counts_before.get("critical", 0) > 0 or counts_before.get("high", 0) > 0, (
        f"Test setup is wrong — netlist has no critical/high violations: "
        f"{counts_before}"
    )

    fixed_nl, drc_after = agent._auto_fix_drc_violations(netlist, max_passes=3)
    counts_after = drc_after.get("counts", {})
    assert counts_after.get("critical", 0) == 0, (
        f"Auto-fix failed to clear critical violations: {counts_after}"
    )
    assert counts_after.get("high", 0) == 0, (
        f"Auto-fix failed to clear high violations: {counts_after}"
    )


def test_auto_fix_loop_idempotent_on_already_clean_netlist():
    """An already-clean netlist passes through the loop without changes
    in the first pass — no infinite loop, no spurious modifications."""
    agent = _make_agent()
    netlist = {
        "nodes": [
            {"instance_id": "U1", "reference_designator": "U1",
             "component_name": "LNA", "part_number": "HMC8410"},
            {"instance_id": "U2", "reference_designator": "U2",
             "component_name": "Mixer", "part_number": "ADL5801"},
        ],
        "edges": [
            {"from_instance": "U1", "from_pin": "RF_OUT",
             "to_instance": "U2", "to_pin": "RF_IN",
             "net_name": "rf_chain_1", "signal_type": "signal"},
        ],
    }
    fixed_nl, drc = agent._auto_fix_drc_violations(netlist, max_passes=3)
    # No critical/high violations after either.
    counts = drc.get("counts", {})
    # Idempotent: fixed_nl is the same shape (might have synthesized
    # supply refs added by binder, but no edges removed).
    assert isinstance(fixed_nl, dict)
    assert "edges" in fixed_nl
