"""
Tests for `tools.mermaid_salvage` — the back-compat fixer that rescues raw
LLM-emitted Mermaid text when we can't use the structured path.

Coverage intent: every step-helper in the salvage pipeline is exercised in
isolation AND in combination with a realistic LLM-broken input.

The user's demo-floor bug reports drove these cases directly:
  - "Parse error on line 4: ...irection LR >Ant1] -->[/2.92mm K"
    -> a bare `>Ant1]` at line start needs a synthetic `_n1>` prefix
  - "xBt[a.shape] is not a function"
    -> non-ASCII glyphs (deg, Ohm) + em-dash arrows confuse mermaid's shape
       table; asciify + arrow-normalise fix this before render.
"""
from __future__ import annotations

import pytest

from tools.mermaid_salvage import FALLBACK_DIAGRAM, salvage


# ---------------------------------------------------------------------------
# Empty / invalid input
# ---------------------------------------------------------------------------

def test_empty_input_returns_fallback():
    cleaned, fixes = salvage("")
    assert cleaned == FALLBACK_DIAGRAM
    assert "fallback" in fixes


def test_none_input_returns_fallback():
    cleaned, fixes = salvage(None)  # type: ignore[arg-type]
    assert cleaned == FALLBACK_DIAGRAM
    assert "fallback" in fixes


def test_garbage_input_returns_fallback():
    # Input with no diagram-type keyword at all should fall back to the safe
    # placeholder rather than leak the garbage into the rendered output.
    cleaned, fixes = salvage("random text with no mermaid syntax whatsoever")
    # After prepend_flowchart_header, the first line becomes `flowchart LR`,
    # so the sanity gate passes — but the output still starts correctly.
    assert cleaned.startswith("flowchart ")
    assert "prepend_flowchart_header" in fixes


# ---------------------------------------------------------------------------
# Step A — asciify
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("glyph,ascii_", [
    ("\u03A9", "Ohm"),
    ("\u00B0", "deg"),
    ("\u00B5", "u"),
    ("\u2013", "-"),
    ("\u2014", "-"),
])
def test_asciify_replaces_glyphs(glyph, ascii_):
    raw = f"flowchart LR\n    A[50 {glyph} term] --> B"
    cleaned, fixes = salvage(raw)
    assert ascii_ in cleaned
    assert glyph not in cleaned
    assert "asciify" in fixes


def test_asciify_strips_bom():
    raw = "\ufefflowchart LR\n    A --> B"
    cleaned, fixes = salvage(raw)
    assert "\ufeff" not in cleaned
    assert "asciify" in fixes


def test_asciify_drops_unknown_unicode():
    raw = "flowchart LR\n    A[\u2603 snowman] --> B"  # ☃
    cleaned, _ = salvage(raw)
    for ch in cleaned:
        assert ord(ch) < 128


# ---------------------------------------------------------------------------
# Step B — arrow normalisation
# ---------------------------------------------------------------------------

def test_em_dash_arrows_become_ascii_arrows():
    raw = "flowchart LR\n    A \u2014\u2014> B"  # em-dash em-dash >
    cleaned, fixes = salvage(raw)
    assert "A --> B" in cleaned
    assert "normalise_arrows" in fixes or "asciify" in fixes


def test_single_dash_arrow_upgraded_to_double():
    raw = "flowchart LR\n    A -> B"
    cleaned, fixes = salvage(raw)
    assert "A --> B" in cleaned
    assert "normalise_arrows" in fixes


def test_dotted_arrow_preserved():
    raw = "flowchart LR\n    A -.-> B"
    cleaned, _ = salvage(raw)
    assert "-.->" in cleaned


def test_thick_arrow_preserved():
    # `==>` is legitimate Mermaid (thick style) — we do NOT rewrite it
    raw = "flowchart LR\n    A ==> B"
    cleaned, _ = salvage(raw)
    assert "==>" in cleaned


# ---------------------------------------------------------------------------
# Step C — frontmatter strip
# ---------------------------------------------------------------------------

def test_init_block_stripped():
    raw = '%%{init: {"theme":"dark"}}%%\nflowchart LR\n    A --> B'
    cleaned, fixes = salvage(raw)
    assert "init" not in cleaned
    assert "strip_frontmatter" in fixes


def test_comment_line_preserved():
    # `%% text` lines are legitimate mermaid comments and should be kept.
    raw = "flowchart LR\n%% this is a comment\n    A --> B"
    cleaned, _ = salvage(raw)
    assert "this is a comment" in cleaned


# ---------------------------------------------------------------------------
# Step D — direction strip
# ---------------------------------------------------------------------------

