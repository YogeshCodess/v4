"""
Back-compat Mermaid salvager — for raw LLM-emitted text we still accept.

New code should emit structured `BlockDiagramSpec` and pass through
`tools.mermaid_render.render_block_diagram()` — output is then valid by
construction. This module exists for:

  1. `architecture_mermaid` and other free-form fields we haven't migrated
     yet.
  2. Legacy DB rows whose `block_diagram_mermaid` is raw Mermaid text.
  3. The P1 finalize retry loop, where the LLM sometimes re-emits raw
     Mermaid on a retry even after we ask for structured output.

Salvage strategy (applied in order, each step independent):

  A. Unicode ASCIIfication — Ohm/deg/u/em-dash/arrows → ASCII equivalents.
  B. Arrow normalisation — `==>`, `->`, `——>`, unicode arrows → `-->`.
  C. Frontmatter strip — `%%{init ...}%%`, `%% comments`, BOM.
  D. Direction scrub — `direction LR` at top level → removed (belongs in
     subgraph only).
  E. Diagram-type fixup — `graph TD` → `flowchart TD`; ensure `flowchart X`
     is on line 1.
  F. Bare-shape IDs — `>Ant1]` starting a line → auto-prefix `_n0>"Ant1"]`.
  G. Label quoting — wrap any node `[...]`/`(...)`/`{{...}}` label that
     contains punctuation the tokeniser dislikes (`<>"#|`) in `"..."`.
  H. Bracket balancing — auto-close unclosed `[` at end of line.
  I. `end` keyword isolation — ensure `subgraph ... end` closes on its
     own line.
  J. Final sanity gate — if we still can't find `flowchart` on line 1,
     fall back to a placeholder diagram.

The function is pure — no I/O, no side effects — and returns both the
cleaned text AND the list of fixes applied so callers can log what was
rescued. That log is critical for diagnosing LLM regressions: if we see
the same fix fire 100 times/day, the prompt needs tightening.

Public API:
    salvage(raw: str) -> tuple[str, list[str]]
    FALLBACK_DIAGRAM       — the safe minimal diagram used as last resort
"""
from __future__ import annotations

import re
from typing import Optional

__all__ = ["salvage", "FALLBACK_DIAGRAM"]


# The placeholder we emit when nothing else works — always parses, always
# renders, explicitly tells the user something went wrong.
FALLBACK_DIAGRAM = (
    "flowchart LR\n"
    '    ERR["diagram could not be rendered"]\n'
    '    HINT["ask P1 to regenerate the block diagram"]\n'
    "    ERR --> HINT\n"
)


# Non-ASCII → ASCII mapping. Same table as mermaid_render; duplicated here
# to keep the salvager a standalone module that doesn't depend on the
# renderer's internals.
_NON_ASCII_MAP: dict[str, str] = {
    "\u03A9": "Ohm", "\u2126": "Ohm",
    "\u00B0": "deg", "\u00B5": "u",
    "\u2013": "-", "\u2014": "-",
    "\u2018": "'", "\u2019": "'",
    "\u201C": "'", "\u201D": "'",
    "\u2264": "<=", "\u2265": ">=",
    "\u00B1": "+-",
    "\u2192": "-->", "\u2190": "<--",
    "\u2022": "*", "\u00B7": "*",
    "\ufeff": "",  # BOM
}

# Shape-opening tokens. Order matters: longer tokens first so we don't
# match `[` when `[[` or `[/` is present.
_SHAPE_OPENERS: tuple[str, ...] = (
    "[[", "[/", "[\\", "[(",  # 2-char brackets
    "{{",                      # hexagon
    "((",                      # circle
    "[", "{", "(", ">",        # 1-char
)

_DIAGRAM_TYPES: tuple[str, ...] = (
    "flowchart", "sequencediagram", "classdiagram", "statediagram",
    "erdiagram", "gantt", "pie", "gitgraph", "mindmap", "timeline",
    "journey", "quadrantchart", "requirementdiagram", "c4context",
)


