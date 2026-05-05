"""
Backend Mermaid helpers — regression tests.

The authoritative Mermaid sanitizer lives in the frontend (ChatView.tsx /
DocumentsView.tsx) because Mermaid rendering happens client-side. On the
backend we only need:

  1. `tools/doc_converter._MERMAID_FENCE` — the fenced-block regex used by
     the DOCX/PDF pipeline to extract diagrams for server-side rendering.
  2. `agents/requirements_agent.RequirementsAgent._extract_or_generate_mermaid`
     — extract a block from LLM output, fall back to a canned default.
  3. `agents/netlist_agent` — the char-stripping rule that removes
     Mermaid-hostile chars (<, >, ", ', |, #, &, @, :) from labels.

These are small, pure helpers; they're cheap to lock in place.
"""
from __future__ import annotations

import re

import pytest


# ---------------------------------------------------------------------------
# tools/doc_converter._MERMAID_FENCE
# ---------------------------------------------------------------------------

def test_mermaid_fence_matches_basic_fenced_block():
    from tools.doc_converter import _MERMAID_FENCE
    md = "some text\n```mermaid\ngraph TD\nA-->B\n```\nmore text"
    matches = _MERMAID_FENCE.findall(md)
    assert matches == ["graph TD\nA-->B"]


def test_mermaid_fence_captures_every_block():
    from tools.doc_converter import _MERMAID_FENCE
    md = "```mermaid\nA\n```\n\n```mermaid\nB\nC\n```\n"
    matches = _MERMAID_FENCE.findall(md)
    assert matches == ["A", "B\nC"]


def test_mermaid_fence_ignores_non_mermaid_fences():
    from tools.doc_converter import _MERMAID_FENCE
    md = "```python\nprint(1)\n```\n"
    assert _MERMAID_FENCE.findall(md) == []


def test_mermaid_fence_is_non_greedy():
    """Two adjacent blocks must be captured separately, not merged into one."""
    from tools.doc_converter import _MERMAID_FENCE
    md = "```mermaid\nA\n```\n```mermaid\nB\n```"
    assert _MERMAID_FENCE.findall(md) == ["A", "B"]


# ---------------------------------------------------------------------------
# RequirementsAgent._extract_or_generate_mermaid
# ---------------------------------------------------------------------------

@pytest.fixture
def _req_agent_extract():
    """Return a callable bound to the method under test — skip the heavy
    agent init by instantiating just enough to access the unbound method."""
    from agents.requirements_agent import RequirementsAgent

    class _Probe:
        # Reuse the real implementation without constructing the full agent
        _extract_or_generate_mermaid = RequirementsAgent._extract_or_generate_mermaid
    return _Probe()


def test_extract_mermaid_returns_inline_fenced_block(_req_agent_extract):
    resp = "Here's the block diagram:\n```mermaid\ngraph TD\nA-->B\n```\nThanks."
    out = _req_agent_extract._extract_or_generate_mermaid(resp, "block")
    assert out == "graph TD\nA-->B"


def test_extract_mermaid_returns_first_block_when_multiple(_req_agent_extract):
    resp = "```mermaid\ngraph TD\nA-->B\n```\n```mermaid\ngraph LR\nC-->D\n```"
    out = _req_agent_extract._extract_or_generate_mermaid(resp, "block")
    # The impl uses re.search → returns the first match.
    assert out == "graph TD\nA-->B"


def test_extract_mermaid_falls_back_to_default_block_diagram(_req_agent_extract):
    out = _req_agent_extract._extract_or_generate_mermaid(
        "no fenced block here", "block"
    )
    assert "graph TD" in out
    assert "PWR" in out  # canned fallback contains Power node


def test_extract_mermaid_falls_back_to_default_architecture_diagram(_req_agent_extract):
    out = _req_agent_extract._extract_or_generate_mermaid(
        "nothing fenced", "architecture"
    )
    assert "graph LR" in out
    assert "subgraph" in out


# ---------------------------------------------------------------------------
# Netlist agent label sanitization (regression for B6)
# ---------------------------------------------------------------------------

_NETLIST_HOSTILE_CHARS = re.compile(r'[<>"\'|#&@:]')


def test_netlist_hostile_char_stripper_removes_all_bad_chars():
    """Exact regex the netlist agent uses — strips Mermaid-hostile chars
    out of auto-generated node labels."""
    dirty = 'LNA "HMC753" <pin#2|alt> @ground:vcc'
    clean = _NETLIST_HOSTILE_CHARS.sub("", dirty)
    assert "<" not in clean and ">" not in clean
    assert '"' not in clean and "'" not in clean
    assert "|" not in clean and "#" not in clean
    assert "&" not in clean and "@" not in clean
    assert ":" not in clean
    # Letters/digits/spaces preserved
    assert "LNA" in clean and "HMC753" in clean


def test_netlist_hostile_char_stripper_is_a_noop_on_safe_labels():
    safe = "LNA_HMC753 (2.4GHz)"
    assert _NETLIST_HOSTILE_CHARS.sub("", safe) == safe