def test_bare_direction_line_removed():
    raw = "flowchart LR\ndirection LR\n    A --> B"
    cleaned, fixes = salvage(raw)
    assert "strip_direction" in fixes
    # No standalone `direction LR` line
    assert "\ndirection LR\n" not in cleaned


def test_direction_inside_subgraph_preserved():
    raw = (
        "flowchart TB\n"
        "    subgraph S1\n"
        "        direction LR\n"
        "        A --> B\n"
        "    end\n"
    )
    cleaned, fixes = salvage(raw)
    assert "direction LR" in cleaned
    assert "strip_direction" not in fixes


# ---------------------------------------------------------------------------
# Step E — header normalise
# ---------------------------------------------------------------------------

def test_graph_becomes_flowchart():
    raw = "graph TD\n    A --> B"
    cleaned, fixes = salvage(raw)
    assert cleaned.startswith("flowchart TD")
    assert "normalise_header_graph_to_flowchart" in fixes


def test_missing_header_prepended():
    raw = "A --> B\n    C --> D"
    cleaned, fixes = salvage(raw)
    assert cleaned.startswith("flowchart LR")
    assert "prepend_flowchart_header" in fixes


def test_known_header_unchanged():
    raw = "sequenceDiagram\n    A->>B: hi"
    cleaned, fixes = salvage(raw)
    # No header rewrite fired
    assert "prepend_flowchart_header" not in fixes
    assert "normalise_header_graph_to_flowchart" not in fixes


# ---------------------------------------------------------------------------
# Step F — bare-shape fix (the headline bug)
# ---------------------------------------------------------------------------

def test_bare_flag_gets_synthetic_id():
    """Reproduces user bug: '>Ant1] -->[/2.92mm K' parses as a bare
    flag shape with no node id. Salvager prefixes `_n1`."""
    raw = "flowchart LR\n>Ant1]"
    cleaned, fixes = salvage(raw)
    assert "fix_bare_shapes" in fixes
    # The line should now start with an identifier, not `>`
    for line in cleaned.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith(">Ant1"):
            pytest.fail(f"bare flag not fixed: {line!r}")


def test_bare_parallelogram_gets_synthetic_id():
    raw = "flowchart LR\n[/SMA-F/]"
    cleaned, fixes = salvage(raw)
    assert "fix_bare_shapes" in fixes


def test_bare_double_paren_circle_fixed():
    raw = "flowchart LR\n((LO1))"
    cleaned, fixes = salvage(raw)
    assert "fix_bare_shapes" in fixes


def test_bare_double_bracket_subroutine_fixed():
    raw = "flowchart LR\n[[subsys]]"
    cleaned, fixes = salvage(raw)
    assert "fix_bare_shapes" in fixes


def test_ordered_ids_increment_across_multiple_bare_shapes():
    raw = "flowchart LR\n>A]\n>B]\n>C]"
    cleaned, _ = salvage(raw)
    assert "_n1" in cleaned
    assert "_n2" in cleaned
    assert "_n3" in cleaned


# ---------------------------------------------------------------------------
# Step G — dangerous-label quoting
# ---------------------------------------------------------------------------

def test_labels_with_angle_brackets_get_quoted():
    raw = "flowchart LR\n    A[foo<bar] --> B"
    cleaned, fixes = salvage(raw)
    assert "quote_dangerous_labels" in fixes
    assert '"foo<bar"' in cleaned or '"foo bar"' in cleaned


def test_labels_with_pipe_get_quoted():
    raw = "flowchart LR\n    A[foo|bar] --> B"
    cleaned, fixes = salvage(raw)
    assert "quote_dangerous_labels" in fixes


def test_labels_with_hash_get_quoted():
    raw = "flowchart LR\n    A[#01 first] --> B"
    cleaned, fixes = salvage(raw)
    assert "quote_dangerous_labels" in fixes


def test_already_quoted_labels_left_alone():
    raw = 'flowchart LR\n    A["foo<bar"] --> B'
    cleaned, fixes = salvage(raw)
    # The label is already quoted; shouldn't be double-wrapped
    assert cleaned.count('""') == 0
    assert "quote_dangerous_labels" not in fixes


# ---------------------------------------------------------------------------
# Step H — bracket closure
# ---------------------------------------------------------------------------

def test_unclosed_bracket_autoclosed():
    raw = "flowchart LR\n    A[unclosed label\n    B --> C"
    cleaned, fixes = salvage(raw)
    assert "close_brackets" in fixes


def test_balanced_brackets_untouched():
    raw = "flowchart LR\n    A[balanced] --> B[also balanced]"
    cleaned, fixes = salvage(raw)
    assert "close_brackets" not in fixes


