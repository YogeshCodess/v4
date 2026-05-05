"""Tests for `tools.mermaid_coerce` — the PERMANENT fix for the mermaid
parse-error bug class the user reported 30+ times.

Strategy under test: instead of patching raw LLM-emitted mermaid (the
salvage approach, which leaves N+1 variants unhandled per fix), the
coercer EXTRACTS (node_id, label) pairs and edges from any shape variant
and returns a structured spec that the deterministic renderer can
re-emit as guaranteed-valid mermaid.

These tests cover every shape variant that has shown up in the user's
bug reports, the edge regex variants, the lines we drop (sequence-
diagram directives in flowchart context), and the empirical broken
inputs from output/{cjfn,fyfu,hjjg,hxhc}/architecture.md.
"""
from __future__ import annotations

import pytest

from tools.mermaid_coerce import coerce_to_spec


# ---------------------------------------------------------------------------
# Empty / invalid / too-small input → caller falls back
# ---------------------------------------------------------------------------


def test_empty_string_returns_none():
    assert coerce_to_spec("") is None


def test_none_input_returns_none():
    assert coerce_to_spec(None) is None  # type: ignore[arg-type]


def test_non_string_input_returns_none():
    assert coerce_to_spec(123) is None  # type: ignore[arg-type]


def test_single_node_returns_none():
    """A diagram with only one node can't form an edge — caller should
    use a fallback diagram instead of rendering a degenerate one."""
    src = "flowchart LR\nA[Only one node]"
    assert coerce_to_spec(src) is None


# ---------------------------------------------------------------------------
# Shape variants — every one the user has reported
# ---------------------------------------------------------------------------


def test_extracts_rect_shape():
    src = "flowchart LR\nA[First] --> B[Second]"
    spec = coerce_to_spec(src)
    assert spec is not None
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert labels == {"A": "First", "B": "Second"}


def test_extracts_round_shape():
    src = "flowchart LR\nA(rounded) --> B[plain]"
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert labels["A"] == "rounded"


def test_extracts_circle_shape():
    src = "flowchart LR\nA((circle)) --> B[next]"
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert labels["A"] == "circle"


def test_extracts_stadium_shape():
    src = 'flowchart LR\nA(["Stadium label"]) --> B[next]'
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert labels["A"] == "Stadium label"


def test_extracts_subroutine_shape():
    src = "flowchart LR\nA[[Subroutine]] --> B[next]"
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert labels["A"] == "Subroutine"


def test_extracts_cylinder_shape():
    src = "flowchart LR\nA[(Database)] --> B[next]"
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert labels["A"] == "Database"


def test_extracts_hexagon_shape():
    src = "flowchart LR\nA{{Hexagon filter}} --> B[next]"
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert labels["A"] == "Hexagon filter"


def test_extracts_rhombus_shape():
    src = "flowchart LR\nA{Decision} --> B[next]"
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert labels["A"] == "Decision"


def test_extracts_trapezoid_shape():
    """Trapezoid `[\\..\\]` was variant #1 in the bug reports — limiter
    blocks emitted by the LLM."""
    src = "flowchart LR\nA[\\Limiter\\] --> B[Mixer]"
    spec = coerce_to_spec(src)
    assert spec is not None
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert "Limiter" in labels["A"]


def test_extracts_parallelogram_shape():
    """Parallelogram `[/.../]` — image rejection blocks."""
    src = "flowchart LR\nA[/Image Reject/] --> B[ADC]"
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert "Image Reject" in labels["A"]


def test_extracts_flag_shape():
    """Flag `>label]` — antenna inputs the user reported as broken."""
    src = "flowchart LR\nAnt1>RF Antenna] --> LNA[LNA HMC8410]"
    spec = coerce_to_spec(src)
    assert spec is not None
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert "RF Antenna" in labels["Ant1"]


def test_extracts_mixed_slash_trapezoid():
    src = "flowchart LR\nA[/MixedSlash\\] --> B[next]"
    spec = coerce_to_spec(src)
    assert spec is not None
    assert "A" in {n["id"] for n in spec["nodes"]}


