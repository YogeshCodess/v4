"""Regression test: chat-draft and persistent-docs Mermaid renderers must
produce the SAME diagram.

User report (2026-04-24):
  > "in chat page why block diagram is same as in documents page block
  >  diagram? it should be same right as in documents page?"

Root cause: `RequirementsAgent._build_response_summary` (the chat-draft
renderer that builds the AI's chat-side summary message) used to read
raw `block_diagram_mermaid` straight from the LLM's tool input — no
salvage, no structured-JSON path — and never even mentioned the
architecture diagram at all. Meanwhile `_generate_output_files` (the
persistent-files writer) routed both `block_diagram` and `architecture`
through `_render_diagram_field`, which prefers the structured spec and
salvages on the raw fallback. Same payload, two different render paths
— chat broke while docs worked.

Fix (P13, 2026-04-24): the chat-draft renderer now also routes through
`_render_diagram_field` for BOTH block_diagram and architecture. These
tests guard the invariant.
"""
from __future__ import annotations

import inspect

import pytest

from agents.requirements_agent import RequirementsAgent


# ---------------------------------------------------------------------------
# Static guards: the chat renderer references _render_diagram_field for
# both block_diagram and architecture, never plucks the raw mermaid.
# ---------------------------------------------------------------------------

def test_response_summary_routes_block_diagram_through_render_diagram_field():
    """Whitebox guard: `_build_response_summary` must call
    `_render_diagram_field` with structured_key='block_diagram'."""
    src = inspect.getsource(RequirementsAgent._build_response_summary)
    assert "_render_diagram_field" in src, (
        "_build_response_summary must route the chat-draft block diagram "
        "through _render_diagram_field so it matches the persistent "
        "docs render path. If you removed the call, also revert P13."
    )
    assert 'structured_key="block_diagram"' in src or "structured_key='block_diagram'" in src


def test_response_summary_routes_architecture_through_render_diagram_field():
    """Whitebox guard: `_build_response_summary` must also render the
    architecture diagram via `_render_diagram_field`. Without this, the
    chat page only showed the block diagram and the user-visible chat
    summary diverged from the persistent `architecture.md` file."""
    src = inspect.getsource(RequirementsAgent._build_response_summary)
    assert 'structured_key="architecture"' in src or "structured_key='architecture'" in src, (
        "_build_response_summary must also render `architecture` via "
        "_render_diagram_field — otherwise the chat page omits the "
        "power-tree / architecture diagram that the docs page shows."
    )


def test_response_summary_does_not_pluck_raw_mermaid_directly():
    """The old code path was `tool_input.get("block_diagram_mermaid")`
    followed by emitting it verbatim into a ```mermaid``` fence. That's
    exactly what produced the broken chat-page diagrams. Block any
    regression that brings it back."""
    src = inspect.getsource(RequirementsAgent._build_response_summary)
    # The old shape: a direct raw read followed by an `if x:` and a fence.
    bad_pattern = 'tool_input.get("block_diagram_mermaid"'
    if bad_pattern in src:
        raise AssertionError(
            "Found direct read of `tool_input.get(\"block_diagram_mermaid\")` "
            "inside `_build_response_summary`. Chat draft must go through "
            "`_render_diagram_field` instead."
        )


# ---------------------------------------------------------------------------
# Behaviour guards: the chat renderer falls back the same way the docs
# renderer does. Use a stub subclass so we can exercise the helpers
# without the agent's full async LLM infrastructure.
# ---------------------------------------------------------------------------

class _StubAgent(RequirementsAgent):
    """Bypass __init__ — we only need the rendering helpers."""

    def __init__(self):  # type: ignore[override]
        # No super().__init__() — RequirementsAgent.__init__ wires LLM
        # clients we don't need here. Build the minimum attribute surface
        # the helpers touch.
        self._offered_candidate_mpns = set()
        self._offered_candidates_by_stage = {}

    def log(self, *_a, **_k):  # silence the salvage info-log
        pass