# ---------------------------------------------------------------------------
# Step helpers — each returns (text, fix_applied_or_None)
# ---------------------------------------------------------------------------

def _step_asciify(text: str) -> tuple[str, Optional[str]]:
    """Step A — replace non-ASCII glyphs with ASCII equivalents."""
    original = text
    for glyph, repl in _NON_ASCII_MAP.items():
        text = text.replace(glyph, repl)
    # Drop any remaining non-ASCII as a safety net.
    text2 = "".join(ch if ord(ch) < 128 else "" for ch in text)
    if text2 != original:
        return text2, "asciify"
    return text, None


def _step_normalise_arrows(text: str) -> tuple[str, Optional[str]]:
    """Step B — `==>`, `->`, `——>` → `-->`. Preserves `-.->` (dotted)
    because that's a legitimate Mermaid form we don't want to flatten."""
    original = text
    # `==>` (thick arrow) is valid Mermaid but often emitted where plain
    # arrow was meant. Leave it alone — the renderer uses `==>` for style=thick.
    # Single `->` is NOT valid — upgrade.
    text = re.sub(r"(?<![-=.])->(?!>)", "-->", text)
    # Em-dash arrows.
    text = re.sub(r"—+>", "-->", text)
    if text != original:
        return text, "normalise_arrows"
    return text, None


def _step_neutralise_br_tags(text: str) -> tuple[str, Optional[str]]:
    """Step B-post (2026-04-25) — `<br/>` and `<br />` self-closing HTML
    tags → `<br>` (mermaid's accepted form).

    Why this matters: mermaid does accept `<br>` inside double-quoted
    labels for line-break rendering, but the SELF-CLOSING `<br/>` form
    confuses our own `_step_quote_dangerous_labels` step downstream — the
    `<` and `>` chars inside `<br/>` make it think the label has hostile
    characters, triggers a re-quote pass, and produces broken output like
    `LABEL<br/>"text"<br/>"more"` (extra quotes around every <br>).

    Real failing input from project `gvv` / `hdhf` (2026-04-25):
        ADC_DIGITAL[\\"ADC / AD9627<br/>+1.8V analog<br/>+3.3V digital"\\]
    After this step the `<br/>` is normalised so the dangerous-char
    detector doesn't fire and the trapezoid survives later steps intact.
    """
    original = text
    text = re.sub(r"<br\s*/>", "<br>", text, flags=re.IGNORECASE)
    if text != original:
        return text, "neutralise_br_tags"
    return text, None


