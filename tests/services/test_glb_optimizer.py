"""Tests for services.glb_optimizer."""
from __future__ import annotations

import copy
from services.glb_optimizer import (
    LIBRARY,
    _compute_cascade,
    _diagnose,
    _is_active,
    _is_passive,
    optimize,
    render_log_md,
    Targets,
)


# --- Fixtures ---------------------------------------------------------------

def _clean_glb() -> dict:
    """A small, already-valid GLB: input at -80 dBm, 20 dB gain, 2.5 dB NF."""
    return {
        "center_freq_mhz": 4000,
        "bandwidth_mhz": 100,
        "analysis_freq_mhz": 4050,
        "input_power_dbm": -80.0,
        "target_output_dbm": -60.0,
        "stages": [
            {"stage_name": "SMA In", "component": "SMA-50",
             "gain_db": -0.3, "noise_figure_db": 0.3},
            {"stage_name": "Limiter", "component": "PE8022",
             "gain_db": -0.5, "noise_figure_db": 0.5},
            {"stage_name": "LNA", "component": "HMC8410",
             "gain_db": 22.0, "noise_figure_db": 1.3,
             "p1db_out_dbm": 17.0, "oip3_out_dbm": 27.0,
             "bias_conditions": {"vdd_v": 3.3, "idq_ma": 60.0, "pdc_mw": 198.0}},
            {"stage_name": "SMA Out", "component": "SMA-50",
             "gain_db": -0.7, "noise_figure_db": 0.7},
        ],
    }


def _hj_like_glb() -> dict:
    """Bad design: gain shortfall, LNA buried behind SAW, filter NF wrong."""
    return {
        "center_freq_mhz": 4000,
        "bandwidth_mhz": 10,
        "analysis_freq_mhz": 4005,
        "input_power_dbm": -104.0,
        "target_output_dbm": -10.0,
        "stages": [
            {"stage_name": "SMA In", "component": "SMA-50",
             "gain_db": -0.7, "noise_figure_db": 0.7},
            {"stage_name": "PCB Trace", "component": "Microstrip",
             "gain_db": -0.3, "noise_figure_db": 0.3},
            {"stage_name": "Limiter", "component": "SKY16602",
             "gain_db": -0.6, "noise_figure_db": 0.6},
            {"stage_name": "Preselector SAW BPF", "component": "QPQ3509SR",
             "gain_db": -3.5, "noise_figure_db": 2.5},  # Friis mismatch on purpose
            {"stage_name": "Bias-T", "component": "LC bias tee",
             "gain_db": -0.2, "noise_figure_db": 0.2},
            {"stage_name": "LNA", "component": "GRF2084",
             "gain_db": 20.0, "noise_figure_db": 2.3,
             "p1db_out_dbm": 19.0, "oip3_out_dbm": 29.0,
             "bias_conditions": {"vdd_v": 5.0, "idq_ma": 80.0, "pdc_mw": 400.0}},
            {"stage_name": "4-Way Splitter", "component": "BP4U1+",
             "gain_db": -7.5, "noise_figure_db": 7.5},
            {"stage_name": "Channel BPF", "component": "BFHK-5001+",
             "gain_db": -3.5, "noise_figure_db": 2.5},  # Friis mismatch on purpose
            {"stage_name": "SMA Out", "component": "SMA-50",
             "gain_db": -0.2, "noise_figure_db": 0.2},
        ],
    }


# --- Classifier tests -------------------------------------------------------

def test_passive_classification_connector():
    st = {"stage_name": "SMA Connector (Input)", "component": "SMA panel mount",
          "gain_db": -0.7}
    assert _is_passive(st)
    assert not _is_active(st)


def test_passive_classification_attenuator_pad():
    st = {"stage_name": "Buffer Pad", "component": "Pi attenuator 6 dB",
          "gain_db": -6.0}
    assert _is_passive(st)
    assert not _is_active(st)


