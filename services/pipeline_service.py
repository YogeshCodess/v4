"""
PipelineService — executes phases as FastAPI background tasks.

Key design decisions:
- All agent execution happens here, NOT in app.py or route handlers.
- Phase status is written to DB immediately (in_progress → completed|failed).
- Background task pattern: caller fires-and-forgets; UI polls /projects/{id}.
- Phase outputs are written through StorageAdapter, never raw Path.write_text().
- All DB writes use async methods so the FastAPI event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from config import settings
from services.llm_logger import (
    finish_pipeline_run,
    pipeline_run_context,
    start_pipeline_run,
)
from services.phase_catalog import AUTO_PHASE_SPECS
from services.project_service import ProjectService
from services.storage import StorageAdapter

log = logging.getLogger(__name__)

# Phase metadata is owned by `services.phase_catalog` so that
# `project_service` and `stale_phases` can reset / audit the same set of
# downstream phases without the three lists drifting apart. We keep the
# `AUTO_PHASES` alias (as a list, so tests can `[p[0] for p in AUTO_PHASES]`)
# for back-compat with call sites that import from here.
AUTO_PHASES = list(AUTO_PHASE_SPECS)


# ─────────────────────────────────────────────────────────────────────────────
# P26 (2026-04-25) — DAG scheduler (replaces P22's batch scheduler).
#
# User report: "struck after glr generation after some time starting fpga".
# Root cause of the perceived pause:
#
# The previous batch scheduler waited for the SLOWEST phase in each batch
# before starting the next. With Batch B = (P3, P6, P8a) and P3 depending
# on slow P2 (HRS, 10+ min in user's project), P7 — which only depends on
# P6 — could not start until P3 finished, even though P6 had been done
# for minutes.
#
# Phase-level DAG removes that artificial barrier: each phase fires the
# instant its OWN upstream deps complete. P7 starts as soon as P6 is done,
# regardless of how long P3 still needs.
#
# Dependency graph (P1 is user-driven, always done before this scheduler):
#
#   P2  (HRS)              ← P1
#   P4  (Netlist)          ← P1
#   P3  (Compliance)       ← P2, P4
#   P6  (GLR)              ← P4
#   P8a (SRS)              ← P4
#   P7  (FPGA RTL)         ← P6
#   P8b (SDD)              ← P8a
#   P7a (Register Map)     ← P6   (was P7; reversed 2026-05-02)
#   P7 (FPGA RTL)          ← P6, P7a (2026-05-02)
#   P8c (Code Review)      ← P8a
#
# Two independent chains:
#   Chain A (slow): P2 → P3                  (HRS-bound critical path)
#   Chain B (fast): P4 → P6 → P7 → P7a       (parallel to chain A)
#                   P4 → P8a → P8b → P8c     (parallel to chain A)
#
# Worst-case wall time = max(Chain A, longest of Chain B variants).
# In the user's scenario where HRS is 10 min: pipeline is 10 min, not 16.
#
# UI perception is preserved by serialising the STATUS WRITES (not the
# work) in phase-id order — phase X's "completed" flag only flips after
# all earlier phase-id flags have flipped. Backend is parallel; frontend
# sidebar still appears to advance one phase at a time.
# ─────────────────────────────────────────────────────────────────────────────
_PHASE_DEPS: dict[str, tuple[str, ...]] = {
    "P2":  (),
    "P4":  (),
    "P3":  ("P2", "P4"),
    "P6":  ("P4",),
    "P8a": ("P4",),
    # 2026-05-02: P7a now runs BEFORE P7. Pre-fix the order was
    # P6 -> P7 -> P7a, which meant P7's RTL was generated with an empty
    # register map (register_description_table.md didn't exist yet); the
    # tailored RTL emitter then fell back to a generic 4-register
    # CTRL/STATUS/VERSION/SCRATCH skeleton instead of the project's actual
    # registers. Now P6 -> P7a -> P7 so the register file IS on disk and
    # the FPGA RTL contains the real address decoder.
    "P7a": ("P6",),
    "P7":  ("P6", "P7a"),
    "P8b": ("P8a",),
    # P8c (Code Generation + Review) loads BOTH SRS_*.md (from P8a) AND
    # SDD_*.md (from P8b) inside `code_agent.py::execute`. Pre-fix this
    # tuple was just `("P8a",)` so P8c started ~3s after P8a finished
    # and immediately returned `phase_complete: False` because SDD wasn't
    # written yet (P8b takes ~6 min). User-facing symptom: P8c always
    # "failed" with no error in the DB, no output files generated.
    # Fixed (P26 #12, 2026-04-25): require BOTH P8a + P8b.
    "P8c": ("P8a", "P8b"),
}

# Phase-id ordering used for serial UI status flips (NOT for execution order).
# Must include every phase in `_PHASE_DEPS`. Order chosen to match the
# left-sidebar visual order in the React frontend.
_PHASE_FLIP_ORDER: tuple[str, ...] = (
    "P2", "P3", "P4", "P6", "P7a", "P7", "P8a", "P8b", "P8c",
)

# Back-compat: tests + external callers may import this symbol. Now derived
# from `_PHASE_DEPS` as a topological grouping for diagnostic logging only —
# the scheduler ignores it and uses the per-phase DAG instead.
_PIPELINE_BATCHES: tuple[tuple[str, ...], ...] = (
    ("P2",  "P4"),
    ("P3",  "P6",  "P8a"),
    ("P7",  "P8b"),
    ("P7a", "P8c"),
)

# Map phase_id → (module_path, class_name, phase_name) for fast lookup.
# Source of truth stays in AUTO_PHASE_SPECS; this is a derived index.
_PHASE_META: dict[str, tuple[str, str, str]] = {
    spec[0]: (spec[1], spec[2], spec[3]) for spec in AUTO_PHASE_SPECS
}


class PipelineService:
    """
    Manages the P2→P8c automated pipeline.
    Designed to run as a FastAPI BackgroundTask.
    """

    # P26 #12 (2026-04-25): re-enabled `_STATUS_FLIP_INTERLUDE_S` after
    # the user reported "elapsed time for compliance only and jumped to
    # GLR" / "srs and sdd completed [too fast to see]". Root cause: the
    # frontend polls phase status every 3s, but `_serialised_flip` writes
    # P3=completed → nudges P4=in_progress → P4 work was already done so
    # P4=completed within microseconds → P6=in_progress... all between
    # two UI polls.
    #
    # P26 #20 (2026-04-26): bumped 4s → 8s after the user reported
    # P8c (took ~5s wall time) showed up linked-to-P8b with no visible
    # elapsed counter. With 4s interlude + 2-3s poll, fast-completing
    # phases still slipped through. 8s gives 3-4 poll opportunities
    # so the in_progress state is reliably seen + the elapsed counter
    # has time to start ticking. Total overhead = 9 phases × 8s = ~72s
    # — still acceptable demo cost for visible per-phase progress.
    #
    # `_STATUS_FLIP_DELAY_S` is unused now; kept for back-compat with
    # test fixtures that set both. Set to 0 to skip.
    _STATUS_FLIP_DELAY_S = 0.0
    _STATUS_FLIP_INTERLUDE_S = 8.0

    def __init__(
        self,
        project_service: Optional[ProjectService] = None,
        storage: Optional[StorageAdapter] = None,
    ):
        self._proj_svc = project_service or ProjectService()
        self._storage = storage or StorageAdapter.local(settings.output_dir)
        # P22 (2026-04-24): per-project asyncio.Lock to serialise the
        # read-modify-write of the `phase_statuses` JSON column across
        # concurrent phase runs in the same batch. Without this lock,
        # two parallel `async_set_phase_status` calls can race:
        # both read the old JSON, each patches their own phase_id, and
        # whichever commits second clobbers the other's update (classic
        # lost-update). SQLite's default isolation does NOT prevent this
        # on a shared JSON column.
        # The lock is per project_id so unrelated project pipelines
        # never contend; within a single project's pipeline, concurrent
        # phases take the lock only for the duration of a status /
        # output DB write (microseconds) — no LLM-call-time impact.
        self._status_locks: dict[int, asyncio.Lock] = {}

    def _status_lock(self, project_id: int) -> asyncio.Lock:
        """Lazy per-project lock factory — created on first use, reused
        for the lifetime of the PipelineService instance."""
        lock = self._status_locks.get(project_id)
        if lock is None:
            lock = asyncio.Lock()
            self._status_locks[project_id] = lock
        return lock

    async def run_pipeline(self, project_id: int) -> None:
        """Execute auto phases (P2→P8c) using a phase-level DAG scheduler.

        DAG semantics (P26 — replaces P22's batch scheduler):
          - Each phase fires the instant its OWN dependencies (per
            `_PHASE_DEPS`) have completed — independent of any sibling
            phases that might still be running.
          - `prior_outputs` is updated with each phase's outputs as soon
            as that phase finishes work, so downstream phases see them
            without waiting for a batch boundary.
          - Status writes (the `in_progress` → `completed`/`failed`
            transitions visible in the UI sidebar) are serialised in
            phase-id order via `_PHASE_FLIP_ORDER` so the user still
            sees phases advance one-at-a-time even though they're
            running in parallel.
          - A failed dependency marks ALL transitively-downstream phases
            failed (no work spent on inputs that won't be valid).

        Designed to be called as:
        `BackgroundTasks.add_task(svc.run_pipeline, project_id)`.
        """
        proj = await self._proj_svc.async_get(project_id)
        if not proj:
            log.error("pipeline.project_not_found", extra={"project_id": project_id})
            return

        log.info(
            "pipeline.started",
            extra={"project_id": project_id, "project_name": proj["name"]},
        )
        prior_outputs: dict[str, str] = self._load_prior_outputs(proj)

        from services.phase_scopes import is_phase_applicable
        scope = (proj.get("design_scope") or "full").lower()
        _pipeline_t0 = time.monotonic()

        # Determine eligible phases (in `_PHASE_DEPS` but not already
        # completed and applicable to the project's scope).
        eligible: set[str] = set()
        for phase_id in _PHASE_DEPS:
            if phase_id not in _PHASE_META:
                log.warning(
                    "pipeline.phase_unknown",
                    extra={"phase": phase_id},
                )
                continue
            if await self._proj_svc.async_get_phase_status(
                project_id, phase_id,
            ) == "completed":
                log.info("pipeline.phase_skipped_completed", extra={"phase": phase_id})
                continue
            if not is_phase_applicable(phase_id, scope):
                log.info(
                    "pipeline.phase_skipped_out_of_scope",
                    extra={"phase": phase_id, "design_scope": scope},
                )
                continue
            eligible.add(phase_id)

        if not eligible:
            log.info("pipeline.no_eligible_phases", extra={"project_id": project_id})
            return

        log.info(
            "pipeline.dag_started",
            extra={
                "project_id": project_id,
                "eligible_phases": sorted(eligible),
            },
        )

        _lock = self._status_lock(project_id)
        # `work_done[p]` fires when phase p's work finishes (regardless of
        # whether the UI status has flipped yet). Downstream phases wait on
        # this — NOT on the UI flip — so backend stays parallel.
        work_done: dict[str, asyncio.Event] = {
            p: asyncio.Event() for p in _PHASE_DEPS
        }
        # `flip_done[p]` fires after phase p's status has been written to
        # the DB (in phase-id order). Phase p flips only after every earlier
        # phase in `_PHASE_FLIP_ORDER` has flipped — preserving sequential
        # UI appearance while backend runs in parallel.
        flip_done: dict[str, asyncio.Event] = {
            p: asyncio.Event() for p in _PHASE_DEPS
        }
        # Phases whose dependency failed — we mark them failed without
        # running so we don't waste an LLM call on garbage input.
        skipped_due_to_dep: set[str] = set()
        # Track outcomes for the tail summary.
        outcomes: dict[str, str] = {}

        # Mark the first eligible phase (in flip order) as in_progress so
        # the user sees activity right away.
        first_eligible_in_flip_order = next(
            (p for p in _PHASE_FLIP_ORDER if p in eligible), None,
        )
        if first_eligible_in_flip_order:
            async with _lock:
                await self._proj_svc.async_set_phase_status(
                    project_id, first_eligible_in_flip_order, "in_progress",
                )

        async def _serialised_flip(pid: str, status: str, elapsed: float) -> None:
            """Write phase pid's status to the DB, but only AFTER every
            earlier phase in `_PHASE_FLIP_ORDER` has had its status
            written. Also nudge the next phase to in_progress so the
            sidebar shows continuous activity.

            P26 #12: holds `flip_done[pid]` for `_STATUS_FLIP_INTERLUDE_S`
            seconds AFTER the DB writes so the next sibling phase whose
            work is already done can't immediately flip. Without this
            interlude the user misses parallel-finished phases entirely
            (their elapsed display blips for less than one 3s poll cycle).
            The interlude DOES NOT extend phase work time — only the
            visible status transition is delayed.
            """
            pid_idx = _PHASE_FLIP_ORDER.index(pid)
            for prev_idx in range(pid_idx):
                prev_pid = _PHASE_FLIP_ORDER[prev_idx]
                if prev_pid in eligible:
                    await flip_done[prev_pid].wait()

            extra = {"duration_seconds": round(elapsed, 2)}
            async with _lock:
                await self._proj_svc.async_set_phase_status(
                    project_id, pid, status, extra=extra,
                )
                # Nudge next eligible phase to in_progress so the UI
                # sidebar always shows ONE active phase. Skip if next
                # phase is already failed-by-dep (we'll write its
                # failed status when its own _serialised_flip runs).
                next_pid = next(
                    (p for p in _PHASE_FLIP_ORDER[pid_idx + 1:]
                     if p in eligible and p not in skipped_due_to_dep),
                    None,
                )
                if next_pid is not None and not flip_done[next_pid].is_set():
                    next_status = await self._proj_svc.async_get_phase_status(
                        project_id, next_pid,
                    )
                    if next_status not in ("in_progress", "completed", "failed"):
                        await self._proj_svc.async_set_phase_status(
                            project_id, next_pid, "in_progress",
                        )

            # Hold the next phase's flip for one poll cycle so the user
            # actually SEES it transition through in_progress. Skipped
            # for the LAST phase (nothing to hold open for) and for
            # failures (failure is the terminal state, no transition to
            # protect). Configurable via class attr for tests that need
            # zero-delay execution.
            if (
                self._STATUS_FLIP_INTERLUDE_S > 0
                and status == "completed"
                and next_pid is not None
            ):
                await asyncio.sleep(self._STATUS_FLIP_INTERLUDE_S)
            flip_done[pid].set()

        async def _phase_worker(pid: str) -> dict:
            """Run one phase: wait for DAG deps → execute → flip status."""
            # 1. Wait for all upstream deps to finish their WORK (not their
            #    UI flip — that's serialised separately).
            for dep in _PHASE_DEPS[pid]:
                if dep not in eligible:
                    # Dep was already completed before this run started,
                    # OR is out of scope. Either way, treat as satisfied.
                    continue
                await work_done[dep].wait()
                if outcomes.get(dep) == "failed":
                    # Dep failed — short-circuit. Mark ourselves failed,
                    # and ALSO short-circuit transitively-downstream deps
                    # by setting our work_done event so they can see us
                    # as failed and short-circuit too.
                    skipped_due_to_dep.add(pid)
                    outcomes[pid] = "failed"
                    work_done[pid].set()
                    log.info(
                        "pipeline.phase_skipped_dep_failed",
                        extra={"project_id": project_id, "phase": pid,
                               "failed_dep": dep},
                    )
                    await _serialised_flip(pid, "failed", 0.0)
                    return {"final_status": "failed", "outputs": {}, "elapsed": 0.0}

            # 2. Run the actual phase work. _run_single_phase already
            #    handles per-phase exceptions and never raises here.
            res = await self._run_single_phase(
                project_id=project_id,
                proj=proj,
                phase_id=pid,
                module_path=_PHASE_META[pid][0],
                class_name=_PHASE_META[pid][1],
                phase_name=_PHASE_META[pid][2],
                # IMPORTANT: pass a snapshot of prior_outputs at the
                # moment work begins. Phases that complete after us
                # mustn't retroactively change what this phase saw.
                prior_outputs=dict(prior_outputs),
            )

            final_status = "completed"
            elapsed_s = 0.0
            if isinstance(res, dict):
                final_status = res.get("final_status", "completed")
                elapsed_s = float(res.get("elapsed", 0.0))
                # Merge outputs into the shared prior_outputs so the next
                # phase that fires sees them. Safe because each phase
                # writes a disjoint set of files (per its phase_id).
                if final_status == "completed" and res.get("outputs"):
                    prior_outputs.update(res["outputs"])

            outcomes[pid] = final_status
            # Signal downstream deps that our work is done — they can
            # start NOW (without waiting for the UI flip).
            work_done[pid].set()
            # 3. Now serialise the UI status flip in phase-id order.
            await _serialised_flip(pid, final_status, elapsed_s)
            return res if isinstance(res, dict) else {
                "final_status": final_status, "outputs": {},
                "elapsed": elapsed_s,
            }

        # Fire ALL eligible phase workers concurrently. They synchronise
        # on `work_done` events per the DAG.
        results = await asyncio.gather(
            *(_phase_worker(p) for p in eligible),
            return_exceptions=True,
        )

        # Defensive: if a worker raised (shouldn't happen — _run_single_phase
        # catches its own exceptions), mark the phase failed.
        for pid, res in zip(eligible, results):
            if isinstance(res, Exception):
                log.warning(
                    "pipeline.dag_phase_exception",
                    extra={"project_id": project_id, "phase": pid,
                           "error": str(res)[:300]},
                )
                if not flip_done[pid].is_set():
                    async with _lock:
                        await self._proj_svc.async_set_phase_status(
                            project_id, pid, "failed",
                            extra={"error": str(res)[:300]},
                        )
                    flip_done[pid].set()

        log.info(
            "pipeline.completed",
            extra={
                "project_id": project_id,
                "total_duration_s": round(time.monotonic() - _pipeline_t0, 2),
                "outcomes": outcomes,
            },
        )

    async def _run_single_phase(
        self,
        project_id: int,
        proj: dict,
        phase_id: str,
        module_path: str,
        class_name: str,
        phase_name: str,
        prior_outputs: dict[str, str],
    ) -> dict:
        """Execute one phase. Returns a result dict with:
          - `outputs`: dict[filename → content] written by this phase
          - `final_status`: "completed" or "failed" (what the batch
                           runner should write to phase_statuses)
          - `elapsed`: seconds spent in the work

        P23: status WRITES (in_progress / completed / failed) are NOT
        performed here anymore — the enclosing batch runner writes them
        in phase-id order after all sibling phases in the batch have
        finished so the frontend sidebar shows serial progression.
        """
        log.info("phase.started", extra={"project_id": project_id, "phase": phase_id})
        # P22/P23: per-project lock serialises all phase_statuses writes so
        # concurrent phases in the same batch don't lose each other's
        # updates (lost-update race on JSON column). Held ONLY across the
        # SQL round-trip, not across the LLM call.
        #
        # P23 (2026-04-24): the "in_progress" + "completed" status
        # transitions are NO LONGER written here. The batch runner in
        # `run_pipeline` writes them in phase-id order AFTER all parallel
        # work completes, so the frontend sidebar shows phases advancing
        # one at a time (sequential appearance) while the backend still
        # executes the batch concurrently (actual speed). This keeps
        # the ~16-min parallel wall-clock AND the user's preferred
        # "serial pipeline" visual.
        _lock = self._status_lock(project_id)
        start = time.monotonic()
        new_outputs: dict[str, str] = {}

        try:
            # Lazy-load agent to avoid circular imports + keep startup fast
            import importlib
            module = importlib.import_module(module_path)
            agent_cls = getattr(module, class_name)
            agent = agent_cls()

            # Re-fetch project (async) to get latest state
            proj = await self._proj_svc.async_get(project_id) or proj

            # B1.3 - open a pipeline_runs row so every llm_calls row this
            # phase produces is associated with this exact (project, phase,
            # requirements_hash) tuple. Lets scripts/reproduce_run.py
            # replay deterministic pieces from a logged run later.
            run_id = start_pipeline_run(
                project_id=str(project_id),
                phase_id=phase_id,
                requirements_hash=proj.get("requirements_hash"),
                model=getattr(settings, "primary_model", None),
            )
            run_started_ms = time.monotonic()

            with pipeline_run_context(run_id):
                try:
                    result = await agent.execute(
                        project_context={
                            "project_id": project_id,
                            "name": proj["name"],
                            "design_type": proj["design_type"],
                            "output_dir": proj["output_dir"],
                            "design_parameters": proj.get("design_parameters", {}),
                            "prior_phase_outputs": prior_outputs,
                        },
                        user_input="",
                    )
                finally:
                    if run_id is not None:
                        try:
                            finish_pipeline_run(
                                run_id,
                                status="completed",
                                wall_clock_ms=int(
                                    (time.monotonic() - run_started_ms) * 1000
                                ),
                            )
                        except Exception:
                            pass

            elapsed = time.monotonic() - start

            # Write outputs through StorageAdapter (sync I/O — acceptable for files)
            if result.get("outputs"):
                written = self._storage.write_outputs(proj["name"], result["outputs"])
                for fname, path in written.items():
                    # Collect into the per-phase return value instead of
                    # mutating the shared prior_outputs dict. The
                    # enclosing batch runner merges after all siblings
                    # finish.
                    new_outputs[fname] = result["outputs"][fname]
                    # Ensure content is always a string before writing to DB
                    content_val = result["outputs"][fname]
                    if not isinstance(content_val, str):
                        content_val = json.dumps(content_val, indent=2)
                    # P22: serialise phase-output DB writes too — they
                    # update an updated_at column that might be contested.
                    async with _lock:
                        await self._proj_svc.async_record_phase_output(
                            project_id=project_id,
                            phase_id=phase_id,
                            phase_name=phase_name,
                            content=content_val,
                            output_type="markdown",
                            file_path=str(path),
                            model_used=result.get("model_used", ""),
                            tokens_input=result.get("usage", {}).get("input_tokens", 0),
                            tokens_output=result.get("usage", {}).get("output_tokens", 0),
                            duration_seconds=elapsed,
                        )

            # Respect phase_complete flag — if agent signals failure, mark as failed
            # rather than silently completing with no outputs (e.g. P4 tool not called).
            # P23: we DO NOT write the status here — the batch runner writes it
            # after all phases complete so UI sees a serial flow. We only stash
            # the decision on the return value.
            phase_complete = result.get("phase_complete", True)
            final_status = "completed" if phase_complete else "failed"
            log.info("phase.work_complete",
                     extra={"project_id": project_id, "phase": phase_id,
                            "duration_s": round(elapsed, 2),
                            "final_status": final_status})

        except Exception as exc:
            elapsed = time.monotonic() - start
            final_status = "failed"
            log.exception("phase.failed",
                          extra={"project_id": project_id, "phase": phase_id})
            # P22: phase_output write is separate from phase_status — still
            # emit it now (the phase_outputs table is per-row, no lost-update
            # risk) so the error is persisted even if status-flip happens
            # later in the batch runner.
            async with _lock:
                await self._proj_svc.async_record_phase_output(
                    project_id=project_id,
                    phase_id=phase_id,
                    phase_name=phase_name,
                    content="",
                    status="failed",
                    error_message=str(exc)[:2000],
                    duration_seconds=elapsed,
                )
            # Continue — the batch runner handles status writes in order.

        return {
            "outputs": new_outputs,
            "final_status": final_status,
            "elapsed": elapsed,
        }

    def _load_prior_outputs(self, proj: dict) -> dict[str, str]:
        """Load all previously-written output files into memory for context."""
        outputs: dict[str, str] = {}
        proj_dir = self._storage.project_dir(proj["name"])
        for f in proj_dir.glob("*.md"):
            try:
                outputs[f.name] = f.read_text(encoding="utf-8")
            except Exception:
                pass
        return outputs

    async def run_single_phase(self, project_id: int, phase_id: str) -> dict:
        """
        Execute one specific phase and return result dict.
        Used by the /phases/{phase_id}/execute endpoint.
        """
        meta = {p[0]: p for p in AUTO_PHASES}
        if phase_id not in meta:
            raise ValueError(f"Unknown phase: {phase_id}")

        _, module_path, class_name, phase_name = meta[phase_id]
        proj = await self._proj_svc.async_get(project_id)
        if not proj:
            raise ValueError(f"Project {project_id} not found")

        # P23: `_run_single_phase` no longer writes phase_status; we do
        # it here for the single-phase path so the UI still sees the
        # classic in_progress → completed transition.
        _lock = self._status_lock(project_id)
        async with _lock:
            await self._proj_svc.async_set_phase_status(
                project_id, phase_id, "in_progress",
            )

        prior_outputs = self._load_prior_outputs(proj)
        res = await self._run_single_phase(
            project_id=project_id,
            proj=proj,
            phase_id=phase_id,
            module_path=module_path,
            class_name=class_name,
            phase_name=phase_name,
            prior_outputs=prior_outputs,
        )
        final_status = "completed"
        elapsed = 0.0
        if isinstance(res, dict):
            final_status = res.get("final_status", "completed")
            elapsed = float(res.get("elapsed", 0.0))
        async with _lock:
            await self._proj_svc.async_set_phase_status(
                project_id, phase_id, final_status,
                extra={"duration_seconds": round(elapsed, 2)},
            )
        return await self._proj_svc.async_get(project_id) or {}
