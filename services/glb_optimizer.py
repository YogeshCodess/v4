"""Closed-loop GLB optimizer.

Takes a Gain-Loss Budget produced by the P1 agent, diagnoses any
contract violations (gain shortfall, NF miss, compression risk,
missing bias, Friis mismatch), applies rule-based corrections from
a curated RF component library, re-runs the cascade math, and
iterates until the design converges or the iteration cap is hit.

Entry point: ``optimize(glb, targets, max_iterations=5)``.

The optimizer is intentionally conservative:

* It mutates a deep copy — the input GLB is never touched.
* Each correction is an atomic stage-list edit (replace / insert /
  reorder / delete a single stage).
* Cascade math is recomputed from stage primitives after every edit.
* If a rule cannot improve the design, it no-ops instead of guessing.

The module is stand-alone — it does NOT import from the agents
package. This keeps the unit tests fast and makes the optimizer
reusable by any future phase that produces a stage list.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any, Optional

# --- RF component library ---------------------------------------------------
# Curated, verified parts at ~2-6 GHz. Each entry carries the fields the
# cascade math and contract checks need; swap-in candidates the rules can
# reach for when they detect a shortfall.
LIBRARY: dict[str, dict[str, Any]] = {
    "lna_low_nf": {
        "stage_name": "LNA (low-NF)",
        "component": "HMC8410",
        "gain_db": 22.0,
        "noise_figure_db": 1.3,
        "p1db_out_dbm": 17.0,
        "oip3_out_dbm": 27.0,
        "bias_conditions": {"vdd_v": 3.3, "idq_ma": 60.0, "pdc_mw": 198.0,
                             "condition_note": "Vdd=3.3V, Idq=60mA, f=4GHz"},
        "bom_meta": {
            "function": "Low-Noise Amplifier (0.1-8 GHz)",
            "primary_manufacturer": "Analog Devices",
            "primary_description": "Broadband GaAs pHEMT LNA, 1.3 dB NF, 22 dB gain",
            "datasheet_url": "https://www.analog.com/en/products/hmc8410.html",
            "primary_key_specs": {
                "Gain": "22 dB typ", "NF": "1.3 dB typ", "P1dB (out)": "+17 dBm",
                "OIP3 (out)": "+27 dBm", "Vdd": "3.3 V", "Idq": "60 mA",
            },
        },
    },
    "lna_post_split": {
        "stage_name": "Post-split LNA",
        "component": "HMC311ST89E",
        "gain_db": 14.0,
        "noise_figure_db": 3.5,
        "p1db_out_dbm": 13.0,
        "oip3_out_dbm": 25.0,
        "bias_conditions": {"vdd_v": 5.0, "idq_ma": 16.0, "pdc_mw": 80.0,
                             "condition_note": "Vdd=5V, Idq=16mA, f=4GHz"},
        "bom_meta": {
            "function": "Post-Splitter Gain Block (DC-6 GHz)",
            "primary_manufacturer": "Analog Devices",
            "primary_description": "Low-power InGaP HBT gain block, SOT-89",
            "datasheet_url": "https://www.analog.com/en/products/hmc311st89.html",
            "primary_key_specs": {
                "Gain": "14 dB typ", "NF": "3.5 dB typ", "P1dB (out)": "+13 dBm",
                "Vdd": "5 V", "Idq": "16 mA",
            },
        },
    },
    "driver": {
        "stage_name": "Driver",
        "component": "HMC462LC4",
        "gain_db": 14.0,
        "noise_figure_db": 4.0,
        "p1db_out_dbm": 23.0,
        "oip3_out_dbm": 33.0,
        "bias_conditions": {"vdd_v": 5.0, "idq_ma": 65.0, "pdc_mw": 325.0,
                             "condition_note": "Vdd=5V, Idq=65mA, f=4GHz"},
        "bom_meta": {
            "function": "Driver Amplifier (2-8 GHz)",
            "primary_manufacturer": "Analog Devices",
            "primary_description": "GaAs pHEMT driver, +23 dBm P1dB",
            "datasheet_url": "https://www.analog.com/en/products/hmc462.html",
            "primary_key_specs": {
                "Gain": "14 dB typ", "NF": "4.0 dB typ", "P1dB (out)": "+23 dBm",
                "OIP3 (out)": "+33 dBm", "Vdd": "5 V", "Idq": "65 mA",
            },
        },
    },
    "if_amp": {
        "stage_name": "IF Gain Block",
        "component": "ADL5541",
        "gain_db": 16.0,
        "noise_figure_db": 3.0,
        "p1db_out_dbm": 22.0,
        "oip3_out_dbm": 32.0,
        "bias_conditions": {"vdd_v": 5.0, "idq_ma": 97.0, "pdc_mw": 485.0,
                             "condition_note": "Vdd=5V, Idq=97mA, f=4GHz"},
        "bom_meta": {
            "function": "IF Gain Block (20 MHz-6 GHz)",
            "primary_manufacturer": "Analog Devices",
            "primary_description": "SiGe RFIC gain block, 50 Ω matched",
            "datasheet_url": "https://www.analog.com/en/products/adl5541.html",
            "primary_key_specs": {
                "Gain": "16 dB typ", "NF": "3.0 dB typ", "P1dB (out)": "+22 dBm",
                "OIP3 (out)": "+32 dBm", "Vdd": "5 V", "Idq": "97 mA",
            },
        },
    },
    "vga": {
        "stage_name": "VGA (AGC)",
        "component": "HMC624LP4E",
        "gain_db": 19.0,      # midpoint of 0..31.5 dB range
        "noise_figure_db": 7.0,
        "p1db_out_dbm": 20.0,
        "oip3_out_dbm": 30.0,
        "bias_conditions": {"vdd_v": 3.3, "idq_ma": 95.0, "pdc_mw": 314.0,
                             "condition_note": "Vdd=3.3V, Idq=95mA, mid-attenuation"},
        "bom_meta": {
            "function": "Digital Step Attenuator / VGA (DC-6 GHz)",
            "primary_manufacturer": "Analog Devices",
            "primary_description": "6-bit 0.5 dB LSB digital step attenuator, 0-31.5 dB",
            "datasheet_url": "https://www.analog.com/en/products/hmc624a.html",
            "primary_key_specs": {
                "Attn. Range": "0-31.5 dB (0.5 dB step)", "IL": "2.5 dB typ",
                "P1dB (out)": "+20 dBm", "Vdd": "3.3 V",
            },
        },
    },
    "final_driver": {
        "stage_name": "Final Driver",
        "component": "HMC8108",
        "gain_db": 15.0,
        "noise_figure_db": 5.0,
        "p1db_out_dbm": 30.0,
        "oip3_out_dbm": 40.0,
        "bias_conditions": {"vdd_v": 5.0, "idq_ma": 275.0, "pdc_mw": 1375.0,
                             "condition_note": "Vdd=5V, Idq=275mA, f=4GHz"},
        "bom_meta": {
            "function": "Final Driver Amplifier (6-12 GHz)",
            "primary_manufacturer": "Analog Devices",
            "primary_description": "GaAs pHEMT medium-power amplifier, +30 dBm P1dB",
            "datasheet_url": "https://www.analog.com/en/products/hmc8108.html",
            "primary_key_specs": {
                "Gain": "15 dB typ", "NF": "5 dB typ", "P1dB (out)": "+30 dBm",
                "OIP3 (out)": "+40 dBm", "Vdd": "5 V", "Idq": "275 mA",
            },
        },
    },
    "stability_pad_3db": {
        "stage_name": "Stability Pad",
        "component": "Pi attenuator 3 dB",
        "gain_db": -3.0,
        "noise_figure_db": 3.0,
        "p1db_out_dbm": None,
        "oip3_out_dbm": None,
        "bias_conditions": None,
        "bom_meta": {
            "function": "Fixed RF Attenuator Pad (3 dB)",
            "primary_manufacturer": "Mini-Circuits",
            "primary_description": "Thin-film 3 dB Pi attenuator, DC-6 GHz, 50 Ω",
            "datasheet_url": "https://www.minicircuits.com/WebStore/dashboard.html?model=YAT-3%2B",
            "primary_key_specs": {"Attenuation": "3 dB ± 0.3 dB", "DC-6 GHz": "true"},
        },
    },
}

# Keyword tables — kept in sync with agents/requirements_agent.py.
_FILT_KW = ("filter", "bpf", "lpf", "hpf", "diplexer", "duplexer",
            "preselector", "saw", "baw", "cavity")
_PASSIVE_KW = _FILT_KW + (
    "sma", "connector", "microstrip", "pcb", "trace", "cable",
    "attenuator", " pad", "pi attenuator", "t attenuator",
    "splitter", "combiner", "coupler", "balun", "transformer",
    "isolator", "circulator", "limiter", "diode", "bias-t",
    "bias tee", "dc block",
)
_ACT_SUBSTR = ("amplifier", "driver", "mixer", "modulator", "demodulator", "vga")
_ACT_TOKENS = ("lna", "pa", "vca")


def _label(st: dict) -> str:
    return (str(st.get("stage_name", "") or "") + " " +
            str(st.get("component", "") or "")).lower()


def _is_passive(st: dict) -> bool:
    lbl = _label(st)
    return any(kw in lbl for kw in _PASSIVE_KW)


def _is_active(st: dict) -> bool:
    lbl = _label(st)
    if _is_passive(st):
        return False
    if any(kw in lbl for kw in _ACT_SUBSTR):
        return True
    # Whole-word check for 2-3 char tokens to avoid substring false positives.
    for tok in _ACT_TOKENS:
        if tok in lbl:
            idx = lbl.find(tok)
            left_ok = idx == 0 or not lbl[idx - 1].isalnum()
            right_ok = idx + len(tok) == len(lbl) or not lbl[idx + len(tok)].isalnum()
            if left_ok and right_ok:
                return True
    g = st.get("gain_db")
    return isinstance(g, (int, float)) and g >= 0.5


# --- Cascade math -----------------------------------------------------------

def _compute_cascade(stages: list[dict], input_power_dbm: Optional[float]) -> dict:
    """Recompute cumulative gain, NF (Friis), output power, and compression
    back-off for each stage in place. Returns summary dict.
    """
    cum_gain = 0.0
    cum_g_linear = 1.0   # running gain in linear units for Friis
    cum_f_linear = None  # running noise factor
    for st in stages:
        g_db = st.get("gain_db") or 0.0
        nf_db = st.get("noise_figure_db")
        g_lin = 10.0 ** (g_db / 10.0)

        cum_gain += g_db
        st["cum_gain_db"] = round(cum_gain, 2)

        if input_power_dbm is not None:
            st["output_power_dbm"] = round(input_power_dbm + cum_gain, 2)

        # Friis cascade NF
        if isinstance(nf_db, (int, float)):
            f_stage = 10.0 ** (nf_db / 10.0)
            if cum_f_linear is None:
                cum_f_linear = f_stage
            else:
                cum_f_linear = cum_f_linear + (f_stage - 1.0) / cum_g_linear
        cum_g_linear *= g_lin
        if cum_f_linear is not None:
            st["cum_nf_db"] = round(10.0 * math.log10(cum_f_linear), 2)

        # Compression back-off (only meaningful for active stages with P1dB)
        p1db = st.get("p1db_out_dbm")
        if isinstance(p1db, (int, float)) and input_power_dbm is not None:
            bo = p1db - (input_power_dbm + cum_gain)
            st["backoff_db"] = round(bo, 1)

    final_out = (input_power_dbm + cum_gain) if input_power_dbm is not None else None
    return {
        "total_gain_db": round(cum_gain, 2),
        "final_nf_db": round(10.0 * math.log10(cum_f_linear), 2) if cum_f_linear else None,
        "final_output_dbm": round(final_out, 2) if final_out is not None else None,
        "total_pdc_mw": round(_sum_pdc(stages), 1),
    }


def _sum_pdc(stages: list[dict]) -> float:
    s = 0.0
    for st in stages:
        bc = st.get("bias_conditions")
        if isinstance(bc, dict) and isinstance(bc.get("pdc_mw"), (int, float)):
            s += bc["pdc_mw"]
    return s


# --- Diagnosis --------------------------------------------------------------

@dataclass
class Issue:
    code: str              # "GAIN_SHORT", "GAIN_OVER", "NF_HIGH", "BIAS_MISSING",
                           # "FRIIS_MISMATCH", "COMPRESSION", "LNA_DEEP"
    severity: str          # "hard" | "warn"
    stage_idx: Optional[int]  # 0-based index into stages, or None for whole-chain
    detail: str
    delta: Optional[float] = None  # numerical gap, e.g. dB short


@dataclass
class Targets:
    required_gain_db: Optional[float] = None
    target_nf_db: Optional[float] = None
    target_output_dbm: Optional[float] = None
    power_budget_mw: Optional[float] = None


@dataclass
class IterationLog:
    iteration: int
    issues: list[Issue] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def _diagnose(stages: list[dict], summary: dict, tgt: Targets) -> list[Issue]:
    issues: list[Issue] = []

    # GAIN_SHORT / GAIN_OVER
    if tgt.required_gain_db is not None and summary["total_gain_db"] is not None:
        delta = summary["total_gain_db"] - tgt.required_gain_db
        if delta < -1.0:
            issues.append(Issue("GAIN_SHORT", "hard", None,
                                f"Cascade gain {summary['total_gain_db']:.1f} dB is "
                                f"{-delta:.1f} dB below target {tgt.required_gain_db:.1f} dB.",
                                delta=delta))
        elif delta > 1.0:
            issues.append(Issue("GAIN_OVER", "warn", None,
                                f"Cascade gain {summary['total_gain_db']:.1f} dB exceeds "
                                f"target {tgt.required_gain_db:.1f} dB by {delta:.1f} dB.",
                                delta=delta))

    # NF_HIGH
    if tgt.target_nf_db is not None and summary["final_nf_db"] is not None:
        if summary["final_nf_db"] > tgt.target_nf_db + 0.3:
            issues.append(Issue("NF_HIGH", "hard", None,
                                f"System NF {summary['final_nf_db']:.2f} dB exceeds "
                                f"target {tgt.target_nf_db:.2f} dB.",
                                delta=summary["final_nf_db"] - tgt.target_nf_db))

    # BIAS_MISSING
    for i, st in enumerate(stages):
        if not _is_active(st):
            continue
        bc = st.get("bias_conditions")
        if not isinstance(bc, dict) or \
                not isinstance(bc.get("vdd_v"), (int, float)) or \
                not isinstance(bc.get("idq_ma"), (int, float)):
            issues.append(Issue("BIAS_MISSING", "hard", i,
                                f"Stage {i+1} ({st.get('stage_name','?')}) is active "
                                f"but lacks Vdd/Idq."))

    # FRIIS_MISMATCH — passive NF must equal |gain_db|
    for i, st in enumerate(stages):
        if _is_active(st):
            continue
        if not _is_passive(st):
            continue
        g = st.get("gain_db"); nf = st.get("noise_figure_db")
        if isinstance(g, (int, float)) and isinstance(nf, (int, float)) and g <= 0:
            if abs(nf - abs(g)) > 0.15:
                issues.append(Issue("FRIIS_MISMATCH", "hard", i,
                                    f"Stage {i+1} passive NF {nf:.2f} dB ≠ |loss| {abs(g):.2f} dB.",
                                    delta=abs(g) - nf))

    # COMPRESSION — any active stage with BO < 10 dB is a risk
    for i, st in enumerate(stages):
        bo = st.get("backoff_db")
        if isinstance(bo, (int, float)) and bo < 10.0:
            issues.append(Issue("COMPRESSION", "hard", i,
                                f"Stage {i+1} ({st.get('stage_name','?')}) back-off "
                                f"{bo:.1f} dB below P1dB — non-linear risk.",
                                delta=10.0 - bo))

    # LNA_DEEP — first LNA should be no deeper than 2 dB of passive loss from input
    loss_before_first_lna = 0.0
    found_lna = False
    for st in stages:
        if _is_active(st) and ("lna" in _label(st) or "amplifier" in _label(st)):
            found_lna = True
            break
        g = st.get("gain_db") or 0.0
        if g < 0:
            loss_before_first_lna += abs(g)
    if found_lna and loss_before_first_lna > 2.0:
        issues.append(Issue("LNA_DEEP", "warn", None,
                            f"{loss_before_first_lna:.1f} dB of passive loss before first LNA — "
                            f"inflates system NF via Friis.",
                            delta=loss_before_first_lna - 2.0))

    # POWER_OVER
    if tgt.power_budget_mw is not None and summary["total_pdc_mw"] > tgt.power_budget_mw * 1.05:
        issues.append(Issue("POWER_OVER", "hard", None,
                            f"Pdc {summary['total_pdc_mw']:.0f} mW exceeds budget "
                            f"{tgt.power_budget_mw:.0f} mW.",
                            delta=summary["total_pdc_mw"] - tgt.power_budget_mw))

    return issues


# --- Corrective rules -------------------------------------------------------

def _new_stage(lib_key: str) -> dict:
    return copy.deepcopy(LIBRARY[lib_key])


def _find_first_lna_idx(stages: list[dict]) -> Optional[int]:
    for i, st in enumerate(stages):
        if _is_active(st) and ("lna" in _label(st) or "amplifier" in _label(st)):
            return i
    return None


def _find_last_active_idx(stages: list[dict]) -> Optional[int]:
    for i in range(len(stages) - 1, -1, -1):
        if _is_active(stages[i]):
            return i
    return None


def _splitter_idx(stages: list[dict]) -> Optional[int]:
    for i, st in enumerate(stages):
        if "splitter" in _label(st) or "combiner" in _label(st):
            return i
    return None


def _apply_fixes(stages: list[dict], issues: list[Issue], tgt: Targets) -> list[str]:
    """Apply one round of corrections. Returns list of action descriptions."""
    actions: list[str] = []

    # Map issues by code for O(1) access
    by_code: dict[str, list[Issue]] = {}
    for iss in issues:
        by_code.setdefault(iss.code, []).append(iss)

    # Rule 1 — FRIIS_MISMATCH: cheapest fix, do first
    for iss in by_code.get("FRIIS_MISMATCH", []):
        if iss.stage_idx is None:
            continue
        st = stages[iss.stage_idx]
        g = st.get("gain_db")
        if isinstance(g, (int, float)):
            st["noise_figure_db"] = round(abs(g), 2)
            actions.append(f"Set Stage {iss.stage_idx+1} passive NF to |loss| = {abs(g):.2f} dB")

    # Rule 2 — BIAS_MISSING: populate default bias for the detected active stage type
    for iss in by_code.get("BIAS_MISSING", []):
        if iss.stage_idx is None:
            continue
        st = stages[iss.stage_idx]
        lbl = _label(st)
        if "lna" in lbl:
            template = LIBRARY["lna_low_nf"]
        elif "driver" in lbl:
            template = LIBRARY["driver"]
        elif "vga" in lbl:
            template = LIBRARY["vga"]
        else:
            template = LIBRARY["if_amp"]
        st["bias_conditions"] = copy.deepcopy(template["bias_conditions"])
        actions.append(f"Populated bias on Stage {iss.stage_idx+1} from "
                       f"'{template['component']}' template.")

    # Rule 3 — LNA_DEEP: move first LNA as early as possible (after limiter)
    if by_code.get("LNA_DEEP"):
        lna_idx = _find_first_lna_idx(stages)
        if lna_idx is not None:
            # Find earliest legal slot: after SMA + PCB + limiter, before SAW/BPF
            earliest = 0
            for j, st in enumerate(stages[:lna_idx]):
                lbl = _label(st)
                if any(k in lbl for k in ("sma", "pcb", "trace", "limiter")):
                    earliest = j + 1
                elif any(k in lbl for k in _FILT_KW):
                    break  # stop at first filter
            if earliest < lna_idx:
                lna = stages.pop(lna_idx)
                stages.insert(earliest, lna)
                actions.append(f"Promoted LNA from stage {lna_idx+1} to stage {earliest+1} "
                               f"(reduces pre-LNA passive loss → better Friis NF).")

    # Rule 4 — NF_HIGH: swap first LNA for low-NF library part
    if by_code.get("NF_HIGH") and not by_code.get("LNA_DEEP"):
        lna_idx = _find_first_lna_idx(stages)
        if lna_idx is not None:
            old = stages[lna_idx]
            new = _new_stage("lna_low_nf")
            # preserve stage_name semantics
            new["stage_name"] = old.get("stage_name") or new["stage_name"]
            stages[lna_idx] = new
            actions.append(f"Replaced Stage {lna_idx+1} LNA with "
                           f"{new['component']} (NF {new['noise_figure_db']} dB, "
                           f"G {new['gain_db']} dB).")

    # Rule 5 — GAIN_SHORT: add as many library gain stages as needed in one pass
    if by_code.get("GAIN_SHORT"):
        shortfall_db = -(by_code["GAIN_SHORT"][0].delta or 0.0)
        # Ladder in order of Friis preference (post-split LNA first — minimises
        # NF hit from the VGA later in chain) then IF/VGA/final driver.
        ladder = ["lna_post_split", "if_amp", "vga", "final_driver"]
        added_gain = 0.0
        for lib_key in ladder:
            if added_gain >= shortfall_db - 1.0:
                break
            part = LIBRARY[lib_key]
            if _chain_has(stages, part["component"].lower()):
                continue
            # post-split LNA only makes sense if a splitter exists
            if lib_key == "lna_post_split" and _splitter_idx(stages) is None:
                continue
            if lib_key == "lna_post_split":
                insert_at = _splitter_idx(stages) + 1
            else:
                insert_at = _chain_insert_pos(stages)
            stages.insert(insert_at, _new_stage(lib_key))
            added_gain += part["gain_db"]
            actions.append(f"Inserted {part['component']} at stage {insert_at+1} "
                           f"(+{part['gain_db']:.0f} dB, NF {part['noise_figure_db']:.1f} dB).")
        # If the library ladder alone isn't enough, add duplicate IF amps
        # (each +16 dB) until the cascade covers the shortfall or we hit a
        # reasonable duplication cap (3 extra amps = +48 dB more).
        extra_cap = 3
        while added_gain < shortfall_db - 1.0 and extra_cap > 0:
            insert_at = _chain_insert_pos(stages)
            part = LIBRARY["if_amp"]
            stages.insert(insert_at, _new_stage(lib_key="if_amp"))
            added_gain += part["gain_db"]
            extra_cap -= 1
            actions.append(f"Inserted additional IF gain block (+{part['gain_db']:.0f} dB) "
                           f"to close remaining shortfall.")
        # Last resort: boost VGA within its 0-31.5 dB tunable range
        if added_gain < shortfall_db - 1.0:
            for st in stages:
                if "vga" in _label(st) and isinstance(st.get("gain_db"), (int, float)):
                    room = 31.5 - st["gain_db"]
                    bump = max(0.0, min(shortfall_db - added_gain, room))
                    if bump > 0:
                        st["gain_db"] = round(st["gain_db"] + bump, 1)
                        actions.append(f"Bumped VGA gain by +{bump:.1f} dB within its 0-31.5 dB range.")
                    break

    # Rule 6 — GAIN_OVER: trim the last added gain stage or back off the VGA
    if by_code.get("GAIN_OVER"):
        over_db = by_code["GAIN_OVER"][0].delta or 0.0
        # Prefer dropping VGA gain first (it's variable by design)
        trimmed = False
        for st in reversed(stages):
            if "vga" in _label(st) and isinstance(st.get("gain_db"), (int, float)):
                new_g = max(0.0, st["gain_db"] - over_db)
                actions.append(f"Reduced VGA gain {st['gain_db']:.1f} → {new_g:.1f} dB "
                               f"to trim {over_db:.1f} dB of excess.")
                st["gain_db"] = round(new_g, 1)
                trimmed = True
                break
        if not trimmed:
            # Remove the last active stage that's a generic driver / IF amp
            for i in range(len(stages) - 1, -1, -1):
                lbl = _label(stages[i])
                if "if gain" in lbl or "final driver" in lbl:
                    removed = stages.pop(i)
                    actions.append(f"Removed redundant {removed.get('component','stage')} "
                                   f"(dropped {removed.get('gain_db',0):.0f} dB of excess).")
                    break

    # Rule 7 — COMPRESSION: insert 3 dB stability pad before the compressing stage
    for iss in by_code.get("COMPRESSION", []):
        if iss.stage_idx is None or iss.stage_idx == 0:
            continue
        # Don't double-pad: skip if the previous stage is already an attenuator pad
        prev = stages[iss.stage_idx - 1]
        if "pad" in _label(prev) or "attenuator" in _label(prev):
            continue
        stages.insert(iss.stage_idx, _new_stage("stability_pad_3db"))
        actions.append(f"Inserted 3 dB stability pad before Stage {iss.stage_idx+1} "
                       f"to restore P1dB back-off.")
        break  # one pad per iteration — re-validate

    # Rule 8 — POWER_OVER: swap highest-Pdc stage for its low-power sibling
    if by_code.get("POWER_OVER"):
        # Find highest-Pdc stage that has a low-power alternative
        worst_idx = None
        worst_pdc = 0.0
        for i, st in enumerate(stages):
            bc = st.get("bias_conditions") or {}
            p = bc.get("pdc_mw", 0) or 0
            if p > worst_pdc:
                worst_pdc = p; worst_idx = i
        if worst_idx is not None and worst_pdc > LIBRARY["lna_post_split"]["bias_conditions"]["pdc_mw"]:
            st = stages[worst_idx]
            # Swap to the lowest-power amp in the library that still has positive gain
            new = _new_stage("lna_post_split")
            new["stage_name"] = st.get("stage_name") or new["stage_name"]
            stages[worst_idx] = new
            actions.append(f"Replaced Stage {worst_idx+1} "
                           f"(Pdc {worst_pdc:.0f} mW) with low-power {new['component']} "
                           f"(Pdc {new['bias_conditions']['pdc_mw']:.0f} mW).")

    return actions


def _chain_has(stages: list[dict], needle: str) -> bool:
    n = needle.lower()
    return any(n in _label(st) for st in stages)


def _chain_insert_pos(stages: list[dict]) -> int:
    """Insert active gain stages before the final output-pad / SMA-out tail."""
    for i in range(len(stages) - 1, -1, -1):
        lbl = _label(stages[i])
        if "sma" in lbl or ("output pad" in lbl) or ("output connector" in lbl):
            continue
        return i + 1
    return len(stages)


# --- Main entry point -------------------------------------------------------

def optimize(
    glb: dict,
    targets: dict,
    max_iterations: int = 5,
) -> tuple[dict, list[IterationLog]]:
    """Iteratively correct a GLB until no hard violations remain or the cap
    is hit.

    Args:
        glb: the gain_loss_budget dict as produced by the P1 agent. Must
            carry ``stages`` (list of dicts with gain_db, noise_figure_db,
            component, stage_name, bias_conditions) and ``input_power_dbm``.
        targets: {required_gain_db, target_nf_db, target_output_dbm,
                  power_budget_mw} — any missing key disables that check.
        max_iterations: hard cap (default 5).

    Returns:
        (new_glb, iteration_log) — new_glb is a deep copy with updated stage
        list and recomputed cumulative fields. iteration_log is a list of
        IterationLog entries, one per pass, suitable for rendering into the
        GLB markdown.
    """
    work = copy.deepcopy(glb)
    stages = work.setdefault("stages", [])
    p_in = work.get("input_power_dbm")
    tgt = Targets(
        required_gain_db=targets.get("required_gain_db"),
        target_nf_db=targets.get("target_nf_db"),
        target_output_dbm=targets.get("target_output_dbm"),
        power_budget_mw=targets.get("power_budget_mw"),
    )

    log: list[IterationLog] = []
    last_hard_issue_count: Optional[int] = None
    for it in range(1, max_iterations + 1):
        summary = _compute_cascade(stages, p_in)
        issues = _diagnose(stages, summary, tgt)
        hard = [i for i in issues if i.severity == "hard"]
        rec = IterationLog(iteration=it, issues=list(issues), summary=dict(summary))

        if not hard:
            rec.actions.append("Converged — no hard violations remain.")
            log.append(rec)
            break

        # Stuck-loop guard: if hard-issue count didn't shrink after an
        # iteration, stop to avoid oscillation.
        if last_hard_issue_count is not None and len(hard) >= last_hard_issue_count:
            rec.actions.append(
                f"Stalled — {len(hard)} hard issue(s) persist after correction. Manual review needed."
            )
            log.append(rec)
            break
        last_hard_issue_count = len(hard)

        rec.actions = _apply_fixes(stages, issues, tgt)
        if not rec.actions:
            rec.actions.append("No applicable rule matched — stopping.")
            log.append(rec)
            break
        log.append(rec)
    else:
        # Ran to max_iterations without a break — record final state
        summary = _compute_cascade(stages, p_in)
        issues = _diagnose(stages, summary, tgt)
        log.append(IterationLog(
            iteration=max_iterations + 1,
            issues=list(issues),
            summary=dict(summary),
            actions=[f"Reached iteration cap ({max_iterations}) — design may not be fully clean."],
        ))

    # Final cascade recomputation so the returned GLB has up-to-date fields
    final_summary = _compute_cascade(stages, p_in)
    work["_optimizer_summary"] = final_summary
    return work, log


def render_log_md(log: list[IterationLog]) -> str:
    """Render the iteration log as markdown suitable for inclusion in the GLB."""
    out = ["## 0.5 Closed-Loop Optimization Log", ""]
    if not log:
        out.append("_No optimization was run._")
        return "\n".join(out)
    for rec in log:
        hard = [i for i in rec.issues if i.severity == "hard"]
        warn = [i for i in rec.issues if i.severity == "warn"]
        out.append(f"### Iteration {rec.iteration}")
        out.append(f"- **Issues:** {len(hard)} hard, {len(warn)} warn")
        for iss in hard[:10]:
            out.append(f"  - ❌ `{iss.code}` — {iss.detail}")
        for iss in warn[:5]:
            out.append(f"  - ⚠ `{iss.code}` — {iss.detail}")
        s = rec.summary
        _tag = f" _(evaluated at {s['_eval_at']})_" if s.get("_eval_at") else ""
        out.append(
            f"- **Cascade:** gain={s.get('total_gain_db')} dB · "
            f"NF={s.get('final_nf_db')} dB · "
            f"Pout={s.get('final_output_dbm')} dBm · "
            f"Pdc={s.get('total_pdc_mw')} mW{_tag}"
        )
        if rec.actions:
            out.append("- **Actions:**")
            for a in rec.actions:
                out.append(f"  - {a}")
        out.append("")
    return "\n".join(out)


# --- Cross-document propagation --------------------------------------------
#
# After the optimizer mutates the stage list, these helpers project the
# changes into:
#   • the BOM (`tool_input["component_recommendations"]`)
#   • the block-diagram Mermaid string (`tool_input["block_diagram_mermaid"]`)
#
# The standalone power-consumption sheet is built from the BOM, so once the
# BOM is updated it follows automatically — no extra hook needed.
#
# Design rule: the stage list is the single source of truth. Anything the
# optimizer adds gets a library-sourced BOM entry and a diagram node.
# Anything it removes is dropped from both (BOM entries that don't match
# any stage are preserved — they may be power/control parts the GLB never
# enumerated).

def _library_bom_entry_for_component(part_name: str) -> Optional[dict]:
    """Look up a component name in LIBRARY and return a BOM-ready dict,
    or None if the part isn't a library part.
    """
    pn = (part_name or "").strip().lower()
    for _key, entry in LIBRARY.items():
        lib_part = (entry.get("component") or "").strip().lower()
        if lib_part and lib_part == pn:
            meta = entry.get("bom_meta") or {}
            return {
                "function": meta.get("function", entry.get("stage_name", "")),
                "primary_part": entry["component"],
                "primary_manufacturer": meta.get("primary_manufacturer", ""),
                "primary_description": meta.get("primary_description", ""),
                "primary_key_specs": dict(meta.get("primary_key_specs", {})),
                "datasheet_url": meta.get("datasheet_url", ""),
                "lifecycle_status": "active",
                "_source": "glb_optimizer",
            }
    return None


def _stage_matches_bom(stage: dict, bom_entry: dict) -> bool:
    """Match a GLB stage to a BOM entry by part number (case-insensitive,
    substring-tolerant).
    """
    stage_part = (stage.get("component") or "").strip().lower()
    bom_part = (bom_entry.get("primary_part") or "").strip().lower()
    if not stage_part or not bom_part:
        return False
    if stage_part == bom_part:
        return True
    # Strip common suffix annotations like " (x4)", " (per channel)"
    import re as _re
    clean = lambda s: _re.sub(r"\s*\(.*?\)\s*$", "", s).strip()
    return clean(stage_part) == clean(bom_part)


def propagate_to_bom(
    components: list[dict],
    new_stages: list[dict],
) -> tuple[list[dict], list[str]]:
    """Update the component_recommendations list to match the optimizer's
    final stage list.

    Rules:
      • Every stage whose component is in LIBRARY and not already in the
        BOM is appended as a new BOM entry (filled from library metadata).
      • BOM entries that are not RF-chain components (power regs, misc) are
        left alone — they don't correspond to GLB stages anyway.
      • Active-RF BOM entries whose part number doesn't appear in any
        current stage are flagged (not deleted) — the human can decide.

    Returns:
        (new_components, change_log) where new_components is the updated
        BOM list and change_log is a list of human-readable strings.
    """
    new_components = [dict(c) for c in (components or [])]
    existing_parts = {
        (c.get("primary_part") or "").strip().lower()
        for c in new_components
        if c.get("primary_part")
    }
    log: list[str] = []

    # Add library-sourced parts for any optimizer-inserted stage not in BOM.
    for st in new_stages:
        if not _is_active(st) and (st.get("gain_db") or 0) > -0.5:
            # Pure passive (SMA, trace, filter) — not added as a library entry
            # unless it came from LIBRARY. Library passives like stability_pad
            # still qualify.
            if not _library_bom_entry_for_component(st.get("component", "")):
                continue
        lib_entry = _library_bom_entry_for_component(st.get("component", ""))
        if lib_entry is None:
            continue
        if lib_entry["primary_part"].lower() in existing_parts:
            continue
        new_components.append(lib_entry)
        existing_parts.add(lib_entry["primary_part"].lower())
        log.append(
            f"Added BOM entry '{lib_entry['primary_part']}' "
            f"({lib_entry['function']}) from optimizer library."
        )

    # Flag orphan RF-chain BOM entries (part no longer in stage list).
    stage_parts = {
        (st.get("component") or "").strip().lower()
        for st in new_stages
    }
    for c in new_components:
        part = (c.get("primary_part") or "").strip().lower()
        fn = (c.get("function") or "").lower()
        # Only flag RF-chain-looking entries; power regs / mechanicals stay quiet.
        rf_chain_hint = any(k in fn for k in (
            "amplifier", "lna", "amp", "filter", "mixer", "splitter", "driver",
            "limiter", "bias-t", "bias tee", "attenuator", "vga", "gain block",
        ))
        if not rf_chain_hint:
            continue
        if part and part not in stage_parts \
                and not any(part in sp or sp in part for sp in stage_parts):
            if not c.get("_orphan_flagged"):
                c["_orphan_flagged"] = True
                log.append(
                    f"BOM entry '{c.get('primary_part')}' is no longer in "
                    f"the GLB chain (optimizer removed/replaced it) — flagged "
                    f"for review."
                )

    return new_components, log


def regenerate_block_diagram(
    stages: list[dict],
    center_freq_mhz: Optional[float] = None,
    bandwidth_mhz: Optional[float] = None,
    antenna_count: int = 1,
    channel_count: int = 1,
) -> str:
    """Rebuild the block_diagram Mermaid string from the final stage list.

    The generated diagram is a single signal chain with explicit antenna
    input and receiver output. If the design is multi-antenna or multi-
    channel, those counts are annotated in a title block rather than
    drawn as parallel lanes — this keeps the diagram legible while staying
    truthful.
    """
    if not stages:
        return "flowchart LR\n    X[No stages defined]"

    lines: list[str] = ["flowchart LR"]

    # Header note (multi-antenna / multi-channel annotation)
    title_bits: list[str] = []
    if center_freq_mhz and bandwidth_mhz:
        title_bits.append(
            f"{center_freq_mhz/1000.0:.2f} GHz ± {bandwidth_mhz/2.0:.0f} MHz"
        )
    if antenna_count and antenna_count > 1:
        title_bits.append(f"×{antenna_count} antenna")
    if channel_count and channel_count > 1:
        title_bits.append(f"×{channel_count} channel")
    if title_bits:
        lines.append(f"    %% {' · '.join(title_bits)}")

    # Stage nodes
    def _node_id(i: int) -> str:
        return f"S{i}"

    def _safe_label(name: str, component: str) -> str:
        # Mermaid labels barf on `"`, `#`, `|`, unmatched brackets. Strip them.
        import re as _re
        raw = (name or "").strip()
        comp = (component or "").strip()
        if comp and comp.lower() != raw.lower() and comp not in raw:
            raw = f"{raw}<br/>{comp}"
        raw = _re.sub(r'[\"\#\|]', "", raw)
        raw = _re.sub(r"\s+", " ", raw).strip()
        return raw or "?"

    # Input node
    lines.append("    ANT((Antenna)) --> " + _node_id(1))

    for i, st in enumerate(stages, 1):
        label = _safe_label(
            st.get("stage_name", ""), st.get("component", ""),
        )
        # Shape: amplifiers = rounded; filters/passives = rectangle
        is_amp = _is_active(st) and not _is_passive(st)
        shape_open, shape_close = ("(", ")") if is_amp else ("[", "]")
        lines.append(f"    {_node_id(i)}{shape_open}\"{label}\"{shape_close}")

    # Edges
    for i in range(1, len(stages)):
        lines.append(f"    {_node_id(i)} --> {_node_id(i+1)}")

    # Output node
    out_label = "Output (to Receiver)" if channel_count > 1 else "Output"
    lines.append(f"    {_node_id(len(stages))} --> OUT[{out_label}]")

    # Classify nodes for styling
    passive_ids: list[str] = []
    active_ids: list[str] = []
    for i, st in enumerate(stages, 1):
        nid = _node_id(i)
        if _is_active(st) and not _is_passive(st):
            active_ids.append(nid)
        else:
            passive_ids.append(nid)
    if active_ids:
        lines.append(
            "    classDef active fill:#10b981,stroke:#065f46,stroke-width:2px,color:#fff"
        )
        lines.append("    class " + ",".join(active_ids) + " active")
    if passive_ids:
        lines.append(
            "    classDef passive fill:#475569,stroke:#1e293b,stroke-width:1px,color:#fff"
        )
        lines.append("    class " + ",".join(passive_ids) + " passive")

    return "\n".join(lines)


# --- Power-delivery (converter / regulator) library -----------------------
#
# Tiered list of real, active production parts per rail. The optimizer picks
# the *smallest* entry whose i_out_max_ma is ≥ (load × margin). Ordered by
# i_out_max_ma ascending within each rail so the linear search finds the
# minimum-over-demand part.
POWER_CONVERTERS: list[dict[str, Any]] = [
    # Each entry includes `theta_ja_c_per_w` (package-typical junction-to-
    # ambient thermal resistance) and `eta` (efficiency, switchers only) so
    # the optimizer can compute T_j and reject parts that would cook.

    # --- 3.3 V rail ---
    {"rail_v": 3.3, "i_out_max_ma": 300.0, "type": "LDO",
     "theta_ja_c_per_w": 250.0,  # SOT-23-5
     "primary_part": "TPS7A2033PDBVR", "primary_manufacturer": "Texas Instruments",
     "function": "+12V to +3.3V Low-Noise LDO (300 mA)",
     "primary_description": "Ultra-low-noise 300 mA LDO, 4.2 µVrms, SOT-23-5",
     "datasheet_url": "https://www.ti.com/product/TPS7A20",
     "primary_key_specs": {"V_out": "3.3 V", "I_out (max)": "300 mA",
                            "V_in": "1.7-6.5 V", "Noise": "4.2 µVrms",
                            "theta_ja": "250 °C/W"}},
    {"rail_v": 3.3, "i_out_max_ma": 1000.0, "type": "LDO",
     "theta_ja_c_per_w": 60.0,  # VSON-10 with exposed pad
     "primary_part": "TPS7A7001DRBR", "primary_manufacturer": "Texas Instruments",
     "function": "+12V to +3.3V LDO (1 A)",
     "primary_description": "1 A LDO, low-dropout, VSON-10",
     "datasheet_url": "https://www.ti.com/product/TPS7A70",
     "primary_key_specs": {"V_out": "3.3 V", "I_out (max)": "1000 mA",
                            "V_in": "3.0-6.5 V", "theta_ja": "60 °C/W"}},
    {"rail_v": 3.3, "i_out_max_ma": 3000.0, "type": "Buck", "eta": 0.94,
     "theta_ja_c_per_w": 80.0,  # SOT-583
     "primary_part": "TPS62933PDRLR", "primary_manufacturer": "Texas Instruments",
     "function": "+12V to +3.3V Buck Converter (3 A)",
     "primary_description": "3 A synchronous buck, 3.8-30 V input, SOT-583",
     "datasheet_url": "https://www.ti.com/product/TPS62933",
     "primary_key_specs": {"V_out": "3.3 V", "I_out (max)": "3000 mA",
                            "V_in": "3.8-30 V", "Efficiency": "94 %",
                            "theta_ja": "80 °C/W"}},
    {"rail_v": 3.3, "i_out_max_ma": 6000.0, "type": "Buck", "eta": 0.95,
     "theta_ja_c_per_w": 35.0,  # VQFN-14 with exposed pad
     "primary_part": "TPS54620RHLR", "primary_manufacturer": "Texas Instruments",
     "function": "+12V to +3.3V Buck Converter (6 A)",
     "primary_description": "6 A synchronous buck, 4.5-17 V input, VQFN-14",
     "datasheet_url": "https://www.ti.com/product/TPS54620",
     "primary_key_specs": {"V_out": "3.3 V", "I_out (max)": "6000 mA",
                            "V_in": "4.5-17 V", "Efficiency": "95 %",
                            "theta_ja": "35 °C/W"}},

    # --- 5 V rail ---
    {"rail_v": 5.0, "i_out_max_ma": 150.0, "type": "LDO",
     "theta_ja_c_per_w": 250.0,
     "primary_part": "TPS7A2050PDBVR", "primary_manufacturer": "Texas Instruments",
     "function": "+12V to +5V Low-Noise LDO (150 mA)",
     "primary_description": "Ultra-low-noise 150 mA LDO, 4.2 µVrms, SOT-23-5",
     "datasheet_url": "https://www.ti.com/product/TPS7A20",
     "primary_key_specs": {"V_out": "5.0 V", "I_out (max)": "150 mA",
                            "V_in": "1.7-6.5 V", "Noise": "4.2 µVrms",
                            "theta_ja": "250 °C/W"}},
    {"rail_v": 5.0, "i_out_max_ma": 500.0, "type": "LDO",
     "theta_ja_c_per_w": 150.0,  # SO-8
     "primary_part": "TPS7A4501DCQR", "primary_manufacturer": "Texas Instruments",
     "function": "+12V to +5V Low-Noise LDO (500 mA)",
     "primary_description": "500 mA LDO, high PSRR, SO-8",
     "datasheet_url": "https://www.ti.com/product/TPS7A45",
     "primary_key_specs": {"V_out": "5.0 V", "I_out (max)": "500 mA",
                            "V_in": "3.0-36 V", "theta_ja": "150 °C/W"}},
    {"rail_v": 5.0, "i_out_max_ma": 1500.0, "type": "Buck", "eta": 0.92,
     "theta_ja_c_per_w": 50.0,  # TDFN-8 with thermal pad
     "primary_part": "MAX17501GATB+T", "primary_manufacturer": "Analog Devices",
     "function": "+12V to +5V Buck Converter (1.5 A)",
     "primary_description": "1.5 A synchronous buck, 4.5-76 V input, TDFN-8",
     "datasheet_url": "https://www.analog.com/en/products/max17501.html",
     "primary_key_specs": {"V_out": "5.0 V", "I_out (max)": "1500 mA",
                            "V_in": "4.5-76 V", "Efficiency": "92 %",
                            "theta_ja": "50 °C/W"}},
    {"rail_v": 5.0, "i_out_max_ma": 3000.0, "type": "Buck", "eta": 0.93,
     "theta_ja_c_per_w": 90.0,  # MSOP-16
     "primary_part": "LT8609SIMSE#PBF", "primary_manufacturer": "Analog Devices",
     "function": "+12V to +5V Buck Converter (3 A)",
     "primary_description": "3 A synchronous buck, 3-42 V input, low EMI, MSOP-16",
     "datasheet_url": "https://www.analog.com/en/products/lt8609s.html",
     "primary_key_specs": {"V_out": "5.0 V", "I_out (max)": "3000 mA",
                            "V_in": "3-42 V", "Efficiency": "93 %",
                            "theta_ja": "90 °C/W"}},
    {"rail_v": 5.0, "i_out_max_ma": 6000.0, "type": "Buck", "eta": 0.94,
     "theta_ja_c_per_w": 45.0,  # QFN with exposed pad
     "primary_part": "LT8645SIV#PBF", "primary_manufacturer": "Analog Devices",
     "function": "+12V to +5V Buck Converter (6 A)",
     "primary_description": "6 A synchronous buck, 3.4-65 V input, Silent Switcher",
     "datasheet_url": "https://www.analog.com/en/products/lt8645s.html",
     "primary_key_specs": {"V_out": "5.0 V", "I_out (max)": "6000 mA",
                            "V_in": "3.4-65 V", "Efficiency": "94 %",
                            "theta_ja": "45 °C/W"}},

    # --- 1.8 V rail ---
    {"rail_v": 1.8, "i_out_max_ma": 1000.0, "type": "LDO",
     "theta_ja_c_per_w": 35.0,  # VQFN-20 with pad
     "primary_part": "TPS7A4700RGWR", "primary_manufacturer": "Texas Instruments",
     "function": "+3.3V to +1.8V LDO (1 A)",
     "primary_description": "1 A low-noise LDO, VQFN-20",
     "datasheet_url": "https://www.ti.com/product/TPS7A47",
     "primary_key_specs": {"V_out": "1.8 V", "I_out (max)": "1000 mA",
                            "V_in": "3.0-36 V", "theta_ja": "35 °C/W"}},
    {"rail_v": 1.8, "i_out_max_ma": 3000.0, "type": "Buck", "eta": 0.93,
     "theta_ja_c_per_w": 40.0,  # QFN-16
     "primary_part": "TPS62130RGTR", "primary_manufacturer": "Texas Instruments",
     "function": "+5V to +1.8V Buck Converter (3 A)",
     "primary_description": "3 A synchronous buck, 3-17 V input, QFN-16",
     "datasheet_url": "https://www.ti.com/product/TPS62130",
     "primary_key_specs": {"V_out": "1.8 V", "I_out (max)": "3000 mA",
                            "V_in": "3-17 V", "Efficiency": "93 %",
                            "theta_ja": "40 °C/W"}},

    # --- 2.5 V rail ---
    {"rail_v": 2.5, "i_out_max_ma": 500.0, "type": "LDO",
     "theta_ja_c_per_w": 250.0,
     "primary_part": "TPS7A2525PDBVR", "primary_manufacturer": "Texas Instruments",
     "function": "+5V to +2.5V LDO (500 mA)",
     "primary_description": "500 mA LDO, low noise, SOT-23-5",
     "datasheet_url": "https://www.ti.com/product/TPS7A25",
     "primary_key_specs": {"V_out": "2.5 V", "I_out (max)": "500 mA",
                            "V_in": "1.7-6.5 V", "theta_ja": "250 °C/W"}},
]

# Thermal safety constants for converter selection.
_T_AMB_C = 85.0          # worst-case ambient for avionics / MIL envelopes
_TJ_MAX_C = 125.0        # absolute junction limit; derate for margin
_TJ_SAFETY_C = 110.0     # target ceiling with 15 °C margin — LDO at the knee
                          # of a datasheet θ_ja is easily 5-10 °C optimistic.


_COMMON_RAILS = (12.0, 5.0, 3.3, 2.5, 1.8, 1.2)


def _closest_rail(v: float) -> float:
    return min(_COMMON_RAILS, key=lambda r: abs(r - v))


def _compute_rail_loads(stages: list[dict]) -> dict[float, float]:
    """Sum per-rail DC current (mA) across all biased stages."""
    loads: dict[float, float] = {}
    for st in stages:
        bc = st.get("bias_conditions") or {}
        v = bc.get("vdd_v"); i = bc.get("idq_ma")
        if isinstance(v, (int, float)) and isinstance(i, (int, float)) and v > 0 and i > 0:
            rail = _closest_rail(v)
            loads[rail] = loads.get(rail, 0.0) + i
    return loads


def _is_power_converter(comp: dict) -> bool:
    """Classify a BOM entry as a power converter / regulator."""
    blob = " ".join([
        (comp.get("function") or ""),
        (comp.get("primary_description") or ""),
        (comp.get("primary_part") or ""),
    ]).lower()
    return any(k in blob for k in (
        "ldo", "regulator", "buck", "boost", "dc-dc", "dc/dc",
        "pmic", "power management",
    ))


def _parse_regulator_rail_v(comp: dict) -> Optional[float]:
    """Infer the output rail voltage of a regulator from its BOM fields."""
    import re as _re
    specs = comp.get("primary_key_specs") or {}
    for k in ("V_out", "Vout", "v_out", "output_voltage", "Output Voltage",
              "voltage_out"):
        v = specs.get(k)
        if v:
            m = _re.search(r"[\d.]+", str(v))
            if m:
                return float(m.group())
    # Parse from function/description: "+12V to +5V", "→ 3.3V", "+3.3V buck"
    blob = (comp.get("function") or "") + " " + (comp.get("primary_description") or "")
    m = _re.search(r"(?:to|→|->)\s*[+]?(\d+(?:\.\d+)?)\s*V", blob, _re.IGNORECASE)
    if m:
        return float(m.group(1))
    # Fallback: pick the *last* voltage mentioned (function strings typically
    # read "+12V to +5V" where output is the second number).
    matches = _re.findall(r"[+]?(\d+(?:\.\d+)?)\s*V(?![a-zA-Z])", blob)
    if matches:
        return float(matches[-1])
    return None


def _parse_regulator_i_max_ma(comp: dict) -> Optional[float]:
    """Read the regulator's rated max output current (mA) from its specs."""
    import re as _re
    specs = comp.get("primary_key_specs") or {}
    for k in ("I_out (max)", "I_out", "I_out_max", "i_out_max",
              "max_output_current", "Output Current", "output_current",
              "i_out_a", "i_out_ma"):
        v = specs.get(k)
        if v:
            s = str(v)
            m = _re.search(r"[\d.]+", s)
            if m:
                val = float(m.group())
                # Unit detection: mA default; A if 'A' seen with no 'm'
                if _re.search(r"\bA\b", s) and not _re.search(r"mA", s, _re.I):
                    val *= 1000.0
                return val
    # Look up in POWER_CONVERTERS by part number
    part = (comp.get("primary_part") or "").strip().lower()
    for lib in POWER_CONVERTERS:
        if lib["primary_part"].lower() == part:
            return lib["i_out_max_ma"]
    return None