def test_active_classification_lna():
    st = {"stage_name": "LNA (Stage 1)", "component": "HMC594-SX",
          "gain_db": 19.5}
    assert not _is_passive(st)
    assert _is_active(st)


def test_active_classification_driver_gain_block():
    st = {"stage_name": "Driver Gain Block", "component": "PMA2-123LNW+",
          "gain_db": 11.5}
    assert _is_active(st)


# --- Cascade math tests -----------------------------------------------------

def test_cascade_math_clean_design():
    glb = _clean_glb()
    summary = _compute_cascade(glb["stages"], glb["input_power_dbm"])
    # -0.3 - 0.5 + 22.0 - 0.7 = 20.5 dB total gain
    assert summary["total_gain_db"] == 20.5
    # Final output = -80 + 20.5 = -59.5
    assert summary["final_output_dbm"] == -59.5
    # Friis NF: pre-LNA loss 0.8 dB + LNA 1.3 dB + tail mostly masked → ~2.3 dB
    assert 2.0 <= summary["final_nf_db"] <= 2.6


def test_cascade_populates_per_stage_fields():
    glb = _clean_glb()
    _compute_cascade(glb["stages"], glb["input_power_dbm"])
    for st in glb["stages"]:
        assert "cum_gain_db" in st
        assert "output_power_dbm" in st


# --- Diagnosis tests --------------------------------------------------------

def test_diagnose_clean_design_has_no_hard_issues():
    glb = _clean_glb()
    summary = _compute_cascade(glb["stages"], glb["input_power_dbm"])
    tgt = Targets(required_gain_db=20, target_nf_db=3.0, target_output_dbm=-60)
    issues = _diagnose(glb["stages"], summary, tgt)
    hard = [i for i in issues if i.severity == "hard"]
    assert len(hard) == 0, f"expected no hard issues, got {hard}"


def test_diagnose_catches_gain_shortfall():
    glb = _hj_like_glb()
    summary = _compute_cascade(glb["stages"], glb["input_power_dbm"])
    tgt = Targets(required_gain_db=94, target_output_dbm=-10)
    issues = _diagnose(glb["stages"], summary, tgt)
    codes = [i.code for i in issues]
    assert "GAIN_SHORT" in codes


def test_diagnose_catches_friis_mismatch():
    glb = _hj_like_glb()
    summary = _compute_cascade(glb["stages"], glb["input_power_dbm"])
    tgt = Targets()
    issues = _diagnose(glb["stages"], summary, tgt)
    friis = [i for i in issues if i.code == "FRIIS_MISMATCH"]
    assert len(friis) == 2, "expected 2 Friis mismatches (SAW + channel BPF)"


def test_diagnose_catches_bias_missing():
    glb = _clean_glb()
    # Strip the LNA's bias to trigger the rule
    for st in glb["stages"]:
        if "lna" in st.get("stage_name", "").lower():
            st.pop("bias_conditions", None)
    summary = _compute_cascade(glb["stages"], glb["input_power_dbm"])
    issues = _diagnose(glb["stages"], summary, Targets())
    assert any(i.code == "BIAS_MISSING" for i in issues)


def test_diagnose_catches_lna_deep():
    # LNA sits after 4+ dB of pre-selector loss — Friis penalty
    glb = _hj_like_glb()
    summary = _compute_cascade(glb["stages"], glb["input_power_dbm"])
    issues = _diagnose(glb["stages"], summary, Targets())
    assert any(i.code == "LNA_DEEP" for i in issues)


# --- optimize() end-to-end tests --------------------------------------------

