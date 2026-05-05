"""P0.3 regression — `/chat` wraps send_message in asyncio.wait_for(180s).

We use a very short deadline in these tests so we don't have to sleep
through the real one.
"""
from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_short_timeout(tmp_path: Path, monkeypatch):
    """Reload `main` with _CHAT_DEADLINE_S forced to a short value so the
    timeout path fires deterministically."""
    db_path = tmp_path / "timeout.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

    import config as _config
    importlib.reload(_config)
    import database.models as _models
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
    import services.chat_service as _cs
    importlib.reload(_cs)
    import main as _main
    importlib.reload(_main)
    # Force a short deadline so the test doesn't wait 180 s.
    _main._CHAT_DEADLINE_S = 0.5

    from database.models import get_engine
    get_engine()

    with TestClient(_main.app) as c:
        yield c

    try:
        if _models._async_engine is not None:
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


async def _hang_forever(*_a, **_k):
    """Stand-in for ChatService.send_message that never returns."""
    await asyncio.sleep(10.0)
    return {}


def test_chat_returns_504_on_deadline(client_with_short_timeout):
    """If the chat service stalls past _CHAT_DEADLINE_S, the route
    must close the request with 504, not hang forever."""
    c = client_with_short_timeout
    pid = c.post("/api/v1/projects", json={"name": "Slow"}).json()["id"]
    with patch(
        "services.chat_service.ChatService.send_message",
        new=AsyncMock(side_effect=_hang_forever),
    ):
        r = c.post(f"/api/v1/projects/{pid}/chat", json={"message": "hi"})
    assert r.status_code == 504
    assert "deadline" in r.json()["detail"].lower()


def test_chat_still_returns_404_on_missing_project(client_with_short_timeout):
    """Deadline logic must not swallow the existing 404 path."""
    c = client_with_short_timeout
    r = c.post("/api/v1/projects/999999/chat", json={"message": "hi"})
    assert r.status_code == 404


def test_chat_still_returns_400_on_empty_message(client_with_short_timeout):
    c = client_with_short_timeout
    pid = c.post("/api/v1/projects", json={"name": "Empty"}).json()["id"]
    r = c.post(f"/api/v1/projects/{pid}/chat", json={"message": "   "})
    assert r.status_code == 400


def test_chat_happy_path_under_deadline(client_with_short_timeout):
    """A fast response must still pass through unchanged."""
    c = client_with_short_timeout
    pid = c.post("/api/v1/projects", json={"name": "Fast"}).json()["id"]
    async def _quick(*_a, **_k):
        return {"response": "ok", "phase_complete": False,
                "draft_pending": False, "outputs": {}}
    with patch(
        "services.chat_service.ChatService.send_message",
        new=AsyncMock(side_effect=_quick),
    ):
        r = c.post(f"/api/v1/projects/{pid}/chat", json={"message": "hi"})
    assert r.status_code == 200
    assert r.json()["response"] == "ok"
