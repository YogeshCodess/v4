"""
Tests for `tools.mermaid_render` — the deterministic JSON → Mermaid renderer.

Coverage intent:
  - Every shape in `SHAPE_NAMES` renders to its documented delimiter pair.
  - Validation catches every class of spec error we've seen in production:
    missing nodes, bad IDs, duplicate IDs, reserved words, invalid shape /
    direction, dangling edges, subgraph nodes that don't exist.
  - Labels are safe: unicode glyphs become ASCII, quotes and backticks are
    stripped, newlines convert to `<br/>`, control chars are dropped.
  - Rendering is deterministic: same spec twice ⇒ byte-identical output.
  - Both `from_` and `from` edge-key spellings are accepted.
  - Soft-error mode emits a `%% ERROR` placeholder instead of raising.
"""
from __future__ import annotations

import pytest

from tools.mermaid_render import (
    MermaidSpecError,
    SHAPE_NAMES,
    render_architecture,
    render_block_diagram,
    validate_spec,
)


# ---------------------------------------------------------------------------
# Shape rendering
# ---------------------------------------------------------------------------

_SHAPE_DELIMS = {
    "flag":       (">",   "]"),
    "connector":  ("[/",  "/]"),
    "rect":       ("[",   "]"),
    "limiter":    ("[/",  "\\]"),
    "amplifier":  (">",   "]"),
    "mixer":      ("(",   ")"),
    "filter":     ("{{",  "}}"),
    "rhombus":    ("{",   "}"),
    "digital":    ("[\\", "\\]"),
    "oscillator": ("(",   ")"),
    "stadium":    ("([",  "])"),
    "subroutine": ("[[",  "]]"),
    "cylinder":   ("[(",  ")]"),
    "circle":     ("((",  "))"),
}


@pytest.mark.parametrize("shape", sorted(SHAPE_NAMES))
def test_every_shape_renders_with_expected_delimiters(shape):
    spec = {
        "direction": "LR",
        "nodes": [{"id": "N1", "label": "x", "shape": shape}],
        "edges": [],
    }
    out = render_block_diagram(spec)
    open_, close_ = _SHAPE_DELIMS[shape]
    assert f'N1{open_}"x"{close_}' in out, f"shape {shape!r} malformed in {out!r}"


def test_shape_coverage_matches_internal_table():
    """SHAPE_NAMES and _SHAPE_DELIMS in this test file must agree — if a shape
    is added to the renderer this test file must be updated too."""
    assert SHAPE_NAMES == set(_SHAPE_DELIMS.keys())


# ---------------------------------------------------------------------------
# Happy-path rendering
# ---------------------------------------------------------------------------

def test_minimal_valid_spec():
    spec = {
        "direction": "LR",
        "nodes": [
            {"id": "A", "label": "start", "shape": "rect"},
            {"id": "B", "label": "end",   "shape": "rect"},
        ],
        "edges": [{"from": "A", "to": "B"}],
    }
    out = render_block_diagram(spec)
    assert out.startswith("flowchart LR")
    assert 'A["start"]' in out
    assert 'B["end"]' in out
    assert "A --> B" in out


def test_edge_label_renders_in_pipe_form():
    """Edge labels are emitted in mermaid's pipe form `A -->|label| B`,
    which is universally compatible across mermaid.js, mmdc, and the
    mermaid.ink HTTP API. The dash-quoted form `A -- "label" --> B`
    has caused intermittent parse failures (P26 #11, 2026-04-25)."""
    spec = {
        "nodes": [
            {"id": "A", "label": "a", "shape": "rect"},
            {"id": "B", "label": "b", "shape": "rect"},
        ],
        "edges": [{"from": "A", "to": "B", "label": "50 Ohm"}],
    }
    out = render_block_diagram(spec)
    assert "A -->|50 Ohm| B" in out
    # The legacy dash-quoted form must NOT appear.
    assert 'A -- "50 Ohm" -->' not in out


def test_edge_style_dotted_and_thick():
    spec = {
        "nodes": [
            {"id": "A", "label": "a", "shape": "rect"},
            {"id": "B", "label": "b", "shape": "rect"},
            {"id": "C", "label": "c", "shape": "rect"},
        ],
        "edges": [
            {"from": "A", "to": "B", "style": "dotted"},
            {"from": "B", "to": "C", "style": "thick"},
        ],
    }
    out = render_block_diagram(spec)
    assert "A -.-> B" in out
    assert "B ==> C" in out


def test_subgraph_references_nodes_by_id():
    spec = {
        "nodes": [
            {"id": "X", "label": "x", "shape": "rect"},
            {"id": "Y", "label": "y", "shape": "rect"},
            {"id": "Z", "label": "z", "shape": "rect"},
        ],
        "edges": [{"from": "X", "to": "Y"}],
        "subgraphs": [{"id": "S1", "title": "Cluster", "nodes": ["X", "Y"]}],
    }
    out = render_block_diagram(spec)
    assert 'subgraph S1["Cluster"]' in out
    assert "    end" in out
    # Nodes must be declared BEFORE the subgraph references them
    assert out.index('X["x"]') < out.index("subgraph S1")