def _step_normalise_shape_quotes(text: str) -> tuple[str, Optional[str]]:
    """Step B-post-2 (2026-04-25) — strip REDUNDANT internal quotes from
    asymmetric / trapezoid / parallelogram shapes that mermaid does NOT
    require quotes on:

        ADC_DIGITAL[\\"ADC / AD9627"\\]   →  ADC_DIGITAL[\\ADC / AD9627\\]
        LVDS_OUT[/"LVDS data"/]           →  LVDS_OUT[/LVDS data/]
        P28V>"+28 VDC Primary"]           →  P28V>+28 VDC Primary]

    The LLM (when JSON-serialising its tool input and emitting Mermaid as
    a string field) frequently leaks JSON-escape artefacts like `[\\"...\\"\\]`
    into the text. Mermaid's own parser is tolerant of these in some
    versions but our `_step_flatten_brace_hell` heuristic — which strips
    backslashes, quotes, and angle brackets from over-quoted labels —
    sees the leaked `\\"` and `<br/>` chars together and over-flattens
    the entire node, mangling shape delimiters.

    Pre-stripping the redundant inner quotes here means the trapezoid
    shape stays intact through later steps and renders correctly."""
    original = text
    # Trapezoid `[\..\]` — the `\` chars here are the SHAPE delimiters, not
    # escape characters. The `[\"label"\]` form has redundant inner quotes.
    text = re.sub(
        r'\[\\\s*"([^"]*)"\s*\\\]',
        lambda m: f'[\\{m.group(1).strip()}\\]',
        text,
    )
    # Parallelogram `[/.../]` — same redundant-quote pattern.
    text = re.sub(
        r'\[/\s*"([^"]*)"\s*/\]',
        lambda m: f'[/{m.group(1).strip()}/]',
        text,
    )
    # Inverted parallelogram `[\\..\\]` (two backslashes per side).
    text = re.sub(
        r'\[\\\\\s*"([^"]*)"\s*\\\\\]',
        lambda m: f'[\\\\{m.group(1).strip()}\\\\]',
        text,
    )
    # P26 #4 (2026-04-25, fyfu DOCX fix) — additional MIXED-SLASH
    # trapezoid variants. Mermaid's `[/..\]` family is the ONLY shape
    # group that genuinely REJECTS inner quotes.
    text = re.sub(
        r'\[/\s*"([^"]*)"\s*\\\]',
        lambda m: f'[/{m.group(1).strip()}\\]',
        text,
    )
    text = re.sub(
        r'\[\\\s*"([^"]*)"\s*/\]',
        lambda m: f'[\\{m.group(1).strip()}/]',
        text,
    )
    # NOTE — DO NOT strip quotes from `[[..]]`, `{{..}}`, `((..))`,
    # `([..])`, `[(..)]`. Mermaid's parser ACCEPTS quoted labels in
    # these shapes — and REQUIRES them when the label contains parens
    # or other special chars. Earlier code stripped them aggressively,
    # which turned `RF_CH1(["RF Chain 1 (Ant1 to ADC1)"])` into
    # `RF_CH1([RF Chain 1 (Ant1 to ADC1)])` — mermaid then choked on
    # the unquoted inner `(Ant1 to ADC1)`:
    #   "Parse error on line 14: ...RF_CH1([RF Chain 1 (Ant1 to ADC1)])
    #    -----------------------^ Expecting 'SQE', 'PE', ..."
    # Quoted labels render correctly in mermaid.js / mermaid.ink / mmdc
    # for ALL these shapes; leaving the LLM's quotes alone is safer.
    if text != original:
        return text, "normalise_shape_quotes"
    return text, None


def _step_strip_parens_in_asymmetric_shapes(
    text: str,
) -> tuple[str, Optional[str]]:
    """Step B-post-3 (2026-05-03) — strip `(`, `)`, `[`, `]`, `{`, `}` from
    inner labels of the parallelogram / trapezoid / mixed-slash / flag
    shape family.

    Mermaid REJECTS quoted labels on these shapes (the only shape group
    that does), so we cannot rescue parser-hostile chars by quoting (the
    way `_step_quote_dangerous_labels` does for rect / round / etc.).
    Parens / brackets / braces inside the unquoted label re-trigger the
    round-shape / rect-shape / rhombus-shape parsers and produce errors
    like:

        Parse error on line 15: ...10 MHz Ref<br>(SMA Input)/]
        ----------------------^ Expecting 'SQE', 'DOUBLECIRC'

    Real failing input from the user (2026-05-03, last-month recurring
    bug): `[/External 10 MHz Ref<br>(SMA Input)/]`.

    `<br>` is preserved through a placeholder swap; `_step_neutralise_br_tags`
    has already normalised any `<br/>` to `<br>` upstream.
    """
    BR = "\x01BR\x01"

    def _strip(inner: str) -> str:
        protected = re.sub(r"<br\s*/?>", BR, inner, flags=re.IGNORECASE)
        cleaned = re.sub(r"[\(\)\[\]\{\}]", " ", protected)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return cleaned.replace(BR, "<br>")

    original = text
    # Parallelogram `[/.../]`
    text = re.sub(
        r"\[/([^/\]]*)/\]",
        lambda m: f"[/{_strip(m.group(1))}/]",
        text,
    )
    # Trapezoid `[\..\]`
    text = re.sub(
        r"\[\\([^\\\]]*)\\\]",
        lambda m: f"[\\{_strip(m.group(1))}\\]",
        text,
    )
    # Mixed-slash trapezoid `[/..\]`
    text = re.sub(
        r"\[/([^\\\]]*)\\\]",
        lambda m: f"[/{_strip(m.group(1))}\\]",
        text,
    )
    # Mixed-slash trapezoid `[\../]`
    text = re.sub(
        r"\[\\([^/\]]*)/\]",
        lambda m: f"[\\{_strip(m.group(1))}/]",
        text,
    )
    # Flag `>label]` — only when the `>` is at start-of-token (not part of
    # `-->` / `==>` arrow and not the `>` closing an HTML `<br>`).
    text = re.sub(
        r"(?<![-=<])(?<!<br)>([^>\]\n]+)\]",
        lambda m: f">{_strip(m.group(1))}]",
        text,
    )

    if text != original:
        return text, "strip_parens_in_asymmetric_shapes"
    return text, None


