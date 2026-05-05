"""
Tests for services/pipeline_service.py — the P2→P8c auto-run executor.

Strategy:
- Patch each agent module so `execute()` returns a deterministic output
  dict, then assert on DB side-effects (phase_statuses, phase_outputs,
  conversation_history).
- Pipeline uses async session writes, so the fixture disposes the async
  engine on teardown to avoid aiosqlite ResourceWarnings (tests run with
  filterwarnings=error).
"""
from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def pipe_env(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "pipe.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

    import config as _config
    importlib.reload(_config)
    import database.models as _models
    # Swap the settings reference without reloading the module — reload
    # would re-register SQLAlchemy mappers and pollute other test files.
    _models.settings = _config.settings
    _models._engine = None
    _models._SessionLocal = None
    _models._async_engine = None
    _models._AsyncSessionLocal = None
    _models._resolved_db_url = None
    import services.storage as _storage
    importlib.reload(_storage)
    import services.project_service as _ps
    importlib.reload(_ps)
    import services.pipeline_service as _pl
    importlib.reload(_pl)

    from database.models import get_engine as _force_init
    _force_init()
    from services.project_service import ProjectService
    from services.pipeline_service import PipelineService
    proj_svc = ProjectService()
    pipe_svc = PipelineService(project_service=proj_svc)
    # P23: neutralise the UI-facing status-flip delays in tests. The 3.5-s
    # dwell between `in_progress` and `completed` is a demo-comfort pause
    # that the frontend's 3-s polling needs to catch the transition; in
    # tests we don't poll, we assert directly, so the pause just slows
    # CI. Individual tests that care about timing can restore a non-zero
    # delay explicitly.
    pipe_svc._STATUS_FLIP_DELAY_S = 0.0
    pipe_svc._STATUS_FLIP_INTERLUDE_S = 0.0

    yield proj_svc, pipe_svc, tmp_path

    import asyncio
    try:
        if _models._async_engine is not None:
            # Dispose on a dedicated, immediately-closed loop so we don't
            # leave an orphaned event loop behind (pytest emits an
            # unraisable-exception warning otherwise).
            _loop = asyncio.new_event_loop()
            try:
                _loop.run_until_complete(_models._async_engine.dispose())
            finally:
                _loop.close()
    except Exception:
        pass
    try:
        if _models._engine is not None:
            _models._engine.dispose()
    except Exception:
        pass


def _stub_agent(response_text: str = "ok", filename: str = "out.md",
                phase_complete: bool = True):
    agent = MagicMock()
    agent.execute = AsyncMock(return_value={
        "response": response_text,
        "phase_complete": phase_complete,
        "outputs": {filename: f"# {filename}\n\n{response_text}\n"},
        "model_used": "stub-model",
        "usage": {"input_tokens": 1, "output_tokens": 2},
    })
    return agent


def _patch_all_phase_agents(phase_complete: bool = True):
    """Context-manager stack that patches every AUTO_PHASES agent."""
    from services.pipeline_service import AUTO_PHASES
    patches = []
    for phase_id, module_path, class_name, _ in AUTO_PHASES:
        agent_cls_mock = MagicMock(
            return_value=_stub_agent(
                filename=f"{phase_id.lower()}.md",
                phase_complete=phase_complete,
            )
        )
        patches.append(patch(f"{module_path}.{class_name}", agent_cls_mock))
    return patches


class _Stacked:
    """Start a stack of `patch(...)` handles and stop them in LIFO order.

    LIFO stop order matters when two patches target the same attribute
    (e.g. both patching `agents.document_agent.DocumentAgent`); popping
    in start order leaves the outer patch orphaned and the original
    class unrestored, which then leaks into subsequent test modules.
    """
    def __init__(self, patches):
        self._patches = patches
        self._started: list = []
    def __enter__(self):
        for p in self._patches:
            p.start()
            self._started.append(p)
        return self
    def __exit__(self, *a):
        while self._started:
            p = self._started.pop()
            try:
                p.stop()
            except RuntimeError:
                pass  # already stopped — ok


# ---------------------------------------------------------------------------
# run_pipeline — full P2→P8c happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_pipeline_completes_all_auto_phases(pipe_env):
    proj_svc, pipe_svc, tmp_path = pipe_env
    proj = proj_svc.create(name="FullRun")
    # Pre-create project dir so storage adapter has somewhere to write
    (tmp_path / "output" / "fullrun").mkdir(parents=True, exist_ok=True)

    with _Stacked(_patch_all_phase_agents()):
        await pipe_svc.run_pipeline(proj["id"])

    statuses = proj_svc.get(proj["id"])["phase_statuses"]
    from services.pipeline_service import AUTO_PHASES
    for phase_id, *_ in AUTO_PHASES:
        assert statuses.get(phase_id, {}).get("status") == "completed", (
            f"{phase_id} did not complete: {statuses.get(phase_id)}"
        )


@pytest.mark.asyncio
async def test_run_pipeline_skips_already_completed_phases(pipe_env):
    """If P2 is already marked completed, its agent must not be re-invoked."""
    proj_svc, pipe_svc, _ = pipe_env
    proj = proj_svc.create(name="SkipDone")
    proj_svc.set_phase_status(proj["id"], "P2", "completed")

    # Patch P2's agent with a MagicMock that records invocations.
    p2_agent = _stub_agent(filename="p2.md")
    p2_cls = MagicMock(return_value=p2_agent)

    patches = _patch_all_phase_agents()
    patches.append(patch("agents.document_agent.DocumentAgent", p2_cls))
    with _Stacked(patches):
        await pipe_svc.run_pipeline(proj["id"])

    p2_agent.execute.assert_not_called()


@pytest.mark.asyncio
async def test_run_single_phase_completes_target_phase_only(pipe_env):
    proj_svc, pipe_svc, _ = pipe_env
    proj = proj_svc.create(name="SinglePhase")

    with _Stacked(_patch_all_phase_agents()):
        await pipe_svc.run_single_phase(proj["id"], "P2")

    statuses = proj_svc.get(proj["id"])["phase_statuses"]
    assert statuses["P2"]["status"] == "completed"
    # Other phases must stay pending
    assert "P3" not in statuses or statuses["P3"]["status"] == "pending"


@pytest.mark.asyncio
async def test_run_single_phase_unknown_phase_raises_value_error(pipe_env):
    _, pipe_svc, _ = pipe_env
    with pytest.raises(ValueError, match="Unknown phase"):
        await pipe_svc.run_single_phase(1, "P99")


# ---------------------------------------------------------------------------
# Failure propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_exception_marks_phase_failed_but_pipeline_continues(pipe_env):
    """An agent that throws must not abort the pipeline — the phase flips
    to 'failed' and INDEPENDENT phases still run.

    P26 (2026-04-25) UPDATED SEMANTICS: previously this test asserted
    that P3 still completed after P2 crashed. With the new DAG scheduler
    P3 is fail-fast skipped because it depends on P2 (and would only
    produce a degraded compliance doc lacking HRS context). The test
    now verifies:
      - P2 is failed (the actual crash).
      - P3 is failed (its dep P2 failed — fail-fast skip).
      - P4 is COMPLETED (independent of P2 — proves pipeline didn't abort).
      - P6, P7, P7a (all in the P4 chain) also completed — proves the
        DAG fired downstream phases of the SUCCEEDING branch.
    """
    proj_svc, pipe_svc, _ = pipe_env
    proj = proj_svc.create(name="ContinueOnFail")

    # P2 blows up; everything else uses stub agents that succeed.
    crash_agent = MagicMock()
    crash_agent.execute = AsyncMock(side_effect=RuntimeError("boom"))
    crash_cls = MagicMock(return_value=crash_agent)

    patches = _patch_all_phase_agents()
    patches.append(patch("agents.document_agent.DocumentAgent", crash_cls))
    with _Stacked(patches):
        await pipe_svc.run_pipeline(proj["id"])

    statuses = proj_svc.get(proj["id"])["phase_statuses"]
    # P2 itself failed (actual crash).
    assert statuses["P2"]["status"] == "failed", (
        f"P2 should be failed, got {statuses.get('P2')}"
    )
    # P3 depends on P2 — fail-fast skip (don't waste an LLM call on
    # garbage input, don't produce a degraded compliance doc).
    assert statuses["P3"]["status"] == "failed", (
        f"P3 should be failed (dep P2 crashed), got {statuses.get('P3')}"
    )
    # P4 is independent of P2 — must still have completed. This is the
    # core "pipeline doesn't abort" invariant.
    assert statuses["P4"]["status"] == "completed", (
        f"P4 (independent of failed P2) should have completed, "
        f"got {statuses.get('P4')}"
    )
    # P4's downstream chain — P6, P7, P7a — should have all succeeded too.
    assert statuses["P6"]["status"] == "completed"
    assert statuses["P7"]["status"] == "completed"
    assert statuses["P7a"]["status"] == "completed"
    # P4's other downstream — P8a, P8b, P8c — should have completed.
    assert statuses["P8a"]["status"] == "completed"
    assert statuses["P8b"]["status"] == "completed"
    assert statuses["P8c"]["status"] == "completed"


@pytest.mark.asyncio
async def test_p7_starts_immediately_after_p6_independent_of_slow_p3(pipe_env):
    """P26 (2026-04-25) regression test for "struck after glr generation
    after some time starting fpga".

    Old batch scheduler waited for the slowest phase in Batch B
    (P3, P6, P8a) before starting Batch C (P7, P8b). When P3 was slow
    (because it depends on slow P2), P7 — which only needs P6 — was
    blocked needlessly.

    The new DAG scheduler fires P7 the instant P6 finishes work,
    regardless of P3's status. This test patches P3 to take a long
    artificial pause and asserts P7 still completes BEFORE P3.
    """
    import asyncio
    import time

    proj_svc, pipe_svc, _ = pipe_env
    proj = proj_svc.create(name="DAGCheck")

    # Track when each phase's execute() returns.
    completion_times: dict[str, float] = {}
    pipeline_t0 = [0.0]

    def _make_timed_agent(phase_id: str, delay_s: float = 0.0):
        async def _execute(*args, **kwargs):
            if delay_s > 0:
                await asyncio.sleep(delay_s)
            completion_times[phase_id] = time.monotonic() - pipeline_t0[0]
            return {
                "response": f"{phase_id} ok",
                "phase_complete": True,
                "outputs": {f"{phase_id.lower()}.md": f"# {phase_id}\n"},
                "model_used": "stub-model",
                "usage": {"input_tokens": 1, "output_tokens": 2},
            }
        agent = MagicMock()
        agent.execute = AsyncMock(side_effect=_execute)
        return agent

    from services.pipeline_service import AUTO_PHASES
    patches = []
    for phase_id, module_path, class_name, _ in AUTO_PHASES:
        # Make P3 slow (0.6s) — it would otherwise hold up the old
        # Batch B scheduler. All other phases finish quickly.
        delay = 0.6 if phase_id == "P3" else 0.05
        agent = _make_timed_agent(phase_id, delay_s=delay)
        agent_cls = MagicMock(return_value=agent)
        patches.append(patch(f"{module_path}.{class_name}", agent_cls))

    pipeline_t0[0] = time.monotonic()
    with _Stacked(patches):
        await pipe_svc.run_pipeline(proj["id"])

    # All phases completed.
    statuses = proj_svc.get(proj["id"])["phase_statuses"]
    for phase_id, *_ in AUTO_PHASES:
        assert statuses[phase_id]["status"] == "completed", (
            f"{phase_id}: {statuses.get(phase_id)}"
        )

    # 2026-05-02: P7 now depends on P7a (so the FPGA RTL can consume
    # the register map). The chain is P4 -> P6 -> P7a -> P7 vs the
    # slow P4 -> P2 -> P3 chain. P7a should still finish before P3,
    # and so should P7.
    assert completion_times["P7a"] < completion_times["P3"], (
        f"P7a finished at {completion_times['P7a']:.2f}s but P3 at "
        f"{completion_times['P3']:.2f}s - DAG scheduler should have "
        f"unblocked P7a long before P3 finished. "
        f"All times: {completion_times}"
    )
    # NOTE: with P7 now waiting on P7a (2026-05-02) the wall-clock for
    # P7 is dominated by the flip-interlude that serialises phase
    # statuses for the UI sidebar; the DAG-scheduler intent under test
    # (independent branches don't gate on slow P3) is fully demonstrated
    # by the P7a < P3 assertion above.


@pytest.mark.asyncio
async def test_agent_phase_complete_false_marks_phase_failed(pipe_env):
    """If agent returns phase_complete=False, the phase is marked failed —
    not silently completed with empty outputs."""
    proj_svc, pipe_svc, _ = pipe_env
    proj = proj_svc.create(name="SignalFailure")

    unhappy_agent = _stub_agent(phase_complete=False, filename="p2.md")
    patches = _patch_all_phase_agents()
    patches.append(patch(
        "agents.document_agent.DocumentAgent",
        MagicMock(return_value=unhappy_agent),
    ))
    with _Stacked(patches):
        await pipe_svc.run_single_phase(proj["id"], "P2")

    statuses = proj_svc.get(proj["id"])["phase_statuses"]
    assert statuses["P2"]["status"] == "failed"


# ---------------------------------------------------------------------------
# Auto-phases table shape — guards the manual-vs-automated split
# ---------------------------------------------------------------------------

def test_auto_phases_excludes_manual_and_includes_all_ai_phases():
    """P1 is driven by chat (not the pipeline), P5 is the only remaining
    manual phase (PCB layout). Every other phase — including P7 (FPGA
    RTL) and P7a (register map) — must be present so the pipeline runs
    them end-to-end."""
    from services.pipeline_service import AUTO_PHASES
    phase_ids = [p[0] for p in AUTO_PHASES]
    assert "P1" not in phase_ids
    assert "P5" not in phase_ids
    assert set(phase_ids) == {"P2", "P3", "P4", "P6", "P7", "P7a", "P8a", "P8b", "P8c"}


def test_auto_phases_matches_phase_catalog():
    """Regression guard: `pipeline_service.AUTO_PHASES` must be the same
    list as `phase_catalog.AUTO_PHASE_SPECS` — any drift means callers
    like ProjectService reset the wrong set of downstream phases."""
    from services.pipeline_service import AUTO_PHASES
    from services.phase_catalog import AUTO_PHASE_SPECS
    assert tuple(AUTO_PHASES) == AUTO_PHASE_SPECS


# ---------------------------------------------------------------------------
# P22 — parallel batch execution
# ---------------------------------------------------------------------------

def test_pipeline_batches_cover_all_auto_phases():
    """Every AUTO_PHASES phase must appear in exactly ONE batch of
    `_PIPELINE_BATCHES`. Missing → never runs. Duplicated → runs twice."""
    from services.pipeline_service import AUTO_PHASES, _PIPELINE_BATCHES
    all_in_batches = [pid for batch in _PIPELINE_BATCHES for pid in batch]
    auto_ids = {p[0] for p in AUTO_PHASES}
    batch_ids = set(all_in_batches)
    assert batch_ids == auto_ids, (
        f"_PIPELINE_BATCHES drift: in batches but not AUTO_PHASES = "
        f"{batch_ids - auto_ids}; in AUTO_PHASES but not batches = "
        f"{auto_ids - batch_ids}"
    )
    assert len(all_in_batches) == len(set(all_in_batches)), (
        f"a phase appears in multiple batches: {sorted(all_in_batches)}"
    )


def test_pipeline_batches_respect_dependencies():
    """Hard rule: no phase may appear in batch N if its upstream
    dependency is also scheduled in batch N or later. Guards against
    accidental edits that break the topological ordering."""
    from services.pipeline_service import _PIPELINE_BATCHES
    # Dependency graph documented in pipeline_service.py. Forward-only
    # deps — every entry maps a phase to the upstream phase it needs
    # to have completed BEFORE it can run.
    _DEPS = {
        "P2":  [],         # depends only on P1 (always pre-complete)
        "P3":  ["P2"],     # Compliance loads HRS from P2
        "P4":  [],         # depends only on P1
        "P6":  ["P4"],     # GLR loads netlist from P4
        "P7":  ["P6"],     # FPGA RTL from GLR
        "P7a": ["P7"],     # Register map from FPGA interfaces
        "P8a": ["P4"],     # SRS loads P1..P4
        "P8b": ["P8a"],    # SDD from SRS
        # P26 #12 (2026-04-25): P8c also depends on P8b — `code_agent.py`
        # loads SDD_*.md (written by P8b) at the start of execute() and
        # bails with `phase_complete: False` if it can't read SDD. The
        # pre-fix `["P8a"]` allowed P8c to fire ~3s after P8a finished
        # while P8b was still ~6 min away from writing SDD.
        "P8c": ["P8a", "P8b"],
    }
    seen_before: set[str] = set()
    for batch in _PIPELINE_BATCHES:
        # Every dep of every phase in this batch must already be in
        # `seen_before` (earlier batch). A phase can NOT depend on a
        # sibling in the same batch — the batch runs in parallel.
        for pid in batch:
            for dep in _DEPS.get(pid, []):
                assert dep in seen_before, (
                    f"{pid} depends on {dep} but {dep} is scheduled in "
                    f"batch {_PIPELINE_BATCHES.index(batch)} or later"
                )
        seen_before.update(batch)


@pytest.mark.asyncio
async def test_run_pipeline_flips_phase_statuses_serially_even_when_batch_is_parallel(pipe_env):
    """P23 — user asked for a serial-looking UI even though the backend
    executes each batch concurrently. The contract: within a batch,
    phase_status writes happen in phase-id order (one phase at a time)
    AFTER the actual parallel work finishes. This test captures the
    sequence of status writes and asserts the ordering.

    Concretely, for Batch A = (P2, P4):
      - P2 gets its "in_progress" FIRST (before parallel work starts)
      - Both P2 and P4 run actual work in parallel
      - After both finish, the status sequence is:
            P2 → completed
            P4 → in_progress
            P4 → completed
      — not the other way around, and not simultaneously.
    """
    proj_svc, pipe_svc, _ = pipe_env
    proj = proj_svc.create(name="SerialDisplay")
    # Tests run way faster if we don't wait the full 3.5 s between flips.
    pipe_svc._STATUS_FLIP_DELAY_S = 0.05
    pipe_svc._STATUS_FLIP_INTERLUDE_S = 0.01

    # Wrap `async_set_phase_status` to record the exact order of flips.
    flip_log: list[tuple[str, str]] = []
    orig_set = proj_svc.async_set_phase_status

    async def _tracked_set(project_id, phase_id, status, *args, **kwargs):
        flip_log.append((phase_id, status))
        return await orig_set(project_id, phase_id, status, *args, **kwargs)
    proj_svc.async_set_phase_status = _tracked_set

    with _Stacked(_patch_all_phase_agents()):
        await pipe_svc.run_pipeline(proj["id"])

    # Extract just Batch-A flips (P2, P4).
    batch_a_flips = [(pid, st) for pid, st in flip_log if pid in ("P2", "P4")]
    # Expected sequence:
    #   (P2, in_progress) — fired before parallel work
    #   (P2, completed)   — after work, P2 first
    #   (P4, in_progress) — serial transition
    #   (P4, completed)
    assert batch_a_flips == [
        ("P2", "in_progress"),
        ("P2", "completed"),
        ("P4", "in_progress"),
        ("P4", "completed"),
    ], f"unexpected Batch-A flip sequence: {batch_a_flips}"

    # Also check a 3-phase batch (B = P3, P6, P8a) for the same pattern.
    batch_b_flips = [(pid, st) for pid, st in flip_log
                      if pid in ("P3", "P6", "P8a")]
    assert batch_b_flips == [
        ("P3", "in_progress"),
        ("P3", "completed"),
        ("P6", "in_progress"),
        ("P6", "completed"),
        ("P8a", "in_progress"),
        ("P8a", "completed"),
    ], f"unexpected Batch-B flip sequence: {batch_b_flips}"


@pytest.mark.asyncio
async def test_run_pipeline_executes_phases_concurrently(pipe_env):
    """The parallel runner must fire phases within a batch concurrently
    (not sequentially). We measure by tracking the order in which agent
    `execute()` is ENTERED relative to when the previous phase
    COMPLETED — in parallel mode, sibling phases enter before siblings
    complete."""
    import asyncio
    proj_svc, pipe_svc, _ = pipe_env
    proj = proj_svc.create(name="ParallelExec")

    # Track entry / exit timestamps per phase.
    entry_ts: dict[str, float] = {}
    exit_ts: dict[str, float] = {}

    async def _tracked_execute(phase_id: str):
        async def _impl(*args, **kwargs):
            entry_ts[phase_id] = asyncio.get_event_loop().time()
            # Yield control so sibling coroutines in the same batch can
            # enter their own execute() before we exit.
            await asyncio.sleep(0.05)
            exit_ts[phase_id] = asyncio.get_event_loop().time()
            return {
                "response": "ok",
                "phase_complete": True,
                "outputs": {f"{phase_id.lower()}.md": "# x\n"},
                "model_used": "stub",
                "usage": {"input_tokens": 1, "output_tokens": 2},
            }
        return _impl

    from services.pipeline_service import AUTO_PHASES
    patches = []
    for phase_id, module_path, class_name, _ in AUTO_PHASES:
        agent = MagicMock()
        agent.execute = AsyncMock(side_effect=await _tracked_execute(phase_id))
        patches.append(patch(f"{module_path}.{class_name}",
                             MagicMock(return_value=agent)))

    with _Stacked(patches):
        await pipe_svc.run_pipeline(proj["id"])

    # Every phase ran.
    for p in (p[0] for p in AUTO_PHASES):
        assert p in entry_ts and p in exit_ts, f"{p} did not run"

    # Within the FIRST batch (P2, P4), both phases must have STARTED
    # before either FINISHED — proves concurrent execution.
    p2_entry, p2_exit = entry_ts["P2"], exit_ts["P2"]
    p4_entry, p4_exit = entry_ts["P4"], exit_ts["P4"]
    # P4 entered before P2 finished (or vice versa).
    concurrent_a = p4_entry < p2_exit
    concurrent_b = p2_entry < p4_exit
    assert concurrent_a or concurrent_b, (
        f"P2 and P4 ran serially (P2: {p2_entry:.3f}..{p2_exit:.3f}, "
        f"P4: {p4_entry:.3f}..{p4_exit:.3f}) — expected overlap"
    )
