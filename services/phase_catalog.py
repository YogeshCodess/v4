"""
Canonical phase catalog — single source of truth for phase IDs.

Rationale
---------
Prior to this module, the list of "phases downstream of P1" was hard-coded in
three different places (`pipeline_service.AUTO_PHASES`, `project_service.
_DOWNSTREAM_AI_PHASES`, `stale_phases.AI_PHASES`). When P7 was promoted from a
manual PCB-style phase to a fully automated FPGA phase, only the first list
was updated — so `project_service.set_phase_status("P1", "completed",
reset_downstream=True)` never reset P7, and the stale-phase detector kept
treating P7 as "manual" (i.e. immune to P1 re-locks). The Ctrl+Shift+R rerun
plan also omitted P7a entirely.

This module fixes that drift by being the ONE place that names phases. Every
service that needs to know "what's automated, what's downstream of P1, what's
still manual" must import from here.

Invariants
-----------
- Import only from the standard library. This module must stay a leaf so it
  can be imported by any service without creating cycles.
- `AUTO_PHASE_SPECS` is ordered — pipeline execution order matches tuple order.
- `DOWNSTREAM_OF_P1 == AUTO_PHASE_IDS` today, but that's a contingent fact
  (every auto phase currently depends on P1). Keep them as separate names so a
  future sub-sheet (e.g. a P8d that only depends on P7) can peel off without
  a rewrite.
"""
from __future__ import annotations


# (phase_id, agent_module, agent_class, display_name)
# Pipeline executes these in order. Mutating this tuple changes:
#   - which agents `PipelineService.run_pipeline` invokes
#   - which phases `ProjectService` resets when P1 is re-completed
#   - which phases `stale_phases` tracks for hash drift
AUTO_PHASE_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("P2",  "agents.document_agent",   "DocumentAgent",    "HRS Document"),
    ("P3",  "agents.compliance_agent", "ComplianceAgent",  "Compliance"),
    ("P4",  "agents.netlist_agent",    "NetlistAgent",     "Netlist"),
    ("P6",  "agents.glr_agent",        "GLRAgent",         "GLR"),
    ("P7",  "agents.fpga_agent",       "FpgaAgent",        "FPGA RTL Design"),
    ("P7a", "agents.rdt_psq_agent",    "RdtPsqAgent",      "Register Map & Programming Sequence"),
    ("P8a", "agents.srs_agent",        "SRSAgent",         "SRS"),
    ("P8b", "agents.sdd_agent",        "SDDAgent",         "SDD"),
    ("P8c", "agents.code_agent",       "CodeAgent",        "Code + Review"),
)

# Just the ids — for list-of-phases comparisons without tupling.
AUTO_PHASE_IDS: tuple[str, ...] = tuple(spec[0] for spec in AUTO_PHASE_SPECS)

# Phases that require human action and have no agent behind them. The lock
# (P1's `requirements_hash`) does NOT pin these — PCB layout (P5) lives
# outside the RF/SW scope and is hand-owned. P7 (FPGA RTL) used to be manual
# but is now auto; it has moved to AUTO_PHASE_SPECS.
MANUAL_PHASE_IDS: tuple[str, ...] = ("P5",)

# Phases whose output depends on P1's requirements. When P1 is re-completed
# with a new lock, every phase in this list must be reset to pending so the
# downstream agents re-run against fresh requirements. P1 itself is excluded
# (it owns the lock, it's not downstream of itself).
DOWNSTREAM_OF_P1: tuple[str, ...] = AUTO_PHASE_IDS