def _step_strip_frontmatter(text: str) -> tuple[str, Optional[str]]:
    """Step C — drop `%%{init ...}%%` and `%% comment` lines."""
    original = text
    text = re.sub(r"%%\{[\s\S]*?\}%%\s*", "", text)
    # Strip line-comments but preserve our own `%% ERROR:` markers from
    # the renderer's soft-error mode (they're still valid Mermaid comments).
    if text != original:
        return text, "strip_frontmatter"
    return text, None


def _step_strip_direction(text: str) -> tuple[str, Optional[str]]:
    """Step D — remove bare `direction LR` lines that are outside a
    subgraph. Inside a subgraph Mermaid does accept them; we only strip
    lines where the preceding non-blank line isn't `subgraph`."""
    lines = text.split("\n")
    out: list[str] = []
    changed = False
    inside_subgraph = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("subgraph "):
            inside_subgraph = True
            out.append(line)
            continue
        if stripped == "end":
            inside_subgraph = False
            out.append(line)
            continue
        if (
            re.match(r"^\s*direction\s+(LR|TD|TB|RL|BT)\s*$", line)
            and not inside_subgraph
        ):
            changed = True
            continue  # drop this line
        out.append(line)
    return ("\n".join(out), "strip_direction" if changed else None)


def _step_normalise_header(text: str) -> tuple[str, Optional[str]]:
    """Step E — ensure the first non-blank line starts with a valid
    diagram type (`flowchart LR`, etc.). If it says `graph X`, upgrade to
    `flowchart X`. If it's missing entirely, prepend `flowchart LR`."""
    lines = text.split("\n")
    # Find first non-blank line.
    first_idx = next((i for i, ln in enumerate(lines) if ln.strip()), 0)
    first = lines[first_idx].strip().lower()

    if first.startswith("graph "):
        lines[first_idx] = re.sub(
            r"^\s*graph\s+", "flowchart ", lines[first_idx], count=1,
        )
        return "\n".join(lines), "normalise_header_graph_to_flowchart"

    if first.startswith(_DIAGRAM_TYPES):
        return text, None

    # Prepend a sensible default.
    return "flowchart LR\n" + text, "prepend_flowchart_header"


def _step_fix_bare_shapes(text: str) -> tuple[str, Optional[str]]:
    """Step F — `>Ant1]`, `[/SMA/]`, `((Circle))` starting a line (no
    preceding node-id token) → prefix with a synthetic id `_n{i}` so
    Mermaid's parser accepts it."""
    lines = text.split("\n")
    changed = False
    synth_idx = 0
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        # Match any shape opener at the start.
        matched = False
        for opener in _SHAPE_OPENERS:
            if stripped.startswith(opener):
                synth_idx += 1
                out.append(f"{indent}_n{synth_idx}{stripped}")
                changed = True
                matched = True
                break
        if not matched:
            out.append(line)
    return ("\n".join(out), "fix_bare_shapes" if changed else None)


