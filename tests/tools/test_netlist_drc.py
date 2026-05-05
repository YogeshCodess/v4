"""Tests for tools/netlist_drc.py — P2.7."""
from __future__ import annotations

import pytest

from tools.netlist_drc import run_drc


def _violation_rules(result: dict) -> set[str]:
    return {v["rule"] for v in result["violations"]}


# ---------------------------------------------------------------------------
# Empty / minimal input
# ---------------------------------------------------------------------------

def test_empty_netlist_has_no_violations():
    r = run_drc({})
    assert r["violations"] == []
    assert r["counts"]["critical"] == 0
    assert r["overall_pass"] is True


# ---------------------------------------------------------------------------
# Shorts
# ---------------------------------------------------------------------------

def test_power_and_ground_name_collision_flagged_critical():
    nl = {
        "edges": [
            {"net_name": "VCC_5V0", "from_instance": "U1", "from_pin": "1",
             "to_instance": "C1", "to_pin": "1", "signal_type": "power"},
        ],
        "power_nets": ["VCC_5V0"],
        "ground_nets": ["VCC_5V0"],   # declared as both — short!
    }
    r = run_drc(nl)
    assert "short" in _violation_rules(r)
    assert r["counts"]["critical"] >= 1


# ---------------------------------------------------------------------------
# Power collisions on a pin
# ---------------------------------------------------------------------------

def test_pin_on_two_power_nets_flagged_critical():
    nl = {
        "nodes": [{"id": "U1"}],
        "edges": [
            {"net_name": "VCC_3V3", "from_instance": "PWR1", "from_pin": "1",
             "to_instance": "U1", "to_pin": "11"},
            {"net_name": "VCC_5V0", "from_instance": "PWR2", "from_pin": "1",
             "to_instance": "U1", "to_pin": "11"},  # same pin, different rail
        ],
    }
    r = run_drc(nl)
    rules = _violation_rules(r)
    assert "power_collision" in rules


# ---------------------------------------------------------------------------
# Floating signal nets
# ---------------------------------------------------------------------------

def test_floating_signal_net_flagged_high():
    nl = {
        "edges": [
            {"net_name": "GPIO_1", "from_instance": "U1", "from_pin": "4",
             "signal_type": "signal"},
            # No receiver — only one endpoint on GPIO_1
        ],
    }
    r = run_drc(nl)
    assert "floating_net" in _violation_rules(r)


def test_signal_net_with_two_endpoints_is_not_floating():
    nl = {
        "edges": [
            {"net_name": "GPIO_1", "from_instance": "U1", "from_pin": "4",
             "to_instance": "U2", "to_pin": "10", "signal_type": "signal"},
        ],
    }
    r = run_drc(nl)
    assert "floating_net" not in _violation_rules(r)


def test_power_nets_are_not_flagged_as_floating():
    """Power traces frequently have a single endpoint per segment — the
    floating-net rule must exempt them."""
    nl = {
        "edges": [
            {"net_name": "VCC_3V3", "from_instance": "U1", "from_pin": "11",
             "signal_type": "power"},
        ],
    }
    r = run_drc(nl)
    assert "floating_net" not in _violation_rules(r)


# ---------------------------------------------------------------------------
# Power naming
# ---------------------------------------------------------------------------

def test_bad_power_naming_flagged_low():
    nl = {
        "edges": [
            {"net_name": "SUPPLY_VOLTAGE_ALPHA", "from_instance": "U1",
             "from_pin": "11", "to_instance": "C1", "to_pin": "1",
             "signal_type": "power"},
        ],
    }
    r = run_drc(nl)
    assert "power_naming" in _violation_rules(r)


def test_standard_rail_naming_passes():
    nl = {
        "edges": [
            {"net_name": "VCC_3V3", "from_instance": "U1",
             "from_pin": "11", "to_instance": "C1", "to_pin": "1",
             "signal_type": "power"},
        ],
    }
    r = run_drc(nl)
    assert "power_naming" not in _violation_rules(r)


# ---------------------------------------------------------------------------
# Decoupling hint
# ---------------------------------------------------------------------------

def test_power_net_without_decoupling_capacitor_flagged_medium():
    nl = {
        "edges": [
            {"net_name": "VCC_3V3", "from_instance": "PWR", "from_pin": "1",
             "to_instance": "U1", "to_pin": "11", "signal_type": "power"},
        ],
    }
    r = run_drc(nl)
    assert "missing_decap" in _violation_rules(r)


def test_power_net_with_decoupling_cap_passes():
    nl = {
        "edges": [
            {"net_name": "VCC_3V3", "from_instance": "PWR", "from_pin": "1",
             "to_instance": "U1", "to_pin": "11", "signal_type": "power"},
            {"net_name": "VCC_3V3", "from_instance": "U1", "from_pin": "11",
             "to_instance": "C1", "to_pin": "1", "signal_type": "power"},
        ],
    }
    r = run_drc(nl)
    assert "missing_decap" not in _violation_rules(r)


# ---------------------------------------------------------------------------
# Unknown references
# ---------------------------------------------------------------------------

def test_unknown_ref_in_edges_flagged_high():
    nl = {
        "nodes": [{"id": "U1"}],  # only U1 declared
        "edges": [
            {"net_name": "NET_A", "from_instance": "U1", "from_pin": "1",
             "to_instance": "U_GHOST", "to_pin": "1", "signal_type": "signal"},
        ],
    }
    r = run_drc(nl)
    assert "unknown_ref" in _violation_rules(r)
    unknown_issues = [v for v in r["violations"] if v["rule"] == "unknown_ref"]
    assert any("U_GHOST" in v["detail"] for v in unknown_issues)


def test_known_refs_do_not_trigger_unknown_ref():
    nl = {
        "nodes": [{"id": "U1"}, {"id": "U2"}],
        "edges": [
            {"net_name": "NET_A", "from_instance": "U1", "from_pin": "1",
             "to_instance": "U2", "to_pin": "1", "signal_type": "signal"},
        ],
    }
    r = run_drc(nl)
    assert "unknown_ref" not in _violation_rules(r)


# ---------------------------------------------------------------------------
# Overall pass flag
# ---------------------------------------------------------------------------

def test_overall_pass_true_when_only_low_violations():
    nl = {
        "edges": [
            {"net_name": "WEIRD_RAIL", "from_instance": "PWR", "from_pin": "1",
             "to_instance": "C1", "to_pin": "1", "signal_type": "power"},
        ],
    }
    r = run_drc(nl)
    # Only `power_naming` (low) + `missing_decap` (medium) — overall_pass
    # should still be True because neither is critical/high.
    assert r["overall_pass"] is True
    assert r["counts"]["critical"] == 0
    assert r["counts"]["high"] == 0


def test_overall_pass_false_on_critical():
    nl = {
        "power_nets": ["VCC"], "ground_nets": ["VCC"],
        "edges": [
            {"net_name": "VCC", "from_instance": "PWR", "from_pin": "1",
             "to_instance": "U1", "to_pin": "1", "signal_type": "power"},
        ],
    }
    r = run_drc(nl)
    assert r["overall_pass"] is False
