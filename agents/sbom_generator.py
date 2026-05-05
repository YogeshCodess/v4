"""
CycloneDX SBOM Generator — Phase 3 add-on.

Produces a CycloneDX 1.4 JSON SBOM from the component BOM generated in P1.
Output: sbom.json  (CycloneDX JSON format, importable into Dependency-Track)

Spec: https://cyclonedx.org/specification/overview/
"""

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _try_cyclonedx_lib(components: List[Dict], project_name: str) -> Optional[str]:
    """Try to generate SBOM using the official cyclonedx-python-lib."""
    try:
        from cyclonedx.model.bom import Bom
        from cyclonedx.model.component import Component, ComponentType
        from cyclonedx.output.json import JsonV1Dot5

        from packageurl import PackageURL

        bom = Bom()

        for comp in components:
            name = comp.get("name", "unknown")
            version = comp.get("version", "")
            manufacturer = comp.get("manufacturer", comp.get("vendor", ""))
            pn = comp.get("part_number", comp.get("mpn", ""))

            # Build PURL: pkg:generic/Manufacturer/PartNumber@version
            purl_str = (
                f"pkg:generic/{manufacturer}/{pn}@{version}"
                if manufacturer and pn
                else f"pkg:generic/unknown/{name}@{version or '1.0'}"
            )

            try:
                purl = PackageURL.from_string(purl_str.replace(" ", "_").lower())
            except Exception:
                purl = None

            c = Component(
                component_type=ComponentType.HARDWARE,
                name=name,
                version=version or "1.0",
                bom_ref=str(uuid.uuid4()),
            )
            if purl:
                c.purl = purl

            bom.components.add(c)

        serialiser = JsonV1Dot5(bom)
        return serialiser.output_as_string()

    except Exception as e:
        logger.debug(f"cyclonedx-python-lib failed: {e} — using manual builder")
        return None


def generate_sbom(
    project_name: str,
    output_dir: Path,
    components_text: str,
    requirements_text: str = "",
) -> Dict:
    """
    Parse the P1 component recommendations and generate a CycloneDX SBOM.

    Returns: {sbom_json: str, component_count: int, sbom_path: str}
    """
    components = _parse_components(components_text)

    if not components:
        logger.warning("SBOM: No components parsed from BOM text — generating empty SBOM")

    # Try official library first, fall back to manual builder
    sbom_json = _try_cyclonedx_lib(components, project_name)
    if not sbom_json:
        sbom_json = _build_sbom_manually(components, project_name)

    # Save to file
    sbom_path = output_dir / "sbom.json"
    sbom_path.write_text(sbom_json, encoding="utf-8")
    logger.info(f"SBOM written: {sbom_path} ({len(components)} components)")

    # Also write a human-readable SBOM summary markdown
    summary_md = _build_sbom_summary(components, project_name)
    summary_path = output_dir / "sbom_summary.md"
    summary_path.write_text(summary_md, encoding="utf-8")

    return {
        "sbom_json": sbom_json,
        "sbom_summary": summary_md,
        "component_count": len(components),
        "sbom_path": str(sbom_path),
        "summary_path": str(summary_path),
    }


# --------------------------------------------------------------------------- #
# Component parser
# --------------------------------------------------------------------------- #

def _parse_components(text: str) -> List[Dict]:
    """
    Extract component rows from markdown BOM tables and bullet lists.

    Handles formats like:
      | STM32F407 | STMicro | MCU | $8.50 | 1 |
      - STM32F407 (STMicro): ARM Cortex-M4 microcontroller
    """
    components = []
    seen = set()

    # 1. Parse markdown table rows — run the pattern per-line so the trailing
    # `(?:[^|]*\|)*` segment can't eat subsequent rows' content (the old
    # full-text match collapsed multi-row tables into the first row only).
    table_row = re.compile(
        r"\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|(?:[^|]*\|)*"
    )
    for line in text.splitlines():
        m = table_row.search(line)
        if not m:
            continue
        name = m.group(1).strip().lstrip("#").strip()
        vendor = m.group(2).strip()
        desc = m.group(3).strip()

        # Skip header rows
        if name.lower() in ("component", "name", "part", "device", "item", "---"):
            continue
        if re.match(r"[-=:]+", name):
            continue
        if len(name) < 2:
            continue

        key = name.lower()
        if key not in seen:
            seen.add(key)
            components.append(_normalize_component(name, vendor, desc))

    # 2. Parse bullet list items  "- PartName (Vendor): description"
    if not components:
        bullet = re.compile(
            r"[-*]\s+([A-Za-z0-9_\-/]+)\s*(?:\(([^)]+)\))?\s*[:\-]?\s*(.*)"
        )
        for m in bullet.finditer(text):
            name = m.group(1).strip()
            vendor = m.group(2) or ""
            desc = m.group(3) or ""
            if len(name) < 2:
                continue
            key = name.lower()
            if key not in seen:
                seen.add(key)
                components.append(_normalize_component(name, vendor.strip(), desc.strip()))

    return components[:100]  # CycloneDX SBOM cap for demo


