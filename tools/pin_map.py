"""
Pin-map validation for LLM-emitted schematic data.

Before this module landed, the netlist agent was free to invent pin
numbers like "DATA" / "CTRL" / "GPIO1" on a 3-lead SOT-89 package —
the JSON shipped, the KiCad `.net` shipped, and nothing caught the
impossibility. This module closes that gap with two layered defences:

  1. **Curated pin map** (`data/pin_maps.json`) — authoritative
     datasheet-derived pin assignments for the top RF parts we seeded.
     When an LLM emits pins for one of these MPNs, we validate every
     pin_number + pin_name pair against the real datasheet.

  2. **Package-pin-count plausibility** — when we don't have a curated
     map but the package name is known (LP2F-14, LQFP100, CSPBGA-196,
     etc.), we infer the maximum valid pin count from the package name
     and flag pin numbers outside the range.

Return values from `validate_component_pins` are plain dicts shaped
like `domains._schema.AuditIssue` so the netlist agent can surface them
in `validation_notes` or an audit report without rewiring.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from threading import Lock
from typing import Any, Optional

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PIN_MAP_PATH = _REPO_ROOT / "data" / "pin_maps.json"

_cache_lock = Lock()
_pin_map_cache: Optional[dict[str, dict]] = None


# ---------------------------------------------------------------------------
# Curated-DB loader
# ---------------------------------------------------------------------------

def _load() -> dict[str, dict]:
    """Lazy-load `data/pin_maps.json` into a {MPN_UPPER: entry} dict."""
    global _pin_map_cache
    with _cache_lock:
        if _pin_map_cache is not None:
            return _pin_map_cache
        try:
            raw = json.loads(_PIN_MAP_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("pin_map.load_failed: %s", exc)
            _pin_map_cache = {}
            return _pin_map_cache
        out: dict[str, dict] = {}
        for mpn, entry in raw.items():
            if mpn.startswith("_"):
                continue
            if isinstance(entry, dict):
                out[mpn.strip().upper()] = entry
        _pin_map_cache = out
        return out


def reset_cache() -> None:
    """Test helper — drop the cached DB so a fresh load runs next call."""
    global _pin_map_cache
    with _cache_lock:
        _pin_map_cache = None


def lookup(part_number: str) -> Optional[dict]:
    """Return the curated pin-map entry for `part_number`, or None if
    we don't have authoritative data for it."""
    if not part_number:
        return None
    return _load().get(part_number.strip().upper())


# ---------------------------------------------------------------------------
# Package-pin-count inference (fallback when no curated entry exists)
# ---------------------------------------------------------------------------

# Explicit packages where the pin count isn't encoded in the name.
_FIXED_PIN_COUNTS: dict[str, int] = {
    "SOT-23": 3,
    "SOT-23-3": 3,
    "SOT-23-5": 5,
    "SOT-23-6": 6,
    "SOT-89": 3,
    "SOT-89-3": 3,
    "TO-220": 3,
    "TO-247": 3,
    "TO-263": 3,
    "TO-252": 3,
    "SMA-F": 2,
    "SMA-M": 2,
    "SMA": 2,
    "K-CONNECTOR": 2,
}

# Families where the pin count appears as the trailing integer in the
# package name (the convention across all major vendor namings).
_TRAILING_NUM_FAMILIES = re.compile(
    r"\b(?:LFCSP|CSPBGA|FCBGA|CSP|BGA|QFN|WQFN|QFP|LQFP|TQFP|LGA|DFN|"
    r"LP\d?F?|LCC|SOIC|SSOP|TSSOP|MSOP|DIP|TSOT|SOT)",
    re.IGNORECASE,
)
_TRAILING_NUM_RE = re.compile(r"(\d{1,4})(?!.*\d)")  # last digit run


