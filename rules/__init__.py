"""Silicon to Software (S2S) - Compliance Rules."""

from .rohs_rules import check_component_rohs, get_rohs_summary, ROHS_RESTRICTED
from .reach_rules import check_component_reach, get_reach_summary, REACH_SVHC
from .fcc_rules import check_emissions_requirement, get_fcc_summary, FCC_PART_15_LIMITS

__all__ = [
    "check_component_rohs",
    "get_rohs_summary",
    "ROHS_RESTRICTED",
    "check_component_reach",
    "get_reach_summary",
    "REACH_SVHC",
    "check_emissions_requirement",
    "get_fcc_summary",
    "FCC_PART_15_LIMITS",
]
