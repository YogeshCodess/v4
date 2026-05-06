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


def run_supply_voltage_audit(
    component_recommendations: list[dict[str, Any]],
    design_parameters: Optional[dict[str, Any]],
    *,
    tolerance_v: float = 0.15,
) -> list[AuditIssue]:
    """Verify every part's required supply voltage is one of the project's
    available rails (within +/- tolerance_v). Catches the "5 V LNA in a
    3.3 V-only design" picks.

    `tolerance_v` (default 150 mV) absorbs minor rail drift — a 3.3 V part
    works fine on a 3.27 V rail.
    """
    if not component_recommendations:
        return []
    rails = _extract_design_rails(design_parameters or {})
    if not rails:
        return []  # no rail list — can't audit, silent

    issues: list[AuditIssue] = []
    for row in component_recommendations:
        v = _extract_part_supply_v(row)
        if v is None:
            continue
        pn = (
            row.get("part_number")
            or row.get("primary_part")
            or row.get("mpn")
            or "?"
        )
        # Compatible if within tolerance of any rail
        compatible = any(abs(v - r) <= tolerance_v for r in rails)
        if compatible:
            continue
        issues.append(AuditIssue(
            severity="high",
            category="supply_voltage_mismatch",
            location=f"component_recommendations/{pn}",
            detail=(
                f"Part `{pn}` requires {v:g} V but the project's available "
                f"rails are {rails}. No matching rail within +/-{tolerance_v} V "
                f"— the part cannot be powered as the design currently stands."
            ),
            suggested_fix=(
                f"Either add a {v:g} V rail (LDO / DC-DC) to the power "
                f"design, or pick a part variant operating from one of "
                f"{rails}. Some parts have multi-rail variants — check the "
                f"datasheet."
            ),
        ))
    return issues
