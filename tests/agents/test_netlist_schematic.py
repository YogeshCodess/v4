"""Tests for the schematic-generation logic in agents.netlist_agent.

Focuses on the bug classes the user called out:
  * RF passives end up on the correct sheet
  * Passive RF blocks don't get VCC pins or decoupling caps
  * Active ICs get the full multi-value decoupling stack
  * Splitter unused ports are terminated into 50 Ω
  * Bias-tee DC_IN gets a choke + bulk cap
  * Components don't overlap geometrically
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from agents.netlist_agent import NetlistAgent


def _make_agent() -> NetlistAgent:
    """Bypass __init__ so tests don't need LLM clients."""
    return NetlistAgent.__new__(NetlistAgent)


def _run_schematic(agent: NetlistAgent, nodes: list[dict], edges: list[dict] | None = None):
    """Call the internal schematic builder. Returns the schematic dict."""
    netlist = {
        "nodes": nodes,
        "edges": edges or [],
        "power_nets": ["VCC_5V", "VCC_3V3"],
        "ground_nets": ["GND"],
    }
    return agent._synthesize_schematic(netlist)


def _sheet_of(schematic: dict, ref: str) -> str | None:
    for sh in schematic.get("sheets", []):
        for c in sh.get("components", []):
            if c.get("ref") == ref:
                return sh.get("title", "")
    return None


def _comp(schematic: dict, ref: str) -> dict | None:
    for sh in schematic.get("sheets", []):
        for c in sh.get("components", []):
            if c.get("ref") == ref:
                return c
    return None


def _all_comps(schematic: dict) -> list[dict]:
    out = []
    for sh in schematic.get("sheets", []):
        out.extend(sh.get("components", []))
    return out


def _nets(schematic: dict) -> list[dict]:
    out = []
    for sh in schematic.get("sheets", []):
        out.extend(sh.get("nets", []))
    return out


# --- Role-band placement tests ---------------------------------------------
#
# P26 #5 (2026-04-25): the schematic now collapses to a SINGLE page with
# all components on one sheet titled "Schematic". The previous per-sheet
# split (RF / ADC+Digital / Clock / Power) is gone, but components are
# still ordered LEFT→RIGHT, TOP→BOTTOM by role-band (connectors at top,
# RF chain below, ADC/FPGA below that, clock, then power at bottom).
# These tests verify the role-band Y-coordinate placement instead of
# the (now-defunct) sheet title.

# Y-coords for each band (matches `_band_y_base` in `_synthesize_schematic`).
# These are MINIMUMS — actual y depends on band-size accumulation, but
# the ORDERING (RF y < ADC y < Power y) is what we verify here.


def _y_of(schematic: dict, ref: str) -> float | None:
    c = _comp(schematic, ref)
    return c["y"] if c else None


def test_single_page_collapse():
    """P26 #5: all components on ONE sheet titled 'Schematic'."""
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "PIN Diode Limiter", "part_number": "CLA4603-000"},
        {"instance_id": "U2", "reference_designator": "U2",
         "component_name": "JESD204B ADC", "part_number": "AD9625"},
    ])
    assert len(s["sheets"]) == 1, (
        f"single-page mode must produce exactly 1 sheet, got {len(s['sheets'])}"
    )
    assert s["sheets"][0]["title"] == "Schematic"
    # Both components on the same sheet:
    assert _comp(s, "U1") is not None
    assert _comp(s, "U2") is not None


def test_limiter_above_adc_in_single_page_layout():
    """P26 #5: RF passives (limiter) must be in a HIGHER row band than
    ADCs. Replaces the old `RF sheet` placement test."""
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "PIN Diode Limiter", "part_number": "CLA4603-000"},
        {"instance_id": "U2", "reference_designator": "U2",
         "component_name": "JESD204B ADC", "part_number": "AD9625"},
    ])
    y_limiter = _y_of(s, "U1")
    y_adc = _y_of(s, "U2")
    assert y_limiter is not None and y_adc is not None
    assert y_limiter < y_adc, (
        f"limiter (RF band) must be ABOVE ADC (digital band) in single-"
        f"page layout — got y_limiter={y_limiter}, y_adc={y_adc}"
    )