def _normalize_component(name: str, vendor: str, desc: str) -> Dict:
    # Heuristic: extract version-like string from name (e.g. STM32F407VGT6 → version suffix)
    version_match = re.search(r"[vV](\d+[\.\d]*)", name + " " + desc)
    version = version_match.group(1) if version_match else "1.0"

    # Classify component type
    comp_type = "hardware"
    desc_lower = desc.lower() + " " + name.lower()
    if any(k in desc_lower for k in ["firmware", "driver", "software", "os", "rtos"]):
        comp_type = "firmware"
    elif any(k in desc_lower for k in ["lib", "library", "sdk", "framework"]):
        comp_type = "library"

    # Extract licence hints
    license_id = "LicenseRef-hardware-component"
    if "open" in desc_lower or "osha" in desc_lower:
        license_id = "LicenseRef-open-hardware"

    return {
        "name": name,
        "version": version,
        "vendor": vendor or "Unknown",
        "description": desc[:120],
        "type": comp_type,
        "license": license_id,
        "bom_ref": str(uuid.uuid4()),
    }


# --------------------------------------------------------------------------- #
# Manual CycloneDX JSON builder (fallback)
# --------------------------------------------------------------------------- #

def _build_sbom_manually(components: List[Dict], project_name: str) -> str:
    """Build a valid CycloneDX 1.4 JSON document manually."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    serial_number = f"urn:uuid:{uuid.uuid4()}"

    comp_list = []
    for c in components:
        purl = (
            f"pkg:generic/{c['vendor'].lower().replace(' ', '_')}"
            f"/{c['name'].lower().replace(' ', '_')}"
            f"@{c['version']}"
        )
        comp_list.append({
            "type": c["type"],
            "bom-ref": c["bom_ref"],
            "name": c["name"],
            "version": c["version"],
            "description": c["description"],
            "purl": purl,
            "supplier": {
                "name": c["vendor"],
            },
            "licenses": [
                {"license": {"id": c["license"]}}
            ],
        })

    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.4",
        "serialNumber": serial_number,
        "version": 1,
        "metadata": {
            "timestamp": now,
            "tools": [
                {
                    "vendor": "Data Patterns India",
                    "name": "Silicon to Software (S2S) v2",
                    "version": "2.0.0",
                }
            ],
            "component": {
                "type": "device",
                "name": project_name,
                "version": "1.0.0",
                "description": "Hardware design generated by Silicon to Software (S2S) AI",
            },
        },
        "components": comp_list,
    }

    return json.dumps(sbom, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Human-readable summary
# --------------------------------------------------------------------------- #

def _build_sbom_summary(components: List[Dict], project_name: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Group by type
    by_type: Dict[str, List[Dict]] = {}
    for c in components:
        t = c["type"]
        by_type.setdefault(t, []).append(c)

    lines = [
        f"# CycloneDX SBOM — {project_name}",
        "",
        "**Format:** CycloneDX 1.4 JSON",
        f"**Generated:** {now}",
        f"**Total Components:** {len(components)}",
        "",
        "## Component Summary",
        "",
    ]

    for comp_type, items in sorted(by_type.items()):
        lines.append(f"### {comp_type.capitalize()} ({len(items)})")
        lines.append("")
        lines.append("| Component | Vendor | Version | Description |")
        lines.append("|-----------|--------|---------|-------------|")
        for c in items:
            lines.append(
                f"| {c['name']} | {c['vendor']} | {c['version']} | {c['description'][:60]} |"
            )
        lines.append("")

    lines += [
        "## Usage",
        "",
        "Import `sbom.json` into [Dependency-Track](https://dependencytrack.org/) "
        "or any CycloneDX-compatible tool for vulnerability scanning and license compliance.",
        "",
        "```bash",
        "# Validate with cyclonedx-cli",
        "cyclonedx validate --input-file sbom.json",
        "",
        "# Upload to Dependency-Track",
        'curl -X PUT "https://your-dt-instance/api/v1/bom" \\',
        '  -H "X-API-Key: YOUR_KEY" \\',
        '  -F "autoCreate=true" \\',
        f'  -F "projectName={project_name}" \\',
        '  -F "bom=@sbom.json"',
        "```",
    ]

    return "\n".join(lines)