def infer_pin_count_from_package(package: Optional[str]) -> Optional[int]:
    """Extract the maximum valid pin count from a package name.

    Convention across vendor packages: the pin count is the **trailing**
    integer in the name. Examples:
        LQFP100     → 100
        LP2F-14     →  14
        LP5-32      →  32
        CSPBGA-196  → 196
        TSOT-23-8   →   8
        WQFN-40     →  40

    `_FIXED_PIN_COUNTS` handles the handful of packages where no digit
    exists in the name (SOT-89, TO-220, etc.).
    Returns None when nothing can be inferred.
    """
    if not package:
        return None
    key = package.strip().upper()
    if key in _FIXED_PIN_COUNTS:
        return _FIXED_PIN_COUNTS[key]
    # Must be a recognised family so random strings like "MYSTERY-123"
    # don't get a pin count assigned.
    if not _TRAILING_NUM_FAMILIES.search(key):
        return None
    m = _TRAILING_NUM_RE.search(key)
    if not m:
        return None
    try:
        n = int(m.group(1))
    except (ValueError, IndexError):
        return None
    return n if 1 <= n <= 4096 else None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _issue(severity: str, category: str, location: str,
           detail: str, fix: str) -> dict[str, Any]:
    return {
        "severity": severity,
        "category": category,
        "location": location,
        "detail": detail,
        "suggested_fix": fix,
    }


