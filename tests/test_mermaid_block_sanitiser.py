"""P26 #17 (2026-04-26) — regression tests for the markdown-walker
mermaid sanitiser used by HRS / SDD / SRS / GLR agents.

These tests use the EXACT bug strings that broke the in-browser
preview on project rx_band so the regression coverage is concrete:

  L257 of HRS:  `MIX2["..."]}` — extra `}` after rect close
  L1540 of HRS: `LDO5C["+5V_CH[1:4]..."]` — nested `[1:4]` inside a
                quoted label that confuses mermaid's parser

The contract: feed a markdown document with a malformed mermaid
block in → get back the same markdown with the fenced block
replaced by a coerce + re-render pass. Other markdown content
(headings, prose, code fences in OTHER languages) is left
untouched.
"""
from __future__ import annotations

import pytest

from tools.mermaid_coerce import sanitize_mermaid_blocks_in_markdown


# ---------------------------------------------------------------------------
# Trivial / pass-through behaviour
# ---------------------------------------------------------------------------


def test_returns_empty_for_empty_input():
    assert sanitize_mermaid_blocks_in_markdown("") == ""


def test_returns_empty_for_none_input():
    assert sanitize_mermaid_blocks_in_markdown(None) == ""  # type: ignore[arg-type]


def test_passes_through_markdown_with_no_mermaid_blocks():
    md = "# Title\n\nSome prose.\n\n```python\nprint('hi')\n```\n"
    out = sanitize_mermaid_blocks_in_markdown(md)
    assert out == md, "non-mermaid markdown must round-trip unchanged"


def test_passes_through_python_fenced_block_untouched():
    """A ```python``` fence with mermaid-looking content inside MUST
    NOT be sanitised — only ```mermaid``` fences are in scope."""
    md = (
        "# Code sample\n\n"
        "```python\n"
        "code = '''\nA[a] --> B[b]\n'''\n"
        "```\n"
    )
    out = sanitize_mermaid_blocks_in_markdown(md)
    assert out == md


# ---------------------------------------------------------------------------
# The actual rx_band bugs that motivated this fix
# ---------------------------------------------------------------------------


def test_fixes_extra_brace_after_rect_close():
    """L257 of rx_band HRS: `MIX2["..."]}` — extra `}` makes mermaid
    parse-error. After sanitisation, the diagram is re-rendered as
    plain rect form with the same node id + label."""
    md = (
        "# HRS\n\n"
        "```mermaid\n"
        "flowchart TD\n"
        '    LO1["LO1 Synthesizer<br/>LMX2820RTCT<br/>14-36 GHz"]\n'
        '    MIX2["Mixer 2<br/>MMIQ-0205HSM-2<br/>IQ Conv Loss: 8 dB<br/>Img Rej: 25 dB"]}\n'
        "    LO1 --> MIX2\n"
        "```\n"
    )
    out = sanitize_mermaid_blocks_in_markdown(md)
    # Stray `}` must be gone.
    assert '"]}' not in out, (
        "sanitiser must strip the extra `}` after rect close"
    )
    # Both nodes survive in the re-rendered diagram.
    assert "LO1" in out
    assert "MIX2" in out
    assert "Mixer 2" in out
    assert "MMIQ-0205HSM-2" in out
    # The edge survives.
    assert "LO1 --> MIX2" in out
    # The rendered output is wrapped back in a mermaid fence.
    assert out.count("```mermaid") == 1
    assert out.count("```") == 2


def test_fixes_nested_brackets_inside_quoted_label():
    """L1540 of rx_band HRS: the label `+5V_CH[1:4]` contains literal
    `[` `]` — mermaid's parser sees the inner `[` as a new shape.
    After sanitisation, brackets in labels are stripped via
    `_clean_label`'s safe-char policy."""
    md = (
        "# HRS — Power Tree\n\n"
        "```mermaid\n"
        "flowchart LR\n"
        '    DCIN[+12V Input] --> LDO5C\n'
        '    LDO5C["LDO LM2940 ×4<br/>+6V → +5V_CH[1:4]<br/>300mA each"]\n'
        "```\n"
    )
    out = sanitize_mermaid_blocks_in_markdown(md)
    # The bracketed run [1:4] must NOT remain inside a label —
    # `_clean_label` replaces `[`/`]` with safer chars (or strips).
    # We just assert the LDO5C node still exists and parses.
    assert "LDO5C" in out
    assert "DCIN" in out
    # The diagram structure (an edge between the two nodes) survives.
    assert "DCIN --> LDO5C" in out
    # Mermaid shouldn't see a literal `[1:4]` in the rendered label
    # (would still be a parse error in mermaid.js). The coercer's
    # quoted-label form `["..."]` strips inner brackets via the
    # `_SHAPE_TRAILING_RE` pattern.
    # Pull just the rendered mermaid block out and assert no nested
    # `[...]` inside a quoted label.
    import re
    blocks = re.findall(r"```mermaid\n([\s\S]+?)\n```", out)
    assert len(blocks) == 1
    rendered = blocks[0]
    # Quick check: count `[`/`]` — they should balance and there
    # should be no inner `[` between an opening `["` and its close.
    # A node line like `LDO5C["a [b] c"]` is invalid mermaid.
    for line in rendered.splitlines():
        if '["' in line and '"]' in line:
            label = line.split('["', 1)[1].rsplit('"]', 1)[0]
            assert "[" not in label, (
                f"label still contains stray `[`: {line!r}"
            )
            assert "]" not in label, (
                f"label still contains stray `]`: {line!r}"
            )