def test_optimize_idempotent_on_clean_glb():
    glb = _clean_glb()
    before_primitives = [
        {k: v for k, v in st.items()
         if k in {"stage_name", "component", "gain_db", "noise_figure_db",
                  "p1db_out_dbm", "oip3_out_dbm", "bias_conditions"}}
        for st in glb["stages"]
    ]
    out, log = optimize(glb, {"required_gain_db": 20, "target_nf_db": 3.0,
                              "target_output_dbm": -60})
    # Should converge in 1 iteration with zero corrective actions
    assert len(log) == 1
    # Primitive fields are unchanged (optimizer may add cum_* computed fields)
    after_primitives = [
        {k: v for k, v in st.items()
         if k in {"stage_name", "component", "gain_db", "noise_figure_db",
                  "p1db_out_dbm", "oip3_out_dbm", "bias_conditions"}}
        for st in out["stages"]
    ]
    assert after_primitives == before_primitives


def test_optimize_closes_gain_shortfall():
    glb = _hj_like_glb()
    out, log = optimize(glb, {"required_gain_db": 94,
                              "target_nf_db": 3.0,
                              "target_output_dbm": -10})
    # Should run multiple iterations and converge
    final = log[-1].summary
    assert final["total_gain_db"] >= 90.0, f"gain still short: {final}"
    assert log[-1].iteration <= 6  # cap = 5 + final summary


def test_optimize_fixes_friis_mismatch_in_first_iteration():
    glb = _hj_like_glb()
    out, _ = optimize(glb, {"required_gain_db": 94})
    for st in out["stages"]:
        if not _is_active(st) and _is_passive(st):
            g = st.get("gain_db")
            nf = st.get("noise_figure_db")
            if isinstance(g, (int, float)) and isinstance(nf, (int, float)) and g <= 0:
                assert abs(nf - abs(g)) < 0.2, f"Friis still broken on {st}"


def test_optimize_respects_iteration_cap():
    # Contrive an unreachable target so the loop must cap out
    glb = _clean_glb()
    _, log = optimize(glb, {"required_gain_db": 500}, max_iterations=3)
    # Either converged, stalled, or capped — total entries must not exceed
    # max_iterations + 1 (the post-cap summary entry)
    assert len(log) <= 4


def test_optimize_adds_bias_for_missing_active_stage():
    glb = _clean_glb()
    for st in glb["stages"]:
        if "lna" in st.get("stage_name", "").lower():
            st.pop("bias_conditions", None)
    out, _ = optimize(glb, {})
    lna = next(s for s in out["stages"] if "lna" in s.get("stage_name", "").lower())
    assert isinstance(lna.get("bias_conditions"), dict)
    assert isinstance(lna["bias_conditions"].get("vdd_v"), (int, float))


def test_render_log_md_emits_iteration_headers():
    glb = _hj_like_glb()
    _, log = optimize(glb, {"required_gain_db": 94, "target_nf_db": 3.0,
                            "target_output_dbm": -10})
    md = render_log_md(log)
    assert "Closed-Loop Optimization Log" in md
    assert "Iteration 1" in md


# --- Library sanity ---------------------------------------------------------

def test_library_components_have_required_fields():
    for key, part in LIBRARY.items():
        assert "gain_db" in part, key
        assert "noise_figure_db" in part, key
        assert "component" in part, key
        # Every active lib part must carry a bias block
        if part["gain_db"] > 0:
            bc = part.get("bias_conditions")
            assert isinstance(bc, dict), f"{key} missing bias_conditions"
            assert isinstance(bc.get("vdd_v"), (int, float))
            assert isinstance(bc.get("idq_ma"), (int, float))
            assert isinstance(bc.get("pdc_mw"), (int, float))


# --- Propagation tests ------------------------------------------------------

from services.glb_optimizer import (
    propagate_to_bom,
    regenerate_block_diagram,
    propagate_changes,
)