def _step_quote_dangerous_labels(text: str) -> tuple[str, Optional[str]]:
    """Step G — find node-label contents that contain tokeniser-hostile
    chars (`<`, `>`, `#`, `|`, pipe, unescaped `(`) and wrap in `"..."`.

    We only touch labels inside `[...]` / `(...)` / `{{...}}` / `{...}` /
    `[/.../]` / `[\\...\\]` / `>...]`. To avoid double-wrapping, we skip
    labels that are already quoted."""
    changed_any = False

    # Each pattern captures (open_delim, inner, close_delim).
    # P26 (2026-04-25): the bare `[..]` rect pattern was greedily matching
    # `[\..\]` trapezoid and `[/.../]` parallelogram shapes too, then
    # re-quoting their labels (which contained `<br>` HTML breaks → caught
    # by the dangerous-char detector). Result: `[\label\]` got mangled
    # into `["\label\"]` — broken on render. Now the rect pattern requires
    # the open `[` is NOT followed by `\` / `/` and the close `]` is NOT
    # preceded by `\` / `/`, leaving shape variants intact.
    patterns: tuple[tuple[re.Pattern[str], str, str], ...] = (
        (re.compile(r"(\[\[)([^\]\[]*?)(\]\])"), "[[", "]]"),
        (re.compile(r"(\{\{)([^}{]*?)(\}\})"), "{{", "}}"),
        (re.compile(r"(\(\()([^)(]*?)(\)\))"), "((", "))"),
        (re.compile(r"(\[/)([^/\]]*?)(/\])"), "[/", "/]"),
        (re.compile(r"(\[/)([^\\\]]*?)(\\\])"), "[/", "\\]"),
        (re.compile(r"(\[\\)([^\\\]]*?)(\\\])"), "[\\", "\\]"),
        (re.compile(r"(\[)(?![\\/])([^\[\]]*?)(?<![\\/])(\])"), "[", "]"),
        (re.compile(r"(\{)([^{}]*?)(\})"), "{", "}"),
        (re.compile(r"(\()([^()]*?)(\))"), "(", ")"),
        # Flag: `>label]` — but only when not already part of `-->` arrow
        # AND not the `>` inside an HTML `<br>` tag inside a label.
        # P26 (2026-04-25): the previous (?<![-=]) lookbehind only checked
        # ONE char back, so for `<br>` the `>` at the end was matched as
        # a flag-shape opener (since the preceding char `r` is alnum and
        # not `-`/`=`). We now use a 3-char lookbehind `(?<!<br)` and
        # `(?<!<BR)` to skip the close-bracket of HTML `<br>` tags.
        # Combined with the alnum lookbehind `(?<=[A-Za-z0-9_])`, this
        # only matches `NodeID>` patterns at the start of a node def.
        (re.compile(
            r"(?<=[A-Za-z0-9_])(?<![-=<])(?<!<br)(?<!<BR)"
            r"(>)([^>\]]*?)(\])"
        ), ">", "]"),
    )

    def needs_quote(inner: str) -> bool:
        if not inner:
            return False
        if inner.startswith('"') and inner.endswith('"'):
            return False  # already quoted
        # P26 (2026-04-25) — strip `<br>` HTML tags BEFORE checking for
        # dangerous chars. Mermaid renders `<br>` as a line break inside
        # labels and our `_step_neutralise_br_tags` has already
        # normalised any `<br/>` self-closing variant to `<br>`. Without
        # this, the `<` and `>` inside every `<br>` count as "dangerous"
        # and the label gets re-quoted — which combined with the OUTER
        # shape delimiters produces `[\"label\"\]` corruption.
        check = re.sub(r"<br\s*/?>", "", inner, flags=re.IGNORECASE)
        return bool(re.search(r'[<>#|"\'\\]', check))

    for pat, open_, close_ in patterns:
        def _sub(m: re.Match[str]) -> str:
            nonlocal changed_any
            inner = m.group(2)
            if needs_quote(inner):
                # Strip the char set that even quoted labels dislike.
                cleaned = re.sub(r'["`]', "", inner)
                changed_any = True
                return f'{m.group(1)}"{cleaned}"{m.group(3)}'
            return m.group(0)

        text = pat.sub(_sub, text)

    return (text, "quote_dangerous_labels" if changed_any else None)