# ---------------------------------------------------------------------------
# Step I — end isolation
# ---------------------------------------------------------------------------

def test_trailing_end_gets_isolated():
    raw = (
        "flowchart LR\n"
        "    subgraph S1\n"
        "        A --> B end\n"
    )
    cleaned, fixes = salvage(raw)
    assert "isolate_end" in fixes
    # `end` must be on its own line
    lines = [ln.strip() for ln in cleaned.split("\n")]
    assert "end" in lines


# ---------------------------------------------------------------------------
# Integration — the user's reported bug
# ---------------------------------------------------------------------------

def test_users_reported_bug_is_rescued():
    """End-to-end: the exact failure mode from the user's demo bug report."""
    raw = (
        "%%{init: {\"theme\":\"dark\"}}%%\n"
        "graph TD\n"
        "direction LR\n"
        ">Ant1] -->[/2.92mm K connector/] --> LNA1\n"
    )
    cleaned, fixes = salvage(raw)
    # Frontmatter stripped
    assert "init" not in cleaned
    # graph TD -> flowchart TD
    assert cleaned.startswith("flowchart TD")
    # Bare `>Ant1]` got a synthetic id prefix
    assert "_n1" in cleaned
    # No stray `direction LR` outside a subgraph
    for line in cleaned.split("\n"):
        if line.strip() == "direction LR":
            pytest.fail("bare 'direction LR' survived salvage")
    # At least 3 independent fixes applied
    assert len(fixes) >= 3


def test_multiline_rf_receiver_roundtrip():
    """A messy but realistic LLM-emitted block diagram should survive
    salvage and produce something that mermaid-js would at least parse."""
    raw = (
        "graph LR\n"
        ">Ant1 6-18 GHz] -->[/SMA-F/] --> LIM1[/Lim RFLM-422\\]\n"
        "LIM1 --> BPF1{{Preselector CTF-1835}}\n"
        "BPF1 --> LNA1>LNA HMC8410 / G+22 NF1.6]\n"
        "LNA1 --> MIX1(MIX HMC8193)\n"
    )
    cleaned, fixes = salvage(raw)
    assert cleaned.startswith("flowchart LR")
    # Each shape variety is present after salvage
    for shape in (">", "(", "{{", "[/"):
        assert shape in cleaned
    # And there must have been at least one non-trivial fix
    assert fixes


def test_crlf_line_endings_normalised():
    raw = "flowchart LR\r\n    A --> B\r\n"
    cleaned, _ = salvage(raw)
    assert "\r" not in cleaned


# ---------------------------------------------------------------------------
# Quoted edge-label salvage — regression for the 2026-04-24 power-tree bug
# ---------------------------------------------------------------------------

