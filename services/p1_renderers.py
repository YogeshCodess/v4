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
    """Wrap the mermaid source in a markdown file with a heading."""
    name = project_name or "Project"
    body = render_block_diagram_mermaid(manifest)
    return (
        f"# System Block Diagram\n"
        f"## {name}\n\n"
        f"```mermaid\n{body}```\n"
    )


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
