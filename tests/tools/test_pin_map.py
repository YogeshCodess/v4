"""Tests for tools/pin_map.py — pin-number validation."""
from __future__ import annotations

import pytest

from tools.pin_map import (
    infer_pin_count_from_package,
    lookup,
    reset_cache,
    validate_component_pins,
    validate_netlist_pins,
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_cache()
    yield
    reset_cache()


# ---------------------------------------------------------------------------
# Package pin-count inference
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pkg,expected", [
    ("LQFP100",      100),
    ("LQFP-144",     144),
    ("WQFN-40",       40),
    ("LFCSP-16",      16),
    ("LFCSP-8",        8),
    ("LFCSP-32",      32),
    ("CSPBGA-196",   196),
    ("CSPBGA-144",   144),
    ("CSPBGA-324",   324),
    ("DFN-8",          8),
    ("QFN-36",        36),
    ("LP2F-14",       14),
    ("LP5-32",        32),
    ("LCC-12",        12),
    ("TSOT-23-8",      8),
])
def test_infer_pin_count_from_package_known_shapes(pkg, expected):
    assert infer_pin_count_from_package(pkg) == expected


@pytest.mark.parametrize("pkg", ["SOT-89", "SOT-89-3", "SOT-23", "TO-220"])
def test_infer_pin_count_fixed_packages(pkg):
    assert infer_pin_count_from_package(pkg) in (2, 3, 5, 6)


def test_infer_pin_count_returns_none_for_unknown():
    assert infer_pin_count_from_package("") is None
    assert infer_pin_count_from_package(None) is None
    assert infer_pin_count_from_package("MYSTERY-PKG") is None


# ---------------------------------------------------------------------------
# Curated lookup
# ---------------------------------------------------------------------------

def test_lookup_finds_known_part():
    entry = lookup("HMC8410LP2FE")
    assert entry is not None
    assert entry["total_pins"] == 14
    assert entry["package"] == "LP2F-14"
    assert entry["pins"]["2"]["name"] == "RFIN"


def test_lookup_is_case_insensitive():
    assert lookup("adl8107") is not None
    assert lookup("ADL8107") is not None


def test_lookup_missing_part_returns_none():
    assert lookup("TOTALLY-INVENTED") is None
    assert lookup("") is None
    assert lookup(None) is None  # type: ignore[arg-type]


def test_lookup_skips_about_metadata_entry():
    """The `_about` metadata key in pin_maps.json must not leak into
    the DB as if it were a part number."""
    assert lookup("_about") is None


# ---------------------------------------------------------------------------
# validate_component_pins — curated-path
# ---------------------------------------------------------------------------

def test_valid_pins_on_known_part_produce_no_issues():
    issues = validate_component_pins(
        part_number="HMC8410LP2FE",
        emitted_pins=[
            {"num": "2",  "name": "RFIN"},
            {"num": "12", "name": "RFOUT"},
            {"num": "6",  "name": "VDD1"},
        ],
        ref="U1",
    )
    assert issues == []


def test_pin_number_out_of_range_flagged_critical():
    issues = validate_component_pins(
        part_number="ADL8107",  # 3-lead SOT-89
        emitted_pins=[
            {"num": "100", "name": "DATA"},  # impossible
        ],
        ref="U7",
    )
    assert any(i["severity"] == "critical" and
               i["category"] == "invalid_pin_number" for i in issues)
    assert any("100" in i["detail"] for i in issues)


def test_pin_name_mismatch_flagged_high():
    """P1.6: promoted from medium → high. A mis-labelled pin on a real
    MPN is a direct integration error — VCC routed to an RF port is not
    a "warning", it's a fatal schematic bug. Severity high triggers
    component rejection by `reject_invalid_components`."""
    issues = validate_component_pins(
        part_number="HMC8410LP2FE",
        emitted_pins=[{"num": "2", "name": "VCC"}],  # really RFIN
        ref="U1",
    )
    assert any(i["category"] == "pin_name_mismatch"
               and i["severity"] == "high" for i in issues)


def test_pin_name_normalisation_accepts_rfin_for_rf_in():
    """'RF_IN' should match datasheet 'RFIN' case-/underscore-insensitively."""
    issues = validate_component_pins(
        part_number="ADL8107",
        emitted_pins=[
            {"num": "1", "name": "RF_IN"},
            {"num": "3", "name": "rf-out"},  # datasheet: RFOUT
        ],
        ref="U1",
    )
    assert not any(i["category"] == "pin_name_mismatch" for i in issues)


# ---------------------------------------------------------------------------
# validate_component_pins — package-fallback path
# ---------------------------------------------------------------------------