def test_bias_tee_above_adc():
    """P26 #5: bias-tee is RF-band → above ADC-band."""
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Bias-Tee DC Injection", "part_number": "PE1604"},
        {"instance_id": "U2", "reference_designator": "U2",
         "component_name": "JESD204B ADC", "part_number": "AD9625"},
    ])
    assert _y_of(s, "U1") < _y_of(s, "U2")


def test_splitter_above_adc():
    """P26 #5: splitter is RF-band → above ADC-band."""
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "4-Way Wilkinson Splitter", "part_number": "MPD4-0108CSP2"},
        {"instance_id": "U2", "reference_designator": "U2",
         "component_name": "JESD204B ADC", "part_number": "AD9625"},
    ])
    assert _y_of(s, "U1") < _y_of(s, "U2")


def test_attenuator_and_isolator_above_adc():
    """P26 #5: attenuator + isolator are RF-band → above ADC-band."""
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Fixed Attenuator Pad", "part_number": "YAT-6+"},
        {"instance_id": "U2", "reference_designator": "U2",
         "component_name": "Isolator", "part_number": "ABC-ISO"},
        {"instance_id": "U3", "reference_designator": "U3",
         "component_name": "JESD204B ADC", "part_number": "AD9625"},
    ])
    y_adc = _y_of(s, "U3")
    for ref in ("U1", "U2"):
        assert _y_of(s, ref) < y_adc, f"{ref} should be above ADC"


def test_real_adc_in_digital_band():
    """P26 #5: ADC's y-coord falls in the digital-band range (above
    clock/power, below RF/connector)."""
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "PIN Diode Limiter", "part_number": "CLA4603-000"},
        {"instance_id": "U2", "reference_designator": "U2",
         "component_name": "JESD204B ADC", "part_number": "AD9625"},
        {"instance_id": "U3", "reference_designator": "U3",
         "component_name": "LDO Regulator", "part_number": "TPS7A4501"},
    ])
    # ADC must be BELOW limiter (RF band) and ABOVE LDO (power band).
    y_adc = _y_of(s, "U2")
    assert _y_of(s, "U1") < y_adc < _y_of(s, "U3"), (
        f"ADC y-coord {y_adc} should be between limiter and LDO — "
        f"limiter={_y_of(s, 'U1')}, LDO={_y_of(s, 'U3')}"
    )


# --- Pin-model tests --------------------------------------------------------

def test_passive_limiter_has_no_vcc_pin():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "RF Limiter", "part_number": "CLA4603"},
    ])
    c = _comp(s, "U1")
    assert c is not None
    pin_names = {p["name"].upper() for p in c.get("pins", [])}
    assert "VCC" not in pin_names and "VDD" not in pin_names, \
        f"Limiter must not have VCC/VDD pin, got {pin_names}"
    assert {"RF_IN", "RF_OUT", "GND"}.issubset(pin_names), \
        f"Limiter missing RF ports: {pin_names}"


def test_bias_tee_has_dc_in_no_vcc():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Bias-Tee", "part_number": "PE1604"},
    ])
    c = _comp(s, "U1")
    assert c is not None
    pin_names = {p["name"].upper() for p in c.get("pins", [])}
    assert "VCC" not in pin_names, f"bias_tee should not have VCC: {pin_names}"
    assert "DC_IN" in pin_names, f"bias_tee missing DC_IN port: {pin_names}"
    assert "RF_IN" in pin_names and "RF_OUT" in pin_names


def test_splitter_has_four_outputs_no_vcc():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Wilkinson Splitter 1:4", "part_number": "BP4U1+"},
    ])
    c = _comp(s, "U1")
    assert c is not None
    pin_names = {p["name"].upper() for p in c.get("pins", [])}
    assert "VCC" not in pin_names
    # All four output ports present
    for i in range(1, 5):
        assert f"RF_OUT_{i}" in pin_names, f"missing RF_OUT_{i}"


# --- Decoupling / passive-network tests -------------------------------------

