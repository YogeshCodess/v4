"""
Manifest validator — the cross-file MPN leak gate (Step 2 of the SSoT refactor).

Given a frozen `DesignManifest` and the markdown / JSON files written by P1,
detect any MPN-shaped token that appears in a file but is NOT in
`manifest.bom`. That is the structural failure mode behind the hackathon-final
bug: HMC parts survived the audit in `gain_loss_budget.md` and the
`requirements.md` prose because the audit only mutated the
`component_recommendations` field.

Usage
-----
    from services.manifest_validator import check_no_mpn_leak

    leaks = check_no_mpn_leak(output_dir, manifest)
    if leaks:
        # Fail P1 — refuse to mark the phase complete.
        raise ManifestLeakError(leaks)

The detector is **conservative**: it only flags tokens that look like
manufacturer part numbers (uppercase letters + digits, optional dashes /
suffix letters). Common false positives (REQ-HW-xxx, IEEE 29148, RoHS,
3GPP) are filtered by an exclusion list. When in doubt the detector errs
toward "this is an MPN" — better a false positive caught at the gate
than a real leak shipping to disk.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from services.design_manifest import DesignManifest


# ---------------------------------------------------------------------------
# MPN pattern
# ---------------------------------------------------------------------------

# An MPN is loosely: 2-6 uppercase letters, then digits (with optional dashes
# inside), with an optional trailing alphanumeric suffix. Matches common shapes:
#   HMC1234, HMC-1234, ADL5375, AD9082-FFV, ADRF5040, MAX2870, LMX2594,
#   LT5560, LTC5594, ADRV9009, STM32H743ZIT6, TPS54620
# Doesn't match: REQ-HW-001, IEEE-29148, USB-2.0, 3GPP, RoHS, MIL-STD-461F
#
# Strategy:
#   Anchor the START of the run on a [A-Z] (so `3GPP` and `123ABC` don't
#   match), require at least 2 letters before any digit, and require at
#   least 2 digits in the body. The `(?<![A-Z0-9-])` look-behind keeps us
#   from matching the tail of e.g. `MIL-STD-461F`.
_MPN_RE = re.compile(
    r"(?<![A-Z0-9-])"           # word boundary (not after another MPN char)
    r"([A-Z]{2,6}"              # 2-6 leading uppercase letters
    r"[A-Z0-9-]{0,3}"           # optional inner alphanumeric / dash run
    r"\d{2,}"                   # at least 2 digits
    r"[A-Z0-9-]*"               # optional trailing alphanumeric
    r")"
    r"(?![A-Z0-9-])"            # word boundary
)

# Exclude tokens that match the regex but are clearly not MPNs.
# Includes: standards prefixes, requirement IDs, units, common
# acronyms in compliance / RF / digital design context.
_NOT_AN_MPN: frozenset[str] = frozenset({
    # Requirements / standards
    "IEEE29148", "IEC60601", "ISO26262", "ISO9001", "ISO14001",
    "MILSTD461", "MILSTD461F", "MILSTD810", "MILSTD810G", "MILSTD810H",
    "MILSTD188", "MILSTD1553", "MILSTD1553B",
    "FCC15", "FCCPART15", "FCC97", "FCCPART97",
    "EN300", "EN303", "EN301", "EN50121", "EN55022", "EN55032",
    "ETSI300", "ETSIEN300",
    "REQHW001", "REQSW001",  # if the regex catches REQ-HW-001 condensed
    # Bands / frequency labels
    "K2A", "K2B", "S100", "S101",
    # Connectors / packages
    "BNC50", "SMA50", "TYPE2", "TYPE3",
    # Datasheet boilerplate
    "REV1", "REV2", "REV3", "REVA", "REVB", "REVC", "REVD",
    "VER1", "VER2", "VER3",
    # Protocols
    "USB2", "USB3", "USB30", "USB20",
    "PCIE3", "PCIE4", "PCIE5",
    "SATA3", "SATA6",
    "DDR3", "DDR4", "DDR5",
    "GDDR5", "GDDR6",
    # CIDR / addresses (rarely in a design doc but cheap to exclude)
    "IPV4", "IPV6",
})

# Standards prefixes that, when followed by digits, should be excluded
# regardless of the specific number. e.g. "ISO/IEC 12345" is a standard,
# not a part. Checked AFTER the regex matches — applied as prefix filter.
_STANDARDS_PREFIXES: tuple[str, ...] = (
    "IEEE", "ISO", "IEC", "ANSI", "ASTM", "JESD",
    "MILSTD", "MILPRF",
    "FCC", "ETSI", "EN5", "EN6", "EN3",
    "RFC",
    "REQ", "FR", "NFR",  # requirement-ID prefixes
    "STD",
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Leak:
    """One MPN-shaped token that appeared in a file but not in the manifest BOM."""
    file: str
    mpn: str
    line: Optional[int] = None
    context: Optional[str] = None  # short surrounding-text excerpt

    def __str__(self) -> str:
        loc = f":{self.line}" if self.line else ""
        ctx = f" — `{self.context}`" if self.context else ""
        return f"{self.file}{loc} -> {self.mpn}{ctx}"


class ManifestLeakError(Exception):
    """Raised when MPN leaks are found and the caller wants a hard fail."""
    def __init__(self, leaks: list[Leak]):
        self.leaks = leaks
        super().__init__(
            f"{len(leaks)} MPN leak(s) detected: "
            + ", ".join(f"{l.file}:{l.mpn}" for l in leaks[:5])
            + ("..." if len(leaks) > 5 else "")
        )


# ---------------------------------------------------------------------------
# Token classification
# ---------------------------------------------------------------------------

def _is_mpn_shaped(token: str) -> bool:
    """True iff the token looks like a manufacturer part number."""
    norm = token.replace("-", "").upper()
    if norm in _NOT_AN_MPN:
        return False
    # Strip standards prefixes that are followed by digits — e.g.
    # "IEEE29148" condenses to "IEEE29148" which starts with "IEEE".
    for prefix in _STANDARDS_PREFIXES:
        if norm.startswith(prefix):
            tail = norm[len(prefix):]
            # If the prefix consumed the letters and the rest is digits/short,
            # this is a standard reference, not an MPN.
            if tail.isdigit() or (len(tail) <= 3 and tail.isalnum()):
                return False
    return True


def extract_mpns(text: str) -> set[str]:
    """Return the set of MPN-shaped tokens in `text`. Uppercase, no dashes
    stripped — preserves the on-page shape so callers can highlight."""
    if not text:
        return set()
    candidates = _MPN_RE.findall(text)
    return {c for c in candidates if _is_mpn_shaped(c)}


def _extract_mpns_with_lines(text: str) -> list[tuple[str, int, str]]:
    """Like extract_mpns but returns (mpn, line_number, context) tuples for
    richer diagnostics."""
    out: list[tuple[str, int, str]] = []
    if not text:
        return out
    for line_no, line in enumerate(text.splitlines(), start=1):
        for match in _MPN_RE.finditer(line):
            tok = match.group(1)
            if not _is_mpn_shaped(tok):
                continue
            # Trim the context to ~60 chars around the match so the
            # operator can see why the token looked MPN-ish.
            start = max(0, match.start() - 20)
            end = min(len(line), match.end() + 20)
            ctx = line[start:end].strip()
            out.append((tok, line_no, ctx))
    return out


# ---------------------------------------------------------------------------
# Leak detection
# ---------------------------------------------------------------------------

# File extensions we scan. Keep narrow — binary outputs (xlsx, pdf, png) are
# excluded to avoid false-positive "leaks" from compressed/encoded data that
# happens to contain MPN-shaped byte sequences.
_SCANNABLE_EXTS: tuple[str, ...] = (".md", ".markdown", ".json", ".txt", ".csv")


def check_no_mpn_leak(
    output_dir: Path | str,
    manifest: DesignManifest,
    *,
    file_filter: Optional[Iterable[str]] = None,
) -> list[Leak]:
    """Scan every text artifact in `output_dir` and return Leak rows for
    MPNs that are not present in `manifest.bom`.

    Args
    ----
    output_dir : the directory P1 wrote files to (project-specific output dir)
    manifest   : the frozen DesignManifest — `manifest.allowed_mpns()` is the
                 whitelist
    file_filter: optional iterable of filenames to restrict the scan to.
                 When None, scans every `.md` / `.markdown` / `.json` / `.txt`
                 / `.csv` in `output_dir` (non-recursive — sub-folders are
                 skipped, which matches the P1 layout).

    Returns
    -------
    list[Leak] — empty when no leaks. Caller decides whether to raise
    ManifestLeakError or surface the leaks as audit issues.
    """
    out_path = Path(output_dir)
    if not out_path.exists() or not out_path.is_dir():
        return []

    allowed = manifest.allowed_mpns()
    leaks: list[Leak] = []

    if file_filter is not None:
        filenames = list(file_filter)
        files = [out_path / f for f in filenames]
    else:
        files = [
            f for f in out_path.iterdir()
            if f.is_file() and f.suffix.lower() in _SCANNABLE_EXTS
        ]

    for f in files:
        if not f.exists() or not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for tok, line_no, ctx in _extract_mpns_with_lines(text):
            if tok.upper() in allowed:
                continue
            # Some BOM rows ship part_number with a dash that the on-page
            # text omits (or vice versa). Compare the de-dashed forms too.
            if tok.replace("-", "").upper() in {a.replace("-", "") for a in allowed}:
                continue
            leaks.append(Leak(file=f.name, mpn=tok, line=line_no, context=ctx))

    return leaks


def leaks_to_audit_issues(leaks: list[Leak]) -> list[dict]:
    """Convert leak rows to AuditIssue-shaped dicts so they can be merged
    into the existing red-team audit report. Severity = `high` because a
    leak is a hard correctness failure (downstream phases will inherit
    the inconsistency)."""
    return [
        {
            "severity": "high",
            "category": "manifest_mpn_leak",
            "location": f"{l.file}" + (f":L{l.line}" if l.line else ""),
            "detail": (
                f"MPN `{l.mpn}` appears in `{l.file}` but is NOT in the "
                f"locked DesignManifest BOM. This is the cross-file consistency "
                f"failure mode that drove the hackathon-final HMC bug — most "
                f"likely the audit removed the part from `component_recommendations` "
                f"but the LLM had also embedded it in this file."
                + (f" Context: `{l.context}`" if l.context else "")
            ),
            "suggested_fix": (
                "Re-render this file deterministically from manifest.bom "
                "(see services/p1_renderers.py), or re-prompt the LLM with the "
                "cleaned BOM as the only allowed part list."
            ),
        }
        for l in leaks
    ]
