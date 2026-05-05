"""
Deterministic Mermaid renderer — permanent fix for LLM-emitted Mermaid bugs.

Problem: LLMs emit Mermaid text inside tool-call JSON. The Mermaid grammar is
unforgiving of shape-syntax edge cases (`>Ant1]` needs a node-ID prefix,
`direction LR` must be inside a subgraph, labels can't contain `<>"#|`, etc.),
so every render failure turns into a new sanitiser regex and another bug
report from the demo floor.

Solution: invert the contract. The LLM emits a structured JSON (nodes,
edges, subgraphs, shapes) and this module — pure Python, deterministic,
fully unit-testable — renders guaranteed-valid Mermaid.

The renderer ALWAYS quotes labels with `"..."` (Mermaid's "any-char-except-
double-quote" form). That single design choice eliminates ~80% of the
failure modes we were patching with regex on the frontend.

Public API:
    render_block_diagram(spec: BlockDiagramSpec) -> str
    render_architecture(spec: BlockDiagramSpec) -> str   # alias with TD default
    validate_spec(spec: dict) -> list[str]               # returns error list, empty = valid
    ShapeName                                            # Literal[...]

All functions are PURE — no I/O, no globals mutated — so they're safe to
call from worker threads and trivially unit-testable.
"""
from __future__ import annotations

import re
from typing import Any, Literal, TypedDict