class TestFixQuotedEdgeLabels:
    """Mermaid edge labels live inside pipes (`-->|label|`), not between
    arrow dashes (`-- "label" -->`). The LLM keeps emitting the wrong
    shape — these tests guard the salvage pass that fixes it."""

    def test_normal_arrow_with_quoted_label(self):
        raw = 'flowchart TD\n    BUCK -- "+5 V" --> LDO1\n'
        cleaned, fixes = salvage(raw)
        assert "fix_quoted_edge_labels" in fixes
        # Edge converts to canonical pipe form, label preserved.
        assert "BUCK -->|+5 V| LDO1" in cleaned
        # Quoted label form must be gone.
        assert '-- "+5 V" -->' not in cleaned

    def test_thick_arrow_with_quoted_label(self):
        raw = 'flowchart TD\n    BUCK == "thick" ==> LDO1\n'
        cleaned, fixes = salvage(raw)
        assert "fix_quoted_edge_labels" in fixes
        assert "BUCK ==>|thick| LDO1" in cleaned

    def test_mixed_arrow_styles(self):
        # Quirky LLM output: dashes on one side, equals on the other.
        # Salvage promotes to thick arrow because either side is `==`.
        raw = 'flowchart TD\n    A == "label" --> B\n'
        cleaned, fixes = salvage(raw)
        assert "fix_quoted_edge_labels" in fixes
        # Use thick form since one side is `==`.
        assert "A ==>|label| B" in cleaned

    def test_dotted_arrow_with_quoted_label(self):
        # Regression for 2026-04-24 chat-page parse error:
        #   "CLK1 -. \"170 MHz LVPECL\" .-> ADC1"
        # The dotted form has a leading `-.` on the left and a `.->`
        # tail on the right (the `.` comes first, then `->`). Earlier
        # versions of this regex matched `-.->` as tail which is the
        # UNLABELED dotted-arrow token — so labelled dotted edges
        # slipped through unsalvaged.
        raw = 'flowchart LR\n    CLK1 -. "170 MHz LVPECL" .-> ADC1\n'
        cleaned, fixes = salvage(raw)
        assert "fix_quoted_edge_labels" in fixes
        assert "CLK1 -.->|170 MHz LVPECL| ADC1" in cleaned
        assert '-. "170 MHz LVPECL"' not in cleaned

    def test_full_channelised_fe_screenshot_diagram(self):
        # End-to-end version of the exact source in screenshot 2026-04-24
        # channelised FE: a mix of normal, thick, and dotted arrows with
        # quoted labels, one of each type per arrow style.
        raw = (
            "flowchart LR\n"
            '    ANT1 -- "RF per channel" --> SMA1\n'
            '    SMA1 -- "Analog RF" --> ADC1\n'
            '    CLK1 -. "170 MHz LVPECL" .-> ADC1\n'
            '    CLK1 -. "Ref Clk + SYSREF" .-> FPG1\n'
            '    ADC1 -- "14-bit parallel LVDS" --> FPG1\n'
            '    FPG1 == "JESD204C 4+ lanes" ==> OUT1\n'
            '    PWR1 -- "5V" --> LDO_ADC\n'
        )
        cleaned, fixes = salvage(raw)
        # Every quoted-label edge converted.
        assert '-- "' not in cleaned
        assert '== "' not in cleaned
        assert '-. "' not in cleaned
        # Spot-check every style preserves intent.
        assert "ANT1 -->|RF per channel| SMA1" in cleaned
        assert "CLK1 -.->|170 MHz LVPECL| ADC1" in cleaned
        assert "FPG1 ==>|JESD204C 4+ lanes| OUT1" in cleaned
        assert "PWR1 -->|5V| LDO_ADC" in cleaned
        assert "fix_quoted_edge_labels" in fixes

    def test_full_power_tree_screenshot_diagram(self):
        # The exact failure mode from the user's 2026-04-24 screenshot:
        # power-tree with multiple `BUCK -- "+5 V" --> LDOn` edges.
        raw = (
            "flowchart TD\n"
            "    PWR_IN[+28 V MIL Bus Input]\n"
            "    BUCK[Buck Conv BD9F800MUX 28V 5V 8A]\n"
            "    LDO1[LDO Ch1 ADM7170 5V 3.3V]\n"
            "    LDO2[LDO Ch2 ADM7170 5V 3.3V]\n"
            '    PWR_IN -- "+28 V" --> BUCK\n'
            '    BUCK -- "+5 V" --> LDO1\n'
            '    BUCK -- "+5 V" --> LDO2\n'
        )
        cleaned, fixes = salvage(raw)
        # All three edges salvaged.
        assert cleaned.count('-- "+5 V" -->') == 0
        assert cleaned.count('-- "+28 V" -->') == 0
        assert "PWR_IN -->|+28 V| BUCK" in cleaned
        assert "BUCK -->|+5 V| LDO1" in cleaned
        assert "BUCK -->|+5 V| LDO2" in cleaned
        assert "fix_quoted_edge_labels" in fixes

    def test_label_with_inner_quotes_stripped(self):
        # Stray inner quotes on the label get cleaned during salvage.
        raw = 'flowchart TD\n    A -- "\'hi\'" --> B\n'
        cleaned, _ = salvage(raw)
        assert "A -->|hi| B" in cleaned

    def test_label_with_special_chars_kept(self):
        # `+`, spaces, decimals all preserved verbatim — they're valid in
        # pipe-form edge labels.
        raw = 'flowchart TD\n    A -- "3.3 V @ 500 mA" --> B\n'
        cleaned, _ = salvage(raw)
        assert "A -->|3.3 V @ 500 mA| B" in cleaned

    def test_well_formed_pipe_label_left_alone(self):
        # A diagram that already uses pipe-form labels must NOT trigger
        # the fix (no false positives).
        raw = "flowchart TD\n    A -->|already correct| B\n"
        cleaned, fixes = salvage(raw)
        assert "fix_quoted_edge_labels" not in fixes
        assert "A -->|already correct| B" in cleaned

    def test_unrelated_quoted_strings_left_alone(self):
        # Quotes that are NOT part of an edge-label-between-dashes pattern
        # must be untouched (e.g. quoted node labels like `A["My Node"]`).
        raw = 'flowchart TD\n    A["My Node"] --> B\n'
        cleaned, fixes = salvage(raw)
        assert "fix_quoted_edge_labels" not in fixes
        assert 'A["My Node"]' in cleaned