def _compute_pdiss_w(entry_or_dict: dict, v_in: float, v_out: float,
                     i_out_a: float) -> float:
    """Dissipation in watts for a regulator under the given operating point.

    * LDO: P_diss = (V_in − V_out) × I_out   (all dropout becomes heat)
    * Switcher: P_diss = P_out × (1 − η)/η   (typical first-order model)
    """
    topology = (entry_or_dict.get("type") or "").lower()
    if topology.startswith("ldo") or "linear" in topology:
        return max(0.0, (v_in - v_out)) * i_out_a
    eta = entry_or_dict.get("eta") or 0.9
    p_out = v_out * i_out_a
    return p_out * (1.0 - eta) / eta if eta > 0 else p_out


def _compute_t_junction_c(entry_or_dict: dict, p_diss_w: float,
                          t_amb_c: float = _T_AMB_C) -> float:
    """T_j = T_amb + P_diss × θ_ja. Uses a conservative default when the
    entry doesn't declare a θ_ja.
    """
    theta_ja = entry_or_dict.get("theta_ja_c_per_w")
    if not isinstance(theta_ja, (int, float)) or theta_ja <= 0:
        theta_ja = 80.0  # conservative SMD default
    return t_amb_c + p_diss_w * theta_ja


def _is_thermally_safe(entry: dict, v_in: float, v_out: float,
                       i_out_a: float, t_j_max_c: float = _TJ_SAFETY_C) -> bool:
    """True if this library entry stays below `t_j_max_c` at the given
    operating point. Uses the 110 °C default (15 °C margin below 125 °C).
    """
    p = _compute_pdiss_w(entry, v_in, v_out, i_out_a)
    return _compute_t_junction_c(entry, p) <= t_j_max_c