def test_propagate_bom_adds_library_part_when_optimizer_inserts_stage():
    # Original BOM has a basic LNA. Optimizer inserts ADL5541 + HMC8410.
    comps = [
        {"function": "Low-Noise Amplifier", "primary_part": "HMC594-SX",
         "primary_manufacturer": "Analog Devices", "lifecycle_status": "active"},
    ]
    new_stages = [
        {"stage_name": "LNA", "component": "HMC8410", "gain_db": 22.0,
         "noise_figure_db": 1.3,
         "bias_conditions": {"vdd_v": 3.3, "idq_ma": 60, "pdc_mw": 198}},
        {"stage_name": "IF Amp", "component": "ADL5541", "gain_db": 16.0,
         "noise_figure_db": 3.0,
         "bias_conditions": {"vdd_v": 5, "idq_ma": 97, "pdc_mw": 485}},
    ]
    new_comps, log = propagate_to_bom(comps, new_stages)
    parts = {c.get("primary_part") for c in new_comps}
    assert "HMC8410" in parts
    assert "ADL5541" in parts
    # Log should mention both additions
    assert any("HMC8410" in line for line in log)
    assert any("ADL5541" in line for line in log)


def test_propagate_bom_is_idempotent():
    comps = [
        {"function": "LNA", "primary_part": "HMC8410",
         "primary_manufacturer": "Analog Devices", "lifecycle_status": "active"},
    ]
    stages = [
        {"stage_name": "LNA", "component": "HMC8410", "gain_db": 22,
         "noise_figure_db": 1.3,
         "bias_conditions": {"vdd_v": 3.3, "idq_ma": 60, "pdc_mw": 198}},
    ]
    new_comps, log = propagate_to_bom(comps, stages)
    # BOM already has HMC8410 — nothing to add
    assert len(new_comps) == 1
    assert not log


def test_propagate_bom_flags_removed_rf_part():
    # Original BOM has HMC594. Optimizer's stage list doesn't include it.
    comps = [
        {"function": "Low-Noise Amplifier", "primary_part": "HMC594-SX",
         "primary_manufacturer": "Analog Devices", "lifecycle_status": "active"},
    ]
    stages = [
        {"stage_name": "LNA", "component": "HMC8410", "gain_db": 22,
         "noise_figure_db": 1.3,
         "bias_conditions": {"vdd_v": 3.3, "idq_ma": 60, "pdc_mw": 198}},
    ]
    new_comps, log = propagate_to_bom(comps, stages)
    orphan = next(c for c in new_comps if c.get("primary_part") == "HMC594-SX")
    assert orphan.get("_orphan_flagged")
    assert any("HMC594-SX" in line and "flagged" in line.lower() for line in log)


def test_regenerate_block_diagram_includes_all_stages():
    stages = [
        {"stage_name": "SMA In", "component": "SMA-J", "gain_db": -0.2,
         "noise_figure_db": 0.2},
        {"stage_name": "LNA", "component": "HMC8410", "gain_db": 22.0,
         "noise_figure_db": 1.3,
         "bias_conditions": {"vdd_v": 3.3, "idq_ma": 60, "pdc_mw": 198}},
        {"stage_name": "SMA Out", "component": "SMA-J", "gain_db": -0.2,
         "noise_figure_db": 0.2},
    ]
    mermaid = regenerate_block_diagram(stages, 4000, 10)
    assert "flowchart LR" in mermaid
    assert "HMC8410" in mermaid
    assert "SMA-J" in mermaid
    # Antenna and output nodes
    assert "Antenna" in mermaid
    assert "Output" in mermaid
    # 3 stage nodes + ANT + OUT = 5 nodes minimum
    assert mermaid.count(" --> ") >= 3


def test_regenerate_block_diagram_annotates_multi_antenna():
    stages = [
        {"stage_name": "LNA", "component": "HMC8410", "gain_db": 22,
         "noise_figure_db": 1.3,
         "bias_conditions": {"vdd_v": 3.3, "idq_ma": 60, "pdc_mw": 198}},
    ]
    mermaid = regenerate_block_diagram(
        stages, 4000, 100, antenna_count=4, channel_count=8,
    )
    assert "×4 antenna" in mermaid or "x4 antenna" in mermaid
    assert "×8 channel" in mermaid or "x8 channel" in mermaid