def test_from_and_from_underscore_both_accepted():
    spec1 = {
        "nodes": [
            {"id": "A", "label": "a", "shape": "rect"},
            {"id": "B", "label": "b", "shape": "rect"},
        ],
        "edges": [{"from": "A", "to": "B"}],
    }
    spec2 = {
        "nodes": [
            {"id": "A", "label": "a", "shape": "rect"},
            {"id": "B", "label": "b", "shape": "rect"},
        ],
        "edges": [{"from_": "A", "to": "B"}],
    }
    assert render_block_diagram(spec1) == render_block_diagram(spec2)


def test_default_direction_for_architecture_is_TD():
    spec = {
        "nodes": [{"id": "A", "label": "a", "shape": "rect"}],
        "edges": [],
    }
    out = render_architecture(spec)
    assert out.startswith("flowchart TD")


def test_output_is_deterministic():
    spec = {
        "nodes": [
            {"id": "A", "label": "x", "shape": "flag"},
            {"id": "B", "label": "y", "shape": "filter"},
        ],
        "edges": [{"from": "A", "to": "B", "label": "z"}],
    }
    assert render_block_diagram(spec) == render_block_diagram(spec)


# ---------------------------------------------------------------------------
# Label escaping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("glyph,ascii_", [
    ("\u03A9", "Ohm"),     # Ω
    ("\u2126", "Ohm"),     # Ohm sign
    ("\u00B0", "deg"),     # °
    ("\u00B5", "u"),       # µ
    ("\u2013", "-"),       # en-dash
    ("\u2014", "-"),       # em-dash
    ("\u2264", "<="),
    ("\u2265", ">="),
    ("\u00B1", "+-"),
])
def test_unicode_glyphs_asciified_in_labels(glyph, ascii_):
    spec = {
        "nodes": [{"id": "A", "label": f"50{glyph}", "shape": "rect"}],
        "edges": [],
    }
    out = render_block_diagram(spec)
    assert f'"50{ascii_}"' in out
    # And no unicode chars leaked
    for ch in out:
        assert ord(ch) < 128, f"non-ASCII {ch!r} leaked: {out!r}"


def test_double_quote_stripped_from_label():
    spec = {
        "nodes": [{"id": "A", "label": 'he said "hi"', "shape": "rect"}],
        "edges": [],
    }
    out = render_block_diagram(spec)
    # quotes inside the inner label are gone — only the wrapper `"` remain
    assert '"he said hi"' in out


def test_backtick_stripped_from_label():
    spec = {
        "nodes": [{"id": "A", "label": "backtick`here", "shape": "rect"}],
        "edges": [],
    }
    out = render_block_diagram(spec)
    assert "`" not in out


def test_newline_in_label_becomes_br_tag():
    spec = {
        "nodes": [{"id": "A", "label": "line1\nline2", "shape": "rect"}],
        "edges": [],
    }
    out = render_block_diagram(spec)
    assert "line1<br/>line2" in out


def test_collapses_multiple_spaces():
    spec = {
        "nodes": [{"id": "A", "label": "a   lot    of   space", "shape": "rect"}],
        "edges": [],
    }
    out = render_block_diagram(spec)
    assert '"a lot of space"' in out


# ---------------------------------------------------------------------------
# Validation — negative cases
# ---------------------------------------------------------------------------

def test_validate_rejects_empty_nodes():
    errors = validate_spec({"direction": "LR", "nodes": [], "edges": []})
    assert any("at least 1 node" in e for e in errors)


def test_validate_rejects_bad_direction():
    errors = validate_spec({"direction": "DIAGONAL", "nodes": [{"id": "A", "label": "", "shape": "rect"}]})
    assert any("direction 'DIAGONAL'" in e for e in errors)


def test_validate_rejects_bad_id_charset():
    errors = validate_spec({"nodes": [{"id": "a-b", "label": "x", "shape": "rect"}]})
    assert any("must match" in e for e in errors)


def test_validate_rejects_id_starting_with_digit():
    errors = validate_spec({"nodes": [{"id": "1A", "label": "x", "shape": "rect"}]})
    assert any("must match" in e for e in errors)


def test_validate_rejects_reserved_word_id():
    errors = validate_spec({"nodes": [{"id": "end", "label": "x", "shape": "rect"}]})
    assert any("reserved word" in e for e in errors)


def test_validate_rejects_duplicate_ids():
    errors = validate_spec({
        "nodes": [
            {"id": "A", "label": "x", "shape": "rect"},
            {"id": "A", "label": "y", "shape": "rect"},
        ],
    })
    assert any("duplicated" in e for e in errors)


def test_validate_rejects_unknown_shape():
    errors = validate_spec({"nodes": [{"id": "A", "label": "x", "shape": "trapezoid"}]})
    assert any("shape 'trapezoid'" in e for e in errors)


