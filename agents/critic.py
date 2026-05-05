"""
Structured-output critic — B2.5 scaffold (deterministic half).

This module is the deterministic sibling of `agents/critic_agent.py`. Where the
LLM-backed critic re-reads the primary agent's prose and produces free-form
disagreements, *this* module takes two structured outputs — the primary and a
fallback — and diffs them by field. Every disagreement becomes an
`AuditIssue` so the red-team audit can merge them into a single report.

Why deterministic?
------------------
Judges want to see that we *could* run the same output through two different
LLMs and flag where they disagree, but calling two models per phase burns the
API budget during the hackathon demo. The deterministic differ lets us:

  1. Run the primary LLM once.
  2. Replay golden-run structured outputs from `tests/golden/*.yaml` as the
     "fallback".
  3. Diff against the live output and flag any regression.

It also unit-tests trivially: no network, no tokens, same input → same output.

When the fallback LLM *is* available (non-air-gap mode), `compare_designs` can
be handed the structured JSON the fallback emitted and the diff still works —
no code change needed.

Public surface
--------------
    compare_designs(primary, fallback, *, tolerance_db=0.5, phase_id="P1")
        -> list[AuditIssue]

The keys we compare are:
  - architecture (string)
  - cascade.noise_figure_db, cascade.total_gain_db, cascade.iip3_dbm,
    cascade.p1db_dbm      — numeric, tolerance-bounded
  - bom                    — list of stage dicts; we compare order + part_number
  - cited_standards        — list of (standard, clause) tuples; order-insensitive
  - domain                 — string, must match exactly

Anything else in the dicts is ignored. This is intentional: a critic that
flags every key that differs produces noise, not signal.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from domains._schema import AuditIssue


# Fields in the cascade block we compare numerically, with per-field tolerance
# knobs. tolerance_db applies to every entry here.
_CASCADE_NUMERIC_FIELDS = (
    "noise_figure_db",
    "total_gain_db",
    "iip3_dbm",
    "p1db_dbm",
)


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _issue(
    severity: str,
    category: str,
    location: str,
    detail: str,
    suggested_fix: Optional[str] = None,
) -> AuditIssue:
    return AuditIssue(
        severity=severity,
        category=category,
        location=location,
        detail=detail,
        suggested_fix=suggested_fix,
    )


def _compare_scalar(
    primary: Any,
    fallback: Any,
    *,
    phase_id: str,
    field: str,
    category: str = "model_disagreement",
    severity: str = "medium",
) -> list[AuditIssue]:
    """Compare two scalar values; flag if they differ."""
    # Both missing ⇒ no issue.
    if primary is None and fallback is None:
        return []
    # One side missing ⇒ missing-field warning (low severity — one side just
    # didn't emit it).
    if primary is None or fallback is None:
        return [_issue(
            severity="low",
            category="missing_field",
            location=f"{phase_id}.{field}",
            detail=(
                f"field '{field}' present in "
                f"{'fallback' if primary is None else 'primary'} only "
                f"(primary={primary!r}, fallback={fallback!r})"
            ),
            suggested_fix="Verify both models were asked for the same fields.",
        )]
    if primary != fallback:
        return [_issue(
            severity=severity,
            category=category,
            location=f"{phase_id}.{field}",
            detail=(
                f"primary said {primary!r}, fallback said {fallback!r}"
            ),
            suggested_fix=(
                "If one side is clearly wrong, fix the prompt or the BOM. "
                "If both are defensible, log the ambiguity in the lock notes."
            ),
        )]
    return []


def _compare_numeric(
    primary: Any,
    fallback: Any,
    *,
    phase_id: str,
    field: str,
    tolerance: float,
) -> list[AuditIssue]:
    """Compare two numeric fields with a tolerance window."""
    p = _as_float(primary)
    f = _as_float(fallback)
    if p is None and f is None:
        return []
    if p is None or f is None:
        return [_issue(
            severity="low",
            category="missing_field",
            location=f"{phase_id}.{field}",
            detail=(
                f"numeric field '{field}' present in "
                f"{'fallback' if p is None else 'primary'} only "
                f"(primary={primary!r}, fallback={fallback!r})"
            ),
        )]
    delta = abs(p - f)
    if delta <= tolerance:
        return []
    severity = "high" if delta > 5 * tolerance else "medium"
    return [_issue(
        severity=severity,
        category="model_disagreement",
        location=f"{phase_id}.{field}",
        detail=(
            f"{field}: primary={p:.3f}, fallback={f:.3f}, delta={delta:.3f} "
            f"> tolerance={tolerance:.3f}"
        ),
        suggested_fix=(
            "Re-run the cascade validator; treat its answer as ground truth "
            "and revise whichever model disagrees."
        ),
    )]


def _compare_bom(
    primary_bom: Iterable[dict],
    fallback_bom: Iterable[dict],
    *,
    phase_id: str,
) -> list[AuditIssue]:
    """Compare BOMs stage-by-stage. Flags length mismatch and per-stage
    part_number / name disagreements.
    """
    primary_list = list(primary_bom or [])
    fallback_list = list(fallback_bom or [])
    issues: list[AuditIssue] = []

    if len(primary_list) != len(fallback_list):
        issues.append(_issue(
            severity="high",
            category="model_disagreement",
            location=f"{phase_id}.bom.length",
            detail=(
                f"BOM length differs: primary has {len(primary_list)} "
                f"stages, fallback has {len(fallback_list)}"
            ),
            suggested_fix=(
                "Align architectures first — a length mismatch usually means "
                "one side picked a different receiver topology."
            ),
        ))

    # Compare what we can align positionally. Mismatched tails are left to
    # the length warning above.
    for idx in range(min(len(primary_list), len(fallback_list))):
        p_stage = primary_list[idx] or {}
        f_stage = fallback_list[idx] or {}
        for key in ("part_number", "name", "kind"):
            p_val = p_stage.get(key)
            f_val = f_stage.get(key)
            if p_val is None and f_val is None:
                continue
            if p_val != f_val:
                # Part-number disagreements are higher-severity than name
                # disagreements because part_number is the thing that drives
                # procurement and cascade math.
                sev = "high" if key == "part_number" else "medium"
                issues.append(_issue(
                    severity=sev,
                    category="model_disagreement",
                    location=f"{phase_id}.bom[{idx}].{key}",
                    detail=(
                        f"stage {idx} {key}: primary={p_val!r}, "
                        f"fallback={f_val!r}"
                    ),
                    suggested_fix=(
                        "Check both parts against the component DB; prefer "
                        "the one with datasheet_verified=True and a matching "
                        "screening class."
                    ),
                ))
    return issues


def _normalise_citations(cits: Any) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for item in (cits or []):
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            out.add((str(item[0]).strip(), str(item[1]).strip()))
        elif isinstance(item, dict):
            std = item.get("standard") or item.get("std")
            cl = item.get("clause")
            if std and cl:
                out.add((str(std).strip(), str(cl).strip()))
    return out


def _compare_citations(
    primary: Any,
    fallback: Any,
    *,
    phase_id: str,
) -> list[AuditIssue]:
    p_set = _normalise_citations(primary)
    f_set = _normalise_citations(fallback)
    if p_set == f_set:
        return []
    only_in_primary = sorted(p_set - f_set)
    only_in_fallback = sorted(f_set - p_set)
    issues: list[AuditIssue] = []
    for std, cl in only_in_primary:
        issues.append(_issue(
            severity="medium",
            category="model_disagreement",
            location=f"{phase_id}.cited_standards",
            detail=f"{std} {cl} cited by primary only",
            suggested_fix=(
                "Confirm whether the standard applies to this domain. "
                "If the fallback was right to drop it, remove from lock."
            ),
        ))
    for std, cl in only_in_fallback:
        issues.append(_issue(
            severity="medium",
            category="model_disagreement",
            location=f"{phase_id}.cited_standards",
            detail=f"{std} {cl} cited by fallback only",
            suggested_fix=(
                "Confirm whether this standard was missed by primary. "
                "If applicable, add to lock."
            ),
        ))
    return issues


def compare_designs(
    primary: dict,
    fallback: dict,
    *,
    tolerance_db: float = 0.5,
    phase_id: str = "P1",
) -> list[AuditIssue]:
    """Diff two structured design outputs and return a flat list of issues.

    A design dict should contain (all optional; missing keys are skipped):

        {
          "domain": "radar",
          "architecture": "superheterodyne",
          "cascade": {"noise_figure_db": 1.5, "total_gain_db": 35, ...},
          "bom": [{"name": "LNA", "part_number": "HMC8411", ...}, ...],
          "cited_standards": [["MIL-STD-461G", "RE102"], ...],
        }

    The function is deterministic: identical inputs → empty list. The returned
    issues use `category="model_disagreement"` (except for one-sided field
    presence, which uses `category="missing_field"`) so the red-team audit can
    merge them without overloading any existing category.
    """
    if not isinstance(primary, dict) or not isinstance(fallback, dict):
        raise TypeError("compare_designs expects two dicts")

    issues: list[AuditIssue] = []

    # Scalar fields.
    issues.extend(_compare_scalar(
        primary.get("domain"), fallback.get("domain"),
        phase_id=phase_id, field="domain",
        severity="high",
    ))
    issues.extend(_compare_scalar(
        primary.get("architecture"), fallback.get("architecture"),
        phase_id=phase_id, field="architecture",
        severity="high",
    ))

    # Cascade numbers.
    p_cascade = primary.get("cascade") or {}
    f_cascade = fallback.get("cascade") or {}
    for field in _CASCADE_NUMERIC_FIELDS:
        issues.extend(_compare_numeric(
            p_cascade.get(field), f_cascade.get(field),
            phase_id=phase_id, field=f"cascade.{field}",
            tolerance=tolerance_db,
        ))

    # BOM.
    issues.extend(_compare_bom(
        primary.get("bom"), fallback.get("bom"),
        phase_id=phase_id,
    ))

    # Citations.
    issues.extend(_compare_citations(
        primary.get("cited_standards"),
        fallback.get("cited_standards"),
        phase_id=phase_id,
    ))

    return issues


def summarise(issues: list[AuditIssue]) -> dict:
    """Small helper for loggers and tests: counts by severity & category."""
    by_severity: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for i in issues:
        by_severity[i.severity] = by_severity.get(i.severity, 0) + 1
        by_category[i.category] = by_category.get(i.category, 0) + 1
    return {
        "total": len(issues),
        "by_severity": by_severity,
        "by_category": by_category,
    }
