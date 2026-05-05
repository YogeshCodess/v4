"""Permanent fix for the recurring "LLM emitted mermaid we can't render"
bug class.

Background — the user has reported the same family of mermaid bugs 30+
times. Each report had a slightly different LLM output:
  - trapezoid `[\\..\\]` with parens in label
  - flag `>...]` with `<br/>` inside quoted label
  - subroutine `[[..]]` with `#` in part numbers
  - parallelogram `[/.../]` with backslash-escaped quotes
  - mixed `[/...\\]` shape with arrow tokens in label
  - sequence-diagram `note right of NODE` directives in flowcharts
  - malformed `class XX` / `style XX` declarations
  - frontmatter `%%{init: {...}}%%` escaping
  - non-ASCII glyphs (Ohm, deg, etc.)
  - `<` / `>` in labels mistaken for HTML tags

The salvage approach (`tools/mermaid_salvage.py`) tries to PATCH the raw
text. Each patch fixes one variant but leaves N+1 variants unhandled.

This module takes the OPPOSITE approach: it EXTRACTS the meaningful
content (node IDs, labels, edges) from any LLM-emitted text using
forgiving regex, then re-renders FRESH via the deterministic renderer
in `tools/mermaid_render.py` which produces ONLY plain `["label"]` rect
shapes with quoted labels. That single shape variant accepts ALL
special chars (parens, `<br>`, `#`, etc.) so the output is GUARANTEED
to render in mermaid.js, mermaid.ink, and mmdc.

The visual nuance (trapezoid for limiters, hexagon for filters, etc.)
is LOST — but a flat rect-only diagram that ALWAYS renders beats a
beautiful trapezoid that fails 30% of the time. Visual nuance can be
restored later by enriching the structured spec, not by patching raw
text.

Public API:
    coerce_to_spec(raw_mermaid: str) -> dict | None
        Returns a BlockDiagramSpec or None if extraction couldn't find
        even 2 nodes (caller should then use FALLBACK_DIAGRAM).
"""
from __future__ import annotations

import re
from typing import Optional

__all__ = ["coerce_to_spec", "sanitize_mermaid_blocks_in_markdown"]


# Lines we want to drop entirely before extraction. These are
# diagram-type-mismatch directives (sequence-diagram syntax in a
# flowchart context) that mermaid rejects.
_DROP_LINE_PREFIXES = (
    "note ",                # `note right of X` etc.
    "participant ",         # sequence-diagram actors
    "actor ",
    "activate ",
    "deactivate ",
    "autonumber",
    "title ",
    "loop ",
    "alt ",
    "else",
    "opt ",
    "par ",
    "rect ",
    "click ",               # mermaid click handlers — not needed for static render
    "linkstyle ",           # global link style — fragile
    "classdef ",            # class definitions — produce parse errors when malformed
    "class ",               # class application — produce parse errors when malformed
    "style ",               # style application — same
)

# Frontmatter / comments to strip.
_FRONTMATTER_RE = re.compile(r"%%\{[\s\S]*?\}%%\s*", re.MULTILINE)
_COMMENT_RE = re.compile(r"%%[^\n]*", re.MULTILINE)

# Diagram-type header (line 1 usually) — we want to PRESERVE the
# direction (TD/TB/LR/RL/BT) but strip the type so we can re-emit.
_HEADER_RE = re.compile(
    r"^\s*(?:flowchart|graph)\s+(TD|TB|LR|RL|BT)\b",
    re.IGNORECASE | re.MULTILINE,
)