def test_propagate_changes_updates_both_bom_and_diagram():
    tool_input = {
        "component_recommendations": [
            {"function": "Low-Noise Amplifier", "primary_part": "HMC594-SX",
             "primary_manufacturer": "Analog Devices", "lifecycle_status": "active"},
        ],
        "block_diagram_mermaid": "flowchart LR\n    A --> B",  # stale
    }
    new_stages = [
        {"stage_name": "SMA In", "component": "SMA-J", "gain_db": -0.2,
         "noise_figure_db": 0.2},
        {"stage_name": "LNA", "component": "HMC8410", "gain_db": 22,
         "noise_figure_db": 1.3,
         "bias_conditions": {"vdd_v": 3.3, "idq_ma": 60, "pdc_mw": 198}},
        {"stage_name": "IF Amp", "component": "ADL5541", "gain_db": 16,
         "noise_figure_db": 3.0,
         "bias_conditions": {"vdd_v": 5, "idq_ma": 97, "pdc_mw": 485}},
        {"stage_name": "SMA Out", "component": "SMA-J", "gain_db": -0.2,
         "noise_figure_db": 0.2},
    ]
    log = propagate_changes(
        tool_input, new_stages,
        center_freq_mhz=4000, bandwidth_mhz=100,
        antenna_count=1, channel_count=1,
    )
    # BOM now contains the library parts
    parts = {c.get("primary_part") for c in tool_input["component_recommendations"]}
    assert "HMC8410" in parts
    assert "ADL5541" in parts
    # Diagram was regenerated and contains all new stage components
    diag = tool_input["block_diagram_mermaid"]
    assert "HMC8410" in diag
    assert "ADL5541" in diag
    # Change log has entries for both BOM and diagram
    assert any("BOM" in line or "HMC8410" in line for line in log)
    assert any("block_diagram" in line.lower() for line in log)


def test_power_rails_swap_undersized_regulator():
    from services.glb_optimizer import optimize_power_rails
    # Existing BOM has a 150 mA LDO on the 5V rail
    comps = [
        {"function": "+12V to +5V LDO", "primary_part": "TPS7A2050PDBVR",
         "primary_manufacturer": "TI",
         "primary_key_specs": {"V_out": "5.0 V", "I_out (max)": "150 mA"},
         "lifecycle_status": "active"},
    ]
    # Stages draw 325 mA from 5V rail (above 150 mA * 1/margin → undersized)
    stages = [
        {"stage_name": "LNA1", "component": "HMC8410", "gain_db": 22,
         "noise_figure_db": 1.3,
         "bias_conditions": {"vdd_v": 5.0, "idq_ma": 200, "pdc_mw": 1000}},
        {"stage_name": "Driver", "component": "HMC462", "gain_db": 14,
         "noise_figure_db": 4.0,
         "bias_conditions": {"vdd_v": 5.0, "idq_ma": 125, "pdc_mw": 625}},
    ]
    new_comps, log = optimize_power_rails(comps, stages)
    # Converter should have been swapped — new part must support ≥ 325×1.3 = 423 mA
    reg = next(c for c in new_comps
               if c.get("function", "").lower().startswith("+12v to +5v"))
    i_max_str = reg["primary_key_specs"].get("I_out (max)", "")
    import re
    i_max = float(re.search(r"[\d.]+", i_max_str).group())
    assert i_max >= 423.0, f"swapped regulator too small: {i_max} mA"
    assert any("Swapped" in line and "+5" in line for line in log)


