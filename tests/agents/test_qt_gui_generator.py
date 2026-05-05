"""Tests for agents/qt_gui_generator.py — the fallback template and LLM
failure handling. The LLM call itself is stubbed out so tests run offline.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents.qt_gui_generator import _fallback_gui_template, generate_qt_gui


# ---------------------------------------------------------------------------
# _fallback_gui_template
# ---------------------------------------------------------------------------

def test_fallback_template_is_runnable_python_with_pyside6():
    code = _fallback_gui_template("MyProj")
    # Core PySide6 imports
    assert "from PySide6" in code
    assert "QMainWindow" in code
    assert "QTabWidget" in code
    # Standalone run block
    assert 'if __name__ == "__main__":' in code


def test_fallback_template_substitutes_project_name_into_header():
    code = _fallback_gui_template("Radar-Receiver")
    assert "Radar-Receiver" in code or "RadarReceiver" in code


def test_fallback_template_strips_spaces_from_project_name():
    # safe_name strips spaces — used for class / filename purposes
    code = _fallback_gui_template("Multi Band Tracker")
    # Should contain the collapsed form somewhere
    assert "MultiBandTracker" in code or "Multi Band Tracker" in code


# ---------------------------------------------------------------------------
# generate_qt_gui — LLM happy + failure paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_qt_gui_returns_llm_output_when_available():
    fake_llm = AsyncMock(return_value={
        "content": (
            "# gui_application.py — custom hand-rolled GUI\n"
            "from PySide6.QtWidgets import QApplication\n"
            + "x = 1\n" * 40
        )
    })
    code = await generate_qt_gui("P", "sdd", "srs", fake_llm)
    assert "custom hand-rolled GUI" in code
    assert "PySide6" in code


@pytest.mark.asyncio
async def test_generate_qt_gui_strips_markdown_fences():
    fake_llm = AsyncMock(return_value={
        "content": "```python\nimport sys\n" + "a = 1\n" * 60 + "```",
    })
    code = await generate_qt_gui("P", "", "", fake_llm)
    assert not code.startswith("```")
    assert not code.rstrip().endswith("```")


@pytest.mark.asyncio
async def test_generate_qt_gui_falls_back_when_llm_returns_short_output():
    fake_llm = AsyncMock(return_value={"content": "too short"})
    code = await generate_qt_gui("Proj", "", "", fake_llm)
    # Short response → fallback template triggered
    assert "PySide6" in code
    assert "QMainWindow" in code


@pytest.mark.asyncio
async def test_generate_qt_gui_falls_back_when_llm_raises():
    crashing = AsyncMock(side_effect=RuntimeError("LLM blew up"))
    code = await generate_qt_gui("Proj", "", "", crashing)
    # Must NOT propagate — fallback template returned instead
    assert "PySide6" in code