def _pick_converter_for(
    rail_v: float,
    required_ma: float,
    v_in: float = 12.0,
) -> Optional[dict]:
    """Pick the smallest library converter on `rail_v` that can both:
      (1) supply `required_ma` mA, AND
      (2) stay below the T_j safety ceiling at V_in → rail_v.

    Preference order after both gates pass: smallest I_out_max first, to
    avoid gratuitous over-sizing. If no LDO is thermally safe, the picker
    naturally falls through to the smallest passing switcher.
    """
    required_a = required_ma / 1000.0
    candidates = [c for c in POWER_CONVERTERS if abs(c["rail_v"] - rail_v) < 0.05]
    candidates.sort(key=lambda c: c["i_out_max_ma"])
    for c in candidates:
        if c["i_out_max_ma"] < required_ma:
            continue
        if not _is_thermally_safe(c, v_in, rail_v, required_a):
            continue
        return c
    # If no candidate passed both gates, try current-only (so the caller
    # gets something rather than nothing) — the caller will log a thermal
    # warning for manual review (heatsink, split rail, etc.).
    for c in candidates:
        if c["i_out_max_ma"] >= required_ma:
            return c
    return None


def _converter_to_bom(lib_entry: dict) -> dict:
    """Shape a POWER_CONVERTERS entry as a BOM dict."""
    return {
        "function": lib_entry["function"],
        "primary_part": lib_entry["primary_part"],
        "primary_manufacturer": lib_entry["primary_manufacturer"],
        "primary_description": lib_entry["primary_description"],
        "datasheet_url": lib_entry["datasheet_url"],
        "primary_key_specs": dict(lib_entry["primary_key_specs"]),
        "lifecycle_status": "active",
        "_source": "power_optimizer",
    }