def test_passive_limiter_gets_no_decoupling_cap():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Limiter", "part_number": "CLA4603"},
    ])
    # All caps on the resulting schematic
    all_caps = [c for c in _all_comps(s) if c.get("type") == "capacitor"]
    # A lone limiter sheet should have zero caps (no ESD here because
    # there's no connector upstream, and limiters have no VCC).
    assert len(all_caps) == 0, \
        f"Limiter sheet should have no caps; got {[c['value'] for c in all_caps]}"


def test_active_lna_gets_multi_value_decoupling_stack():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Low-Noise Amplifier", "part_number": "HMC8410"},
    ])
    caps = [c for c in _all_comps(s) if c.get("type") == "capacitor"]
    values = {c["value"] for c in caps}
    # Multi-value stack: bulk + mid + HF
    assert "1uF" in values, f"missing bulk cap, got {values}"
    assert "100nF" in values, f"missing mid-band cap, got {values}"
    assert "10nF" in values, f"missing HF cap, got {values}"


# --- Splitter termination + bias-tee DC feed tests --------------------------

def test_splitter_unused_ports_terminated_with_50ohm():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Wilkinson Splitter", "part_number": "BP4U1+"},
    ])
    resistors = [c for c in _all_comps(s) if c.get("type") == "resistor"]
    # Secondary outputs (RF_OUT_2, RF_OUT_3, RF_OUT_4) all get 50 Ω terminations
    term_res = [r for r in resistors if r.get("value", "") == "50R"]
    assert len(term_res) >= 3, \
        f"expected ≥3 50Ω terminators, got {[r.get('value') for r in resistors]}"
    # Each secondary port should have a net tying it to a resistor
    term_nets = [n for n in _nets(s) if n.get("name", "").startswith("TERM_")]
    assert len(term_nets) >= 3


def test_bias_tee_dc_in_has_choke_and_bulk_cap():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Bias-Tee", "part_number": "PE1604"},
    ])
    inductors = [c for c in _all_comps(s) if c.get("type") == "inductor"]
    bulk_caps = [c for c in _all_comps(s)
                 if c.get("type") == "capacitor" and c.get("value") == "10uF"]
    assert inductors, "bias-tee must get an RF choke on DC_IN"
    assert bulk_caps, "bias-tee must get a bulk cap on DC_IN"


# --- Geometry tests ---------------------------------------------------------

def test_components_do_not_overlap_geometrically():
    agent = _make_agent()
    # Enough components to trigger row wrapping
    nodes = [{"instance_id": f"U{i}", "reference_designator": f"U{i}",
              "component_name": "LNA", "part_number": f"HMC841{i}"}
             for i in range(1, 8)]
    s = _run_schematic(agent, nodes)
    ic_positions = [(c["x"], c["y"]) for c in _all_comps(s)
                    if c.get("ref", "").startswith("U")]
    # Two ICs never placed at the same coordinate
    assert len(set(ic_positions)) == len(ic_positions), \
        f"overlap detected: {ic_positions}"
    # Column pitch is at least 8 units so IC bodies don't collide
    xs = sorted({x for x, _ in ic_positions})
    if len(xs) > 1:
        min_gap = min(b - a for a, b in zip(xs, xs[1:]))
        assert min_gap >= 8, f"columns too tight: gap={min_gap}, xs={xs}"


def test_chip_resistor_renders_as_resistor_not_ic():
    """User complaint: the Vishay CRCW chip resistor was drawn as an IC
    block with VCC/GND/IN/OUT pins. Verify it's now a native 2-pin
    resistor symbol."""
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "R1", "reference_designator": "R1",
         "component_name": "Chip Resistor", "part_number": "CRCW060310K0FKEA"},
    ])
    c = _comp(s, "R1")
    assert c is not None
    assert c["type"] == "resistor", \
        f"resistor must render as type='resistor', got {c['type']}"
    # Passives have no 'pins' list — they use the 2-pin ("1"/"2") convention
    assert "pins" not in c or not c.get("pins")