def _step_close_brackets(text: str) -> tuple[str, Optional[str]]:
    """Step H — if a line opens more `[` than it closes, append closing
    brackets. Works around LLM's tendency to drop the final `]`."""
    lines = text.split("\n")
    changed = False
    out: list[str] = []
    for line in lines:
        opens = line.count("[")
        closes = line.count("]")
        if opens > closes:
            line = line + ("]" * (opens - closes))
            changed = True
        out.append(line)
    return ("\n".join(out), "close_brackets" if changed else None)


def _step_flatten_brace_hell(text: str) -> tuple[str, Optional[str]]:
    """Step I-pre-pre — some LLM emissions have nested quotes + braces
    inside rhombus node labels, e.g.:

        BT1{"{BiasT}" / MBT-283+"}
        BPF1{{"Preselector / BPF-B140N+ / IL1.5 BW140"}}
        ADC1[\\"[\\ADC\\] / AD4008BRMZ / 16-bit 500MSPS"\\]

    The main label-sanitiser below uses a non-nested regex
    `/\{([^}]*)\}/g` which grabs only up to the first `}`, mangling the
    rest and producing syntactically-garbage output the parser rejects
    with `Expecting 'DIAMOND_STOP'` (the 2026-04-24 user screenshot).

    This pass flattens any node whose label contains an inner `"` or
    `{` / `}` / `[` / `]` into a simpler `NODE[clean_label]` form.
    Regex matches `NODEID<shape-open>...<shape-close>` with a shape
    token that opens and closes the node, then normalises to a square-
    bracket node with the stripped label. The rhombus / stadium /
    trapezoid visual is lost but the diagram actually renders.
    """
    original = text
    # Match any node definition: ID followed by opener to matching closer.
    # We walk each line and rewrite problematic nodes.
    lines = text.split("\n")
    changed = False
    # Heuristic MUST be tight enough not to fire on well-formed diagrams
    # that happen to have multiple nodes on one line — e.g.
    # `A[LNA] --> B{Decision}` has 2 opens + 2 closes but is fine.
    # The failing pattern is ALWAYS characterised by ≥3 quotes in the
    # suspicious node (the LLM wraps the whole label in outer quotes AND
    # includes inner quotes — never happens in sanitary emission).
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        dq = stripped.count('"')
        # Trigger ONLY on 3+ quote marks OR backslash-escaped brackets
        # (another LLM pathology: `[\"[\ADC\]` with backslash escapes).
        has_esc_brackets = "\\[" in stripped or "\\]" in stripped or "\\\"" in stripped
        suspicious = dq >= 3 or has_esc_brackets
        if not suspicious:
            continue
        # P26 (2026-04-25) — don't fire on lines that are already a
        # well-formed trapezoid `[\..\]`, parallelogram `[/.../]`, mixed-
        # slash trapezoid `[/...\]` / `[\.../]`, inverted parallelogram
        # `[\\..\\]`, OR double-bracket subroutine `[[..]]`. The `\` and
        # `/` chars in those shapes look like "escape brackets" to the
        # heuristic above but are actually mermaid's SHAPE delimiters.
        # The earlier `_step_normalise_shape_quotes` already stripped
        # any redundant inner quotes from these forms, so the line that
        # reaches us here is parseable mermaid.
        #
        # P26 #3 (2026-04-25, project fyfu): added mixed-slash trapezoid
        # variants `[/...\]` and `[\.../]` — mermaid accepts these, the
        # LLM emits them, and `flatten_brace_hell` was mangling them to
        # `["..."]` because `\]` matched `has_esc_brackets`.
        if re.search(
            r"\b\w+\s*"
            r"(?:"
            r"\[\\[^\\\]]*\\\]"     # trapezoid    [\..\]
            r"|\[/[^/\]]*/\]"        # parallelogram [/.../]
            r"|\[\\\\[^\\\]]*\\\\\]" # inv. parallelo [\\..\\]
            r"|\[/[^\\\]]*\\\]"      # mixed trapezoid [/..\]
            r"|\[\\[^/\]]*/\]"       # mixed trapezoid [\..]/
            r"|\[\[[^\[\]]*\]\]"     # subroutine   [[..]]
            r")",
            stripped,
        ):
            continue
        # Try to match: NODEID followed by opener to outermost closer.
        # Use a non-greedy best-effort for the label content.
        m = re.match(
            r"^(\s*)([\w][\w\-]*)"              # 1=indent, 2=node-id
            r"([\[\{\(]+)"                       # 3=opener(s)
            r"(.*?)"                             # 4=label content (non-greedy)
            r"([\]\}\)]+)\s*$",                  # 5=closer(s) at end of line
            line,
        )
        if not m:
            continue
        indent, node_id, _opener, label, _closer = m.groups()
        # Extract a clean label: keep alnum + basic punctuation, drop
        # quotes, nested braces, backslashes.
        clean = re.sub(r"[\\\"'`{}\[\]<>]", " ", label)
        clean = re.sub(r"\s{2,}", " ", clean).strip(" /-")
        if not clean:
            clean = node_id  # last-resort fallback
        lines[idx] = f'{indent}{node_id}["{clean}"]'
        changed = True
    if changed:
        return "\n".join(lines), "flatten_brace_hell"
    return text, None


