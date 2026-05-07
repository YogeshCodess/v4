"""
RF-spec discipline audit — catches per-component spec-vs-requirement mismatches.

Originally built for the "user wants 6-18 GHz, BOM has a 6-7 GHz LO" bug class
but extended (2026-05-06) to cover ALL major RF specs that operators commonly
specify in design_parameters but that the LLM doesn't reliably honour when
picking components:

  - Frequency range  : every part's spec'd RF range must overlap the user's
                       band, with stricter rules for stages that need full
                       coverage (LNA / mixer / filter).
  - System NF target : front-end LNA NF must be < ~70 % of the project's
                       claimed system NF (Friis says the first-stage NF
                       dominates; an LNA spec'd at 4 dB cannot enable a
                       system NF claim of 3 dB).
  - Supply voltage   : every part's required supply must be one of the
                       project's available rails. Catches "5 V LNA in a
                       3.3 V-only design" picks.

These rules are advisory at retrieval (filtering bad candidates upstream is
fragile) but mandatory at audit (post-LLM verification gate).

Three failure modes this module catches:

  1. **No overlap** (severity=critical) — a BOM part's spec'd frequency range
     doesn't intersect the user's requested band at all. e.g. user wants
     6-18 GHz, BOM has a 0.4-2 GHz LNA. Almost certainly a wrong pick.

  2. **Partial coverage** (severity=high) — a part covers only part of the
     user's band. e.g. user wants 6-18 GHz, BOM has a 6-7 GHz LO. May be
     intentional (a fixed-LO super-het with multiple LOs) but more often
     is the LLM picking the closest semantically-similar part without
     checking numeric coverage. Worth a reviewer flag.

  3. **Missing constraint** (severity=medium, advisory only) — the project
     has no `frequency_range_ghz` recorded in design_parameters, so the
     audit can't run. Surfaces a reminder to populate it.

Public API:

    issues = run_frequency_audit(
        component_recommendations,
        design_parameters,
    )

The same `freq_band_relationship` helper that powers the audit is also
used by `tools/component_search.search()` to filter Chroma results
upstream — defense in depth, so bad candidates never reach the LLM in
the first place.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Literal, Optional

from domains._schema import AuditIssue

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Frequency-range parsing
# ---------------------------------------------------------------------------

# Common RF band names and their canonical GHz ranges. Drives the
# `extract_freq_range` parser when the user types "X-band" instead of
# "8-12 GHz". Kept conservative — only well-established designations.
_BAND_NAMES: dict[str, tuple[float, float]] = {
    "hf":      (0.003, 0.03),
    "vhf":     (0.03, 0.3),
    "uhf":     (0.3, 1.0),
    "l-band":  (1.0, 2.0),
    "lband":   (1.0, 2.0),
    "s-band":  (2.0, 4.0),
    "sband":   (2.0, 4.0),
    "c-band":  (4.0, 8.0),
    "cband":   (4.0, 8.0),
    "x-band":  (8.0, 12.0),
    "xband":   (8.0, 12.0),
    "ku-band": (12.0, 18.0),
    "kuband":  (12.0, 18.0),
    "k-band":  (18.0, 27.0),
    "kband":   (18.0, 27.0),
    "ka-band": (27.0, 40.0),
    "kaband":  (27.0, 40.0),
    "v-band":  (40.0, 75.0),
    "vband":   (40.0, 75.0),
    "w-band":  (75.0, 110.0),
    "wband":   (75.0, 110.0),
}


# Numeric range pattern: "6-18 GHz", "6 to 18 GHz", "6–18GHz", "0.4-2 GHz",
# "57 MHz - 14 GHz" (mixed units). Captures three optional pieces:
# value1, optional unit1, value2, unit2.
_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*"                     # value 1
    r"(MHz|GHz|kHz|Hz)?\s*"                   # optional unit 1
    r"(?:[-–—]|to)\s*"                         # separator
    r"(\d+(?:\.\d+)?)\s*"                     # value 2
    r"(MHz|GHz|kHz|Hz)",                      # required unit 2 (the trailing one)
    re.IGNORECASE,
)


_UNIT_TO_GHZ: dict[str, float] = {
    "hz":  1e-9,
    "khz": 1e-6,
    "mhz": 1e-3,
    "ghz": 1.0,
}


def _to_ghz(value: float, unit: Optional[str], fallback_unit: str = "GHz") -> float:
    """Convert a numeric value + unit to GHz. When `unit` is None, falls
    back to `fallback_unit` (used for the value-1 in '57 MHz - 14 GHz'
    style strings, where unit-1 is implicit if absent)."""
    u = (unit or fallback_unit).strip().lower()
    return value * _UNIT_TO_GHZ.get(u, 1.0)


def parse_freq_range(text: Any) -> Optional[tuple[float, float]]:
    """Parse an RF frequency range from free text. Returns (min_ghz, max_ghz)
    or None when no range can be extracted.

    Accepts:
      "6-18 GHz", "6 to 18 GHz", "6–18 GHz", "0.4-2.0 GHz"
      "57 MHz - 14 GHz", "100 MHz to 6 GHz"
      "X-band", "S-band 2-4 GHz", "Ku-band"

    For band-name + range mix ("X-band 8-12 GHz"), the explicit range
    wins (more precise than the band name's canonical bounds).
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        # Single-frequency point — treat as a degenerate range.
        return (float(text), float(text))
    s = str(text).strip()
    if not s:
        return None

    # 1. Try the numeric range pattern first — most precise.
    m = _RANGE_RE.search(s)
    if m:
        v1 = float(m.group(1))
        u1 = m.group(2)
        v2 = float(m.group(3))
        u2 = m.group(4)
        # If unit-1 is missing, inherit from unit-2. e.g. "6-18 GHz" sets
        # both to GHz; "57 MHz - 14 GHz" keeps them distinct.
        f1 = _to_ghz(v1, u1, fallback_unit=u2)
        f2 = _to_ghz(v2, u2)
        if f1 > f2:
            f1, f2 = f2, f1
        return (f1, f2)

    # 2. Fall back to band-name lookup. Use word-boundary matching so
    # "low band" doesn't accidentally match "lband"/"wband" by substring.
    # We split on non-alphanumeric and look for an exact token match.
    tokens = re.split(r"[\s\-_/,;.]+", s.lower())
    # Also include the joined no-space variant (e.g. "X-band" → "xband")
    # so users typing "S-band" or "Sband" both hit.
    norm_joined = re.sub(r"[\s\-_/,;.]+", "", s.lower())
    for key, rng in _BAND_NAMES.items():
        if key in tokens:
            return rng
        # Allow the no-dash variants ("xband", "sband") to match the joined
        # form, but require it to be the WHOLE input — substring match
        # turned "lowband" into "wband" pre-fix.
        if key == norm_joined:
            return rng
    return None


def extract_design_freq_range(
    design_parameters: dict[str, Any],
) -> Optional[tuple[float, float]]:
    """Pull the project's RF frequency range out of the design_parameters
    payload. Tries common keys in order of specificity."""
    if not design_parameters:
        return None
    for key in (
        "frequency_range_ghz",
        "frequency_range",
        "freq_range_ghz",
        "freq_range",
        "operating_frequency",
        "operating_freq",
        "rf_band",
        "band",
    ):
        if key in design_parameters and design_parameters[key]:
            r = parse_freq_range(design_parameters[key])
            if r:
                return r
    # Numeric pair fallback (e.g. {min_freq_ghz: 6, max_freq_ghz: 18})
    fmin = design_parameters.get("min_freq_ghz") or design_parameters.get("freq_min_ghz")
    fmax = design_parameters.get("max_freq_ghz") or design_parameters.get("freq_max_ghz")
    if fmin is not None and fmax is not None:
        try:
            a, b = float(fmin), float(fmax)
            return (min(a, b), max(a, b))
        except (TypeError, ValueError):
            pass
    return None


def extract_part_freq_range(row: dict[str, Any]) -> Optional[tuple[float, float]]:
    """Pull a part's spec'd frequency range from a BOM row. Tries the
    structured numeric fields first, then falls back to parsing the
    description text. Returns None when the part has no frequency info
    (caller should treat this as "unknown" — don't penalise)."""
    if not row:
        return None

    # 1. Structured Hz fields (curated component spec library uses these).
    for fmin_key, fmax_key in (
        ("min_freq_hz", "max_freq_hz"),
        ("pll_min_output_freq_hz", "pll_max_output_freq_hz"),
        ("rf_min_freq_hz", "rf_max_freq_hz"),
        ("operating_freq_min_hz", "operating_freq_max_hz"),
    ):
        v_min = row.get(fmin_key)
        v_max = row.get(fmax_key)
        if v_min is not None and v_max is not None:
            try:
                return (float(v_min) / 1e9, float(v_max) / 1e9)
            except (TypeError, ValueError):
                pass

    # 2. Structured GHz fields (LLM tool output / spec_hint extraction).
    for fmin_key, fmax_key in (
        ("min_freq_ghz", "max_freq_ghz"),
        ("freq_min_ghz", "freq_max_ghz"),
        ("frequency_min_ghz", "frequency_max_ghz"),
    ):
        v_min = row.get(fmin_key)
        v_max = row.get(fmax_key)
        if v_min is not None and v_max is not None:
            try:
                return (float(v_min), float(v_max))
            except (TypeError, ValueError):
                pass

    # 3. Free-text fields — description, frequency_range, key_specs.
    for text_key in ("frequency_range_ghz", "frequency_range", "rf_band", "band"):
        if row.get(text_key):
            r = parse_freq_range(row[text_key])
            if r:
                return r

    # 4. key_specs nested dict (LLM often puts spec details there).
    ks = row.get("key_specs")
    if isinstance(ks, dict):
        nested = extract_part_freq_range(ks)
        if nested:
            return nested

    # 5. Last resort: parse the description text — works for the curated
    # specs library where descriptions like "57 MHz - 14 GHz wideband
    # synthesizer" carry the range inline.
    desc = row.get("description") or row.get("primary_description") or ""
    if desc:
        return parse_freq_range(desc)

    return None


# ---------------------------------------------------------------------------
# Band-relationship classifier — also consumed by component_search.py
# ---------------------------------------------------------------------------

BandRelation = Literal["covers", "partial", "no_overlap", "unknown"]


def freq_band_relationship(
    spec_min_ghz: Optional[float],
    spec_max_ghz: Optional[float],
    req_min_ghz: Optional[float],
    req_max_ghz: Optional[float],
    *,
    tolerance_ghz: float = 0.05,
) -> BandRelation:
    """Classify how a part's spec'd frequency range compares to the user's
    requested band.

    Returns one of:
      - "covers"     : spec range fully contains the requested band
      - "partial"    : spec range overlaps but doesn't fully cover
      - "no_overlap" : disjoint ranges
      - "unknown"    : either side is missing — caller must not penalise

    `tolerance_ghz` (default 50 MHz) absorbs minor numeric noise so a part
    spec'd at 6.0-18.0 GHz isn't flagged as "partial" against a 6.0-18.0 GHz
    request that has trailing-decimal drift. Increase if you find legitimate
    edge-case parts being flagged.
    """
    if (spec_min_ghz is None or spec_max_ghz is None
            or req_min_ghz is None or req_max_ghz is None):
        return "unknown"
    sm, sx = float(spec_min_ghz), float(spec_max_ghz)
    rm, rx = float(req_min_ghz), float(req_max_ghz)
    if sx + tolerance_ghz < rm or sm - tolerance_ghz > rx:
        return "no_overlap"
    if sm <= rm + tolerance_ghz and sx + tolerance_ghz >= rx:
        return "covers"
    return "partial"


# ---------------------------------------------------------------------------
# Roles where frequency coverage matters
# ---------------------------------------------------------------------------

# Stages that MUST cover the user's full RF band (no partial OK). LNA is
# obvious — if it doesn't cover the band, the receiver doesn't work in
# that part of the band. Filters / mixers similarly.
_FULL_COVERAGE_ROLES: frozenset[str] = frozenset({
    "lna", "lna1", "lna2", "lna3",
    "rf_amp", "rfamp",
    "filter", "bpf", "hpf", "lpf", "saw",
    "preselector",
    "limiter",
    "balun",
    "attenuator", "atten", "dsa", "vga",
    "mixer", "mix", "downconverter", "upconverter",
    "splitter", "combiner",
    "switch",  # switch matrices route the full band
})

# Stages where partial coverage is often intentional (multi-LO super-het,
# IF-band ADC). For these we DO check no_overlap (full miss) but we
# DON'T flag partial coverage as a bug.
_OVERLAP_OK_ROLES: frozenset[str] = frozenset({
    "lo", "synth", "synthesizer", "pll", "vco",
    "if_amp", "ifamp", "if",
    "if_filter",
    "adc", "dac",                  # IF-sampling ADCs typically cover IF, not RF
    "tcxo", "ocxo", "xo", "clock",
})


def _normalise_role(row: dict[str, Any]) -> str:
    raw = row.get("role") or row.get("kind") or row.get("name") or ""
    return str(raw).strip().lower()


def _role_requires_full_coverage(row: dict[str, Any]) -> bool:
    """Decide whether a part's role requires full-band coverage. When the
    role is unknown we conservatively assume YES — better a false positive
    that the operator dismisses than a missed band-edge failure."""
    role = _normalise_role(row)
    if role in _FULL_COVERAGE_ROLES:
        return True
    if role in _OVERLAP_OK_ROLES:
        return False
    # Substring fallback for compound role labels like "lna_band1"
    for r in _FULL_COVERAGE_ROLES:
        if r in role:
            return True
    for r in _OVERLAP_OK_ROLES:
        if r in role:
            return False
    return True  # unknown → conservative


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def run_frequency_audit(
    component_recommendations: list[dict[str, Any]],
    design_parameters: Optional[dict[str, Any]],
) -> list[AuditIssue]:
    """Compare every BOM part's spec'd frequency range against the project's
    requested band. Returns AuditIssue rows for the failure modes documented
    at the top of this module.

    Returns empty list (no issues) when:
      - No BOM is supplied
      - The project has no `frequency_range_ghz` (or equivalent) — in this
        case we return a single advisory issue so the operator knows the
        audit didn't run
    """
    issues: list[AuditIssue] = []
    if not component_recommendations:
        return issues

    req = extract_design_freq_range(design_parameters or {})
    if req is None:
        # Advisory — surface a single low-priority issue so the operator
        # knows we couldn't audit the BOM against a band.
        return [AuditIssue(
            severity="medium",
            category="freq_audit_skipped",
            location="design_parameters",
            detail=(
                "Frequency audit skipped: project has no `frequency_range_ghz` "
                "(or equivalent) in design_parameters. Add the band the system "
                "must operate over (e.g. '6-18 GHz', 'X-band') so the gate can "
                "verify every BOM part is actually spec'd for that band."
            ),
            suggested_fix=(
                "In P1, answer the 'What is the RF frequency range of operation?' "
                "question with a numeric range or band name. The audit will "
                "then run automatically on the next P1 lock."
            ),
        )]
    req_min, req_max = req

    for row in component_recommendations:
        spec = extract_part_freq_range(row)
        pn = (
            row.get("part_number")
            or row.get("primary_part")
            or row.get("mpn")
            or "?"
        )
        if spec is None:
            continue  # unknown — don't penalise
        spec_min, spec_max = spec
        rel = freq_band_relationship(spec_min, spec_max, req_min, req_max)
        if rel == "unknown":
            continue
        if rel == "covers":
            continue

        location = f"component_recommendations/{pn}"
        if rel == "no_overlap":
            issues.append(AuditIssue(
                severity="critical",
                category="frequency_no_overlap",
                location=location,
                detail=(
                    f"Part `{pn}` is spec'd for {spec_min:g}-{spec_max:g} GHz "
                    f"but the project requires {req_min:g}-{req_max:g} GHz — "
                    f"the ranges do NOT overlap. This part cannot operate at "
                    f"the requested frequencies."
                ),
                suggested_fix=(
                    f"Replace with a part whose spec'd range covers "
                    f"{req_min:g}-{req_max:g} GHz, or split the design into "
                    f"multiple per-band paths if a single wideband part "
                    f"isn't available."
                ),
            ))
            continue

        # rel == "partial"
        # Decide severity by role: full-coverage roles get high, overlap-OK
        # roles get a softer warning.
        if _role_requires_full_coverage(row):
            issues.append(AuditIssue(
                severity="high",
                category="frequency_partial_coverage",
                location=location,
                detail=(
                    f"Part `{pn}` covers {spec_min:g}-{spec_max:g} GHz but the "
                    f"project requires {req_min:g}-{req_max:g} GHz — the part "
                    f"only covers part of the band. The receiver / signal "
                    f"chain will not meet spec at frequencies outside "
                    f"{spec_min:g}-{spec_max:g} GHz."
                ),
                suggested_fix=(
                    f"Pick a wider-band part (spec'd ≥{req_min:g}-{req_max:g} "
                    f"GHz), or document a per-band switching architecture "
                    f"that uses multiple parts to span the full range."
                ),
            ))
        else:
            # LO/synth/IF — partial overlap may be intentional (e.g. fixed-LO
            # super-het). Soft advisory only.
            issues.append(AuditIssue(
                severity="medium",
                category="frequency_partial_coverage_advisory",
                location=location,
                detail=(
                    f"Part `{pn}` (role tolerates partial coverage) is spec'd "
                    f"for {spec_min:g}-{spec_max:g} GHz against a project "
                    f"requirement of {req_min:g}-{req_max:g} GHz. Confirm this "
                    f"is intentional (e.g. a fixed LO that drives the IF "
                    f"down into the ADC's band, or an IF-band ADC)."
                ),
                suggested_fix=(
                    "If the partial coverage is by design, document the "
                    "architecture in the requirements so this advisory can "
                    "be silenced with a higher-confidence rationale."
                ),
            ))

    return issues


# ---------------------------------------------------------------------------
# Noise-figure budget audit — front-end LNA NF must enable system NF claim
# ---------------------------------------------------------------------------

# Front-end roles whose NF dominates the cascaded system NF (Friis: F1
# contributes 1×, downstream stages are divided by upstream gain).
_FRONT_END_ROLES: frozenset[str] = frozenset({
    "lna", "lna1",
    "preselector", "preselect",
    "limiter",                     # contributes loss → adds to NF
    "filter", "bpf",              # if before LNA, adds loss = NF
})


def _extract_design_nf_target(dp: dict[str, Any]) -> Optional[float]:
    """Pull the project's claimed system NF target (in dB) from
    design_parameters. Tries common keys."""
    if not dp:
        return None
    for key in (
        "noise_figure_db",
        "nf_db",
        "system_nf_db",
        "nf_target_db",
        "target_nf_db",
    ):
        v = dp.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def _extract_part_nf_db(row: dict[str, Any]) -> Optional[float]:
    """Pull a part's NF (dB) from a BOM row. Checks structured fields first,
    then key_specs nested dict."""
    for key in ("nf_db", "noise_figure_db"):
        v = row.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    ks = row.get("key_specs")
    if isinstance(ks, dict):
        return _extract_part_nf_db(ks)
    return None


def run_nf_budget_audit(
    component_recommendations: list[dict[str, Any]],
    design_parameters: Optional[dict[str, Any]],
    *,
    front_end_headroom_factor: float = 0.7,
) -> list[AuditIssue]:
    """Front-end NF discipline check (Friis-derived).

    A receiver's first-stage NF dominates the system NF — the rule of thumb
    is the front-end LNA NF should be no more than ~70 % of the claimed
    system NF (so downstream stages have headroom to contribute). When an
    LNA in the BOM has NF >= system NF target, the system NF claim is
    arithmetically impossible.

    Returns:
      - `lna_nf_exceeds_system` (critical) — LNA NF >= system NF target.
        Friis bound is hard: cascaded NF can never be less than the first
        stage's NF. Any claim below that is wrong by construction.
      - `lna_nf_too_high` (high) — LNA NF > headroom_factor * system NF.
        Possible (cascade math could close) but very tight; demands
        confirmation.
    """
    if not component_recommendations:
        return []
    nf_target = _extract_design_nf_target(design_parameters or {})
    if nf_target is None:
        return []  # no system NF claim to compare against — silent

    issues: list[AuditIssue] = []
    for row in component_recommendations:
        role = _normalise_role(row)
        if role not in _FRONT_END_ROLES and not any(r in role for r in _FRONT_END_ROLES):
            continue
        nf = _extract_part_nf_db(row)
        if nf is None:
            continue
        pn = (
            row.get("part_number")
            or row.get("primary_part")
            or row.get("mpn")
            or "?"
        )
        location = f"component_recommendations/{pn}"
        if nf >= nf_target:
            issues.append(AuditIssue(
                severity="critical",
                category="lna_nf_exceeds_system",
                location=location,
                detail=(
                    f"Front-end part `{pn}` (role={role}) has NF "
                    f"{nf:.2f} dB >= system NF target {nf_target:.2f} dB. "
                    f"Friis says the cascaded NF cannot be less than the "
                    f"first stage's NF — the system NF claim is "
                    f"arithmetically impossible with this part."
                ),
                suggested_fix=(
                    f"Pick a front-end with NF <= {nf_target * front_end_headroom_factor:.2f} dB "
                    f"to leave headroom for downstream stages, OR relax the "
                    f"system NF target above {nf:.2f} dB."
                ),
            ))
            continue
        if nf > nf_target * front_end_headroom_factor:
            issues.append(AuditIssue(
                severity="high",
                category="lna_nf_too_high",
                location=location,
                detail=(
                    f"Front-end part `{pn}` (role={role}) has NF "
                    f"{nf:.2f} dB, more than "
                    f"{front_end_headroom_factor*100:.0f}% of the system "
                    f"NF target {nf_target:.2f} dB. Cascade math may close "
                    f"but only with very low-loss / high-gain downstream "
                    f"stages — confirm or pick a lower-NF LNA."
                ),
                suggested_fix=(
                    f"Pick a front-end LNA with NF <= "
                    f"{nf_target * front_end_headroom_factor:.2f} dB."
                ),
            ))
    return issues


# ---------------------------------------------------------------------------
# Supply-voltage compatibility audit
# ---------------------------------------------------------------------------

def _extract_design_rails(dp: dict[str, Any]) -> Optional[list[float]]:
    """Pull the project's available supply rails (volts) from
    design_parameters. Accepts `supply_rails_v` (list of numbers) or
    `available_rails` (free text like "5V, 3.3V, 1.8V")."""
    if not dp:
        return None
    rails = dp.get("supply_rails_v") or dp.get("supply_rails") or dp.get("available_rails")
    if rails is None:
        return None
    if isinstance(rails, (list, tuple)):
        out = []
        for r in rails:
            try:
                out.append(float(r))
            except (TypeError, ValueError):
                pass
        return out or None
    if isinstance(rails, (int, float)):
        return [float(rails)]
    if isinstance(rails, str):
        # Parse free-text like "5V, 3.3V, 1.8V" or "5, 3.3, 1.8"
        out = []
        for tok in re.findall(r"\d+(?:\.\d+)?", rails):
            try:
                out.append(float(tok))
            except ValueError:
                pass
        return out or None
    return None


def _extract_part_supply_v(row: dict[str, Any]) -> Optional[float]:
    """Pull a part's nominal supply voltage from a BOM row. Tries
    structured fields first, then nested key_specs."""
    for key in ("supply_voltage", "supply_voltage_v", "vdd_v", "vcc_v"):
        v = row.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    ks = row.get("key_specs")
    if isinstance(ks, dict):
        return _extract_part_supply_v(ks)
    return None


def _extract_part_input_v_range(row: dict[str, Any]) -> Optional[tuple[float, float]]:
    """Pull a regulator's input-voltage range from a BOM row. Returns
    `(min_v, max_v)` or None when the row has no input-range info.
    Used by `run_supply_voltage_audit` to catch "12 V supply driving an
    LDO with 2.5-5.5 V input range" scenarios (rx-output-audit B1.8).

    Tries structured numeric fields first, then `key_specs`, then
    free-text parsing of the description (e.g.
    "input_voltage: 2.5 - 5.5 V").
    """
    # Structured min/max fields
    for min_key, max_key in (
        ("min_input_v", "max_input_v"),
        ("input_min_v", "input_max_v"),
        ("vin_min_v", "vin_max_v"),
        ("vin_min", "vin_max"),
    ):
        v_min = row.get(min_key)
        v_max = row.get(max_key)
        if v_min is not None and v_max is not None:
            try:
                return (float(v_min), float(v_max))
            except (TypeError, ValueError):
                pass
    # Free-text input_voltage / vin_range field — parse a "2.5 - 5.5 V" range
    for key in ("input_voltage", "vin_range", "input_voltage_v",
                "input_voltage_range"):
        v = row.get(key)
        if v is not None:
            text = str(v)
            # Match ranges like "2.5 - 5.5 V", "2.5V to 5.5V"
            import re as _re
            m = _re.search(
                r"(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)",
                text,
            )
            if m:
                try:
                    a, b = float(m.group(1)), float(m.group(2))
                    return (min(a, b), max(a, b))
                except ValueError:
                    pass
    # Nested key_specs
    for nested_key in ("primary_key_specs", "key_specs", "specs"):
        nested = row.get(nested_key)
        if isinstance(nested, dict):
            r = _extract_part_input_v_range(nested)
            if r is not None:
                return r
    return None


def _is_regulator_role(row: dict[str, Any]) -> bool:
    """True if the part is a regulator / LDO / DC-DC. The input-voltage
    range check only fires for these — non-regulator parts have no
    'input range' concept (they have a supply pin)."""
    role = (
        row.get("role")
        or row.get("kind")
        or row.get("function")
        or ""
    ).lower()
    desc = (
        row.get("description")
        or row.get("primary_description")
        or row.get("name")
        or ""
    ).lower()
    keywords = ("ldo", "regulator", "dcdc", "dc-dc", "buck", "boost",
                "buck-boost", "smps", "switching converter",
                "linear regulator", "voltage regulator")
    return any(k in role for k in keywords) or any(k in desc for k in keywords)


def run_supply_voltage_audit(
    component_recommendations: list[dict[str, Any]],
    design_parameters: Optional[dict[str, Any]],
    *,
    tolerance_v: float = 0.15,
) -> list[AuditIssue]:
    """Verify every part's required supply voltage is one of the project's
    available rails (within +/- tolerance_v). Catches the "5 V LNA in a
    3.3 V-only design" picks.

    Also (rx-output-audit B1.8): for regulators / LDOs / DC-DCs, verify
    the project's PRIMARY supply voltage falls within the part's
    input-voltage RANGE. The MAX25301 case: project supplies 12 V but
    MAX25301 input range is 2.5-5.5 V — the LDO would fry. Pre-fix the
    audit didn't check this.

    `tolerance_v` (default 150 mV) absorbs minor rail drift — a 3.3 V part
    works fine on a 3.27 V rail.
    """
    if not component_recommendations:
        return []
    rails = _extract_design_rails(design_parameters or {})
    if not rails:
        return []  # no rail list — can't audit, silent

    # Project's primary input voltage — used to check regulator input
    # ranges. Defaults to the highest rail when explicit primary isn't set.
    primary_v: Optional[float] = None
    if design_parameters:
        for key in ("primary_input_v", "input_voltage_v",
                    "supply_voltage_v", "supply_voltage", "vin_v", "vin"):
            v = design_parameters.get(key)
            if v is not None:
                try:
                    primary_v = float(v)
                    break
                except (TypeError, ValueError):
                    pass
    if primary_v is None and rails:
        primary_v = max(rails)

    issues: list[AuditIssue] = []
    for row in component_recommendations:
        pn = (
            row.get("part_number")
            or row.get("primary_part")
            or row.get("mpn")
            or "?"
        )

        # Rule A: required supply voltage matches an available rail.
        v = _extract_part_supply_v(row)
        if v is not None:
            compatible = any(abs(v - r) <= tolerance_v for r in rails)
            if not compatible:
                issues.append(AuditIssue(
                    severity="high",
                    category="supply_voltage_mismatch",
                    location=f"component_recommendations/{pn}",
                    detail=(
                        f"Part `{pn}` requires {v:g} V but the project's "
                        f"available rails are {rails}. No matching rail "
                        f"within +/-{tolerance_v} V — the part cannot be "
                        f"powered as the design currently stands."
                    ),
                    suggested_fix=(
                        f"Either add a {v:g} V rail (LDO / DC-DC) to the "
                        f"power design, or pick a part variant operating "
                        f"from one of {rails}. Some parts have multi-rail "
                        f"variants — check the datasheet."
                    ),
                ))

        # Rule B (rx-output-audit B1.8): regulator input-voltage range
        # check. Only fires for regulator-role parts. Catches the
        # "MAX25301 LDO with 2.5-5.5 V input fed from 12 V" case.
        if primary_v is not None and _is_regulator_role(row):
            in_range = _extract_part_input_v_range(row)
            if in_range is not None:
                lo, hi = in_range
                if primary_v > hi + tolerance_v or primary_v < lo - tolerance_v:
                    issues.append(AuditIssue(
                        severity="critical",
                        category="regulator_input_range_violation",
                        location=f"component_recommendations/{pn}",
                        detail=(
                            f"Regulator `{pn}` has datasheet input range "
                            f"{lo:g}-{hi:g} V but the project's primary "
                            f"supply is {primary_v:g} V. The part cannot "
                            f"safely accept the supply voltage and will "
                            f"fail (over-voltage destruction or thermal "
                            f"runaway)."
                        ),
                        suggested_fix=(
                            f"Pick a regulator whose input range includes "
                            f"{primary_v:g} V (e.g. for 12 V→5 V/3.3 V "
                            f"step-down, use a wide-input buck like "
                            f"TPS54620 or LMR16006), OR add a pre-regulator "
                            f"that drops {primary_v:g} V to within "
                            f"{lo:g}-{hi:g} V before this stage."
                        ),
                    ))
    return issues


# ---------------------------------------------------------------------------
# Architecture topology constraint audit (rx-output-audit B1.9)
# ---------------------------------------------------------------------------
# Switch_matrix designs have a fixed topology constraint: the gain block
# (when present) MUST come AFTER the matrix, not before. The GLB
# optimizer's generic "promote LNA toward the front" heuristic violates
# this for switch_matrix and produces a topology that can't actually be
# built (you can't put 8 gain blocks before a 4×8 matrix; you have 4
# input ports). Pre-fix this went undetected because no audit checks
# topology vs project_type.

# Per project_type, the canonical signal-chain ordering — used to detect
# when the GLB has stages out of position. Each entry maps a role to its
# canonical position index in the chain (lower = closer to RF input).
_ARCH_TOPOLOGY: dict[str, dict[str, tuple[int, str]]] = {
    "switch_matrix": {
        # input port → limiter → switch fabric → gain block → output port
        "antenna":     (0,  "input"),
        "connector":   (0,  "input"),
        "input":       (0,  "input"),
        "limiter":     (10, "before_fabric"),
        "preselector": (10, "before_fabric"),
        "preselect":   (10, "before_fabric"),
        "filter":      (10, "before_fabric"),
        "bpf":         (10, "before_fabric"),
        "switch":      (20, "fabric"),
        "sw":          (20, "fabric"),
        "rf_amp":      (30, "after_fabric"),  # gain block AFTER matrix
        "lna":         (30, "after_fabric"),
        "amp":         (30, "after_fabric"),
        "gain_block":  (30, "after_fabric"),
        "output":      (40, "output"),
    },
    "receiver": {
        # antenna → limiter/filter → LNA → mixer → IF amp → ADC
        "antenna":      (0,  "front"),
        "connector":    (0,  "front"),
        "input":        (0,  "front"),
        "limiter":      (5,  "front"),
        "preselector":  (8,  "front"),
        "preselect":    (8,  "front"),
        "filter":       (10, "front"),
        "bpf":          (10, "front"),
        "lna":          (15, "front"),
        "rf_amp":       (15, "front"),
        "mixer":        (20, "freq_conv"),
        "if_amp":       (25, "if"),
        "if":           (25, "if"),
        "if_filter":    (28, "if"),
        "adc":          (40, "back"),
        "output":       (50, "back"),
    },
}


def _normalise_role_for_topology(row: dict[str, Any]) -> str:
    """Pull a normalised role from a BOM row for topology classification."""
    role = (row.get("role") or row.get("kind") or "").strip().lower()
    if role:
        return role
    # Fallback to name / function / description heuristics
    text = " ".join([
        str(row.get("name") or ""),
        str(row.get("function") or ""),
        str(row.get("primary_description") or ""),
        str(row.get("description") or ""),
    ]).lower()
    keywords = {
        "limiter": "limiter",
        "preselector": "preselector",
        "filter": "filter",
        "switch": "switch",
        "amplifier": "rf_amp",
        "amp ": "rf_amp",
        "gain block": "rf_amp",
        "lna": "lna",
        "mixer": "mixer",
        "antenna": "antenna",
        "connector": "connector",
        "adc": "adc",
    }
    for kw, mapped in keywords.items():
        if kw in text:
            return mapped
    return ""


def run_topology_constraint_audit(
    component_recommendations: list[dict[str, Any]],
    design_parameters: Optional[dict[str, Any]],
) -> list[AuditIssue]:
    """For architectures with a fixed signal-chain topology
    (switch_matrix, super-heterodyne receiver), verify the GLB / BOM
    role ordering matches the canonical layout. Catches the rx-output
    `optimizer promoted gain block to before the matrix` failure mode.

    Returns issues at severity=high — topology violations are real bugs
    that ship a non-functional system, but the rendering still completes
    so the operator can review.
    """
    if not component_recommendations or not design_parameters:
        return []

    project_type = str(
        design_parameters.get("project_type")
        or design_parameters.get("architecture")
        or ""
    ).strip().lower()
    if project_type not in _ARCH_TOPOLOGY:
        return []  # no canonical topology defined for this arch

    role_map = _ARCH_TOPOLOGY[project_type]
    issues: list[AuditIssue] = []

    if project_type == "switch_matrix":
        # The dominant constraint: gain block must come AFTER any switch.
        # Find the position of the FIRST switch and the FIRST gain block.
        first_switch_idx: Optional[int] = None
        first_gain_idx: Optional[int] = None
        first_gain_pn = ""
        for i, row in enumerate(component_recommendations):
            role = _normalise_role_for_topology(row)
            if role in ("switch", "sw") and first_switch_idx is None:
                first_switch_idx = i
            if role in ("rf_amp", "lna", "amp", "gain_block") and first_gain_idx is None:
                first_gain_idx = i
                first_gain_pn = (
                    row.get("part_number")
                    or row.get("primary_part") or "?"
                )
        if (
            first_switch_idx is not None and first_gain_idx is not None
            and first_gain_idx < first_switch_idx
        ):
            issues.append(AuditIssue(
                severity="high",
                category="architecture_topology_violation",
                location=f"component_recommendations/{first_gain_pn}",
                detail=(
                    f"Switch matrix architecture requires the gain block to "
                    f"appear AFTER the switch fabric (it compensates the "
                    f"matrix insertion loss on the OUTPUT side). The BOM "
                    f"places `{first_gain_pn}` (gain block, position "
                    f"{first_gain_idx + 1}) before the first switch "
                    f"(position {first_switch_idx + 1}). The GLB optimizer "
                    f"may have applied the generic 'promote LNA toward "
                    f"the front' heuristic which is invalid for switch "
                    f"matrices."
                ),
                suggested_fix=(
                    f"Reorder the BOM / GLB so the switch fabric stages "
                    f"come first, then the gain block on each output path. "
                    f"For an N×M matrix, expect N input ports + N "
                    f"limiters + the switch fabric + M gain blocks + M "
                    f"output ports."
                ),
            ))

    if project_type == "receiver":
        # Constraint 1: LNA before mixer.
        lna_idx: Optional[int] = None
        mixer_idx: Optional[int] = None
        for i, row in enumerate(component_recommendations):
            role = _normalise_role_for_topology(row)
            if role == "lna" and lna_idx is None:
                lna_idx = i
            if role == "mixer" and mixer_idx is None:
                mixer_idx = i
        if (
            lna_idx is not None and mixer_idx is not None
            and lna_idx > mixer_idx
        ):
            issues.append(AuditIssue(
                severity="high",
                category="architecture_topology_violation",
                location="component_recommendations",
                detail=(
                    f"Receiver architecture requires the LNA before the "
                    f"mixer (Friis: LNA NF dominates only when it's the "
                    f"first amplifying stage). The BOM places mixer at "
                    f"position {mixer_idx + 1} ahead of LNA at position "
                    f"{lna_idx + 1}."
                ),
                suggested_fix=(
                    "Reorder so the LNA precedes the mixer. Placing the "
                    "mixer first means the mixer's conversion-loss + NF "
                    "directly add to the system NF instead of being divided "
                    "down by the LNA gain — typically 8-12 dB worse system NF."
                ),
            ))

    return issues


# ---------------------------------------------------------------------------
# Role-vs-description semantic match audit (rx-output-audit B1.7)
# ---------------------------------------------------------------------------
# The ASWD-S2-0009-Q-T case: declared `role: "connector"` but the
# description is "RF Switch ICs Automotive Wideband GaAs SPDT RF Switch"
# — a switch IC labelled as a connector. The fact that the description's
# semantic class disagrees with the declared role is a hallmark of an
# LLM hallucination.

# Keywords that strongly signal a part class, used for cross-checking
# `role` vs `description`. Order matters — more-specific classes first
# so a "diode limiter" classifies as limiter not diode.
_ROLE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("limiter",     ("limiter", "pin diode limiter")),
    ("preselector", ("preselector", "preselect filter")),
    ("filter",      ("bandpass filter", "lowpass filter", "highpass filter",
                     "saw filter", "ceramic filter", " bpf ", " lpf ", " hpf ")),
    ("mixer",       ("mixer", "downconverter", "upconverter", "double-balanced")),
    ("lna",         ("low-noise amp", "low noise amp", "lna ", "lna,", "lna.")),
    ("rf_amp",      ("gain block", "rf amplifier", " amp ", "driver amp",
                     "power amp", " pa ", "vga", "variable gain")),
    ("switch",      ("rf switch", "spdt", "spnt", "switch ic", "absorptive switch",
                     "reflective switch")),
    ("synth",       ("synthesizer", "synthesiser", "pll", "vco")),
    ("oscillator",  ("oscillator", "tcxo", "ocxo", " xo ", "crystal osc")),
    ("adc",         ("analog-to-digital", "adc", "a/d converter")),
    ("dac",         ("digital-to-analog", "dac", "d/a converter")),
    ("attenuator",  ("attenuator", "fixed pad", "dsa")),
    ("connector",   ("sma connector", "n-type connector", "smb connector",
                     "panel-mount connector", "panel mount connector",
                     "rf connector", "coaxial connector", "bnc connector",
                     "sma female", "sma male", "panel mount sma",
                     "smt connector", "edge-launch")),
    ("ldo",         ("ldo", "low-dropout", "linear regulator")),
    ("dcdc",        ("buck converter", "boost converter", "dc-dc",
                     "switching regulator", "buck regulator",
                     "buck-boost", "smps")),
    ("fpga",        ("fpga", "field-programmable")),
    ("mcu",         ("microcontroller", " mcu ", "stm32", "atmega")),
    ("antenna",     ("antenna",)),
    ("balun",       ("balun",)),
    ("isolator",    ("isolator", "circulator")),
)