def test_power_rails_adds_missing_regulator():
    from services.glb_optimizer import optimize_power_rails
    # No regulator in BOM, but stages need 3.3V
    comps = []
    stages = [
        {"stage_name": "LNA", "component": "HMC8410", "gain_db": 22,
         "noise_figure_db": 1.3,
         "bias_conditions": {"vdd_v": 3.3, "idq_ma": 60, "pdc_mw": 198}},
    ]
    new_comps, log = optimize_power_rails(comps, stages)
    assert len(new_comps) == 1, "should have added a regulator"
    reg = new_comps[0]
    assert "3.3" in reg["function"] or "3.3" in reg["primary_description"]
    assert any("added" in line.lower() for line in log)


def test_power_rails_swap_thermally_failed_ldo_to_buck():
    """LDO dropping 12V→3.3V @ 300 mA dissipates 2.6 W. In a SOT-23 with
    θ_ja ≈ 250 °C/W, T_j = 85 + 2.6×250 = 735 °C — obviously cooked.
    Optimizer must swap to a buck that runs cool.
    """
    from services.glb_optimizer import optimize_power_rails, _library_entry_by_part
    comps = [
        # Existing LDO — current is fine, but thermal would explode
        {"function": "+12V to +3.3V Low-Noise LDO",
         "primary_part": "TPS7A2033PDBVR",
         "primary_manufacturer": "TI",
         "primary_key_specs": {"V_out": "3.3 V", "I_out (max)": "300 mA",
                                "theta_ja": "250 °C/W"},
         "lifecycle_status": "active"},
    ]
    stages = [
        {"stage_name": "Heavy load", "component": "LOAD", "gain_db": 10,
         "noise_figure_db": 2,
         "bias_conditions": {"vdd_v": 3.3, "idq_ma": 300, "pdc_mw": 990}},
    ]
    new_comps, log = optimize_power_rails(comps, stages, v_in_supply=12.0)

    # The LDO must be swapped
    reg = next(c for c in new_comps
               if c.get("function", "").lower().startswith("+12v to +3.3"))
    assert reg["primary_part"] != "TPS7A2033PDBVR"
    # The replacement must be a switcher (LDO can't survive 12→3.3 @ ~400 mA)
    lib = _library_entry_by_part(reg["primary_part"])
    assert lib and lib["type"].lower() != "ldo", \
        f"expected a switcher replacement, got {reg['primary_part']} ({lib['type'] if lib else '?'})"
    assert any("T_j" in line and "safety ceiling" in line for line in log)


def test_power_rails_small_ldo_drop_stays_as_ldo():
    """A 5V-only supply feeding a 3.3V LDO at 60 mA dissipates just 102 mW.
    T_j at 85 °C ambient = 85 + 0.102×250 = 110.5 °C — on the knife's edge.
    At 50 mA it's well within limits — should NOT swap to a switcher.
    """
    from services.glb_optimizer import optimize_power_rails, _library_entry_by_part
    comps = [
        {"function": "+5V to +3.3V LDO", "primary_part": "TPS7A2033PDBVR",
         "primary_manufacturer": "TI",
         "primary_key_specs": {"V_out": "3.3 V", "I_out (max)": "300 mA"},
         "lifecycle_status": "active"},
    ]
    stages = [
        {"stage_name": "Light load", "component": "SMALL", "gain_db": 14,
         "noise_figure_db": 2,
         "bias_conditions": {"vdd_v": 3.3, "idq_ma": 40, "pdc_mw": 132}},
    ]
    new_comps, log = optimize_power_rails(comps, stages, v_in_supply=5.0)
    # Should be untouched — LDO at 1.7 V drop × 52 mA = 88 mW is fine
    assert new_comps[0]["primary_part"] == "TPS7A2033PDBVR"
    assert not any("Swapped" in line for line in log)