# ---------------------------------------------------------------------------
# Multi-block document: only mermaid fences get touched
# ---------------------------------------------------------------------------


def test_handles_multiple_mermaid_blocks_in_one_document():
    md = (
        "# HRS\n\n"
        "## Block 1\n\n"
        "```mermaid\n"
        "flowchart TD\nA[a] --> B[b]\n"
        "```\n\n"
        "Some prose between.\n\n"
        "```python\nx = 1\n```\n\n"
        "## Block 2\n\n"
        "```mermaid\n"
        "flowchart LR\nC[c] --> D[d]\n"
        "```\n"
    )
    out = sanitize_mermaid_blocks_in_markdown(md)
    # Both mermaid blocks survive and re-render.
    assert out.count("```mermaid") == 2
    # Python block untouched.
    assert "```python\nx = 1\n```" in out
    # Prose untouched.
    assert "Some prose between." in out
    # All 4 nodes present in the output.
    for nid in ("A", "B", "C", "D"):
        assert nid in out


def test_idempotent_on_already_clean_input():
    """Running the sanitiser twice on a clean diagram should produce
    the same output as running it once. Critical because agents may
    call the helper multiple times across re-runs."""
    md = (
        "# Clean diagram\n\n"
        "```mermaid\n"
        "flowchart LR\n"
        '    A["alpha"] --> B["beta"]\n'
        "```\n"
    )
    once = sanitize_mermaid_blocks_in_markdown(md)
    twice = sanitize_mermaid_blocks_in_markdown(once)
    assert once == twice, (
        "sanitiser is not idempotent — repeated runs change content"
    )


# ---------------------------------------------------------------------------
# Pathological inputs: too few nodes → block left unchanged
# ---------------------------------------------------------------------------


def test_too_few_nodes_block_left_unchanged():
    """A mermaid block with only 1 extractable node can't be safely
    re-rendered (need at least 2 for an edge). The sanitiser must
    leave it untouched so the legacy DOCX-render salvage layer still
    has a chance at it."""
    md = (
        "```mermaid\n"
        "flowchart LR\n"
        "A[only one]\n"
        "```\n"
    )
    out = sanitize_mermaid_blocks_in_markdown(md)
    assert out == md


def test_completely_broken_block_left_unchanged():
    """Block that the coercer can't extract any nodes from is left
    untouched — no node names to re-render with, so the safest fallback
    is to ship the original and let the salvage / placeholder layer
    handle it later."""
    md = (
        "```mermaid\n"
        "flowchart LR\n"
        "this is just garbage and not parseable\n"
        "```\n"
    )
    out = sanitize_mermaid_blocks_in_markdown(md)
    assert out == md


# ---------------------------------------------------------------------------
# Direction parameter
# ---------------------------------------------------------------------------


def test_default_direction_used_when_block_has_none():
    md = (
        "```mermaid\n"
        "A[a] --> B[b]\n"
        "```\n"
    )
    out_lr = sanitize_mermaid_blocks_in_markdown(md, default_direction="LR")
    out_td = sanitize_mermaid_blocks_in_markdown(md, default_direction="TD")
    assert "flowchart LR" in out_lr
    assert "flowchart TD" in out_td


# ---------------------------------------------------------------------------
# P26 #21 — newline-leak regression: a malformed line MUST NOT swallow
# the next line into its label.
# ---------------------------------------------------------------------------


def test_malformed_line_does_not_swallow_next_line():
    """User reported (project Test): the trap_mixed_a regex's lazy
    `[^"\\\\]+?` swallowed newlines, so:

        SMA1[/SMA-F/]
        LIM1[/Lim / [specify] / IL<1 P+30max\\]

    was parsed as ONE giant SMA1 node spanning both lines (label
    became "SMA-F/]\\n    LIM1[/Lim / [specify] / IL<1 P+30max").
    LIM1 then disappeared from the spec entirely, and the rect
    pattern picked up a truncated `LIM1[/Lim / [specify]`.

    Fix: every label class also excludes `\\n` so lazy matches stay
    on one line. This test pins that — both nodes must extract with
    their full labels."""
    md = (
        "```mermaid\n"
        "flowchart TD\n"
        "    ANT1>\"Ant1-16<br>6-18 GHz\"]\n"
        "    SMA1[/SMA-F/]\n"
        "    LIM1[/Lim / [specify] / IL<1 P+30max\\]\n"
        "    BPF1{{Preselector / BFHKI-7851+ / IL2.5 BW1.9}}\n"
        "    LIM1 --> BPF1\n"
        "```\n"
    )
    out = sanitize_mermaid_blocks_in_markdown(md)
    # All 4 nodes survive intact.
    for nid in ("ANT1", "SMA1", "LIM1", "BPF1"):
        assert nid in out, f"node {nid} disappeared from output"
    # LIM1's label MUST contain the full text — `Lim`, `specify`, AND
    # `P+30max` (the bit that used to get truncated by the rect-pattern
    # short-match when trap_mixed_a leaked into SMA1's line).
    import re as _re
    block = _re.search(r"```mermaid\n([\s\S]+?)\n```", out)
    assert block is not None
    rendered = block.group(1)
    lim_lines = [ln for ln in rendered.splitlines() if "LIM1" in ln and "[" in ln]
    assert lim_lines, "no LIM1 node-definition line in output"
    lim_label_line = lim_lines[0]
    assert "Lim" in lim_label_line
    assert "specify" in lim_label_line
    assert "P+30max" in lim_label_line, (
        f"LIM1 label is truncated — `P+30max` lost: {lim_label_line!r}"
    )
    # Edge survives.
    assert "LIM1 --> BPF1" in rendered