# ---------------------------------------------------------------------------
# Labels with problematic characters (the actual root cause of "30 reports")
# ---------------------------------------------------------------------------


def test_label_with_parens_in_trapezoid():
    """Variant from user report: trapezoid label containing parens that
    mermaid's strict parser refused. Coercer should extract the text and
    let the renderer quote it safely."""
    src = "flowchart LR\nA[\\Limiter (15 dB)\\] --> B[Filter]"
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert "Limiter" in labels["A"]
    assert "15 dB" in labels["A"]


def test_label_with_html_break():
    """`<br/>` inside labels — user demo report."""
    src = 'flowchart LR\nA["First line<br/>second line"] --> B[next]'
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    # `<br/>` is normalised to `<br>` (mermaid's preferred form)
    assert "<br>" in labels["A"]


def test_label_with_hash_chars():
    """`#` in part numbers (e.g. `#TPS54620`) — caused subroutine `[[..]]`
    to fail."""
    src = "flowchart LR\nA[[Buck #TPS54620]] --> B[Load]"
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert "TPS54620" in labels["A"]


def test_label_with_double_quotes_stripped():
    src = 'flowchart LR\nA["with quotes"] --> B[next]'
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert labels["A"] == "with quotes"  # quotes were stripped


def test_label_with_pipe_replaced():
    """`|` is reserved in edge labels — coercer replaces with `/`."""
    src = "flowchart LR\nA[before|after] --> B[next]"
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert "|" not in labels["A"]
    assert "/" in labels["A"]


def test_label_with_html_tags_stripped():
    src = "flowchart LR\nA[label with <i>italic</i> tag] --> B[next]"
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert "<i>" not in labels["A"]
    assert "italic" in labels["A"]


def test_label_with_backslashes_replaced():
    """Backslashes are shape delimiters — they should never appear inside
    a label. Coercer replaces with spaces."""
    src = "flowchart LR\nA[label\\with\\slashes] --> B[next]"
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert "\\" not in labels["A"]


# ---------------------------------------------------------------------------
# Non-ASCII glyphs
# ---------------------------------------------------------------------------


def test_ohm_symbol_replaced():
    src = "flowchart LR\nA[50Ω load] --> B[next]"
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert "Ohm" in labels["A"]


def test_degree_symbol_replaced():
    src = "flowchart LR\nA[Phase 90°] --> B[next]"
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert "deg" in labels["A"]


def test_mu_symbol_replaced():
    src = "flowchart LR\nA[100µs pulse] --> B[next]"
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert "100us pulse" in labels["A"]


# ---------------------------------------------------------------------------
# Edge variants
# ---------------------------------------------------------------------------


def test_extracts_simple_arrow():
    src = "flowchart LR\nA[a] --> B[b]"
    spec = coerce_to_spec(src)
    edges = spec["edges"]
    assert len(edges) == 1
    assert edges[0]["from_"] == "A" and edges[0]["to"] == "B"


def test_extracts_thick_arrow():
    src = "flowchart LR\nA[a] ==> B[b]"
    spec = coerce_to_spec(src)
    assert len(spec["edges"]) == 1


def test_extracts_dotted_arrow():
    src = "flowchart LR\nA[a] -.-> B[b]"
    spec = coerce_to_spec(src)
    assert len(spec["edges"]) == 1


def test_extracts_pipe_label_edge():
    src = "flowchart LR\nA[a] -->|with label| B[b]"
    spec = coerce_to_spec(src)
    assert len(spec["edges"]) == 1
    assert spec["edges"][0].get("label") == "with label"


def test_extracts_quoted_label_edge():
    src = 'flowchart LR\nA[a] -- "edge label" --> B[b]'
    spec = coerce_to_spec(src)
    assert len(spec["edges"]) == 1
    assert spec["edges"][0].get("label") == "edge label"


def test_implicit_node_via_edge_synthesised_as_rect():
    """When the LLM writes `A --> B` without a `B[label]` declaration,
    we should still produce a node B (label = id) so the edge isn't
    dropped."""
    src = "flowchart LR\nA[a] --> B"
    spec = coerce_to_spec(src)
    assert spec is not None
    ids = {n["id"] for n in spec["nodes"]}
    assert "A" in ids and "B" in ids