def _detect_role_from_description(text: str) -> set[str]:
    """Return the set of role classes implied by free-text description.
    Multiple matches possible (e.g. "RF gain block amplifier" hits both
    rf_amp keywords)."""
    if not text:
        return set()
    low = " " + text.lower() + " "
    detected: set[str] = set()
    for role, keywords in _ROLE_KEYWORDS:
        for kw in keywords:
            if kw in low:
                detected.add(role)
                break
    return detected


def _is_role_compatible(declared: str, detected: set[str]) -> bool:
    """True if the declared role and the detected description-roles agree.
    A "rf_amp" declared as "amp" / "amplifier" is compatible. A "connector"
    declared part with a "switch" detected role is NOT compatible."""
    if not detected:
        return True  # no detection = no contradiction
    declared_low = declared.lower()
    # Direct match
    if declared_low in detected:
        return True
    # Family match (e.g. "rf_amp" inside "amp" family)
    family_aliases = {
        "rf_amp":   ("rf_amp", "lna", "amp"),
        "lna":      ("rf_amp", "lna"),
        "amp":      ("rf_amp", "lna"),
        "gain_block": ("rf_amp",),
        "limiter":  ("limiter",),
        "filter":   ("filter",),
        "bpf":      ("filter",),
        "lpf":      ("filter",),
        "hpf":      ("filter",),
        "switch":   ("switch",),
        "sw":       ("switch",),
        "mixer":    ("mixer",),
        "synth":    ("synth",),
        "pll":      ("synth",),
        "vco":      ("synth",),
        "tcxo":     ("oscillator",),
        "ocxo":     ("oscillator",),
        "xo":       ("oscillator",),
        "clock":    ("oscillator", "synth"),
        "adc":      ("adc",),
        "dac":      ("dac",),
        "ldo":      ("ldo",),
        "regulator": ("ldo", "dcdc"),
        "dcdc":     ("dcdc",),
        "buck":     ("dcdc",),
        "boost":    ("dcdc",),
        "connector": ("connector",),
        "fpga":     ("fpga",),
        "mcu":      ("mcu",),
        "antenna":  ("antenna",),
    }
    aliases = family_aliases.get(declared_low, ())
    return any(a in detected for a in aliases)


