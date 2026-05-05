"""REACH Compliance Rules - Registration, Evaluation, Authorisation and Restriction of Chemicals."""

import logging
from typing import Dict

logger = logging.getLogger(__name__)


# REACH SVHC (Substances of Very High Concern) - subset for demo
REACH_SVHC = [
    "Antimony trioxide",
    "Cobalt(II) chloride",
    "Cobalt(II) sulfate",
    "Cobalt(II) carbonate",
    "Dichromium tris(chromate)",
    "Lead chromate",
    "Lead sulfochromate yellow",
    "C.I. Pigment Red 104",
    "Triethyl arsenate",
    "Substances category: 1,2-Benzenedicarboxylic acid, di-C8-10-branched alkyl esters",
]


def check_component_reach(component: Dict) -> Dict:
    """Check if a component contains REACH SVHC substances."""
    part_number = component.get("part_number", "Unknown")
    materials = component.get("materials", [])
    svhs_list = component.get("svhc_substances", [])

    result = {
        "part_number": part_number,
        "standard": "REACH EC 1907/2006",
        "status": "pass",
        "svhc_found": [],
        "warnings": [],
    }

    # Check against SVHC list
    for material in materials:
        if material in REACH_SVHC:
            result["svhc_found"].append(material)
            result["status"] = "review"

    # Add any declared SVHC
    for svhc in svhs_list:
        if svhc not in result["svhc_found"]:
            result["svhc_found"].append(svhc)
            result["status"] = "review"

    return result


def get_reach_summary() -> Dict:
    """Get REACH rule summary."""
    return {
        "standard": "REACH EC 1907/2006",
        "svhc_count": len(REACH_SVHC),
        "threshold": "0.1% by weight for SVHC notification",
    }
