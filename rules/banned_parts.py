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