def test_chat_summary_uses_structured_block_diagram_when_provided():
    """When `block_diagram` (structured spec) is present, the chat draft
    goes through `render_block_diagram` (deterministic, always valid)
    instead of the raw `block_diagram_mermaid` fallback."""
    agent = _StubAgent()
    tool_input = {
        "block_diagram": {
            "direction": "LR",
            "nodes": [
                {"id": "ANT", "label": "Antenna", "shape": "flag"},
                {"id": "LNA", "label": "LNA HMC8410", "shape": "amplifier"},
            ],
            "edges": [{"from": "ANT", "to": "LNA"}],
        },
        # Raw fallback intentionally bad — must NOT be reached.
        "block_diagram_mermaid": (
            'flowchart LR\n    BUCK -- "+5 V" --> LDO\n'
        ),
    }
    md = agent._build_response_summary(tool_input)
    # Block-diagram section is present.
    assert "## System Block Diagram" in md
    # Structured path produced clean Mermaid for the two real nodes.
    assert "ANT" in md and "LNA" in md
    # The bad raw fallback must NOT have leaked through verbatim.
    assert '-- "+5 V" -->' not in md, (
        "structured path should win; raw `block_diagram_mermaid` must "
        "not appear verbatim in the chat draft"
    )


def test_chat_summary_salvages_raw_block_diagram_mermaid_when_structured_missing():
    """When only raw `block_diagram_mermaid` is provided, the chat draft
    must run it through `salvage()` — quoted edge labels become pipes,
    em-dash arrows become `-->`, etc."""
    agent = _StubAgent()
    tool_input = {
        "block_diagram_mermaid": (
            'flowchart TD\n'
            '    BUCK[Buck Conv]\n'
            '    LDO1[LDO Ch1]\n'
            '    BUCK -- "+5 V" --> LDO1\n'
        ),
    }
    md = agent._build_response_summary(tool_input)
    # The salvager fixed the quoted-edge-label syntax.
    assert "BUCK -->|+5 V| LDO1" in md, (
        f"salvager should have converted -- \"+5 V\" --> to -->|+5 V|\n"
        f"got md:\n{md}"
    )
    assert '-- "+5 V" -->' not in md


def test_chat_summary_salvages_raw_architecture_mermaid_when_structured_missing():
    """Architecture diagram (power tree) was the actual user-visible
    failure — `architecture_mermaid` had `BUCK -- "+5 V" --> LDO1` style
    edges that broke Mermaid's parser. Verify salvage rescues it."""
    agent = _StubAgent()
    tool_input = {
        "architecture_mermaid": (
            'flowchart TD\n'
            '    PWR_IN[+28 V MIL Bus Input]\n'
            '    BUCK[Buck Conv BD9F800MUX]\n'
            '    LDO1[LDO Ch1 ADM7170]\n'
            '    PWR_IN -- "+28 V" --> BUCK\n'
            '    BUCK -- "+5 V" --> LDO1\n'
        ),
    }
    md = agent._build_response_summary(tool_input)
    # Architecture section is present.
    assert "## System Architecture" in md
    # All quoted-edge labels converted to pipe form.
    assert '-- "+28 V" -->' not in md
    assert '-- "+5 V" -->' not in md
    assert "PWR_IN -->|+28 V| BUCK" in md
    assert "BUCK -->|+5 V| LDO1" in md


def test_chat_summary_omits_diagram_section_when_no_block_at_all():
    """Empty tool_input (no structured + no raw) → no `## System Block
    Diagram` section in the chat draft. Verifies `allow_empty=True` is
    honored on the chat path."""
    agent = _StubAgent()
    tool_input = {}
    md = agent._build_response_summary(tool_input)
    assert "## System Block Diagram" not in md
    assert "## System Architecture" not in md


# ---------------------------------------------------------------------------
# P19 — architecture.md fallback when LLM skips the `architecture` spec
# ---------------------------------------------------------------------------

def test_generate_output_files_uses_block_diagram_as_architecture_fallback(tmp_path):
    """User report (2026-04-24): architecture.md always shows only
    "Architecture diagram will be generated with HRS." because the LLM
    routinely skips the structured `architecture` spec on dense designs.

    Fix (P19): when `arch_mermaid` is empty but `block_mermaid` is
    present, write architecture.md reusing the block diagram content
    with a clear note. Never leaves the user with an empty file."""
    import inspect
    from agents.requirements_agent import RequirementsAgent

    # Whitebox check — the fallback branch must exist in the source.
    src = inspect.getsource(RequirementsAgent._generate_output_files)
    assert "elif block_mermaid:" in src, (
        "_generate_output_files must have an `elif block_mermaid:` "
        "branch that reuses the block diagram when architecture is "
        "empty. Without this, architecture.md ships as a bare "
        "placeholder on dense designs."
    )
    assert "Architecture view derived from the block diagram" in src, (
        "The fallback branch must include an explanatory note so the "
        "user understands the architecture.md is the block diagram, "
        "not the LLM's intended architecture spec."
    )


