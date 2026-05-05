"""Regression tests for the DOCX export's mermaid sanitisation pipeline.

P26 (2026-04-25) — `main._sanitize_mermaid_code` was reduced to a no-op
because every transformation it used to do is now handled (better) by
either `tools.mermaid_salvage.salvage` (raw-text patches) or
`tools.mermaid_coerce.coerce_to_spec` (structured re-rendering).

These tests still guard the SAME real-world failure mode that motivated
the original sanitiser — a 12 GHz Rx block diagram whose round-bracket
nodes contained nested parens and broke mermaid's parser, sending the
DOCX into a placeholder fallback. They now assert the COERCE pipeline
extracts the broken input correctly and re-renders it as flat rect
shapes that mermaid.js + mmdc + mermaid.ink all accept.

This mirrors the frontend tests in
`hardware-pipeline-v5-react/src/utils/mermaidSanitize.test.ts`.
"""
from __future__ import annotations

import importlib

from tools.mermaid_coerce import coerce_to_spec
from tools.mermaid_render import render_block_diagram


_main = importlib.import_module("main")
sanitize = _main._sanitize_mermaid_code  # noqa: SLF001


def _coerce_then_render(src: str, direction: str = "LR") -> str:
    """End-to-end through the production DOCX pipeline: extract a spec
    from raw mermaid text, then re-emit deterministic mermaid that all
    three downstream renderers (mermaid.js, mmdc, mermaid.ink) accept."""
    spec = coerce_to_spec(src, default_direction=direction)
    assert spec is not None, f"coerce_to_spec returned None for:\n{src}"
    return render_block_diagram(spec, default_direction=direction, raise_on_error=True)


class TestSanitizeNoOp:
    """`_sanitize_mermaid_code` is a deliberate no-op — its old logic
    UN-FIXED salvage's work and got removed (see docstring in main.py).
    These tests pin that contract so nobody accidentally re-introduces
    a broken sanitiser between salvage and the renderers."""

    def test_sanitize_is_identity(self):
        src = 'flowchart LR\n    A["a"] --> B["b"]'
        assert sanitize(src) == src

    def test_sanitize_does_not_strip_quotes(self):
        src = 'A["label with quotes"]'
        out = sanitize(src)
        assert '"label with quotes"' in out, (
            "no-op sanitiser must NOT strip quotes — that was the cjfn "
            "DOCX bug (P26 #6, 2026-04-25)"
        )


class TestRoundBracketNestedParens:
    """The 12 GHz Rx scenario — `S11("VGA (AGC)<br/>HMC624LP4E")` was
    truncated to `S11("VGA (AGC` by the round-bracket regex. Coerce now
    handles quoted-form labels with inner parens correctly."""

    def test_vga_agc_node_extracts_full_label(self):
        """Regression: the exact line from the 12 GHz Rx diagram. The
        label MUST include both the opening MPN portion AND the trailing
        chip name — no truncation at the inner `)`."""
        src = (
            'flowchart LR\n'
            '    A[Antenna] --> S11("VGA (AGC)<br/>HMC624LP4E")\n'
        )
        spec = coerce_to_spec(src)
        labels = {n["id"]: n["label"] for n in spec["nodes"]}
        assert "S11" in labels, f"S11 missing — got nodes: {list(labels)}"
        # Both ends of the label survived.
        assert "VGA" in labels["S11"], f"label lost 'VGA': {labels['S11']!r}"
        assert "AGC" in labels["S11"], f"label lost 'AGC': {labels['S11']!r}"
        assert "HMC624LP4E" in labels["S11"], (
            f"label lost MPN 'HMC624LP4E' (truncated at inner paren?): "
            f"{labels['S11']!r}"
        )

    def test_plain_round_bracket_node_label_preserved(self):
        """A round-bracket node without nested parens is extracted with
        its full label intact."""
        src = (
            'flowchart LR\n'
            '    A[Antenna] --> S4("LNA Stage 1<br/>HMC618ALP3E")\n'
        )
        spec = coerce_to_spec(src)
        labels = {n["id"]: n["label"] for n in spec["nodes"]}
        assert "S4" in labels
        assert "LNA Stage 1" in labels["S4"]
        assert "HMC618ALP3E" in labels["S4"]

    def test_full_rx_front_end_chain_extracts_cleanly(self):
        """End-to-end: the shape the pipeline actually emits for a 12 GHz
        Rx. ALL real nodes are extracted, NO phantom nodes appear from
        inner-label parens (e.g. `Microstrip(RO4350B)` inside S2's
        label must NOT become its own node)."""
        src = (
            'flowchart LR\n'
            '    %% 12.00 GHz +- 50 MHz\n'
            '    ANT((Antenna)) --> S1\n'
            '    S1["N-type Input Connector<br/>N-type IP67 50 ohm"]\n'
            '    S2["PCB Trace<br/>50Ohm Microstrip (RO4350B)"]\n'
            '    S11("VGA (AGC)<br/>HMC624LP4E")\n'
            '    S1 --> S2\n'
            '    S2 --> S11\n'
        )
        spec = coerce_to_spec(src, default_direction="LR")
        ids = {n["id"] for n in spec["nodes"]}
        # All 4 real nodes present.
        assert {"ANT", "S1", "S2", "S11"}.issubset(ids), (
            f"missing real nodes — got {ids}"
        )
        # No phantom nodes from inside other nodes' quoted labels.
        assert "Microstrip" not in ids, (
            "phantom node 'Microstrip' extracted from inside S2's label — "
            "non-overlap span tracking failed"
        )
        # Edges intact.
        edge_pairs = {(e["from_"], e["to"]) for e in spec["edges"]}
        assert ("ANT", "S1") in edge_pairs
        assert ("S1", "S2") in edge_pairs
        assert ("S2", "S11") in edge_pairs

        # And the rendered output must START with a valid flowchart header
        # so all three downstream renderers accept it.
        rendered = render_block_diagram(spec, default_direction="LR",
                                        raise_on_error=True)
        assert rendered.lstrip().startswith("flowchart "), rendered[:100]
        # MPNs survive into the rendered output.
        assert "RO4350B" in rendered
        assert "HMC624LP4E" in rendered
