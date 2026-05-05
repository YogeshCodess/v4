"""
Phase → applicable design-scope mapping (backend source of truth).

Mirrors the frontend table in `hardware-pipeline-v5-react/src/data/phases.ts`.
Keep the two in sync — the backend file is authoritative for the
/phases/{id}/execute endpoint's scope validation.

Design scopes:
  full            — complete hardware + software pipeline
  front-end       — RF front-end only (LNA / filter / limiter)
  downconversion  — front-end + IF stage
  dsp             — digital back-end + FPGA + software

Policy (2026-04-20): the `design_scope` field is still captured during
project creation because it drives the Phase-1 wizard (which architecture
and spec questions appear), but it NO LONGER gates downstream phase
execution. Every phase is applicable to every scope. Rationale: even a
"front-end only" RF board has a bias / housekeeping MCU that needs SRS,
SDD, and register-map documentation, so hiding P6-P8c was producing a
misleading pipeline story and worse end-deliverables. If a phase is truly
irrelevant for a given design, its own agent is responsible for producing
a short "not applicable for this project" report rather than being skipped
from the pipeline entirely.

Phases P5 and P7 are manual (external tools) — their applicability is
unchanged; the pipeline still does not auto-execute them.
"""
from __future__ import annotations

from typing import Dict, Set

_ALL_SCOPES: Set[str] = {"full", "front-end", "downconversion", "dsp"}
PHASE_APPLICABLE_SCOPES: Dict[str, Set[str]] = {
    "P1":  _ALL_SCOPES,
    "P2":  _ALL_SCOPES,
    "P3":  _ALL_SCOPES,
    "P4":  _ALL_SCOPES,
    "P5":  _ALL_SCOPES,
    "P6":  _ALL_SCOPES,
    "P7":  _ALL_SCOPES,
    "P7a": _ALL_SCOPES,
    "P8a": _ALL_SCOPES,
    "P8b": _ALL_SCOPES,
    "P8c": _ALL_SCOPES,
}


def is_phase_applicable(phase_id: str, scope: str) -> bool:
    """Return True when `phase_id` may be executed under the given scope.

    Unknown phases are treated as applicable (fail-open) so that future
    phase IDs do not immediately get rejected before this table is updated.
    """
    allowed = PHASE_APPLICABLE_SCOPES.get(phase_id)
    if allowed is None:
        return True
    return scope in allowed
