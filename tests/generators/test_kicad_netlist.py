"""Tests for generators/kicad_netlist.py — P1.4."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from generators.kicad_netlist import (
    infer_net_class,
    netlist_to_kicad,
    save_kicad_netlist,
)


# ---------------------------------------------------------------------------
# Fixture: a small but representative netlist (LNA + mixer + LO + IF + ADC)
# ---------------------------------------------------------------------------

@pytest.fixture
def rf_netlist():
    return {
        "project": "TestRadarRX",
        "nodes": [
            {"id": "U1", "name": "LNA",    "part_number": "HMC8410",
             "manufacturer": "ADI", "footprint": "LP2F-14", "value": "HMC8410"},
            {"id": "U2", "name": "Mixer",  "part_number": "HMC1049LP5E",
             "manufacturer": "ADI", "footprint": "LP5-32"},
            {"id": "U3", "name": "LO PLL", "part_number": "LMX2594",
             "manufacturer": "TI",  "footprint": "WQFN-40"},
            {"id": "U4", "name": "ADC",    "part_number": "AD9208",
             "manufacturer": "ADI", "footprint": "CSPBGA-196"},
        ],
        "edges": [
            {"source": "U1", "source_pin": "2", "target": "U2",
             "target_pin": "3", "signal": "RF_IN", "type": "wire"},
            {"source": "U3", "source_pin": "15", "target": "U2",
             "target_pin": "7", "signal": "LO", "type": "wire"},
            {"source": "U2", "source_pin": "10", "target": "U4",
             "target_pin": "A1", "signal": "IF_OUT", "type": "wire"},
            {"source": "PWR", "source_pin": "1", "target": "U1",
             "target_pin": "14", "signal": "VCC_5V0", "type": "wire"},
            {"source": "PWR", "source_pin": "1", "target": "U4",
             "target_pin": "B1", "signal": "VCC_5V0", "type": "wire"},
        ],
    }


# ---------------------------------------------------------------------------
# Structural shape
# ---------------------------------------------------------------------------

def test_rendered_netlist_has_kicad_header(rf_netlist):
    s = netlist_to_kicad(rf_netlist)
    assert s.startswith("(export (version ")
    assert s.strip().endswith(")")


def test_design_section_includes_project_name(rf_netlist):
    s = netlist_to_kicad(rf_netlist)
    assert "TestRadarRX.sch" in s


def test_components_section_lists_every_node(rf_netlist):
    s = netlist_to_kicad(rf_netlist)
    for ref in ("U1", "U2", "U3", "U4"):
        assert f'(ref "{ref}")' in s
    assert '(footprint "LP2F-14")' in s


def test_components_section_emits_mpn_and_manufacturer(rf_netlist):
    s = netlist_to_kicad(rf_netlist)
    assert '(property (name "MPN") (value "HMC8410"))' in s
    assert '(property (name "Manufacturer") (value "ADI"))' in s


# ---------------------------------------------------------------------------
# Net groups
# ---------------------------------------------------------------------------

def test_nets_group_is_emitted_per_unique_signal(rf_netlist):
    s = netlist_to_kicad(rf_netlist)
    # Each unique net appears as one `(net ...)` block
    net_names = re.findall(r'\(net \(code \d+\) \(name "([^"]+)"\)', s)
    assert set(net_names) == {"RF_IN", "LO", "IF_OUT", "VCC_5V0"}


def test_multi_drop_net_dedupes_endpoints(rf_netlist):
    s = netlist_to_kicad(rf_netlist)
    # VCC_5V0 connects PWR→U1 and PWR→U4 — PWR pin1 should appear once.
    vcc_block = re.search(
        r'\(net \(code \d+\) \(name "VCC_5V0"\)(.*?)\n\s*\)\n',
        s, flags=re.DOTALL,
    )
    assert vcc_block is not None
    pwr_refs = re.findall(r'\(node \(ref "PWR"\) \(pin "1"\)\)', vcc_block.group(1))
    assert len(pwr_refs) == 1


def test_each_net_has_netclass_property(rf_netlist):
    s = netlist_to_kicad(rf_netlist)
    # Every `(net ...)` block must carry a netclass property
    net_blocks = re.findall(
        r'\(net \(code \d+\) \(name "[^"]+"\)(.*?)\n\s*\)\n',
        s, flags=re.DOTALL,
    )
    assert net_blocks
    for body in net_blocks:
        assert "netclass" in body


# ---------------------------------------------------------------------------
# Net-class classifier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("VCC_3V3",    "POWER"),
    ("VDD",        "POWER"),
    ("VBAT",       "POWER"),
    ("GND",        "GND"),
    ("AGND",       "GND"),
    ("SCLK",       "CLK"),
    ("MCLK_100",   "CLK"),
    ("RF_IN",      "RF_50OHM"),
    ("ANT_IN",     "RF_50OHM"),
    ("LO",         "RF_50OHM"),
    ("USB_DP_P",   "DIFF_PAIR"),
    ("GPIO_5",     "SIGNAL"),
])
def test_infer_net_class_bins(name, expected):
    assert infer_net_class(name) == expected


def test_infer_net_class_trusts_explicit_signal_type():
    # Even if the name looks generic, `signal_type: "power"` wins.
    assert infer_net_class("NET_42", signal_type="power") == "POWER"
    assert infer_net_class("NET_42", signal_type="ground") == "GND"
    assert infer_net_class("NET_42", signal_type="differential") == "DIFF_PAIR"


# ---------------------------------------------------------------------------
# Defensive handling of malformed input
# ---------------------------------------------------------------------------

def test_empty_netlist_returns_valid_skeleton():
    s = netlist_to_kicad({})
    assert "(export (version " in s
    assert "(components" in s
    assert "(nets" in s


def test_edge_without_signal_is_skipped():
    nl = {
        "project": "X",
        "nodes": [{"id": "U1"}],
        "edges": [{"source": "U1", "source_pin": "1",
                   "target": "U2", "target_pin": "1"}],  # no signal
    }
    s = netlist_to_kicad(nl)
    # No nets listed for this edge, but components section still intact.
    assert '(ref "U1")' in s
    assert "(name \"\")" not in s  # empty-string nets must not leak


def test_non_alphanumeric_ref_is_sanitised():
    nl = {
        "project": "X",
        "nodes": [{"id": "U 1!", "name": "foo"}],
        "edges": [],
    }
    s = netlist_to_kicad(nl)
    # Non-alphanumeric chars stripped
    assert '(ref "U1")' in s


# ---------------------------------------------------------------------------
# save_kicad_netlist
# ---------------------------------------------------------------------------

def test_save_kicad_netlist_writes_file(tmp_path: Path, rf_netlist):
    out = save_kicad_netlist(rf_netlist, tmp_path)
    assert out.name == "netlist.net"
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert content.startswith("(export (version ")
    assert "HMC8410" in content