__all__ = [
    "render_block_diagram",
    "render_architecture",
    "validate_spec",
    "BlockDiagramSpec",
    "Node",
    "Edge",
    "Subgraph",
    "ShapeName",
    "MermaidSpecError",
    "SHAPE_NAMES",
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ShapeName = Literal[
    "flag",        # >text]        — antenna, output, amplifier (triangle)
    "connector",   # [/text/]      — SMA / N-type / BNC (parallelogram)
    "rect",        # [text]        — PCB trace, generic passive
    "limiter",     # [/text\]      — limiter / attenuator pad (trapezoid)
    "amplifier",   # >text]        — alias for flag (LNA / PA)
    "mixer",       # (text)        — mixer (rounded)
    "filter",      # {{text}}      — BPF / LPF / SAW (hexagon)
    "rhombus",     # {text}        — Bias-T / splitter / combiner
    "digital",     # [\text\]      — ADC / DAC (parallelogram alt)
    "oscillator",  # (text)        — LO / TCXO / OCXO (rounded)
    "stadium",     # ([text])      — start/end blocks
    "subroutine",  # [[text]]      — subsystem
    "cylinder",    # [(text)]      — data store
    "circle",      # ((text))      — small junction
]


class Node(TypedDict, total=False):
    id: str            # REQUIRED — must match ^[A-Za-z][A-Za-z0-9_]*$
    label: str         # REQUIRED — plain text, we escape
    shape: ShapeName   # REQUIRED — one of ShapeName
    stage: str         # optional — semantic stage id (lna, mixer, ...) for auto-fix


class Edge(TypedDict, total=False):
    # Python keyword `from` can't be a TypedDict key, so we accept both
    # `from_` (preferred) and `from` (reserved-word-safe JSON) and normalise.
    from_: str         # REQUIRED — source node id
    to: str            # REQUIRED — target node id
    label: str         # optional — edge label, plain text
    style: Literal["solid", "dotted", "thick"]  # optional


class Subgraph(TypedDict, total=False):
    id: str            # REQUIRED
    title: str         # REQUIRED — subgraph header
    nodes: list[str]   # REQUIRED — list of node ids that belong


class BlockDiagramSpec(TypedDict, total=False):
    direction: Literal["LR", "TD", "TB", "RL", "BT"]  # default LR
    nodes: list[Node]
    edges: list[Edge]
    subgraphs: list[Subgraph]
    title: str         # optional — header comment only


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class MermaidSpecError(ValueError):
    """Raised when a BlockDiagramSpec fails validation. The message lists
    every problem so the caller can log them all at once."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Shape → (open_delim, close_delim). The open/close pair wraps a quoted
# label. Mermaid accepts any char except `"` inside the quotes, so our job
# at render time is simply to strip/replace `"` in the label.
_SHAPE: dict[str, tuple[str, str]] = {
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
    "circle":     (("((", "))")),
}

SHAPE_NAMES: frozenset[str] = frozenset(_SHAPE.keys())

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# Mermaid reserved words that cannot be used as node IDs. Hitting these is
# rare but triggers confusing parse errors, so we catch them at validate.
_RESERVED_IDS: frozenset[str] = frozenset({
    "end", "subgraph", "graph", "flowchart", "direction",
    "click", "style", "class", "classDef", "linkStyle",
    "linkId", "default", "start", "stop",
})

# Non-ASCII symbols frequently produced by RF tooling — convert to ASCII
# equivalents so Mermaid's tokenizer never chokes on a stray glyph.
_NON_ASCII_MAP: dict[str, str] = {
    "\u03A9": "Ohm", "\u2126": "Ohm",
    "\u00B0": "deg", "\u00B5": "u",
    "\u2013": "-", "\u2014": "-",
    "\u2018": "'", "\u2019": "'",
    "\u201C": "'", "\u201D": "'",
    "\u2264": "<=", "\u2265": ">=",
    "\u00B1": "+-",
    "\u2192": "->", "\u2190": "<-",
    "\u2022": "*", "\u00B7": "*",
}

# Characters Mermaid's quoted-label tokeniser still dislikes even inside
# `"..."` — most notably the double-quote itself, and raw backticks/newlines.
_LABEL_STRIP = re.compile(r'["`\r]')
_LABEL_NEWLINE = re.compile(r"\n")

# Valid direction values.
_VALID_DIRECTIONS: frozenset[str] = frozenset({"LR", "TD", "TB", "RL", "BT"})


# ---------------------------------------------------------------------------
# Escape helpers
# ---------------------------------------------------------------------------

def _escape_label(text: str) -> str:
    """Make a label safe to wrap with `"..."` in Mermaid quoted-label form.

    We:
      1. Convert non-ASCII RF glyphs (Ohm, deg, u, em-dash, ...) to ASCII.
      2. Strip characters Mermaid can't handle even in quoted labels (`"`,
         backtick, CR).
      3. Replace newlines with `<br/>` — Mermaid's own line-break token
         inside quoted labels.
      4. Collapse runs of whitespace so the output is tidy.
    """
    if text is None:
        return ""
    s = str(text)
    for glyph, ascii_ in _NON_ASCII_MAP.items():
        s = s.replace(glyph, ascii_)
    s = _LABEL_STRIP.sub("", s)
    s = _LABEL_NEWLINE.sub("<br/>", s)
    # Also drop any remaining non-printable/control chars (NULLs, BELs, ...).
    s = "".join(ch for ch in s if ch == "<" or ch == ">" or ch == "/" or ord(ch) >= 0x20)
    # Mermaid is still unhappy if the label begins / ends with whitespace
    # after wrapping in quotes — trim it and collapse internal runs.
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s


def _normalise_edge(edge: dict[str, Any]) -> Edge:
    """Accept both `from_` and `from` keys (the JSON-safe spelling). Callers
    coming from JSON naturally use `from`, callers constructing Edges in
    Python use `from_`. Normalise to `from_` internally."""
    out: dict[str, Any] = dict(edge)
    if "from" in out and "from_" not in out:
        out["from_"] = out.pop("from")
    return out  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_spec(spec: dict[str, Any]) -> list[str]:
    """Return a list of human-readable validation errors. Empty list = OK.

    Always inspects the whole spec — doesn't short-circuit on first error —
    so a single call surfaces every problem."""
    errors: list[str] = []

    if not isinstance(spec, dict):
        return ["spec must be a JSON object, not %s" % type(spec).__name__]

    direction = spec.get("direction", "LR")
    if direction not in _VALID_DIRECTIONS:
        errors.append(
            f"direction '{direction}' not one of {sorted(_VALID_DIRECTIONS)}"
        )

    nodes = spec.get("nodes") or []
    if not isinstance(nodes, list):
        errors.append("nodes must be a list")
        nodes = []
    if len(nodes) == 0:
        errors.append("diagram needs at least 1 node")

    seen_ids: set[str] = set()
    for idx, n in enumerate(nodes):
        if not isinstance(n, dict):
            errors.append(f"nodes[{idx}] must be an object, got {type(n).__name__}")
            continue
        nid = n.get("id")
        if not isinstance(nid, str) or not nid:
            errors.append(f"nodes[{idx}].id missing or not a string")
        elif not _ID_RE.match(nid):
            errors.append(
                f"nodes[{idx}].id '{nid}' must match ^[A-Za-z][A-Za-z0-9_]*$"
            )
        elif nid.lower() in _RESERVED_IDS:
            errors.append(
                f"nodes[{idx}].id '{nid}' is a Mermaid reserved word — rename it"
            )
        elif nid in seen_ids:
            errors.append(f"nodes[{idx}].id '{nid}' is duplicated")
        else:
            seen_ids.add(nid)

        shape = n.get("shape")
        if shape not in SHAPE_NAMES:
            errors.append(
                f"nodes[{idx}].shape '{shape}' not in {sorted(SHAPE_NAMES)}"
            )

        label = n.get("label")
        if not isinstance(label, str):
            errors.append(f"nodes[{idx}].label missing or not a string")

    edges = spec.get("edges") or []
    if not isinstance(edges, list):
        errors.append("edges must be a list")
        edges = []
    for idx, raw in enumerate(edges):
        if not isinstance(raw, dict):
            errors.append(f"edges[{idx}] must be an object")
            continue
        e = _normalise_edge(raw)
        src, dst = e.get("from_"), e.get("to")
        if not isinstance(src, str) or not src:
            errors.append(f"edges[{idx}].from missing")
        elif src not in seen_ids:
            errors.append(f"edges[{idx}].from '{src}' not defined in nodes")
        if not isinstance(dst, str) or not dst:
            errors.append(f"edges[{idx}].to missing")
        elif dst not in seen_ids:
            errors.append(f"edges[{idx}].to '{dst}' not defined in nodes")
        style = e.get("style")
        if style is not None and style not in ("solid", "dotted", "thick"):
            errors.append(f"edges[{idx}].style '{style}' invalid")

    subgraphs = spec.get("subgraphs") or []
    if not isinstance(subgraphs, list):
        errors.append("subgraphs must be a list")
        subgraphs = []
    for idx, sg in enumerate(subgraphs):
        if not isinstance(sg, dict):
            errors.append(f"subgraphs[{idx}] must be an object")
            continue
        sid = sg.get("id")
        if not isinstance(sid, str) or not _ID_RE.match(sid or ""):
            errors.append(f"subgraphs[{idx}].id missing or invalid")
        elif sid in seen_ids:
            errors.append(
                f"subgraphs[{idx}].id '{sid}' clashes with a node id"
            )
        title = sg.get("title")
        if not isinstance(title, str):
            errors.append(f"subgraphs[{idx}].title missing")
        sg_nodes = sg.get("nodes") or []
        if not isinstance(sg_nodes, list):
            errors.append(f"subgraphs[{idx}].nodes must be a list")
        else:
            for nid in sg_nodes:
                if nid not in seen_ids:
                    errors.append(
                        f"subgraphs[{idx}].nodes ref '{nid}' not defined"
                    )

    return errors


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_ARROW_BY_STYLE: dict[str, str] = {
    "solid":  "-->",
    "dotted": "-.->",
    "thick":  "==>",
}


def _render_node(n: Node) -> str:
    """Render a single node: `N1>"label"]`. Label is always double-quoted
    so shape delimiters don't collide with label content."""
    open_, close_ = _SHAPE[n["shape"]]
    label = _escape_label(n["label"])
    return f'{n["id"]}{open_}"{label}"{close_}'