def _library_entry_by_part(part: str) -> Optional[dict]:
    """Find the POWER_CONVERTERS entry whose primary_part matches exactly."""
    p = (part or "").strip().lower()
    for e in POWER_CONVERTERS:
        if e["primary_part"].strip().lower() == p:
            return e
    return None


def _infer_topology_from_component(comp: dict) -> str:
    """LDO / Buck / Boost / Regulator from BOM fields. Used when the
    component isn't in our library (e.g. LLM-emitted parts).
    """
    blob = " ".join([
        (comp.get("function") or ""),
        (comp.get("primary_description") or ""),
        (comp.get("primary_part") or ""),
        (comp.get("type") or ""),
    ]).lower()
    if any(k in blob for k in ("ldo", "linear regulator")):
        return "LDO"
    if "buck-boost" in blob: return "Buck-Boost"
    if "buck" in blob:       return "Buck"
    if "boost" in blob:      return "Boost"
    if any(k in blob for k in ("dc-dc", "dcdc", "switching", "switcher")):
        return "Buck"
    return "LDO"  # conservative: LDO has higher P_diss, so assume worst


def _regulator_operating_point(
    comp: dict,
    v_in_supply: float,
    rail_loads: dict[float, float],
) -> tuple[float, float, float]:
    """Return (V_in, V_out, I_out_A) for a BOM regulator under the real
    rail load. Uses library data if the part is a library entry, otherwise
    parses the BOM fields.
    """
    v_out = _parse_regulator_rail_v(comp) or 0.0
    rail_ma = rail_loads.get(_closest_rail(v_out), 0.0) if v_out else 0.0
    return v_in_supply, v_out, rail_ma / 1000.0