def test_chip_capacitor_renders_as_capacitor_not_ic():
    """Same failure mode as resistors — Murata GRM part number must map
    to the native capacitor symbol.
    """
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "C4", "reference_designator": "C4",
         "component_name": "Chip Capacitor 0.1uF", "part_number": "GRM188R71H104KA93D"},
    ])
    c = _comp(s, "C4")
    assert c is not None
    assert c["type"] == "capacitor", \
        f"capacitor must render as type='capacitor', got {c['type']}"


def test_passive_rlc_gets_no_decoupling_or_vcc():
    """Resistors/caps/inductors/diodes must never get auto-injected
    decoupling stacks or VCC symbols — only real active ICs do.
    """
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "R1", "reference_designator": "R1",
         "component_name": "Chip Resistor 10k", "part_number": "CRCW060310K0FKEA"},
        {"instance_id": "C1", "reference_designator": "C1",
         "component_name": "Chip Capacitor 100nF", "part_number": "GRM188R71H104KA93D"},
    ])
    # No VCC symbol should be emitted for an R-and-C-only sheet
    vcc_syms = [c for c in _all_comps(s) if c.get("type") == "vcc"]
    assert vcc_syms == [], \
        f"no VCC symbol should be emitted for passive-only sheet, got {vcc_syms}"
    # Total caps should be exactly 1 (the C1 itself), no decoupling stack
    caps = [c for c in _all_comps(s) if c.get("type") == "capacitor"]
    assert len(caps) == 1, \
        f"expected only C1, got {[c['ref'] for c in caps]}"


def test_auto_ref_counters_do_not_collide_with_llm_refs():
    """User complaint: LLM R1 collided with auto-generated R1 from
    splitter terminators. Verify seed counters skip past existing refs.
    """
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "R1", "reference_designator": "R1",
         "component_name": "Chip Resistor", "part_number": "CRCW060310K0FKEA"},
        {"instance_id": "R2", "reference_designator": "R2",
         "component_name": "Chip Resistor", "part_number": "CRCW060310K0FKEA"},
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Wilkinson Splitter 1:4", "part_number": "BP4U1+"},
    ])
    refs = [c["ref"] for c in _all_comps(s) if c.get("ref")]
    # No duplicates
    assert len(refs) == len(set(refs)), \
        f"duplicate refs: {[r for r in refs if refs.count(r) > 1]}"
    # The LLM's R1 and R2 are preserved
    assert "R1" in refs and "R2" in refs
    # Auto-generated splitter terminator resistors come after R2
    term_rs = [r for r in refs if r.startswith("R") and r != "R1" and r != "R2"]
    for r in term_rs:
        suffix = int(r[1:])
        assert suffix >= 3, f"auto-resistor {r} collides with LLM range"


def test_ground_symbol_lands_on_ic_gnd_pin_not_offset():
    """User complaint: U1's ground pin is far from the ground symbol.
    Verify the ground symbol's anchor (0.5, 0) lands on the IC's GND
    pin after pin-anchor math.
    """
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Limiter", "part_number": "PE8022"},
    ])
    ic = _comp(s, "U1")
    gnd_syms = [c for c in _all_comps(s) if c.get("type") == "ground"]
    assert gnd_syms, "no ground symbol emitted"

    # For a 3-pin limiter (2 LR pins + 1 bottom GND), size is w=max(4,0+2)=4,
    # h=max(3, max(1,1,2)+1)=3. GND pin lives on bottom: pos = w/(1+1) = 2.
    # So GND pin is at (ic.x + 2, ic.y + 3). The ground symbol's anchor
    # (0.5, 0) must land there → ground.x = ic.x + 1.5, ground.y = ic.y + 3.
    expected_anchor_x = ic["x"] + 2
    expected_anchor_y = ic["y"] + 3

    # The ground symbol directly under the IC GND pin
    ic_gnd_sym = min(gnd_syms,
                     key=lambda g: abs((g["x"] + 0.5) - expected_anchor_x)
                                 + abs(g["y"] - expected_anchor_y))
    assert abs((ic_gnd_sym["x"] + 0.5) - expected_anchor_x) <= 1.0, \
        f"ground x off: ground.x={ic_gnd_sym['x']}, expected anchor x={expected_anchor_x}"
    assert abs(ic_gnd_sym["y"] - expected_anchor_y) <= 1.0, \
        f"ground y off: ground.y={ic_gnd_sym['y']}, expected anchor y={expected_anchor_y}"