def _render_edge(edge: Edge) -> str:
    """Render a single edge: `A --> B` or `A -->|label| B`.

    Uses pipe-form edge labels (`A -->|label| B`) which is mermaid's most
    universally-compatible label syntax — the dash-quoted form
    `A -- "label" --> B` has caused intermittent parse failures across
    older mermaid versions and the mermaid.ink HTTP API. Pipe form
    works in mermaid.js (browser), mmdc CLI, and mermaid.ink without
    exception.

    Pipe content can contain anything except the literal `|` (which we
    swap to `/` in `_escape_label`). Quotes inside pipe-form labels are
    rendered literally — no escaping required.
    """
    e = _normalise_edge(edge)  # type: ignore[arg-type]
    arrow = _ARROW_BY_STYLE.get(e.get("style") or "solid", "-->")
    label = e.get("label")
    if label:
        safe = _escape_label(label).replace("|", "/").replace('"', "")
        # Pipe-form: `A -->|text| B`, `A -.->|text| B`, `A ==>|text| B`.
        return f'{e["from_"]} {arrow}|{safe}| {e["to"]}'
    return f'{e["from_"]} {arrow} {e["to"]}'


def _render_subgraph(sg: Subgraph, indent: str = "    ") -> list[str]:
    """Render a subgraph block. Children nodes are referenced by id (their
    full `id>"label"]` definition goes in the main node list)."""
    title = _escape_label(sg["title"])
    lines = [f'{indent}subgraph {sg["id"]}["{title}"]']
    for nid in sg["nodes"]:
        lines.append(f"{indent}    {nid}")
    lines.append(f"{indent}end")
    return lines


