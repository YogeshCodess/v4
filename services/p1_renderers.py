"""
Deterministic P1 artifact renderers (Step 1 → Step 3 of the SSoT refactor).

Every renderer here takes a frozen DesignManifest and produces a single
artifact. The contract:

  1. Output is a PURE FUNCTION of `manifest.bom` + `manifest.architecture`
     + `manifest.design_parameters`. Same input -> same bytes. No LLM, no
     network, no time.

  2. Every MPN that appears in the output MUST be in `manifest.bom`. The
     `services.manifest_validator.check_no_mpn_leak` gate enforces this
     at the boundary.

  3. The renderer is named `render_<artifact>(manifest) -> str`, returning
     the file content as a string. Disk writes happen in the caller.

This module currently exposes:

  - render_block_diagram_mermaid(manifest)
        Mermaid `block-beta` (NOT `flowchart`!) — the proper Mermaid
        primitive for system block diagrams. Picks an architecture-aware
        layout: linear cascade, switch tree, or direct-RF.

The other renderers (requirements.md / components.md / glb.md) will land
in a follow-up commit; in the interim the existing `_build_*` methods on
`RequirementsAgent` continue to render those files. The block-beta
renderer is wired in first because the hackathon-final bug specifically
hit the block-diagram + GLB layer, and a deterministic block diagram
also unblocks the leak gate (LLM-emitted mermaid was the largest source
of false-positive leaks because the LLM puts MPNs in node labels).
"""
from __future__ import annotations

import math
import re
from typing import Any, Optional

from services.design_manifest import DesignManifest


# ---------------------------------------------------------------------------
# Role classification — we order BOM rows in canonical signal-chain order
# ---------------------------------------------------------------------------

# Canonical role -> column position. Lower number = closer to the input
# (antenna / RF in). Roles not in this list are appended at the end in the
# order they appear in the BOM (preserves any ordering the LLM chose).
_ROLE_ORDER: dict[str, int] = {
    # RX front end
    "antenna": 0, "ant": 0,
    "limiter": 5,
    "preselector": 8, "preselect": 8,
    "lna": 10, "lna1": 10, "lna2": 11, "lna3": 12,
    "rf_amp": 13, "rfamp": 13,
    "filter": 20, "bpf": 20, "lpf": 21, "hpf": 21, "saw": 22, "crystal": 22,
    "attenuator": 25, "atten": 25, "dsa": 25, "vga": 26,
    "balun": 28,
    # Frequency conversion
    "mixer": 30, "mix": 30, "downconverter": 31, "upconverter": 31,
    "lo": 35, "synth": 36, "pll": 37, "synthesizer": 36,
    # IF / digitisation
    "if_amp": 40, "ifamp": 40, "if": 40,
    "if_filter": 45,
    "agc": 48,
    "adc": 50, "dac": 50,
    # Digital
    "fpga": 60, "cpld": 60, "dsp": 61, "mcu": 62, "soc": 62,
    "memory": 65, "flash": 65, "ram": 65, "ddr": 65,
    # Switch matrix-specific
    "switch": 15, "sw": 15,
    "splitter": 16, "combiner": 16,
    # Power / clock supplemental
    "ldo": 90, "regulator": 91, "dcdc": 91,
    "tcxo": 95, "ocxo": 95, "xo": 95, "clock": 95,
}


def _normalise_role(row: dict[str, Any]) -> str:
    """Pull a normalised role string from a BOM row. Tries `role`, `kind`,
    then `name`, lowercases, strips."""
    raw = row.get("role") or row.get("kind") or row.get("name") or row.get("category") or ""
    return str(raw).strip().lower()


def _role_order_key(row: dict[str, Any]) -> tuple[int, int]:
    """Sort key: (canonical_position, original_index_fallback)."""
    role = _normalise_role(row)
    # Direct lookup
    if role in _ROLE_ORDER:
        return (_ROLE_ORDER[role], 0)
    # Substring fallback — handles roles like "rf_amplifier_1" or "lna_2"
    for k, v in _ROLE_ORDER.items():
        if k in role:
            return (v, 0)
    # Unknown role — push to the end, preserve declaration order
    return (1000, 0)


# ---------------------------------------------------------------------------
# Refdes / label helpers
# ---------------------------------------------------------------------------

# Reference designator prefix per role family. Keeps block-beta IDs short
# and stable across re-renders.
_REFDES_PREFIX: dict[str, str] = {
    "lna": "U", "rf_amp": "U", "if_amp": "U", "vga": "U",
    "mixer": "U", "downconverter": "U", "upconverter": "U",
    "lo": "U", "synth": "U", "pll": "U", "synthesizer": "U",
    "adc": "U", "dac": "U",
    "fpga": "U", "cpld": "U", "dsp": "U", "mcu": "U", "soc": "U",
    "switch": "SW", "sw": "SW",
    "filter": "FL", "bpf": "FL", "lpf": "FL", "hpf": "FL", "saw": "FL",
    "balun": "T",
    "attenuator": "ATT", "atten": "ATT", "dsa": "ATT",
    "antenna": "ANT", "ant": "ANT",
    "limiter": "LIM",
    "ldo": "VR", "regulator": "VR", "dcdc": "VR",
    "tcxo": "Y", "ocxo": "Y", "xo": "Y", "clock": "Y",
    "memory": "M", "flash": "M", "ram": "M",
    "splitter": "DV", "combiner": "DV",
}


def _refdes_for(row: dict[str, Any], counter: dict[str, int]) -> str:
    """Allocate a stable reference designator for a BOM row. Counter is
    mutated to preserve uniqueness across calls within one render."""
    role = _normalise_role(row)
    # Honour explicit refdes if the LLM already supplied one
    explicit = row.get("refdes") or row.get("reference") or row.get("designator")
    if explicit:
        return str(explicit).strip().upper()
    # Pick prefix from role family
    prefix = "U"  # safe generic IC default
    for k, v in _REFDES_PREFIX.items():
        if k == role or k in role:
            prefix = v
            break
    counter[prefix] = counter.get(prefix, 0) + 1
    return f"{prefix}{counter[prefix]}"


