"""
ChatService — handles Phase 1 conversational requirements capture.

Responsible for:
- Routing user messages to RequirementsAgent
- Persisting conversation history to DB (via ProjectService, async methods)
- Resolving phase_complete signals and updating phase status in DB
- Writing output files through StorageAdapter

app.py never calls the agent directly — it calls this service.
All DB writes use async session methods so the FastAPI event loop is not blocked.
"""

from __future__ import annotations

import logging
from typing import Optional

from config import settings
from services.project_service import ProjectService
from services.storage import StorageAdapter

log = logging.getLogger(__name__)


class ChatService:
    """Orchestrates Phase 1 chat: user → agent → DB → UI response."""

    def __init__(
        self,
        project_service: Optional[ProjectService] = None,
        storage: Optional[StorageAdapter] = None,
    ):
        self._proj_svc = project_service or ProjectService()
        self._storage = storage or StorageAdapter.local(settings.output_dir)

    async def send_message(self, project_id: int, user_message: str) -> dict:
        """
        Send a message to the Phase 1 agent and persist everything to DB.

        Returns:
            {
                "response": str,           — text to display in chat
                "draft_pending": bool,     — show Approve/Change buttons
                "phase_complete": bool,    — Phase 1 is done
                "outputs": {filename: content},
            }
        """
        proj = await self._proj_svc.async_get(project_id)
        if not proj:
            raise ValueError(f"Project {project_id} not found")

        # Persist user message first (so it's saved even if agent crashes)
        # Uses async session — does not block the event loop
        await self._proj_svc.async_append_conversation(project_id, "user", user_message)

        # Build context with full conversation history (re-fetch to include new message)
        proj = await self._proj_svc.async_get(project_id)
        history = proj.get("conversation_history", [])

        # Detect whether P1 was already completed (enables refinement mode in agent)
        phase_statuses = proj.get("phase_statuses", {})
        # phase_statuses values are dicts: {"status": "completed", "updated_at": "..."}
        _p1_val = phase_statuses.get("P1", {})
        p1_complete = (
            (_p1_val.get("status") if isinstance(_p1_val, dict) else _p1_val)
            == "completed"
        )

        # Call the agent
        from agents.requirements_agent import RequirementsAgent
        agent = RequirementsAgent()

        try:
            result = await agent.execute(
                project_context={
                    "project_id": project_id,
                    "name": proj["name"],
                    "design_type": proj["design_type"],
                    "project_type": proj.get("project_type", "receiver"),
                    "design_scope": proj.get("design_scope", "full"),
                    "design_parameters": proj.get("design_parameters") or {},
                    "description": proj.get("description", ""),
                    "conversation_history": history,
                    "output_dir": proj["output_dir"],
                    "p1_complete": p1_complete,
                },
                user_input=user_message,
            )
        except Exception:
            log.exception("chat.agent_failed", extra={"project_id": project_id})
            raise

        response_text = result.get("response", "")
        phase_complete = result.get("phase_complete", False)
        draft_pending = result.get("draft_pending", False)
        outputs = result.get("outputs", {})

        # Persist assistant reply (async)
        await self._proj_svc.async_append_conversation(
            project_id=project_id,
            role="assistant",
            content=response_text,
            design_parameters=result.get("parameters"),
        )

        # Write output files through StorageAdapter (sync file I/O — acceptable)
        if outputs:
            self._storage.write_outputs(proj["name"], outputs)

        # A1.2 — persist the frozen requirements lock BEFORE stamping P1
        # completed, so the phase-status entry we're about to write can pick
        # up `requirements_hash_at_completion` from the newly-saved row.
        lock_row = result.get("lock_row")
        if lock_row:
            try:
                self._proj_svc.save_requirements_lock(project_id, lock_row)
            except Exception:
                log.exception(
                    "chat.requirements_lock_save_failed",
                    extra={"project_id": project_id},
                )

        # Update phase status atomically in DB (async)
        if phase_complete:
            # Pass reset_downstream=True when P1 was already complete — this means the user
            # added follow-up requirements, so all downstream phases are now stale and must
            # be re-run to incorporate the updated requirements.md.
            await self._proj_svc.async_set_phase_status(
                project_id, "P1", "completed",
                reset_downstream=p1_complete,   # p1_complete = was already done before this call
            )
            if p1_complete:
                log.info(
                    "chat.phase1_requirements_updated — downstream phases reset to pending",
                    extra={"project_id": project_id},
                )
            else:
                log.info("chat.phase1_complete", extra={"project_id": project_id})
        elif draft_pending:
            await self._proj_svc.async_set_phase_status(project_id, "P1", "draft_pending")
            log.info("chat.draft_pending", extra={"project_id": project_id})

        # ── Diagnostic log — let us confirm (turn by turn) whether the agent
        # emitted structured clarification_cards. If this logs `cards=0` while
        # the user sees a clarification-shaped prose response, the bug is in
        # the agent (tool_choice / filter). If it logs `cards=N>0` but the UI
        # still doesn't render chips, the bug is client-side (bundle cache).
        _cards = result.get("clarification_cards") or None
        _qcount = len((_cards or {}).get("questions") or []) if _cards else 0
        log.info(
            "chat.turn_result project_id=%s phase_complete=%s draft_pending=%s cards=%s intro=%r",
            project_id, phase_complete, draft_pending, _qcount,
            ((_cards or {}).get("intro") or "")[:80],
        )

        final_response = {
            "response": response_text,
            "draft_pending": draft_pending,
            "phase_complete": phase_complete,
            "outputs": {k: str(v) for k, v in outputs.items()},
            "model_used": result.get("model_used", ""),
            # Structured card JSON (present when the agent called
            # show_clarification_cards this turn). Frontend renders these
            # directly as clickable chip cards — the prose parser is a fallback.
            "clarification_cards": _cards,
        }
        # Dump the EXACT JSON payload we're about to send back — lets us
        # inspect whether clarification_cards made it through.
        try:
            import json as _json
            _preview = _json.dumps(final_response, default=str)[:1200]
            log.info("chat.payload project_id=%s keys=%s preview=%s",
                     project_id, list(final_response.keys()), _preview)
        except Exception:
            log.exception("chat.payload_dump_failed")
        return final_response