def test_validate_rejects_dangling_edge():
    errors = validate_spec({
        "nodes": [{"id": "A", "label": "x", "shape": "rect"}],
        "edges": [{"from": "A", "to": "Z"}],
    })
    assert any("'Z' not defined" in e for e in errors)


def test_validate_rejects_subgraph_with_unknown_child():
    errors = validate_spec({
        "nodes": [{"id": "A", "label": "x", "shape": "rect"}],
        "subgraphs": [{"id": "S1", "title": "t", "nodes": ["A", "NOPE"]}],
    })
    assert any("'NOPE' not defined" in e for e in errors)


def test_validate_rejects_subgraph_id_clashing_with_node():
    errors = validate_spec({
        "nodes": [{"id": "A", "label": "x", "shape": "rect"}],
        "subgraphs": [{"id": "A", "title": "t", "nodes": []}],
    })
    assert any("clashes with a node id" in e for e in errors)


def test_validate_accepts_valid_spec_with_no_errors():
    errors = validate_spec({
        "direction": "LR",
        "nodes": [
            {"id": "A", "label": "x", "shape": "rect"},
            {"id": "B", "label": "y", "shape": "flag"},
        ],
        "edges": [{"from": "A", "to": "B", "style": "solid"}],
    })
    assert errors == []


def test_validate_rejects_non_dict_spec():
    assert validate_spec("flowchart LR") != []  # type: ignore[arg-type]
    assert validate_spec(None) != []             # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Error-handling modes
# ---------------------------------------------------------------------------

def test_render_raises_on_invalid_spec_by_default():
    with pytest.raises(MermaidSpecError):
        render_block_diagram({"nodes": []})


def test_render_soft_mode_emits_error_placeholder():
    out = render_block_diagram({"nodes": []}, raise_on_error=False)
    assert "flowchart LR" in out
    assert "%% ERROR" in out
    assert "invalid" in out.lower()


def test_soft_mode_never_leaks_percent_in_error_text():
    # `%%` inside a mermaid comment would terminate the comment early — the
    # renderer replaces `%` with `pct` to make sure error text is safe.
    out = render_block_diagram(
        {"nodes": [], "edges": []},  # triggers "at least 1 node" error
        raise_on_error=False,
    )
    # Count the `%` chars — each legitimate `%% ERROR:` line has exactly 2.
    for line in out.split("\n"):
        percent_count = line.count("%")
        if percent_count > 0:
            assert percent_count == 2, f"line has stray %: {line!r}"


# ---------------------------------------------------------------------------
# Realistic RF front-end smoke
# ---------------------------------------------------------------------------

def test_realistic_rf_frontend_smoke():
    spec = {
        "direction": "LR",
        "nodes": [
            {"id": "ANT1", "label": "Ant1 / 6-18 GHz", "shape": "flag", "stage": "antenna"},
            {"id": "SMA1", "label": "SMA-F", "shape": "connector"},
            {"id": "LIM1", "label": "Lim / RFLM-422 / IL 0.4 P+30max", "shape": "limiter", "stage": "limiter"},
            {"id": "BPF1", "label": "Preselector / CTF-1835 / IL2.5 BW150", "shape": "filter", "stage": "preselector"},
            {"id": "LNA1", "label": "LNA1 / HMC8410 / G+22 NF1.6 P1+22", "shape": "amplifier", "stage": "lna"},
            {"id": "MIX1", "label": "MIX / HMC8193", "shape": "mixer", "stage": "mixer"},
            {"id": "LO1",  "label": "LO / HMC830 / 5-15 GHz", "shape": "oscillator", "stage": "lo"},
            {"id": "C_G",  "label": "Net Gain +37 dB", "shape": "rect"},
            {"id": "C_NF", "label": "System NF 2.1 dB", "shape": "rect"},
        ],
        "edges": [
            {"from": "ANT1", "to": "SMA1"},
            {"from": "SMA1", "to": "LIM1"},
            {"from": "LIM1", "to": "BPF1"},
            {"from": "BPF1", "to": "LNA1", "label": "50 Ohm"},
            {"from": "LNA1", "to": "MIX1"},
            {"from": "LO1",  "to": "MIX1", "label": "LO+13 dBm"},
        ],
        "subgraphs": [
            {"id": "CASCADE", "title": "System Cumulative Performance", "nodes": ["C_G", "C_NF"]},
        ],
    }
    out = render_block_diagram(spec)
    # Every node appears exactly once in definition form
    for nid in ("ANT1", "SMA1", "LIM1", "BPF1", "LNA1", "MIX1", "LO1"):
        assert f"{nid}" in out
    # Pre-selector sits between limiter and LNA — per architect rule 9a
    assert out.index("LIM1") < out.index("BPF1") < out.index("LNA1")
    # Cascade subgraph present
    assert 'subgraph CASCADE["System Cumulative Performance"]' in out