def test_pick_converter_for_applies_thermal_gate():
    """Directly exercise the picker: 12V→3.3V @ 300 mA should NOT return
    the SOT-23 LDO even though it nominally meets current.
    """
    from services.glb_optimizer import _pick_converter_for, _library_entry_by_part
    c = _pick_converter_for(rail_v=3.3, required_ma=300, v_in=12.0)
    assert c is not None
    # Not the SOT-23 LDO (would cook)
    assert c["primary_part"] != "TPS7A2033PDBVR"
    # Must be thermally fine: P_diss = f(V_in, V_out, I_out) * ...
    lib = _library_entry_by_part(c["primary_part"])
    # Either a switcher, or an LDO with better θ_ja
    if lib and lib["type"].lower() == "ldo":
        assert lib["theta_ja_c_per_w"] < 100.0  # good thermal path needed


def test_power_rails_leaves_adequate_regulator_alone():
    from services.glb_optimizer import optimize_power_rails
    # BOM has a 3A buck on 5V, load is tiny — no swap
    comps = [
        {"function": "+12V to +5V Buck (3 A)", "primary_part": "LT8609SIMSE#PBF",
         "primary_manufacturer": "ADI",
         "primary_key_specs": {"V_out": "5 V", "I_out (max)": "3000 mA"},
         "lifecycle_status": "active"},
    ]
    stages = [
        {"stage_name": "LNA", "component": "HMC8410", "gain_db": 22,
         "noise_figure_db": 1.3,
         "bias_conditions": {"vdd_v": 5.0, "idq_ma": 60, "pdc_mw": 300}},
    ]
    new_comps, log = optimize_power_rails(comps, stages)
    # BOM should be unchanged (same part number)
    assert new_comps[0]["primary_part"] == "LT8609SIMSE#PBF"
    # No swap log
    assert not any("Swapped" in line for line in log)


def test_power_rails_computes_multi_rail_load_correctly():
    from services.glb_optimizer import _compute_rail_loads
    stages = [
        # 5V rail: 200 + 100 = 300 mA
        {"stage_name": "A", "component": "X", "gain_db": 10, "noise_figure_db": 2,
         "bias_conditions": {"vdd_v": 5.0, "idq_ma": 200, "pdc_mw": 1000}},
        {"stage_name": "B", "component": "Y", "gain_db": 10, "noise_figure_db": 2,
         "bias_conditions": {"vdd_v": 5.0, "idq_ma": 100, "pdc_mw": 500}},
        # 3.3V rail: 60 mA
        {"stage_name": "C", "component": "Z", "gain_db": 14, "noise_figure_db": 1.3,
         "bias_conditions": {"vdd_v": 3.3, "idq_ma": 60, "pdc_mw": 198}},
    ]
    loads = _compute_rail_loads(stages)
    assert abs(loads[5.0] - 300.0) < 0.1
    assert abs(loads[3.3] - 60.0) < 0.1


def test_propagate_changes_leaves_unchanged_diagram_alone():
    # When stage count matches existing diagram, diagram isn't regenerated.
    existing = (
        "flowchart LR\n"
        "    ANT((Antenna)) --> S1[SMA In]\n"
        "    S1 --> S2[LNA]\n"
        "    S2 --> S3[SMA Out]\n"
        "    S3 --> OUT[Output]\n"
    )
    tool_input = {
        "component_recommendations": [],
        "block_diagram_mermaid": existing,
    }
    new_stages = [
        {"stage_name": "SMA In", "component": "SMA-J", "gain_db": -0.2,
         "noise_figure_db": 0.2},
        {"stage_name": "LNA", "component": "HMC594", "gain_db": 20,
         "noise_figure_db": 2.0,
         "bias_conditions": {"vdd_v": 5, "idq_ma": 60, "pdc_mw": 300}},
        {"stage_name": "SMA Out", "component": "SMA-J", "gain_db": -0.2,
         "noise_figure_db": 0.2},
    ]
    propagate_changes(tool_input, new_stages, antenna_count=1, channel_count=1)
    # Stage count matches (3 stages + ANT + OUT = 5 nodes in existing diagram),
    # so the diagram stays
    assert tool_input["block_diagram_mermaid"] == existing
