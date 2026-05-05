"""
Banned manufacturers + obsolete / NRND part rules — P1.5.

The requirements_agent system prompt lists these in natural language
("BANNED MANUFACTURER: VPT Inc.", obsolete HMC-series patterns, etc.).
The LLM is expected to respect them, but nothing enforces it. That's
unsafe for a defence-grade pipeline — a hallucinated part number can
still slip through.

This module codifies those rules so they're applied as a **hard filter**
on every `component_recommendations` payload before it reaches the BOM:

    kept, rejected = filter_components(bom_list)

Rejected components come back with a `_rejection_reason` key so the
audit report can surface them.

Keeping the rules as data (two frozensets + a regex list) makes them
trivial to extend — no agent prompt-tuning required.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional


def normalize_mpn(mpn: Optional[str]) -> str:
    """Canonical MPN form used everywhere we compare part numbers.

    All sites previously did `.strip().upper()` inline (over a dozen
    locations). This helper exists so any future change to the canonical
    form (e.g. dash collapsing) only has to be made once. New code SHOULD
    call this; existing call sites are equivalent and don't need to be
    touched.
    """
    return (mpn or "").strip().upper()


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

# Case-insensitive exact-match manufacturer names that must never ship.
# Extend when procurement / counter-intelligence flags new suppliers.
BANNED_MANUFACTURERS: frozenset[str] = frozenset(
    name.lower() for name in (
        # ITAR / policy
        "VPT", "VPT Inc.", "VPT, Inc.", "VPT Inc",
    )
)

# Regex patterns matched against `part_number` (case-insensitive).
# Each tuple is (pattern, reason).
BANNED_PART_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # HMC-family parts ADI has formally discontinued / NRND'd. The
    # requirements prompt lists these in prose — codified here so the LLM
    # can't sneak one back in under a hallucinated "lifecycle_status: active".
    (re.compile(r"^HMC-?C024\b", re.IGNORECASE), "HMC-C024 is EOL (ADI NRND)"),
    (re.compile(r"^HMC-?1040", re.IGNORECASE), "HMC-1040 family is NRND"),
    (re.compile(r"^HMC-?1049LP5CE\b", re.IGNORECASE), "HMC-1049LP5CE is obsolete — use HMC1049LP5E"),
    (re.compile(r"^HMC-?753\b", re.IGNORECASE), "HMC753 is NRND — use HMC-C017 or similar"),
    (re.compile(r"^HMC-?C017\b", re.IGNORECASE), "HMC-C017 is NRND (ADI 2023)"),
)

# Connectorised / coaxial RF modules — these come with SMA / N-type / BNC
# jacks already attached and CANNOT be soldered to a PCB. The LLM sometimes
# recommends them in BOMs because they hit the spec on paper (e.g. Pasternack
# PE8022 is a 2-18 GHz limiter with 0.5 dB IL — looks great), but the PCB
# designer can't actually place one. Reject any candidate that matches a
# known coax-module pattern.
#
# Format: (manufacturer-fragment-lowercase, part-number-regex, reason).
# Manufacturer matching is substring-based on the lowercased mfr field so
# variants ("Pasternack Enterprises", "Pasternack, Inc.") all hit.
CONNECTORISED_BY_MFR_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("pasternack", re.compile(r"^PE\d", re.IGNORECASE),
     "Pasternack `PE…` parts are connectorised coaxial modules with SMA / N-type jacks "
     "— not board-mountable. Pick an SMT chip equivalent (e.g. MACOM MADL-011017, "
     "Skyworks SKY16406-321LF, Qorvo TGL2222 for a limiter)."),
    ("mini-circuits", re.compile(r"^Z(X|HL|JL|AFL|QL|RL|SDR|SC|SWA|FSW|FBT|GBF|GBT|HSS|TST|VBT)[-A-Z0-9]*",
                                  re.IGNORECASE),
     "Mini-Circuits `Z…`-series parts are connectorised coaxial modules — not "
     "board-mountable. Use a chip-package alternative (PMA / PSA / MNA / BFCN / "
     "GVA / RAMP / TAV / VLF / VHF / VBF for filters / amps)."),
    ("mini circuits", re.compile(r"^Z(X|HL|JL|AFL|QL|RL|SDR|SC|SWA|FSW|FBT|GBF|GBT|HSS|TST|VBT)[-A-Z0-9]*",
                                  re.IGNORECASE),
     "Mini-Circuits `Z…`-series parts are connectorised coaxial modules — not "
     "board-mountable. Use a chip-package alternative."),
    # Crystek "CCSO" / "CRBV" rack-mount oscillators / lab modules — NOT chips.
    # PN forms: CCSO-575, CRBV55BE-0010, CRBV-65A, etc. — match prefix only.
    ("crystek", re.compile(r"^(CCSO|CRBV)", re.IGNORECASE),
     "Crystek CCSO / CRBV are rack-mount instrument oscillators — use a chip TCXO "
     "(e.g. CCHD-957, CCPD-575) instead."),
)

# Description / component-name keywords that always flag connectorised regardless
# of manufacturer. These catch generic "buy the box, stick SMA on it" parts.
_CONNECTORISED_KEYWORDS: tuple[str, ...] = (
    "connectorised", "connectorized",
    "coaxial module", "coaxial assembly", "coax module", "coax assembly",
    "sma-to-sma", "sma to sma",
    "drop-in module", "drop in module",
    "n-type module", "bnc module",
    "rack-mount module", "rackmount module",
    "instrument-grade", "benchtop module",
    "panel-mount module", "panel mount module",
)
_SMA_JACK_RE = re.compile(r"\bsma\s*(female|male|jack|plug|connector)\b", re.IGNORECASE)
# Package-field keywords that reliably indicate the part is NOT board-mountable.
# Real SMT/chip packages look like QFN-32, LFCSP-24, SOT-89, BGA-256, 0603, 0402.
# Any package field referencing SMA/N-type/BNC is a coax assembly, not a chip.
_NON_SMT_PACKAGE_RE = re.compile(
    r"\b(connector|connectorised|connectorized|coax|coaxial|"
    r"sma\b|n-type|n\s*type|bnc|drop-?in|module\b|panel\s*mount)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Rejection:
    """Why a specific component was rejected. Structured so the audit
    report can render a per-part table without re-parsing strings."""
    part_number: str
    manufacturer: str
    reason: str

    def to_issue_dict(self) -> dict[str, Any]:
        """Shape expected by `domains._schema.AuditIssue`."""
        return {
            "severity": "critical",
            "category": "banned_part",
            "location": f"component_recommendations/{self.part_number}",
            "detail": (
                f"Part `{self.part_number}` ({self.manufacturer or 'unknown'}) "
                f"is banned: {self.reason}"
            ),
            "suggested_fix": (
                "Choose an active-production alternative from the RF "
                "component library — see data/sample_components.json."
            ),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_banned_manufacturer(manufacturer: str | None) -> str | None:
    """Return a rejection reason if the manufacturer is on the ban-list,
    else None."""
    if not manufacturer:
        return None
    normalised = str(manufacturer).strip().lower()
    if normalised in BANNED_MANUFACTURERS:
        return f"Manufacturer '{manufacturer}' is on the ban list"
    # Also catch "Vpt Inc" / "VPT_INC" style variants.
    stripped = re.sub(r"[\s,\.\-_]", "", normalised)
    for banned in BANNED_MANUFACTURERS:
        if re.sub(r"[\s,\.\-_]", "", banned) == stripped:
            return f"Manufacturer '{manufacturer}' is on the ban list"
    return None


def is_banned_part_number(part_number: str | None) -> str | None:
    """Return a rejection reason if the part number matches any EOL /
    NRND pattern, else None."""
    if not part_number:
        return None
    pn = str(part_number).strip()
    for pattern, reason in BANNED_PART_PATTERNS:
        if pattern.match(pn):
            return reason
    return None


def is_connectorised(component: dict[str, Any]) -> str | None:
    """Return a rejection reason if the component is a connectorised /
    coaxial / panel-mount module that cannot be soldered to a PCB. The
    full BOM must be SMT / chip / through-hole — never instrument-grade
    coax modules with SMA jacks pre-attached.

    Detection is layered:
      1. Manufacturer + PN-prefix match (Pasternack PE…, Mini-Circuits Z…
         coax series, Crystek CCSO/CRBV).
      2. Description / rationale / notes keyword match.
      3. Package field references SMA / N-type / BNC.
    """
    if not component:
        return None
    pn = (
        component.get("part_number")
        or component.get("primary_part")
        or component.get("mpn")
        or ""
    )
    mfr = (
        component.get("manufacturer")
        or component.get("primary_manufacturer")
        or component.get("vendor")
        or ""
    )
    pn_str = str(pn).strip()
    mfr_norm = str(mfr).strip().lower()

    # 1. Mfr + PN-prefix match
    for mfr_frag, pat, reason in CONNECTORISED_BY_MFR_PATTERNS:
        if mfr_frag in mfr_norm and pat.match(pn_str):
            return reason

    # 2. Description keywords (concat all free-form fields)
    desc_blob = " ".join(
        str(component.get(k) or "") for k in (
            "description", "rationale", "notes", "component_name",
            "component_type", "value", "summary",
        )
    ).lower()
    for kw in _CONNECTORISED_KEYWORDS:
        if kw in desc_blob:
            return (
                f"Description flags this as a connectorised module ('{kw}') — "
                "use a board-mountable / SMT-packaged equivalent."
            )
    if _SMA_JACK_RE.search(desc_blob):
        return (
            "Description references an SMA jack / connector — likely a "
            "connectorised coax assembly. Use a chip-package alternative."
        )

    # 3. Package field references a connector
    pkg = str(component.get("package") or "")
    if pkg and _NON_SMT_PACKAGE_RE.search(pkg):
        return (
            f"`package = {pkg!r}` indicates a connectorised / non-SMT part. "
            "Use a chip / SMT package equivalent."
        )

    return None


def classify_component(component: dict[str, Any]) -> Rejection | None:
    """Return a Rejection if the component should not ship, else None.

    Accepts either the rich `component_recommendations` shape (with
    `primary_part` / `primary_manufacturer`) or the flat `bom` shape
    (`part_number` / `manufacturer`). Missing keys are treated as empty.
    """
    pn = (
        component.get("part_number")
        or component.get("primary_part")
        or component.get("mpn")
        or ""
    )
    mfr = (
        component.get("manufacturer")
        or component.get("primary_manufacturer")
        or component.get("vendor")
        or ""
    )
    reason = (
        is_banned_part_number(pn)
        or is_banned_manufacturer(mfr)
        or is_connectorised(component)
    )
    if reason:
        return Rejection(
            part_number=str(pn),
            manufacturer=str(mfr),
            reason=reason,
        )
    return None


def filter_components(
    components: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[Rejection]]:
    """Split components into (kept, rejected). The kept list preserves the
    original dict shape unchanged — the caller can replace the original
    `component_recommendations` array with it. Rejections carry structured
    metadata the audit report renders as a per-part table."""
    kept: list[dict[str, Any]] = []
    rejected: list[Rejection] = []
    for c in components or []:
        rej = classify_component(c)
        if rej:
            rejected.append(rej)
        else:
            kept.append(c)
    return kept, rejected