# Shape-stripping regexes — we capture the NODE ID and the LABEL from
# any of mermaid's shape syntaxes. We try the QUOTED variants first so
# labels containing parens / brackets / braces (e.g. `("VGA (AGC)")`)
# are captured as a single quoted span, then fall back to the unquoted
# variants. Within each tier, longer shapes go first so `[[foo]]`
# matches before `[foo]`, `((foo))` before `(foo)`, etc.
#
# Each pattern captures (id, label). For quoted-form patterns the label
# is the inner quoted text; for unquoted-form it's the bare label.
# `_clean_label` strips quotes and surrounding whitespace downstream.
_SHAPE_PATTERNS = [
    # ── Tier 1: QUOTED-LABEL variants (match `<id><open>"<anything>"<close>`).
    # Quoted labels can contain parens, brackets, braces, slashes, etc.
    # without confusing the parser. Listed longest-shape-first.
    (re.compile(r'\b([A-Za-z][A-Za-z0-9_]*)\s*\[\[\s*"([^"]+)"\s*\]\]'), "subroutine_q"),
    (re.compile(r'\b([A-Za-z][A-Za-z0-9_]*)\s*\{\{\s*"([^"]+)"\s*\}\}'), "hexagon_q"),
    (re.compile(r'\b([A-Za-z][A-Za-z0-9_]*)\s*\(\(\s*"([^"]+)"\s*\)\)'), "circle_q"),
    (re.compile(r'\b([A-Za-z][A-Za-z0-9_]*)\s*\(\[\s*"([^"]+)"\s*\]\)'), "stadium_q"),
    (re.compile(r'\b([A-Za-z][A-Za-z0-9_]*)\s*\[\(\s*"([^"]+)"\s*\)\]'), "cylinder_q"),
    (re.compile(r'\b([A-Za-z][A-Za-z0-9_]*)\s*\[\\\s*"([^"]+)"\s*\\\]'), "trapezoid_q"),
    (re.compile(r'\b([A-Za-z][A-Za-z0-9_]*)\s*\[/\s*"([^"]+)"\s*/\]'), "parallelogram_q"),
    (re.compile(r'\b([A-Za-z][A-Za-z0-9_]*)\s*\[/\s*"([^"]+)"\s*\\\]'), "trap_mixed_a_q"),
    (re.compile(r'\b([A-Za-z][A-Za-z0-9_]*)\s*\[\\\s*"([^"]+)"\s*/\]'), "trap_mixed_b_q"),
    (re.compile(r'\b([A-Za-z][A-Za-z0-9_]*)\s*>\s*"([^"]+)"\s*\]'), "flag_q"),
    (re.compile(r'\b([A-Za-z][A-Za-z0-9_]*)\s*\{(?!\{)\s*"([^"]+)"\s*\}'), "rhombus_q"),
    (re.compile(r'\b([A-Za-z][A-Za-z0-9_]*)\s*\((?!\()\s*"([^"]+)"\s*\)'), "round_q"),
    (re.compile(r'\b([A-Za-z][A-Za-z0-9_]*)\s*\[\s*"([^"]+)"\s*\]'), "rect_q"),
    # ── Tier 2: UNQUOTED-LABEL variants (label can't contain its own
    # closing delimiter or the quote char). Listed longest-first.
    # P26 #21 (2026-04-26): every label class also excludes `\n` so a
    # malformed line can't accidentally swallow the next line. Pre-fix
    # `SMA1[/SMA-F/]\n    LIM1[/Lim/...\\]` matched as ONE node spanning
    # 2 lines because the lazy `[^"\\]+?` happily ate the newline +
    # the LIM1 prefix in search of the closing `\]`. Adding `\n` to
    # the negation forces lazy matches to stay on one line.
    (re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\[\[\s*([^\"\]\n]+?)\s*\]\]"), "subroutine"),
    (re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\{\{\s*([^\"\}\n]+?)\s*\}\}"), "hexagon"),
    (re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\(\(\s*([^\"\)\n]+?)\s*\)\)"), "circle"),
    (re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\(\[\s*([^\"\]\n]+?)\s*\]\)"), "stadium"),
    (re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\[\(\s*([^\"\)\n]+?)\s*\)\]"), "cylinder"),
    (re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\[\\\s*([^\"\\\n]+?)\s*\\\]"), "trapezoid"),
    (re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\[/\s*([^\"/\n]+?)\s*/\]"), "parallelogram"),
    (re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\[/\s*([^\"\\\n]+?)\s*\\\]"), "trap_mixed_a"),
    (re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\[\\\s*([^\"/\n]+?)\s*/\]"), "trap_mixed_b"),
    (re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*>\s*([^\"\]\n]+?)\s*\]"), "flag"),
    (re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\{(?!\{)\s*([^\"\}\n]+?)\s*\}"), "rhombus"),
    (re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\((?!\()\s*([^\"\)\n]+?)\s*\)"), "round"),
    (re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\[\s*([^\"\]\n]+?)\s*\]"), "rect"),
]

# Trailing-shape stripper — used to NORMALISE the text before edge
# extraction. Without this, an edge `A[label] --> B[label]` doesn't
# match the simple `\bA\s*-->\s*B\b` pattern because `[label]` sits
# between the id and the arrow. We replace `<id><shape>` with just
# `<id>` so edges become `A --> B` and the patterns below all match.
# Note: we use [\s\S] inside trapezoid/parallelogram contents so labels
# with newlines (rare but seen) still get stripped.
_SHAPE_TRAILING_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]*)"
    r"(?:"
        r"\[\[[^\]]*\]\]"            # subroutine [[..]]
        r"|\{\{[^}]*\}\}"            # hexagon {{..}}
        r"|\(\([^)]*\)\)"            # circle ((..))
        r"|\(\[[^\]]*\]\)"           # stadium ([..])
        r"|\[\([^)]*\)\]"            # cylinder [(..)]
        r"|\[\\[^\]]*\\\]"           # trapezoid [\..\]
        r"|\[/[^\]]*/\]"             # parallelogram [/.../]
        r"|\[/[^\]]*\\\]"            # mixed-slash [/...\]
        r"|\[\\[^\]]*/\]"            # mixed-slash [\../]
        r"|>[^\]]*\]"                # flag >..]
        r"|\{[^}]*\}"                # rhombus {..}
        r"|\([^)]*\)"                # round (..)
        r"|\[[^\]]*\]"               # rect [..]
    r")"
)

# Edge regexes — we capture (from, [label], to). All edge styles are
# normalised to plain `-->` since the output is rect-only anyway.
_EDGE_PATTERNS = [
    # A == "label" ==> B  (thick with label)
    re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*==\s*\"([^\"]+)\"\s*==>\s*([A-Za-z][A-Za-z0-9_]*)"),
    # A -- "label" --> B  (normal with label)
    re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*--\s*\"([^\"]+)\"\s*-->\s*([A-Za-z][A-Za-z0-9_]*)"),
    # A -. "label" .-> B  (dotted with label)
    re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*-\.\s*\"([^\"]+)\"\s*\.->\s*([A-Za-z][A-Za-z0-9_]*)"),
    # A -->|label| B  (pipe-form label)
    re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*(?:--+|==+|-\.-?)>?\|([^|]+)\|\s*([A-Za-z][A-Za-z0-9_]*)"),
    # A -- label --> B  (label without quotes — careful, `label` can't
    # contain arrow chars)
    re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*--\s*([^\-\n][^\n]*?)\s*-->\s*([A-Za-z][A-Za-z0-9_]*)"),
    # A --> B          (no label)
    re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*-->\s*([A-Za-z][A-Za-z0-9_]*)"),
    # A ==> B          (thick no label)
    re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*==>\s*([A-Za-z][A-Za-z0-9_]*)"),
    # A -.-> B         (dotted no label)
    re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*-\.->\s*([A-Za-z][A-Za-z0-9_]*)"),
]


# Glyphs we replace before extraction so labels are clean ASCII.
_NON_ASCII_MAP = {
    "Ω": "Ohm", "Ω": "Ohm",
    "°": "deg", "µ": "u",
    "–": "-", "—": "-",
    "‘": "'", "’": "'",
    "“": "'", "”": "'",
    "≤": "<=", "≥": ">=",
    "±": "+-",
    "→": "->", "←": "<-",
    "﻿": "",  # BOM
}


def _clean_label(raw: str) -> str:
    """Make a label safe for the deterministic renderer's `["label"]`
    quoted form. Mermaid's quoted-label form accepts almost anything
    EXCEPT the closing double-quote — so we strip just `"` and a few
    chars that confuse some downstream renderers."""
    s = raw.strip()
    # Strip surrounding quotes if any
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        s = s[1:-1]
    # Replace problem chars
    s = s.replace('"', '')          # the only thing mermaid quoted-label can't hold
    s = s.replace("\\\"", "")        # escaped quotes
    s = s.replace("\\", " ")         # backslashes (used as shape delimiters; never in labels)
    # P26 #17 (2026-04-26): square brackets inside a quoted label
    # (e.g. `+5V_CH[1:4]`) trip mermaid's parser even when wrapped in
    # `["..."]` quotes — the inner `[` is read as a new shape opener.
    # Replace with parens which render fine inside quoted labels.
    s = s.replace("[", "(").replace("]", ")")
    # Convert HTML breaks to mermaid's <br>
    s = re.sub(r"<br\s*/?>", "<br>", s, flags=re.IGNORECASE)
    # Convert other HTML tags to spaces (preserve <br>)
    s = re.sub(r"<(?!br\b)[^>]*>", " ", s)
    # Replace pipe with slash (pipe is reserved in edge labels)
    s = s.replace("|", "/")
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def coerce_to_spec(
    raw_mermaid: str,
    *,
    default_direction: str = "LR",
) -> Optional[dict]:
    """Extract nodes + edges from raw LLM mermaid into a structured
    BlockDiagramSpec that `render_block_diagram` can convert into
    guaranteed-valid clean mermaid.

    Returns a spec dict if at least 2 distinct nodes were extracted,
    or None if extraction effectively failed (caller should then use
    a fallback diagram or skip rendering).

    Strategy:
      1. ASCIIfy + strip frontmatter / comments / non-flowchart directives
      2. Extract direction from the diagram header (default LR)
      3. Extract every (node_id, label) pair from any shape variant
      4. Extract every edge (from, label, to) from any arrow form
      5. Build a structured spec with shape="rect" for ALL nodes
         (deterministic renderer always quotes labels — accepts
         everything safely)
    """
    if not raw_mermaid or not isinstance(raw_mermaid, str):
        return None

    text = raw_mermaid.replace("\r\n", "\n").replace("\r", "\n")

    # Step 1: ASCIIfy
    for glyph, repl in _NON_ASCII_MAP.items():
        text = text.replace(glyph, repl)
    text = "".join(ch if ord(ch) < 128 else "" for ch in text)

    # Step 2: strip frontmatter + comments + diagram-type-mismatch directives
    text = _FRONTMATTER_RE.sub("", text)
    text = _COMMENT_RE.sub("", text)
    cleaned_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        # Drop lines that start with non-flowchart directives.
        if stripped:
            # We look at the FIRST word case-insensitively.
            first = stripped.lower()
            if any(first.startswith(p) for p in _DROP_LINE_PREFIXES):
                continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    # Step 3: extract direction
    direction = default_direction
    m = _HEADER_RE.search(text)
    if m:
        direction = m.group(1).upper()
        # TB and TD are aliases; renderer prefers TD/LR/RL/BT.
        if direction == "TB":
            direction = "TD"

    # Step 4: extract nodes — track first label seen per id
    nodes_dict: dict[str, str] = {}
    # Subgraphs are dropped entirely (Step 7 below) to keep the output
    # diagram flat and parse-safe. We must DELETE the whole subgraph
    # header line so the rect pattern doesn't grab `PWR["Power"]` as a
    # node — matching just `subgraph PWR` and leaving the trailing
    # `["Power"]` behind would still let the rect regex pick up `PWR`.
    text_for_extract = re.sub(
        r"^\s*subgraph\s+[A-Za-z][A-Za-z0-9_]*[^\n]*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    # Matching `end` keywords for subgraph blocks should also go.
    text_for_extract = re.sub(
        r"^\s*end\s*$",
        "",
        text_for_extract,
        flags=re.MULTILINE,
    )
    # Collect ALL pattern matches first as (start, end, id, label, tier).
    # `tier` is the index in _SHAPE_PATTERNS so quoted variants (lower
    # index) win ties. After collection, sort and accept non-overlapping
    # candidates greedily — this prevents `Microstrip(RO4350B)` INSIDE
    # `S2["...Microstrip (RO4350B)"]` from being extracted as a phantom
    # round-bracket node.
    candidates: list[tuple[int, int, str, str, int]] = []
    for tier_idx, (pattern, _shape_name) in enumerate(_SHAPE_PATTERNS):
        for shape_match in pattern.finditer(text_for_extract):
            node_id = shape_match.group(1)
            raw_label = shape_match.group(2)
            label = _clean_label(raw_label)
            if not label or len(label) > 200:
                continue
            candidates.append((
                shape_match.start(), shape_match.end(),
                node_id, label, tier_idx,
            ))

    # Sort: by start asc, then by tier asc (quoted before unquoted),
    # then by length desc (longer match preferred at same start+tier).
    candidates.sort(key=lambda c: (c[0], c[4], -(c[1] - c[0])))

    # Greedy non-overlap: walk left to right, accept each candidate
    # whose start >= the last accepted end.
    last_end = 0
    for start, end, nid, lbl, _tier in candidates:
        if start < last_end:
            continue  # overlaps with an earlier accepted span — phantom
        if nid not in nodes_dict:
            nodes_dict[nid] = lbl
        last_end = end

    # Step 5: extract edges
    # ── Pre-pass: strip shape syntax so `A[label] --> B[label]` becomes
    # `A --> B` for edge regexes that expect ids adjacent to the arrow.
    # We DON'T mutate the original `text` (still needed in case some
    # downstream step references positions); we just operate on a copy.
    text_for_edges = _SHAPE_TRAILING_RE.sub(r"\1", text)
    edges_list: list[dict] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for pattern in _EDGE_PATTERNS:
        for edge_match in pattern.finditer(text_for_edges):
            grp = edge_match.groups()
            if len(grp) == 3:
                src, label, dst = grp[0], _clean_label(grp[1]), grp[2]
            elif len(grp) == 2:
                src, label, dst = grp[0], "", grp[1]
            else:
                continue
            # Skip if either endpoint isn't an extracted node — the
            # rendered diagram would have a dangling reference.
            if src not in nodes_dict or dst not in nodes_dict:
                # Add the missing node as a generic rect — this catches
                # cases where the LLM defined the node implicitly via
                # an edge (e.g. `A --> B` without `B[label]` first).
                if src not in nodes_dict and re.match(r"^[A-Za-z]", src):
                    nodes_dict[src] = src
                if dst not in nodes_dict and re.match(r"^[A-Za-z]", dst):
                    nodes_dict[dst] = dst
                if src not in nodes_dict or dst not in nodes_dict:
                    continue
            key = (src, label, dst)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edge: dict = {"from_": src, "to": dst}
            if label:
                edge["label"] = label
            edges_list.append(edge)

    # Step 6: refuse to coerce if we got too little — caller falls back
    if len(nodes_dict) < 2:
        return None

    # Step 7: build the spec — all nodes are rect (the safest shape).
    nodes_out = [
        {"id": nid, "label": lbl, "shape": "rect"}
        for nid, lbl in nodes_dict.items()
    ]

    return {
        "direction": direction,
        "nodes": nodes_out,
        "edges": edges_list,
        # No subgraphs — they often cause cascading parse errors in the
        # raw LLM output. Cleaner without them in the deterministic path.
    }


# ---------------------------------------------------------------------------
# Markdown-walker helper — sanitises every ```mermaid``` block in a
# document via the coerce-and-re-render pipeline.
#
# This is the agent-facing entry point used by `document_agent` (HRS),
# `sdd_agent`, `srs_agent`, `glr_agent`, and any future agent that
# writes markdown containing mermaid. Each fenced mermaid block gets
# parsed → coerced to a structured spec → re-rendered as the safe
# `flowchart <dir>` + `id["label"]` rect form. Blocks the coercer
# can't extract a spec from (fewer than 2 nodes) are left untouched
# so the legacy salvage layer still has a chance during DOCX render.
#
# Real-world bugs this catches (P26 #17, 2026-04-26, project rx_band):
#   - L257 of HRS: `MIX2["..."]}` — extra `}` after rect close
#   - L1540 of HRS: `LDO5C["+5V_CH[1:4]..."]` — nested `[...]` inside
#                   a quoted label that confuses mermaid's parser
# ---------------------------------------------------------------------------

# Match a fenced mermaid block. We allow optional trailing whitespace on
# the opening fence (`__main__\n`) and any content (incl. blank lines)
# inside. Non-greedy + DOTALL via [\s\S].
_MERMAID_FENCE_RE = re.compile(
    r"```mermaid[ \t]*\n([\s\S]*?)\n```",
    re.MULTILINE,
)


def sanitize_mermaid_blocks_in_markdown(
    markdown_text: str,
    *,
    default_direction: str = "LR",
) -> str:
    """Walk a markdown document and replace every ```mermaid``` fenced
    block with a coerce-and-re-rendered version that is guaranteed to
    parse in mermaid.js, mmdc, and mermaid.ink.

    Empty / unparseable blocks (fewer than 2 extractable nodes) are
    left UNCHANGED so the legacy DOCX-render salvage layer still gets
    a shot at them. This is the same trade-off the requirements_agent
    already uses — it's better to ship the original than a half-fixed
    placeholder when the spec is too sparse.

    Used at WRITE time inside agents that emit markdown with embedded
    mermaid (HRS, SDD, SRS, GLR). Without this pass, malformed LLM
    mermaid (bracket mismatches, nested labels, stray glyphs) lands on
    disk verbatim and breaks the in-browser preview.

    Pure text-in / text-out — no I/O, no side effects, safe to call
    multiple times (idempotent on already-clean input).
    """
    if not markdown_text or not isinstance(markdown_text, str):
        return markdown_text or ""

    # Lazy-import the renderer to avoid a circular import — render
    # itself imports mermaid_coerce-adjacent helpers in some paths.
    try:
        from tools.mermaid_render import render_block_diagram
    except Exception:
        # Renderer not importable — return text unchanged so we don't
        # silently corrupt mermaid blocks.
        return markdown_text

    def _replace(m: re.Match[str]) -> str:
        raw_block = m.group(1)
        try:
            spec = coerce_to_spec(raw_block, default_direction=default_direction)
        except Exception:
            return m.group(0)  # original block unchanged
        if not spec:
            return m.group(0)
        if len(spec.get("nodes") or []) < 2:
            return m.group(0)
        try:
            rendered = render_block_diagram(
                spec,
                default_direction=default_direction,
                raise_on_error=True,
            )
        except Exception:
            return m.group(0)
        # Re-wrap in fenced markdown.
        return f"```mermaid\n{rendered}\n```"

    return _MERMAID_FENCE_RE.sub(_replace, markdown_text)