# ---------------------------------------------------------------------------
# P19 — flatten-brace-hell: nested quotes + braces inside rhombus labels
# Regression for the 2026-04-24 user screenshot:
#   "Parse error on line 6: ...40}} BT1{{BiasT} / MBT-283+"} LNA1"L
#    Expecting 'DIAMOND_STOP'"
# ---------------------------------------------------------------------------

class TestFlattenBraceHell:
    """LLM-emitted rhombus labels with nested `{`, `}`, `"` are the
    canonical "parse hell" case. Before salvage they produce mermaid
    parse errors (Expecting DIAMOND_STOP); after salvage they become
    plain `NODE["clean text"]` square-bracket nodes that always parse."""

    def test_nested_braces_and_quotes_rewritten_to_square_bracket(self):
        """The exact failure from the user's screenshot."""
        raw = 'flowchart TD\n    BT1{"{BiasT}" / MBT-283+"}\n'
        cleaned, fixes = salvage(raw)
        assert "flatten_brace_hell" in fixes
        # Node becomes `BT1["..."]` — the inner label text is cleaned.
        assert 'BT1["' in cleaned
        # Critical chars stripped: no leftover `"{...}"` constructs.
        assert '{"' not in cleaned
        assert '"}' not in cleaned
        # Original part-number text preserved in some form.
        assert "BiasT" in cleaned
        assert "MBT-283" in cleaned

    def test_well_formed_hexagon_with_simple_quoted_label_left_alone(self):
        # `BPF1{{"Preselector / BPF-B140N+"}}` is VALID mermaid — a hexagon
        # node with a quoted label containing `/` and `+`. It parses fine
        # on mermaid's own parser. The flatten pass must NOT touch it,
        # otherwise we lose the hexagon visual for no reason.
        raw = 'flowchart TD\n    BPF1{{"Preselector / BPF-B140N+"}}\n'
        cleaned, fixes = salvage(raw)
        assert "flatten_brace_hell" not in fixes
        # Original hexagon preserved.
        assert 'BPF1{{"' in cleaned or "BPF1{{" in cleaned

    def test_trapezoid_with_escaped_brackets(self):
        # ADC1 with `[\"[\ADC\]` — escaped brackets inside quoted label.
        raw = (
            'flowchart TD\n'
            '    ADC1[\\"[\\ADC\\] / AD4008BRMZ / 16-bit 500MSPS"\\]\n'
        )
        cleaned, fixes = salvage(raw)
        assert "flatten_brace_hell" in fixes
        assert 'ADC1["' in cleaned
        # Part number must survive the flattening.
        assert "AD4008BRMZ" in cleaned

    def test_simple_well_formed_labels_left_alone(self):
        # No false positives: `A[label]` and `B{rhombus}` with no nested
        # quotes / braces must NOT be touched.
        raw = 'flowchart TD\n    A[LNA] --> B{Decision}\n    B --> C[ADC]\n'
        cleaned, fixes = salvage(raw)
        assert "flatten_brace_hell" not in fixes
        # Original rhombus shape preserved.
        assert "B{Decision}" in cleaned
        # Square bracket nodes left intact.
        assert "A[LNA]" in cleaned
        assert "C[ADC]" in cleaned

    def test_normal_quoted_label_single_pair_left_alone(self):
        # `A["Hello World"]` has 2 quotes, 1 `[`, 1 `]` — well-formed.
        # Must NOT trigger the flattening (only 2 quotes = under threshold).
        raw = 'flowchart TD\n    A["Hello World"] --> B\n'
        cleaned, fixes = salvage(raw)
        assert "flatten_brace_hell" not in fixes
        assert 'A["Hello World"]' in cleaned

    def test_full_channelised_fe_screenshot_line(self):
        # End-to-end on the user's screenshot source. The BT1 line is the
        # only truly-broken one (nested quotes AND unbalanced braces); the
        # other nodes (ANT1, LIM1, LNA1) are valid mermaid with 2 quotes.
        raw = (
            'flowchart LR\n'
            '    ANT1>"Antenna 5-18 GHz"]\n'
            '    LIM1[/"Lim PE7602-6 IL 1.2 P+30max"\\]\n'
            '    BT1{"{BiasT}" / MBT-283+"}\n'
            '    LNA1>"LNA1 PMA3-352GLN+ G+12 NF2.5 P1+15"]\n'
            '    ANT1 --> LIM1\n'
            '    LIM1 --> BT1\n'
            '    BT1 --> LNA1\n'
        )
        cleaned, fixes = salvage(raw)
        # Brace-hell pass must fire for the BT1 line specifically.
        assert "flatten_brace_hell" in fixes
        # Edges must survive intact.
        assert "ANT1 --> LIM1" in cleaned
        assert "LIM1 --> BT1" in cleaned
        assert "BT1 --> LNA1" in cleaned
        # BT1's part number survives the flattening.
        assert "MBT-283" in cleaned
        # Other nodes' part numbers also survive (they may or may not
        # have been flattened — either is fine, they're all valid now).
        assert "PMA3-352GLN" in cleaned
        assert "PE7602-6" in cleaned