def _as_int_or_none(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def validate_component_pins(
    part_number: str,
    emitted_pins: list[dict[str, Any]],
    *,
    package: Optional[str] = None,
    ref: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Validate a list of LLM-emitted pins for one component.

    Args:
      part_number: The manufacturer part number the LLM claimed.
      emitted_pins: List of {"num": "3", "name": "RFIN", ...} dicts.
      package: Fallback package name when no curated entry exists.
      ref: Reference designator (U1, U2, ...) — used in the location
           so downstream reports can point at the exact component.

    Returns a list of issue dicts (empty when everything checks out).
    """
    issues: list[dict[str, Any]] = []
    if not emitted_pins:
        return issues
    who = f"{ref or part_number or '?'}"

    curated = lookup(part_number) if part_number else None
    if curated:
        issues.extend(_validate_against_curated(curated, emitted_pins, who, part_number))
        return issues

    # No curated entry → package-count plausibility fallback.
    pkg = package or ""
    expected_n = infer_pin_count_from_package(pkg)
    if expected_n is None:
        # Can't validate — emit an info note so the operator knows.
        issues.append(_issue(
            "info", "pin_validation_skipped",
            f"component/{who}",
            (
                f"No curated pin map for `{part_number or '?'}` and "
                f"could not infer pin count from package `{pkg or '?'}`. "
                "Skipping pin-number validation."
            ),
            "Add an entry to data/pin_maps.json for this part, or use a package name with an encoded pin count (e.g. LQFP100, CSPBGA-196).",
        ))
        return issues

    for p in emitted_pins:
        num = _as_int_or_none(p.get("num"))
        name = str(p.get("name") or "").strip()
        if num is None:
            # Non-numeric pins only make sense for BGA grids ("A1", "B14");
            # flag when the package isn't a BGA.
            if "BGA" not in pkg.upper() and name:
                issues.append(_issue(
                    "medium", "invalid_pin_number",
                    f"component/{who}/pin/{name}",
                    (
                        f"Pin '{p.get('num')}' on `{part_number}` (package {pkg}) "
                        "is non-numeric; only BGA packages use letter-row refs."
                    ),
                    "Use 1-based integer pin numbers or switch to a BGA package.",
                ))
            continue
        if num < 1 or num > expected_n:
            issues.append(_issue(
                "high", "invalid_pin_number",
                f"component/{who}/pin/{num}",
                (
                    f"Pin {num} on `{part_number}` exceeds the "
                    f"{expected_n}-pin range of package {pkg}."
                ),
                f"Use a pin number in 1..{expected_n} or correct the package name.",
            ))
    return issues


def _validate_against_curated(
    curated: dict,
    emitted_pins: list[dict[str, Any]],
    who: str,
    part_number: str,
) -> list[dict[str, Any]]:
    """Exhaustive validation against the datasheet-derived map."""
    issues: list[dict[str, Any]] = []
    total = int(curated.get("total_pins") or 0)
    real_pins: dict[str, dict] = curated.get("pins") or {}

    for p in emitted_pins:
        num_raw = p.get("num")
        name = str(p.get("name") or "").strip()
        num = _as_int_or_none(num_raw)

        # Pin number out of range
        if num is not None and total and (num < 1 or num > total):
            issues.append(_issue(
                "critical", "invalid_pin_number",
                f"component/{who}/pin/{num}",
                (
                    f"Pin {num} on `{part_number}` is outside the valid "
                    f"1..{total} range for package {curated.get('package', '?')}."
                ),
                f"Use a pin number in 1..{total} per the datasheet.",
            ))
            continue

        # Pin-name vs. datasheet mismatch (only check when BOTH are present).
        # Promoted to `high` in P1.6: a mis-labelled pin on a real MPN is a
        # direct integration error (VCC connected to an RF port, etc.) and
        # must reject the component, not just warn about it.
        if num is not None and name and str(num) in real_pins:
            expected_name = str(real_pins[str(num)].get("name") or "").strip()
            if expected_name and not _names_match(expected_name, name):
                issues.append(_issue(
                    "high", "pin_name_mismatch",
                    f"component/{who}/pin/{num}",
                    (
                        f"Pin {num} on `{part_number}` labelled '{name}' but "
                        f"datasheet says '{expected_name}'."
                    ),
                    f"Rename pin {num} to '{expected_name}' or fix the schematic.",
                ))
    return issues


# ---------------------------------------------------------------------------
# Component-level acceptance gate (P1.6)
# ---------------------------------------------------------------------------

def component_should_reject(issues: list[dict[str, Any]]) -> bool:
    """Return True when pin-validation issues are severe enough that the
    component cannot ship as-is. Used by netlist_agent to strip the
    component from the schematic_data output."""
    return any(
        i.get("severity") in ("critical", "high")
        and i.get("category") in ("invalid_pin_number", "pin_name_mismatch")
        for i in issues or []
    )


def reject_invalid_components(
    netlist_data: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Walk schematic_data and remove any component whose pin list fails
    validation with critical/high severity. Returns the filtered netlist
    + a list of rejection issues suitable for the DRC report."""
    import copy
    nd = copy.deepcopy(netlist_data) if netlist_data else {}
    rejections: list[dict[str, Any]] = []

    # Build reject-set from schematic components
    to_reject: set[str] = set()
    schematic = nd.get("schematic_data") or {}
    for sheet in schematic.get("sheets") or []:
        kept = []
        for comp in sheet.get("components") or []:
            ref = str(comp.get("ref") or comp.get("reference_designator")
                      or comp.get("id") or "").strip()
            mpn = str(comp.get("part_number") or "").strip()
            # Pull package from matching node if present
            pkg = str(comp.get("package") or "").strip()
            if not pkg:
                for n in nd.get("nodes") or []:
                    n_ref = str(n.get("reference_designator")
                                or n.get("instance_id")
                                or n.get("id") or "").strip()
                    if n_ref == ref:
                        pkg = str(n.get("package")
                                  or (n.get("properties") or {}).get("package")
                                  or "").strip()
                        break
            pins = comp.get("pins") or []
            issues = validate_component_pins(
                part_number=mpn, emitted_pins=pins,
                package=pkg, ref=ref or mpn,
            )
            if component_should_reject(issues):
                to_reject.add(ref)
                rejections.append({
                    "severity": "critical",
                    "rule": "pin_map_reject",
                    "location": f"component/{ref or mpn}",
                    "detail": (
                        f"Component `{ref}` ({mpn}) rejected because pin "
                        f"validation produced {len(issues)} critical/high "
                        "issue(s): "
                        + "; ".join(
                            str(i.get("detail", ""))[:80] for i in issues
                        )[:500]
                    ),
                    "suggested_fix": (
                        "Fix the pin list per the datasheet or switch to a "
                        "curated MPN from data/pin_maps.json."
                    ),
                })
            else:
                kept.append(comp)
        sheet["components"] = kept

        # Purge any nets whose endpoints reference a rejected component.
        # Without this, the React canvas (SheetSchematic.tsx) renders
        # orphan L-shaped traces to empty grid squares, and the orphan-pin
        # sidebar flags phantom floating pins that no longer exist in
        # components[]. KiCad .net export is already safe because edges[]
        # is pruned below, but schematic.json needs matching treatment.
        if to_reject:
            sheet["nets"] = [
                net for net in (sheet.get("nets") or [])
                if not any(
                    str(ep.get("ref") or "").strip() in to_reject
                    for ep in (net.get("endpoints") or [])
                )
            ]

    # Mirror the rejection in nodes + edges so downstream KiCad .net
    # generation doesn't emit orphan components.
    if to_reject:
        nd["nodes"] = [
            n for n in (nd.get("nodes") or [])
            if str(n.get("reference_designator")
                   or n.get("instance_id")
                   or n.get("id") or "").strip() not in to_reject
        ]
        nd["edges"] = [
            e for e in (nd.get("edges") or [])
            if str(e.get("from_instance") or e.get("source") or "").strip() not in to_reject
            and str(e.get("to_instance") or e.get("target") or "").strip() not in to_reject
        ]

    return nd, rejections


def _names_match(datasheet: str, emitted: str) -> bool:
    """Case / punctuation-insensitive equality so 'RF_IN' matches 'RFIN'
    and 'VDD1' matches 'Vdd1'."""
    def norm(s: str) -> str:
        return re.sub(r"[\s_\-]", "", s).upper()
    return norm(datasheet) == norm(emitted)


# ---------------------------------------------------------------------------
# Top-level entry — validate every component in a netlist payload
# ---------------------------------------------------------------------------

def validate_netlist_pins(netlist_data: dict) -> list[dict[str, Any]]:
    """Walk every component in `netlist_data.nodes` and return a flat
    list of pin-validation issues. Accepts the raw tool_call payload
    from `netlist_agent` (`GENERATE_NETLIST_TOOL`).

    Uses `schematic_data.sheets[].components[].pins` when present,
    falling back to `nodes[].pins` when the schematic block was not
    emitted (the skeleton path).
    """
    issues: list[dict[str, Any]] = []
    if not isinstance(netlist_data, dict):
        return issues

    # Build a MPN lookup table from `nodes[]` so we can attach package
    # info to each component we encounter inside schematic_data.
    mpn_by_ref: dict[str, str] = {}
    package_by_ref: dict[str, str] = {}
    for n in netlist_data.get("nodes") or []:
        ref = str(n.get("reference_designator") or n.get("instance_id")
                  or n.get("id") or "").strip()
        if not ref:
            continue
        mpn = str(n.get("part_number") or "").strip()
        if mpn:
            mpn_by_ref[ref] = mpn
        pkg = str(n.get("package") or (n.get("properties") or {}).get("package") or "").strip()
        if pkg:
            package_by_ref[ref] = pkg

    # Walk schematic_data.sheets[].components[] when present.
    schematic = netlist_data.get("schematic_data") or {}
    sheets = schematic.get("sheets") or []
    for sheet in sheets:
        for comp in (sheet.get("components") or []):
            ref = str(comp.get("ref") or comp.get("reference_designator")
                      or comp.get("id") or "").strip()
            mpn = (str(comp.get("part_number") or "").strip()
                   or mpn_by_ref.get(ref, ""))
            pkg = (str(comp.get("package") or "").strip()
                   or package_by_ref.get(ref, ""))
            pins = comp.get("pins") or []
            issues.extend(validate_component_pins(
                part_number=mpn, emitted_pins=pins,
                package=pkg, ref=ref or mpn,
            ))

    # Fallback: validate pins declared directly on `nodes[]` when no
    # schematic sheets were produced.
    if not sheets:
        for n in netlist_data.get("nodes") or []:
            ref = str(n.get("reference_designator") or n.get("instance_id")
                      or n.get("id") or "").strip()
            mpn = str(n.get("part_number") or "").strip()
            pkg = str(n.get("package") or (n.get("properties") or {}).get("package") or "").strip()
            pins = n.get("pins") or []
            issues.extend(validate_component_pins(
                part_number=mpn, emitted_pins=pins,
                package=pkg, ref=ref or mpn,
            ))

    return issues