# ---------------------------------------------------------------------------
# P20 — BOM-derived structured block diagram (ultimate fallback)
# When raw LLM mermaid is unrecoverable AND no structured spec was
# provided, derive a linear chain from `component_recommendations` so the
# user always gets a REAL diagram with their real parts, not the generic
# FALLBACK_DIAGRAM "diagram could not be rendered" placeholder.
# ---------------------------------------------------------------------------

def test_bom_derived_fallback_kicks_in_when_no_structured_and_no_raw():
    """LLM that skips BOTH the structured `block_diagram` spec AND the
    raw `block_diagram_mermaid` field must NOT produce the generic
    FALLBACK_DIAGRAM ("diagram could not be rendered") placeholder.
    Instead, the BOM-derived path must render a structured diagram
    using the user's actual parts."""
    agent = _StubAgent()
    tool_input = {
        # NO block_diagram (structured) + NO block_diagram_mermaid (raw)
        "component_recommendations": [
            {"primary_part": "HMC8410", "function": "LNA"},
            {"primary_part": "ADL5801", "function": "Mixer"},
            {"primary_part": "AD9643", "function": "ADC"},
        ],
    }
    out = agent._render_diagram_field(
        tool_input,
        structured_key="block_diagram",
        raw_key="block_diagram_mermaid",
        default_direction="LR",
        allow_empty=False,
    )
    # Must NOT be the FALLBACK_DIAGRAM placeholder.
    assert "diagram could not be rendered" not in out
    assert "ask P1 to regenerate" not in out
    # The BOM-derived chain must include each real part's MPN in order.
    assert "HMC8410" in out
    assert "ADL5801" in out
    assert "AD9643" in out
    # It must parse as a valid flowchart.
    assert out.startswith("flowchart ")


def test_bom_derived_fallback_falls_through_when_bom_too_small():
    """With fewer than 2 components the fallback returns None, and
    `_render_diagram_field` should honour `allow_empty=True`."""
    agent = _StubAgent()
    tool_input = {
        "component_recommendations": [
            {"primary_part": "HMC8410", "function": "LNA"},
        ],  # only 1 component — can't form a chain
    }
    out = agent._render_diagram_field(
        tool_input,
        structured_key="block_diagram",
        raw_key="block_diagram_mermaid",
        default_direction="LR",
        allow_empty=True,
    )
    # allow_empty=True + no structured + no raw + too-small BOM → empty string.
    assert out == ""


def test_derive_block_diagram_from_bom_returns_structured_spec():
    """Unit test for the helper: given a BOM of 3 components, it returns
    a dict with nodes + edges forming a linear chain."""
    agent = _StubAgent()
    tool_input = {
        "component_recommendations": [
            {"primary_part": "HMC8410", "function": "LNA"},
            {"primary_part": "ADL5801", "function": "Mixer"},
            {"primary_part": "AD9643", "function": "ADC"},
        ],
    }
    spec = agent._derive_block_diagram_from_bom(tool_input)
    assert spec is not None
    assert spec["direction"] == "LR"
    assert len(spec["nodes"]) == 3
    assert len(spec["edges"]) == 2  # N1→N2, N2→N3 linear chain
    # Each node carries the MPN in its label.
    labels = " ".join(n["label"] for n in spec["nodes"])
    assert "HMC8410" in labels
    assert "ADL5801" in labels
    assert "AD9643" in labels
    # Edge ids point to the real nodes.
    node_ids = {n["id"] for n in spec["nodes"]}
    for e in spec["edges"]:
        assert e["from_"] in node_ids
        assert e["to"] in node_ids