def test_esd_diode_is_wired_not_floating():
    """User complaint: D4 (ESD) is hanging. Verify the diode is
    type='diode_tvs', rot=90, and both its pins are in nets."""
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "J1", "reference_designator": "J1",
         "component_name": "SMA Connector", "part_number": "SMA-J"},
    ])
    tvs = [c for c in _all_comps(s) if c.get("type") == "diode_tvs"]
    assert tvs, "no ESD/TVS diode emitted"
    d = tvs[0]
    assert d.get("rot") == 90, \
        f"ESD must be vertical (rot=90) so anode→signal, cathode→GND; got rot={d.get('rot')}"

    # Both pins of the diode must be in nets (not hanging)
    nets = _nets(s)
    d_refs_pins = set()
    for n in nets:
        for ep in n["endpoints"]:
            if ep["ref"] == d["ref"]:
                d_refs_pins.add(ep["pin"])
    assert "1" in d_refs_pins, f"ESD anode (pin 1) not in any net"
    assert "2" in d_refs_pins, f"ESD cathode (pin 2) not in any net"


def test_connector_pin_names_resolve_not_hanging():
    """User complaint: pin 1 of J1 is hanging. Root cause was the
    connector was rendered as type='connector' (numeric pins only) but
    nets referenced named pins like 'RF_P'. Verify the connector is now
    an IC with resolvable named pins.
    """
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "J1", "reference_designator": "J1",
         "component_name": "SMA Connector", "part_number": "SMA-J"},
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "LNA", "part_number": "HMC8410"},
    ])
    j = _comp(s, "J1")
    assert j is not None
    assert j.get("type") == "ic", \
        f"connector must render as IC for named-pin lookup; got {j.get('type')}"
    # Pins array present with RF_OUT + GND
    pin_names = {p["name"].upper() for p in j.get("pins", [])}
    assert "RF_OUT" in pin_names, f"connector missing RF_OUT pin: {pin_names}"
    assert "GND" in pin_names, f"connector missing GND pin: {pin_names}"


def test_decoupling_caps_align_to_vcc_pin():
    """User complaint: caps hang near VCC. Verify every decoupling cap's
    pin 1 (at cap.x - 0.5 after rot=90) lands on the IC's VCC anchor x.
    """
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "LNA", "part_number": "HMC8410"},
    ])
    ic = _comp(s, "U1")
    pins = ic.get("pins", [])
    # Compute IC bbox w/h the same way the TS renderer does
    sides = {"left": 0, "right": 0, "top": 0, "bottom": 0}
    for p in pins:
        sides[p["side"]] += 1
    w = max(4, max(sides["top"], sides["bottom"], 0) + 2)
    # Find VCC pin's global x (expecting single top pin centered)
    top_pins = [p for p in pins if p["side"] == "top"]
    vcc_pos_x = ic["x"] + (w / (len(top_pins) + 1)) * 1
    # Only the decoupling-stack caps (rot=90, values 1uF/100nF/10nF) —
    # AC-ground caps on unused differential inputs have rot=0 and a
    # different placement, so we exclude them.
    decoup_caps = [c for c in _all_comps(s)
                   if c.get("type") == "capacitor"
                   and c.get("rot") == 90
                   and c.get("value") in ("1uF", "100nF", "10nF")]
    assert decoup_caps, "no decoupling stack caps found"
    for c in decoup_caps:
        pin1_x = c["x"] - 0.5
        assert abs(pin1_x - vcc_pos_x) <= 1.0, \
            f"cap {c['ref']} pin1 x={pin1_x} does not align with VCC anchor x={vcc_pos_x}"


def test_no_vcc_symbol_emitted_when_sheet_is_all_passive():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Limiter", "part_number": "CLA4603"},
        {"instance_id": "U2", "reference_designator": "U2",
         "component_name": "Wilkinson Splitter", "part_number": "MPD4-0108CSP2"},
    ])
    vcc_syms = [c for c in _all_comps(s) if c.get("type") == "vcc"]
    assert vcc_syms == [], \
        f"all-passive sheet must emit no VCC symbol, got {vcc_syms}"


