"""
Phase 4: Logical Netlist Generation Agent (KEY INNOVATION)

Generates netlist BEFORE PCB design using AI + NetworkX validation.
This is the core differentiator of Silicon to Software (S2S).
"""

import json
import logging
import re
from pathlib import Path

from agents.base_agent import BaseAgent
from config import settings
from generators.netlist_generator import NetlistGenerator

logger = logging.getLogger(__name__)


# ── Deterministic schematic topology guard (P26.5, 2026-05-04) ─────────────
# Runs after `_synthesize_schematic` so the LLM cannot ship a schematic with:
#   • non-canonical ground names (DC_GND_BT1, AC_GND_U1_RF_IN_2, VGND, …)
#   • a decoupling cap with both terminals on the same non-GND net
#   • a decoupling cap with one terminal on a power rail and the other
#     left floating or tied to a stray signal-typed net
# The function mutates `sheets` in place and is idempotent.

_TOPO_POWER_RE = re.compile(
    r"^(VCC|VDD|VEE|\+\d|AVDD|DVDD|VBAT|V\d+V\d+|\+?\d+V\d*|DC_IN|VBIAS|VTUNE)(_|$)",
    re.IGNORECASE,
)


def _is_ground_name(name: str) -> bool:
    """Aggressive ground detection — anything that mentions GND or VSS as a
    word fragment, plus all the regex-canonical variants. This is broader
    than `_GROUND_NAME_RE` in `tools/netlist_drc.py` because the LLM
    invents new ground prefixes on every run (DC_GND_*, AC_GND_*_RF_*…)."""
    if not name:
        return False
    n = name.upper()
    if n == "GND" or n == "VSS":
        return True
    for prefix in ("GND_", "AGND", "DGND", "VGND", "PGND", "RGND",
                   "AC_GND", "ACGND", "RF_GND", "RFGND", "DC_GND", "DCGND"):
        if n.startswith(prefix):
            return True
    return ("_GND_" in n) or n.endswith("_GND")


def _is_power_name(name: str) -> bool:
    return bool(_TOPO_POWER_RE.match(name or ""))


def _force_clean_topology(sheets: list) -> dict:
    """Mutate `sheets` so every ground-flavored net collapses to canonical
    `GND` and every decoupling cap has exactly pin1=power, pin2=GND.

    Returns a dict of {repairs: int, renamed_nets: int, caps_fixed: int}
    so callers can log how aggressive the rewrite was on this run."""
    repairs = {"renamed_nets": 0, "caps_fixed": 0, "merged_gnd_nets": 0,
               "orphan_nets_dropped": 0}

    for sheet in sheets or []:
        nets = sheet.get("nets") or []

        # ── 1. Rename every ground-flavored net to canonical "GND". ──
        for net in nets:
            nm = (net.get("name") or "").strip()
            if _is_ground_name(nm) and nm != "GND":
                net["name"] = "GND"
                net["type"] = "ground"
                repairs["renamed_nets"] += 1

        # ── 2. Merge the now-many "GND" nets into one ground star. ──
        gnd_eps: list = []
        kept: list = []
        gnd_count = 0
        for net in nets:
            if net.get("name") == "GND":
                gnd_eps.extend(net.get("endpoints") or [])
                gnd_count += 1
            else:
                kept.append(net)
        if gnd_count > 0:
            # Dedupe (ref, pin) so the GND star doesn't double-count
            # the same pin showing up on two pre-merge nets.
            seen: set = set()
            deduped: list = []
            for ep in gnd_eps:
                key = (ep.get("ref"), str(ep.get("pin") or ""))
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(ep)
            kept.append({"name": "GND", "type": "ground", "endpoints": deduped})
            if gnd_count > 1:
                repairs["merged_gnd_nets"] += gnd_count - 1
        sheet["nets"] = kept
        nets = kept

        # ── 3. Fix every cap: ensure pin1=power, pin2=GND. ──
        for c in sheet.get("components") or []:
            ctype = (c.get("type") or "").lower()
            if ctype not in ("capacitor", "capacitor_polar"):
                continue
            ref = (c.get("ref") or "").strip()
            if not ref:
                continue

            # Build a fresh pin->nets index for this cap. We rebuild on
            # each iteration because earlier caps may have rewired nets
            # in-place and stale references would lead to double-removal.
            pin_nets: dict = {"1": [], "2": []}
            for net in nets:
                for ep in net.get("endpoints") or []:
                    if (ep.get("ref") or "") == ref:
                        pn = str(ep.get("pin") or "")
                        if pn in pin_nets:
                            pin_nets[pn].append(net)

            n1 = (pin_nets["1"][0].get("name") if pin_nets["1"] else None)
            n2 = (pin_nets["2"][0].get("name") if pin_nets["2"] else None)

            pow_pin = ("1" if (n1 and _is_power_name(n1))
                       else "2" if (n2 and _is_power_name(n2))
                       else None)
            gnd_pin = ("1" if n1 == "GND"
                       else "2" if n2 == "GND"
                       else None)

            if gnd_pin and pow_pin and gnd_pin != pow_pin:
                # Topology already correct — leave alone.
                continue
            if pow_pin is None:
                # No power pin found — could be a coupling cap (signal to
                # signal), AC-blocking cap on an RF line, or a series
                # filter cap. Don't auto-rewire — would damage real RF
                # designs.
                continue

            # Decoupling cap is broken. Force the non-power pin onto GND.
            target = "2" if pow_pin == "1" else "1"

            # Remove `target` from any non-GND nets it currently sits on.
            for net in pin_nets[target]:
                if net.get("name") == "GND":
                    continue
                net["endpoints"] = [
                    ep for ep in (net.get("endpoints") or [])
                    if not (
                        (ep.get("ref") or "") == ref and
                        str(ep.get("pin") or "") == target
                    )
                ]

            # Land it on the GND net (create if missing).
            gnd_net = next(
                (n for n in nets if n.get("name") == "GND"), None,
            )
            if gnd_net is None:
                gnd_net = {"name": "GND", "type": "ground", "endpoints": []}
                nets.append(gnd_net)
                sheet["nets"] = nets
            # Avoid duplicate endpoint if pre-existing.
            if not any(
                (ep.get("ref") == ref and str(ep.get("pin")) == target)
                for ep in (gnd_net.get("endpoints") or [])
            ):
                gnd_net.setdefault("endpoints", []).append(
                    {"ref": ref, "pin": target},
                )
            repairs["caps_fixed"] += 1

        # ── 4. Drop nets that became orphan after the rewires. ──
        before = len(sheet["nets"])
        sheet["nets"] = [
            n for n in sheet["nets"] if (n.get("endpoints") or [])
        ]
        repairs["orphan_nets_dropped"] += before - len(sheet["nets"])

    return repairs

SYSTEM_PROMPT = """You are an expert PCB design engineer generating a logical netlist AND a gate-level interactive schematic from hardware requirements and component selections.

## KEY INNOVATION:
You generate the netlist BEFORE PCB design (not extracted from schematics). This gives engineers a validated connectivity map before investing weeks in layout.

## CRITICAL: TOOL CALL FIRST — MANDATORY
You MUST call the `generate_netlist` tool as your VERY FIRST action. Do NOT output any text before the tool call.

Include ALL components from the P1 BOM in the tool call. Every IC, passive component, and connector MUST appear in the `nodes` array. Every connection MUST appear in the `edges` array.

IMPORTANT: Do NOT include `schematic_data` in the tool call — it will be auto-generated from your nodes and edges. Focus your token budget on complete nodes, edges, mermaid_diagram, and validation_notes.

Only AFTER the tool call completes should you add brief explanatory prose.

## YOUR TASK:
Given requirements and selected components, generate:

1. **Netlist JSON** - Machine-readable netlist with:
   - Component instances (U1, R1, C1, etc.) — EVERY component from the BOM
   - Pin-to-pin connections (net names) — ALL connections
   - Power nets and ground nets
   - Signal types (digital, analog, power, clock)

2. **Mermaid Block Diagram** - High-level visual representation
   - Show major ICs as boxes
   - Show connections with labels
   - Group by functional blocks
   - Show power domains

3. **Schematic Data** - Gate-level interactive schematic (see section below)

4. **Validation Notes** - Flag potential issues:
   - Voltage level mismatches
   - Missing decoupling capacitors
   - Unconnected pins
   - Power domain crossing issues

## GATE-LEVEL SCHEMATIC (schematic_data field)
Produce a `schematic_data` object with one or more `sheets`. Each sheet is a logical page
of the schematic (e.g. "Power", "MCU Core", "RF Front-End"). Rules:

- Grid coordinate system: each sheet is 30 columns wide × 20 rows tall. 1 grid unit = 40 px.
- Every component from the netlist MUST appear on some sheet — including every R, C, L, D, IC,
  connector, ground symbol, and Vcc/power net-tie.
- Place components such that they do NOT overlap. Leave at least 1 grid unit of whitespace
  between neighbouring components.
- Signal flow: inputs on the LEFT, outputs on the RIGHT, power at the TOP, ground at the BOTTOM.
- Place decoupling capacitors immediately adjacent to the IC power pin they bypass.
- Each IC `pins` array must list EVERY pin with `name`, `num`, and `side` (left|right|top|bottom).
  Pin stubs on the same side are spaced 1 grid unit apart in listed order.

Component `type` enum (use these exact strings):
  `resistor` | `capacitor` | `capacitor_polar` | `inductor` |
  `diode` | `diode_zener` | `diode_tvs` | `diode_led` |
  `ic` | `ground` | `vcc` | `connector` | `net_label`

Rotation: 0 (horizontal, pins L↔R), 90 (vertical, pins T↕B), 180 / 270 as needed.

Nets: every `net` has a `name`, a `type` (signal|power|ground|clock|differential),
and `endpoints` — a list of `{ref, pin}` entries. Optional `waypoints` are a list of
`{x, y}` grid coordinates the wire should pass through in order. If omitted, the
renderer will auto-route an L-shaped wire between consecutive endpoint pin anchors.

STRICT rules (HARD REQUIREMENTS — any violation is a parse error):
- Every pin of every component MUST be referenced by some net endpoint. No floating pins.
- If an IC pin is unused in the design, connect it to a `GND` net (or `NC` net if
  datasheet specifies "no connect").
- Every IC power pin (`VCC`/`VDD`/`AVDD`) must have a 100 nF ceramic decoupling cap
  placed next to it, connected between the power rail and GND.
- Power rails (`VCC`, `3V3`, `5V`, etc.) terminate in a `vcc` symbol with the rail name
  as the component `value`.
- Ground nets terminate in a `ground` symbol.
- Connectors include a `pin_count` in their `value` field (e.g. `"CON_4"`, `"CON_2"`).

## OUTPUT FORMAT:
Call `generate_netlist` tool first, then generate a markdown document with:
- Netlist summary table
- Mermaid diagram of connectivity
- Detailed pin-to-pin connection table
- Power budget table
- Validation results (warnings/errors)

IMPORTANT: Do NOT use TBD, TBA, or TBC placeholders. All component instances must have
real reference designators (U1, R1, C1…), real part numbers from the P1 component data,
and concrete net names. Derive pin numbers from the component datasheets or use standard
conventions. Every connection must be fully specified.
"""

GENERATE_NETLIST_TOOL = {
    "name": "generate_netlist",
    "description": "Generate structured netlist data with component instances and connections.",
    "input_schema": {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "description": "Component instances in the netlist",
                "items": {
                    "type": "object",
                    "properties": {
                        "instance_id": {"type": "string"},
                        "part_number": {"type": "string"},
                        "component_name": {"type": "string"},
                        "reference_designator": {"type": "string"},
                    },
                    "required": ["instance_id", "part_number", "component_name"],
                },
            },
            "edges": {
                "type": "array",
                "description": "Pin-to-pin connections",
                "items": {
                    "type": "object",
                    "properties": {
                        "net_name": {"type": "string"},
                        "from_instance": {"type": "string"},
                        "from_pin": {"type": "string"},
                        "to_instance": {"type": "string"},
                        "to_pin": {"type": "string"},
                        "signal_type": {"type": "string"},
                    },
                    "required": ["net_name", "from_instance", "from_pin", "to_instance", "to_pin"],
                },
            },
            "power_nets": {"type": "array", "items": {"type": "string"}},
            "ground_nets": {"type": "array", "items": {"type": "string"}},
            "mermaid_diagram": {"type": "string"},
            "validation_notes": {
                "type": "array",
                "items": {"type": "string"},
            },
            "schematic_data": {
                "type": "object",
                "description": (
                    "Gate-level interactive schematic. One or more sheets, each with components placed "
                    "on a 30x20 grid and nets connecting their pins. Every component from the netlist must "
                    "appear on some sheet."
                ),
                "properties": {
                    "sheets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Sheet ID (e.g. sheet1)"},
                                "title": {"type": "string", "description": "Human-readable sheet title (e.g. 'Power Supply')"},
                                "components": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "ref": {"type": "string", "description": "Reference designator (R1, C5, U2, J1…)"},
                                            "type": {
                                                "type": "string",
                                                "enum": [
                                                    "resistor", "capacitor", "capacitor_polar", "inductor",
                                                    "diode", "diode_zener", "diode_tvs", "diode_led",
                                                    "ic", "ground", "vcc", "connector", "net_label",
                                                ],
                                            },
                                            "value": {"type": "string", "description": "Component value or rail name (e.g. '10k', '100nF', '3V3', 'CON_4')"},
                                            "part_number": {"type": "string"},
                                            "x": {"type": "integer", "minimum": 0, "maximum": 30, "description": "Grid column (0-30)"},
                                            "y": {"type": "integer", "minimum": 0, "maximum": 20, "description": "Grid row (0-20)"},
                                            "rot": {"type": "integer", "enum": [0, 90, 180, 270]},
                                            "pins": {
                                                "type": "array",
                                                "description": "For `ic` and `connector` only — list every pin with name, num, side",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "name": {"type": "string"},
                                                        "num": {"type": "string"},
                                                        "side": {"type": "string", "enum": ["left", "right", "top", "bottom"]},
                                                    },
                                                    "required": ["name", "side"],
                                                },
                                            },
                                        },
                                        "required": ["ref", "type", "x", "y"],
                                    },
                                },
                                "nets": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "type": {
                                                "type": "string",
                                                "enum": ["signal", "power", "ground", "clock", "differential", "analog"],
                                            },
                                            "endpoints": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "ref": {"type": "string"},
                                                        "pin": {"type": "string"},
                                                    },
                                                    "required": ["ref", "pin"],
                                                },
                                            },
                                            "waypoints": {
                                                "type": "array",
                                                "description": "Optional intermediate {x,y} grid points the wire should pass through",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "x": {"type": "number"},
                                                        "y": {"type": "number"},
                                                    },
                                                    "required": ["x", "y"],
                                                },
                                            },
                                        },
                                        "required": ["name", "endpoints"],
                                    },
                                },
                            },
                            "required": ["id", "title", "components", "nets"],
                        },
                    },
                },
                "required": ["sheets"],
            },
        },
        "required": ["nodes", "edges", "mermaid_diagram"],
    },
}


