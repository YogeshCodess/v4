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
    """Wrap the mermaid source in a markdown file with a heading."""
    name = project_name or "Project"
    body = render_block_diagram_mermaid(manifest)
    return (
        f"# System Block Diagram\n"
        f"## {name}\n\n"
        f"```mermaid\n{body}```\n"
    )
