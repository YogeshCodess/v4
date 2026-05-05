"""
KiCad netlist (`.net`) exporter — P1.4.

Turn the JSON netlist the NetlistAgent produces into a KiCad-compatible
`.net` file (S-expression format, the one `Eeschema → Tools → Generate
Netlist` emits and Pcbnew imports).

Previously the pipeline shipped only a pretty JSON dump — a hardware
engineer could admire it but not import it. Now every P4 run also
produces `netlist.net` alongside `netlist.json`.

Input shape (from `generators.netlist_generator.NetlistGenerator.generate`):

    {
      "project": "MyProject",
      "nodes": [{"id": "U1", "name": "LNA",  "pins": [{"num":"1","name":"RF_IN"}, ...]}],
      "edges": [{"source": "U1", "source_pin":"1",
                 "target": "J1", "target_pin":"1",
                 "signal": "RF_IN", "type":"wire"}]
    }

Extra metadata we honour if present on nodes:
  - `part_number`, `manufacturer`, `footprint`, `value`.

Reference: https://dev-docs.kicad.org/en/file-formats/sexpr-netlist/
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

_KICAD_NETLIST_VERSION = "E"  # KiCad 6+ S-exp format


# ---------------------------------------------------------------------------
# Quoting / sanitisation
# ---------------------------------------------------------------------------

def _quote(s: str | None) -> str:
    """Wrap a value in double-quotes and escape what KiCad's reader doesn't
    tolerate (double-quote, backslash, newline)."""
    if s is None:
        return '""'
    s = str(s).replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return f'"{s}"'


_SAFE_REF_RE = re.compile(r"[^A-Za-z0-9_]")


def _safe_ref(ref: str | None, fallback: str) -> str:
    """Reference designators must be alphanumeric. Fall back when the LLM
    emits something unusable (spaces, punctuation)."""
    if not ref:
        return fallback
    cleaned = _SAFE_REF_RE.sub("", str(ref))
    return cleaned or fallback


# ---------------------------------------------------------------------------
# Net-class heuristic — P2.8
# ---------------------------------------------------------------------------

_NET_CLASS_KEYWORDS: tuple[tuple[str, str], ...] = (
    # (regex on uppercased net name, net-class)
    (r"(^|_)(VCC|VDD|VEE|VSS|V\d+V\d+|\+\d|AVDD|DVDD|VBAT)(_|$)", "POWER"),
    (r"(^|_)(GND|AGND|DGND|VSS|GNDA|GNDD)(_|$)", "GND"),
    (r"(^|_)(CLK|SCLK|MCLK|SCK)(_|$)", "CLK"),
    (r"(^|_)(P|N)$|_\+$|_-$|_P$|_N$", "DIFF_PAIR"),
    (r"(^|_)(RF|ANT|IF|LO|MIX|LNA)(_|$)", "RF_50OHM"),
)


def infer_net_class(net_name: str, signal_type: str | None = None) -> str:
    """Heuristic classifier. Returns one of POWER / GND / DIFF_PAIR /
    RF_50OHM / CLK / SIGNAL (default).

    `signal_type` (if the LLM set it) is trusted first — it's explicit;
    the name regex is a fallback. Keeps the classifier conservative so
    an LLM that mislabels a net as "signal" doesn't accidentally get a
    50Ω impedance-controlled trace assignment.
    """
    stype = (signal_type or "").strip().lower()
    if stype == "power":
        return "POWER"
    if stype == "ground":
        return "GND"
    if stype == "clock":
        return "CLK"
    if stype == "differential":
        return "DIFF_PAIR"

    n = (net_name or "").upper()
    for pattern, cls in _NET_CLASS_KEYWORDS:
        if re.search(pattern, n):
            return cls
    return "SIGNAL"


# ---------------------------------------------------------------------------
# Main exporter
# ---------------------------------------------------------------------------

def netlist_to_kicad(netlist: dict) -> str:
    """Render `netlist` as a KiCad S-expression `.net` file string.

    Minimal output — components section + nets section. No libraries /
    design block / sheets section (PCB designers typically re-link
    footprints after import, and KiCad accepts a bare netlist)."""
    project = netlist.get("project") or "HardwarePipelineProject"
    nodes: list[dict] = list(netlist.get("nodes") or [])
    edges: list[dict] = list(netlist.get("edges") or [])

    out: list[str] = [f"(export (version {_KICAD_NETLIST_VERSION})"]

    # ── design header ────────────────────────────────────────────
    out.append("  (design")
    out.append(f"    (source {_quote(project + '.sch')})")
    out.append(f"    (date {_quote('')})")
    out.append(f'    (tool {_quote("Silicon to Software (S2S) v2 (kicad_netlist.py)")})')
    out.append("  )")

    # ── components ──────────────────────────────────────────────
    out.append("  (components")
    for i, n in enumerate(nodes, start=1):
        ref = _safe_ref(n.get("reference_designator") or n.get("id"), f"U{i}")
        value = n.get("value") or n.get("part_number") or n.get("name") or ref
        footprint = n.get("footprint") or ""
        out.append(f"    (comp (ref {_quote(ref)})")
        out.append(f"      (value {_quote(value)})")
        if footprint:
            out.append(f"      (footprint {_quote(footprint)})")
        # Custom fields — manufacturer / part number are non-standard but
        # KiCad tolerates them and displays them in the BOM generator.
        mfr = n.get("manufacturer")
        if mfr:
            out.append(f"      (property (name {_quote('Manufacturer')}) (value {_quote(mfr)}))")
        mpn = n.get("part_number")
        if mpn:
            out.append(f"      (property (name {_quote('MPN')}) (value {_quote(mpn)}))")
        out.append("    )")
    out.append("  )")

    # ── nets ────────────────────────────────────────────────────
    # Group edges by net_name. Each unique net gets one (net ...) block
    # with every node (ref+pin) that touches it as a child.
    nets_by_name: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for e in edges:
        net = e.get("signal") or e.get("net_name") or ""
        if not net:
            continue
        src_ref = _safe_ref(e.get("source") or e.get("from_instance"), "")
        tgt_ref = _safe_ref(e.get("target") or e.get("to_instance"), "")
        src_pin = str(e.get("source_pin") or e.get("from_pin") or "")
        tgt_pin = str(e.get("target_pin") or e.get("to_pin") or "")
        if src_ref and src_pin:
            nets_by_name[net].append((src_ref, src_pin))
        if tgt_ref and tgt_pin:
            nets_by_name[net].append((tgt_ref, tgt_pin))

    # Deduplicate (same ref+pin can show up on multiple edges when a net
    # is multi-drop — KiCad expects each node listed once per net).
    def _uniq(items: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
        seen, out_items = set(), []
        for ref, pin in items:
            k = (ref, pin)
            if k in seen:
                continue
            seen.add(k)
            out_items.append((ref, pin))
        return out_items

    out.append("  (nets")
    for code, (name, ends) in enumerate(sorted(nets_by_name.items()), start=1):
        cls = infer_net_class(name)
        out.append(f"    (net (code {code}) (name {_quote(name)})")
        out.append(f"      (property (name {_quote('netclass')}) (value {_quote(cls)}))")
        for ref, pin in _uniq(ends):
            out.append(f"      (node (ref {_quote(ref)}) (pin {_quote(pin)}))")
        out.append("    )")
    out.append("  )")

    out.append(")")
    return "\n".join(out) + "\n"


def save_kicad_netlist(netlist: dict, output_dir: str | Path,
                       *, filename: str = "netlist.net") -> Path:
    """Render + write to disk. Returns the path."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / filename
    out_path.write_text(netlist_to_kicad(netlist), encoding="utf-8")
    log.info("kicad_netlist.written path=%s components=%d nets=%d",
             out_path, len(netlist.get("nodes") or []),
             len({e.get("signal") or e.get("net_name")
                  for e in netlist.get("edges") or [] if e.get("signal") or e.get("net_name")}))
    return out_path
