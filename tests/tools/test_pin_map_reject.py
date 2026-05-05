"""P1.6 — `reject_invalid_components` + `component_should_reject` tests."""
from __future__ import annotations

import pytest

from tools.pin_map import (
    component_should_reject,
    reject_invalid_components,
)


# ---------------------------------------------------------------------------
# component_should_reject
# ---------------------------------------------------------------------------

def test_should_reject_on_critical_invalid_pin_number():
    assert component_should_reject([{
        "severity": "critical", "category": "invalid_pin_number",
        "detail": "Pin 100 on SOT-89-3",
    }])


def test_should_reject_on_high_pin_name_mismatch():
    """The P1.6 promotion from medium → high means mismatches now reject."""
    assert component_should_reject([{
        "severity": "high", "category": "pin_name_mismatch",
        "detail": "VCC on RFIN pin",
    }])


def test_should_not_reject_on_info_only():
    assert not component_should_reject([{
        "severity": "info", "category": "pin_validation_skipped",
        "detail": "no curated map",
    }])


def test_should_not_reject_on_empty_issues():
    assert not component_should_reject([])


# ---------------------------------------------------------------------------
# reject_invalid_components — end-to-end removal
# ---------------------------------------------------------------------------

def test_rejects_component_with_out_of_range_pin():
    """ADL8107 is a 3-lead SOT-89. Pin 100 is impossible → reject."""
    netlist = {
        "nodes": [
            {"instance_id": "U1", "reference_designator": "U1",
             "part_number": "ADL8107"},
            {"instance_id": "U2", "reference_designator": "U2",
             "part_number": "HMC8410LP2FE"},
        ],
        "edges": [
            {"net_name": "RF", "from_instance": "U1", "from_pin": "1",
             "to_instance": "U2", "to_pin": "2"},
        ],
        "schematic_data": {
            "sheets": [{
                "components": [
                    {"ref": "U1", "part_number": "ADL8107",
                     "pins": [{"num": "100", "name": "DATA"}]},
                    {"ref": "U2", "part_number": "HMC8410LP2FE",
                     "pins": [{"num": "2", "name": "RFIN"}]},
                ],
            }],
        },
    }
    cleaned, rejections = reject_invalid_components(netlist)

    # U1 stripped everywhere
    assert len(rejections) == 1
    assert "U1" in rejections[0]["location"] or "ADL8107" in rejections[0]["detail"]
    refs = [
        (s.get("ref") or "")
        for s in cleaned["schematic_data"]["sheets"][0]["components"]
    ]
    assert "U1" not in refs
    assert "U2" in refs
    # Nodes + edges cascade-removed so KiCad export doesn't emit orphans
    node_refs = [n.get("reference_designator") for n in cleaned["nodes"]]
    assert "U1" not in node_refs
    assert cleaned["edges"] == []  # the only edge referenced U1


def test_valid_component_kept():
    netlist = {
        "nodes": [{"instance_id": "U1", "reference_designator": "U1",
                   "part_number": "HMC8410LP2FE"}],
        "schematic_data": {
            "sheets": [{
                "components": [{
                    "ref": "U1", "part_number": "HMC8410LP2FE",
                    "pins": [{"num": "2", "name": "RFIN"}],
                }],
            }],
        },
    }
    cleaned, rejections = reject_invalid_components(netlist)
    assert rejections == []
    assert len(cleaned["schematic_data"]["sheets"][0]["components"]) == 1


def test_pin_name_mismatch_triggers_rejection():
    """U1 labels pin 2 as VCC, but HMC8410's pin 2 is RFIN — reject."""
    netlist = {
        "nodes": [{"reference_designator": "U1",
                   "part_number": "HMC8410LP2FE"}],
        "schematic_data": {
            "sheets": [{
                "components": [{
                    "ref": "U1", "part_number": "HMC8410LP2FE",
                    "pins": [{"num": "2", "name": "VCC"}],
                }],
            }],
        },
    }
    cleaned, rejections = reject_invalid_components(netlist)
    assert len(rejections) == 1


def test_empty_netlist_returns_empty():
    cleaned, rejections = reject_invalid_components({})
    assert rejections == []
    assert cleaned == {}


def test_rejection_purges_nets_in_schematic_data():
    """When a component is rejected on pin grounds, any schematic_data
    net that has an endpoint on the rejected ref must be purged too.
    Otherwise the React canvas renders orphan L-shaped traces and the
    orphan-pin sidebar flags phantom floating pins."""
    netlist = {
        "nodes": [
            {"reference_designator": "U1", "part_number": "ADL8107"},
            {"reference_designator": "U2", "part_number": "HMC8410LP2FE"},
        ],
        "schematic_data": {
            "sheets": [{
                "name": "RF Front-End",
                "components": [
                    {"ref": "U1", "part_number": "ADL8107",
                     "pins": [{"num": "100", "name": "DATA"}]},  # out-of-range → reject
                    {"ref": "U2", "part_number": "HMC8410LP2FE",
                     "pins": [{"num": "2", "name": "RFIN"}]},
                ],
                "nets": [
                    {"name": "RF_IN",
                     "endpoints": [{"ref": "U1", "pin": "DATA"},
                                   {"ref": "U2", "pin": "RFIN"}]},
                    {"name": "GND",
                     "endpoints": [{"ref": "U2", "pin": "GND"}]},
                ],
            }],
        },
    }
    cleaned, rejections = reject_invalid_components(netlist)
    assert len(rejections) == 1

    sheet = cleaned["schematic_data"]["sheets"][0]
    # U1 removed from components
    assert [c["ref"] for c in sheet["components"]] == ["U2"]
    # The RF_IN net touched U1 → gone. GND net only touches U2 → kept.
    assert [n["name"] for n in sheet["nets"]] == ["GND"]


def test_rejection_preserves_cross_sheet_nets_without_rejected_refs():
    """A net in a different sheet that doesn't reference the rejected
    component is untouched."""
    netlist = {
        "schematic_data": {
            "sheets": [
                {"name": "RF", "components": [
                    {"ref": "U1", "part_number": "ADL8107",
                     "pins": [{"num": "100", "name": "X"}]},  # reject
                 ], "nets": [
                    {"name": "RF_TO_NOWHERE",
                     "endpoints": [{"ref": "U1", "pin": "X"}]},
                 ]},
                {"name": "Power", "components": [
                    {"ref": "U2", "part_number": "HMC8410LP2FE",
                     "pins": [{"num": "2", "name": "RFIN"}]},
                 ], "nets": [
                    {"name": "VCC_3V3",
                     "endpoints": [{"ref": "U2", "pin": "VCC"}]},
                 ]},
            ],
        },
    }
    cleaned, _ = reject_invalid_components(netlist)
    rf_sheet, pwr_sheet = cleaned["schematic_data"]["sheets"]
    assert rf_sheet["nets"] == []  # net touching U1 gone
    assert [n["name"] for n in pwr_sheet["nets"]] == ["VCC_3V3"]


def test_package_inherited_from_nodes_when_missing_on_schematic():
    """When the schematic entry doesn't carry a package but the
    matching node does, the inherited package should still drive
    plausibility checks."""
    netlist = {
        "nodes": [{"reference_designator": "U1", "part_number": "MYSTERY-X",
                   "package": "LFCSP-16"}],
        "schematic_data": {
            "sheets": [{
                "components": [{
                    "ref": "U1", "part_number": "MYSTERY-X",
                    # no package on the schematic entry
                    "pins": [{"num": "200", "name": "NC"}],  # past LFCSP-16
                }],
            }],
        },
    }
    _, rejections = reject_invalid_components(netlist)
    assert len(rejections) == 1
