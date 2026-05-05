"""
Standards lookup — thin loader over domains/standards.json.

Used by:
  - requirements_agent to cite the right clause when a spec is set
  - compliance_agent to build the compliance matrix
  - red_team audit agent to verify every cited clause actually exists
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from domains._schema import StandardClause

_HERE = Path(__file__).parent
_STANDARDS_FILE = _HERE / "standards.json"


def _load_all() -> list[StandardClause]:
    if not _STANDARDS_FILE.exists():
        return []
    raw = json.loads(_STANDARDS_FILE.read_text())
    return [StandardClause(**c) for c in raw.get("clauses", [])]


# Cache — standards rarely change within a run.
_CACHE: Optional[list[StandardClause]] = None


def get_all_standards() -> list[StandardClause]:
    global _CACHE
    if _CACHE is None:
        _CACHE = _load_all()
    return list(_CACHE)


def find_clause(standard: str, clause: str) -> Optional[StandardClause]:
    """Exact-match lookup, case-insensitive."""
    s = standard.strip().lower()
    c = clause.strip().lower()
    for cl in get_all_standards():
        if cl.standard.lower() == s and cl.clause.lower() == c:
            return cl
    return None


def clauses_for_platform(platform: str) -> list[StandardClause]:
    """Return clauses that list `platform` in typical_applicability."""
    p = platform.strip().lower()
    return [cl for cl in get_all_standards()
            if any(t.lower() == p for t in cl.typical_applicability)]


def clauses_for_domain(domain: str) -> list[StandardClause]:
    """Return clauses that list this RF domain (radar/ew/satcom/communication)."""
    d = domain.strip().lower()
    return [cl for cl in get_all_standards()
            if any(t.lower() == d for t in cl.typical_applicability)]


def validate_citations(citations: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """
    Given a list of (standard, clause) pairs as cited by an LLM agent, return
    the pairs that do NOT resolve to any known clause. The red-team auditor
    uses this to flag hallucinated standard citations.
    """
    missing: list[tuple[str, str]] = []
    for std, cl in citations:
        if find_clause(std, cl) is None:
            missing.append((std, cl))
    return missing