# ---------------------------------------------------------------------------
# Direction extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hdr,expected", [
    ("flowchart LR", "LR"),
    ("flowchart RL", "RL"),
    ("flowchart TD", "TD"),
    ("flowchart TB", "TD"),  # TB normalised to TD
    ("flowchart BT", "BT"),
    ("graph LR", "LR"),
    ("graph TD", "TD"),
])
def test_direction_extracted_from_header(hdr, expected):
    src = f"{hdr}\nA[a] --> B[b]"
    spec = coerce_to_spec(src)
    assert spec["direction"] == expected


def test_direction_defaults_to_lr_when_missing():
    src = "A[a] --> B[b]"
    spec = coerce_to_spec(src)
    assert spec["direction"] == "LR"


def test_direction_default_param_overridable():
    src = "A[a] --> B[b]"
    spec = coerce_to_spec(src, default_direction="TD")
    assert spec["direction"] == "TD"


# ---------------------------------------------------------------------------
# Lines that must be DROPPED (cause parse errors in flowchart context)
# ---------------------------------------------------------------------------


def test_drops_sequence_diagram_note_directive():
    """User report: `note right of NODE` in a flowchart. Mermaid rejects
    sequence-diagram directives in flowcharts."""
    src = """flowchart LR
A[Sender] --> B[Receiver]
note right of B
    Initialize handshake
end note"""
    spec = coerce_to_spec(src)
    assert spec is not None
    # Edge survived; note was dropped.
    assert len(spec["edges"]) == 1


def test_drops_participant_directive():
    src = """flowchart LR
participant U1 as User
A[Login] --> B[Auth]"""
    spec = coerce_to_spec(src)
    ids = {n["id"] for n in spec["nodes"]}
    # Note: 'participant' line itself doesn't get extracted as a node.
    assert "A" in ids and "B" in ids


def test_drops_class_and_style_lines():
    """Malformed `class`/`style` declarations were a P26 #6 bug."""
    src = """flowchart LR
A[a] --> B[b]
class A,B critical
style A fill:#f9f"""
    spec = coerce_to_spec(src)
    assert spec is not None
    # The two real nodes survive without picking up class/style noise.
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert labels.get("A") == "a"
    assert labels.get("B") == "b"


# ---------------------------------------------------------------------------
# Frontmatter / comments stripping
# ---------------------------------------------------------------------------


def test_strips_frontmatter_init_block():
    src = """%%{init: {'theme': 'dark'}}%%
flowchart LR
A[a] --> B[b]"""
    spec = coerce_to_spec(src)
    assert spec is not None
    assert spec["direction"] == "LR"


def test_strips_comment_lines():
    src = """flowchart LR
%% this is a comment
A[a] --> B[b]
%% another comment"""
    spec = coerce_to_spec(src)
    assert len(spec["edges"]) == 1


# ---------------------------------------------------------------------------
# Subgraph headers don't get mis-extracted as nodes
# ---------------------------------------------------------------------------


def test_subgraph_header_not_extracted_as_node():
    src = """flowchart LR
subgraph PWR["Power section"]
    A[Reg] --> B[Cap]
end"""
    spec = coerce_to_spec(src)
    assert spec is not None
    ids = {n["id"] for n in spec["nodes"]}
    # PWR was a subgraph wrapper, not a node — must NOT appear.
    assert "PWR" not in ids
    assert "A" in ids and "B" in ids


# ---------------------------------------------------------------------------
# Realistic broken inputs from the bug reports — full pipeline
# ---------------------------------------------------------------------------


