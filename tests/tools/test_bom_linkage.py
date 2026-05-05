"""Tests for tools/bom_linkage.py — P2.9."""
from __future__ import annotations

import pytest

from tools.bom_linkage import validate_bom_schematic_linkage


def _bom(*mpns):
    return [{"part_number": m} for m in mpns]


def _nodes(*items):
    """Build netlist nodes from (ref, mpn) tuples."""
    return [{"reference_designator": r, "part_number": m} for r, m in items]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_empty_inputs_return_empty():
    assert validate_bom_schematic_linkage([], []) == []


def test_bom_and_schematic_fully_aligned():
    bom = _bom("HMC8410LP2FE", "HMC1049LP5E", "LMX2594")
    nodes = _nodes(
        ("U1", "HMC8410LP2FE"),
        ("U2", "HMC1049LP5E"),
        ("U3", "LMX2594"),
    )
    assert validate_bom_schematic_linkage(bom, nodes) == []


def test_case_insensitive_mpn_matching():
    bom = _bom("adl8107")
    nodes = _nodes(("U1", "ADL8107"))
    assert validate_bom_schematic_linkage(bom, nodes) == []


# ---------------------------------------------------------------------------
# BOM entry missing from schematic — high
# ---------------------------------------------------------------------------

def test_bom_part_missing_from_schematic_flagged_high():
    bom = _bom("HMC8410LP2FE", "HMC1049LP5E")
    nodes = _nodes(("U1", "HMC8410LP2FE"))  # HMC1049LP5E skipped
    issues = validate_bom_schematic_linkage(bom, nodes)
    assert any(
        i["severity"] == "high"
        and i["category"] == "bom_missing_in_schematic"
        and "HMC1049LP5E" in i["detail"]
        for i in issues
    )


def test_multiple_missing_parts_produce_separate_issues():
    bom = _bom("A", "B", "C")
    nodes = _nodes(("U1", "A"))
    issues = validate_bom_schematic_linkage(bom, nodes)
    missing = {i["detail"] for i in issues if i["category"] == "bom_missing_in_schematic"}
    assert len(missing) == 2  # B and C


# ---------------------------------------------------------------------------
# Schematic part not in BOM — medium
# ---------------------------------------------------------------------------

def test_invented_schematic_part_flagged_medium():
    bom = _bom("HMC8410LP2FE")
    nodes = _nodes(
        ("U1", "HMC8410LP2FE"),
        ("U99", "INVENTED-BY-LLM"),  # not in BOM
    )
    issues = validate_bom_schematic_linkage(bom, nodes)
    assert any(
        i["severity"] == "medium"
        and i["category"] == "schematic_part_not_in_bom"
        and "INVENTED-BY-LLM" in i["detail"]
        for i in issues
    )


def test_invented_part_includes_ref_designators():
    bom = _bom()
    nodes = _nodes(("U7", "FAKE-MPN"), ("U8", "FAKE-MPN"))
    issues = validate_bom_schematic_linkage(bom, nodes)
    invent = [i for i in issues if i["category"] == "schematic_part_not_in_bom"]
    assert len(invent) == 1  # same MPN → one issue
    assert "U7" in invent[0]["detail"] and "U8" in invent[0]["detail"]


# ---------------------------------------------------------------------------
# Node with no MPN — low
# ---------------------------------------------------------------------------

def test_unannotated_node_flagged_low():
    bom = _bom("HMC8410LP2FE")
    nodes = [
        {"reference_designator": "U1", "part_number": "HMC8410LP2FE"},
        {"reference_designator": "C1"},  # no part_number
    ]
    issues = validate_bom_schematic_linkage(bom, nodes)
    lows = [i for i in issues if i["severity"] == "low"]
    assert len(lows) == 1
    assert "C1" in lows[0]["detail"]


def test_many_unannotated_nodes_collapsed_into_one_issue():
    bom = _bom()
    nodes = [{"reference_designator": f"R{i}"} for i in range(10)]
    issues = validate_bom_schematic_linkage(bom, nodes)
    lows = [i for i in issues if i["category"] == "schematic_node_missing_mpn"]
    assert len(lows) == 1  # one roll-up issue, not ten
    assert "10 schematic node(s)" in lows[0]["detail"]


# ---------------------------------------------------------------------------
# Input shape tolerance
# ---------------------------------------------------------------------------

def test_rich_component_recommendations_shape_accepted():
    """Accept both `part_number` (flat) and `primary_part` (rich) shapes."""
    bom = [{"primary_part": "LMX2594", "primary_manufacturer": "TI"}]
    nodes = _nodes(("U1", "LMX2594"))
    assert validate_bom_schematic_linkage(bom, nodes) == []


def test_node_id_fallback_when_reference_designator_missing():
    bom = _bom()
    nodes = [{"id": "U_orphan", "part_number": ""}]
    issues = validate_bom_schematic_linkage(bom, nodes)
    lows = [i for i in issues if i["category"] == "schematic_node_missing_mpn"]
    assert any("U_orphan" in i["detail"] for i in lows)