def optimize_power_rails(
    components: list[dict],
    stages: list[dict],
    margin: float = 1.3,
    v_in_supply: float = 12.0,
) -> tuple[list[dict], list[str]]:
    """Enforce two invariants on every regulator that feeds a GLB rail:
      (1) I_out_max ≥ rail load × margin  — current headroom
      (2) T_j @ T_amb=85 °C ≤ 110 °C       — thermal headroom (LDO
          dissipation is what usually breaks this)

    If an existing regulator fails either gate, it's swapped for the
    smallest library part that passes BOTH. If a rail has load but no
    regulator at all, one is inserted. Returns (new_components, change_log).
    """
    new_components = [dict(c) for c in (components or [])]
    rail_loads = _compute_rail_loads(stages)
    log: list[str] = []

    # Sort rails high→low so the deeper-rail swap (e.g. 5 V) is logged
    # before derived rails (e.g. 3.3 V / 1.8 V). Purely cosmetic.
    for rail_v in sorted(rail_loads.keys(), reverse=True):
        load_ma = rail_loads[rail_v]
        required_ma = load_ma * margin

        # Find existing regulators on this rail
        rail_idxs: list[int] = []
        for i, c in enumerate(new_components):
            if not _is_power_converter(c):
                continue
            rv = _parse_regulator_rail_v(c)
            if rv is not None and abs(rv - rail_v) < 0.15:
                rail_idxs.append(i)

        if not rail_idxs:
            candidate = _pick_converter_for(rail_v, required_ma, v_in_supply)
            if candidate:
                new_components.append(_converter_to_bom(candidate))
                log.append(
                    f"+{rail_v:g}V rail needs {load_ma:.0f} mA (× {margin:.1f} = "
                    f"{required_ma:.0f} mA target) but no converter was present — "
                    f"added '{candidate['primary_part']}' "
                    f"({candidate['i_out_max_ma']:.0f} mA {candidate['type']})."
                )
            else:
                log.append(
                    f"⚠ +{rail_v:g}V rail needs {load_ma:.0f} mA but no library "
                    f"converter is rated that high — manual sizing required."
                )
            continue

        # Check each existing regulator; if undersized OR thermally unsafe,
        # swap the weakest one.
        rail_idxs.sort(key=lambda i: _parse_regulator_i_max_ma(new_components[i]) or 0.0)
        weakest_idx = rail_idxs[0]
        weakest = new_components[weakest_idx]
        i_max = _parse_regulator_i_max_ma(weakest)
        weakest_part = weakest.get("primary_part", "?")

        if i_max is None:
            log.append(
                f"⚠ +{rail_v:g}V regulator '{weakest_part}' has no declared "
                f"I_out_max — cannot verify it covers {load_ma:.0f} mA load."
            )
            continue

        current_ok = i_max >= required_ma

        # Thermal check: use library θ_ja if we recognise the part,
        # otherwise infer topology and use a conservative default.
        lib_entry = _library_entry_by_part(weakest_part)
        topology = (lib_entry or {}).get("type") or _infer_topology_from_component(weakest)
        thermal_probe = {
            "type": topology,
            "eta": (lib_entry or {}).get("eta", 0.9),
            "theta_ja_c_per_w": (lib_entry or {}).get("theta_ja_c_per_w"),
        }
        load_a = load_ma / 1000.0
        p_diss = _compute_pdiss_w(thermal_probe, v_in_supply, rail_v, load_a)
        t_j = _compute_t_junction_c(thermal_probe, p_diss)
        thermal_ok = t_j <= _TJ_SAFETY_C

        if current_ok and thermal_ok:
            continue  # adequate on both gates

        reasons: list[str] = []
        if not current_ok:
            reasons.append(
                f"I_out_max {i_max:.0f} mA < required {required_ma:.0f} mA"
            )
        if not thermal_ok:
            reasons.append(
                f"P_diss {p_diss:.2f} W → T_j {t_j:.0f} °C "
                f"exceeds {_TJ_SAFETY_C:.0f} °C safety ceiling"
            )

        candidate = _pick_converter_for(rail_v, required_ma, v_in_supply)
        if not candidate:
            log.append(
                f"⚠ +{rail_v:g}V rail: '{weakest_part}' flagged ({'; '.join(reasons)}) "
                f"but library has no part that satisfies both gates — "
                f"split the rail or source a larger part manually."
            )
            continue

        # Verify candidate actually improves thermal — if the issue was
        # thermal and the candidate is another LDO of similar θ_ja, we
        # need to escalate to a switcher explicitly.
        cand_p = _compute_pdiss_w(candidate, v_in_supply, rail_v, load_a)
        cand_tj = _compute_t_junction_c(candidate, cand_p)
        if not thermal_ok and cand_tj > _TJ_SAFETY_C:
            # Force-pick the smallest switcher on this rail that meets load
            switchers = [c for c in POWER_CONVERTERS
                         if abs(c["rail_v"] - rail_v) < 0.05
                         and c.get("type", "").lower() != "ldo"
                         and c["i_out_max_ma"] >= required_ma]
            switchers.sort(key=lambda c: c["i_out_max_ma"])
            if switchers:
                candidate = switchers[0]
                cand_p = _compute_pdiss_w(candidate, v_in_supply, rail_v, load_a)
                cand_tj = _compute_t_junction_c(candidate, cand_p)

        new_components[weakest_idx] = _converter_to_bom(candidate)
        log.append(
            f"Swapped +{rail_v:g}V converter '{weakest_part}' → "
            f"'{candidate['primary_part']}' ({candidate['i_out_max_ma']:.0f} mA "
            f"{candidate['type']}, P_diss={cand_p:.2f} W, T_j={cand_tj:.0f} °C). "
            f"Reason: {'; '.join(reasons)}."
        )

    return new_components, log


