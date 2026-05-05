"""
BOM ↔ schematic linkage audit — P2.9.

A frequent silent failure mode: the netlist agent drops a decap from
the BOM, or invents a reference designator that was never in the BOM
("R_BIAS1"), or picks a different MPN than P1 specified. The netlist
agent was told "include ALL components from the P1 BOM", but nothing
validated it.

This module cross-references:
  - `component_recommendations[].part_number` — the BOM (source of truth)
  - `netlist.nodes[].part_number` — the schematic

Every BOM entry must appear at least once in nodes; every node must map
to a BOM part_number. Missing on either side surfaces as an AuditIssue
so downstream PCB layout can catch it before a designer starts placing.

Shape:
    issues = validate_bom_schematic_linkage(
        component_recommendations=[...],  # P1 output
        netlist_nodes=[...],              # P4 output
    )
"""
from __future__ import annotations

from typing import Any


def _norm_mpn(value: Any) -> str:
    """Case-insensitive, whitespace-stripped MPN comparison key."""
    if value is None:
        return ""
    return str(value).strip().upper()


def _bom_mpn(component: dict[str, Any]) -> str:
    """Extract the canonical MPN from a BOM entry, tolerating either
    flat (`part_number`) or rich (`primary_part`) shapes."""
    return _norm_mpn(
        component.get("part_number")
        or component.get("primary_part")
        or component.get("mpn")
    )


def _node_mpn(node: dict[str, Any]) -> str:
    return _norm_mpn(node.get("part_number") or node.get("mpn"))


def validate_bom_schematic_linkage(
    component_recommendations: list[dict[str, Any]],
    netlist_nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a list of issue dicts (AuditIssue-shaped) for BOM ↔ schematic
    mismatches.

    Rules:
      1. BOM entry missing from nodes  →  severity=high (the schematic
         dropped a component the designer expected to see).
      2. Node with a part_number not in the BOM  →  severity=medium (the
         netlist agent invented a part that P1 didn't approve; passive
         glue logic like R_BIAS is common, but worth surfacing).
      3. Node with no part_number  →  severity=low (missing metadata,
         annoys the BOM roll-up but doesn't fail DRC).

    Empty inputs return an empty list — callers decide whether that
    state is acceptable.
    """
    issues: list[dict[str, Any]] = []
    if not component_recommendations and not netlist_nodes:
        return issues

    bom_mpns: dict[str, dict[str, Any]] = {}
    for c in component_recommendations or []:
        mpn = _bom_mpn(c)
        if mpn:
            bom_mpns[mpn] = c

    node_mpns: dict[str, list[dict[str, Any]]] = {}
    unannotated_nodes: list[str] = []
    for n in netlist_nodes or []:
        mpn = _node_mpn(n)
        if not mpn:
            ref = str(n.get("reference_designator")
                      or n.get("instance_id")
                      or n.get("id") or "(unknown)")
            unannotated_nodes.append(ref)
            continue
        node_mpns.setdefault(mpn, []).append(n)

    # 1. BOM entries missing from the schematic
    for mpn in bom_mpns:
        if mpn not in node_mpns:
            issues.append({
                "severity": "high",
                "category": "bom_missing_in_schematic",
                "location": f"bom/{mpn}",
                "detail": (
                    f"BOM entry `{mpn}` has no corresponding node in the "
                    "schematic. The netlist agent skipped this component."
                ),
                "suggested_fix": (
                    "Re-run P4 and ensure every component_recommendations "
                    "entry is instantiated in the netlist, or drop the "
                    "entry from the BOM if it's intentionally unused."
                ),
            })

    # 2. Schematic nodes with MPNs not in the BOM
    for mpn, nodes in node_mpns.items():
        if mpn not in bom_mpns:
            refs = ", ".join(
                str(n.get("reference_designator")
                    or n.get("instance_id")
                    or n.get("id") or "?")
                for n in nodes[:3]
            )
            issues.append({
                "severity": "medium",
                "category": "schematic_part_not_in_bom",
                "location": f"schematic/{mpn}",
                "detail": (
                    f"Schematic uses MPN `{mpn}` (refs: {refs}) but the "
                    "BOM doesn't list it. The netlist agent may have "
                    "invented a part."
                ),
                "suggested_fix": (
                    "Either add the MPN to component_recommendations "
                    "(+ validate it through distributor lookup) or "
                    "rewrite the schematic to use a BOM-approved part."
                ),
            })

    # 3. Nodes with no MPN at all (low-severity metadata hygiene)
    if unannotated_nodes:
        # Group into a single issue so the report doesn't bloat.
        issues.append({
            "severity": "low",
            "category": "schematic_node_missing_mpn",
            "location": f"schematic/{','.join(unannotated_nodes[:3])}",
            "detail": (
                f"{len(unannotated_nodes)} schematic node(s) are missing "
                f"a part_number: {', '.join(unannotated_nodes[:5])}"
                + ("…" if len(unannotated_nodes) > 5 else "")
            ),
            "suggested_fix": (
                "Populate `part_number` on every netlist node so the "
                "BOM roll-up is complete."
            ),
        })

    return issues