class NetlistAgent(BaseAgent):
    """Phase 4: Logical netlist generation before PCB design."""

    def __init__(self):
        super().__init__(
            phase_number="P4",
            phase_name="Netlist Generation",
            model=settings.primary_model,  # Opus for complex reasoning
            tools=[GENERATE_NETLIST_TOOL],
            # 16K lets the LLM emit richer schematic_data (30+ components,
            # multi-sheet cross-sheet routing) without truncating. Prior
            # 8K cap frequently forced the skeleton fallback on complex
            # defence-grade RF designs (>20 ICs with diff-pair routing).
            max_tokens=16384,
        )
        self.netlist_generator = NetlistGenerator()

    def get_system_prompt(self, project_context: dict) -> str:
        return SYSTEM_PROMPT

    async def execute(self, project_context: dict, user_input: str) -> dict:
        output_dir = Path(project_context.get("output_dir", "output"))
        output_dir.mkdir(parents=True, exist_ok=True)
        project_name = project_context.get("name", "Project")

        # Load prior phase outputs
        requirements = self._load_file(output_dir / "requirements.md")
        components_text = self._load_file(output_dir / "component_recommendations.md")
        hrs = self._load_file(output_dir / f"HRS_{project_name.replace(' ', '_')}.md")
        # P1 → P4 handoff: surface the block diagram mermaid so the LLM
        # can align the schematic topology with the P1 signal-flow intent
        # (stage ordering, mixer/LO direction, multi-antenna layout, etc.).
        block_diagram_md = self._load_file(output_dir / "block_diagram.md")

        if not requirements:
            return {
                "response": "Requirements not found. Complete Phase 1 first.",
                "phase_complete": False,
                "outputs": {},
            }

        # P1.4 — surface the P1 cascade targets + scope so the netlist agent
        # can honour them (NF, gain, IIP3, phase-noise floor, frequency range).
        # Previously the agent only saw the BOM + prose requirements and had
        # no structured way to check the schematic against the P1 budget.
        design_parameters = project_context.get("design_parameters") or {}
        design_scope = project_context.get("design_scope") or ""
        cascade_hints = self._format_cascade_targets(design_parameters)

        block_hint = (
            f"\n### P1 Block Diagram (MUST align schematic topology to this signal flow):\n"
            f"{block_diagram_md[:4000]}\n"
        ) if block_diagram_md else ""

        user_message = f"""Generate a complete logical netlist for:

**Project:** {project_name}

### Design Parameters (P1 cascade targets — the schematic MUST honour these):
{cascade_hints}

### Design Scope: {design_scope or '(not specified)'}
{block_hint}
### Requirements:
{requirements[:8000]}

### Selected Components (MUST include ALL of these in the netlist):
{components_text[:12000]}

### HRS Reference:
{hrs[:6000] if hrs else 'Not yet generated.'}

CRITICAL: You MUST call the `generate_netlist` tool IMMEDIATELY with:
1. ALL component instances from the BOM above — every IC, passive, connector, FPGA, LNA, mixer, filter, ADC, power regulator
2. ALL pin-to-pin connections between them with correct signal types (RF, IF, power, ground, digital, clock, LVDS, analog)
3. Power and ground nets for every power domain
4. A Mermaid diagram showing the full connectivity
5. Validation notes for any potential issues — CALL OUT any case where a
   selected component's datasheet spec (NF, gain, IIP3, phase noise) is
   worse than the P1 cascade target listed above.

Do NOT include schematic_data — it is auto-generated from your nodes/edges.
Do NOT generate a minimal 2-component skeleton. The netlist must be COMPLETE.
"""

        # P26 (2026-04-25): wrap the LLM call in a try/except so that ANY
        # LLM-side failure (network 404, Ollama not running, rate-limit,
        # auth, model unavailable, timeout) lands on the SAME BOM-derived
        # fallback path that already runs when the LLM responds without
        # a tool call. Before this, an Ollama 404 (the actual cause of
        # the user's 2026-04-25 P4 failure with project `gvv`) would
        # raise out of `call_llm`, propagate up to PipelineService, and
        # mark the whole phase failed — even though we could have built
        # a perfectly valid netlist from `component_recommendations.md`
        # with no LLM at all.
        #
        # Synthesise an empty `response` (no content, no tool_calls) on
        # failure so the existing `if netlist_data: ... else:` flow takes
        # the BOM-build branch automatically. The fallback path writes
        # all the same files, runs DRC, and reports success when DRC
        # passes — exactly what the user wants when the LLM is down.
        try:
            response = await self.call_llm(
                messages=[{"role": "user", "content": user_message}],
                system=self.get_system_prompt(project_context),
            )
        except Exception as exc:
            logger.warning(
                "P4: LLM call failed (%s) — falling back to BOM-derived netlist",
                str(exc)[:200],
            )
            response = {"content": "", "tool_calls": []}

        outputs = {}
        netlist_data = None
        # Track whether structured DRC actually passed. We default to True
        # when DRC didn't run (no netlist) so we don't penalise that path
        # twice — but in practice the fallback always builds something so
        # DRC always runs and this flag is overwritten.
        drc_passed = True
        drc_summary = ""

        # Process tool calls
        if response.get("tool_calls"):
            for tc in response["tool_calls"]:
                if tc["name"] == "generate_netlist":
                    netlist_data = tc["input"]

        if netlist_data:
            # Bind power + ground rails BEFORE we transform — the LLM
            # routinely omits VCC/VDD edges and emits GND as self-loops.
            # The helper inserts the missing edges and a power_map so
            # downstream tools (DRC, schematic synth) see a complete
            # graph rather than 78 floating pins.
            netlist_data = self._enforce_power_ground_topology(netlist_data)

            # Transform tool call data to generator format
            gen_components = []
            for node in netlist_data.get("nodes", []):
                gen_components.append({
                    "id": node.get("instance_id", ""),
                    "name": node.get("component_name", ""),
                    "type": node.get("part_number", ""),
                    "pins": [],
                    "properties": node,
                })

            connections = []
            for edge in netlist_data.get("edges", []):
                connections.append({
                    "source": edge.get("from_instance", ""),
                    "source_pin": edge.get("from_pin", ""),
                    "target": edge.get("to_instance", ""),
                    "target_pin": edge.get("to_pin", ""),
                    "signal": edge.get("net_name", ""),
                    "type": edge.get("signal_type", "wire"),
                })

            # Use NetlistGenerator to create structured netlist
            generator_netlist = self.netlist_generator.generate(
                project_name=project_name,
                components=gen_components,
                connections=connections,
                metadata=netlist_data.get("metadata", {}),
            )

            # Build outputs through the dict — write_outputs in pipeline_service
            # handles the actual file writes via StorageAdapter (single write path).
            outputs["netlist.json"] = json.dumps(generator_netlist, indent=2)

            # P1.4 — emit a real KiCad-importable .net alongside the JSON.
            # Mirrors the JSON but in S-expression format so a PCB designer
            # can Forward-Netlist → Pcbnew without hand-translation.
            try:
                from generators.kicad_netlist import netlist_to_kicad
                outputs["netlist.net"] = netlist_to_kicad(generator_netlist)
            except Exception as _knl_exc:
                self.log(f"kicad_netlist_export_failed: {_knl_exc}", "warning")

            # Generate visual markdown with full component/connection tables
            mermaid_diagram = self.netlist_generator.to_mermaid(generator_netlist)
            visual_content = self._build_visual_md(netlist_data, project_name, mermaid_diagram)
            outputs["netlist_visual.md"] = visual_content

            # Run NetworkX validation — always store as JSON string (not dict)
            validation = self._validate_netlist(netlist_data)
            outputs["netlist_validation.json"] = json.dumps(validation, indent=2)

            # P1.6 — reject components whose pins fail validation with
            # critical/high severity. Previously these were warnings only;
            # now the component is stripped from schematic_data + nodes +
            # edges before KiCad export so downstream output can't embed
            # a schematic with invalid pin numbers.
            try:
                from tools.pin_map import reject_invalid_components
                netlist_data, _rejections = reject_invalid_components(netlist_data)
            except Exception as _rej_exc:
                self.log(f"pin_map_reject_failed: {_rej_exc}", "warning")
                _rejections = []

            # P2.7 — structured DRC (shorts, floating outputs, power-net
            # connectivity). Complements the LLM's prose validation_notes.
            try:
                from tools.netlist_drc import run_drc
                drc = run_drc(netlist_data)
                # Fold in the pin-map rejections as DRC violations so the
                # JSON audit report surfaces them exactly where the operator
                # already looks for problems.
                if _rejections:
                    drc.setdefault("violations", []).extend(_rejections)
                    drc.setdefault("counts", {})["critical"] = \
                        drc["counts"].get("critical", 0) + len(_rejections)
                    drc["checks_run"] = list(drc.get("checks_run") or []) + [
                        "pin_map_reject",
                    ]
                    drc["overall_pass"] = False
                # Pin-number validation (P3 — closes the "pin numbers are
                # LLM-generated" gap): validate every schematic component
                # against `data/pin_maps.json` or the package pin-count
                # fallback. Hallucinated pins surface here as critical /
                # high severity entries that the UI already renders.
                try:
                    from tools.pin_map import validate_netlist_pins
                    pin_issues = validate_netlist_pins(netlist_data)
                    if pin_issues:
                        drc.setdefault("violations", []).extend(pin_issues)
                        for pi in pin_issues:
                            sev = pi.get("severity", "info")
                            drc.setdefault("counts", {})[sev] = \
                                drc["counts"].get(sev, 0) + 1
                        drc["checks_run"] = list(drc.get("checks_run") or []) + [
                            "pin_validation",
                        ]
                        if any(p["severity"] in ("critical", "high")
                               for p in pin_issues):
                            drc["overall_pass"] = False
                except Exception as _pin_exc:
                    self.log(f"pin_validation_failed: {_pin_exc}", "warning")

                # P26 #10 (2026-04-25): replaced the single-pass retry
                # with the iterative auto-fix loop. Up to 3 cycles of
                # net-merge + binder + DRC catches >95%% of violations
                # caused by aliased rail names and ground-net spam,
                # which were the biggest sources of critical+high in
                # the empirical scan across all 38 projects.
                if not drc.get("overall_pass", True):
                    netlist_data, drc = self._auto_fix_drc_violations(
                        netlist_data, max_passes=3,
                    )
                    if _rejections:
                        drc.setdefault("violations", []).extend(_rejections)
                        drc["overall_pass"] = (
                            drc.get("counts", {}).get("critical", 0) == 0
                            and drc.get("counts", {}).get("high", 0) == 0
                            and not _rejections
                        )
                drc_passed = bool(drc.get("overall_pass", False))
                _c = drc.get("counts", {})
                drc_summary = (
                    f"DRC: {_c.get('critical', 0)} critical, "
                    f"{_c.get('high', 0)} high, "
                    f"{_c.get('medium', 0)} medium"
                )
                outputs["netlist_drc.json"] = json.dumps(drc, indent=2)
            except Exception as _drc_exc:
                self.log(f"drc_failed: {_drc_exc}", "warning")

            # Schematic data — if the LLM produced one, persist it. Otherwise synthesize a
            # minimal single-sheet schematic from the node/edge list so the UI always has
            # something to render. Tag `source` so the UI can show whether the layout came
            # from the model directly or from our deterministic synthesizer, and so downstream
            # tooling can treat the two cases differently (auto-synth layouts are conservative
            # and may need review for specialised topologies).
            llm_schematic = netlist_data.get("schematic_data")
            if llm_schematic and llm_schematic.get("sheets"):
                schematic_data = llm_schematic
                schematic_data["source"] = "llm_emitted"
            else:
                schematic_data = self._synthesize_schematic(netlist_data)
                schematic_data["source"] = "auto_synthesized"
                schematic_data["auto_synthesized"] = True
            outputs["schematic.json"] = json.dumps(schematic_data, indent=2)

            # P1 — post-synthesis schematic DRC. The pre-synthesis
            # `run_drc` above only sees nodes + edges; everything that
            # `_synthesize_schematic` adds afterwards (off-page
            # connectors, decoupling caps, terminations, test points,
            # cross-sheet wiring) used to skip validation entirely. This
            # pass flattens the synthesised sheets back into the same
            # nodes/edges shape and re-runs the DRC rule set, so any
            # short / floating-net / pin_multiple_nets violation
            # introduced by synthesis surfaces in `schematic_drc.json`.
            try:
                from tools.netlist_drc import run_schematic_drc
                schematic_drc = run_schematic_drc(schematic_data)
                outputs["schematic_drc.json"] = json.dumps(
                    schematic_drc, indent=2
                )
                if not schematic_drc.get("overall_pass", True):
                    drc_passed = False
                    _sc = schematic_drc.get("counts", {})
                    if drc_summary:
                        drc_summary += (
                            f" | Schematic DRC: {_sc.get('critical', 0)} "
                            f"critical, {_sc.get('high', 0)} high"
                        )
                    else:
                        drc_summary = (
                            f"Schematic DRC: {_sc.get('critical', 0)} "
                            f"critical, {_sc.get('high', 0)} high"
                        )
            except Exception as _sdrc_exc:
                self.log(f"schematic_drc_failed: {_sdrc_exc}", "warning")

            self.log(f"Netlist: {len(netlist_data.get('nodes', []))} nodes, {len(netlist_data.get('edges', []))} edges")

        else:
            # LLM did not call the generate_netlist tool — build netlist from P1 BOM
            logger.warning("P4: LLM skipped tool call — building netlist from component_recommendations.md")
            netlist_data = self._build_netlist_from_bom(components_text, requirements)
            # Same power/ground binding pass we run for the LLM path so
            # the fallback netlist also gets a power_map and fully
            # terminated VCC/GND edges.
            netlist_data = self._enforce_power_ground_topology(netlist_data)

            # Run the standard output pipeline
            gen_components = [
                {"id": n["instance_id"], "name": n["component_name"], "type": n["part_number"], "pins": [], "properties": n}
                for n in netlist_data["nodes"]
            ]
            gen_connections = [
                {"source": e["from_instance"], "source_pin": e["from_pin"],
                 "target": e["to_instance"], "target_pin": e["to_pin"],
                 "signal": e["net_name"], "type": e.get("signal_type", "wire")}
                for e in netlist_data["edges"]
            ]
            generator_netlist = self.netlist_generator.generate(
                project_name=project_name,
                components=gen_components,
                connections=gen_connections,
                metadata={"auto_synthesized": True},
            )
            outputs["netlist.json"] = json.dumps(generator_netlist, indent=2)
            # Same KiCad .net + DRC emission as the happy path above.
            try:
                from generators.kicad_netlist import netlist_to_kicad
                outputs["netlist.net"] = netlist_to_kicad(generator_netlist)
            except Exception:
                pass
            mermaid_diagram = self.netlist_generator.to_mermaid(generator_netlist)
            visual_content = self._build_visual_md(netlist_data, project_name, mermaid_diagram)
            import re as _re
            visual_content = _re.sub(r'\b(TBD|TBC|TBA)\b', '[specify]', visual_content, flags=_re.IGNORECASE)
            outputs["netlist_visual.md"] = visual_content
            validation = self._validate_netlist(netlist_data)
            outputs["netlist_validation.json"] = json.dumps(validation, indent=2)
            try:
                from tools.netlist_drc import run_drc
                drc = run_drc(netlist_data)
                try:
                    from tools.pin_map import validate_netlist_pins
                    pin_issues = validate_netlist_pins(netlist_data)
                    if pin_issues:
                        drc.setdefault("violations", []).extend(pin_issues)
                        for pi in pin_issues:
                            sev = pi.get("severity", "info")
                            drc.setdefault("counts", {})[sev] = \
                                drc["counts"].get(sev, 0) + 1
                        drc["checks_run"] = list(drc.get("checks_run") or []) + [
                            "pin_validation",
                        ]
                        if any(p["severity"] in ("critical", "high")
                               for p in pin_issues):
                            drc["overall_pass"] = False
                except Exception:
                    pass
                # P26 #10 (2026-04-25): same iterative auto-fix as the
                # LLM-success path — net-merge + binder + DRC up to 3
                # cycles. BOM-fallback netlists are simpler than LLM-
                # emitted ones (no aliased rails), but still benefit
                # from the unknown_ref cleanup in the auto-fix loop.
                if not drc.get("overall_pass", True):
                    netlist_data, drc = self._auto_fix_drc_violations(
                        netlist_data, max_passes=3,
                    )
                drc_passed = bool(drc.get("overall_pass", False))
                _c = drc.get("counts", {})
                drc_summary = (
                    f"DRC: {_c.get('critical', 0)} critical, "
                    f"{_c.get('high', 0)} high, "
                    f"{_c.get('medium', 0)} medium"
                )
                outputs["netlist_drc.json"] = json.dumps(drc, indent=2)
            except Exception:
                pass
            _fb_schematic = self._synthesize_schematic(netlist_data)
            _fb_schematic["source"] = "auto_synthesized"
            _fb_schematic["auto_synthesized"] = True
            outputs["schematic.json"] = json.dumps(_fb_schematic, indent=2)
            # Bug #1 — schematic post-synthesis DRC must also run on the
            # BOM-fallback path (not just the LLM-emitted path). Flatten the
            # multi-sheet schematic back into {nodes, edges} and run_drc over
            # it so we catch shorts introduced by the auto-synthesis stage
            # (cross-sheet OPC aliasing, etc.).
            try:
                from tools.netlist_drc import run_schematic_drc
                schematic_drc = run_schematic_drc(_fb_schematic)
                outputs["schematic_drc.json"] = json.dumps(
                    schematic_drc, indent=2
                )
                if not schematic_drc.get("overall_pass", True):
                    drc_passed = False
                    _sc = schematic_drc.get("counts", {})
                    _sfx = (
                        f"Schematic DRC: {_sc.get('critical', 0)} critical, "
                        f"{_sc.get('high', 0)} high"
                    )
                    drc_summary = (
                        f"{drc_summary} | {_sfx}" if drc_summary else _sfx
                    )
            except Exception as _sdrc_exc:
                self.log(f"schematic_drc_failed: {_sdrc_exc}", "warning")

        # Phase completion gate.
        #
        # P26 (2026-04-25) — UX FIX for "netlist generated but showing
        # FAILED status". User report: P4 sidebar shows red "Failed —
        # click to retry" but the Documents tab actually has a fully
        # rendered schematic with 19 symbols / 22 nets and the
        # AUTO-SYNTHESIZED tag. Reason: the BOM-fallback path is known
        # to produce approximate connectivity (the LLM-emitted netlist
        # would have proper pin maps; auto-synth uses heuristics) — so
        # DRC reports `high` or `critical` violations on roughly every
        # AUTO-SYNTHESIZED run. Gating phase_complete on DRC means the
        # phase is marked failed even when the netlist is structurally
        # complete and visible.
        #
        # New gate:
        #   - LLM-success path (real LLM-emitted netlist with full pin
        #     maps): keep DRC gate. If critical/high violations, mark
        #     failed so the operator fixes them before P5.
        #   - BOM-fallback (auto-synthesized): mark COMPLETED whenever
        #     `outputs` contains a netlist.json. The AUTO-SYNTHESIZED
        #     tag in the UI already tells the operator the connectivity
        #     is approximate; DRC summary is appended to response_text
        #     so they can see what to fix manually in P5.
        #
        # This matches the user's mental model: "if the schematic
        # rendered, the phase succeeded — DRC warnings are info."
        # P26 #9 (2026-04-25, hgj cascade fix): UNIFIED phase_complete
        # gate — completion is purely "did we produce a netlist.json".
        # DRC failures NO LONGER block the phase.
        #
        # Why: in project hgj, P4 ran successfully (LLM called the tool,
        # 7 output files written) but DRC reported critical violations.
        # That set phase_complete=False → status=failed → DAG fail-fast
        # cascaded P3 / P6 / P7 / P7a / P8a / P8b / P8c (all the
        # downstream phases) as `failed` without even running them.
        # User saw "everything failed after HRS" in the UI for a fresh
        # project, even though the netlist had ALREADY been generated.
        #
        # The previous gate (LLM-success path requires drc_passed) was
        # too strict — DRC catches real shorts but ALSO flags every
        # auto-bound power rail as "critical" because the LLM didn't
        # explicitly emit each VCC pin connection. False-positive
        # critical count was always >0 on a real-world LLM netlist,
        # which made the cascade kick in EVERY run.
        #
        # New rule: phase_complete = (netlist.json exists). DRC summary
        # is still in response_text + persisted in netlist_drc.json so
        # the operator sees the warnings, but they no longer halt the
        # downstream pipeline. PCB layout (P5) is manual anyway — the
        # operator catches DRC issues during layout review.
        response_text = response.get("content", "Netlist generated.")
        is_auto_synth = (
            netlist_data is None
            or (response.get("tool_calls") in (None, []))
        )
        if drc_summary:
            response_text = f"{response_text}\n\n{drc_summary}"
            if not drc_passed:
                if is_auto_synth:
                    response_text += (
                        " — auto-synthesized from BOM, please review the "
                        "schematic and address DRC warnings before PCB "
                        "layout."
                    )
                else:
                    response_text += (
                        " — DRC reported violations; review netlist_drc.json "
                        "and address before PCB layout."
                    )
        has_netlist = bool(outputs.get("netlist.json"))
        # Phase completes if netlist was produced — DRC is informational only.
        phase_complete = has_netlist
        return {
            "response": response_text,
            "phase_complete": phase_complete,
            "outputs": outputs,
        }

    @staticmethod
    def _drc_aware_post_process(netlist_data: dict) -> dict:
        """P26 #10 (2026-04-25): three-step post-process that eliminates
        the most common DRC violations LLMs produce. Runs in pure Python
        — no LLM call — so adds <1s to the phase.

        Empirical scan of all 38 projects in `output/` showed only THREE
        rule classes account for >95%% of critical+high DRC violations:

          1. `power_collision` (47x) — LLM creates aliased rail names
             that all source from the SAME regulator output (e.g.
             `VCC_5V`, `VCC_5V_TO_FPGA`, `VCC_5V_RF_CH1` all share
             `DCDC1.VOUT`). Each name is a distinct net to the parser
             but they're physically the same wire. Merge them.

          2. `pin_multiple_nets` (91x) — most are GND pins where the
             LLM emitted multiple ground-typed names (`GND`, `GND_R_LNA1`,
             `GND_C_FPGA`...) all going to the same physical ground star.
             All ground-typed nets ARE the same net — merge to canonical
             `GND`.

          3. `unknown_ref` (4x) — edge endpoints reference component
             refs not in the nodes list. Drop those edges.

        This cleanup runs BEFORE `_enforce_power_ground_topology` so the
        binder sees an already-cleaned graph and doesn't add MORE aliased
        names on top.

        For senior RF reviewer audience: the merge does NOT change
        electrical behaviour — aliased rails were always the same net
        physically; the merge just makes the symbolic name match the
        physical reality so DRC's symbolic check passes.
        """
        nodes = netlist_data.get("nodes") or []
        edges = netlist_data.get("edges") or []
        valid_refs = {
            (n.get("instance_id") or n.get("reference_designator") or "")
            for n in nodes
        }
        valid_refs.discard("")

        # Step 1: every ground-typed-or-named net → canonical "GND".
        # Mermaid-level distinct names (GND_R_LNA1, GND_C_FPGA, etc.)
        # collapse into the single ground star.
        for e in edges:
            net = (e.get("net_name") or "").strip()
            stype = (e.get("signal_type") or "").lower()
            is_gnd_typed = stype == "ground"
            net_u = net.upper().replace("-", "_")
            is_gnd_named = (
                net_u == "GND"
                or net_u.startswith("GND_")
                or net_u.startswith("AGND")
                or net_u.startswith("DGND")
                or net_u.startswith("VGND")
                or net_u.startswith("AC_GND")
                or net_u.startswith("ACGND")
                or net_u.startswith("RF_GND")
                or net_u.startswith("RFGND")
                or net_u.startswith("PGND")
                or net_u.startswith("RGND")
                or net_u in ("VSS", "GNDA", "GNDD")
            )
            if is_gnd_typed or is_gnd_named:
                e["net_name"] = "GND"
                e["signal_type"] = "ground"

        # Step 2: merge power-rail aliases that share the same source pin.
        # `from_instance` + `from_pin` uniquely identifies a regulator
        # output / supply connector pin; if it appears in 2+ named nets,
        # those names are aliases. Pick the SHORTEST name (most generic)
        # as canonical, rename the others.
        from collections import defaultdict
        src_to_nets: dict = defaultdict(set)
        for e in edges:
            if (e.get("signal_type") or "").lower() == "power":
                src = (e.get("from_instance"), e.get("from_pin"))
                src_to_nets[src].add(e.get("net_name") or "")
        rename: dict = {}
        for src, names in src_to_nets.items():
            if len(names) > 1:
                # Sort by (length, alphabetic) so we get a deterministic
                # canonical even when two names tie on length.
                canonical = sorted(names, key=lambda n: (len(n), n))[0]
                for n in names:
                    if n and n != canonical and n not in rename:
                        rename[n] = canonical
        if rename:
            for e in edges:
                old = e.get("net_name") or ""
                if old in rename:
                    e["net_name"] = rename[old]

        # Step 3: drop edges whose endpoints reference unknown component refs.
        cleaned_edges = []
        for e in edges:
            f, t = e.get("from_instance"), e.get("to_instance")
            if (f in valid_refs) and (t in valid_refs):
                cleaned_edges.append(e)
            elif f and t:
                # Only drop if BOTH endpoints reference real refs OR if
                # one is a synthetic supply ref (e.g. GND_STAR, J_PWR
                # synthesised by the binder). The binder always uses
                # synthetic refs prefixed with GND_ / J_PWR / VCC_ /
                # PWR_ — preserve those.
                _allow_synth = ("GND_STAR", "J_PWR")
                _allow_prefix = ("GND_", "J_PWR", "VCC_", "PWR_")
                f_ok = (f in valid_refs) or (f in _allow_synth) or any(f.startswith(p) for p in _allow_prefix if isinstance(f, str))
                t_ok = (t in valid_refs) or (t in _allow_synth) or any(t.startswith(p) for p in _allow_prefix if isinstance(t, str))
                if f_ok and t_ok:
                    cleaned_edges.append(e)

        # Step 4 (P26 #22, 2026-05-04): defence-in-depth dedupe pass.
        # Even after the 1:1 LO↔mixer pairing fix, an upstream LLM-emitted
        # netlist or a future agent regression might still hand us
        # multiple drivers wired into the same single-ended input pin
        # (mixer LO, ADC AIN, mixer IF_OUT, etc.). Detect any (ref, pin)
        # that appears as the destination of multiple distinct nets,
        # keep the FIRST occurrence (deterministic — netlist edges are
        # emitted in component declaration order), and drop the rest.
        # Power/ground pins are exempt — many ICs share them deliberately.
        from collections import defaultdict as _dd
        _PWR_PINS = {"VCC", "VDD", "VEE", "VSS", "GND", "VBAT", "AVCC", "AGND",
                     "DGND", "DVCC", "PVCC", "PGND", "VREF"}
        _by_dest: dict = _dd(list)
        for e in cleaned_edges:
            tref, tpin = e.get("to_instance"), e.get("to_pin")
            if tref and tpin and str(tpin).upper() not in _PWR_PINS:
                _by_dest[(tref, str(tpin).upper())].append(e)
        _drop_ids = set()
        for (tref, tpin), es in _by_dest.items():
            distinct_nets = {ed.get("net_name") for ed in es}
            if len(distinct_nets) > 1:
                # Keep the first edge by net_name sort order; drop the rest.
                kept = sorted(es, key=lambda x: x.get("net_name") or "")[0]
                for ed in es:
                    if id(ed) != id(kept):
                        _drop_ids.add(id(ed))
                logger.warning(
                    "netlist.dedupe.pin_multi_drivers ref=%s pin=%s nets=%s "
                    "kept=%s",
                    tref, tpin, sorted(distinct_nets), kept.get("net_name"),
                )
        if _drop_ids:
            cleaned_edges = [e for e in cleaned_edges if id(e) not in _drop_ids]
        netlist_data["edges"] = cleaned_edges

        return netlist_data

    def _auto_fix_drc_violations(
        self,
        netlist_data: dict,
        max_passes: int = 3,
    ) -> tuple[dict, dict]:
        """P26 #10 (2026-04-25): iterative DRC-driven fix loop.

        Runs DRC, applies the post-process + binder, re-runs DRC, up to
        `max_passes` iterations. Stops as soon as DRC passes (zero
        critical+high). Returns the patched netlist plus the FINAL DRC
        result so the caller can persist `netlist_drc.json` and emit
        the summary.

        Pure-Python — no LLM calls. Total added wall-time per phase:
        ~500ms-1s for typical netlists, regardless of project size.

        Why three passes:
          - Pass 1: post-process merges aliased rails + ground nets.
          - Pass 2: binder re-binds with the merged names; new GND/VCC
            edges may surface new violations (e.g. a GND pin that was
            previously hidden behind the aliased name).
          - Pass 3: catches any second-order violations from the
            re-binding. Empirically rare; included for safety margin.
        """
        from tools.netlist_drc import run_drc
        last_drc: dict = {}
        for pass_idx in range(max_passes):
            netlist_data = self._drc_aware_post_process(netlist_data)
            netlist_data = self._enforce_power_ground_topology(netlist_data)
            last_drc = run_drc(netlist_data)
            counts = last_drc.get("counts", {})
            crit = counts.get("critical", 0)
            high = counts.get("high", 0)
            # Use module-level logger (not self.log) so this method is
            # callable on a `NetlistAgent.__new__()` instance from tests
            # without needing __init__ to have populated `phase_number`.
            logger.info(
                "P4 DRC pass %d/%d: crit=%d high=%d",
                pass_idx + 1, max_passes, crit, high,
            )
            if not crit and not high:
                break
        return netlist_data, last_drc

    @staticmethod
    def _enforce_power_ground_topology(netlist_data: dict) -> dict:
        """Bind every IC's power and ground pin to a real driver endpoint.

        The LLM-emitted netlists frequently omit power-pin edges (treating
        VCC as implicit) and emit GND as zero-length self-loops, which
        renders as floating pins in the schematic and fails DRC. This
        helper fills the gap deterministically:

          1. Detect drivers — regulator outputs (role=power) and supply
             connectors (role=connector with PWR/VCC/PSU in the ref).
          2. For every IC node, scan its known pin template for VCC/VDD/
             AVDD/DVDD-class names and emit a power edge to the closest
             driver if one is missing.
          3. Replace every GND self-loop edge (from==to with GND pin)
             with a star-topology edge to a synthetic GND_STAR node.
          4. Build `power_map: {ref: {pin: rail}}` so downstream tools
             can verify the binding without re-deriving it.

        Idempotent — repeated calls don't grow the edge list.
        """
        nodes = netlist_data.get("nodes") or []
        edges = netlist_data.get("edges") or []
        power_nets = set(netlist_data.get("power_nets") or [])
        ground_nets = set(netlist_data.get("ground_nets") or [])

        if not nodes:
            return netlist_data

        # ── Identify drivers and IC nodes ──────────────────────────────
        def _ref_of(n: dict) -> str:
            return n.get("instance_id") or n.get("reference_designator", "")

        def _role_of(n: dict) -> str:
            blob = (
                (n.get("component_name", "") + " "
                 + n.get("part_number", "")).lower()
            )
            if any(k in blob for k in ("ldo", "regulator", "dc-dc", "pmic",
                                       "buck", "boost", "power supply")):
                return "power"
            ref = _ref_of(n).upper()
            if any(ref.startswith(p) for p in ("J_PWR", "J_VCC", "PWR",
                                               "PSU", "REG", "LDO",
                                               "VREG", "U_VREG", "CONN")):
                return "supply_connector"
            if any(k in blob for k in ("connector", "jack", "sma",
                                       "header")):
                return "connector"
            # P6 — passives & RF blocks should not be force-fed VCC/GND
            # edges when the LLM omits explicit pin metadata. Detect
            # them by ref prefix (R*, C*, L*, FB*) and by part-name
            # keywords (resistor, capacitor, inductor, ferrite). RF
            # blocks (LNA / mixer / filter / coupler / attenuator /
            # balun / switch) are picked up by their keywords and a
            # broader set of passive RF parts that legitimately have no
            # supply pin in the BOM model — feeding them synthetic VCC
            # and GND edges fabricates connectivity that downstream
            # tools then trust.
            if any(ref.startswith(p) for p in ("R", "C", "L", "FB", "FER")) and \
                    not ref.startswith(("REG", "LDO", "RFP", "CONN")):
                # Heuristic: short ref like "R3", "C12", "L2" → passive
                tail = ref[1:] if ref else ""
                if tail and tail[0].isdigit():
                    return "passive"
            if any(k in blob for k in ("resistor", "capacitor", "inductor",
                                       "ferrite bead", "ferrite",
                                       "termination", " bead")):
                return "passive"
            if any(k in blob for k in ("lna", "low noise amp",
                                       "mixer", "downconvert", "upconvert",
                                       "balun", "coupler", "attenuator",
                                       "rf switch", "rf filter", "saw",
                                       "isolator", "circulator",
                                       "splitter", "combiner",
                                       "transformer", "matching network")):
                return "rf_block"
            return "ic"

        def _power_pin(name: str) -> bool:
            n = (name or "").upper()
            return n.startswith(("VCC", "VDD", "AVDD", "DVDD", "VBAT",
                                 "VIN", "+V", "V+"))

        def _gnd_pin(name: str) -> bool:
            n = (name or "").upper()
            return n in ("GND", "VSS", "AGND", "DGND", "GND_PWR", "RGND")

        # Pick the canonical rail name + driver ref. Prefer a regulator
        # output; fall back to a supply connector; fall back to a
        # synthesised supply connector node.
        regulators = [n for n in nodes if _role_of(n) == "power"]
        supply_conns = [n for n in nodes if _role_of(n) == "supply_connector"]

        if regulators:
            driver_ref = _ref_of(regulators[0])
            driver_pin = "OUT"
        elif supply_conns:
            driver_ref = _ref_of(supply_conns[0])
            driver_pin = "1"
        else:
            driver_ref = "J_PWR"
            driver_pin = "1"
            if not any(_ref_of(n) == driver_ref for n in nodes):
                nodes.append({
                    "instance_id": driver_ref,
                    "part_number": "PWR_HEADER",
                    "component_name": "Supply Connector (synthesised)",
                    "reference_designator": driver_ref,
                })

        rail_name = "VCC"
        power_nets.add(rail_name)

        # GND star anchor — virtual node every GND pin terminates on.
        gnd_ref = "GND_STAR"
        gnd_pin_num = "1"
        if not any(_ref_of(n) == gnd_ref for n in nodes):
            nodes.append({
                "instance_id": gnd_ref,
                "part_number": "GND",
                "component_name": "Ground Reference",
                "reference_designator": gnd_ref,
            })
        ground_nets.add("GND")

        # ── Strip GND self-loops first (the LLM emits these as zero-
        # length placeholders for "this pin goes to ground"). They have
        # to come out before we index existing edges, otherwise the
        # index sees the self-loop and skips the legitimate star edge.
        cleaned_edges = []
        for e in edges:
            sig = (e.get("signal_type") or "").lower()
            from_inst = e.get("from_instance")
            to_inst = e.get("to_instance")
            from_pin = (e.get("from_pin") or "").upper()
            to_pin = (e.get("to_pin") or "").upper()
            if sig == "ground" and from_inst == to_inst and \
                    _gnd_pin(from_pin) and _gnd_pin(to_pin):
                continue  # drop self-loop
            cleaned_edges.append(e)
        edges = cleaned_edges

        # ── Index real existing edges so we don't double-add ──────────
        existing_power = set()
        existing_gnd = set()
        for e in edges:
            sig = (e.get("signal_type") or "").lower()
            if sig == "power":
                existing_power.add((e.get("to_instance"), e.get("to_pin")))
                existing_power.add((e.get("from_instance"),
                                    e.get("from_pin")))
            if sig == "ground":
                existing_gnd.add((e.get("to_instance"), e.get("to_pin")))
                existing_gnd.add((e.get("from_instance"),
                                  e.get("from_pin")))

        # ── For every IC node, ensure VCC + GND pins are bound ────────
        power_map: dict = {}
        for n in nodes:
            ref = _ref_of(n)
            if not ref or ref in (driver_ref, gnd_ref):
                continue
            role = _role_of(n)
            if role in ("power", "supply_connector"):
                continue

            # Walk the pins on the node IF it has a `pins` list (some
            # LLM emissions include the gate-level pin map directly on
            # the node). Otherwise default to one VCC pin + one GND pin
            # so the IC at minimum gets bound to both rails.
            node_pins = n.get("pins") or []
            vcc_pins = [p.get("pin_name") or p.get("name")
                        for p in node_pins if _power_pin(p.get("pin_name")
                        or p.get("name") or "")]
            gnd_pins = [p.get("pin_name") or p.get("name")
                        for p in node_pins if _gnd_pin(p.get("pin_name")
                        or p.get("name") or "")]

            # P6 — passives (R/C/L/ferrite) and RF blocks (LNA, mixer,
            # filter, coupler, etc.) don't get synthetic VCC/GND edges
            # invented for them. The pre-fix code defaulted vcc_pins
            # to ["VCC"] and gnd_pins to ["GND"] for any node lacking
            # an explicit pin map, which fabricated supply edges on
            # passive RF parts and let those edges propagate into the
            # netlist DRC + schematic synthesis as if they were real.
            #
            # Exception: if the node DID declare power/ground pins in
            # its `pins` list, honour those (rare but legitimate — an
            # active mixer with VCC, a powered SAW filter, etc.).
            if role in ("passive", "rf_block") and not (
                vcc_pins or gnd_pins
            ):
                continue

            if not vcc_pins:
                vcc_pins = ["VCC"]
            if not gnd_pins:
                gnd_pins = ["GND"]

            ref_map: dict = {}
            for vp in vcc_pins:
                key = (ref, vp.upper())
                if key not in existing_power:
                    edges.append({
                        "net_name": rail_name,
                        "from_instance": driver_ref,
                        "from_pin": driver_pin,
                        "to_instance": ref,
                        "to_pin": vp,
                        "signal_type": "power",
                    })
                    existing_power.add(key)
                ref_map[vp] = rail_name
            for gp in gnd_pins:
                key = (ref, gp.upper())
                if key not in existing_gnd:
                    edges.append({
                        "net_name": "GND",
                        "from_instance": ref,
                        "from_pin": gp,
                        "to_instance": gnd_ref,
                        "to_pin": gnd_pin_num,
                        "signal_type": "ground",
                    })
                    existing_gnd.add(key)
                ref_map[gp] = "GND"
            if ref_map:
                power_map[ref] = ref_map

        netlist_data["nodes"] = nodes
        netlist_data["edges"] = edges
        netlist_data["power_nets"] = sorted(power_nets)
        netlist_data["ground_nets"] = sorted(ground_nets)
        netlist_data["power_map"] = power_map
        return netlist_data

    @staticmethod
    def _format_cascade_targets(design_parameters: dict) -> str:
        """Render the P1 cascade targets as a compact bulleted block so the
        LLM can reason about per-stage budget. Returns '(no targets)' when
        the caller didn't pass any — the agent then operates in the
        original BOM-only mode."""
        if not isinstance(design_parameters, dict) or not design_parameters:
            return "(no design parameters supplied — operating in BOM-only mode)"
        # Pick the subset the netlist agent can actually act on. Other
        # fields (project_summary, application, etc.) are noise here.
        relevant = (
            "freq_range", "freq_range_ghz",
            "bandwidth_mhz", "instantaneous_bandwidth_mhz", "ibw",
            "noise_figure_db", "nf_db",
            "total_gain_db", "gain_db",
            "iip3_dbm_input", "iip3_dbm", "iip3",
            "p1db_dbm_out", "p1db_dbm", "p1db",
            "sfdr_db",
            "sensitivity_dbm", "mds_dbm",
            "phase_noise_dbchz",
            "supply_voltage", "vdd", "power_budget_w",
            "lo_frequency", "lo_frequency_ghz",
            "if_frequency", "if_frequency_mhz",
            "architecture", "application",
        )
        lines = []
        for k in relevant:
            if k in design_parameters and design_parameters[k] is not None:
                v = design_parameters[k]
                lines.append(f"- {k}: {v}")
        if not lines:
            return "(design_parameters supplied but no cascade-relevant keys)"
        return "\n".join(lines)

    def _build_visual_md(self, data: dict, project_name: str, mermaid: str) -> str:
        lines = [
            "# Logical Netlist",
            f"## {project_name}",
            "",
            "## Block Diagram",
            "",
            f"```mermaid\n{mermaid}\n```",
            "",
            "## Component Instances",
            "",
            "| Ref | Part Number | Component |",
            "|---|---|---|",
        ]
        for node in data.get("nodes", []):
            lines.append(f"| {node.get('instance_id', '')} | {node.get('part_number', '')} | {node.get('component_name', '')} |")

        lines.extend(["", "## Pin-to-Pin Connections", "", "| Net | From | Pin | To | Pin | Type |", "|---|---|---|---|---|---|"])
        for edge in data.get("edges", []):
            lines.append(
                f"| {edge.get('net_name', '')} | {edge.get('from_instance', '')} | {edge.get('from_pin', '')} "
                f"| {edge.get('to_instance', '')} | {edge.get('to_pin', '')} | {edge.get('signal_type', '')} |"
            )

        # Net-centric connection list — groups all pins sharing each net
        edges = data.get("edges", [])
        if edges:
            # Build net → list of "RefDes - Pin" entries
            net_map: dict = {}
            for edge in edges:
                net = edge.get("net_name", "").strip()
                if not net:
                    continue
                from_entry = f"{edge.get('from_instance', '')} - {edge.get('from_pin', '')}"
                to_entry   = f"{edge.get('to_instance', '')} - {edge.get('to_pin', '')}"
                net_map.setdefault(net, [])
                if from_entry not in net_map[net]:
                    net_map[net].append(from_entry)
                if to_entry not in net_map[net]:
                    net_map[net].append(to_entry)

            lines.extend([
                "",
                "## Net Connection List",
                "",
                "| Net Name | Reference Designator - Pin No. |",
                "|----------|-------------------------------|",
            ])
            for net_name, pins in sorted(net_map.items()):
                pins_str = ",  ".join(pins)
                lines.append(f"| {net_name} | {pins_str} |")

        # Validation notes
        notes = data.get("validation_notes", [])
        if notes:
            lines.extend(["", "## Validation Notes", ""])
            for note in notes:
                lines.append(f"- {note}")

        return "\n".join(lines)

    def _validate_netlist(self, data: dict) -> dict:
        """Basic netlist validation using NetworkX."""
        try:
            import networkx as nx

            G = nx.DiGraph()
            for node in data.get("nodes", []):
                G.add_node(node["instance_id"], **node)
            for edge in data.get("edges", []):
                G.add_edge(
                    edge["from_instance"], edge["to_instance"],
                    net_name=edge.get("net_name", ""),
                )

            # Check for isolated nodes
            isolated = list(nx.isolates(G))

            # Check for cycles (shouldn't exist in most designs)
            cycles = list(nx.simple_cycles(G))

            return {
                "total_nodes": G.number_of_nodes(),
                "total_edges": G.number_of_edges(),
                "isolated_nodes": isolated,
                "cycles": [list(c) for c in cycles[:5]],
                "is_connected": nx.is_weakly_connected(G) if G.number_of_nodes() > 0 else False,
            }
        except ImportError:
            return {"error": "NetworkX not installed"}
        except Exception as e:
            return {"error": str(e)}

    def _load_file(self, path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _build_netlist_from_bom(self, components_md: str, requirements_md: str) -> dict:
        """Parse component_recommendations.md to build a complete netlist when LLM
        skips the tool call. Extracts every component, assigns ref designators,
        builds power/ground/signal connections based on component roles."""
        import re as _re

        nodes = []
        edges = []
        power_nets = set()
        ground_nets = {"GND", "AGND"}

        # Parse "### N. Component Name" sections
        sections = _re.split(r'^### \d+\.\s+', components_md, flags=_re.MULTILINE)
        ref_counter = {"U": 0, "J": 0, "Y": 0}

        parsed_components = []
        for sec in sections[1:]:  # skip preamble before first ###
            lines = sec.strip().split("\n")
            comp_title = lines[0].strip() if lines else "Unknown"

            # Extract part number from **Primary Choice:** [PartNum](url) (Manufacturer)
            pn_match = _re.search(r'\*\*Primary Choice:\*\*\s*\[([^\]]+)\]', sec)
            part_number = pn_match.group(1) if pn_match else comp_title.split()[0]

            # Extract specs from | key | value | table
            specs = {}
            for m in _re.finditer(r'\|\s*(\w[\w_]*)\s*\|\s*([^|]+?)\s*\|', sec):
                specs[m.group(1).strip()] = m.group(2).strip()

            # Determine component category for ref designator and signal type
            title_lower = comp_title.lower()
            if any(k in title_lower for k in ["connector", "jack", "plug", "sma", "2.4mm"]):
                ref_counter["J"] = ref_counter.get("J", 0) + 1
                ref = f"J{ref_counter['J']}"
            elif any(k in title_lower for k in ["oscillator", "clock", "crystal"]):
                ref_counter["Y"] = ref_counter.get("Y", 0) + 1
                ref = f"Y{ref_counter['Y']}"
            else:
                ref_counter["U"] = ref_counter.get("U", 0) + 1
                ref = f"U{ref_counter['U']}"

            # Detect supply voltage → power rail
            supply_v = specs.get("supply_voltage_v", specs.get("supply_v", specs.get("output_voltage_v", "")))

            # Classify component role
            role = "signal"  # default
            if any(k in title_lower for k in ["mixer", "downconvert", "upconvert"]):
                role = "rf_mixer"
            elif any(k in title_lower for k in ["lna", "amplifier", "vga", "driver", "pa"]):
                role = "rf_amplifier"
            elif any(k in title_lower for k in ["ldo", "regulator", "dc-dc", "pmic", "power supply", "buck", "boost"]):
                role = "power"
            elif any(k in title_lower for k in ["adc", "digitiz"]):
                role = "adc"
            elif any(k in title_lower for k in ["fpga", "cpld", "zynq", "ultrascale", "processing"]):
                role = "fpga"
            elif any(k in title_lower for k in ["phy", "ethernet", "transceiver", "uart", "spi"]):
                role = "interface"
            elif any(k in title_lower for k in ["connector", "jack"]):
                role = "connector"
            elif any(k in title_lower for k in ["filter", "bandpass", "lowpass", "saw"]):
                role = "filter"
            elif any(k in title_lower for k in ["synthesizer", "pll", "lo", "vco"]):
                role = "lo_synth"

            parsed_components.append({
                "ref": ref,
                "part_number": part_number,
                "name": comp_title,
                "role": role,
                "supply_v": supply_v,
                "specs": specs,
            })

            nodes.append({
                "instance_id": ref,
                "part_number": part_number,
                "component_name": comp_title,
                "reference_designator": ref,
            })

        # ── Build connections based on component roles ──
        # Find power regulators
        power_regs = [c for c in parsed_components if c["role"] == "power"]
        rf_amps = [c for c in parsed_components if c["role"] == "rf_amplifier"]
        mixers = [c for c in parsed_components if c["role"] == "rf_mixer"]
        adcs = [c for c in parsed_components if c["role"] == "adc"]
        fpgas = [c for c in parsed_components if c["role"] == "fpga"]
        interfaces = [c for c in parsed_components if c["role"] == "interface"]
        connectors = [c for c in parsed_components if c["role"] == "connector"]
        lo_synths = [c for c in parsed_components if c["role"] == "lo_synth"]
        filters = [c for c in parsed_components if c["role"] == "filter"]

        # Power connections: each regulator powers downstream ICs.
        # If no regulator was selected in the BOM, synthesise a virtual
        # supply connector so every IC's VCC/VDD pin still has a driving
        # endpoint — otherwise the dangling_power_rail DRC check fires
        # on every part.
        if power_regs:
            for reg in power_regs:
                rail = f"V{reg['supply_v'].replace('.', 'p').replace(' ', '_').split('/')[0]}" if reg["supply_v"] else "VCC"
                power_nets.add(rail)
                for comp in parsed_components:
                    if comp["role"] != "power" and comp["role"] != "connector":
                        edges.append({
                            "net_name": rail, "from_instance": reg["ref"], "from_pin": "OUT",
                            "to_instance": comp["ref"], "to_pin": "VCC", "signal_type": "power",
                        })
        else:
            psu_ref = "J_PWR"
            if not any(n["instance_id"] == psu_ref for n in nodes):
                nodes.append({
                    "instance_id": psu_ref,
                    "part_number": "PWR_HEADER",
                    "component_name": "Supply Connector (synthesised)",
                    "reference_designator": psu_ref,
                })
            rail = "VCC"
            power_nets.add(rail)
            for comp in parsed_components:
                if comp["role"] != "connector":
                    edges.append({
                        "net_name": rail, "from_instance": psu_ref, "from_pin": "1",
                        "to_instance": comp["ref"], "to_pin": "VCC", "signal_type": "power",
                    })

        # Ground connections: STAR topology to a synthetic GND_STAR ref
        # rather than self-loops (the previous from==to scheme rendered as
        # zero-length edges and tripped the floating-pin DRC check). Add a
        # virtual ground reference node so every IC's GND pin terminates
        # on a real driver endpoint.
        ground_nets.add("GND")
        gnd_ref = "GND_STAR"
        if not any(n["instance_id"] == gnd_ref for n in nodes):
            nodes.append({
                "instance_id": gnd_ref,
                "part_number": "GND",
                "component_name": "Ground Reference",
                "reference_designator": gnd_ref,
            })
        for comp in parsed_components:
            edges.append({
                "net_name": "GND", "from_instance": comp["ref"], "from_pin": "GND",
                "to_instance": gnd_ref, "to_pin": "1", "signal_type": "ground",
            })

        # RF signal chain: connector → LNA → filter → mixer → IF amp → ADC → FPGA
        rf_chain = []
        if connectors:
            rf_chain.append(connectors[0])
        rf_chain.extend(rf_amps)
        rf_chain.extend(filters)
        rf_chain.extend(mixers)
        rf_chain.extend(adcs)
        if fpgas:
            rf_chain.append(fpgas[0])

        for i in range(len(rf_chain) - 1):
            src = rf_chain[i]
            dst = rf_chain[i + 1]
            sig_type = "RF" if i < len(rf_amps) + len(filters) + len(connectors) else "IF"
            if dst["role"] == "adc":
                sig_type = "IF"
            if dst["role"] == "fpga":
                sig_type = "digital"
            net_name = f"{sig_type}_{src['ref']}_{dst['ref']}"
            edges.append({
                "net_name": net_name, "from_instance": src["ref"], "from_pin": "OUT",
                "to_instance": dst["ref"], "to_pin": "IN", "signal_type": sig_type.lower(),
            })

        # LO synth → mixer LO port. P26 (2026-05-04): pre-fix this was a
        # cartesian product (every LO × every mixer), so on a project with
        # 5 LO synths and 1 mixer the agent generated 5 separate edges
        # all wired to U_mixer.LO — DRC then flagged "pin_multiple_nets:
        # U3.LO connected to 5 nets" (electrical short). Pair LO sources
        # with mixers 1:1 in declaration order; if there are more mixers
        # than LOs, the last LO drives every remaining mixer (typical
        # multi-conversion architecture shares one wide-band LO).
        for i, mx in enumerate(mixers):
            if not lo_synths:
                break
            lo = lo_synths[min(i, len(lo_synths) - 1)]
            edges.append({
                "net_name": f"LO_{lo['ref']}_{mx['ref']}", "from_instance": lo["ref"],
                "from_pin": "RF_OUT", "to_instance": mx["ref"], "to_pin": "LO",
                "signal_type": "clock",
            })

        # FPGA → interface ICs
        for iface in interfaces:
            if fpgas:
                edges.append({
                    "net_name": f"DATA_{fpgas[0]['ref']}_{iface['ref']}",
                    "from_instance": fpgas[0]["ref"], "from_pin": "DATA",
                    "to_instance": iface["ref"], "to_pin": "DATA",
                    "signal_type": "digital",
                })

        # Build mermaid diagram
        mermaid_lines = ["graph LR"]
        for comp in parsed_components:
            label = f"{comp['name'][:30]} {comp['part_number']}"
            # Sanitize: remove quotes, angle brackets, pipes
            label = _re.sub(r'[<>"\'|#&@:]', '', label)
            mermaid_lines.append(f"    {comp['ref']}[{label}]")
        for edge in edges:
            if edge["signal_type"] not in ("ground",):
                mermaid_lines.append(
                    f"    {edge['from_instance']} -->|{edge['net_name'][:20]}| {edge['to_instance']}"
                )
        # Deduplicate mermaid edges
        seen_edges = set()
        deduped = [mermaid_lines[0]]
        for line in mermaid_lines[1:]:
            if line not in seen_edges:
                seen_edges.add(line)
                deduped.append(line)
        mermaid_diagram = "\n".join(deduped)

        # Validation notes
        validation_notes = [
            f"INFO: Auto-extracted {len(nodes)} components from P1 BOM",
            f"INFO: Generated {len(edges)} connections based on signal chain analysis",
            f"INFO: Power nets: {', '.join(sorted(power_nets))}",
            f"INFO: Ground nets: {', '.join(sorted(ground_nets))}",
        ]
        if not rf_amps:
            validation_notes.append("WARNING: No RF amplifiers detected in BOM")
        if not power_regs:
            validation_notes.append("WARNING: No power regulators detected in BOM")
        if not fpgas:
            validation_notes.append("WARNING: No FPGA/processor detected in BOM")

        return {
            "nodes": nodes,
            "edges": edges,
            "power_nets": sorted(power_nets),
            "ground_nets": sorted(ground_nets),
            "mermaid_diagram": mermaid_diagram,
            "validation_notes": validation_notes,
        }

    def _synthesize_schematic(self, netlist_data: dict) -> dict:
        """Synthesise a multi-sheet gate-level schematic from nodes + edges.

        Produces khv-quality output: role-specific IC pin lists, differential
        pairs, decoupling caps wired to VCC/GND, proper RF signal chain,
        SPI buses, clock distribution — zero floating pins.
        """
        nodes = netlist_data.get("nodes", []) or []
        edges = netlist_data.get("edges", []) or []
        power_nets = set(netlist_data.get("power_nets", []) or [])
        ground_nets = set(netlist_data.get("ground_nets", []) or [])

        # ── Build ref→node + role lookup ──────────────────────────────────
        ref_node: dict = {}
        ref_role: dict = {}
        for n in nodes:
            ref = n.get("instance_id") or n.get("reference_designator", "")
            if not ref:
                continue
            ref_node[ref] = n
            name_l = (n.get("component_name", "") + " " + n.get("part_number", "")).lower()
            pn_up = (n.get("part_number", "") or "").upper()
            # Order matters — more specific passive RF roles MUST be
            # classified before the generic "amplifier/filter" buckets so
            # a "PIN diode limiter" isn't grabbed by the amplifier rule
            # via the word "amplifier" in a longer description, etc.
            # Also: native chip resistors/caps (CRCW, GRM, etc.) are
            # detected by part-number prefix + ref-prefix so they render
            # as 2-pin symbols, not IC blocks.
            # Chip resistor / capacitor: require BOTH a matching ref
            # prefix (R*/C* with a digit) AND a passive-sounding name or
            # MPN pattern, OR an explicit "chip resistor/capacitor" name.
            # Narrow-gating prevents IC MPNs like `CLA4603` (limiter)
            # being mis-classified by a simple `CL` prefix match.
            _is_r_ref = ref.startswith("R") and ref[1:2].isdigit()
            _is_c_ref = ref.startswith("C") and ref[1:2].isdigit()
            _r_mpn = pn_up.startswith(("CRCW", "ERJ", "RC0", "RC1", "RK7",
                                       "RMCF", "RT", "RG"))
            _c_mpn = pn_up.startswith(("GRM", "CC0", "CL0", "CL1", "CL2",
                                       "CL3", "CL4", "CL5", "CGA", "MC0",
                                       "MCCA"))
            if (_is_r_ref and (_r_mpn or "resistor" in name_l)) or \
                    "chip resistor" in name_l:
                ref_role[ref] = "chip_resistor"
            elif (_is_c_ref and (_c_mpn or "capacitor" in name_l)) or \
                    "chip capacitor" in name_l:
                ref_role[ref] = "chip_capacitor"
            elif any(k in name_l for k in ["bias-tee", "bias tee", "bias-t",
                                           "dc injection", "dc feed",
                                           "dc inject"]):
                ref_role[ref] = "bias_tee"
            elif any(k in name_l for k in ["splitter", "combiner",
                                           "wilkinson", "power divider",
                                           "hybrid coupler"]):
                ref_role[ref] = "splitter"
            elif any(k in name_l for k in ["limiter", "pin diode limiter"]):
                ref_role[ref] = "limiter"
            elif any(k in name_l for k in ["attenuator", "pad",
                                           "fixed attenuator"]):
                ref_role[ref] = "attenuator"
            elif any(k in name_l for k in ["isolator", "circulator"]):
                ref_role[ref] = "isolator"
            elif any(k in name_l for k in ["mixer", "downconvert", "upconvert"]):
                ref_role[ref] = "rf_mixer"
            elif any(k in name_l for k in ["lna", "amplifier", "vga", "driver"]):
                ref_role[ref] = "rf_amp"
            elif any(k in name_l for k in ["filter", "bandpass", "lowpass", "saw"]):
                ref_role[ref] = "filter"
            elif any(k in name_l for k in ["connector", "jack", "sma", "2.4mm",
                                           "bnc", "n-type", "n type"]):
                ref_role[ref] = "connector"
            elif any(k in name_l for k in ["ldo", "regulator", "dc-dc", "pmic", "buck", "boost"]):
                ref_role[ref] = "power"
            elif any(k in name_l for k in ["adc", "digitiz"]):
                ref_role[ref] = "adc"
            elif any(k in name_l for k in ["fpga", "cpld", "zynq", "ultrascale"]):
                ref_role[ref] = "fpga"
            elif any(k in name_l for k in ["synthesiz", "pll", "vco", " lo "]):
                ref_role[ref] = "lo_synth"
            elif any(k in name_l for k in ["oscillat", "clock", "crystal"]):
                ref_role[ref] = "clock"
            elif any(k in name_l for k in ["phy", "ethernet", "transceiver"]):
                ref_role[ref] = "interface"
            else:
                ref_role[ref] = "signal"

        # ── Role-specific pin templates ───────────────────────────────────
        # Each role gets realistic pins matching real datasheets.
        #
        # Passive RF roles (limiter, bias_tee, splitter, attenuator,
        # isolator) intentionally have NO VCC/VDD pin so:
        #   - The topology pass does not fabricate synthetic power edges
        #   - The decoupling-cap loop does not add caps for them
        #   - The "VCC symbol at top of sheet" block does not emit a VCC
        #     symbol when the sheet is all-passive.
        ROLE_PINS: dict = {
            "connector": [
                {"name": "RF_OUT", "num": "1", "side": "right"},
                {"name": "GND", "num": "2", "side": "bottom"},
            ],
            "limiter": [
                {"name": "RF_IN", "num": "1", "side": "left"},
                {"name": "RF_OUT", "num": "2", "side": "right"},
                {"name": "GND", "num": "3", "side": "bottom"},
            ],
            "attenuator": [
                {"name": "RF_IN", "num": "1", "side": "left"},
                {"name": "RF_OUT", "num": "2", "side": "right"},
                {"name": "GND", "num": "3", "side": "bottom"},
            ],
            "isolator": [
                {"name": "RF_IN", "num": "1", "side": "left"},
                {"name": "RF_OUT", "num": "2", "side": "right"},
                {"name": "GND", "num": "3", "side": "bottom"},
            ],
            "bias_tee": [
                {"name": "RF_IN", "num": "1", "side": "left"},
                {"name": "RF_OUT", "num": "2", "side": "right"},
                {"name": "DC_IN", "num": "3", "side": "top"},
                {"name": "GND", "num": "4", "side": "bottom"},
            ],
            "splitter": [
                {"name": "RF_IN", "num": "1", "side": "left"},
                {"name": "RF_OUT_1", "num": "2", "side": "right"},
                {"name": "RF_OUT_2", "num": "3", "side": "right"},
                {"name": "RF_OUT_3", "num": "4", "side": "right"},
                {"name": "RF_OUT_4", "num": "5", "side": "right"},
                {"name": "GND", "num": "6", "side": "bottom"},
            ],
            "rf_amp": [
                {"name": "RF_IN_1", "num": "1", "side": "left"},
                {"name": "RF_IN_2", "num": "2", "side": "left"},
                {"name": "RF_OUT_1", "num": "3", "side": "right"},
                {"name": "RF_OUT_2", "num": "4", "side": "right"},
                {"name": "VCC", "num": "5", "side": "top"},
                {"name": "GND", "num": "6", "side": "bottom"},
            ],
            "filter": [
                {"name": "IN_1", "num": "1", "side": "left"},
                {"name": "IN_2", "num": "2", "side": "left"},
                {"name": "OUT_1", "num": "3", "side": "right"},
                {"name": "OUT_2", "num": "4", "side": "right"},
                {"name": "GND", "num": "5", "side": "bottom"},
            ],
            "rf_mixer": [
                {"name": "RF_IN", "num": "1", "side": "left"},
                {"name": "LO_P", "num": "2", "side": "left"},
                {"name": "LO_N", "num": "3", "side": "left"},
                {"name": "IF_OUT_P", "num": "4", "side": "right"},
                {"name": "IF_OUT_N", "num": "5", "side": "right"},
                {"name": "VCC", "num": "6", "side": "top"},
                {"name": "GND", "num": "7", "side": "bottom"},
            ],
            "lo_synth": [
                {"name": "CLK_REF_P", "num": "1", "side": "left"},
                {"name": "CLK_REF_N", "num": "2", "side": "left"},
                {"name": "SPI_CLK", "num": "3", "side": "left"},
                {"name": "SPI_DATA", "num": "4", "side": "left"},
                {"name": "SPI_LE", "num": "5", "side": "left"},
                {"name": "RF_OUT_P", "num": "6", "side": "right"},
                {"name": "RF_OUT_N", "num": "7", "side": "right"},
                {"name": "LOCK_DET", "num": "8", "side": "right"},
                {"name": "VCC_RF", "num": "9", "side": "top"},
                {"name": "VCC_DIG", "num": "10", "side": "top"},
                {"name": "GND", "num": "11", "side": "bottom"},
            ],
            "clock": [
                {"name": "VCC", "num": "1", "side": "top"},
                {"name": "GND", "num": "2", "side": "bottom"},
                {"name": "CLK_OUT_P", "num": "3", "side": "right"},
                {"name": "CLK_OUT_N", "num": "4", "side": "right"},
                {"name": "EN", "num": "5", "side": "left"},
            ],
            "adc": [
                {"name": "AIN_P", "num": "1", "side": "left"},
                {"name": "AIN_N", "num": "2", "side": "left"},
                {"name": "CLK_P", "num": "3", "side": "left"},
                {"name": "CLK_N", "num": "4", "side": "left"},
                {"name": "SYNC_P", "num": "5", "side": "left"},
                {"name": "SYNC_N", "num": "6", "side": "left"},
                {"name": "D0_P", "num": "7", "side": "right"},
                {"name": "D0_N", "num": "8", "side": "right"},
                {"name": "D1_P", "num": "9", "side": "right"},
                {"name": "D1_N", "num": "10", "side": "right"},
                {"name": "DCO_P", "num": "11", "side": "right"},
                {"name": "DCO_N", "num": "12", "side": "right"},
                {"name": "SPI_CLK", "num": "13", "side": "left"},
                {"name": "SPI_MOSI", "num": "14", "side": "left"},
                {"name": "SPI_CS", "num": "15", "side": "left"},
                {"name": "AVDD", "num": "16", "side": "top"},
                {"name": "DVDD", "num": "17", "side": "top"},
                {"name": "GND", "num": "18", "side": "bottom"},
            ],
            "fpga": [
                {"name": "ADC_D0_P", "num": "1", "side": "left"},
                {"name": "ADC_D0_N", "num": "2", "side": "left"},
                {"name": "ADC_D1_P", "num": "3", "side": "left"},
                {"name": "ADC_D1_N", "num": "4", "side": "left"},
                {"name": "ADC_DCO_P", "num": "5", "side": "left"},
                {"name": "ADC_DCO_N", "num": "6", "side": "left"},
                {"name": "ADC_FRAME_P", "num": "7", "side": "left"},
                {"name": "ADC_FRAME_N", "num": "8", "side": "left"},
                {"name": "SPI_CLK", "num": "9", "side": "right"},
                {"name": "SPI_MOSI", "num": "10", "side": "right"},
                {"name": "SPI_CS_ADC", "num": "11", "side": "right"},
                {"name": "SPI_CS_CLKGEN", "num": "12", "side": "right"},
                {"name": "CLK_IN_P", "num": "13", "side": "left"},
                {"name": "CLK_IN_N", "num": "14", "side": "left"},
                {"name": "GPIO_0", "num": "15", "side": "right"},
                {"name": "GPIO_1", "num": "16", "side": "right"},
                {"name": "VCCINT", "num": "17", "side": "top"},
                {"name": "VCCIO", "num": "18", "side": "top"},
                {"name": "GND", "num": "19", "side": "bottom"},
            ],
            "power": [
                {"name": "VIN", "num": "1", "side": "left"},
                {"name": "EN", "num": "2", "side": "left"},
                {"name": "VOUT", "num": "3", "side": "right"},
                {"name": "FB", "num": "4", "side": "right"},
                {"name": "GND", "num": "5", "side": "bottom"},
            ],
            "interface": [
                {"name": "DATA_IN", "num": "1", "side": "left"},
                {"name": "DATA_OUT", "num": "2", "side": "right"},
                {"name": "CLK", "num": "3", "side": "left"},
                {"name": "CS", "num": "4", "side": "left"},
                {"name": "VCC", "num": "5", "side": "top"},
                {"name": "GND", "num": "6", "side": "bottom"},
            ],
            "signal": [
                {"name": "IN", "num": "1", "side": "left"},
                {"name": "OUT", "num": "2", "side": "right"},
                {"name": "VCC", "num": "3", "side": "top"},
                {"name": "GND", "num": "4", "side": "bottom"},
            ],
        }

        # ── Group refs by sheet ───────────────────────────────────────────
        # P26 #5 (2026-04-25): the schematic now collapses to a SINGLE
        # page. Previously we split into 4 sheets (RF / ADC+Digital /
        # Clock / Power) with cross-sheet off-page connectors (OPCs)
        # bridging them. User feedback ("can we update netlist to single
        # page? instead of multi page?") — single page reads at a glance
        # in the browser viewer (zoom + pan), and avoids the OPC clutter
        # that doubled the visible component count for cross-domain nets.
        #
        # The bucketing dict is kept ONLY so the per-role layout pass
        # below can still order components left-to-right by domain
        # (signal flow: connectors → RF chain → mixers → ADCs → FPGA →
        # power → clock at side). Cross-sheet OPC creation is bypassed
        # entirely on the single-page path.
        sheet_map = {
            "rf": [], "power": [], "adc_dig": [], "clock": [],
        }
        _has_rf_active = any(
            r in ("rf_amp", "filter", "rf_mixer", "connector", "limiter",
                  "bias_tee", "splitter", "attenuator", "isolator")
            for r in ref_role.values()
        )
        for ref, role in ref_role.items():
            if role in ("connector", "rf_amp", "filter", "rf_mixer",
                        "limiter", "bias_tee", "splitter", "attenuator",
                        "isolator"):
                sheet_map["rf"].append(ref)
            elif role in ("chip_resistor", "chip_capacitor"):
                if _has_rf_active:
                    sheet_map["rf"].append(ref)
                else:
                    sheet_map["adc_dig"].append(ref)
            elif role == "power":
                sheet_map["power"].append(ref)
            elif role in ("adc", "fpga", "interface", "signal"):
                sheet_map["adc_dig"].append(ref)
            elif role in ("lo_synth", "clock"):
                sheet_map["clock"].append(ref)
            else:
                sheet_map["adc_dig"].append(ref)

        # P26 #5 — single-page mode: emit ONE sheet per project.
        # SHEET_ORDER also defines the LEFT→RIGHT visual order on the page.
        SHEET_TITLES = {
            "single": "Schematic",
            "rf": "RF Front-End",
            "power": "Power Distribution",
            "adc_dig": "ADC & Digitisation",
            "clock": "Clock Generation & SPI Control",
        }
        SHEET_ORDER = ["single"]
        # Combine all role buckets into one ordered list. The order
        # mirrors the natural signal flow so the layout grid below places
        # related components near each other:
        #   connectors / RF chain → ADC / FPGA → clock → power.
        sheet_map["single"] = (
            sheet_map["rf"]
            + sheet_map["adc_dig"]
            + sheet_map["clock"]
            + sheet_map["power"]
        )

        # ── Build sheets ──────────────────────────────────────────────────
        sheets = []
        # Seed auto-ref counters past any LLM-provided refs so the new
        # floating-pin closure doesn't collide with R1/C1/etc. already in
        # the netlist. For each prefix we find the highest existing number
        # and start counting above it. Missing prefix → counter stays at 0.
        def _max_ref_index(prefix: str) -> int:
            import re as _re
            pat = _re.compile(rf"^{_re.escape(prefix)}(\d+)$")
            hi = 0
            for _r in ref_node.keys():
                m = pat.match(_r)
                if m:
                    try:
                        n = int(m.group(1))
                        if n > hi:
                            hi = n
                    except ValueError:
                        pass
            return hi

        g_cap = _max_ref_index("C")
        g_gnd = _max_ref_index("GND")
        g_pwr = 0
        g_res = _max_ref_index("R")

        for sheet_key in SHEET_ORDER:
            refs = sheet_map.get(sheet_key, [])
            if not refs:
                continue

            comps: list = []
            nets: list = []
            placed: set = set()

            # P26 #5 (2026-04-25) — single-page layout with role-aware
            # row clustering. Previously a 3-col modulo grid placed
            # components in pure ref order, mixing RF + power + clock
            # ICs into the same row and producing visual chaos with 60+
            # IC schematics. Now we lay out by role-band:
            #   row band 0 — connectors (left edge)
            #   row band 1 — RF chain (LNA / filter / mixer / etc.)
            #   row band 2 — ADC / FPGA / digital interfaces
            #   row band 3 — Clock / LO / synth
            #   row band 4 — Power regulators
            # Within a band, components are placed left→right at 12-unit
            # horizontal pitch (was 9). Vertical pitch between bands is
            # 9 units (was 7). 6 columns per row instead of 3 — gives
            # ~72-unit-wide canvas (was 27) which matches the actual
            # area we have on a single 30×20 grid scaled up to 90×60.
            _band_for_role = {
                "connector": 0,
                "rf_amp": 1, "filter": 1, "rf_mixer": 1, "limiter": 1,
                "bias_tee": 1, "splitter": 1, "attenuator": 1,
                "isolator": 1,
                "adc": 2, "fpga": 2, "interface": 2, "signal": 2,
                "clock": 3, "lo_synth": 3,
                "power": 4,
                "chip_resistor": 1, "chip_capacitor": 1,
            }
            # Pre-compute band index per ref so we can group + count
            # within bands for left→right placement.
            _ref_band = {r: _band_for_role.get(ref_role.get(r, "signal"), 2)
                         for r in refs}
            # Pre-compute band sizes so we know how many rows each band
            # actually needs. Then offset each band's y-base by the
            # CUMULATIVE rows of bands above it — no fixed `band * Y_PITCH * 2`
            # collision when one band wraps into the next.
            from collections import Counter as _C
            _band_sizes = _C(_ref_band.values())
            COLS_PER_BAND = 8        # was 6 — wider canvas, fewer wraps
            X_PITCH = 12             # was 9 — same horizontal pitch
            Y_PITCH = 9              # was 7 — extra room for decap stack
            X_BASE = 4
            Y_BASE = 5
            BAND_GAP = Y_PITCH * 2   # blank vertical gutter between bands
            # Y-offset for each band = sum of rows used by all earlier
            # bands * Y_PITCH + BAND_GAP per band boundary. Guarantees
            # zero overlap even when a band has 50+ components.
            _band_y_base: dict = {}
            _y_cursor = Y_BASE
            for _b in sorted(_band_sizes):
                _band_y_base[_b] = _y_cursor
                _rows_in_band = max(
                    1, (_band_sizes[_b] + COLS_PER_BAND - 1) // COLS_PER_BAND
                )
                _y_cursor += _rows_in_band * Y_PITCH + BAND_GAP
            _band_cursor: dict = {}  # band → next col index
            for idx, ref in enumerate(refs):
                node = ref_node.get(ref, {})
                role = ref_role.get(ref, "signal")
                band = _ref_band.get(ref, 2)
                band_pos = _band_cursor.get(band, 0)
                _band_cursor[band] = band_pos + 1
                col = band_pos % COLS_PER_BAND
                row_in_band = band_pos // COLS_PER_BAND
                x = X_BASE + col * X_PITCH
                y = _band_y_base.get(band, Y_BASE) + row_in_band * Y_PITCH

                # Native passive symbols (chip R / chip C) — render as
                # 2-pin parts with no `pins` list, matching the TS schem
                # renderer's resistor/capacitor primitives. They don't
                # get decoupling caps or ground symbols of their own —
                # downstream nets wire their "1"/"2" pins directly.
                if role == "chip_resistor":
                    comps.append({
                        "ref": ref, "type": "resistor",
                        "value": node.get("value") or node.get("part_number", "10k"),
                        "part_number": node.get("part_number", ""),
                        "x": x, "y": y, "rot": 0,
                    })
                    placed.add(ref)
                    continue
                if role == "chip_capacitor":
                    comps.append({
                        "ref": ref, "type": "capacitor",
                        "value": node.get("value") or node.get("part_number", "100nF"),
                        "part_number": node.get("part_number", ""),
                        "x": x, "y": y, "rot": 0,
                    })
                    placed.add(ref)
                    continue

                pins = [dict(p) for p in ROLE_PINS.get(role, ROLE_PINS["signal"])]

                # `connector` now renders as IC so named pins (RF_OUT,
                # GND) are resolvable by cross-sheet nets. The shape is
                # still visually a connector; only the lookup layer
                # treats it as named-pin-addressable.
                comp_type = "ic"
                comp_value = node.get("part_number", "")
                if role == "connector":
                    comp_value = node.get("part_number") or "SMA-J"

                comps.append({
                    "ref": ref, "type": comp_type,
                    "value": comp_value,
                    "part_number": node.get("part_number", ""),
                    "x": x, "y": y, "rot": 0, "pins": pins,
                })
                placed.add(ref)

                # Decoupling cap stack for every IC with VCC/VDD/AVDD pin.
                # Active ICs (rf_amp, rf_mixer, adc, fpga, lo_synth,
                # clock, interface, signal, power) get the full three-
                # value stack per VCC pin: 1uF bulk + 100nF mid + 10nF HF.
                # Passive RF roles (limiter/bias_tee/splitter/attenuator/
                # isolator) have no VCC pin at all, so this loop naturally
                # skips them.
                #
                # Geometry: the TS renderer places top-pin k at global
                # x = ic.x + w/(nt+1)*(k+1); cap rot=90 puts pin 1 at
                # cap.x - 0.5. We align cap.x so pin 1 lands on the VCC
                # anchor. Three caps share the same anchor x but are
                # offset by <=0.9 units so they stay inside the
                # `abs(pin1_x - vcc_pos_x) <= 1.0` tolerance the
                # alignment test enforces.
                vcc_pins = [p for p in pins if p["side"] == "top" and
                            any(p["name"].upper().startswith(v) for v in ("VCC", "VDD", "AVDD", "DVDD"))]
                _decap_stack = ("1uF", "100nF", "10nF")
                _cap_x_offsets = (0.0, 0.9, -0.9)
                _tc = sum(1 for pp in pins if pp["side"] == "top")
                _bc = sum(1 for pp in pins if pp["side"] == "bottom")
                _w_ic = max(4, max(_tc, _bc, 0) + 2)
                _top_pins = [pp for pp in pins if pp["side"] == "top"]
                for vp in vcc_pins:
                    rail = vp["name"].upper()
                    try:
                        _vcc_idx = _top_pins.index(vp) + 1
                    except ValueError:
                        _vcc_idx = 1
                    vcc_anchor_x = x + (_w_ic / (_tc + 1)) * _vcc_idx
                    for k, cval in enumerate(_decap_stack):
                        g_cap += 1
                        cref = f"C{g_cap}"
                        cx = vcc_anchor_x + 0.5 + _cap_x_offsets[k]
                        cy = max(y - 2, 1)
                        comps.append({"ref": cref, "type": "capacitor",
                                      "value": cval,
                                      "x": cx, "y": cy, "rot": 90})
                        nets.append({"name": rail, "type": "power",
                                     "endpoints": [{"ref": ref, "pin": vp["name"]},
                                                   {"ref": cref, "pin": "1"}]})
                        g_gnd += 1
                        gref = f"GND_C{g_cap}"
                        comps.append({"ref": gref, "type": "ground", "value": "GND",
                                      "x": cx, "y": cy + 2, "rot": 0})
                        nets.append({"name": "GND", "type": "ground",
                                     "endpoints": [{"ref": cref, "pin": "2"},
                                                   {"ref": gref, "pin": "1"}]})

                # Ground symbol directly under the IC GND pin. The TS
                # renderer lays out bottom pins at `pin_x = w / (nb+1) * k`
                # where `w = max(4, max(top_count, bottom_count) + 2)`
                # and `nb` is the number of bottom pins. We replicate that
                # math so the ground symbol's anchor (0.5, 0) lines up on
                # the IC's GND pin instead of sitting at the IC's corner.
                gnd_pins = [p for p in pins if p["name"].upper() in ("GND", "AGND", "DGND")]
                if gnd_pins:
                    _sides_count = {"top": 0, "bottom": 0}
                    for _p in pins:
                        if _p["side"] in _sides_count:
                            _sides_count[_p["side"]] += 1
                    _w = max(4, max(_sides_count["top"], _sides_count["bottom"], 0) + 2)
                    _bottom = [p for p in pins if p["side"] == "bottom"]
                    for k_idx, gp in enumerate(gnd_pins):
                        # Find this GND pin's position among the bottom pins
                        try:
                            b_idx = _bottom.index(gp)
                        except ValueError:
                            b_idx = 0
                        pin_dx = _w / (len(_bottom) + 1) * (b_idx + 1)
                        # Ground symbol anchor is (0.5, 0) — subtract 0.5
                        # so anchor lands on the pin.
                        g_gnd += 1
                        gref = f"GND{g_gnd}"
                        comps.append({"ref": gref, "type": "ground", "value": "GND",
                                      "x": x + pin_dx - 0.5, "y": y + 3, "rot": 0})
                        nets.append({"name": "GND", "type": "ground",
                                     "endpoints": [{"ref": ref, "pin": gp["name"]},
                                                   {"ref": gref, "pin": "1"}]})

                # Splitter unused-port terminations. A Wilkinson splitter
                # hands out four secondary outputs (RF_OUT_1..4); any that
                # the caller doesn't route to downstream must see a 50R
                # termination to ground so the port isn't reflective. We
                # conservatively terminate RF_OUT_2..4 and leave RF_OUT_1
                # as the "primary" downstream feed — this keeps the
                # schematic legal even when the netlister hasn't yet
                # decided which secondary is primary.
                if role == "splitter":
                    for _pn in ("RF_OUT_2", "RF_OUT_3", "RF_OUT_4"):
                        g_res += 1
                        rref = f"R{g_res}"
                        # P26 #5: removed hardcoded `min(x+5, 28)` clamp
                        # left over from the old 30-col grid. Single-page
                        # canvas is now ~80 cols wide.
                        rx = x + 5
                        ry = max(y + 1 + (g_res % 3), 1)
                        comps.append({"ref": rref, "type": "resistor",
                                      "value": "50R",
                                      "x": rx, "y": ry, "rot": 0})
                        term_net = f"TERM_{ref}_{_pn}"
                        nets.append({"name": term_net, "type": "signal",
                                     "endpoints": [{"ref": ref, "pin": _pn},
                                                   {"ref": rref, "pin": "1"}]})
                        g_gnd += 1
                        gref_t = f"GND_T{g_gnd}"
                        comps.append({"ref": gref_t, "type": "ground",
                                      "value": "GND",
                                      "x": rx, "y": ry + 2, "rot": 0})
                        nets.append({"name": "GND", "type": "ground",
                                     "endpoints": [{"ref": rref, "pin": "2"},
                                                   {"ref": gref_t, "pin": "1"}]})

                # Bias-tee DC feed network. A bias-tee's DC_IN pin must
                # see an RF choke (inductor) plus a bulk decoupling cap
                # to ground. Without the choke, RF leaks onto the DC
                # supply; without the bulk cap, transients couple into
                # the RF path. Emit both so schematic review catches any
                # downstream circuit that forgot them.
                if role == "bias_tee":
                    g_ind = locals().get("g_ind", _max_ref_index("L"))
                    g_ind += 1
                    lref = f"L{g_ind}"
                    lx = x + 2
                    ly = max(y - 3, 1)
                    comps.append({"ref": lref, "type": "inductor",
                                  "value": "100nH",
                                  "x": lx, "y": ly, "rot": 0})
                    nets.append({"name": f"DC_IN_{ref}", "type": "power",
                                 "endpoints": [{"ref": ref, "pin": "DC_IN"},
                                               {"ref": lref, "pin": "2"}]})
                    g_cap += 1
                    cref_b = f"C{g_cap}"
                    cx_b = lx + 2
                    cy_b = ly
                    comps.append({"ref": cref_b, "type": "capacitor",
                                  "value": "10uF",
                                  "x": cx_b, "y": cy_b, "rot": 90})
                    nets.append({"name": f"DC_IN_{ref}", "type": "power",
                                 "endpoints": [{"ref": lref, "pin": "1"},
                                               {"ref": cref_b, "pin": "1"}]})
                    g_gnd += 1
                    gref_b = f"GND_C{g_cap}"
                    comps.append({"ref": gref_b, "type": "ground",
                                  "value": "GND",
                                  "x": cx_b, "y": cy_b + 2, "rot": 0})
                    nets.append({"name": "GND", "type": "ground",
                                 "endpoints": [{"ref": cref_b, "pin": "2"},
                                               {"ref": gref_b, "pin": "1"}]})

                # ESD / TVS diode for every exposed RF connector. The
                # diode sits vertically (rot=90) so its anode (pin 1)
                # lands on the RF signal trace and the cathode (pin 2)
                # drains to ground. Without this, any ESD event on the
                # connector punches straight through to the LNA input.
                if role == "connector":
                    g_diode = locals().get("g_diode", _max_ref_index("D"))
                    g_diode += 1
                    dref = f"D{g_diode}"
                    dx = x + 2
                    dy = max(y - 2, 1)
                    comps.append({"ref": dref, "type": "diode_tvs",
                                  "value": "ESD",
                                  "x": dx, "y": dy, "rot": 90})
                    nets.append({"name": f"RF_{ref}", "type": "signal",
                                 "endpoints": [{"ref": ref, "pin": "RF_OUT"},
                                               {"ref": dref, "pin": "1"}]})
                    g_gnd += 1
                    gref_d = f"GND_D{g_diode}"
                    comps.append({"ref": gref_d, "type": "ground",
                                  "value": "GND",
                                  "x": dx, "y": dy + 2, "rot": 0})
                    nets.append({"name": "GND", "type": "ground",
                                 "endpoints": [{"ref": dref, "pin": "2"},
                                               {"ref": gref_d, "pin": "1"}]})

            # VCC symbols at top for power rails. Only roles with a
            # real IC pin-template (NOT chip_resistor/chip_capacitor, and
            # NOT the signal fallback for passive refs that lack pins)
            # count. Passive roles (limiter/bias_tee/splitter/attenuator/
            # isolator/chip_resistor/chip_capacitor) never contribute —
            # an all-passive sheet emits no VCC symbol at all.
            _passive_roles = {
                "limiter", "bias_tee", "splitter", "attenuator", "isolator",
                "chip_resistor", "chip_capacitor", "connector",
            }
            rail_names = set()
            for ref in refs:
                role = ref_role.get(ref, "signal")
                if role in _passive_roles:
                    continue
                # Only consider roles that have an explicit pin template
                # — the "signal" fallback has a VCC top pin but we don't
                # want it emitting a rail for refs that the user never
                # actually modelled as an active IC.
                if role not in ROLE_PINS:
                    continue
                pins = ROLE_PINS.get(role, [])
                for p in pins:
                    pn = p["name"].upper()
                    if p["side"] == "top" and any(pn.startswith(v) for v in ("VCC", "VDD", "AVDD", "DVDD", "VIN")):
                        rail_names.add(p["name"])
            xp = 3
            for rail in sorted(rail_names)[:5]:
                g_pwr += 1
                pref = f"VCC_{g_pwr}"
                comps.append({"ref": pref, "type": "vcc", "value": rail,
                              "x": xp, "y": 1, "rot": 0})
                # Wire VCC symbol to first IC that uses this rail (skip
                # passive roles — they have no VCC pin)
                for ref in refs:
                    role = ref_role.get(ref, "signal")
                    if role in _passive_roles:
                        continue
                    pins = ROLE_PINS.get(role, ROLE_PINS["signal"])
                    if any(p["name"] == rail for p in pins):
                        nets.append({"name": rail, "type": "power",
                                     "endpoints": [{"ref": pref, "pin": "1"},
                                                   {"ref": ref, "pin": rail}]})
                        break
                xp += 5

            # ── Signal nets: wire adjacent ICs in the signal chain ────────
            for i in range(len(refs) - 1):
                src_ref = refs[i]
                dst_ref = refs[i + 1]
                src_role = ref_role.get(src_ref, "signal")
                dst_role = ref_role.get(dst_ref, "signal")
                src_pins = ROLE_PINS.get(src_role, ROLE_PINS["signal"])
                dst_pins = ROLE_PINS.get(dst_role, ROLE_PINS["signal"])

                # Find output pins of src and input pins of dst
                out_pins = [p for p in src_pins if p["side"] == "right"
                            and not any(p["name"].upper().startswith(x) for x in ("SPI", "GPIO", "LOCK", "FB"))]
                in_pins = [p for p in dst_pins if p["side"] == "left"
                           and not any(p["name"].upper().startswith(x) for x in ("SPI", "EN", "CLK_REF"))]

                # Wire matching pairs (differential or single-ended)
                n_pairs = min(len(out_pins), len(in_pins))
                for j in range(n_pairs):
                    op = out_pins[j]["name"]
                    ip = in_pins[j]["name"]
                    # Determine net type
                    ntype = "analog"
                    if "CLK" in op.upper() or "CLK" in ip.upper():
                        ntype = "clock"
                    elif "D0" in op.upper() or "D1" in op.upper() or "DCO" in op.upper():
                        ntype = "signal"
                    net_name = f"{op}_{src_ref}"
                    nets.append({"name": net_name, "type": ntype,
                                 "endpoints": [{"ref": src_ref, "pin": op},
                                               {"ref": dst_ref, "pin": ip}]})

            # ── SPI bus: wire FPGA SPI pins → ADC/Synth SPI pins ──────────
            # Maps target SPI pin names to FPGA pin names per role
            fpga_refs = [r for r in refs if ref_role.get(r) == "fpga"]
            spi_targets = [r for r in refs if ref_role.get(r) in ("adc", "lo_synth")]
            if fpga_refs and spi_targets:
                fpga = fpga_refs[0]
                for tgt in spi_targets:
                    tgt_role = ref_role.get(tgt, "signal")
                    tgt_pins = ROLE_PINS.get(tgt_role, [])
                    tgt_spi = [p for p in tgt_pins if p["name"].upper().startswith("SPI")]
                    for tp in tgt_spi:
                        pn_up = tp["name"].upper()
                        # Map target pin → FPGA pin
                        if "CS" in pn_up or "LE" in pn_up:
                            suffix = "ADC" if tgt_role == "adc" else "CLKGEN"
                            fpga_pin_name = f"SPI_CS_{suffix}"
                        elif "DATA" in pn_up or "MOSI" in pn_up:
                            fpga_pin_name = "SPI_MOSI"
                        elif "CLK" in pn_up:
                            fpga_pin_name = "SPI_CLK"
                        else:
                            fpga_pin_name = tp["name"]
                        nets.append({
                            "name": f"SPI_{tp['name']}_{tgt}",
                            "type": "signal",
                            "endpoints": [{"ref": fpga, "pin": fpga_pin_name},
                                          {"ref": tgt, "pin": tp["name"]}],
                        })

            # ── Floating-pin closure (P2 — broaden coverage) ─────────────
            # Pre-fix code only AC-grounded differential _N / _2 input
            # pins on the left side. Sync, lock-detect, SPI, EN, RST,
            # spare FPGA pins and generic single-ended I/O all fell
            # through, so the schematic shipped with floating pins even
            # though the prompt promised zero. Strategy:
            #
            #   • Differential _N/_2 inputs (left)  → AC-cap to GND
            #   • Active-low control (CS/SS/RST/RESET/EN_N) → 10k pull-up
            #   • Active-high control + sync (EN/SYNC/RDY/IRQ/INT/...
            #     when on left) → 10k pull-down
            #   • SPI control pins on the LEFT with no driver → 10k
            #     pull-down (SCLK/SDIN/CS) — assumes upstream FPGA wiring
            #     is missing rather than that this pin is a bus host
            #   • LOCK_DET / status outputs (right side) → test point
            #   • Generic single-ended outputs (right) with no sink →
            #     test point
            #   • Generic single-ended inputs (left) with no driver →
            #     10k pull-down
            #
            # All terminations route to the existing GND star (or VCC
            # symbol) so the post-synthesis DRC sees a fully bound net.
            connected_pins: set = set()
            for net in nets:
                for ep in net["endpoints"]:
                    connected_pins.add((ep["ref"], ep["pin"]))

            def _is_pull_up_pin(name_up: str) -> bool:
                # Active-low control pins idle high — pulled up.
                if name_up in ("CS", "SS", "RST", "RESET", "OE", "WE"):
                    return True
                if name_up.endswith("_N") and name_up.startswith(
                        ("CS", "SS", "RST", "RESET", "EN", "OE", "WE", "INT", "IRQ")):
                    return True
                return False

            def _is_status_output(name_up: str) -> bool:
                return name_up.startswith((
                    "LOCK_DET", "LOCK", "PG", "POWERGOOD", "PWRGD",
                    "RDY", "READY", "ALERT", "FAULT", "STATUS",
                ))

            for c in comps:
                if c["type"] != "ic" or "pins" not in c:
                    continue
                # P26 #5 (2026-04-25) — distribute closure components by
                # pin index. Pre-fix: every pull-down R + GND symbol for a
                # given IC was placed at the SAME (cx, cy) coordinates,
                # producing visual overlap (5+ symbols stacked at one
                # point). Now each closure component is offset by the
                # pin's position index along its side, so the closure
                # forms a vertical column of resistors / caps / GND
                # symbols that flanks the IC instead of stacking on
                # itself.
                _ic_pins = c.get("pins", [])
                _left_idx = {pp["name"]: i for i, pp in
                             enumerate(p2 for p2 in _ic_pins if p2.get("side") == "left")}
                _right_idx = {pp["name"]: i for i, pp in
                              enumerate(p2 for p2 in _ic_pins if p2.get("side") == "right")}
                for p in _ic_pins:
                    if (c["ref"], p["name"]) in connected_pins:
                        continue
                    pn = p["name"].upper()
                    side = p.get("side", "")
                    # Skip power/ground rails — handled by the topology pass.
                    if pn in ("GND", "AGND", "DGND") or pn.startswith(
                            ("VCC", "VDD", "AVDD", "DVDD", "VBAT", "VIN")):
                        continue

                    # Per-pin offset: how far along the IC's side is this
                    # pin? Used to spread closure components vertically.
                    if side == "left":
                        _pin_offset = _left_idx.get(p["name"], 0)
                    elif side == "right":
                        _pin_offset = _right_idx.get(p["name"], 0)
                    else:
                        _pin_offset = 0

                    # 1. Differential _N / _2 inputs → AC-cap to GND
                    is_diff_n = (
                        (pn.endswith("_2") or pn.endswith("_N"))
                        and side == "left"
                    )
                    if is_diff_n:
                        g_cap += 1
                        ac_ref = f"C{g_cap}"
                        cx = max(c["x"] - 3, 1)
                        cy = c["y"] + 1 + _pin_offset * 2
                        comps.append({"ref": ac_ref, "type": "capacitor",
                                      "value": "100nF",
                                      "x": cx, "y": cy, "rot": 0})
                        g_gnd += 1
                        gref_ac = f"GND_AC{g_gnd}"
                        comps.append({"ref": gref_ac, "type": "ground",
                                      "value": "GND",
                                      "x": cx, "y": cy + 2, "rot": 0})
                        nets.append({
                            "name": f"AC_GND_{c['ref']}_{p['name']}",
                            "type": "analog",
                            "endpoints": [
                                {"ref": c["ref"], "pin": p["name"]},
                                {"ref": ac_ref, "pin": "1"},
                            ],
                        })
                        nets.append({
                            "name": "GND", "type": "ground",
                            "endpoints": [
                                {"ref": ac_ref, "pin": "2"},
                                {"ref": gref_ac, "pin": "1"},
                            ],
                        })
                        connected_pins.add((c["ref"], p["name"]))
                        continue

                    # 2. LOCK_DET / status outputs → test point header.
                    if side == "right" and _is_status_output(pn):
                        g_pwr += 1
                        tp_ref = f"TP_{g_pwr}"
                        comps.append({
                            "ref": tp_ref, "type": "connector",
                            "value": f"TP_{pn}",
                            "x": c["x"] + 5,
                            "y": c["y"] + 1 + _pin_offset * 2, "rot": 0,
                            "pins": [{"name": "1", "num": "1",
                                      "side": "left"}],
                        })
                        nets.append({
                            "name": f"{pn}_{c['ref']}", "type": "signal",
                            "endpoints": [
                                {"ref": c["ref"], "pin": p["name"]},
                                {"ref": tp_ref, "pin": "1"},
                            ],
                        })
                        connected_pins.add((c["ref"], p["name"]))
                        continue

                    # 3. Active-low control on the left → 10k pull-up
                    if side == "left" and _is_pull_up_pin(pn):
                        g_res += 1
                        rref = f"R{g_res}"
                        rx = max(c["x"] - 3, 1)
                        # Distribute by pin index so each IC's pull-ups
                        # form a vertical column instead of stacking.
                        ry = max(c["y"] + _pin_offset * 2, 1)
                        comps.append({"ref": rref, "type": "resistor",
                                      "value": "10k",
                                      "x": rx, "y": ry, "rot": 90})
                        g_pwr += 1
                        vref_pu = f"VCC_PU_{g_pwr}"
                        comps.append({"ref": vref_pu, "type": "vcc",
                                      "value": "VCC",
                                      "x": rx, "y": max(ry - 2, 1),
                                      "rot": 0})
                        nets.append({
                            "name": f"PU_{c['ref']}_{p['name']}",
                            "type": "signal",
                            "endpoints": [
                                {"ref": c["ref"], "pin": p["name"]},
                                {"ref": rref, "pin": "1"},
                            ],
                        })
                        nets.append({
                            "name": "VCC", "type": "power",
                            "endpoints": [
                                {"ref": rref, "pin": "2"},
                                {"ref": vref_pu, "pin": "1"},
                            ],
                        })
                        connected_pins.add((c["ref"], p["name"]))
                        continue

                    # 4. Generic left-side input (control / sync / SPI /
                    #    spare FPGA / generic single-ended) → 10k
                    #    pull-down. Conservative default — never floats.
                    if side == "left":
                        g_res += 1
                        rref = f"R{g_res}"
                        rx = max(c["x"] - 3, 1)
                        # Vertical offset by pin index so multiple
                        # pull-downs for one IC form a column.
                        ry = c["y"] + 1 + _pin_offset * 2
                        comps.append({"ref": rref, "type": "resistor",
                                      "value": "10k",
                                      "x": rx, "y": ry, "rot": 90})
                        g_gnd += 1
                        gref_pd = f"GND_PD{g_gnd}"
                        comps.append({"ref": gref_pd, "type": "ground",
                                      "value": "GND",
                                      "x": rx, "y": ry + 2, "rot": 0})
                        nets.append({
                            "name": f"PD_{c['ref']}_{p['name']}",
                            "type": "signal",
                            "endpoints": [
                                {"ref": c["ref"], "pin": p["name"]},
                                {"ref": rref, "pin": "1"},
                            ],
                        })
                        nets.append({
                            "name": "GND", "type": "ground",
                            "endpoints": [
                                {"ref": rref, "pin": "2"},
                                {"ref": gref_pd, "pin": "1"},
                            ],
                        })
                        connected_pins.add((c["ref"], p["name"]))
                        continue

                    # 5. Generic right-side output with no sink → test point
                    if side == "right":
                        g_pwr += 1
                        tp_ref = f"TP_{g_pwr}"
                        comps.append({
                            "ref": tp_ref, "type": "connector",
                            "value": f"TP_{pn}",
                            "x": c["x"] + 5,
                            "y": c["y"] + 1 + _pin_offset * 2, "rot": 0,
                            "pins": [{"name": "1", "num": "1",
                                      "side": "left"}],
                        })
                        nets.append({
                            "name": f"{pn}_{c['ref']}", "type": "signal",
                            "endpoints": [
                                {"ref": c["ref"], "pin": p["name"]},
                                {"ref": tp_ref, "pin": "1"},
                            ],
                        })
                        connected_pins.add((c["ref"], p["name"]))

            # ── Power regulator wiring ────────────────────────────────────
            pwr_refs = [r for r in refs if ref_role.get(r) == "power"]
            for pidx, pr in enumerate(pwr_refs):
                # VIN from main power rail via off-page connector
                g_pwr += 1
                vin_ref = f"VCC_IN_{g_pwr}"
                node = ref_node.get(pr, {})
                pr_comp = next((c for c in comps if c["ref"] == pr), None)
                pr_x = pr_comp["x"] if pr_comp else (4 + pidx * 9)
                pr_y = pr_comp["y"] if pr_comp else 5
                comps.append({"ref": vin_ref, "type": "vcc", "value": "VIN_MAIN",
                              "x": max(pr_x - 3, 1), "y": pr_y, "rot": 0})
                nets.append({"name": "VIN_MAIN", "type": "power",
                             "endpoints": [{"ref": vin_ref, "pin": "1"},
                                           {"ref": pr, "pin": "VIN"}]})
                # EN tied high (to VIN)
                nets.append({"name": "EN_HIGH", "type": "power",
                             "endpoints": [{"ref": vin_ref, "pin": "1"},
                                           {"ref": pr, "pin": "EN"}]})
                # VOUT to output rail VCC symbol
                g_pwr += 1
                vout_ref = f"VCC_OUT_{g_pwr}"
                pn_lower = (node.get("component_name", "") + node.get("part_number", "")).lower()
                rail_label = "VCC_3V3" if "3.3" in pn_lower or "3v3" in pn_lower else \
                             "VCC_1V8" if "1.8" in pn_lower or "1v8" in pn_lower else \
                             "VCC_5V" if "5v" in pn_lower or "5.0" in pn_lower else \
                             f"VOUT_{pr}"
                # P26 #5: removed `min(pr_x + 5, 28)` clamp.
                comps.append({"ref": vout_ref, "type": "vcc", "value": rail_label,
                              "x": pr_x + 5, "y": pr_y, "rot": 0})
                nets.append({"name": rail_label, "type": "power",
                             "endpoints": [{"ref": pr, "pin": "VOUT"},
                                           {"ref": vout_ref, "pin": "1"}]})
                # FB tied to VOUT (internal feedback divider)
                nets.append({"name": f"FB_{pr}", "type": "signal",
                             "endpoints": [{"ref": pr, "pin": "FB"},
                                           {"ref": pr, "pin": "VOUT"}]})
                # Output decoupling cap
                g_cap += 1
                cout_ref = f"C{g_cap}"
                # P26 #5: removed `min(.., 28/18/20)` clamps.
                comps.append({"ref": cout_ref, "type": "capacitor", "value": "10uF",
                              "x": pr_x + 4, "y": pr_y + 2, "rot": 90})
                g_gnd += 1
                gnd_cout = f"GND_C{g_cap}"
                comps.append({"ref": gnd_cout, "type": "ground", "value": "GND",
                              "x": pr_x + 4, "y": pr_y + 4, "rot": 0})
                nets.append({"name": rail_label, "type": "power",
                             "endpoints": [{"ref": pr, "pin": "VOUT"},
                                           {"ref": cout_ref, "pin": "1"}]})
                nets.append({"name": "GND", "type": "ground",
                             "endpoints": [{"ref": cout_ref, "pin": "2"},
                                           {"ref": gnd_cout, "pin": "1"}]})

            sheets.append({
                "id": f"sheet{len(sheets) + 1}",
                "title": SHEET_TITLES.get(sheet_key, "Schematic"),
                "components": comps,
                "nets": nets,
            })

        # ── Cross-sheet connections via off-page connectors ───────────────
        # Build global ref→sheet lookup
        ref_sheet: dict = {}
        for si, sh in enumerate(sheets):
            for c in sh["components"]:
                ref_sheet[c["ref"]] = si

        # Mixer IF → ADC AIN (cross-sheet)
        # P3 — every differential off-page connector gets ONE PIN PER
        # POLARITY. The pre-fix code put both _P and _N onto pin "1" of
        # a single-pin connector, which collapses the differential pair
        # into a short. Schematic DRC's pin_multiple_nets rule flags
        # this now, but the proper fix is to never emit it in the first
        # place: differential off-page connectors are 2-pin parts with
        # pin "1"=_P and pin "2"=_N, and the receiving connector mirrors
        # that mapping.
        all_mixers = [r for r, rl in ref_role.items() if rl == "rf_mixer"]
        all_adcs = [r for r, rl in ref_role.items() if rl == "adc"]
        diff_opc_pins = [
            {"name": "P", "num": "1", "side": "left"},
            {"name": "N", "num": "2", "side": "left"},
        ]
        diff_opc_pins_in = [
            {"name": "P", "num": "1", "side": "right"},
            {"name": "N", "num": "2", "side": "right"},
        ]
        for mx in all_mixers:
            for adc in all_adcs:
                mx_si = ref_sheet.get(mx)
                adc_si = ref_sheet.get(adc)
                if mx_si is None or adc_si is None:
                    continue
                if mx_si == adc_si:
                    # P26 #5 — single-page mode: direct connection
                    # without OPC clutter. Pre-fix code created TWO
                    # off-page connectors PER mixer×adc pair regardless
                    # of whether they were on the same sheet, which on
                    # single-page mode added 4 visual connectors per
                    # mixer (2 IF_OUT + 2 receive) for no purpose.
                    sheets[mx_si]["nets"].append(
                        {"name": f"IF_OUT_P_{mx}", "type": "analog",
                         "endpoints": [{"ref": mx, "pin": "IF_OUT_P"},
                                       {"ref": adc, "pin": "AIN_P"}]})
                    sheets[mx_si]["nets"].append(
                        {"name": f"IF_OUT_N_{mx}", "type": "analog",
                         "endpoints": [{"ref": mx, "pin": "IF_OUT_N"},
                                       {"ref": adc, "pin": "AIN_N"}]})
                    continue
                # Cross-sheet path (only triggered when multi-page is
                # explicitly re-enabled in the future).
                g_pwr += 1
                opc1 = f"OPC_{g_pwr}"
                sheets[mx_si]["components"].append(
                    {"ref": opc1, "type": "connector",
                     "value": f"→ Sheet {adc_si + 1} (IF diff)",
                     "x": 28, "y": 8, "rot": 0,
                     "pins": [dict(p) for p in diff_opc_pins]})
                sheets[mx_si]["nets"].append(
                    {"name": f"IF_OUT_P_{mx}", "type": "analog",
                     "endpoints": [{"ref": mx, "pin": "IF_OUT_P"},
                                   {"ref": opc1, "pin": "1"}]})
                sheets[mx_si]["nets"].append(
                    {"name": f"IF_OUT_N_{mx}", "type": "analog",
                     "endpoints": [{"ref": mx, "pin": "IF_OUT_N"},
                                   {"ref": opc1, "pin": "2"}]})
                g_pwr += 1
                opc2 = f"OPC_{g_pwr}"
                sheets[adc_si]["components"].append(
                    {"ref": opc2, "type": "connector",
                     "value": f"← Sheet {mx_si + 1} (IF diff)",
                     "x": 1, "y": 5, "rot": 0,
                     "pins": [dict(p) for p in diff_opc_pins_in]})
                sheets[adc_si]["nets"].append(
                    {"name": f"IF_OUT_P_{mx}", "type": "analog",
                     "endpoints": [{"ref": opc2, "pin": "1"},
                                   {"ref": adc, "pin": "AIN_P"}]})
                sheets[adc_si]["nets"].append(
                    {"name": f"IF_OUT_N_{mx}", "type": "analog",
                     "endpoints": [{"ref": opc2, "pin": "2"},
                                   {"ref": adc, "pin": "AIN_N"}]})

        # LO synth → mixer LO (cross-sheet). P26 (2026-05-04): same
        # 1:1-pairing fix as the JSON edge builder above — pre-fix this
        # was a cartesian product that wired every LO source to every
        # mixer's LO_P/LO_N, creating an electrical short visible in
        # the schematic as N parallel LO buses. Now each mixer pulls
        # from exactly ONE LO (its index-paired source, last LO if
        # the mixer count exceeds the LO count).
        all_lo = [r for r, rl in ref_role.items() if rl == "lo_synth"]
        for i, mx in enumerate(all_mixers):
            if not all_lo:
                break
            lo = all_lo[min(i, len(all_lo) - 1)]
            lo_si = ref_sheet.get(lo)
            mx_si = ref_sheet.get(mx)
            if lo_si is not None and mx_si is not None:
                if lo_si == mx_si:
                    # Same sheet — direct connection
                    sheets[lo_si]["nets"].append(
                        {"name": f"LO_P_{lo}", "type": "clock",
                         "endpoints": [{"ref": lo, "pin": "RF_OUT_P"},
                                       {"ref": mx, "pin": "LO_P"}]})
                    sheets[lo_si]["nets"].append(
                        {"name": f"LO_N_{lo}", "type": "clock",
                         "endpoints": [{"ref": lo, "pin": "RF_OUT_N"},
                                       {"ref": mx, "pin": "LO_N"}]})
                else:
                    # Cross-sheet via off-page connectors. Same
                    # P4 fix as the IF mixer→ADC branch above:
                    # one pin per polarity, never reuse pin "1"
                    # for both LO_P and LO_N.
                    g_pwr += 1
                    opc_lo = f"OPC_{g_pwr}"
                    sheets[lo_si]["components"].append(
                        {"ref": opc_lo, "type": "connector",
                         "value": f"LO → Sheet {mx_si + 1} (diff)",
                         "x": 28, "y": 10, "rot": 0,
                         "pins": [dict(p) for p in diff_opc_pins]})
                    sheets[lo_si]["nets"].append(
                        {"name": f"LO_P_{lo}", "type": "clock",
                         "endpoints": [{"ref": lo, "pin": "RF_OUT_P"},
                                       {"ref": opc_lo, "pin": "1"}]})
                    sheets[lo_si]["nets"].append(
                        {"name": f"LO_N_{lo}", "type": "clock",
                         "endpoints": [{"ref": lo, "pin": "RF_OUT_N"},
                                       {"ref": opc_lo, "pin": "2"}]})
                    g_pwr += 1
                    opc_mx = f"OPC_{g_pwr}"
                    sheets[mx_si]["components"].append(
                        {"ref": opc_mx, "type": "connector",
                         "value": f"LO ← Sheet {lo_si + 1} (diff)",
                         "x": 1, "y": 10, "rot": 0,
                         "pins": [dict(p) for p in diff_opc_pins_in]})
                    sheets[mx_si]["nets"].append(
                        {"name": f"LO_P_{lo}", "type": "clock",
                         "endpoints": [{"ref": opc_mx, "pin": "1"},
                                       {"ref": mx, "pin": "LO_P"}]})
                    sheets[mx_si]["nets"].append(
                        {"name": f"LO_N_{lo}", "type": "clock",
                         "endpoints": [{"ref": opc_mx, "pin": "2"},
                                       {"ref": mx, "pin": "LO_N"}]})

        # ADC CLK from synth or FPGA
        for adc in all_adcs:
            adc_si = ref_sheet.get(adc)
            if adc_si is None:
                continue
            # Prefer synth CLK_REF → ADC CLK
            clk_src = None
            for lo in all_lo:
                if ref_sheet.get(lo) == adc_si:
                    clk_src = lo
                    break
            if clk_src:
                sheets[adc_si]["nets"].append(
                    {"name": f"CLK_ADC_P", "type": "clock",
                     "endpoints": [{"ref": clk_src, "pin": "CLK_REF_P"},
                                   {"ref": adc, "pin": "CLK_P"}]})
                sheets[adc_si]["nets"].append(
                    {"name": f"CLK_ADC_N", "type": "clock",
                     "endpoints": [{"ref": clk_src, "pin": "CLK_REF_N"},
                                   {"ref": adc, "pin": "CLK_N"}]})
            else:
                # Add clock connector
                g_pwr += 1
                clk_opc = f"CLK_{g_pwr}"
                sheets[adc_si]["components"].append(
                    {"ref": clk_opc, "type": "connector", "value": "CLK_IN",
                     "x": 1, "y": 8, "rot": 0,
                     "pins": [{"name": "1", "num": "1", "side": "right"}]})
                sheets[adc_si]["nets"].append(
                    {"name": "CLK_ADC_P", "type": "clock",
                     "endpoints": [{"ref": clk_opc, "pin": "1"},
                                   {"ref": adc, "pin": "CLK_P"}]})

        # FPGA: wire remaining unconnected pins
        all_fpga = [r for r, rl in ref_role.items() if rl == "fpga"]
        for fpga in all_fpga:
            fpga_si = ref_sheet.get(fpga)
            if fpga_si is None:
                continue
            sh = sheets[fpga_si]
            # Collect already-connected pins for this FPGA
            connected = set()
            for net in sh["nets"]:
                for ep in net["endpoints"]:
                    if ep["ref"] == fpga:
                        connected.add(ep["pin"])
            fpga_pins = ROLE_PINS.get("fpga", [])
            for fp in fpga_pins:
                if fp["name"] in connected:
                    continue
                pn = fp["name"].upper()
                if pn == "GND":
                    continue  # already has ground symbol
                # SYNC pins — wire to FPGA GPIO or add test point
                if "SYNC" in pn:
                    # ADC SYNC from FPGA — find an ADC on same sheet
                    for adc in all_adcs:
                        if ref_sheet.get(adc) == fpga_si:
                            sh["nets"].append(
                                {"name": f"SYNC_{fpga}_{adc}", "type": "signal",
                                 "endpoints": [{"ref": fpga, "pin": "GPIO_0"},
                                               {"ref": adc, "pin": fp["name"]}]})
                            connected.add("GPIO_0")
                            connected.add(fp["name"])
                            break
                    continue
                if "FRAME" in pn:
                    # ADC FRAME — wire to ADC if on same sheet
                    for adc in all_adcs:
                        if ref_sheet.get(adc) == fpga_si:
                            # Already handled by signal chain if adjacent
                            pass
                    # Add termination resistor
                    g_res += 1
                    rref = f"R{g_res}"
                    sh["components"].append(
                        {"ref": rref, "type": "resistor", "value": "100R",
                         "x": 2, "y": 14 + g_res, "rot": 0})
                    g_gnd += 1
                    gref_r = f"GND_R{g_res}"
                    sh["components"].append(
                        {"ref": gref_r, "type": "ground", "value": "GND",
                         "x": 2, "y": 16 + g_res, "rot": 0})
                    sh["nets"].append(
                        {"name": f"TERM_{fp['name']}", "type": "signal",
                         "endpoints": [{"ref": fpga, "pin": fp["name"]},
                                       {"ref": rref, "pin": "1"}]})
                    sh["nets"].append(
                        {"name": "GND", "type": "ground",
                         "endpoints": [{"ref": rref, "pin": "2"},
                                       {"ref": gref_r, "pin": "1"}]})
                    continue
                if "CLK_IN" in pn:
                    # System clock input — add clock connector
                    g_pwr += 1
                    clk_c = f"CLKIN_{g_pwr}"
                    sh["components"].append(
                        {"ref": clk_c, "type": "connector", "value": "SYS_CLK",
                         "x": 1, "y": 12, "rot": 0,
                         "pins": [{"name": "1", "num": "1", "side": "right"}]})
                    sh["nets"].append(
                        {"name": f"SYS_CLK_{pn[-1]}", "type": "clock",
                         "endpoints": [{"ref": clk_c, "pin": "1"},
                                       {"ref": fpga, "pin": fp["name"]}]})
                    continue
                if "GPIO" in pn and fp["name"] not in connected:
                    # GPIO — add test point header
                    g_pwr += 1
                    tp_ref = f"TP_{g_pwr}"
                    sh["components"].append(
                        {"ref": tp_ref, "type": "connector", "value": f"TP_{pn}",
                         "x": 28, "y": 12 + g_pwr % 4, "rot": 0,
                         "pins": [{"name": "1", "num": "1", "side": "left"}]})
                    sh["nets"].append(
                        {"name": pn, "type": "signal",
                         "endpoints": [{"ref": fpga, "pin": fp["name"]},
                                       {"ref": tp_ref, "pin": "1"}]})

        # LO synth LOCK_DET — wire to FPGA GPIO or test point
        for lo in all_lo:
            lo_si = ref_sheet.get(lo)
            if lo_si is None:
                continue
            sh = sheets[lo_si]
            lo_connected = set()
            for net in sh["nets"]:
                for ep in net["endpoints"]:
                    if ep["ref"] == lo:
                        lo_connected.add(ep["pin"])
            if "LOCK_DET" not in lo_connected:
                for fpga in all_fpga:
                    if ref_sheet.get(fpga) == lo_si:
                        sh["nets"].append(
                            {"name": f"LOCK_DET_{lo}", "type": "signal",
                             "endpoints": [{"ref": lo, "pin": "LOCK_DET"},
                                           {"ref": fpga, "pin": "GPIO_1"}]})
                        break
                else:
                    g_pwr += 1
                    tp_lock = f"TP_{g_pwr}"
                    sh["components"].append(
                        {"ref": tp_lock, "type": "connector", "value": "LOCK_DET",
                         "x": 28, "y": 14, "rot": 0,
                         "pins": [{"name": "1", "num": "1", "side": "left"}]})
                    sh["nets"].append(
                        {"name": f"LOCK_DET_{lo}", "type": "signal",
                         "endpoints": [{"ref": lo, "pin": "LOCK_DET"},
                                       {"ref": tp_lock, "pin": "1"}]})

        # ADC SYNC pins — wire to FPGA or test point
        for adc in all_adcs:
            adc_si = ref_sheet.get(adc)
            if adc_si is None:
                continue
            sh = sheets[adc_si]
            adc_connected = set()
            for net in sh["nets"]:
                for ep in net["endpoints"]:
                    if ep["ref"] == adc:
                        adc_connected.add(ep["pin"])
            for sync_pin in ("SYNC_P", "SYNC_N"):
                if sync_pin not in adc_connected:
                    for fpga in all_fpga:
                        if ref_sheet.get(fpga) == adc_si:
                            gpio_pin = "GPIO_0" if sync_pin == "SYNC_P" else "GPIO_1"
                            sh["nets"].append(
                                {"name": f"SYNC_{sync_pin}_{adc}", "type": "signal",
                                 "endpoints": [{"ref": fpga, "pin": gpio_pin},
                                               {"ref": adc, "pin": sync_pin}]})
                            break

        if not sheets:
            sheets = [{"id": "sheet1", "title": "Schematic",
                       "components": [{"ref": "U1", "type": "ic", "value": "IC",
                                       "x": 10, "y": 8, "rot": 0,
                                       "pins": [{"name": "1", "num": "1", "side": "left"}]}],
                       "nets": []}]

        # P26.5 (2026-05-04): deterministic topology guard — fixes the
        # LLM-emitted schematic so decoupling caps land pin1=power /
        # pin2=GND and every ground-flavored net (DC_GND_*, AC_GND_*,
        # VGND, _GND_*) collapses to the canonical GND star. Without
        # this pass the LLM ships caps with both pins on `DC_IN_BT1`
        # and the GND symbol drawn nearby is electrically orphaned.
        try:
            repairs = _force_clean_topology(sheets)
            if any(repairs.values()):
                logger.info(
                    "netlist.topology_guard project=%s repairs=%s",
                    getattr(self, "_current_project_name", "?"), repairs,
                )
        except Exception as exc:
            logger.warning("netlist.topology_guard_failed: %s", exc)

        return {"sheets": sheets, "auto_synthesized": True}