def test_unknown_part_with_known_package_uses_plausibility_check():
    issues = validate_component_pins(
        part_number="MYSTERY-123",
        emitted_pins=[
            {"num": "1",   "name": "VDD"},
            {"num": "100", "name": "NC"},   # well past LFCSP-16 range
        ],
        package="LFCSP-16",
        ref="U9",
    )
    pin_number_issues = [i for i in issues if i["category"] == "invalid_pin_number"]
    # Only pin 100 is out of range; pin 1 is valid on an LFCSP-16.
    assert len(pin_number_issues) == 1
    assert "100" in pin_number_issues[0]["detail"]
    assert pin_number_issues[0]["severity"] == "high"


def test_unknown_part_and_unknown_package_emits_info_skip():
    issues = validate_component_pins(
        part_number="MYSTERY-Q2",
        emitted_pins=[{"num": "1", "name": "VCC"}],
        package="SOME-WEIRD-PKG",
        ref="U5",
    )
    assert any(i["category"] == "pin_validation_skipped"
               and i["severity"] == "info" for i in issues)


def test_non_numeric_pin_on_non_bga_package_flagged():
    issues = validate_component_pins(
        part_number="MYSTERY-LQFP",
        emitted_pins=[{"num": "DATA", "name": "DATA"}],  # invalid
        package="LQFP100",
        ref="U2",
    )
    assert any(i["category"] == "invalid_pin_number"
               and "non-numeric" in i["detail"] for i in issues)


def test_non_numeric_pin_on_bga_package_accepted():
    """BGA packages legitimately use 'A1', 'B14', etc."""
    issues = validate_component_pins(
        part_number="MYSTERY-BGA",
        emitted_pins=[
            {"num": "A1",  "name": "VCC"},
            {"num": "H14", "name": "GND"},
        ],
        package="CSPBGA-196",
        ref="U3",
    )
    # No invalid_pin_number for non-numeric pins on BGA
    assert not any(i["category"] == "invalid_pin_number" for i in issues)


# ---------------------------------------------------------------------------
# validate_netlist_pins (top-level walker)
# ---------------------------------------------------------------------------

def test_validate_netlist_pins_happy_path():
    netlist = {
        "nodes": [
            {"instance_id": "U1", "part_number": "HMC8410LP2FE",
             "reference_designator": "U1"},
            {"instance_id": "U2", "part_number": "ADL8107",
             "reference_designator": "U2"},
        ],
        "schematic_data": {
            "sheets": [{
                "components": [
                    {"ref": "U1", "pins": [{"num": "2", "name": "RFIN"}]},
                    {"ref": "U2", "pins": [{"num": "3", "name": "RFOUT"}]},
                ],
            }],
        },
    }
    assert validate_netlist_pins(netlist) == []


def test_validate_netlist_pins_catches_lp2f14_overflow():
    netlist = {
        "nodes": [{"instance_id": "U1", "part_number": "HMC8410LP2FE",
                   "reference_designator": "U1"}],
        "schematic_data": {
            "sheets": [{
                "components": [
                    {"ref": "U1", "pins": [
                        {"num": "20", "name": "RFIN"},  # 14-pin part
                    ]},
                ],
            }],
        },
    }
    issues = validate_netlist_pins(netlist)
    assert any(i["category"] == "invalid_pin_number"
               and i["severity"] == "critical" for i in issues)


def test_validate_netlist_pins_falls_back_to_nodes_when_no_schematic():
    """When schematic_data is absent, fall back to nodes[].pins."""
    netlist = {
        "nodes": [{
            "instance_id": "U1",
            "part_number": "ADL8107",
            "reference_designator": "U1",
            "pins": [{"num": "99", "name": "X"}],  # out of 3-pin range
        }],
    }
    issues = validate_netlist_pins(netlist)
    assert any(i["category"] == "invalid_pin_number" for i in issues)


def test_validate_netlist_pins_empty_payload_returns_empty():
    assert validate_netlist_pins({}) == []
    assert validate_netlist_pins({"nodes": []}) == []
    assert validate_netlist_pins(None) == []  # type: ignore[arg-type]


def test_validate_netlist_pins_inherits_package_from_nodes():
    """When the schematic component doesn't carry a package but the
    corresponding node in `nodes[]` does, use the latter for the
    plausibility check."""
    netlist = {
        "nodes": [{"instance_id": "U1", "part_number": "UNKNOWN-X",
                   "reference_designator": "U1", "package": "LQFP100"}],
        "schematic_data": {
            "sheets": [{
                "components": [
                    {"ref": "U1", "pins": [{"num": "200", "name": "NC"}]},
                ],
            }],
        },
    }
    issues = validate_netlist_pins(netlist)
    assert any(i["category"] == "invalid_pin_number"
               and "200" in i["detail"] for i in issues)
