"""
ACPR / spurious-emission regulatory mask validator.

A transmitter BOM can pass every internal cascade check and still
fail at type-approval because the PA's adjacent-channel leakage
exceeds the regulatory mask for the band / service chosen. This
validator takes the wizard's claimed ACPR and spurious-mask selection
and compares against the actual mask limits published by the
governing body.

Five mask families are supported — the same set offered in the
TX_SPECS.spur_mask chip list in rfArchitect.ts:

  MIL-STD-461 (military EMI)        — CE102 / RE102 / etc.
  FCC Part 15 Class A (commercial)   — industrial/commercial radiators
  FCC Part 15 Class B (residential)  — tighter than Class A
  ETSI EN 300 (European SRD)         — short-range devices
  None / N/A                         — test/lab only; validator skips

Each mask carries an `acpr_limit_dbc` field representing the
**minimum acceptable suppression** of adjacent-channel power below the
carrier. When the claimed ACPR (a negative dBc figure — more negative
is better) is less suppressive than the mask requires, we flag it.

The masks here are **approximations** suitable for early-design
sanity checking, not a substitute for a compliance test report. Real
FCC / ETSI filings involve a full 3rd-party measurement sweep; this
tool catches the 'PA is 20 dB too hot' case before hardware is built.

Returns AuditIssue-shaped dicts so services/rf_audit.py can wrap them.
"""
from __future__ import annotations

import re
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Regulatory mask definitions
# ---------------------------------------------------------------------------

# `acpr_limit_dbc`: adjacent-channel power must be at least this many
# dBc below the carrier. More negative = stricter. Values chosen from
# the conservative end of each standard's published limits so the
# validator errs on the side of flagging real risks.
_MASKS: dict[str, dict[str, Any]] = {
    "MIL-STD-461": {
        "label": "MIL-STD-461 (military EMI)",
        "acpr_limit_dbc": -60.0,
        "harmonic_limit_dbc": -70.0,
        "note": "MIL-STD-461 CE102/RE102 spurious limits for ground + airborne",
    },
    "FCC-PART-15-CLASS-A": {
        "label": "FCC Part 15 Class A (commercial / industrial)",
        "acpr_limit_dbc": -45.0,
        "harmonic_limit_dbc": -50.0,
        "note": "FCC 47 CFR §15.109 Class A conducted emission limits",
    },
    "FCC-PART-15-CLASS-B": {
        "label": "FCC Part 15 Class B (residential)",
        "acpr_limit_dbc": -50.0,
        "harmonic_limit_dbc": -55.0,
        "note": "FCC 47 CFR §15.109 Class B conducted emission limits — 10 dB stricter than Class A",
    },
    "ETSI-EN-300": {
        "label": "ETSI EN 300 (European SRD)",
        "acpr_limit_dbc": -45.0,
        "harmonic_limit_dbc": -50.0,
        "note": "ETSI EN 300 short-range-device harmonised standard",
    },
    "FCC-PART-97": {
        "label": "FCC Part 97 (amateur radio)",
        "acpr_limit_dbc": -43.0,
        "harmonic_limit_dbc": -43.0,
        "note": "FCC 47 CFR §97.307(d) amateur transmitter spurious limits",
    },
}

# Aliases — the wizard chip labels are more readable than the canonical
# keys. Map any common phrasing the user might land on.
_ALIASES: dict[str, str] = {
    "MIL-STD-461": "MIL-STD-461",
    "MIL_STD_461": "MIL-STD-461",
    "MILSTD461": "MIL-STD-461",
    "MIL-STD": "MIL-STD-461",
    "FCC PART 15 CLASS A": "FCC-PART-15-CLASS-A",
    "FCC-PART-15-CLASS-A": "FCC-PART-15-CLASS-A",
    "FCC PART 15 CLASS B": "FCC-PART-15-CLASS-B",
    "FCC-PART-15-CLASS-B": "FCC-PART-15-CLASS-B",
    "FCC PART 15": "FCC-PART-15-CLASS-A",  # if unspecified default to the looser class
    "ETSI EN 300": "ETSI-EN-300",
    "ETSI-EN-300": "ETSI-EN-300",
    "EN 300": "ETSI-EN-300",
    "FCC PART 97": "FCC-PART-97",
    "FCC-PART-97": "FCC-PART-97",
}


