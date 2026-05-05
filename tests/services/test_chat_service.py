"""
Tests for services/chat_service.py — Phase-1 conversational loop.

The agent itself is heavy (calls an LLM), so every test patches
`agents.requirements_agent.RequirementsAgent` with a stub that returns
deterministic results. We then assert on the persisted side-effects:
conversation history rows, phase status transitions, requirements-lock
persistence, and the returned payload shape.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared DB / service fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def chat_env(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "chat.db"
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
    import services.chat_service as _cs
    importlib.reload(_cs)

    from services.project_service import ProjectService
    from services.chat_service import ChatService
    # Force sync-engine init so Base.metadata.create_all runs before any
    # async_* operation touches the DB.
    from database.models import get_engine as _force_init
    _force_init()
    proj_svc = ProjectService()
    chat_svc = ChatService(project_service=proj_svc)

    yield proj_svc, chat_svc

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


def _stub_agent(result: dict):
    """Return a MagicMock that behaves like a RequirementsAgent instance
    whose .execute(...) returns the given dict."""
    agent = MagicMock()
    agent.execute = AsyncMock(return_value=result)
    return agent


# ---------------------------------------------------------------------------
# Happy path: user message → agent → persisted history + response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_message_appends_user_and_assistant_to_history(chat_env):
    proj_svc, chat_svc = chat_env
    proj = proj_svc.create(name="ChatBasic")

    agent_result = {
        "response": "What frequency range?",
        "phase_complete": False,
        "draft_pending": False,
        "outputs": {},
        "parameters": None,
    }
    with patch(
        "agents.requirements_agent.RequirementsAgent",
        return_value=_stub_agent(agent_result),
    ):
        resp = await chat_svc.send_message(proj["id"], "Hello, I want an EW receiver")

    # Response shape
    assert resp["response"] == "What frequency range?"
    assert resp["phase_complete"] is False

    # History has both user + assistant messages
    history = proj_svc.get(proj["id"])["conversation_history"]
    assert [h["role"] for h in history] == ["user", "assistant"]
    assert history[0]["content"] == "Hello, I want an EW receiver"
    assert history[1]["content"] == "What frequency range?"


@pytest.mark.asyncio
async def test_send_message_persists_user_message_even_if_agent_crashes(chat_env):
    """Guarantee the user's message is never lost if the LLM call throws —
    the chat service explicitly writes it BEFORE invoking the agent."""
    proj_svc, chat_svc = chat_env
    proj = proj_svc.create(name="CrashGuard")

    crashing_agent = MagicMock()
    crashing_agent.execute = AsyncMock(side_effect=RuntimeError("LLM blew up"))

    with patch(
        "agents.requirements_agent.RequirementsAgent",
        return_value=crashing_agent,
    ):
        with pytest.raises(RuntimeError):
            await chat_svc.send_message(proj["id"], "save-me-even-if-broken")

    history = proj_svc.get(proj["id"])["conversation_history"]
    assert len(history) == 1
    assert history[0] == {"role": "user", "content": "save-me-even-if-broken"}


# ---------------------------------------------------------------------------
# Phase transitions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase_complete_flips_p1_to_completed(chat_env):
    proj_svc, chat_svc = chat_env
    proj = proj_svc.create(name="Complete")

    with patch(
        "agents.requirements_agent.RequirementsAgent",
        return_value=_stub_agent({
            "response": "Requirements locked.",
            "phase_complete": True,
            "draft_pending": False,
            "outputs": {"requirements.md": "# Requirements\n"},
        }),
    ):
        resp = await chat_svc.send_message(proj["id"], "Approve")

    assert resp["phase_complete"] is True
    reloaded = proj_svc.get(proj["id"])
    assert reloaded["phase_statuses"]["P1"]["status"] == "completed"
    assert reloaded["current_phase"] == "P2"


@pytest.mark.asyncio
async def test_draft_pending_flips_p1_to_draft_pending(chat_env):
    proj_svc, chat_svc = chat_env
    proj = proj_svc.create(name="Draft")

    with patch(
        "agents.requirements_agent.RequirementsAgent",
        return_value=_stub_agent({
            "response": "Please review this draft.",
            "phase_complete": False,
            "draft_pending": True,
            "outputs": {},
        }),
    ):
        await chat_svc.send_message(proj["id"], "Make draft")

    assert (
        proj_svc.get(proj["id"])["phase_statuses"]["P1"]["status"]
        == "draft_pending"
    )


# ---------------------------------------------------------------------------
# Requirements-lock persistence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_message_persists_requirements_lock_row(chat_env):
    proj_svc, chat_svc = chat_env
    proj = proj_svc.create(name="LockPersist")

    lock_row = {
        "requirements_hash": "deadbeef",
        "requirements_frozen_at": "2026-04-20T10:00:00Z",
        "requirements_locked_json": "{\"project_id\": \"x\"}",
    }
    with patch(
        "agents.requirements_agent.RequirementsAgent",
        return_value=_stub_agent({
            "response": "Locked.",
            "phase_complete": True,
            "draft_pending": False,
            "outputs": {},
            "lock_row": lock_row,
        }),
    ):
        await chat_svc.send_message(proj["id"], "Confirm")

    reloaded = proj_svc.get(proj["id"])
    assert reloaded["requirements_hash"] == "deadbeef"
    # Completion entry should also carry the hash stamp
    entry = reloaded["phase_statuses"]["P1"]
    assert entry["status"] == "completed"
    assert entry.get("requirements_hash_at_completion") == "deadbeef"


# ---------------------------------------------------------------------------
# Clarification cards passthrough
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clarification_cards_flow_through_response(chat_env):
    proj_svc, chat_svc = chat_env
    proj = proj_svc.create(name="Cards")

    cards = {
        "intro": "Quick clarifications",
        "questions": [
            {"id": "freq", "text": "Frequency range?", "chips": ["S-band", "X-band"]},
        ],
    }
    with patch(
        "agents.requirements_agent.RequirementsAgent",
        return_value=_stub_agent({
            "response": "See chips above.",
            "phase_complete": False,
            "draft_pending": False,
            "outputs": {},
            "clarification_cards": cards,
        }),
    ):
        resp = await chat_svc.send_message(proj["id"], "Start")

    assert resp["clarification_cards"] == cards


# ---------------------------------------------------------------------------
# Unknown project → ValueError (→ 404 at the route layer)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_message_unknown_project_raises_value_error(chat_env):
    _, chat_svc = chat_env
    with pytest.raises(ValueError, match="not found"):
        await chat_svc.send_message(999999, "Hello")


# ---------------------------------------------------------------------------
# p1_complete flag toggles downstream-reset semantics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recomplete_p1_resets_downstream_phases(chat_env):
    proj_svc, chat_svc = chat_env
    proj = proj_svc.create(name="Recomplete")

    # Seed P1 completed and P2 completed (simulating an already-run pipeline).
    proj_svc.set_phase_status(proj["id"], "P1", "completed")
    proj_svc.set_phase_status(proj["id"], "P2", "completed")

    with patch(
        "agents.requirements_agent.RequirementsAgent",
        return_value=_stub_agent({
            "response": "Requirements updated.",
            "phase_complete": True,
            "draft_pending": False,
            "outputs": {},
        }),
    ):
        await chat_svc.send_message(proj["id"], "Update requirements")

    statuses = proj_svc.get(proj["id"])["phase_statuses"]
    assert statuses["P1"]["status"] == "completed"
    # Downstream phase must have been flipped back to pending.
    assert statuses["P2"]["status"] == "pending"