def run_role_semantic_audit(
    component_recommendations: list[dict[str, Any]],
    design_parameters: Optional[dict[str, Any]] = None,
) -> list[AuditIssue]:
    """Detect parts whose declared `role` (or implicit role from BOM
    position) disagrees with the part-class implied by the description.

    The canonical case (rx-output-audit B1.7): ASWD-S2-0009-Q-T listed
    as the SMA panel-mount connector with description *"RF Switch ICs
    Automotive Wideband GaAs SPDT RF Switch (referenced for RF
    connector-grade system component)"* — clearly a switch IC, not a
    connector.

    Returns issues at severity=critical when the mismatch is unambiguous
    (e.g. role=connector but description says "switch IC").
    """
    if not component_recommendations:
        return []
    issues: list[AuditIssue] = []
    for row in component_recommendations:
        declared_role = (
            row.get("role") or row.get("kind") or row.get("function") or ""
        ).strip().lower()
        if not declared_role:
            continue
        # Pull description from any of the standard fields
        desc = " ".join(filter(None, [
            str(row.get("name") or ""),
            str(row.get("function") or ""),
            str(row.get("description") or ""),
            str(row.get("primary_description") or ""),
        ])).strip()
        if not desc:
            continue
        detected = _detect_role_from_description(desc)
        if _is_role_compatible(declared_role, detected):
            continue
        # Mismatch — but only flag the critical / unambiguous cases.
        # If the declared role is "connector" but description matches
        # "switch", that's a critical flag. Other mismatches (e.g.
        # role=lna but desc mentions both LNA and amp) are usually fine.
        unambiguous_pairs = {
            ("connector", "switch"), ("connector", "rf_amp"),
            ("connector", "lna"), ("connector", "mixer"),
            ("connector", "filter"), ("connector", "synth"),
            ("connector", "ldo"), ("connector", "dcdc"),
            ("connector", "fpga"), ("connector", "mcu"),
            ("ldo", "switch"), ("ldo", "rf_amp"),
            ("rf_amp", "connector"),
            ("filter", "switch"), ("filter", "connector"),
            ("switch", "connector"), ("switch", "rf_amp"),
            ("antenna", "switch"), ("antenna", "rf_amp"),
        }
        critical_flag = any(
            (declared_role, d) in unambiguous_pairs for d in detected
        )
        pn = (
            row.get("part_number") or row.get("primary_part")
            or row.get("mpn") or "?"
        )
        if critical_flag:
            issues.append(AuditIssue(
                severity="critical",
                category="role_description_mismatch",
                location=f"component_recommendations/{pn}",
                detail=(
                    f"Part `{pn}` is declared with role=`{declared_role}` "
                    f"but the description implies role(s) "
                    f"{sorted(detected)}. This is the canonical signature "
                    f"of an LLM hallucination — the part-class doesn't "
                    f"match the slot it was placed in."
                ),
                suggested_fix=(
                    f"Replace `{pn}` with a part whose datasheet matches "
                    f"role=`{declared_role}`. For SMA connectors, use "
                    f"Amphenol 132xxx / 901 series, Cinch SMA-50, Molex "
                    f"73251 or similar — NOT a switch IC. For switches, "
                    f"use proper RF switch MPNs like ADRF5040, PE42522, "
                    f"HMC1118."
                ),
            ))
    return issues