def test_realistic_mixed_shapes_with_problem_chars():
    """Composite of all the variants in one diagram — what an actual
    LLM emission can look like."""
    src = """%%{init: {'theme': 'dark'}}%%
flowchart LR
%% RF chain
Ant1>RF Antenna 2-18 GHz] --> LIM[\\Limiter (15 dB)\\]
LIM --> LNA[[LNA HMC8410]]
LNA --> FILT[/Image Reject Filter/]
FILT --> MIX{{Mixer ADL5801}}
MIX --> ADC[(ADC AD9625)]
ADC --> FPGA{Decision FPGA}
note right of FPGA
   Process samples
end note
class FPGA critical
style ADC fill:#0f0"""
    spec = coerce_to_spec(src)
    assert spec is not None
    ids = {n["id"] for n in spec["nodes"]}
    assert {"Ant1", "LIM", "LNA", "FILT", "MIX", "ADC", "FPGA"}.issubset(ids)
    # 6 edges (Ant1->LIM, LIM->LNA, LNA->FILT, FILT->MIX, MIX->ADC, ADC->FPGA)
    assert len(spec["edges"]) >= 6
    # All nodes are emitted as `rect` (the safe shape)
    for node in spec["nodes"]:
        assert node["shape"] == "rect"


def test_de_duplicates_repeated_edges():
    """If the LLM emits the same edge twice (declaration + reference),
    we should dedupe to avoid arrow-stacking."""
    src = """flowchart LR
A[a] --> B[b]
A --> B"""
    spec = coerce_to_spec(src)
    assert len(spec["edges"]) == 1


def test_first_label_wins_for_duplicate_node_id():
    """If a node id appears multiple times with different labels, the
    first one wins (matches user's intent — declarations are at top)."""
    src = """flowchart LR
A[First label] --> B[b]
A[Second label]"""
    spec = coerce_to_spec(src)
    labels = {n["id"]: n["label"] for n in spec["nodes"]}
    assert labels["A"] == "First label"


# ---------------------------------------------------------------------------
# All output nodes have shape=rect (the SAFE shape — accepts any chars
# inside quoted-label syntax)
# ---------------------------------------------------------------------------


def test_all_extracted_nodes_emit_as_rect():
    """The contract: regardless of the source shape, the coerced spec
    always uses `rect` because the deterministic renderer uses
    `["label"]` quoted form — accepts any character except `"` itself
    (which we strip)."""
    src = """flowchart LR
A((circle)) --> B{{hexagon}}
B --> C[/parallelogram/]
C --> D[\\trapezoid\\]
D --> E([stadium])
E --> F[(cylinder)]
F --> G[[subroutine]]
G --> H{rhombus}
H --> I(round)
I --> J[plain rect]"""
    spec = coerce_to_spec(src)
    assert spec is not None
    for node in spec["nodes"]:
        assert node["shape"] == "rect", (
            f"Node {node['id']} should be rect, got {node['shape']}"
        )


# ---------------------------------------------------------------------------
# Output is a renderer-compatible BlockDiagramSpec
# ---------------------------------------------------------------------------


def test_output_is_renderer_compatible():
    """End-to-end: coerced spec must be accepted by the deterministic
    `render_block_diagram` and produce valid mermaid source."""
    from tools.mermaid_render import render_block_diagram

    src = "flowchart LR\nA[a] --> B[b] --> C[c]"
    spec = coerce_to_spec(src)
    rendered = render_block_diagram(spec, default_direction="LR", raise_on_error=True)

    # Must start with a flowchart header.
    assert rendered.startswith("flowchart "), f"Expected header, got {rendered[:50]}"
    # All three nodes must appear with their labels in quoted form.
    assert '["a"]' in rendered
    assert '["b"]' in rendered
    assert '["c"]' in rendered


def test_no_subgraphs_in_output_spec():
    """Coerced spec deliberately drops subgraphs (they cause cascading
    parse errors with broken LLM input). Caller knows this is a
    deliberate degradation in exchange for guaranteed render."""
    src = """flowchart LR
subgraph S1["Section 1"]
A[a] --> B[b]
end
subgraph S2["Section 2"]
C[c] --> D[d]
end"""
    spec = coerce_to_spec(src)
    assert spec is not None
    # No 'subgraphs' key present (or empty if present).
    assert not spec.get("subgraphs"), (
        "Coerced spec should not include subgraphs (deliberate)"
    )
    # All four real nodes were still extracted.
    ids = {n["id"] for n in spec["nodes"]}
    assert {"A", "B", "C", "D"}.issubset(ids)