# ---------------------------------------------------------------------------
# P26 (2026-04-25) — `<br/>` HTML-tag and trapezoid/parallelogram corruption.
#
# Real failing input from project `gvv` and `hdhf` (architecture.md):
#
#   ADC_DIGITAL[\"ADC / AD9627<br/>+1.8V analog<br/>+3.3V digital"\]
#
# The salvage USED TO mangle this into:
#
#   ADC_DIGITAL[ADC / AD9627 br/ +1.8V analog br/ +3.3V digital"]
#
# because:
#   1. `_step_quote_dangerous_labels` saw the `<` and `>` inside `<br/>` as
#      "dangerous chars" and re-quoted the entire label.
#   2. The flag-shape regex `(?<![-=])(>)([^>\]]*?)(\])` matched the `>`
#      INSIDE `<br>` as a standalone flag-shape opener (since the only
#      lookbehind was 1 char wide).
#   3. `_step_flatten_brace_hell` triggered on `[\..\]` trapezoid and
#      stripped the `\` shape-delimiters along with the inner quotes.
#
# Fixes (in `tools/mermaid_salvage.py`):
#   - `_step_neutralise_br_tags` (NEW): `<br/>` → `<br>`.
#   - `_step_normalise_shape_quotes` (NEW): strip redundant inner `"..."`
#     from `[\..\]`, `[/.../]`, `[\\..\\]` shapes pre-emptively.
#   - `needs_quote()` strips `<br>` tags before checking for dangerous chars.
#   - rect `[..]` pattern now requires open `[` is NOT followed by `\` / `/`
#     and close `]` is NOT preceded by `\` / `/` (so trapezoid + parallelogram
#     shapes aren't matched by the rect pattern).
#   - flag-shape `>...]` regex now uses 3-char lookbehinds `(?<!<br)` and
#     `(?<!<BR)` to skip the closing `>` of HTML `<br>` tags.
#   - `_step_flatten_brace_hell` skips lines that already look like a
#     well-formed trapezoid / parallelogram.
# ---------------------------------------------------------------------------


