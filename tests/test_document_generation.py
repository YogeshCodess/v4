"""
Tests for tools/doc_converter.py — markdown → docx/pdf/html conversion.

Focus on the pure pre-processing helpers (Mermaid fence rewriting, pandoc-
availability gate). The `convert()` happy path is only exercised when pandoc
is actually installed on the test runner; otherwise it must return None
instead of raising.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.doc_converter import (
    DocConverter,
    _check_pandoc,
    _preprocess_mermaid,
    _MERMAID_FENCE,
)


# ---------------------------------------------------------------------------
# _MERMAID_FENCE regex
# ---------------------------------------------------------------------------

def test_mermaid_fence_captures_basic_block():
    md = "text\n```mermaid\ngraph TD\nA-->B\n```\ntrailing"
    matches = _MERMAID_FENCE.findall(md)
    assert matches == ["graph TD\nA-->B"]


def test_mermaid_fence_captures_multiple_blocks():
    md = "```mermaid\nA\n```\n```mermaid\nB\n```"
    assert _MERMAID_FENCE.findall(md) == ["A", "B"]


def test_mermaid_fence_ignores_non_mermaid_code():
    md = "```python\nprint(1)\n```"
    assert _MERMAID_FENCE.findall(md) == []


# ---------------------------------------------------------------------------
# _preprocess_mermaid — fallback path (no network)
# ---------------------------------------------------------------------------

def test_preprocess_mermaid_falls_back_to_code_block_when_png_fails(tmp_path: Path):
    """With mermaid.ink mocked to fail, each block must turn into a labelled
    code block (no raw ```mermaid left behind)."""
    md = "```mermaid\ngraph TD\nA-->B\n```"
    with patch("tools.doc_converter._mermaid_to_png", return_value=None):
        out = _preprocess_mermaid(md, tmp_path)
    assert "```mermaid" not in out
    assert "graph TD" in out
    assert "System Architecture Diagram 1" in out


def test_preprocess_mermaid_embeds_png_reference_when_render_succeeds(tmp_path: Path):
    """When the PNG render succeeds, an image reference (![...]()) is injected."""
    fake_png = tmp_path / "fake.png"
    fake_png.write_bytes(b"\x89PNG\r\n" + b"0" * 200)

    with patch("tools.doc_converter._mermaid_to_png", return_value=fake_png):
        out = _preprocess_mermaid("```mermaid\nflowchart TD\nA-->B\n```", tmp_path)
    assert "![Diagram 1]" in out
    assert str(fake_png) in out


def test_preprocess_mermaid_increments_index_per_block(tmp_path: Path):
    md = "```mermaid\nA\n```\n```mermaid\nB\n```"
    with patch("tools.doc_converter._mermaid_to_png", return_value=None):
        out = _preprocess_mermaid(md, tmp_path)
    assert "Diagram 1" in out
    assert "Diagram 2" in out


def test_preprocess_mermaid_noop_for_markdown_without_mermaid():
    md = "# Title\n\nJust regular prose with `code` inline."
    out = _preprocess_mermaid(md, Path("/tmp"))
    assert out == md


# ---------------------------------------------------------------------------
# _check_pandoc — gate
# ---------------------------------------------------------------------------

def test_check_pandoc_true_when_binary_on_path():
    """If pandoc is installed on the test host, the gate returns True.
    If not, it returns False. Either way the function must not raise."""
    import tools.doc_converter as m
    m._PANDOC_AVAILABLE = None  # force re-check
    result = _check_pandoc()
    assert isinstance(result, bool)
    # Consistent with shutil.which on this host
    assert result == (shutil.which("pandoc") is not None)


# ---------------------------------------------------------------------------
# DocConverter.convert() — no pandoc → graceful None
# ---------------------------------------------------------------------------

def test_convert_returns_none_when_pandoc_unavailable(tmp_path: Path):
    conv = DocConverter()
    with patch("tools.doc_converter._check_pandoc", return_value=False):
        result = conv.convert("# Hello\n", "test", tmp_path, fmt="docx")
    assert result is None


def test_to_docx_and_to_pdf_delegate_to_convert(tmp_path: Path):
    """Ensure the convenience methods hit convert() with the right fmt."""
    conv = DocConverter()
    with patch.object(conv, "convert") as m_convert:
        m_convert.return_value = tmp_path / "x.docx"
        conv.to_docx("md", "x", tmp_path)
        assert m_convert.call_args.args[3] == "docx"

        m_convert.reset_mock()
        m_convert.return_value = tmp_path / "x.pdf"
        conv.to_pdf("md", "x", tmp_path)
        assert m_convert.call_args.args[3] == "pdf"


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
def test_convert_writes_docx_when_pandoc_available(tmp_path: Path):
    """Integration test — only runs when pandoc is on PATH."""
    conv = DocConverter()
    path = conv.convert("# Hello\n\nThis is a test doc.\n", "hello", tmp_path, fmt="docx")
    assert path is not None
    assert path.exists()
    assert path.suffix == ".docx"
    # ZIP-based format starts with PK\x03\x04
    assert path.read_bytes()[:4] == b"PK\x03\x04"