def _step_fix_quoted_edge_labels(text: str) -> tuple[str, Optional[str]]:
    """Step I-pre — Mermaid edge labels live INSIDE pipes (`-->|label|`),
    not inside quotes between dashes (`-- "label" -->`).  The LLM
    routinely emits the wrong shape across EVERY arrow style:

        BUCK -- "+5 V"   --> LDO1          (normal)
        A    == "thick"  ==> B              (thick)
        CLK1 -. "170 MHz".-> ADC1          (dotted)
        A    ~~ "invis"  ~~> B              (invisible)

    All four are parse errors. Convert each to the canonical pipe form:

        BUCK -->|+5 V| LDO1
        A    ==>|thick| B
        CLK1 -.->|170 MHz| ADC1
        A    ~~~|invis| B

    Regression for the 2026-04-24 power-tree + channelised-FE diagrams
    (the dotted-arrow form was the breaking case on the FE clock distribution).
    """
    original = text
    # Three arrow styles with three tail tokens:
    #   normal:  left `--`,  tail `-->`
    #   thick:   left `==`,  tail `==>`
    #   dotted:  left `-.`,  tail `.->`    (leading `.` is the anchor)
    # Previous version had tail `-.->` which is the UNLABELED dotted form
    # — when a label is between `-.` and `.->`, the tail is just `.->`.
    # That's why screenshot 2026-04-24 dotted edges still broke the parser.
    pattern = re.compile(
        r"(\b[\w][\w-]*\b)\s*"              # 1: source node
        r"(==|--|-\.)"                      # 2: arrow style (thick/normal/dotted)
        r"\s*\"([^\"]+)\"\s*"               # 3: quoted label
        r"(==>|-->|\.->)"                   # 4: arrow tail
        r"\s*(\b[\w][\w-]*\b)"              # 5: dest node
    )

    def _sub(m: re.Match[str]) -> str:
        src, style, label, tail, dst = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        # Pick the dominant arrow form:
        #   thick (`==`) > dotted (`-.`) > normal (`--`)
        if "==" in style or "==" in tail:
            arrow = "==>"
        elif "-." in style or tail.startswith("."):
            arrow = "-.->"
        else:
            arrow = "-->"
        # Strip leading/trailing whitespace + any stray quotes from the label.
        clean_label = label.strip().strip("'\"`")
        return f"{src} {arrow}|{clean_label}| {dst}"

    text = pattern.sub(_sub, text)
    if text != original:
        return text, "fix_quoted_edge_labels"
    return text, None


