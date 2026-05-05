"""
RF Cascade Validator — B1.1 (Workstream B / AI-ML).

Given a list of RF stages (LNA -> filter -> mixer -> IF amp -> ADC), compute the
full cascade: noise figure (Friis), total gain, input-referred IIP3, input-referred
P1dB, thermal sensitivity, and SFDR. Flag any error / warning per industry rules
of thumb so the red-team auditor and the P1 agent can reason numerically before
locking requirements.

This module is the single source of truth for cascade math in the pipeline.
Every LLM-facing tool call that claims "system NF = X dB" MUST cite a
CascadeReport produced here. That is the hallucination fence.

Conventions
-----------
- All noise figures are in dB (NF). Noise factors (F) are linear, F = 10**(NF/10).
- All gains are in dB.
- All IIP3 / P1dB values are dBm, input-referred to the *stage input* unless noted.
- Temperatures in Celsius. Reference thermal noise kT at 290 K = -174 dBm/Hz.
- Passive (loss) stages: enter gain_db as a negative number (e.g. filter IL 3 dB ->
  gain_db = -3, nf_db = 3). For an ideal passive at ambient, NF (dB) == IL (dB).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KT_DBM_PER_HZ_AT_290K = -174.0  # thermal noise floor at 290 K


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class Stage:
    """One stage in the RF signal chain."""

    name: str
    gain_db: float
    nf_db: float
    iip3_dbm: Optional[float] = None   # input-referred IIP3; None = not specified
    p1db_dbm: Optional[float] = None   # input-referred P1dB; None = not specified
    # Optional temperature-coefficient hints (dB per deg-C); used for derating.
    nf_tc_db_per_c: float = 0.0
    gain_tc_db_per_c: float = 0.0
    # Free-form tag ("LNA", "mixer", "filter", "adc"...). Used by rules engine.
    kind: str = ""

    def derated(self, temperature_c: float) -> "Stage":
        """Return a copy of this stage with NF/gain linearly derated to the target temp."""
        dt = temperature_c - 25.0
        return Stage(
            name=self.name,
            gain_db=self.gain_db + self.gain_tc_db_per_c * dt,
            nf_db=self.nf_db + self.nf_tc_db_per_c * dt,
            iip3_dbm=self.iip3_dbm,
            p1db_dbm=self.p1db_dbm,
            nf_tc_db_per_c=self.nf_tc_db_per_c,
            gain_tc_db_per_c=self.gain_tc_db_per_c,
            kind=self.kind,
        )


@dataclass
class CascadeReport:
    """Output of validate_cascade()."""

    total_gain_db: float
    noise_figure_db: float
    iip3_dbm_input: Optional[float]
    p1db_dbm_input: Optional[float]
    sensitivity_dbm: Optional[float]       # MDS for the configured BW and required SNR
    sfdr_db: Optional[float]               # 2-tone SFDR estimate
    noise_floor_out_dbm: float
    temperature_c: float
    bandwidth_hz: float
    snr_required_db: float
    per_stage_cumulative_nf_db: list[float] = field(default_factory=list)
    per_stage_cumulative_gain_db: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    passed: bool = True

    def to_dict(self) -> dict:
        return {
            "total_gain_db": round(self.total_gain_db, 2),
            "noise_figure_db": round(self.noise_figure_db, 2),
            "iip3_dbm_input": None if self.iip3_dbm_input is None else round(self.iip3_dbm_input, 2),
            "p1db_dbm_input": None if self.p1db_dbm_input is None else round(self.p1db_dbm_input, 2),
            "sensitivity_dbm": None if self.sensitivity_dbm is None else round(self.sensitivity_dbm, 2),
            "sfdr_db": None if self.sfdr_db is None else round(self.sfdr_db, 2),
            "noise_floor_out_dbm": round(self.noise_floor_out_dbm, 2),
            "temperature_c": self.temperature_c,
            "bandwidth_hz": self.bandwidth_hz,
            "snr_required_db": self.snr_required_db,
            "per_stage_cumulative_nf_db": [round(x, 2) for x in self.per_stage_cumulative_nf_db],
            "per_stage_cumulative_gain_db": [round(x, 2) for x in self.per_stage_cumulative_gain_db],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "passed": self.passed,
        }


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

def db_to_lin(x_db: float) -> float:
    return 10.0 ** (x_db / 10.0)


def lin_to_db(x: float) -> float:
    if x <= 0:
        return float("-inf")
    return 10.0 * math.log10(x)


# ---------------------------------------------------------------------------
# Core cascade math
# ---------------------------------------------------------------------------

def cascade_nf_db(stages: list[Stage]) -> tuple[float, list[float]]:
    """
    Friis formula. Returns (total NF dB, cumulative NF after each stage in dB).

    F_total = F1 + (F2 - 1)/G1 + (F3 - 1)/(G1*G2) + ...
    """
    if not stages:
        return 0.0, []

    f_total = db_to_lin(stages[0].nf_db)
    g_running = db_to_lin(stages[0].gain_db)
    per_stage = [lin_to_db(f_total)]

    for stage in stages[1:]:
        f_i = db_to_lin(stage.nf_db)
        f_total += (f_i - 1.0) / g_running
        g_running *= db_to_lin(stage.gain_db)
        per_stage.append(lin_to_db(f_total))

    return lin_to_db(f_total), per_stage


def cascade_gain_db(stages: list[Stage]) -> tuple[float, list[float]]:
    """Total gain in dB + cumulative gain after each stage."""
    total = 0.0
    cumulative: list[float] = []
    for s in stages:
        total += s.gain_db
        cumulative.append(total)
    return total, cumulative


def cascade_iip3_input_dbm(stages: list[Stage]) -> Optional[float]:
    """
    Input-referred IIP3 for the full cascade.

    1/IIP3_total = sum_i (G_pre_i / IIP3_i)    (all linear, powers in mW)

    where G_pre_i is the linear gain *before* stage i (G_pre_0 = 1).
    Any stage with IIP3 = None is treated as infinite (non-limiting).
    """
    have_any = any(s.iip3_dbm is not None for s in stages)
    if not have_any:
        return None

    g_pre = 1.0  # linear
    inv_iip3_sum = 0.0
    for s in stages:
        if s.iip3_dbm is not None:
            iip3_lin = db_to_lin(s.iip3_dbm)  # in mW
            inv_iip3_sum += g_pre / iip3_lin
        g_pre *= db_to_lin(s.gain_db)

    if inv_iip3_sum <= 0:
        return None
    return lin_to_db(1.0 / inv_iip3_sum)


def cascade_p1db_input_dbm(stages: list[Stage]) -> Optional[float]:
    """
    Input-referred cascade P1dB using the same harmonic-sum approximation as IIP3
    (valid when stages are weakly saturating and roughly independent):

        1/P1dB_total = sum_i (G_pre_i / P1dB_i)

    This is an approximation (true P1dB cascade is non-analytic) but gives a
    conservative lower bound, which is what we want for specification validation.
    """
    have_any = any(s.p1db_dbm is not None for s in stages)
    if not have_any:
        return None

    g_pre = 1.0
    inv_sum = 0.0
    for s in stages:
        if s.p1db_dbm is not None:
            p_lin = db_to_lin(s.p1db_dbm)
            inv_sum += g_pre / p_lin
        g_pre *= db_to_lin(s.gain_db)

    if inv_sum <= 0:
        return None
    return lin_to_db(1.0 / inv_sum)


def thermal_noise_floor_dbm(bandwidth_hz: float, nf_db: float, temperature_c: float = 25.0) -> float:
    """
    Input-referred noise floor:  kT + 10log10(BW) + NF
    Temperature correction: kT scales linearly with absolute temperature.
    """
    t_kelvin = temperature_c + 273.15
    kt_dbm_per_hz = KT_DBM_PER_HZ_AT_290K + 10.0 * math.log10(t_kelvin / 290.0)
    return kt_dbm_per_hz + 10.0 * math.log10(max(bandwidth_hz, 1.0)) + nf_db


def sensitivity_dbm(bandwidth_hz: float, nf_db: float, snr_required_db: float,
                    temperature_c: float = 25.0) -> float:
    """Minimum detectable signal = noise floor + required SNR."""
    return thermal_noise_floor_dbm(bandwidth_hz, nf_db, temperature_c) + snr_required_db


def sfdr_db(iip3_input_dbm: Optional[float], noise_floor_in_dbm: float) -> Optional[float]:
    """
    Two-tone SFDR (spurious-free dynamic range):
        SFDR = (2/3) * (IIP3 - noise_floor)
    """
    if iip3_input_dbm is None:
        return None
    return (2.0 / 3.0) * (iip3_input_dbm - noise_floor_in_dbm)


# ---------------------------------------------------------------------------
# Rules engine — sanity checks typical defense-RF designs must satisfy
# ---------------------------------------------------------------------------

def _run_rules(stages: list[Stage], report: CascadeReport,
               target_nf_db: Optional[float],
               target_sensitivity_dbm: Optional[float],
               target_sfdr_db: Optional[float]) -> None:
    """Populate report.warnings / report.errors in-place."""

    # R1 — First stage should be an LNA (low NF, positive gain).
    if stages:
        s0 = stages[0]
        if s0.nf_db > 3.0:
            report.warnings.append(
                f"First stage '{s0.name}' has NF={s0.nf_db} dB (>3). "
                "Consider an LNA in front to set system noise figure."
            )
        if s0.gain_db <= 0:
            report.warnings.append(
                f"First stage '{s0.name}' has non-positive gain ({s0.gain_db} dB). "
                "Passive first stage directly raises system NF by its insertion loss."
            )

    # R2 — Gain compression check: no single stage should be driven above its P1dB
    #       when the full chain delivers max expected input (caller decides max).
    # (Left as a warning hook; generate_requirements supplies max_input_dbm.)

    # R3 — Target NF.
    if target_nf_db is not None and report.noise_figure_db > target_nf_db + 0.5:
        report.errors.append(
            f"Cascade NF {report.noise_figure_db:.2f} dB exceeds target "
            f"{target_nf_db:.2f} dB (+0.5 dB tolerance)."
        )

    # R4 — Target sensitivity.
    if target_sensitivity_dbm is not None and report.sensitivity_dbm is not None:
        if report.sensitivity_dbm > target_sensitivity_dbm + 1.0:
            report.errors.append(
                f"Cascade sensitivity {report.sensitivity_dbm:.2f} dBm is worse "
                f"than target {target_sensitivity_dbm:.2f} dBm (+1 dB tolerance)."
            )

    # R5 — Target SFDR.
    if target_sfdr_db is not None and report.sfdr_db is not None:
        if report.sfdr_db < target_sfdr_db - 1.0:
            report.errors.append(
                f"Cascade SFDR {report.sfdr_db:.2f} dB is below target "
                f"{target_sfdr_db:.2f} dB (-1 dB tolerance)."
            )

    # R6 — Total gain sanity (too little or too much).
    if report.total_gain_db < 20 and stages:
        report.warnings.append(
            f"Total cascade gain {report.total_gain_db:.1f} dB is low for a "
            "receiver (typical 40-80 dB to fill ADC dynamic range)."
        )
    if report.total_gain_db > 100:
        report.warnings.append(
            f"Total cascade gain {report.total_gain_db:.1f} dB is very high; "
            "consider distributed gain with AGC to manage blocker power."
        )

    # R7 — Any IIP3 is missing on an active (gain > 0) stage.
    missing_iip3 = [s.name for s in stages if s.gain_db > 0 and s.iip3_dbm is None
                    and s.kind.lower() not in {"filter", "adc", "limiter"}]
    if missing_iip3:
        report.warnings.append(
            "IIP3 not specified for active stage(s): " + ", ".join(missing_iip3) +
            ". Cascade IIP3 / SFDR estimate may be optimistic."
        )

    report.passed = len(report.errors) == 0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def validate_cascade(
    stages: list[Stage],
    bandwidth_hz: float = 1_000_000.0,
    snr_required_db: float = 10.0,
    temperature_c: float = 25.0,
    target_nf_db: Optional[float] = None,
    target_sensitivity_dbm: Optional[float] = None,
    target_sfdr_db: Optional[float] = None,
) -> CascadeReport:
    """
    Validate a proposed RF signal chain against optional targets.

    Parameters
    ----------
    stages:
        Ordered list of Stage objects, signal-flow order (antenna -> detector).
    bandwidth_hz:
        Noise bandwidth used for sensitivity / noise-floor calculations.
    snr_required_db:
        SNR required at detection point (depends on modulation / POI / BER).
    temperature_c:
        Worst-case operating temperature for NF/gain derating.
    target_nf_db, target_sensitivity_dbm, target_sfdr_db:
        Optional specification targets. If provided, rules engine flags errors.

    Returns
    -------
    CascadeReport
    """
    derated_stages = [s.derated(temperature_c) for s in stages]

    total_gain_db, cum_gain = cascade_gain_db(derated_stages)
    nf_db, cum_nf = cascade_nf_db(derated_stages)
    iip3_in = cascade_iip3_input_dbm(derated_stages)
    p1db_in = cascade_p1db_input_dbm(derated_stages)

    noise_floor_in = thermal_noise_floor_dbm(bandwidth_hz, nf_db, temperature_c)
    noise_floor_out = noise_floor_in + total_gain_db
    sens = noise_floor_in + snr_required_db
    sfdr = sfdr_db(iip3_in, noise_floor_in)

    report = CascadeReport(
        total_gain_db=total_gain_db,
        noise_figure_db=nf_db,
        iip3_dbm_input=iip3_in,
        p1db_dbm_input=p1db_in,
        sensitivity_dbm=sens,
        sfdr_db=sfdr,
        noise_floor_out_dbm=noise_floor_out,
        temperature_c=temperature_c,
        bandwidth_hz=bandwidth_hz,
        snr_required_db=snr_required_db,
        per_stage_cumulative_nf_db=cum_nf,
        per_stage_cumulative_gain_db=cum_gain,
    )

    _run_rules(
        stages=derated_stages,
        report=report,
        target_nf_db=target_nf_db,
        target_sensitivity_dbm=target_sensitivity_dbm,
        target_sfdr_db=target_sfdr_db,
    )

    return report


# ---------------------------------------------------------------------------
# Convenience wrapper — build from dict (e.g. from LLM tool call)
# ---------------------------------------------------------------------------

def validate_cascade_from_dicts(
    stages: list[dict],
    bandwidth_hz: float = 1_000_000.0,
    snr_required_db: float = 10.0,
    temperature_c: float = 25.0,
    target_nf_db: Optional[float] = None,
    target_sensitivity_dbm: Optional[float] = None,
    target_sfdr_db: Optional[float] = None,
) -> CascadeReport:
    """Like validate_cascade but accepts a list of plain dicts (for LLM tool-call friendliness)."""
    stage_objs: list[Stage] = []
    for d in stages:
        stage_objs.append(Stage(
            name=d.get("name", "stage"),
            gain_db=float(d["gain_db"]),
            nf_db=float(d["nf_db"]),
            iip3_dbm=None if d.get("iip3_dbm") is None else float(d["iip3_dbm"]),
            p1db_dbm=None if d.get("p1db_dbm") is None else float(d["p1db_dbm"]),
            nf_tc_db_per_c=float(d.get("nf_tc_db_per_c", 0.0)),
            gain_tc_db_per_c=float(d.get("gain_tc_db_per_c", 0.0)),
            kind=str(d.get("kind", "")),
        ))
    return validate_cascade(
        stages=stage_objs,
        bandwidth_hz=bandwidth_hz,
        snr_required_db=snr_required_db,
        temperature_c=temperature_c,
        target_nf_db=target_nf_db,
        target_sensitivity_dbm=target_sensitivity_dbm,
        target_sfdr_db=target_sfdr_db,
    )