def test_bom_derived_path_now_preferred_over_raw_llm_mermaid():
    """P25 (2026-04-25): the user reported repeated mermaid parse
    failures across many sessions — every run produces a NEW broken
    pattern in the LLM's raw `block_diagram_mermaid` string. Trying
    to clean each pattern is whack-a-mole.

    Fix: BOM-derived structured chain is now PROMOTED above raw-mermaid
    salvage in `_render_diagram_field`. When both `block_diagram_mermaid`
    (raw, possibly clean) and `component_recommendations` are present,
    the BOM-derived path wins. Result: the rendered diagram is
    deterministic and includes the user's real parts; the LLM's
    free-text mermaid is no longer the primary source of truth.

    This test pins the priority ordering against future refactors that
    might silently swap them back.
    """
    agent = _StubAgent()
    tool_input = {
        # NO structured spec
        "block_diagram_mermaid": (
            # Even a perfectly-clean raw mermaid string must lose to
            # the BOM-derived chain when both are present.
            "flowchart LR\n"
            "    LEGACY_NODE[Legacy LLM diagram]\n"
        ),
        "component_recommendations": [
            {"primary_part": "HMC8410", "function": "LNA"},
            {"primary_part": "ADL5801", "function": "Mixer"},
            {"primary_part": "AD9643", "function": "ADC"},
        ],
    }
    out = agent._render_diagram_field(
        tool_input,
        structured_key="block_diagram",
        raw_key="block_diagram_mermaid",
        default_direction="LR",
    )
    # BOM-derived chain wins — output contains all 3 real MPNs.
    assert "HMC8410" in out
    assert "ADL5801" in out
    assert "AD9643" in out
    # The LLM's raw mermaid label is NOT in the output.
    assert "Legacy LLM diagram" not in out
    assert "LEGACY_NODE" not in out


def test_llm_structured_spec_still_wins_over_bom_derived():
    """The promotion of BOM-derived in P25 must NOT override the LLM's
    structured `block_diagram` JSON spec when one is provided.
    Priority order is: structured > BOM-derived > salvaged-raw."""
    agent = _StubAgent()
    tool_input = {
        # LLM provided a structured spec — this must win.
        "block_diagram": {
            "direction": "LR",
            "nodes": [
                {"id": "LLM_LNA", "label": "LLM-chosen LNA",
                 "shape": "amplifier"},
                {"id": "LLM_MIX", "label": "LLM-chosen Mixer",
                 "shape": "mixer"},
            ],
            "edges": [{"from": "LLM_LNA", "to": "LLM_MIX"}],
        },
        # And a BOM exists — but should NOT win because structured did.
        "component_recommendations": [
            {"primary_part": "HMC8410", "function": "LNA"},
            {"primary_part": "ADL5801", "function": "Mixer"},
        ],
    }
    out = agent._render_diagram_field(
        tool_input,
        structured_key="block_diagram",
        raw_key="block_diagram_mermaid",
        default_direction="LR",
    )
    # LLM's structured labels appear; BOM MPNs do not.
    assert "LLM-chosen LNA" in out or "LLM_LNA" in out
    assert "HMC8410" not in out


def test_derive_block_diagram_sanitises_mpn_for_node_ids():
    """Mermaid node IDs must match `^[A-Za-z][A-Za-z0-9_]*$`. MPNs with
    `+` / `-` / `/` must be scrubbed before becoming IDs, otherwise the
    rendered diagram fails to parse."""
    agent = _StubAgent()
    tool_input = {
        "component_recommendations": [
            {"primary_part": "ZX60-P103LN+", "function": "LNA"},    # + and -
            {"primary_part": "XCKU040-2FFVA1156I", "function": "FPGA"},
        ],
    }
    spec = agent._derive_block_diagram_from_bom(tool_input)
    assert spec is not None
    for node in spec["nodes"]:
        nid = node["id"]
        # First char alphanumeric (actually, alpha only per regex).
        assert nid[0].isalpha(), f"node id {nid!r} must start with a letter"
        # No +, -, /, spaces.
        assert "+" not in nid
        assert "-" not in nid
        assert "/" not in nid
        assert " " not in nid
        # But the label (free text) preserves the real MPN.
    labels = " ".join(n["label"] for n in spec["nodes"])
    assert "ZX60-P103LN+" in labels
    assert "XCKU040-2FFVA1156I" in labels
