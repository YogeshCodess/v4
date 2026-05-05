"""
PA thermal envelope validator.

Closes the last-mile gap in TX audits: a transmitter BOM can look fine
on Pout / OIP3 / PAE grounds but still ship with a PA device that will
cook itself in the first hour of operation. This module runs the
textbook junction-temperature calculation:

    Tj = Ta + P_dissipated × (θ_jc + θ_cs + θ_sa)

and flags PAs whose computed junction temperature exceeds the
technology-dependent Tj_max (with a safety margin). The math is the
same one an RF engineer does manually after picking a device — we just
do it automatically from the BOM + design_parameters.

Inputs:
  components         — BOM list (each component can expose pdc_w,
                       pae_pct, category, and a device "technology" hint)
  design_parameters  — `ambient_temp_c`, optional `heatsink_theta_sa`,
                       `case_sink_theta_cs`. When omitted we use
                       conservative defaults that roughly match a
                       bolt-down flange PA on a finned heatsink.

Outputs a list of AuditIssue-shaped dicts (so services/rf_audit.py
wraps them into AuditIssue objects cheaply).

References:
  - Razavi, "RF Microelectronics", §7.5 thermal design
  - Cripps, "RF Power Amplifiers for Wireless Communications", §2.3
  - Qorvo app-note QPD1006 "GaN HEMT thermal derating"
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Device-technology defaults
# ---------------------------------------------------------------------------

# Tj_max by technology. Conservative operational values — vendor
# datasheets often publish an "absolute max" ~25 °C higher, but the
# reliability literature (MTTF doubling per 10 °C drop) argues strongly
# for derating to these numbers.
_TJ_MAX_BY_TECH: dict[str, float] = {
    "GAN": 200.0,           # GaN HEMT — absolute max often 225 °C
    "GAN_HEMT": 200.0,
    "GAAS": 150.0,          # GaAs pHEMT / HBT
    "GAAS_PHEMT": 150.0,
    "GAAS_HBT": 150.0,
    "LDMOS": 200.0,
    "SIGE": 125.0,          # SiGe BiCMOS
    "SIGE_BICMOS": 125.0,
    "CMOS": 125.0,
    "SI": 125.0,            # bulk silicon
    "SIC": 225.0,           # silicon carbide — harsh-env comms
}

# Default junction-to-case thermal resistance when the datasheet doesn't
# land in key_specs. Ballpark for flange-mount devices at the relevant
# power classes. Units: °C / W.
_DEFAULT_THETA_JC: dict[str, float] = {
    "GAN":  1.5,
    "GAAS": 4.0,
    "LDMOS": 0.8,
    "SIGE": 10.0,
    "CMOS": 10.0,
    "SIC":  0.6,
}

# Total case→ambient resistance fallback when the operator hasn't
# specified a heatsink. 10 °C/W is the classic "datasheet typical" for
# a small finned sink in still air. Tight — catches under-sized heatsinks.
_DEFAULT_THETA_CS_SA: float = 10.0  # °C / W

# Derating margin from Tj_max — we flag at Tj > Tj_max − margin so the
# BOM has headroom for manufacturing spread, aging, and ambient spikes.
_DEFAULT_DERATING_MARGIN_C: float = 15.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_number(d: dict[str, Any], keys: tuple[str, ...]) -> Optional[float]:
    """Copy of rf_cascade's helper — pull a numeric value from either
    the top-level dict or its `key_specs` / `specs` sub-dict."""
    specs = d.get("key_specs") or d.get("specs") or {}
    for k in keys:
        for source in (d, specs):
            if not isinstance(source, dict):
                continue
            v = source.get(k)
            if v is None:
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                m = re.search(r"-?\d+(?:\.\d+)?", str(v))
                if m:
                    try:
                        return float(m.group(0))
                    except ValueError:
                        continue
    return None


def _infer_technology(c: dict[str, Any]) -> Optional[str]:
    """Pull the device technology out of whatever field the LLM populated.

    Checks, in priority order:
      1. `technology` key (most common)
      2. `device_tech` / `pa_tech` hints
      3. `category` — our category labels carry the family sometimes
         (e.g. "RF-PA-GaN")
      4. `part_number` prefix — RFMD / Qorvo / MACOM / ADI numbering
         conventions occasionally encode the process
    """
    for k in ("technology", "device_tech", "pa_tech", "process"):
        v = c.get(k) or (c.get("key_specs") or {}).get(k)
        if v:
            up = str(v).upper().replace(" ", "_").replace("-", "_")
            for tech in _TJ_MAX_BY_TECH:
                if tech in up:
                    return tech
    cat = str(c.get("category") or "").upper()
    for tech in _TJ_MAX_BY_TECH:
        if tech in cat:
            return tech
    # Last resort — part-number hints.
    pn = str(c.get("part_number") or "").upper()
    if re.search(r"\b(QPD|QPA|TGF2|TGA25|CMPA)", pn):
        return "GAN"
    if re.search(r"\b(HMC|AMMP|TGA|VMMK)", pn):
        return "GAAS"
    if re.search(r"\b(BLF|MRF)", pn):
        return "LDMOS"
    return None


def _is_pa(c: dict[str, Any]) -> bool:
    """True when this BOM entry is a PA / driver stage that we should
    thermally analyse. Passive filters + connectors obviously don't count."""
    cat = str(c.get("category") or "").strip().upper()
    if cat.startswith("RF-PA") or cat == "RF-PA" or cat == "PA":
        return True
    if cat in {"RF-DRIVER", "DRIVER", "RF-PREDRIVER", "PREDRIVER"}:
        return True
    if cat in {"RF-AMPLIFIER", "AMPLIFIER"}:
        # Only if it carries real TX power (not an LNA mis-classified).
        pout = _first_number(c, ("pout_dbm", "p_sat_dbm", "p1db_dbm"))
        if pout is not None and pout >= 10.0:  # ≥ +10 dBm
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_pa_thermal(
    components: list[dict[str, Any]],
    *,
    ambient_temp_c: float = 25.0,
    heatsink_theta_sa: Optional[float] = None,
    case_sink_theta_cs: Optional[float] = None,
    derating_margin_c: float = _DEFAULT_DERATING_MARGIN_C,
) -> list[dict[str, Any]]:
    """Walk every PA-class component and flag any whose computed
    junction temperature exceeds its technology's Tj_max − margin.

    Args:
      components         — BOM list (each dict can carry pdc_w, pae_pct,
                           pout_dbm, theta_jc, technology).
      ambient_temp_c     — worst-case ambient (default 25 °C; military
                           designs should pass 85 °C here).
      heatsink_theta_sa  — optional heatsink θ_sa. When omitted we use
                           10 °C/W plus θ_cs split from the shared default.
      case_sink_theta_cs — optional case→sink θ_cs (interface compound +
                           mounting pressure). Defaults to 0 when a total
                           external θ is already supplied.
      derating_margin_c  — how far below Tj_max to flag. Default 15 °C.

    Returns a list of issue dicts (empty when every PA checks out).
    """
    if not components:
        return []

    issues: list[dict[str, Any]] = []

    # External thermal resistance (case to ambient) either comes from the
    # caller (preferred — they know the heatsink) or from our conservative
    # default. When only one of the two is given, we collapse into the
    # single number to avoid double-counting.
    if heatsink_theta_sa is None and case_sink_theta_cs is None:
        theta_ext = _DEFAULT_THETA_CS_SA
    else:
        theta_ext = (heatsink_theta_sa or 0.0) + (case_sink_theta_cs or 0.0)
        if theta_ext <= 0:
            theta_ext = _DEFAULT_THETA_CS_SA

    for c in components:
        if not isinstance(c, dict):
            continue
        if not _is_pa(c):
            continue

        pn = c.get("part_number") or c.get("primary_part") or "unknown"

        # P_dissipated: prefer an explicit pdc_w − Pout(W) calculation.
        # Fall back to Pdc × (1 − PAE/100) when we have PAE but not Pout.
        pdc_w = _first_number(c, ("pdc_w", "pdc", "dc_power_w", "power_dissipation_w"))
        pout_dbm = _first_number(c, ("pout_dbm", "p_sat_dbm", "p1db_dbm", "output_power_dbm"))
        pae_pct = _first_number(c, ("pae_pct", "pae", "drain_efficiency_pct"))

        p_diss_w: Optional[float] = None
        if pdc_w is not None:
            if pout_dbm is not None:
                pout_w = 10.0 ** (pout_dbm / 10.0) / 1000.0  # dBm → W
                p_diss_w = max(0.0, pdc_w - pout_w)
            elif pae_pct is not None:
                p_diss_w = pdc_w * max(0.0, 1.0 - pae_pct / 100.0)
            else:
                # Worst case: assume 0 % efficiency (all DC becomes heat).
                p_diss_w = pdc_w

        if p_diss_w is None or p_diss_w <= 0:
            # Nothing we can compute — flag as info so the operator knows
            # the stage was skipped, but don't block the run.
            issues.append({
                "severity": "info",
                "category": "pa_thermal_unknown",
                "location": f"component_recommendations/{pn}",
                "detail": (
                    f"PA `{pn}` has no pdc_w / pae_pct / pout_dbm to compute "
                    "dissipated power — thermal check skipped."
                ),
                "suggested_fix": (
                    "Populate `key_specs.pdc_w` (DC power) and either "
                    "`pae_pct` or `pout_dbm` so the thermal budget can be "
                    "derived."
                ),
            })
            continue

        # θ_jc: prefer the datasheet number; default by technology.
        theta_jc = _first_number(c, ("theta_jc", "rth_jc", "thermal_resistance_jc"))
        tech = _infer_technology(c)
        if theta_jc is None:
            theta_jc = _DEFAULT_THETA_JC.get(tech or "", 2.5)

        # Tj_max by technology, with a conservative 150 °C default when
        # we can't identify the device family.
        tj_max = _TJ_MAX_BY_TECH.get(tech or "", 150.0)

        # Junction temperature: classic cascade of thermal resistances.
        tj = ambient_temp_c + p_diss_w * (theta_jc + theta_ext)

        headroom = tj_max - tj
        derated_limit = tj_max - derating_margin_c

        if tj > tj_max:
            # Hard failure — absolute max exceeded.
            issues.append({
                "severity": "critical",
                "category": "pa_thermal_overrun",
                "location": f"component_recommendations/{pn}",
                "detail": (
                    f"PA `{pn}` computed junction temperature Tj = "
                    f"{tj:.0f} °C EXCEEDS Tj_max = {tj_max:.0f} °C "
                    f"({tech or 'unknown tech'}) at Ta={ambient_temp_c:.0f} °C, "
                    f"P_diss={p_diss_w:.1f} W, θ_jc={theta_jc:.1f} °C/W, "
                    f"θ_ext={theta_ext:.1f} °C/W. Device will fail."
                ),
                "suggested_fix": (
                    f"Add a heatsink with θ_sa < "
                    f"{max(0.1, (tj_max - derating_margin_c - ambient_temp_c) / p_diss_w - theta_jc):.1f}"
                    " °C/W, or move to a higher Tj_max technology (GaN / SiC)."
                ),
            })
        elif tj > derated_limit:
            # Within absolute max but outside the derating margin — still
            # ships with poor MTTF (every 10 °C over nominal halves life).
            issues.append({
                "severity": "high",
                "category": "pa_thermal_derating",
                "location": f"component_recommendations/{pn}",
                "detail": (
                    f"PA `{pn}` Tj = {tj:.0f} °C is within the {derating_margin_c:.0f} °C "
                    f"derating margin of Tj_max = {tj_max:.0f} °C "
                    f"({tech or 'unknown tech'}, headroom {headroom:.0f} °C). "
                    "MTTF will be significantly reduced."
                ),
                "suggested_fix": (
                    "Improve the heatsink (lower θ_sa), increase PAE, or "
                    "allocate more device area (parallel PA combining) "
                    "to restore thermal headroom."
                ),
            })

    return issues