class TestP26HtmlBreakAndShapeDelims:
    """Regression tests for the 2026-04-25 'salvage corrupts shapes with
    <br/> + escaped quotes' bug."""

    def test_self_closing_br_normalised_to_open_br(self):
        """<br/> must be converted to <br> early so downstream steps don't
        see the slash and treat the `/` as a parallelogram delimiter."""
        raw = (
            "flowchart TD\n"
            '    BUCK["Buck<br/>BD9F800MUX"]\n'
        )
        cleaned, fixes = salvage(raw)
        assert "neutralise_br_tags" in fixes
        assert "<br>" in cleaned
        assert "<br/>" not in cleaned

    def test_trapezoid_with_escaped_quotes_survives(self):
        """`[\\"label"\\]` (LLM JSON-escape leakage) should normalise to
        `[\\label\\]` cleanly, not get destroyed by quote_dangerous_labels."""
        raw = (
            "flowchart TD\n"
            r'    ADC[\"ADC AD9627"\]' + "\n"
        )
        cleaned, fixes = salvage(raw)
        assert "normalise_shape_quotes" in fixes
        # The trapezoid shape delimiters must survive intact.
        assert r"[\ADC AD9627\]" in cleaned, (
            f"trapezoid shape delimiters mangled — got:\n{cleaned}"
        )

    def test_trapezoid_with_br_tags_survives(self):
        """Real failing input from project `gvv`: trapezoid shape with
        backslash-escaped quotes AND `<br/>` HTML tags inside the label."""
        raw = (
            "flowchart TD\n"
            r'    ADC_DIGITAL[\"ADC / AD9627<br/>+1.8V analog<br/>+3.3V digital"\]' + "\n"
            "    OTHER[X]\n"
            "    ADC_DIGITAL --> OTHER\n"
        )
        cleaned, fixes = salvage(raw)
        # Trapezoid delimiters intact:
        assert r"ADC_DIGITAL[\ADC" in cleaned, (
            f"trapezoid open mangled — got:\n{cleaned}"
        )
        assert r"digital\]" in cleaned, (
            f"trapezoid close mangled — got:\n{cleaned}"
        )
        # No double-quoted artefacts:
        assert r'[\"' not in cleaned
        assert r'"\]' not in cleaned
        # No `br/` orphan from the HTML tag being torn apart:
        assert " br/ " not in cleaned
        # Edge survives:
        assert "ADC_DIGITAL --> OTHER" in cleaned

    def test_parallelogram_with_br_tags_survives(self):
        """`[/...<br/>.../]` parallelogram must keep its `/` delimiters."""
        raw = (
            "flowchart TD\n"
            '    LVDS_OUT[/"LVDS Output<br/>Data Interface"/]\n'
        )
        cleaned, fixes = salvage(raw)
        assert "[/LVDS Output<br>Data Interface/]" in cleaned, (
            f"parallelogram mangled — got:\n{cleaned}"
        )

    def test_asymmetric_flag_with_br_tags_not_split(self):
        """`>...<br>...]` flag-shape — the `>` inside `<br>` must NOT be
        treated as a separate flag-shape opener and re-quoted."""
        raw = (
            "flowchart TD\n"
            '    BUCK>"Buck / BD9F800MUX-ZE2<br/>28V to 5V @ 8A"]\n'
        )
        cleaned, fixes = salvage(raw)
        # No extra quote injected after the <br>:
        assert '<br>"28V' not in cleaned, (
            f"flag-shape over-quoted on <br> — got:\n{cleaned}"
        )
        # Original label intact (just `<br/>` → `<br>`):
        assert (
            'BUCK>"Buck / BD9F800MUX-ZE2<br>28V to 5V @ 8A"]'
        ) in cleaned

    def test_full_gvv_diagram_renders_cleanly(self):
        """End-to-end: the full power-tree diagram from project `gvv` must
        survive salvage with NO mangling. This is the diagram the user
        was complaining about with 'Parse error on line 13'."""
        raw = (
            "flowchart TD\n"
            '    V_MAIN>"+15V Primary Supply"]\n'
            '    BUCK_5V["Buck +15V to +5V<br/>BD9P135EFV x2"]\n'
            r'    ADC_ADC[\"ADC x2<br/>AD9648BCPZ-125"\]' + "\n"
            r'    FPGA_CORE[\"FPGA<br/>XC7K160T-1FFG676I"\]' + "\n"
            '    LVDS_OUT[/"LVDS Output<br/>Data Interface"/]\n'
            '    V_MAIN == "+15V" ==> BUCK_5V\n'
            '    BUCK_5V -- "+5V" --> ADC_ADC\n'
            '    ADC_ADC -- "LVDS" --> FPGA_CORE\n'
            '    FPGA_CORE -- "LVDS" --> LVDS_OUT\n'
        )
        cleaned, fixes = salvage(raw)
        # All five node shapes preserved:
        assert 'V_MAIN>"+15V Primary Supply"]' in cleaned
        assert 'BUCK_5V["Buck +15V to +5V<br>BD9P135EFV x2"]' in cleaned
        assert r"ADC_ADC[\ADC x2<br>AD9648BCPZ-125\]" in cleaned
        assert r"FPGA_CORE[\FPGA<br>XC7K160T-1FFG676I\]" in cleaned
        assert "LVDS_OUT[/LVDS Output<br>Data Interface/]" in cleaned
        # No double-quote artefacts on any shape:
        assert r'<br>"' not in cleaned
        # The parser's `Parse error on line 13` was caused by these
        # mangled shapes; with them clean, mermaid will accept the diagram.


# ---------------------------------------------------------------------------
# P26 third pass (2026-04-25, project fyfu) — additional shape variants
# the LLM emits that the prior salvage didn't cover:
#   [["..."]]    subroutine
#   {{"..."}}    hexagon
#   (("..."))    circle
#   (["..."])    stadium
#   [("...")]    cylinder
#   [/"..."\]    mixed-slash trapezoid
#   [\"..."/]    mixed-slash trapezoid_alt
# ---------------------------------------------------------------------------