# --- P26 #5 single-page layout regression tests ----------------------------


def test_no_ic_overlap_with_many_components():
    """P26 #5: 60+ ICs across all role-bands must produce ZERO overlapping
    IC positions. The pre-fix multi-sheet code stacked all closure
    components (pull-down resistors, GND symbols, test points) at the
    SAME (cx, cy) per IC, and the role bands collided when one band
    wrapped past 6 columns into the next band's y-range."""
    agent = _make_agent()
    nodes = []
    # 12 RF amps, 12 ADCs, 12 power regs, 12 connectors, 12 clocks
    for n in range(12):
        nodes.append({"instance_id": f"U{n}",
                      "reference_designator": f"U{n}",
                      "component_name": f"LNA #{n}",
                      "part_number": f"HMC{8410+n}"})
        nodes.append({"instance_id": f"U{n+100}",
                      "reference_designator": f"U{n+100}",
                      "component_name": f"ADC #{n}",
                      "part_number": f"AD{9625+n}"})
        nodes.append({"instance_id": f"U{n+200}",
                      "reference_designator": f"U{n+200}",
                      "component_name": f"LDO Regulator #{n}",
                      "part_number": f"TPS{7400+n}"})
        nodes.append({"instance_id": f"J{n}",
                      "reference_designator": f"J{n}",
                      "component_name": f"SMA Connector #{n}",
                      "part_number": "SMA-F-RF"})
        nodes.append({"instance_id": f"U{n+300}",
                      "reference_designator": f"U{n+300}",
                      "component_name": f"PLL Clock Synth #{n}",
                      "part_number": f"LMX{2572+n}"})
    s = _run_schematic(agent, nodes)
    # Single sheet:
    assert len(s["sheets"]) == 1
    # No two ICs at the same (x, y):
    from collections import Counter
    ic_xys = [(round(c["x"], 1), round(c["y"], 1))
              for c in s["sheets"][0]["components"]
              if c.get("type") == "ic"]
    overlaps = [(k, v) for k, v in Counter(ic_xys).items() if v > 1]
    assert not overlaps, (
        f"ICs overlapping at same (x, y): {overlaps[:5]} — "
        f"the cumulative-band y-base allocation was supposed to "
        f"prevent this."
    )


def test_floating_pin_closure_distributes_by_pin_index():
    """P26 #5: pull-down resistors / test points / AC-caps for a multi-
    pin IC must FAN OUT vertically (one per pin index), not stack at
    the same (cx, cy). Pre-fix code placed every primary closure
    component at `cy = c["y"] + 1` regardless of pin position, producing
    visual overlap of 5+ symbols on top of each other.

    We ONLY check the PRIMARY closure components here (resistor + cap +
    test-point) — their paired GND/VCC symbols sit at `ry+2` / `ry-2`
    by design, which intentionally re-uses y-rows of the next pin's
    primary closure (R for pin 1 lives at y=8; GND for that R at y=10;
    R for pin 2 also at y=10 if pin_offset=1). That y-sharing of R+GND
    pairs is expected and the renderer handles it cleanly."""
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "FPGA / Zynq UltraScale+",
         "part_number": "XCZU4CG"},
    ])
    # PRIMARY closure components only (R for pull-up/pull-down,
    # capacitor for AC-coupling, connector for test-point). Skip the
    # paired GND / VCC symbols — they're decorative anchors, not the
    # core closure component.
    primary = [c for c in _all_comps(s)
               if c.get("ref", "").startswith(("R", "TP_"))
               or (c.get("type") == "capacitor"
                   and c.get("ref", "").startswith("C"))]
    # Group by x — primary closures for one IC side share an x.
    from collections import defaultdict
    by_x = defaultdict(list)
    for c in primary:
        by_x[round(c["x"], 0)].append(round(c["y"], 1))
    if by_x:
        # Find the column with the most primary closures — that's
        # one side of U1. No two should share a y.
        biggest_col = max(by_x.values(), key=len)
        assert len(set(biggest_col)) == len(biggest_col), (
            f"primary closure components in column overlap on y: "
            f"{sorted(biggest_col)}"
        )