# ---------------------------------------------------------------------------
# Cascade-vs-claim audit — RX/switch_matrix/transceiver IIP3 + gain
# ---------------------------------------------------------------------------
# Pre-fix the existing `run_tx_cascade_audit` only fired for TX projects.
# RX / switch_matrix / receiver projects had NO check for IIP3 or gain
# claim vs computed cascade. The rx-output-audit found the "+65 dBm IIP3
# claimed but ZVA-183WA-S+ gives ~+14 dBm IIP3" bug (B1.6) and the
# "near-0 dB net gain claimed but cascade computes +10.8 dB" bug (B1.16)
# both went silent.

def _normalise_direction(dp: dict[str, Any]) -> str:
    """Map design_parameters.direction / project_type to cascade direction.
    Same alias map as `tools.rf_cascade.compute_cascade`."""
    if not dp:
        return "rx"
    raw = str(
        dp.get("direction")
        or dp.get("project_type")
        or ""
    ).strip().lower()
    aliases = {
        "receiver": "rx", "rx": "rx",
        "transmitter": "tx", "tx": "tx",
        "transceiver": "tx",
        "switch_matrix": "rx",
        "power_supply": "none",
    }
    return aliases.get(raw, "rx")


def run_cascade_claims_audit(
    component_recommendations: list[dict[str, Any]],
    design_parameters: Optional[dict[str, Any]],
    *,
    iip3_shortfall_db: float = 2.0,
    gain_overshoot_db: float = 3.0,
    nf_overshoot_db: float = 1.0,
) -> list[AuditIssue]:
    """Compare claimed cascade specs (IIP3, total gain, NF) against the
    Friis-computed cascade values. Direction-agnostic — fires for RX,
    TX, switch_matrix, transceiver. Catches:

      - rx-output-audit B1.6: "+65 dBm IIP3 claimed" vs computed ~+14
      - rx-output-audit B1.16: "near-0 dB net gain claimed" vs computed +10.8

    Tolerances are deliberately generous (default ±2 dB IIP3, ±3 dB gain,
    ±1 dB NF) — flag only the obvious arithmetic-impossibility cases.
    """
    if not component_recommendations or not design_parameters:
        return []
    direction = _normalise_direction(design_parameters)
    if direction == "none":
        return []  # power-supply project, no RF cascade

    try:
        from tools.rf_cascade import compute_cascade
    except Exception:
        return []

    # Resolve each claim with explicit None checks — `or` chains break
    # when a legitimate claim is 0 (e.g. switch_matrix with claimed
    # total_gain_db = 0 means "near-0 dB net path loss").
    def _first_set(*keys: str) -> Any:
        for k in keys:
            v = design_parameters.get(k)
            if v is not None:
                return v
        return None

    cascade = compute_cascade(
        list(component_recommendations),
        direction=direction,
        claimed_iip3_dbm=_first_set("iip3_dbm", "iip3_dbm_input"),
        claimed_total_gain_db=_first_set("total_gain_db", "system_gain_db"),
        claimed_nf_db=_first_set("noise_figure_db", "nf_db"),
        claimed_pout_dbm=_first_set("pout_dbm", "output_power_dbm"),
        claimed_oip3_dbm=_first_set("oip3_dbm"),
    )
    totals = cascade.get("totals") or {}
    claims = cascade.get("claims") or {}
    verdict = cascade.get("verdict") or {}

    issues: list[AuditIssue] = []

    # IIP3 shortfall (rx-output-audit B1.6) — fires when the claimed IIP3
    # is meaningfully higher than what the cascade can deliver.
    if (
        direction in ("rx",)  # tx is covered by run_tx_cascade_audit
        and claims.get("iip3_dbm") is not None
        and totals.get("iip3_dbm") is not None
    ):
        shortfall = float(claims["iip3_dbm"]) - float(totals["iip3_dbm"])
        if shortfall > iip3_shortfall_db:
            issues.append(AuditIssue(
                severity="critical",
                category="iip3_cascade_shortfall",
                location="design_parameters/iip3_dbm",
                detail=(
                    f"Claimed system IIP3 {claims['iip3_dbm']:.1f} dBm but "
                    f"the Friis cascade computes {totals['iip3_dbm']:.1f} dBm "
                    f"(shortfall {shortfall:.1f} dB). The claim is "
                    f"arithmetically unreachable with the current BOM — "
                    f"the dominant linearity stage caps system IIP3."
                ),
                suggested_fix=(
                    f"Pick a higher-IIP3 amplifier / mixer in the dominant "
                    f"stage (cascade IIP3 is set by the LAST stage in RX, "
                    f"divided down by the cumulative gain ahead of it), OR "
                    f"relax the claimed IIP3 below {totals['iip3_dbm']:.1f} dBm."
                ),
            ))

    # Gain target overshoot (rx-output-audit B1.16) — fires when computed
    # gain is well over the claimed target. Switch matrices are the
    # canonical case (claim "near-0 dB" but pick a 18 dB amp to compensate
    # 2 dB matrix loss → 16 dB overshoot).
    claimed_gain = claims.get("total_gain_db")
    computed_gain = totals.get("gain_db")
    if claimed_gain is not None and computed_gain is not None:
        overshoot = float(computed_gain) - float(claimed_gain)
        if abs(overshoot) > gain_overshoot_db:
            severity = "high" if abs(overshoot) > 2 * gain_overshoot_db else "medium"
            direction_word = "exceeds" if overshoot > 0 else "falls short of"
            issues.append(AuditIssue(
                severity=severity,
                category="gain_target_mismatch",
                location="design_parameters/total_gain_db",
                detail=(
                    f"Computed cascade gain {computed_gain:.1f} dB "
                    f"{direction_word} the claimed target {claimed_gain:.1f} dB "
                    f"by {abs(overshoot):.1f} dB. For a switch matrix this "
                    f"often means the gain block is over-spec'd "
                    f"(amplifying signal beyond the loss it's compensating)."
                ),
                suggested_fix=(
                    f"For an overshoot, swap to a lower-gain amp (target "
                    f"+gain ≈ matrix loss + ~2 dB margin) or remove the "
                    f"gain stage entirely. For a shortfall, add an "
                    f"additional gain stage or pick a higher-gain part."
                ),
            ))

    # NF overshoot (defensive — rare in practice, but cheap to check)
    claimed_nf = claims.get("nf_db")
    computed_nf = totals.get("nf_db")
    if claimed_nf is not None and computed_nf is not None:
        excess = float(computed_nf) - float(claimed_nf)
        if excess > nf_overshoot_db:
            issues.append(AuditIssue(
                severity="high",
                category="nf_cascade_overshoot",
                location="design_parameters/noise_figure_db",
                detail=(
                    f"Cascaded NF {computed_nf:.2f} dB exceeds the claimed "
                    f"target {claimed_nf:.2f} dB by {excess:.2f} dB. The "
                    f"design cannot meet the system NF spec with this BOM."
                ),
                suggested_fix=(
                    f"Reduce the front-end NF (lower-NF LNA), reduce "
                    f"pre-LNA passive losses, or relax the system NF "
                    f"target above {computed_nf:.2f} dB."
                ),
            ))

    return issues