def _step_isolate_end(text: str) -> tuple[str, Optional[str]]:
    """Step I — if `end` appears on the same line as other content (e.g.
    `FOO[X] end`), split it so `end` is alone. Mermaid requires `end` to
    close a subgraph on its own line."""
    lines = text.split("\n")
    out: list[str] = []
    changed = False
    for line in lines:
        # Match `... end` at end of line, where `... ` has content.
        m = re.match(r"^(\s*)(.+?)\s+end\s*$", line)
        if m and m.group(2).strip() and not m.group(2).rstrip().endswith("end"):
            out.append(f"{m.group(1)}{m.group(2)}")
            out.append(f"{m.group(1)}end")
            changed = True
        else:
            out.append(line)
    return ("\n".join(out), "isolate_end" if changed else None)


def _step_trim(text: str) -> tuple[str, Optional[str]]:
    """Step final — strip trailing whitespace on each line + collapse 3+
    blank lines to 1. Keeps output tidy."""
    lines = [ln.rstrip() for ln in text.split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank <= 1:
                out.append(ln)
        else:
            blank = 0
            out.append(ln)
    return ("\n".join(out).strip() + "\n", None)  # never report


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

_STEPS = (
    _step_asciify,
    _step_normalise_arrows,
    _step_neutralise_br_tags,      # P26 (2026-04-25): <br/> -> <br>
    _step_normalise_shape_quotes,  # P26 (2026-04-25): strip extra quotes
                                    # from trapezoid/parallelogram shapes
    _step_strip_parens_in_asymmetric_shapes,  # P26 (2026-05-03): strip
                                    # parens/brackets from inner labels of
                                    # parallelogram/trapezoid/flag shapes
    _step_strip_frontmatter,
    _step_strip_direction,
    _step_normalise_header,
    _step_flatten_brace_hell,      # P19: fix NODE{"{nested}" "broken"} patterns
    _step_fix_bare_shapes,
    _step_quote_dangerous_labels,
    _step_close_brackets,
    _step_fix_quoted_edge_labels,
    _step_isolate_end,
    _step_trim,
)


def salvage(raw: str) -> tuple[str, list[str]]:
    """Best-effort fix for raw LLM-emitted Mermaid. Returns
    (cleaned_text, list_of_fixes_applied).

    The list is for observability only — ops can grep it to see which
    classes of LLM mis-emission are most common and adjust prompts.

    `cleaned_text` is always a string ending in a newline; if everything
    fails, returns FALLBACK_DIAGRAM unchanged and reports `"fallback"`.
    """
    if not raw or not isinstance(raw, str):
        return FALLBACK_DIAGRAM, ["fallback"]

    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    fixes: list[str] = []
    for step in _STEPS:
        text, fix = step(text)
        if fix:
            fixes.append(fix)

    # Last-chance sanity gate — if the result doesn't start with a known
    # diagram type keyword, we lost the plot; return the fallback.
    first_line = next(
        (ln.strip() for ln in text.split("\n") if ln.strip()),
        "",
    ).lower()
    if not any(first_line.startswith(t) for t in _DIAGRAM_TYPES):
        return FALLBACK_DIAGRAM, fixes + ["fallback"]

    return text, fixes
