"""RoHS Compliance Rules - Restriction of Hazardous Substances."""

import logging
from typing import Dict

logger = logging.getLogger(__name__)


# RoHS restricted substances (EU Directive 2011/65/EU)
ROHS_RESTRICTED = {
    "Lead (Pb)": 0.1,  # 1000 ppm
    "Mercury (Hg)": 0.1,
    "Cadmium (Cd)": 0.01,  # 100 ppm
    "Hexavalent Chromium (Cr6+)": 0.1,
    "PBB (Polybrominated biphenyls)": 0.1,
    "PBDE (Polybrominated diphenyl ethers)": 0.1,
    # RoHS 3 (2019) additions:
    "Bis(2-ethylhexyl) phthalate (DEHP)": 0.1,
    "Butyl benzyl phthalate (BBP)": 0.1,
    "Dibutyl phthalate (DBP)": 0.1,
    "Diisobutyl phthalate (DIBP)": 0.1,
}

# Common exemptions
ROHS_EXEMPTIONS = [
    "Lead in solders for servers, storage, and array storage equipment",
    "Lead in electronic ceramic parts",
    "Mercury in fluorescent lamps",
]


def check_component_rohs(component: Dict) -> Dict:
    """Check if a component is RoHS compliant."""
    part_number = component.get("part_number", "Unknown")
    compliance = component.get("rohs_compliant", "unknown").lower()
    substances = component.get("substances", {})

    result = {
        "part_number": part_number,
        "standard": "RoHS 2011/65/EU",
        "status": "unknown",
        "violations": [],
        "warnings": [],
    }

    # Check explicit compliance flag
    if compliance == "compliant":
        result["status"] = "pass"
    elif compliance == "non_compliant":
        result["status"] = "fail"
    else:
        # Check substances if available
        for substance, concentration in substances.items():
            if substance in ROHS_RESTRICTED:
                limit = ROHS_RESTRICTED[substance]
                if concentration > limit:
                    result["violations"].append({
                        "substance": substance,
                        "concentration": concentration,
                        "limit": limit,
                    })
                    result["status"] = "fail"
                elif concentration > limit * 0.9:
                    result["warnings"].append({
                        "substance": substance,
                        "concentration": concentration,
                        "limit": limit,
                        "message": "Near limit",
                    })
                    if result["status"] != "fail":
                        result["status"] = "review"

        if result["status"] == "unknown" and not result["violations"] and not result["warnings"]:
            result["status"] = "review"
            result["warnings"].append({"message": "No compliance data available"})

    return result


def get_rohs_summary() -> Dict:
    """Get RoHS rule summary."""
    return {
        "standard": "EU Directive 2011/65/EU (RoHS 3)",
        "substances": ROHS_RESTRICTED,
        "exemptions_count": len(ROHS_EXEMPTIONS),
    }