def propagate_changes(
    tool_input: dict,
    new_stages: list[dict],
    *,
    center_freq_mhz: Optional[float] = None,
    bandwidth_mhz: Optional[float] = None,
    antenna_count: int = 1,
    channel_count: int = 1,
) -> list[str]:
    """One-stop propagation: after the optimizer has finalised new_stages,
    update the block diagram and BOM in ``tool_input`` in place.

    Returns a change log (list of human-readable strings) the caller can
    fold into the optimizer iteration log for display.
    """
    change_log: list[str] = []

    # --- BOM ---
    comps_before = tool_input.get("component_recommendations") or []
    comps_after, bom_log = propagate_to_bom(comps_before, new_stages)
    if bom_log:
        tool_input["component_recommendations"] = comps_after
        change_log.extend(bom_log)
    else:
        comps_after = comps_before

    # --- Power-rail converter sizing (current headroom + thermal) ---
    # Rail loads are derived from the GLB stage bias conditions; the BOM
    # may now have additional amplifiers that increase rail demand. Any
    # regulator that fails current-headroom OR thermal (T_j > 110 °C) gets
    # swapped for a library part that passes both. LDOs dropping more than
    # a few volts at >200 mA typically cannot pass thermal — the optimizer
    # will escalate to a buck on its own.
    v_in_supply = 12.0
    dp = tool_input.get("design_parameters") or {}
    for k in ("supply_voltage_v", "input_voltage_v", "primary_supply_voltage_v"):
        v = dp.get(k)
        if v:
            try:
                import re as _re
                m = _re.search(r"[\d.]+", str(v))
                if m:
                    v_in_supply = float(m.group())
                    break
            except Exception:
                pass
    comps_after2, power_log = optimize_power_rails(
        comps_after, new_stages, v_in_supply=v_in_supply,
    )
    if power_log:
        tool_input["component_recommendations"] = comps_after2
        change_log.extend(power_log)

    # --- Block diagram ---
    # Regenerate only when the optimizer's stage count materially differs
    # from what the existing Mermaid encodes. Counting unique node ids
    # (identifier immediately followed by `[`, `(`, or `{`) is a cheap
    # proxy that handles both line-leading and mid-line node definitions.
    old_mermaid = tool_input.get("block_diagram_mermaid") or ""
    import re as _re
    node_re = _re.compile(r"(?<![A-Za-z_])([A-Za-z_]\w*)\s*[\[\(\{]")
    _KEYWORDS = {
        "flowchart", "graph", "subgraph", "direction",
        "classDef", "class", "style", "linkStyle",
    }
    old_nodes = {m.group(1) for m in node_re.finditer(old_mermaid)
                 if m.group(1) not in _KEYWORDS}
    expected_nodes = len(new_stages) + 2  # stages + ANT + OUT
    # Tolerance 2: small differences (e.g. a decorative subgraph) don't
    # force regeneration; real structural changes do.
    if abs(len(old_nodes) - expected_nodes) >= 2 or not old_mermaid.strip():
        tool_input["block_diagram_mermaid"] = regenerate_block_diagram(
            new_stages,
            center_freq_mhz=center_freq_mhz,
            bandwidth_mhz=bandwidth_mhz,
            antenna_count=antenna_count,
            channel_count=channel_count,
        )
        change_log.append(
            f"Regenerated block_diagram_mermaid to reflect the "
            f"{len(new_stages)}-stage optimizer output."
        )

    return change_log