def render_block_diagram(
    spec: dict[str, Any],
    *,
    default_direction: str = "LR",
    raise_on_error: bool = True,
) -> str:
    """Convert a BlockDiagramSpec into valid Mermaid flowchart text.

    The output is deterministic: same input ⇒ byte-identical output. No
    trailing whitespace, Unix newlines, labels always quoted.

    Args:
        spec: structured diagram definition.
        default_direction: used if spec omits `direction`. RF receivers
            look best as LR (left-to-right signal chain); the HRS agent
            passes TD for stack-ups.
        raise_on_error: if True, validation errors raise MermaidSpecError;
            if False, we emit a best-effort diagram plus a `%% ERROR` comment.

    Returns:
        Multi-line Mermaid source string. Always starts with `flowchart`.
    """
    errors = validate_spec(spec)
    if errors:
        if raise_on_error:
            raise MermaidSpecError("; ".join(errors))
        # Soft mode: render a minimal placeholder so downstream writers
        # still produce a file, but embed the errors as Mermaid comments
        # (Mermaid treats `%%` lines as comments and ignores them).
        lines = [f"flowchart {default_direction}"]
        for err in errors:
            safe = err.replace("\n", " ").replace("%", "pct")
            lines.append(f"    %% ERROR: {safe}")
        lines.append('    ERR["diagram spec invalid — see comments above"]')
        return "\n".join(lines)

    direction = spec.get("direction") or default_direction
    out: list[str] = [f"flowchart {direction}"]

    # Node definitions first — every node must be defined before being
    # referenced in an edge or subgraph.
    for n in spec.get("nodes") or []:
        out.append("    " + _render_node(n))

    # Then edges in the order given.
    for e in spec.get("edges") or []:
        out.append("    " + _render_edge(e))

    # Finally any subgraphs. Reference-by-id keeps the top-level node list
    # as the single source of truth for shapes/labels.
    for sg in spec.get("subgraphs") or []:
        out.extend(_render_subgraph(sg))

    return "\n".join(out)


def render_architecture(
    spec: dict[str, Any],
    *,
    raise_on_error: bool = True,
) -> str:
    """Same as `render_block_diagram` but defaults to top-down (TD) — that's
    the preferred layout for architecture diagrams where dataflow between
    subsystems is the point, not a linear signal chain."""
    return render_block_diagram(
        spec, default_direction="TD", raise_on_error=raise_on_error
    )