def test_zero_ic_overlap_across_diverse_topologies():
    """P26 #5: prove the layout fix is GENERAL — not project-specific.
    Synthesise schematics for FIVE distinct topologies (radar, comms,
    SDR, big FPGA design, all-passive RF chain) and assert zero IC
    overlap on every one. The cumulative-band y-base allocation
    guarantees this property regardless of how many ICs land in any
    single band."""
    from collections import Counter
    agent = _make_agent()

    topologies = {
        # 1) Big radar receiver — heavy on RF chain
        "radar": [{"instance_id": f"U{i}", "reference_designator": f"U{i}",
                   "component_name": f"LNA #{i}",
                   "part_number": f"HMC{8410+i}"} for i in range(15)]
                + [{"instance_id": f"X{i}", "reference_designator": f"X{i}",
                    "component_name": f"Mixer #{i}",
                    "part_number": f"ADL{5801+i}"} for i in range(8)]
                + [{"instance_id": f"A{i}", "reference_designator": f"A{i}",
                    "component_name": f"ADC #{i}",
                    "part_number": f"AD{9625+i}"} for i in range(4)],
        # 2) Comms — balanced ADC + FPGA + power
        "comms": [{"instance_id": f"U{i}", "reference_designator": f"U{i}",
                   "component_name": f"ADC #{i}",
                   "part_number": f"AD{9625+i}"} for i in range(10)]
                + [{"instance_id": f"P{i}", "reference_designator": f"P{i}",
                    "component_name": f"LDO Regulator #{i}",
                    "part_number": f"TPS{7400+i}"} for i in range(8)]
                + [{"instance_id": f"F{i}", "reference_designator": f"F{i}",
                    "component_name": f"FPGA #{i}",
                    "part_number": f"XCZU{4+i}CG"} for i in range(3)],
        # 3) SDR — clock + LO synth heavy
        "sdr": [{"instance_id": f"U{i}", "reference_designator": f"U{i}",
                 "component_name": f"PLL Synth #{i}",
                 "part_number": f"LMX{2572+i}"} for i in range(12)]
              + [{"instance_id": f"C{i}", "reference_designator": f"C{i}",
                  "component_name": f"Clock Distribution #{i}",
                  "part_number": f"AD{9523+i}"} for i in range(6)],
        # 4) All-passive RF (limiters, BPFs, attenuators only)
        "passive_rf": [{"instance_id": f"U{i}", "reference_designator": f"U{i}",
                        "component_name": f"PIN Diode Limiter #{i}",
                        "part_number": f"CLA{4603+i}"} for i in range(20)]
                     + [{"instance_id": f"F{i}", "reference_designator": f"F{i}",
                         "component_name": f"BPF Filter #{i}",
                         "part_number": f"BPF{1000+i}"} for i in range(15)],
        # 5) Many connectors — forces band 0 wrap
        "connectors": [{"instance_id": f"J{i}", "reference_designator": f"J{i}",
                        "component_name": f"SMA Connector #{i}",
                        "part_number": "SMA-F-RF"} for i in range(20)],
    }

    failures = []
    for name, nodes in topologies.items():
        s = _run_schematic(agent, nodes)
        # Assert single page:
        if len(s["sheets"]) != 1:
            failures.append(f"{name}: expected 1 sheet, got {len(s['sheets'])}")
            continue
        # Assert zero IC overlap:
        ic_xys = [(round(c["x"], 1), round(c["y"], 1))
                  for c in s["sheets"][0]["components"]
                  if c.get("type") == "ic"]
        overlaps = [(k, v) for k, v in Counter(ic_xys).items() if v > 1]
        if overlaps:
            failures.append(
                f"{name}: {len(overlaps)} IC overlap positions, e.g. {overlaps[:3]}"
            )

    assert not failures, (
        "Layout fix is NOT general across topologies:\n  "
        + "\n  ".join(failures)
    )