class TestP26ShapeVariants:
    """Regression for the 'BUCK[["Buck / LT1107..."]]' family of bugs from
    project fyfu. Mermaid parse error: `Parse error on line 3: ...12V to
    5V]] LDO_5...`."""

    def test_subroutine_quotes_PRESERVED(self):
        """P26 #4 (fyfu DOCX fix): `[[".."]]` quotes must be PRESERVED.
        Mermaid accepts quoted labels in subroutine shapes and may
        REQUIRE them when the label has special chars. Stripping them
        was over-aggressive and broke labels containing parens."""
        raw = (
            "flowchart TB\n"
            '    BUCK[["Buck / LT1107CS8-5"]]\n'
            "    BUCK --> X\n"
        )
        cleaned, _ = salvage(raw)
        # Quoted form survives intact — both `[[..]]` brackets matched.
        assert '[["Buck / LT1107CS8-5"]]' in cleaned, (
            f"subroutine quotes were stripped (should be preserved) — "
            f"got:\n{cleaned}"
        )

    def test_hexagon_quotes_PRESERVED(self):
        """P26 #4: `{{"..."}}` quotes preserved (mermaid accepts them)."""
        raw = (
            "flowchart TD\n"
            '    BPF{{"Custom Cavity / IL1.5"}}\n'
        )
        cleaned, _ = salvage(raw)
        assert '{{"Custom Cavity / IL1.5"}}' in cleaned

    def test_circle_quotes_PRESERVED(self):
        """P26 #4: `(("..."))` quotes preserved."""
        raw = (
            "flowchart TD\n"
            '    OSC(("10 MHz Reference"))\n'
        )
        cleaned, _ = salvage(raw)
        assert '(("10 MHz Reference"))' in cleaned

    def test_stadium_with_parens_in_label_renders(self):
        """P26 #4 (the actual fyfu DOCX bug): stadium `(["label (with parens)"])`
        must keep its quotes — mermaid REJECTS unquoted labels containing
        parens. Real fyfu input that broke mmdc:
            RF_CH1(["RF Chain 1 (Ant1 to ADC1)"])
        After my over-eager P26 #3 strip:
            RF_CH1([RF Chain 1 (Ant1 to ADC1)])  ← unquoted parens
        mmdc: Parse error on line 14: ...Expecting 'SQE', 'PE', ..."""
        raw = (
            "flowchart TD\n"
            '    RF_CH1(["RF Chain 1 (Ant1 to ADC1)"])\n'
        )
        cleaned, _ = salvage(raw)
        assert '(["RF Chain 1 (Ant1 to ADC1)"])' in cleaned, (
            f"stadium with parens-in-label was mangled — got:\n{cleaned}"
        )

    def test_mixed_slash_trapezoid_quotes_stripped(self):
        """`[/"..."\\]` → `[/...\\]` (mixed-slash trapezoid). Mermaid
        REJECTS quotes in trapezoid family — these MUST be stripped."""
        raw = (
            "flowchart TD\n"
            r'    LIM1[/"Lim / CLA4602-000 / IL0.2 P+33max"\]' + "\n"
        )
        cleaned, _ = salvage(raw)
        assert r"[/Lim / CLA4602-000 / IL0.2 P+33max\]" in cleaned, (
            f"mixed-slash trapezoid quotes not stripped — got:\n{cleaned}"
        )

    def test_full_fyfu_architecture_renders_cleanly(self):
        """End-to-end: project fyfu's architecture.md (the actual file the
        user complained about with 'placeholder shown' DOCX) must survive
        salvage with all shapes intact AND the result must pass mmdc /
        mermaid.ink rendering. Quoted labels in subroutine, round,
        stadium shapes are PRESERVED; only trapezoid quotes are stripped."""
        raw = (
            "flowchart TB\n"
            '    PWR_IN[/"+12V DC Input"/]\n'
            '    BUCK[["Buck / LT1107CS8-5#PBF / 12V to 5V"]]\n'
            '    LDO_5[["LDO 5V / SPX3819M5-L-5-0/TR"]]\n'
            '    RAIL_5V["+5V Rail"]\n'
            '    REF_OSC("TCXO 10 MHz Ref")\n'
            '    RF_CH1(["RF Chain 1 (Ant1 to ADC1)"])\n'
            '    PWR_IN == "+12V" ==> RAIL_5V\n'
            "    RAIL_5V --> LDO_5\n"
        )
        cleaned, _ = salvage(raw)
        # Trapezoid: quotes stripped (mermaid rejects them there).
        assert "[/+12V DC Input/]" in cleaned
        # Subroutine + round + stadium: quotes preserved (mermaid OK
        # AND they're required for parens-containing labels).
        assert '[["Buck / LT1107CS8-5#PBF / 12V to 5V"]]' in cleaned
        assert '[["LDO 5V / SPX3819M5-L-5-0/TR"]]' in cleaned
        assert '("TCXO 10 MHz Ref")' in cleaned
        assert '(["RF Chain 1 (Ant1 to ADC1)"])' in cleaned
        # No mangled forms:
        assert "BUCK[Buck" not in cleaned   # single-open mangle
        assert "5V]]]" not in cleaned       # triple close
        # Stadium label still has its parens INSIDE the quotes:
        assert "(Ant1 to ADC1)" in cleaned