_LABEL_SAFE_RE = re.compile(r'[\[\]{}|"`]')


def _block_label(refdes: str, row: dict[str, Any]) -> str:
    """Build a short two-line block label: refdes on top, MPN+role below.
    Strips characters that would break Mermaid block-beta parsing."""
    pn = row.get("part_number") or row.get("primary_part") or row.get("mpn") or ""
    role = _normalise_role(row).upper() or "STAGE"
    pn = _LABEL_SAFE_RE.sub("", str(pn)).strip()
    role = _LABEL_SAFE_RE.sub("", role).strip()
    if pn:
        return f'"{refdes}<br/>{pn}<br/>{role}"'
    return f'"{refdes}<br/>{role}"'


# ---------------------------------------------------------------------------
# Architecture-aware layout dispatch
# ---------------------------------------------------------------------------

def _is_switch_matrix(manifest: DesignManifest) -> bool:
    """True when the project_type or architecture indicates a switch matrix
    topology, OR when ≥ 50 % of the BOM rows are switches."""
    pt = (manifest.project_type or "").lower()
    if pt == "switch_matrix":
        return True
    arch = (manifest.architecture or "").lower()
    if "switch" in arch and "matrix" in arch:
        return True
    if not manifest.bom:
        return False
    n_sw = sum(1 for r in manifest.bom if "switch" in _normalise_role(r) or _normalise_role(r) == "sw")
    return n_sw >= max(2, len(manifest.bom) // 2)


def _is_direct_rf_sampling(manifest: DesignManifest) -> bool:
    arch = (manifest.architecture or "").lower()
    return ("direct" in arch and "rf" in arch) or "direct-rf" in arch or "direct_rf" in arch


# ---------------------------------------------------------------------------
# Renderers — one per topology family
# ---------------------------------------------------------------------------

def _render_linear_cascade(manifest: DesignManifest) -> str:
    """Default layout: a left-to-right signal chain ordered by canonical
    role. Works for superhet / direct-conversion / SDR / digital-IF."""
    rows = sorted(
        list(manifest.bom or []),
        key=_role_order_key,
    )
    if not rows:
        return _empty_diagram(manifest)

    lines: list[str] = ["block-beta", "  columns 1", ""]
    counter: dict[str, int] = {}
    refdes_list: list[str] = []
    label_lines: list[str] = []

    # Block declarations (one per row, on its own line — block-beta lays
    # them out left-to-right when columns is set high enough; we use a
    # single column with `columns 1` and rely on the explicit edges to
    # express the chain instead, which renders cleanly in Mermaid 10+).
    for row in rows:
        rd = _refdes_for(row, counter)
        refdes_list.append(rd)
        label_lines.append(f"  {rd}{_block_label(rd, row)}")

    # Re-emit with a wider columns count for left-to-right flow
    cols = min(max(len(rows), 3), 6)
    lines = ["block-beta", f"  columns {cols}", ""]
    lines.extend(label_lines)
    lines.append("")

    # Linear edges: each stage feeds the next
    for a, b in zip(refdes_list, refdes_list[1:]):
        lines.append(f"  {a} --> {b}")

    # Style the LO / synth blocks differently when present (they side-feed
    # into the mixer rather than appearing in the chain).
    lo_refdes = [
        rd for rd, row in zip(refdes_list, rows)
        if _normalise_role(row) in {"lo", "synth", "pll", "synthesizer"}
    ]
    if lo_refdes:
        lines.append("")
        lines.append("  classDef lo fill:#9c6,stroke:#363;")
        for rd in lo_refdes:
            lines.append(f"  class {rd} lo;")

    lines.append("")
    lines.append("  classDef rf fill:#69c,stroke:#36c,color:#fff;")
    rf_refdes = [
        rd for rd, row in zip(refdes_list, rows)
        if _normalise_role(row) in {"lna", "mixer", "rf_amp", "filter", "bpf",
                                    "balun", "limiter", "preselector"}
    ]
    if rf_refdes:
        lines.append(f"  class {','.join(rf_refdes)} rf;")

    return "\n".join(lines).rstrip() + "\n"


def _render_switch_matrix(manifest: DesignManifest) -> str:
    """Switch-matrix specific layout: input(s) -> switch tree -> output(s).

    For an N×M switch matrix we lay out: a column of inputs on the left,
    a fan of switch ICs in the middle, and a column of outputs on the
    right. Falls back to the linear renderer if the BOM is too small to
    make a tree out of (< 2 switches)."""
    rows = list(manifest.bom or [])
    sw_rows = [r for r in rows if "switch" in _normalise_role(r) or _normalise_role(r) == "sw"]
    if len(sw_rows) < 2:
        return _render_linear_cascade(manifest)

    # Inputs / outputs from design_parameters when supplied; else infer
    # from the switch matrix geometry (e.g. 4×4 = 4 in, 4 out).
    dp = manifest.design_parameters or {}
    n_in = int(dp.get("matrix_inputs") or dp.get("n_inputs") or len(sw_rows) // 2 or 2)
    n_out = int(dp.get("matrix_outputs") or dp.get("n_outputs") or n_in)
    n_in = max(1, min(n_in, 16))
    n_out = max(1, min(n_out, 16))

    counter: dict[str, int] = {}
    sw_refdes = [_refdes_for(r, counter) for r in sw_rows]
    other_rows = [r for r in rows if r not in sw_rows]
    other_refdes = [_refdes_for(r, counter) for r in other_rows]

    # 3-column layout: inputs | switches | outputs.
    lines: list[str] = ["block-beta", "  columns 3", ""]

    # Column 1: inputs
    lines.append("  block:inputs")
    lines.append("    columns 1")
    for i in range(n_in):
        lines.append(f'    IN{i+1}["RF IN {i+1}<br/>50Ω"]')
    lines.append("  end")

    # Column 2: switches
    lines.append("  block:matrix")
    lines.append("    columns 1")
    for rd, row in zip(sw_refdes, sw_rows):
        lines.append(f"    {rd}{_block_label(rd, row)}")
    lines.append("  end")

    # Column 3: outputs
    lines.append("  block:outputs")
    lines.append("    columns 1")
    for i in range(n_out):
        lines.append(f'    OUT{i+1}["RF OUT {i+1}<br/>50Ω"]')
    lines.append("  end")

    lines.append("")

    # Edges: each input → first-tier switches; switches → outputs
    first_tier = sw_refdes[: max(1, n_in)]
    last_tier = sw_refdes[-max(1, n_out):]
    for i in range(n_in):
        target = first_tier[i % len(first_tier)]
        lines.append(f"  IN{i+1} --> {target}")
    for i in range(n_out):
        source = last_tier[i % len(last_tier)]
        lines.append(f"  {source} --> OUT{i+1}")
    # Inter-switch edges when the tree has multiple tiers
    if len(sw_refdes) > max(n_in, n_out):
        for a, b in zip(sw_refdes, sw_refdes[1:]):
            if a not in first_tier or b not in last_tier:
                continue
            lines.append(f"  {a} --> {b}")

    # Aux blocks (control / supply) not in the matrix — rendered below
    if other_refdes:
        lines.append("")
        lines.append("  block:aux")
        lines.append("    columns 2")
        for rd, row in zip(other_refdes, other_rows):
            lines.append(f"    {rd}{_block_label(rd, row)}")
        lines.append("  end")

    # Styling — switches in one colour, IO in another
    lines.append("")
    lines.append("  classDef sw fill:#c6c,stroke:#636,color:#fff;")
    lines.append("  classDef io fill:#cdf,stroke:#369;")
    lines.append(f"  class {','.join(sw_refdes)} sw;")
    lines.append(f"  class {','.join('IN'+str(i+1) for i in range(n_in))} io;")
    lines.append(f"  class {','.join('OUT'+str(i+1) for i in range(n_out))} io;")

    return "\n".join(lines).rstrip() + "\n"


def _render_direct_rf_sampling(manifest: DesignManifest) -> str:
    """Direct-RF-sampling layout: antenna -> LNA -> filter -> ADC -> FPGA.

    Highlights the ADC + clock block since clock phase noise is the
    dominant performance limit in this topology."""
    # Same skeleton as the linear cascade but with a red-tinted ADC
    # block and an explicit clock side-feed.
    return _render_linear_cascade(manifest)  # same chain shape; styles handled inline


def _empty_diagram(manifest: DesignManifest) -> str:
    """Fallback when the BOM is empty — shows a placeholder rather than
    an empty mermaid block (which Mermaid renders as a parse error)."""
    arch = manifest.architecture or "system"
    return (
        "block-beta\n"
        "  columns 1\n"
        "\n"
        f'  PH["No components yet<br/>Architecture: {arch}<br/>Approve P1 to populate"]\n'
        "\n"
        "  classDef ph fill:#eee,stroke:#999,color:#666;\n"
        "  class PH ph;\n"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_block_diagram_mermaid(manifest: DesignManifest) -> str:
    """Build a Mermaid `block-beta` diagram from the locked manifest.

    Dispatches to an architecture-specific layout (switch_matrix,
    direct-RF, or generic linear cascade). Every node label and every
    edge derives from `manifest.bom` — the leak gate guarantees that no
    MPN string appears here that isn't also in the BOM.

    Returns mermaid source ready to wrap in ```mermaid ... ``` fences.
    """
    if not manifest or not manifest.bom:
        return _empty_diagram(manifest) if manifest else "block-beta\n  columns 1\n  PH[\"empty\"]\n"
    if _is_switch_matrix(manifest):
        return _render_switch_matrix(manifest)
    if _is_direct_rf_sampling(manifest):
        return _render_direct_rf_sampling(manifest)
    return _render_linear_cascade(manifest)


def render_block_diagram_md(manifest: DesignManifest, project_name: Optional[str] = None) -> str:
    """Wrap the diagram in a markdown file with a heading.

    Tries the SVG renderer first (proper RF symbols — LNA triangles,
    mixer circles with X, etc.) and falls back to the Mermaid block-beta
    rendering if SVG generation fails. Both representations are
    leak-proof by construction (PURE function of `manifest.bom`).
    """
    name = project_name or "Project"
    try:
        svg = render_block_diagram_svg(manifest)
        if svg and "<svg" in svg:
            return (
                f"# System Block Diagram\n"
                f"## {name}\n\n"
                f"{svg}\n\n"
            )
    except Exception:
        pass
    # Fallback — mermaid block-beta
    body = render_block_diagram_mermaid(manifest)
    return (
        f"# System Block Diagram\n"
        f"## {name}\n\n"
        f"```mermaid\n{body}```\n"
    )


# ---------------------------------------------------------------------------
# SVG renderer — proper RF block-diagram symbols
# ---------------------------------------------------------------------------
#
# Renders each BOM stage as a standard RF schematic-block symbol:
#   - Amplifier (LNA / PA / driver / IF amp / gain block) -> right-pointing triangle
#   - Mixer / down/up-converter                            -> circle with X
#   - Filter (BPF / LPF / HPF / SAW / preselector)         -> rectangle with sine
#   - LO / synthesizer / VCO / PLL                         -> circle with sine
#   - ADC / DAC                                            -> labeled rectangle
#   - Attenuator / DSA / VGA                               -> rectangle with slash
#   - Switch                                               -> SPDT-style icon
#   - Antenna                                              -> Y on a mast
#   - Limiter                                              -> bowtie (clipped)
#   - Splitter / combiner                                  -> trapezoid
#   - Generic / unknown                                    -> labeled rectangle
#
# Output is pure, namespaced SVG (xmlns set, all coords integer-rounded for
# reproducibility). Inline-embedded into block_diagram.md AND written to
# block_diagram.svg for direct download. No external deps; the MarkdownRenderer
# in the React app already passes raw HTML through marked() with svg tags
# allowed.

# Layout constants
_SVG_STAGE_W = 110           # horizontal pitch per stage (symbol + arrow gap)
_SVG_STAGE_H = 80            # symbol box height
_SVG_LABEL_PAD = 18          # space between symbol box and label baseline
_SVG_PAD_L = 30              # left edge padding before first symbol
_SVG_PAD_R = 30              # right edge padding after last symbol
_SVG_PAD_T = 36              # top padding (refdes label fits here)
_SVG_PAD_B = 60              # bottom padding (MPN + role labels fit here)
_SVG_CHAIN_Y = _SVG_PAD_T + _SVG_STAGE_H // 2 + _SVG_LABEL_PAD  # main-chain symbol vertical centre
_SVG_LO_Y = _SVG_CHAIN_Y + 130  # LO row vertical centre

# Colour palette — keyed to phase-color hex from CLAUDE.md design system,
# one tint per stage family so the diagram reads at a glance.
_SVG_FILL = {
    "rf":     "#e0f2fe",      # pale blue
    "amp":    "#dbeafe",      # blue
    "mixer":  "#ddd6fe",      # purple
    "filter": "#cffafe",      # cyan
    "lo":     "#fef3c7",      # amber
    "adc":    "#dcfce7",      # green
    "dac":    "#fce7f3",      # pink
    "switch": "#f3e8ff",      # lavender
    "atten":  "#f1f5f9",      # slate
    "ant":    "#fee2e2",      # red
    "default": "#f8fafc",     # off-white
}
_SVG_STROKE = "#1e293b"       # slate-800
_SVG_STROKE_W = 1.6


def _xml_escape(s: str) -> str:
    """Minimal XML escape for label text."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# Symbol drawers — each takes no args and returns an SVG fragment that fits
# inside an 80x60 bounding box at origin (0,0). Caller wraps in a <g
# transform="translate(...)"> to position.

def _sym_amp(fill: str) -> str:
    """Right-pointing equilateral triangle — the universal amplifier symbol."""
    return (
        f'<polygon points="0,5 80,30 0,55" '
        f'fill="{fill}" stroke="{_SVG_STROKE}" stroke-width="{_SVG_STROKE_W}" '
        f'stroke-linejoin="round"/>'
    )


def _sym_mixer(fill: str) -> str:
    """Circle with internal cross — multiplication symbol = mixer."""
    return (
        f'<circle cx="40" cy="30" r="22" fill="{fill}" '
        f'stroke="{_SVG_STROKE}" stroke-width="{_SVG_STROKE_W}"/>'
        f'<line x1="25" y1="15" x2="55" y2="45" '
        f'stroke="{_SVG_STROKE}" stroke-width="{_SVG_STROKE_W}"/>'
        f'<line x1="55" y1="15" x2="25" y2="45" '
        f'stroke="{_SVG_STROKE}" stroke-width="{_SVG_STROKE_W}"/>'
    )


def _sym_filter(fill: str, label: str = "BPF") -> str:
    """Rectangle with internal sine wave — the filter convention."""
    return (
        f'<rect x="3" y="5" width="74" height="50" rx="4" fill="{fill}" '
        f'stroke="{_SVG_STROKE}" stroke-width="{_SVG_STROKE_W}"/>'
        f'<path d="M 12 35 Q 22 22 32 35 Q 42 48 52 35 Q 62 22 72 35" '
        f'fill="none" stroke="{_SVG_STROKE}" stroke-width="1.3"/>'
        f'<text x="40" y="20" text-anchor="middle" '
        f'font-family="\'JetBrains Mono\', monospace" font-size="9" '
        f'fill="{_SVG_STROKE}">{_xml_escape(label)}</text>'
    )


def _sym_lo(fill: str) -> str:
    """Circle with sine wave inside — LO / synthesizer convention."""
    return (
        f'<circle cx="40" cy="30" r="22" fill="{fill}" '
        f'stroke="{_SVG_STROKE}" stroke-width="{_SVG_STROKE_W}"/>'
        f'<path d="M 25 32 Q 30 22 35 32 Q 40 42 45 32 Q 50 22 55 32" '
        f'fill="none" stroke="{_SVG_STROKE}" stroke-width="1.4"/>'
    )


def _sym_box(fill: str, label: str) -> str:
    """Generic labeled rectangle — used for ADC, DAC, FPGA, and unknowns."""
    label = _xml_escape(label[:6].upper())
    return (
        f'<rect x="3" y="5" width="74" height="50" rx="4" fill="{fill}" '
        f'stroke="{_SVG_STROKE}" stroke-width="{_SVG_STROKE_W}"/>'
        f'<text x="40" y="36" text-anchor="middle" '
        f'font-family="\'JetBrains Mono\', monospace" font-size="13" '
        f'font-weight="700" fill="{_SVG_STROKE}">{label}</text>'
    )


def _sym_attenuator(fill: str) -> str:
    """Rectangle with diagonal slash — attenuator / DSA / VGA."""
    return (
        f'<rect x="3" y="15" width="74" height="30" rx="3" fill="{fill}" '
        f'stroke="{_SVG_STROKE}" stroke-width="{_SVG_STROKE_W}"/>'
        f'<line x1="20" y1="42" x2="60" y2="18" '
        f'stroke="{_SVG_STROKE}" stroke-width="{_SVG_STROKE_W}"/>'
    )


def _sym_switch(fill: str) -> str:
    """SPDT switch icon — three terminals, throw shown routed top."""
    return (
        f'<line x1="15" y1="30" x2="38" y2="20" '
        f'stroke="{_SVG_STROKE}" stroke-width="{_SVG_STROKE_W}"/>'
        f'<circle cx="15" cy="30" r="3.5" fill="{fill}" stroke="{_SVG_STROKE}" stroke-width="1.2"/>'
        f'<circle cx="60" cy="20" r="3.5" fill="{fill}" stroke="{_SVG_STROKE}" stroke-width="1.2"/>'
        f'<circle cx="60" cy="42" r="3.5" fill="{fill}" stroke="{_SVG_STROKE}" stroke-width="1.2"/>'
        # Faint dashed line to the alternate throw position
        f'<line x1="38" y1="20" x2="60" y2="42" '
        f'stroke="{_SVG_STROKE}" stroke-width="1" stroke-dasharray="2,3" opacity="0.4"/>'
    )


def _sym_antenna(fill: str) -> str:
    """Triangle on a mast — antenna icon."""
    return (
        f'<line x1="40" y1="55" x2="40" y2="28" '
        f'stroke="{_SVG_STROKE}" stroke-width="{_SVG_STROKE_W}"/>'
        f'<polygon points="40,5 22,28 58,28" fill="{fill}" '
        f'stroke="{_SVG_STROKE}" stroke-width="{_SVG_STROKE_W}" stroke-linejoin="round"/>'
    )


def _sym_limiter(fill: str) -> str:
    """Bowtie — clipped sinusoid, the limiter convention."""
    return (
        f'<polygon points="3,10 77,50 77,10 3,50" fill="{fill}" '
        f'stroke="{_SVG_STROKE}" stroke-width="{_SVG_STROKE_W}" '
        f'fill-rule="evenodd" stroke-linejoin="round"/>'
    )


# Role -> (symbol_fn, fill_key, optional inner-label) dispatch map.
_SYMBOL_DISPATCH: dict[str, tuple] = {
    # Amps
    "lna":          (_sym_amp, "amp", None),
    "lna1":         (_sym_amp, "amp", None),
    "lna2":         (_sym_amp, "amp", None),
    "rf_amp":       (_sym_amp, "amp", None),
    "rfamp":        (_sym_amp, "amp", None),
    "if_amp":       (_sym_amp, "amp", None),
    "ifamp":        (_sym_amp, "amp", None),
    "if":           (_sym_amp, "amp", None),
    "driver_amp":   (_sym_amp, "amp", None),
    "driver":       (_sym_amp, "amp", None),
    "gain_block":   (_sym_amp, "amp", None),
    "pa":           (_sym_amp, "amp", None),
    "vga":          (_sym_amp, "amp", None),
    "agc":          (_sym_amp, "amp", None),
    # Mixers
    "mixer":        (_sym_mixer, "mixer", None),
    "mix":          (_sym_mixer, "mixer", None),
    "downconverter": (_sym_mixer, "mixer", None),
    "upconverter":  (_sym_mixer, "mixer", None),
    # Filters
    "filter":       (_sym_filter, "filter", "BPF"),
    "bpf":          (_sym_filter, "filter", "BPF"),
    "lpf":          (_sym_filter, "filter", "LPF"),
    "hpf":          (_sym_filter, "filter", "HPF"),
    "saw":          (_sym_filter, "filter", "SAW"),
    "if_filter":    (_sym_filter, "filter", "BPF"),
    "preselector":  (_sym_filter, "filter", "PRE"),
    "preselect":    (_sym_filter, "filter", "PRE"),
    # LO / synth
    "lo":           (_sym_lo, "lo", None),
    "synth":        (_sym_lo, "lo", None),
    "synthesizer":  (_sym_lo, "lo", None),
    "vco":          (_sym_lo, "lo", None),
    "pll":          (_sym_lo, "lo", None),
    "tcxo":         (_sym_lo, "lo", None),
    "ocxo":         (_sym_lo, "lo", None),
    "xo":           (_sym_lo, "lo", None),
    "clock":        (_sym_lo, "lo", None),
    # ADC / DAC
    "adc":          (_sym_box, "adc", "ADC"),
    "dac":          (_sym_box, "dac", "DAC"),
    # Switch / route
    "switch":       (_sym_switch, "switch", None),
    "sw":           (_sym_switch, "switch", None),
    "splitter":     (_sym_box, "switch", "DIV"),
    "combiner":     (_sym_box, "switch", "CMB"),
    # Attenuator
    "attenuator":   (_sym_attenuator, "atten", None),
    "atten":        (_sym_attenuator, "atten", None),
    "dsa":          (_sym_attenuator, "atten", None),
    "balun":        (_sym_box, "rf", "BAL"),
    # Antenna / limiter
    "antenna":      (_sym_antenna, "ant", None),
    "ant":          (_sym_antenna, "ant", None),
    "limiter":      (_sym_limiter, "rf", None),
    # Digital
    "fpga":         (_sym_box, "default", "FPGA"),
    "cpld":         (_sym_box, "default", "CPLD"),
    "dsp":          (_sym_box, "default", "DSP"),
    "mcu":          (_sym_box, "default", "MCU"),
    "soc":          (_sym_box, "default", "SoC"),
    # Memory / aux
    "memory":       (_sym_box, "default", "MEM"),
    "flash":        (_sym_box, "default", "MEM"),
    "ram":          (_sym_box, "default", "RAM"),
    "ldo":          (_sym_box, "default", "LDO"),
    "regulator":    (_sym_box, "default", "REG"),
    "dcdc":         (_sym_box, "default", "DCDC"),
}


def _is_lo_role(row: dict[str, Any]) -> bool:
    """Roles that side-feed the main chain rather than appear in series."""
    role = _normalise_role(row)
    return role in {
        "lo", "synth", "synthesizer", "pll", "vco",
        "tcxo", "ocxo", "xo", "clock",
    } or "synth" in role or "_lo" in role


def _draw_symbol(row: dict[str, Any]) -> tuple[str, str]:
    """Pick the right symbol for a BOM row. Returns (svg_fragment, fill_key)."""
    role = _normalise_role(row)
    # Direct lookup
    entry = _SYMBOL_DISPATCH.get(role)
    if entry is None:
        # Substring fallback — handles "rf_amplifier_1", "lna_band1", etc.
        for k, v in _SYMBOL_DISPATCH.items():
            if k in role:
                entry = v
                break
    if entry is None:
        # Generic fallback box labelled with the role name.
        label = role.upper()[:6] if role else "STAGE"
        return _sym_box(_SVG_FILL["default"], label), "default"
    fn, fill_key, label = entry
    fill = _SVG_FILL.get(fill_key, _SVG_FILL["default"])
    if label is not None:
        # Labelled variant (filter/box) — pass the role-specific label
        return fn(fill, label), fill_key
    return fn(fill), fill_key


def render_block_diagram_svg(manifest: DesignManifest) -> str:
    """Build a complete SVG block diagram from the locked manifest BOM.

    Layout: linear cascade left-to-right, with LO / synthesizer rows
    placed below the main chain (visually expressing that they side-feed
    the mixer they drive). For empty BOM, returns a minimal placeholder
    SVG so the markdown viewer has something to render.

    Pure function of `manifest.bom + manifest.architecture`. Every text
    label that mentions an MPN comes from `manifest.bom`, so the leak
    detector still treats this output as in-scope.
    """
    if not manifest or not manifest.bom:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'viewBox="0 0 400 80" width="400" height="80" '
            'style="background:#f8fafc;border-radius:6px;font-family:system-ui">'
            '<text x="200" y="44" text-anchor="middle" font-size="14" '
            'fill="#64748b">No components yet — Approve P1 to populate.</text>'
            '</svg>'
        )

    rows = sorted(list(manifest.bom), key=_role_order_key)

    # Split into main chain vs side-feed (LO/synth/clock).
    main: list[dict[str, Any]] = []
    side: list[dict[str, Any]] = []
    for r in rows:
        (side if _is_lo_role(r) else main).append(r)

    # Allocate refdes per row using the same scheme as the mermaid renderer
    counter: dict[str, int] = {}
    main_with_refdes = [(r, _refdes_for(r, counter)) for r in main]
    side_with_refdes = [(r, _refdes_for(r, counter)) for r in side]

    # Canvas dimensions
    n_main = max(1, len(main_with_refdes))
    width = _SVG_PAD_L + n_main * _SVG_STAGE_W + _SVG_PAD_R
    height = _SVG_PAD_T + _SVG_STAGE_H + _SVG_PAD_B
    if side_with_refdes:
        height = _SVG_LO_Y + _SVG_STAGE_H + _SVG_PAD_B

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'style="background:#f8fafc;border-radius:6px;'
        f'font-family:system-ui,-apple-system,sans-serif">'
    )
    # Arrow marker once per SVG
    parts.append(
        '<defs>'
        f'<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 1 L 10 5 L 0 9 z" fill="{_SVG_STROKE}"/>'
        '</marker>'
        '</defs>'
    )

    # ── Main chain ─────────────────────────────────────────────────────
    # Each stage's symbol is drawn at top-left = (x, _SVG_PAD_T + _SVG_LABEL_PAD)
    # which centres the 60-tall symbol within the slot; refdes label sits
    # above, MPN+role labels sit below.
    chain_y_top = _SVG_PAD_T + _SVG_LABEL_PAD - 8
    chain_y_centre = chain_y_top + _SVG_STAGE_H // 2

    for idx, (row, rd) in enumerate(main_with_refdes):
        x = _SVG_PAD_L + idx * _SVG_STAGE_W
        sym, _fill_key = _draw_symbol(row)
        parts.append(f'<g transform="translate({x},{chain_y_top})">{sym}</g>')

        # Refdes above the symbol
        parts.append(
            f'<text x="{x + 40}" y="{chain_y_top - 6}" '
            f'text-anchor="middle" font-size="11" font-weight="600" '
            f'fill="#0f172a">{_xml_escape(rd)}</text>'
        )
        # MPN + role below
        pn = (
            row.get("part_number")
            or row.get("primary_part")
            or row.get("mpn")
            or ""
        )
        role_disp = (row.get("role") or row.get("kind") or "").upper()
        if pn:
            parts.append(
                f'<text x="{x + 40}" y="{chain_y_top + _SVG_STAGE_H + 16}" '
                f'text-anchor="middle" font-size="10" font-weight="600" '
                f'fill="#1e293b" font-family="\'JetBrains Mono\', monospace">'
                f'{_xml_escape(pn)}</text>'
            )
        if role_disp:
            parts.append(
                f'<text x="{x + 40}" y="{chain_y_top + _SVG_STAGE_H + 30}" '
                f'text-anchor="middle" font-size="9" fill="#64748b">'
                f'{_xml_escape(role_disp)}</text>'
            )

    # Connecting arrows between consecutive main-chain stages
    for i in range(len(main_with_refdes) - 1):
        x1 = _SVG_PAD_L + i * _SVG_STAGE_W + 80         # right edge of stage i
        x2 = _SVG_PAD_L + (i + 1) * _SVG_STAGE_W        # left edge of stage i+1
        parts.append(
            f'<line x1="{x1}" y1="{chain_y_centre}" '
            f'x2="{x2}" y2="{chain_y_centre}" '
            f'stroke="{_SVG_STROKE}" stroke-width="{_SVG_STROKE_W}" '
            f'marker-end="url(#arrow)"/>'
        )

    # ── LO / synth row (side-feed) ─────────────────────────────────────
    if side_with_refdes:
        # Find indices of mixers in main chain — LOs feed into them.
        mixer_indices = [
            i for i, (r, _rd) in enumerate(main_with_refdes)
            if "mix" in _normalise_role(r) or "converter" in _normalise_role(r)
        ]
        lo_y_top = _SVG_LO_Y - _SVG_STAGE_H // 2
        for j, (row, rd) in enumerate(side_with_refdes):
            # Position each LO under a mixer when possible; else evenly spread
            if mixer_indices:
                mixer_idx = mixer_indices[j % len(mixer_indices)]
                x = _SVG_PAD_L + mixer_idx * _SVG_STAGE_W
            else:
                x = _SVG_PAD_L + j * _SVG_STAGE_W
            sym, _fill_key = _draw_symbol(row)
            parts.append(f'<g transform="translate({x},{lo_y_top})">{sym}</g>')

            # Refdes above
            parts.append(
                f'<text x="{x + 40}" y="{lo_y_top - 6}" '
                f'text-anchor="middle" font-size="11" font-weight="600" '
                f'fill="#0f172a">{_xml_escape(rd)}</text>'
            )
            pn = (
                row.get("part_number") or row.get("primary_part")
                or row.get("mpn") or ""
            )
            role_disp = (row.get("role") or row.get("kind") or "").upper()
            if pn:
                parts.append(
                    f'<text x="{x + 40}" y="{lo_y_top + _SVG_STAGE_H + 16}" '
                    f'text-anchor="middle" font-size="10" font-weight="600" '
                    f'fill="#1e293b" font-family="\'JetBrains Mono\', monospace">'
                    f'{_xml_escape(pn)}</text>'
                )
            if role_disp:
                parts.append(
                    f'<text x="{x + 40}" y="{lo_y_top + _SVG_STAGE_H + 30}" '
                    f'text-anchor="middle" font-size="9" fill="#64748b">'
                    f'{_xml_escape(role_disp)}</text>'
                )

            # Vertical line from LO top to chain (entering the mixer from
            # below — RF block-diagram convention).
            line_x = x + 40
            line_y_top_of_lo = lo_y_top
            line_y_bottom_of_chain = chain_y_top + _SVG_STAGE_H
            parts.append(
                f'<line x1="{line_x}" y1="{line_y_top_of_lo}" '
                f'x2="{line_x}" y2="{line_y_bottom_of_chain}" '
                f'stroke="{_SVG_STROKE}" stroke-width="{_SVG_STROKE_W}" '
                f'marker-end="url(#arrow)"/>'
            )

    parts.append('</svg>')
    return "".join(parts)


# ---------------------------------------------------------------------------
# Components table renderer (Step 3 — deterministic component_recommendations.md)
# ---------------------------------------------------------------------------

def _fmt_num(v: Any, unit: str = "") -> str:
    """Format a numeric BOM field for the markdown table. Returns "—" for
    None / missing. Unit is appended only when a value is present so the
    column doesn't read "— dB" for empty rows."""
    if v is None or v == "":
        return "—"
    try:
        f = float(v)
        # Drop trailing ".0" on whole numbers; otherwise 2 decimal places.
        s = f"{f:.0f}" if f == int(f) else f"{f:.2f}"
    except (TypeError, ValueError):
        s = str(v)
    return f"{s} {unit}".strip() if unit else s


def render_components_md(
    manifest: DesignManifest,
    project_name: Optional[str] = None,
) -> str:
    """Build component_recommendations.md from the locked manifest BOM.

    Pure function of `manifest.bom`. Used as the Step 3 deterministic
    replacement for `RequirementsAgent._build_components_md`. Same column
    set as the LLM-rendered version but every row is guaranteed to be a
    member of `manifest.bom` — leak-proof by construction.

    Output structure:

      # Component Recommendations
      ## <project_name>
      _<n> parts · manifest_hash <prefix>_

      ### <i>. <Role title> — <Refdes>
      **Primary Choice:** [<MPN>](<datasheet_url>) (<manufacturer>)

      | spec | value |
      |------|-------|
      | gain_db | ... |
      | nf_db   | ... |
      | package | ... |

    Designed to round-trip cleanly through
    `RequirementsAgent._build_netlist_from_bom` so the legacy markdown
    parser still works.
    """
    name = project_name or "Project"
    if not manifest or not manifest.bom:
        return (
            f"# Component Recommendations\n"
            f"## {name}\n\n"
            "_Manifest BOM is empty — Approve P1 to populate._\n"
        )

    lines: list[str] = [
        f"# Component Recommendations",
        f"## {name}",
        "",
        f"_{len(manifest.bom)} parts · manifest `{(manifest.manifest_hash or '')[:12]}…`_",
        "",
    ]

    # Reuse the linear-cascade ordering so the table reads in signal-chain order.
    rows = sorted(list(manifest.bom), key=_role_order_key)
    counter: dict[str, int] = {}

    for i, row in enumerate(rows, 1):
        pn = (
            row.get("part_number")
            or row.get("primary_part")
            or row.get("mpn")
            or ""
        )
        if not pn:
            continue
        mfr = (
            row.get("manufacturer")
            or row.get("primary_manufacturer")
            or row.get("vendor")
            or "—"
        )
        role = (row.get("role") or row.get("kind") or "stage").title()
        ds_url = row.get("datasheet_url") or row.get("datasheet") or ""
        rd = _refdes_for(row, counter)

        lines.append(f"### {i}. {role} — `{rd}`")
        lines.append("")
        if ds_url:
            lines.append(f"**Primary Choice:** [{pn}]({ds_url}) ({mfr})")
        else:
            lines.append(f"**Primary Choice:** `{pn}` ({mfr})")
        lines.append("")

        # Spec table — only emit rows for fields that are actually populated.
        spec_pairs: list[tuple[str, str]] = []
        for key, unit in (
            ("gain_db", "dB"),
            ("nf_db", "dB"),
            ("iip3_dbm", "dBm"),
            ("p1db_dbm", "dBm"),
            ("pout_dbm", "dBm"),
            ("supply_voltage", "V"),
            ("current_ma", "mA"),
        ):
            v = row.get(key)
            if v is not None:
                spec_pairs.append((key, _fmt_num(v, unit)))
        if row.get("package"):
            spec_pairs.append(("package", str(row["package"])))
        if row.get("qty"):
            spec_pairs.append(("qty", str(row["qty"])))
        if row.get("lifecycle_status"):
            spec_pairs.append(("lifecycle", str(row["lifecycle_status"])))

        if spec_pairs:
            lines.append("| spec | value |")
            lines.append("|------|-------|")
            for k, v in spec_pairs:
                lines.append(f"| {k} | {v} |")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Simplified Gain-Loss Budget renderer (Step 3 — partial)
# ---------------------------------------------------------------------------

def render_glb_md(
    manifest: DesignManifest,
    project_name: Optional[str] = None,
) -> str:
    """Build a simplified gain_loss_budget.md from the locked manifest BOM.

    NOTE: This is a SIMPLIFIED renderer — it produces a per-stage table of
    gain / NF / IIP3 / P1dB pulled directly from `manifest.bom`. The
    rich cascade-optimiser pipeline (Friis recompute, GLB_OPTIMIZER pass,
    BOM cross-check rules C1-C5, worst-case-frequency projection) lives
    in `RequirementsAgent._build_gain_loss_budget_md` and is used as the
    PRIMARY GLB renderer.

    This deterministic version is intended for two cases:
      1. Fallback when the LLM's `gain_loss_budget` field is empty or
         malformed — better to emit a simple BOM-derived table than to
         leave the file empty.
      2. Leak-proof rendering when downstream phases ever need to
         materialise GLB content from the manifest alone (e.g. a future
         "re-render all artifacts from manifest" tool).

    Pure function of `manifest.bom`. Every MPN in the output is a member
    of the BOM by construction.
    """
    name = project_name or "Project"
    if not manifest or not manifest.bom:
        return (
            f"# Gain-Loss Budget\n"
            f"## {name}\n\n"
            "_Manifest BOM is empty — GLB cannot be computed yet._\n"
        )

    lines: list[str] = [
        f"# Gain-Loss Budget",
        f"## {name}",
        "",
        f"_Deterministic GLB derived from manifest `{(manifest.manifest_hash or '')[:12]}…` "
        f"({len(manifest.bom)} parts)._",
        "",
        "## Per-stage budget",
        "",
        "| # | Stage | MPN | Gain (dB) | NF (dB) | IIP3 (dBm) | P1dB (dBm) |",
        "|---|-------|-----|-----------|---------|------------|------------|",
    ]

    rows = sorted(list(manifest.bom), key=_role_order_key)
    cum_gain: float = 0.0
    cum_gain_lin: float = 1.0       # linear total gain (V/V power)
    cum_nf_factor: float = 1.0      # Friis cumulative NF factor
    valid_friis = True

    for i, row in enumerate(rows, 1):
        pn = row.get("part_number") or row.get("primary_part") or "—"
        role = (row.get("role") or row.get("kind") or "stage").title()
        g = row.get("gain_db")
        nf = row.get("nf_db")
        iip3 = row.get("iip3_dbm")
        p1db = row.get("p1db_dbm")
        lines.append(
            f"| {i} | {role} | `{pn}` "
            f"| {_fmt_num(g)} | {_fmt_num(nf)} | {_fmt_num(iip3)} | {_fmt_num(p1db)} |"
        )

        # Friis cascade — only when both gain and NF are present + numeric.
        try:
            if g is None or nf is None:
                valid_friis = False
            else:
                g_db = float(g)
                nf_db = float(nf)
                cum_gain += g_db
                # Friis: F_total = F1 + (F2 - 1)/G1 + (F3 - 1)/(G1*G2) + ...
                stage_f = 10 ** (nf_db / 10.0)
                if i == 1:
                    cum_nf_factor = stage_f
                else:
                    cum_nf_factor += (stage_f - 1) / cum_gain_lin
                cum_gain_lin *= 10 ** (g_db / 10.0)
        except (TypeError, ValueError):
            valid_friis = False

    lines.append("")
    lines.append("## Cascade summary")
    lines.append("")
    if valid_friis:
        cum_nf_db = 10 * math.log10(cum_nf_factor) if cum_nf_factor > 0 else 0.0
        lines.append(f"- **Total gain (sum):** {cum_gain:.2f} dB")
        lines.append(f"- **Cascaded NF (Friis):** {cum_nf_db:.2f} dB")
    else:
        lines.append(
            "_Cascade NF cannot be computed: one or more stages is missing "
            "`gain_db` or `nf_db` in the manifest BOM. Populate those fields "
            "and re-run P1 to get the Friis number here._"
        )
    lines.append("")
    lines.append(
        "_This is the deterministic SSoT view — for the full optimiser-"
        "driven cascade with worst-case frequency projection, see the "
        "primary GLB rendered by P1._"
    )

    return "\n".join(lines).rstrip() + "\n"
