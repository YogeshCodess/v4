"""
Pytest configuration and shared fixtures for Silicon to Software (S2S) tests.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import Response

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Add project-local `bin/` to PATH so `shutil.which("pandoc")` finds the
# bundled pandoc.exe (Windows dev install per .gitignore). Without this,
# tests that skip on `pandoc not installed` are skipped on dev boxes that
# DO have pandoc bundled in the repo. Idempotent — safe to run multiple
# times.
_BIN_DIR = Path(__file__).parent.parent / "bin"
if _BIN_DIR.exists():
    _path_sep = os.pathsep
    _current_path = os.environ.get("PATH", "")
    if str(_BIN_DIR) not in _current_path.split(_path_sep):
        os.environ["PATH"] = str(_BIN_DIR) + _path_sep + _current_path


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_env_vars(temp_dir: Path) -> Generator[None, None, None]:
    """Set mock environment variables for testing."""
    original_env = os.environ.copy()

    os.environ.update({
        "ANTHROPIC_API_KEY": "sk-ant-test-key",
        "OPENAI_API_KEY": "sk-openai-test-key",
        "GLM_API_KEY": "test-glm-key",
        "PRIMARY_MODEL": "claude-opus-4-6",
        "FAST_MODEL": "claude-haiku-4-5-20251001",
        "FALLBACK_MODEL": "ollama/qwen2.5-coder:32b",
        "LAST_RESORT_MODEL": "glm-4",
        "DATABASE_URL": f"sqlite:///{temp_dir}/test.db",
        "CHROMA_PERSIST_DIR": str(temp_dir / "chroma"),
        "DEBUG": "true",
        "LOG_LEVEL": "DEBUG",
    })

    yield

    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def mock_anthropic_response() -> MagicMock:
    """Create a mock Anthropic API response."""
    mock = MagicMock()
    mock.content = [
        MagicMock(type="text", text="Test response content"),
    ]
    mock.stop_reason = "end_turn"
    mock.usage = MagicMock(
        input_tokens=10,
        output_tokens=20,
    )
    mock.model = "claude-opus-4-6"
    return mock


@pytest.fixture
def mock_anthropic_client() -> AsyncMock:
    """Create a mock Anthropic client."""
    client = AsyncMock()
    client.messages = MagicMock()
    client.messages.create = MagicMock(return_value=mock_anthropic_response())
    return client


@pytest.fixture
def mock_httpx_response() -> MagicMock:
    """Create a mock httpx response."""
    mock = MagicMock(spec=Response)
    mock.status_code = 200
    mock.json.return_value = {
        "message": {"content": "Ollama response"},
        "prompt_eval_count": 5,
        "eval_count": 10,
    }
    mock.raise_for_status = MagicMock()
    return mock


@pytest.fixture
async def mock_httpx_client() -> AsyncMock:
    """Create a mock httpx.AsyncClient."""
    client = AsyncMock()
    client.post = MagicMock(return_value=mock_httpx_response())
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock()
    return client


@pytest.fixture
def sample_project_context() -> dict:
    """Sample project context for testing agents."""
    return {
        "project_id": "test-project-123",
        "project_name": "Test Hardware Project",
        "user_input": "Design a simple LED blinker circuit",
        "requirements": {
            "voltage": "3.3V",
            "current": "20mA",
            "components": ["LED", "Resistor", "MCU"],
        },
        "phase_outputs": {},
    }


@pytest.fixture
def sample_tool_definition() -> dict:
    """Sample Claude tool definition for testing."""
    return {
        "name": "search_components",
        "description": "Search for electronic components",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Component search query",
                },
                "category": {
                    "type": "string",
                    "enum": ["resistors", "capacitors", "ics", "leds"],
                    "description": "Component category",
                },
            },
            "required": ["query"],
        },
    }


# API Key skip decorators
def skip_if_no_api_key(env_var: str = "ANTHROPIC_API_KEY"):
    """Skip test if API key is not set."""
    return pytest.mark.skipif(
        not os.environ.get(env_var),
        reason=f"Requires {env_var} to be set"
    )


def skip_if_airgapped():
    """Skip test if running in air-gapped mode (no API keys)."""
    return pytest.mark.skipif(
        not any(
            os.environ.get(k)
            for k in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GLM_API_KEY"]
        ),
        reason="Requires at least one API key (not air-gapped mode)"
    )
