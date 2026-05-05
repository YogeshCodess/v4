"""
Stale-phase detection helpers — A2.1 / A2.2.

Contract (see ADR-003 §5, §6):

A phase's output is stale when the project's current `requirements_hash` does
not equal the `requirements_hash_at_completion` stamped on that phase's most
recent entry in `phase_statuses`. Phases that have never completed are NOT
considered stale — they're just pending. The one remaining manual phase (P5,
PCB layout) is excluded from stale detection because the lock only pins
RF/SW/FPGA requirements — PCB artefacts live outside the lock's scope and
are hand-owned.

Historical note: P7 (FPGA RTL) used to be manual and was excluded here;
once it became a scripted phase behind `FpgaAgent`, we moved it into
`AI_PHASES` so P1 re-locks invalidate its output like any other AI phase.

Used by:
  - `main.py` — "Re-run all stale phases" button surface.
  - `scripts/run_baseline_eval.py` — batch re-runs after re-freezing a lock.
  - `agents/red_team_audit.py` — downgrade trust on stale downstream artefacts.

The helpers accept either a SQLAlchemy ORM row or a plain dict (so tests and
pure-stdlib callers don't need to spin up a session). Both return the phase
ids in canonical pipeline order.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from services.phase_catalog import (
    AUTO_PHASE_IDS,
    DOWNSTREAM_OF_P1 as _CATALOG_DOWNSTREAM_OF_P1,
    MANUAL_PHASE_IDS,
)

# Canonical ordered list of AI phases — P1 (lock owner) + every automated
# downstream phase. Sourced from `phase_catalog` so `pipeline_service`,
# `project_service` and this module can never disagree about which phase
# needs what.
AI_PHASES: tuple[str, ...] = ("P1",) + AUTO_PHASE_IDS

MANUAL_PHASES: tuple[str, ...] = MANUAL_PHASE_IDS

# Downstream dependencies: if P1 is re-run, which phases have to be re-run
# afterwards (assuming their current output was built on the old lock)?
# Kept as a module-level name for back-compat; re-exported from the catalog.
DOWNSTREAM_OF_P1: tuple[str, ...] = tuple(_CATALOG_DOWNSTREAM_OF_P1)


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    """Read a column from either an ORM instance or a dict-like row."""
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _phase_status_entry(row: Any, phase_id: str) -> dict:
    """Extract the phase_statuses[phase_id] sub-dict safely."""
    statuses = _row_get(row, "phase_statuses", {}) or {}
    entry = statuses.get(phase_id)
    if entry is None:
        return {}
    if isinstance(entry, dict):
        return entry
    # Legacy: phase_statuses[phase_id] was sometimes a bare string. Normalise.
    return {"status": str(entry)}


def _is_completed(entry: dict) -> bool:
    return entry.get("status") == "completed"


def stale_phase_ids(
    project_row: Any,
    *,
    include_manual: bool = False,
    phase_order: Optional[Iterable[str]] = None,
) -> list[str]:
    """Return the ids of AI phases whose completion hash no longer matches
    the project's current requirements_hash.

    Parameters
    ----------
    project_row:
        The ProjectDB row (ORM instance) or a dict with the same column names.
    include_manual:
        Default False. P5/P7 are excluded unless explicitly asked for.
    phase_order:
        Override the default canonical ordering (useful in tests).

    Returns
    -------
    list[str]:
        Phase ids, in canonical order. Empty list if:
          - the project has no lock yet (`requirements_hash is None`); or
          - every completed phase was completed against the current lock.
    """
    current_hash = _row_get(project_row, "requirements_hash")
    if not current_hash:
        return []

    order = list(phase_order) if phase_order is not None else list(AI_PHASES)
    if include_manual:
        # Manual phases go in pipeline order. Today only P5 is manual —
        # it's inserted right after P4 (PCB follows netlist). If we ever
        # reintroduce a manual phase with a different anchor, add it here.
        order_with_manual: list[str] = list(order)
        for manual, anchor in (("P5", "P4"),):
            if manual not in order_with_manual and anchor in order_with_manual:
                order_with_manual.insert(order_with_manual.index(anchor) + 1, manual)
        order = order_with_manual

    out: list[str] = []
    for phase_id in order:
        if not include_manual and phase_id in MANUAL_PHASES:
            continue
        entry = _phase_status_entry(project_row, phase_id)
        if not _is_completed(entry):
            continue
        stamped = entry.get("requirements_hash_at_completion")
        if stamped is None:
            # Completed before lock existed — treat as stale so the user is
            # forced to re-run under the new lock.
            out.append(phase_id)
            continue
        if stamped != current_hash:
            out.append(phase_id)
    return out


def rerun_plan(
    project_row: Any,
    *,
    include_manual: bool = False,
) -> dict[str, Any]:
    """Return a plan describing what to re-run and in what order.

    Output shape:

        {
          "current_hash": str | None,
          "stale": ["P2", "P4", ...],          # what's actually stale
          "order": ["P2", "P3", "P4", ...],    # stale, re-ordered canonically
          "blocked_by_manual": ["P4"],         # stale phases whose downstream
                                               # manual phase (P5/P7) has no
                                               # matching lock — caller must
                                               # warn the user that PCB / FPGA
                                               # may need rework.
          "summary": "3 stale phases — re-run in order P2 → P3 → P4",
        }

    The plan is advisory; invoking it is the caller's job (e.g. FastAPI POSTing
    `/projects/{id}/phases/{phase_id}/execute` for each id in `order`).
    """
    current_hash = _row_get(project_row, "requirements_hash")
    stale = stale_phase_ids(project_row, include_manual=include_manual)
    order = [p for p in AI_PHASES if p in stale]
    if include_manual:
        # Surface manual phases where the upstream AI phase they depend on is
        # being re-run. Today only PCB (P5) follows P4 — FPGA (P7) used to
        # be manual but is now an AI phase and flows through `AI_PHASES`
        # above without special-casing.
        if "P4" in stale:
            order.append("P5")

    blocked_by_manual: list[str] = []
    # If the upstream AI phase is stale but the manual phase is marked
    # completed, flag it — the manual artefact likely needs rework.
    for upstream, manual in (("P4", "P5"),):
        if upstream in stale:
            m_entry = _phase_status_entry(project_row, manual)
            if _is_completed(m_entry):
                blocked_by_manual.append(manual)

    if not stale:
        summary = "No stale phases — the lock matches every completed phase."
    else:
        summary = (
            f"{len(stale)} stale phase{'s' if len(stale) != 1 else ''} — "
            f"re-run in order "
            + " -> ".join(order)
        )
        if blocked_by_manual:
            summary += (
                f" (manual rework may be needed: "
                f"{', '.join(blocked_by_manual)})"
            )

    return {
        "current_hash": current_hash,
        "stale": stale,
        "order": order,
        "blocked_by_manual": blocked_by_manual,
        "summary": summary,
    }


def phase_status_summary(project_row: Any) -> dict[str, str]:
    """Flatten phase_statuses to a {phase_id: label} map. Useful for widgets
    that want to show stale/fresh/pending in one pass.

    Labels: 'fresh', 'stale', 'pending', 'manual', 'in_progress', 'failed',
    or 'unknown'.
    """
    current_hash = _row_get(project_row, "requirements_hash")
    stale_set = set(stale_phase_ids(project_row, include_manual=True))
    result: dict[str, str] = {}
    for phase_id in list(AI_PHASES) + list(MANUAL_PHASES):
        entry = _phase_status_entry(project_row, phase_id)
        if phase_id in MANUAL_PHASES:
            result[phase_id] = "manual"
            continue
        status = entry.get("status", "pending")
        if status == "completed":
            if phase_id in stale_set or (
                current_hash and not entry.get("requirements_hash_at_completion")
            ):
                result[phase_id] = "stale"
            else:
                result[phase_id] = "fresh"
        elif status in ("in_progress", "running"):
            result[phase_id] = "in_progress"
        elif status == "failed":
            result[phase_id] = "failed"
        elif status == "pending" or not status:
            result[phase_id] = "pending"
        else:
            result[phase_id] = "unknown"
    return result
