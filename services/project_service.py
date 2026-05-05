"""
ProjectService — authoritative source of truth for project + phase lifecycle.

Rules:
- DB is always written first; session_state/UI is derived from DB reads.
- Phase status transitions are atomic: in_progress → completed|failed.
- No business logic may live in app.py or route handlers.

Session strategy:
- Sync  methods (create, get, list_all, set_phase_status, …):
    Used by FastAPI route handlers and Streamlit — SQLite is fast enough for
    these short, infrequent reads/writes.
- Async methods (async_set_phase_status, async_append_conversation, …):
    Used inside PipelineService / ChatService background tasks so they never
    block the FastAPI async event loop.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from config import settings
from database.models import (
    get_session,
    get_async_session_factory,
    ProjectDB,
    PhaseOutputDB,
)
from services.phase_catalog import DOWNSTREAM_OF_P1
from services.project_reset import (
    RESETTABLE_COLUMNS as _RESETTABLE_COLUMNS,
    summarise_reset,
)
from services.storage import StorageAdapter

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data-transfer types (plain dicts — no Pydantic dependency in service layer)
# ---------------------------------------------------------------------------

VALID_DESIGN_SCOPES = {"full", "front-end", "downconversion", "dsp"}

# Project-type catalogue. Each value drives:
#   - which architecture set / tier-1 spec questions the wizard shows
#     (`hardware-pipeline-v5-react/src/data/rfArchitect.ts`)
#   - which direction `tools.rf_cascade.compute_cascade` runs in
#     (rx | tx | bidirectional | passive | dc-dc)
#   - which audit branch fires in `services.rf_audit`
#
# P26 #13 (2026-04-25): added transceiver, power_supply, switch_matrix
# alongside receiver / transmitter. New types currently reuse the
# transmitter cascade for the "active" direction (see
# `tools.rf_cascade._direction_for_project_type`); a follow-up will
# add bespoke math (DC-DC efficiency curves for power_supply,
# routing-algebra IL/isolation for switch_matrix).
VALID_PROJECT_TYPES = {
    "receiver",
    "transmitter",
    "transceiver",
    "power_supply",
    "switch_matrix",
}


def _project_to_dict(p: ProjectDB) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description or "",
        "design_type": p.design_type or "general",
        "design_scope": getattr(p, "design_scope", None) or "full",
        "project_type": getattr(p, "project_type", None) or "receiver",
        "current_phase": p.current_phase or "P1",
        "phase_statuses": dict(p.phase_statuses or {}),
        "conversation_history": list(p.conversation_history or []),
        "design_parameters": dict(p.design_parameters or {}),
        "output_dir": p.output_dir or "",
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "requirements_hash": getattr(p, "requirements_hash", None),
        "requirements_frozen_at": (
            p.requirements_frozen_at.isoformat()
            if getattr(p, "requirements_frozen_at", None) else None
        ),
    }


# ---------------------------------------------------------------------------
# A2.1 — stale-phase detection
# ---------------------------------------------------------------------------

# All downstream AI phases that depend on P1 requirements. Owned by
# `services.phase_catalog` (single source of truth); re-exported here as a
# tuple for the legacy `compute_stale_phase_ids` API below. Including P7
# (FPGA RTL) is deliberate — P7 is now automated and must be reset when
# the P1 lock changes.
_DOWNSTREAM_AI_PHASES: tuple[str, ...] = tuple(DOWNSTREAM_OF_P1)


def compute_stale_phase_ids(
    phase_statuses: dict,
    current_hash: Optional[str],
) -> list[str]:
    """
    Return the list of phase IDs whose last completion used a
    `requirements_hash_at_completion` that differs from the project's current
    `requirements_hash`. If the project has no hash yet we return [] (nothing
    is "stale" because nothing has been frozen).

    This is the predicate the UI uses to light up the "Re-run stale phases"
    button and show "⚠ outdated" badges on individual phase tiles.
    """
    if not current_hash:
        return []
    stale: list[str] = []
    for phase_id in _DOWNSTREAM_AI_PHASES:
        entry = phase_statuses.get(phase_id)
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "completed":
            continue
        hash_at_completion = entry.get("requirements_hash_at_completion")
        if hash_at_completion and hash_at_completion != current_hash:
            stale.append(phase_id)
    return stale


# ---------------------------------------------------------------------------
# ProjectService
# ---------------------------------------------------------------------------

class ProjectService:
    """Manages project lifecycle and phase status — DB is the single source of truth."""

    def __init__(self, storage: Optional[StorageAdapter] = None):
        self._storage = storage or StorageAdapter.local(settings.output_dir)

    # ── CRUD ────────────────────────────────────────────────────────────────

    def create(
        self,
        name: str,
        description: str = "",
        design_type: str = "rf",
        design_scope: str = "full",
        project_type: str = "receiver",
    ) -> dict:
        """Create a project in the DB and its output directory."""
        scope = (design_scope or "full").strip().lower()
        if scope not in VALID_DESIGN_SCOPES:
            raise ValueError(
                f"Invalid design_scope '{design_scope}'. "
                f"Must be one of: {sorted(VALID_DESIGN_SCOPES)}"
            )
        ptype = (project_type or "receiver").strip().lower()
        if ptype not in VALID_PROJECT_TYPES:
            raise ValueError(
                f"Invalid project_type '{project_type}'. "
                f"Must be one of: {sorted(VALID_PROJECT_TYPES)}."
            )
        session = get_session()
        try:
            output_dir = self._storage.project_dir(name)
            db = ProjectDB(
                name=name,
                description=description,
                design_type=design_type,
                design_scope=scope,
                project_type=ptype,
                output_dir=str(output_dir),
                current_phase="P1",
                phase_statuses={},
                conversation_history=[],
            )
            session.add(db)
            session.commit()
            session.refresh(db)
            log.info(
                "project.created",
                extra={"project_id": db.id, "project_name": name,
                       "design_scope": scope, "project_type": ptype},
            )
            return _project_to_dict(db)
        except Exception:
            session.rollback()
            log.exception("project.create_failed", extra={"project_name": name})
            raise
        finally:
            session.close()

    # ── Scope update (wizard may refine scope mid-project) ──────────────────

    def set_design_scope(self, project_id: int, scope: str) -> dict:
        """Update the project's design_scope. Used when the wizard narrows
        or widens the scope after initial creation. Returns the full project dict.
        """
        scope = (scope or "").strip().lower()
        if scope not in VALID_DESIGN_SCOPES:
            raise ValueError(
                f"Invalid design_scope '{scope}'. "
                f"Must be one of: {sorted(VALID_DESIGN_SCOPES)}"
            )
        session = get_session()
        try:
            p = session.query(ProjectDB).filter(ProjectDB.id == project_id).first()
            if not p:
                raise ValueError(f"Project {project_id} not found")
            p.design_scope = scope
            session.commit()
            log.info(
                "project.scope_updated",
                extra={"project_id": project_id, "design_scope": scope},
            )
            session.refresh(p)
            return _project_to_dict(p)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get(self, project_id: int) -> Optional[dict]:
        """Load project from DB — always fresh, never from session state."""
        session = get_session()
        try:
            # Force fresh read — expire all cached objects and flush any pending state
            session.expire_all()
            p = session.query(ProjectDB).filter(ProjectDB.id == project_id).first()
            return _project_to_dict(p) if p else None
        finally:
            session.close()

    def list_all(self) -> list[dict]:
        session = get_session()
        try:
            rows = session.query(ProjectDB).order_by(ProjectDB.created_at.desc()).all()
            return [_project_to_dict(p) for p in rows]
        finally:
            session.close()

    # ── Phase status ────────────────────────────────────────────────────────

    # All AI phase IDs downstream of P1 — must be reset when P1 requirements
    # change. Sourced from `phase_catalog.DOWNSTREAM_OF_P1` so there's one
    # authoritative list for `pipeline_service`, `project_service` and
    # `stale_phases` to share. Includes P7 (FPGA RTL) — promoted from manual
    # to automated so it now resets alongside P7a.
    _DOWNSTREAM_AI_PHASES = list(DOWNSTREAM_OF_P1)

    def set_phase_status(
        self,
        project_id: int,
        phase_id: str,
        status: str,            # "in_progress" | "completed" | "failed"
        extra: Optional[dict] = None,
        reset_downstream: bool = False,
    ) -> dict:
        """
        Atomically update a phase's status in the DB.
        Returns the full updated phase_statuses dict.

        Args:
            reset_downstream: When True AND phase_id == 'P1' AND status == 'completed',
                              reset all downstream AI phases to 'pending' because P1
                              requirements changed and all outputs are now stale.
        """
        session = get_session()
        try:
            p = session.query(ProjectDB).filter(ProjectDB.id == project_id).first()
            if not p:
                raise ValueError(f"Project {project_id} not found")

            statuses = dict(p.phase_statuses or {})
            was_already_complete = statuses.get(phase_id, {}).get("status") == "completed"
            entry: dict = {"status": status, "updated_at": datetime.utcnow().isoformat()}
            if extra:
                entry.update(extra)
            # A2.1 — stamp the requirements_hash in place at completion so the
            # stale-phase detector can tell whether this output was generated
            # against the now-current lock or an older one.
            if status == "completed":
                current_hash = getattr(p, "requirements_hash", None)
                if current_hash:
                    entry["requirements_hash_at_completion"] = current_hash
            statuses[phase_id] = entry

            # When P1 is RE-completed (was already done → new requirements submitted),
            # reset all downstream AI phases to pending so they pick up fresh requirements.
            if (phase_id == "P1" and status == "completed"
                    and (reset_downstream or was_already_complete)):
                ts = datetime.utcnow().isoformat()
                for ds_phase in self._DOWNSTREAM_AI_PHASES:
                    if ds_phase in statuses:
                        statuses[ds_phase] = {"status": "pending", "updated_at": ts}
                log.info(
                    "phase.downstream_reset: P1 requirements updated — "
                    "downstream phases reset to pending",
                    extra={"project_id": project_id},
                )

            p.phase_statuses = statuses
            flag_modified(p, "phase_statuses")  # force SQLAlchemy to detect JSON column change
            if status == "completed" and phase_id == "P1":
                p.current_phase = "P2"
            session.commit()

            log.info(
                "phase.status_updated",
                extra={"project_id": project_id, "phase": phase_id, "status": status},
            )
            return statuses
        except Exception:
            session.rollback()
            log.exception("phase.status_update_failed",
                          extra={"project_id": project_id, "phase": phase_id})
            raise
        finally:
            session.close()

    def get_phase_status(self, project_id: int, phase_id: str) -> str:
        """Return status string for a phase, defaulting to 'pending'."""
        proj = self.get(project_id)
        if not proj:
            return "pending"
        return proj["phase_statuses"].get(phase_id, {}).get("status", "pending")

    # ── A1.1 / A2.1 — Requirements lock persistence ─────────────────────────

    def save_requirements_lock(self, project_id: int, lock_row: dict) -> None:
        """
        Persist a frozen `RequirementsLock` onto the project row.

        `lock_row` is the dict produced by `requirements_lock.save_to_row()` — it
        contains `requirements_hash`, `requirements_frozen_at` (ISO-8601 string),
        and `requirements_locked_json` (canonical JSON).
        """
        if not lock_row:
            return
        session = get_session()
        try:
            p = session.query(ProjectDB).filter(ProjectDB.id == project_id).first()
            if not p:
                raise ValueError(f"Project {project_id} not found")
            p.requirements_hash = lock_row.get("requirements_hash")
            ts = lock_row.get("requirements_frozen_at")
            if ts:
                try:
                    p.requirements_frozen_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    p.requirements_frozen_at = datetime.utcnow()
            p.requirements_locked_json = lock_row.get("requirements_locked_json")
            session.commit()
            log.info(
                "project.requirements_lock_saved",
                extra={"project_id": project_id,
                       "hash_prefix": (p.requirements_hash or "")[:12]},
            )
        except Exception:
            session.rollback()
            log.exception(
                "project.requirements_lock_save_failed",
                extra={"project_id": project_id},
            )
            raise
        finally:
            session.close()

    def get_stale_phase_ids(self, project_id: int) -> list[str]:
        """Return the list of downstream phase IDs whose last output was
        generated against an older requirements_hash than the current lock.

        Used by the /status endpoint (A2.1) and the rerun-stale endpoint (A2.2).
        """
        proj = self.get(project_id)
        if not proj:
            return []
        return compute_stale_phase_ids(
            proj.get("phase_statuses", {}),
            proj.get("requirements_hash"),
        )

    # ── E1 — Judge-mode wipe-state ──────────────────────────────────────────

    # Columns cleared by `reset_state`. Kept as a class attribute so tests can
    # assert against it and the API handler can echo the list back to callers.
    # Source of truth lives in services/project_reset.py (pure, stdlib-only).
    RESETTABLE_COLUMNS = _RESETTABLE_COLUMNS

    def reset_state(self, project_id: int, *, phases_only: bool = False) -> dict:
        """Wipe project state back to pre-demo defaults.

        Default behaviour clears `phase_statuses`, `conversation_history`,
        `design_parameters`, and every requirements-lock column.

        When `phases_only=True`, only `phase_statuses` and the lock columns
        are cleared - `conversation_history` and `design_parameters` are
        preserved so the user can re-run the pipeline against the same
        captured P1 context without re-typing the chat.

        Preserves identity fields: `id`, `name`, `description`,
        `design_type`, `output_dir`, `created_at`. Resets `current_phase`
        back to `P1` (full reset) or leaves it untouched (phases_only).

        Idempotent. Returns the cleared dict so the API handler can surface
        what was wiped.
        """
        session = get_session()
        try:
            p = session.query(ProjectDB).filter(ProjectDB.id == project_id).first()
            if not p:
                raise ValueError(f"Project {project_id} not found")

            # Snapshot "before" for the summariser — the pure helper in
            # services.project_reset owns the counting logic.
            before = {
                "phase_statuses": dict(p.phase_statuses or {}),
                "conversation_history": list(p.conversation_history or []),
                "design_parameters": dict(p.design_parameters or {}),
                "requirements_hash": getattr(p, "requirements_hash", None),
            }

            # JSON columns -> empty containers. Use fresh containers (not
            # shared references) so we don't accidentally keep mutation
            # aliasing into the old dict.
            p.phase_statuses = {}
            flag_modified(p, "phase_statuses")
            if not phases_only:
                p.conversation_history = []
                flag_modified(p, "conversation_history")
                p.design_parameters = {}
                flag_modified(p, "design_parameters")

            # Lock columns - nullable. Do not touch identity columns.
            p.requirements_hash = None
            p.requirements_frozen_at = None
            p.requirements_locked_json = None

            # Back to the start of the pipeline (full reset only - phases_only
            # leaves current_phase alone since the user is mid-iteration).
            if not phases_only:
                p.current_phase = "P1"

            session.commit()

            summary = summarise_reset(before, {"current_phase": p.current_phase})
            log.info(
                "project.state_reset",
                extra={"project_id": project_id, **summary},
            )
            return {"project_id": project_id, **summary}
        except Exception:
            session.rollback()
            log.exception(
                "project.state_reset_failed", extra={"project_id": project_id}
            )
            raise
        finally:
            session.close()

    # ── Conversation history ─────────────────────────────────────────────────

    def append_conversation(
        self,
        project_id: int,
        role: str,
        content: str,
        design_parameters: Optional[dict] = None,
    ) -> None:
        """Append a message to the project's conversation history in DB."""
        session = get_session()
        try:
            p = session.query(ProjectDB).filter(ProjectDB.id == project_id).first()
            if not p:
                raise ValueError(f"Project {project_id} not found")
            history = list(p.conversation_history or [])
            history.append({"role": role, "content": content})
            p.conversation_history = history
            flag_modified(p, "conversation_history")  # force SQLAlchemy to detect JSON column change
            if design_parameters:
                p.design_parameters = {**(p.design_parameters or {}), **design_parameters}
                flag_modified(p, "design_parameters")
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── Phase outputs ────────────────────────────────────────────────────────

    def record_phase_output(
        self,
        project_id: int,
        phase_id: str,
        phase_name: str,
        content: str,
        output_type: str = "markdown",
        file_path: str = "",
        model_used: str = "",
        tokens_input: int = 0,
        tokens_output: int = 0,
        duration_seconds: float = 0.0,
        status: str = "completed",
        error_message: str = "",
    ) -> None:
        session = get_session()
        try:
            row = PhaseOutputDB(
                project_id=project_id,
                phase_number=phase_id,
                phase_name=phase_name,
                output_type=output_type,
                file_path=file_path,
                content=content,
                model_used=model_used,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                duration_seconds=duration_seconds,
                status=status,
                error_message=error_message,
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
            )
            session.add(row)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── Async variants (used by background-task services) ───────────────────
    # These mirror the sync methods above but use AsyncSession so they don't
    # block the FastAPI event loop during long pipeline runs.

    async def async_set_phase_status(
        self,
        project_id: int,
        phase_id: str,
        status: str,
        extra: Optional[dict] = None,
        reset_downstream: bool = False,
    ) -> dict:
        """Async version of set_phase_status — safe to call from background tasks."""
        factory = get_async_session_factory()
        async with factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(ProjectDB).where(ProjectDB.id == project_id)
                )
                p = result.scalar_one_or_none()
                if not p:
                    raise ValueError(f"Project {project_id} not found")

                statuses = dict(p.phase_statuses or {})
                was_already_complete = statuses.get(phase_id, {}).get("status") == "completed"
                entry: dict = {"status": status, "updated_at": datetime.utcnow().isoformat()}
                if extra:
                    entry.update(extra)
                # Mirror the sync variant: stamp the current requirements_hash
                # onto every completion so the stale-phase detector can tell
                # whether this output was generated against the currently-locked
                # requirements or an older version.
                if status == "completed":
                    current_hash = getattr(p, "requirements_hash", None)
                    if current_hash:
                        entry["requirements_hash_at_completion"] = current_hash
                statuses[phase_id] = entry

                # When P1 requirements are updated (re-completed), reset all downstream
                # AI phases to pending so they pick up the fresh requirements.md.
                if (phase_id == "P1" and status == "completed"
                        and (reset_downstream or was_already_complete)):
                    ts = datetime.utcnow().isoformat()
                    for ds_phase in self._DOWNSTREAM_AI_PHASES:
                        if ds_phase in statuses:
                            statuses[ds_phase] = {"status": "pending", "updated_at": ts}
                    log.info(
                        "phase.downstream_reset (async): P1 updated — downstream set to pending",
                        extra={"project_id": project_id},
                    )

                p.phase_statuses = statuses
                flag_modified(p, "phase_statuses")  # force SQLAlchemy to detect JSON column change
                if status == "completed" and phase_id == "P1":
                    p.current_phase = "P2"

            log.info(
                "phase.status_updated (async)",
                extra={"project_id": project_id, "phase": phase_id, "status": status},
            )
            return statuses

    async def async_get(self, project_id: int) -> Optional[dict]:
        """Async version of get — reads project from DB without blocking."""
        factory = get_async_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(ProjectDB).where(ProjectDB.id == project_id)
            )
            p = result.scalar_one_or_none()
            return _project_to_dict(p) if p else None

    async def async_append_conversation(
        self,
        project_id: int,
        role: str,
        content: str,
        design_parameters: Optional[dict] = None,
    ) -> None:
        """Async version of append_conversation."""
        factory = get_async_session_factory()
        async with factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(ProjectDB).where(ProjectDB.id == project_id)
                )
                p = result.scalar_one_or_none()
                if not p:
                    raise ValueError(f"Project {project_id} not found")
                history = list(p.conversation_history or [])
                history.append({"role": role, "content": content})
                p.conversation_history = history
                flag_modified(p, "conversation_history")  # force SQLAlchemy to detect JSON column change
                if design_parameters:
                    p.design_parameters = {**(p.design_parameters or {}), **design_parameters}
                    flag_modified(p, "design_parameters")

    async def async_record_phase_output(
        self,
        project_id: int,
        phase_id: str,
        phase_name: str,
        content: str,
        output_type: str = "markdown",
        file_path: str = "",
        model_used: str = "",
        tokens_input: int = 0,
        tokens_output: int = 0,
        duration_seconds: float = 0.0,
        status: str = "completed",
        error_message: str = "",
    ) -> None:
        """Async version of record_phase_output."""
        factory = get_async_session_factory()
        async with factory() as session:
            async with session.begin():
                row = PhaseOutputDB(
                    project_id=project_id,
                    phase_number=phase_id,
                    phase_name=phase_name,
                    output_type=output_type,
                    file_path=file_path,
                    content=content,
                    model_used=model_used,
                    tokens_input=tokens_input,
                    tokens_output=tokens_output,
                    duration_seconds=duration_seconds,
                    status=status,
                    error_message=error_message,
                    started_at=datetime.utcnow(),
                    completed_at=datetime.utcnow(),
                )
                session.add(row)

    async def async_get_phase_status(self, project_id: int, phase_id: str) -> str:
        """Async version of get_phase_status."""
        proj = await self.async_get(project_id)
        if not proj:
            return "pending"
        return proj["phase_statuses"].get(phase_id, {}).get("status", "pending")
