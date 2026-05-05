"""
Phase-noise budget validator — P2.8.

The cascade validator already checks gain / NF / IIP3 numbers in the BOM
against the P1 claimed cascade. Phase noise is the one RF spec that
frequently ships without any cross-check: the P1 system prompt talks
about a phase-noise floor (e.g. "< -140 dBc/Hz at 10 kHz" for radar
MTI) and the LLM happily emits a spec value, but nothing in the
pipeline verifies that the selected PLL / OCXO / synthesizer actually
meets it.

This module rectifies that: given a claimed system phase-noise target
and the LNA/mixer/PLL components in the BOM, identify the dominant LO
and ensure its datasheet phase-noise is **better than** the claim.

Input shape for `validate_phase_noise`:

    validate_phase_noise(
        claimed_phase_noise_dbchz=-140.0,
        offset_hz=10_000,
        components=[
            {"part_number": "LMX2594", "category": "RF-PLL",
             "key_specs": {"phase_noise_dbchz": -115}},
            ...
        ],
    )

Returns a list of dicts shaped like `domains._schema.AuditIssue`.
"""
from __future__ import annotations

import re
from typing import Any, Optional


# Categories that contribute phase noise to a coherent RF chain. Only
# these are checked — a passive filter adding -160 dBc/Hz is fine, we
# don't care about it.
_LO_CATEGORIES = frozenset({
    "RF-PLL", "RF-PLL/Synth", "RF-Synthesizer",
    "RF-Clock", "RF-OCXO", "RF-TCXO",
    "PLL", "Synthesizer",
    "Clock", "Oscillator",
})


# Common dBc/Hz keys used in seed JSON / distributor data.
_PHASE_NOISE_KEYS = (
    "phase_noise_dbchz",
    "phase_noise_dbc_hz",
    "phase_noise_1khz_dbchz",
    "phase_noise_10khz_dbchz",
    "phase_noise_100khz_dbchz",
    "phase_noise",
)


def _extract_phase_noise(component: dict[str, Any]) -> Optional[float]:
    """Pull a phase-noise number out of a component entry.

    Looks in `key_specs` first, then at top level. Handles both numeric
    values and strings like "-115 dBc/Hz". Returns None when no
    recognisable number is present."""
    specs = component.get("key_specs") or {}
    candidates: list[Any] = []
    for k in _PHASE_NOISE_KEYS:
        if isinstance(specs, dict) and k in specs:
            candidates.append(specs[k])
        if k in component:
            candidates.append(component[k])
    for raw in candidates:
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
        m = re.search(r"(-?\d+(?:\.\d+)?)", str(raw))
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _is_lo_component(component: dict[str, Any]) -> bool:
    """True when this component is in the LO / clock tree."""
    cat = str(component.get("category") or "").strip()
    if cat in _LO_CATEGORIES:
        return True
    # Fall back to part-number hints (PLLs / synths often have
    # distinctive prefixes). Narrow-match: LMX2xxx, ADF4xxx, HMC73x/83x,
    # MAX2870, CDCExxx, CCHD-9xx — avoids false-positives on LNAs that
    # share a vendor prefix (HMC8410 is an LNA, not a synthesizer).
    pn = str(component.get("part_number") or "").upper()
    if re.match(
        r"^(?:LMX\d{3,}|ADF\d{3,}|HMC(?:73\d|83\d)|"
        r"MAX2870|CDCE\d+|CCHD-?\d+)",
        pn,
    ):
        return True
    return False


def validate_phase_noise(
    claimed_phase_noise_dbchz: Optional[float],
    *,
    offset_hz: float = 10_000.0,
    components: list[dict[str, Any]],
    margin_db: float = 3.0,
) -> list[dict[str, Any]]:
    """Compare the claimed system phase-noise floor against the best
    LO / synthesizer in the BOM.

    Rule: the dominant LO's datasheet phase-noise at the target offset
    must be at least `margin_db` dB better than the system claim.
    A claim of -140 dBc/Hz with a LO at -115 dBc/Hz and 3 dB margin
    fails (system can never be better than its LO).

    Returns zero issues on any of:
      - no claim supplied
      - no LO-class components in the BOM (nothing to check against)
      - claim + LO within margin
    """
    issues: list[dict[str, Any]] = []
    if claimed_phase_noise_dbchz is None:
        return issues
    try:
        claim = float(claimed_phase_noise_dbchz)
    except (TypeError, ValueError):
        return issues

    los: list[tuple[str, float]] = []
    los_without_spec: list[str] = []
    for c in components or []:
        if not _is_lo_component(c):
            continue
        pn = str(c.get("part_number") or c.get("primary_part") or "unknown")
        pn_db = _extract_phase_noise(c)
        if pn_db is None:
            los_without_spec.append(pn)
            continue
        los.append((pn, pn_db))

    if not los and not los_without_spec:
        return issues  # no LO-class components to check

    if los_without_spec:
        issues.append({
            "severity": "medium",
            "category": "phase_noise_unknown",
            "location": f"components/{','.join(los_without_spec[:3])}",
            "detail": (
                f"LO / synthesizer parts without a datasheet phase-noise "
                f"spec: {', '.join(los_without_spec[:5])}. Cannot verify "
                f"the {claim:.1f} dBc/Hz system claim."
            ),
            "suggested_fix": (
                "Populate `key_specs.phase_noise_dbchz` for these parts "
                "or replace them with MPNs that carry the spec."
            ),
        })

    if not los:
        return issues  # nothing measurable

    # Worst (highest, since dBc/Hz is negative) LO sets the floor.
    worst_pn, worst_pn_db = max(los, key=lambda t: t[1])

    # System claim must be worse (higher, less negative) than the LO
    # plus margin. "-140 claim vs -115 LO" → worst-LO is 25 dB worse
    # than the claim → fail.
    # Required: claim >= worst_pn_db + margin  (i.e. LO is ≥ margin dB below claim)
    if claim < worst_pn_db + margin_db:
        delta = worst_pn_db - claim
        issues.append({
            "severity": "high",
            "category": "phase_noise_budget",
            "location": f"components/{worst_pn}",
            "detail": (
                f"System claims phase-noise floor {claim:.1f} dBc/Hz @ "
                f"{offset_hz/1000:.1f} kHz offset, but the selected LO "
                f"(`{worst_pn}`) is {worst_pn_db:.1f} dBc/Hz — "
                f"{delta:+.1f} dB worse than the claim. The cascade "
                "cannot be better than its LO."
            ),
            "suggested_fix": (
                f"Select an LO with phase-noise ≤ {claim - margin_db:.1f} "
                f"dBc/Hz (at least {margin_db} dB below the claim) or relax "
                "the system phase-noise requirement."
            ),
        })
    return issues
