"""
FastAPI route-level tests — exercise the HTTP surface with TestClient.

Focus:
- POST /api/v1/projects                 → creates project with defaults
- PATCH /api/v1/projects/{id}/design-scope
- POST /api/v1/projects/{id}/phases/{id}/execute
    - 400 for invalid phase
    - 404 for missing project
    - Happy path — schedules background task and returns phase_started
- GET /api/v1/projects/{id}/status      → includes design_scope & applicable_phase_ids
- POST /api/v1/projects/{id}/chat       → 400 on empty message

All tests use an in-process FastAPI app + TestClient, a per-test SQLite DB,
and patch out the long-running agent/pipeline background tasks so the tests
are fast and deterministic.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "api.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

    # Reload everything so the new env vars win.
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
    import services.chat_service as _cs
    importlib.reload(_cs)
    import main as _main
    importlib.reload(_main)

    # Force the engine + tables to exist before any request
    from database.models import get_engine
    get_engine()

    with TestClient(_main.app) as c:
        yield c

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


# ---------------------------------------------------------------------------
# POST /api/v1/projects
# ---------------------------------------------------------------------------

def test_create_project_with_minimum_body(client):
    r = client.post("/api/v1/projects", json={"name": "ApiTest"})
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["name"] == "ApiTest"
    assert data["design_scope"] == "full"
    assert data["current_phase"] == "P1"


def test_create_project_rejects_missing_name(client):
    r = client.post("/api/v1/projects", json={})
    assert r.status_code == 400
    assert "name is required" in r.json()["detail"]


def test_create_project_rejects_invalid_design_scope(client):
    r = client.post(
        "/api/v1/projects",
        json={"name": "BadScope", "design_scope": "nonsense"},
    )
    assert r.status_code == 400
    assert "Invalid design_scope" in r.json()["detail"]


def test_create_project_ignores_product_id_if_sent(client):
    """Backend has no product_id column — extra keys are silently dropped."""
    r = client.post(
        "/api/v1/projects",
        json={"name": "ExtraKeys", "product_id": "ignored"},
    )
    assert r.status_code == 201
    assert "product_id" not in r.json()


def test_create_project_defaults_to_receiver(client):
    r = client.post("/api/v1/projects", json={"name": "DefaultType"})
    assert r.status_code == 201
    assert r.json()["project_type"] == "receiver"


def test_create_project_accepts_transmitter(client):
    r = client.post(
        "/api/v1/projects",
        json={"name": "GaNPaChain", "project_type": "transmitter"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["project_type"] == "transmitter"


def test_create_project_rejects_invalid_project_type(client):
    r = client.post(
        "/api/v1/projects",
        json={"name": "BadType", "project_type": "banana"},
    )
    assert r.status_code == 400
    assert "project_type" in r.json()["detail"]


def test_get_project_surfaces_project_type(client):
    created = client.post(
        "/api/v1/projects",
        json={"name": "RoundTrip", "project_type": "transmitter"},
    ).json()
    r = client.get(f"/api/v1/projects/{created['id']}")
    assert r.status_code == 200
    assert r.json()["project_type"] == "transmitter"


# ---------------------------------------------------------------------------
# PATCH /api/v1/projects/{id}/design-scope
# ---------------------------------------------------------------------------

def test_patch_design_scope_persists(client):
    pid = client.post("/api/v1/projects", json={"name": "Patch"}).json()["id"]
    r = client.patch(
        f"/api/v1/projects/{pid}/design-scope",
        json={"design_scope": "front-end"},
    )
    assert r.status_code == 200
    assert r.json()["design_scope"] == "front-end"


def test_patch_design_scope_rejects_invalid(client):
    pid = client.post("/api/v1/projects", json={"name": "Patch2"}).json()["id"]
    r = client.patch(
        f"/api/v1/projects/{pid}/design-scope",
        json={"design_scope": "garbage"},
    )
    assert r.status_code == 400


def test_patch_design_scope_404_for_missing_project(client):
    r = client.patch(
        "/api/v1/projects/999999/design-scope",
        json={"design_scope": "full"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/projects/{id}/phases/{id}/execute
# ---------------------------------------------------------------------------

def test_execute_phase_rejects_unknown_phase_id(client):
    pid = client.post("/api/v1/projects", json={"name": "Exec"}).json()["id"]
    r = client.post(f"/api/v1/projects/{pid}/phases/P99/execute")
    assert r.status_code == 400
    assert "Invalid phase" in r.json()["detail"]


def test_execute_phase_404_for_missing_project(client):
    r = client.post("/api/v1/projects/999999/phases/P2/execute")
    assert r.status_code == 404


def test_execute_phase_schedules_background_task(client):
    pid = client.post("/api/v1/projects", json={"name": "ExecBG"}).json()["id"]
    # Patch the pipeline service's single-phase runner to an AsyncMock so the
    # route returns immediately without invoking a real agent.
    with patch(
        "services.pipeline_service.PipelineService.run_single_phase",
        new=AsyncMock(return_value={}),
    ):
        r = client.post(f"/api/v1/projects/{pid}/phases/P2/execute")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "phase_started"
    assert body["phase_id"] == "P2"


# ---------------------------------------------------------------------------
# v23 scope policy — 409 gate code exists but is never hit under the current
# PHASE_APPLICABLE_SCOPES table. Lock down both facts.
# ---------------------------------------------------------------------------

def test_execute_phase_never_returns_409_under_v23_policy(client):
    """Every phase is applicable to every scope in v23 → no scope triggers 409."""
    pid = client.post(
        "/api/v1/projects",
        json={"name": "NoGate", "design_scope": "front-end"},
    ).json()["id"]

    with patch(
        "services.pipeline_service.PipelineService.run_single_phase",
        new=AsyncMock(return_value={}),
    ):
        for phase in ["P1", "P2", "P6", "P7a", "P8c"]:
            r = client.post(f"/api/v1/projects/{pid}/phases/{phase}/execute")
            assert r.status_code != 409, (
                f"{phase} returned 409 under front-end scope — "
                "v23 policy is meant to be advisory only"
            )


# ---------------------------------------------------------------------------
# GET /api/v1/projects/{id}/status
# ---------------------------------------------------------------------------

def test_status_returns_design_scope_and_applicable_phase_ids(client):
    pid = client.post(
        "/api/v1/projects",
        json={"name": "Status", "design_scope": "dsp"},
    ).json()["id"]

    r = client.get(f"/api/v1/projects/{pid}/status")
    assert r.status_code == 200
    body = r.json()

    assert body["design_scope"] == "dsp"
    assert "applicable_phase_ids" in body
    # v23: every phase applies, so the list is the full set, sorted.
    assert set(body["applicable_phase_ids"]) == {
        "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P7a", "P8a", "P8b", "P8c",
    }
    assert body["phase_statuses"] == {}
    assert body["stale_phase_ids"] == []


def test_status_404_for_missing_project(client):
    assert client.get("/api/v1/projects/999999/status").status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/projects/{id}/chat
# ---------------------------------------------------------------------------

def test_chat_rejects_empty_message(client):
    pid = client.post("/api/v1/projects", json={"name": "Chat"}).json()["id"]
    r = client.post(f"/api/v1/projects/{pid}/chat", json={"message": "   "})
    assert r.status_code == 400


def test_chat_404_for_missing_project(client):
    r = client.post("/api/v1/projects/999999/chat", json={"message": "hi"})
    assert r.status_code == 404


def test_chat_returns_agent_response(client):
    pid = client.post("/api/v1/projects", json={"name": "ChatOK"}).json()["id"]

    stub_agent = MagicMock()
    stub_agent.execute = AsyncMock(return_value={
        "response": "How about 2.4 GHz?",
        "phase_complete": False,
        "draft_pending": False,
        "outputs": {},
    })
    with patch(
        "agents.requirements_agent.RequirementsAgent",
        return_value=stub_agent,
    ):
        r = client.post(
            f"/api/v1/projects/{pid}/chat",
            json={"message": "Design an EW receiver"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["response"] == "How about 2.4 GHz?"
    assert r.json()["phase_complete"] is False


# ---------------------------------------------------------------------------
# GET /api/v1/projects/{id}/export — ZIP download
# ---------------------------------------------------------------------------

class TestExportProjectZip:

    def _make_project_with_outputs(self, client, tmp_path: Path):
        """Create a project, then drop two dummy output files in its dir."""
        p = client.post("/api/v1/projects", json={"name": "ExportTest"}).json()
        output_dir = Path(p["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        # write_bytes to preserve "\n" exactly — on Windows, write_text() in
        # text mode translates "\n" to "\r\n" which breaks the byte-level
        # assertion below.
        (output_dir / "requirements.md").write_bytes(b"# Requirements\nhello")
        (output_dir / "bom.json").write_bytes(b'{"parts": []}')
        return p

    def test_export_returns_zip_when_outputs_exist(self, client, tmp_path):
        p = self._make_project_with_outputs(client, tmp_path)
        r = client.get(f"/api/v1/projects/{p['id']}/export")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert "attachment" in r.headers["content-disposition"]
        assert "ExportTest_documents.zip" in r.headers["content-disposition"]

        # Verify the ZIP is well-formed and carries our files
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = set(zf.namelist())
            assert "requirements.md" in names
            assert "bom.json" in names
            assert zf.read("requirements.md").decode() == "# Requirements\nhello"

    def test_export_returns_404_for_missing_project(self, client):
        r = client.get("/api/v1/projects/999999/export")
        assert r.status_code == 404

    def test_export_returns_404_for_empty_output_dir(self, client, tmp_path):
        """Project created but no files written yet — must 404, not empty zip."""
        p = client.post("/api/v1/projects", json={"name": "EmptyExport"}).json()
        Path(p["output_dir"]).mkdir(parents=True, exist_ok=True)
        r = client.get(f"/api/v1/projects/{p['id']}/export")
        assert r.status_code == 404
        assert "No documents" in r.json()["detail"]

    def test_export_filename_sanitises_unsafe_chars(self, client, tmp_path):
        """Slashes / colons / spaces in the project name must not break the
        Content-Disposition header."""
        p = client.post(
            "/api/v1/projects",
            json={"name": "Weird/Name: with spaces"},
        ).json()
        Path(p["output_dir"]).mkdir(parents=True, exist_ok=True)
        (Path(p["output_dir"]) / "f.md").write_text("x", encoding="utf-8")
        r = client.get(f"/api/v1/projects/{p['id']}/export")
        assert r.status_code == 200
        # No raw slashes / colons / spaces in the filename
        disp = r.headers["content-disposition"]
        assert "/" not in disp.split('filename="')[1].rstrip('"')
        assert ":" not in disp.split('filename="')[1].rstrip('"')
        assert " " not in disp.split('filename="')[1].rstrip('"')


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health_endpoint_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"
