"""
IEEE Document Structure Validator

Validates that generated documents contain all required IEEE sections.
Supports IEEE 29148 (HRS), IEEE 830 (SRS), and IEEE 1016 (SDD).
"""

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    document_type: str
    ieee_standard: str
    is_valid: bool
    total_sections: int
    found_sections: int
    missing_sections: list[str]
    warnings: list[str]


# Required sections per IEEE standard
IEEE_29148_HRS_SECTIONS = [
    "Introduction",
    "System Overview",
    "Hardware Requirements",
    "Design Constraints",
    "Verification",
]

IEEE_830_SRS_SECTIONS = [
    "Introduction",
    "Overall Description",
    "Specific Requirements",
    "Verification",
]

IEEE_1016_SDD_SECTIONS = [
    "Introduction",
    "Context",
    "Composition",
    "Logical",
    "Interface",
    "Interaction",
    "State",
]


def validate_hrs(content: str) -> ValidationResult:
    """Validate HRS against IEEE 29148 structure."""
    return _validate_document(content, "HRS", "IEEE 29148:2018", IEEE_29148_HRS_SECTIONS)


def validate_srs(content: str) -> ValidationResult:
    """Validate SRS against IEEE 830/29148 structure."""
    return _validate_document(content, "SRS", "IEEE 830/29148", IEEE_830_SRS_SECTIONS)


def validate_sdd(content: str) -> ValidationResult:
    """Validate SDD against IEEE 1016 structure."""
    return _validate_document(content, "SDD", "IEEE 1016-2009", IEEE_1016_SDD_SECTIONS)


def _validate_document(
    content: str, doc_type: str, standard: str, required_sections: list[str]
) -> ValidationResult:
    """Generic validator that checks for presence of required sections."""
    content_lower = content.lower()
    found = []
    missing = []
    warnings = []

    for section in required_sections:
        # Check for section header (markdown ## or #)
        pattern = rf'#+\s*[\d.]*\s*{re.escape(section.lower())}'
        if re.search(pattern, content_lower):
            found.append(section)
        elif section.lower() in content_lower:
            found.append(section)
            warnings.append(f"Section '{section}' found in text but not as a proper header")
        else:
            missing.append(section)

    # Check for requirement IDs
    if doc_type == "HRS":
        hw_reqs = re.findall(r'REQ-HW-\d+', content)
        if not hw_reqs:
            warnings.append("No REQ-HW-xxx requirement IDs found")
        else:
            logger.info(f"Found {len(hw_reqs)} hardware requirement IDs")

    elif doc_type == "SRS":
        sw_reqs = re.findall(r'REQ-SW-\d+', content)
        if not sw_reqs:
            warnings.append("No REQ-SW-xxx requirement IDs found")

    # Check for traceability matrix
    if "traceability" not in content_lower:
        warnings.append("No traceability matrix found")

    is_valid = len(missing) == 0

    return ValidationResult(
        document_type=doc_type,
        ieee_standard=standard,
        is_valid=is_valid,
        total_sections=len(required_sections),
        found_sections=len(found),
        missing_sections=missing,
        warnings=warnings,
    )


def validate_all(output_dir: str, project_name: str) -> dict[str, ValidationResult]:
    """Validate all generated documents."""
    from pathlib import Path
    results = {}
    base = Path(output_dir)
    safe_name = project_name.replace(" ", "_")

    hrs_path = base / f"HRS_{safe_name}.md"
    if hrs_path.exists():
        results["HRS"] = validate_hrs(hrs_path.read_text(encoding="utf-8"))

    srs_path = base / f"SRS_{safe_name}.md"
    if srs_path.exists():
        results["SRS"] = validate_srs(srs_path.read_text(encoding="utf-8"))

    sdd_path = base / f"SDD_{safe_name}.md"
    if sdd_path.exists():
        results["SDD"] = validate_sdd(sdd_path.read_text(encoding="utf-8"))

    return results