def _normalise_mask(name: Any) -> Optional[str]:
    if not name:
        return None
    key = str(name).strip().upper()
    if key in ("NONE", "N/A", "NA", "NO", "", "OTHER"):
        return None
    if key in _MASKS:
        return key
    if key in _ALIASES:
        return _ALIASES[key]
    # Loose fallback: scan for family keywords
    if "MIL-STD" in key or "MIL_STD" in key:
        return "MIL-STD-461"
    if "CLASS A" in key and "FCC" in key:
        return "FCC-PART-15-CLASS-A"
    if "CLASS B" in key and "FCC" in key:
        return "FCC-PART-15-CLASS-B"
    if "ETSI" in key:
        return "ETSI-EN-300"
    if "PART 97" in key:
        return "FCC-PART-97"
    return None


def _parse_dbc(value: Any) -> Optional[float]:
    """Accept '-45 dBc' / '-45' / -45.0 / '-45 dBc adjacent'."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    m = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_supported_masks() -> list[str]:
    """Enumerate mask IDs — used by tests and by future UI dropdowns."""
    return list(_MASKS.keys())


def get_mask(mask_name: Any) -> Optional[dict[str, Any]]:
    """Look up mask metadata by name/alias. Returns None for unknown
    or N/A selections."""
    key = _normalise_mask(mask_name)
    return _MASKS.get(key) if key else None


def validate_acpr_mask(
    *,
    claimed_aclr_dbc: Optional[float],
    claimed_harmonic_dbc: Optional[float] = None,
    mask_name: Any = None,
    safety_margin_db: float = 3.0,
) -> list[dict[str, Any]]:
    """Compare claimed ACPR + harmonic rejection against the selected
    regulatory mask. Both figures are in dBc with the RF sign convention
    (more negative = better suppression).

    Args:
      claimed_aclr_dbc       — user's ACPR claim (e.g. -45 dBc).
      claimed_harmonic_dbc   — user's harmonic rejection (e.g. -50 dBc).
                               Optional; when provided, checked against
                               the mask's harmonic limit.
      mask_name              — one of the wizard's spur_mask choices.
      safety_margin_db       — headroom required above the published
                               limit. Default 3 dB — tight enough to
                               catch real risks without firing on
                               borderline designs.

    Returns issue dicts (empty when everything passes).
    """
    issues: list[dict[str, Any]] = []
    mask_key = _normalise_mask(mask_name)
    if not mask_key:
        # No mask chosen (or 'N/A') — nothing to validate against.
        return issues
    mask = _MASKS[mask_key]

    # --- ACPR check ---
    aclr = _parse_dbc(claimed_aclr_dbc)
    if aclr is None:
        # Mask chosen but no ACPR claim. Advisory info — can't assess.
        issues.append({
            "severity": "info",
            "category": "acpr_unknown",
            "location": "design_parameters/aclr_dbc",
            "detail": (
                f"{mask['label']} applies to this design but no ACPR "
                "claim was supplied. Cannot assess mask compliance."
            ),
            "suggested_fix": (
                "Populate `design_parameters.aclr_dbc` with the expected "
                "adjacent-channel leakage in dBc (e.g. -45)."
            ),
        })
    else:
        # RF convention: claim more negative than limit is good.
        required = mask["acpr_limit_dbc"] - safety_margin_db
        if aclr > required:  # less suppressive than required
            shortfall = aclr - mask["acpr_limit_dbc"]
            issues.append({
                "severity": "high",
                "category": "acpr_mask_violation",
                "location": "design_parameters/aclr_dbc",
                "detail": (
                    f"Claimed ACPR {aclr:.1f} dBc does not meet the "
                    f"{mask['label']} limit of {mask['acpr_limit_dbc']:.0f} dBc "
                    f"(with {safety_margin_db:.0f} dB safety margin). "
                    f"Shortfall: {shortfall:+.1f} dB. "
                    "Type-approval will fail at this output spectrum."
                ),
                "suggested_fix": (
                    "Back off the PA further from P1dB, move to a Doherty / "
                    "DPD-linearized architecture, or add a crest-factor "
                    "reduction (CFR) stage on the baseband."
                ),
            })

    # --- Harmonic check ---
    harm = _parse_dbc(claimed_harmonic_dbc)
    if harm is not None:
        required = mask["harmonic_limit_dbc"] - safety_margin_db
        if harm > required:
            shortfall = harm - mask["harmonic_limit_dbc"]
            issues.append({
                "severity": "high",
                "category": "harmonic_mask_violation",
                "location": "design_parameters/harmonic_rej_dbc",
                "detail": (
                    f"Claimed harmonic rejection {harm:.1f} dBc does not meet "
                    f"the {mask['label']} limit of {mask['harmonic_limit_dbc']:.0f} dBc. "
                    f"Shortfall: {shortfall:+.1f} dB. "
                    "Post-PA harmonic filter is under-designed."
                ),
                "suggested_fix": (
                    "Increase harmonic filter order (5th → 7th) or switch "
                    "to a higher-Q technology (ceramic / cavity)."
                ),
            })

    return issues
