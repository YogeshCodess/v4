"""
Golden-value tests for tools/cascade_validator.py.

These values are taken from standard RF textbook examples (Pozar; Razavi) so
any math regression is caught immediately. When any assertion below fails,
DO NOT weaken the tolerance — the implementation is wrong.
"""
from __future__ import annotations

import math

import pytest

from tools.cascade_validator import (
    Stage,
    cascade_gain_db,
    cascade_iip3_input_dbm,
    cascade_nf_db,
    cascade_p1db_input_dbm,
    sensitivity_dbm,
    sfdr_db,
    thermal_noise_floor_dbm,
    validate_cascade,
    validate_cascade_from_dicts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _close(a: float, b: float, tol: float = 0.05) -> bool:
    return math.isclose(a, b, abs_tol=tol)


# ---------------------------------------------------------------------------
# Noise figure — Friis cascade, textbook canonical example
#   Stage 1: LNA     NF = 1 dB, G = 15 dB
#   Stage 2: Mixer   NF = 6 dB, G = -7 dB (conversion loss)
#   Stage 3: IF amp  NF = 4 dB, G = 30 dB
# Expected: NF_total ~ 2.02 dB
# ---------------------------------------------------------------------------

def test_friis_textbook_example():
    stages = [
        Stage(name="LNA", gain_db=15.0, nf_db=1.0, kind="LNA"),
        Stage(name="Mixer", gain_db=-7.0, nf_db=6.0, kind="mixer"),
        Stage(name="IF_Amp", gain_db=30.0, nf_db=4.0, kind="amp"),
    ]
    nf, cum = cascade_nf_db(stages)
    assert _close(nf, 2.02, tol=0.05), f"expected ~2.02 dB, got {nf}"
    # Sanity: first stage cumulative NF == stage 1 NF.
    assert _close(cum[0], 1.0)
    assert len(cum) == 3


def test_single_stage_nf_equals_stage_nf():
    stages = [Stage(name="only", gain_db=20.0, nf_db=3.5)]
    nf, _ = cascade_nf_db(stages)
    assert _close(nf, 3.5, tol=1e-6)


def test_empty_cascade_returns_zero_nf():
    nf, cum = cascade_nf_db([])
    assert nf == 0.0
    assert cum == []


def test_lna_dominates_noise():
    """Classic rule: LNA NF + loss roughly sets system NF when LNA gain is high."""
    stages = [
        Stage(name="LNA", gain_db=25.0, nf_db=1.2, kind="LNA"),
        Stage(name="Filter", gain_db=-2.0, nf_db=2.0, kind="filter"),
        Stage(name="Mixer", gain_db=-8.0, nf_db=8.0, kind="mixer"),
    ]
    nf, _ = cascade_nf_db(stages)
    # With 25 dB of LNA gain in front, downstream contributions get attenuated by
    # ~316x. Expect system NF very close to LNA NF.
    assert 1.2 < nf < 1.6, f"NF {nf} dB — later stages dominating, LNA gain too low?"


# ---------------------------------------------------------------------------
# Gain cascade
# ---------------------------------------------------------------------------

def test_gain_cascade_adds_db():
    stages = [
        Stage(name="a", gain_db=10.0, nf_db=1.0),
        Stage(name="b", gain_db=-5.0, nf_db=5.0),
        Stage(name="c", gain_db=25.0, nf_db=3.0),
    ]
    total, cum = cascade_gain_db(stages)
    assert _close(total, 30.0, tol=1e-9)
    assert cum == [10.0, 5.0, 30.0]


# ---------------------------------------------------------------------------
# IIP3 cascade
#   Stage 1: amp IIP3 = +10 dBm, G = 20 dB
#   Stage 2: mixer IIP3 = +5 dBm
# Expected input-referred IIP3 ~ -15.02 dBm  (mixer dominates because of upstream gain)
# ---------------------------------------------------------------------------

def test_iip3_downstream_dominance():
    stages = [
        Stage(name="amp", gain_db=20.0, nf_db=2.0, iip3_dbm=10.0),
        Stage(name="mixer", gain_db=-7.0, nf_db=7.0, iip3_dbm=5.0, kind="mixer"),
    ]
    iip3 = cascade_iip3_input_dbm(stages)
    assert iip3 is not None
    assert _close(iip3, -15.0, tol=0.1), f"expected ~-15 dBm, got {iip3}"


def test_iip3_all_missing_returns_none():
    stages = [
        Stage(name="a", gain_db=10.0, nf_db=1.0),
        Stage(name="b", gain_db=20.0, nf_db=3.0),
    ]
    assert cascade_iip3_input_dbm(stages) is None


def test_iip3_single_stage_no_gain_ahead():
    stages = [Stage(name="only", gain_db=10.0, nf_db=1.0, iip3_dbm=20.0)]
    iip3 = cascade_iip3_input_dbm(stages)
    assert iip3 is not None
    assert _close(iip3, 20.0, tol=1e-6)


# ---------------------------------------------------------------------------
# P1dB cascade — same math-form as IIP3 (conservative estimate)
# ---------------------------------------------------------------------------

def test_p1db_cascade_is_conservative_lower_bound():
    stages = [
        Stage(name="amp", gain_db=20.0, nf_db=2.0, p1db_dbm=20.0),
        Stage(name="mixer", gain_db=-5.0, nf_db=5.0, p1db_dbm=10.0),
    ]
    p1db = cascade_p1db_input_dbm(stages)
    assert p1db is not None
    # Both stages contribute; result must be below the min referred value.
    assert p1db < 10.0


# ---------------------------------------------------------------------------
# Thermal noise floor
# ---------------------------------------------------------------------------

def test_thermal_noise_floor_1mhz_zero_nf():
    # kT(290K) = -174 dBm/Hz, +10log10(1e6) = +60, NF = 0 -> -114 dBm.
    n = thermal_noise_floor_dbm(bandwidth_hz=1_000_000, nf_db=0.0, temperature_c=(290.0 - 273.15))
    assert _close(n, -114.0, tol=0.05), f"expected -114 dBm, got {n}"


def test_thermal_noise_floor_with_nf_adds_linearly():
    n0 = thermal_noise_floor_dbm(1_000_000, 0.0, 25.0)
    n3 = thermal_noise_floor_dbm(1_000_000, 3.0, 25.0)
    assert _close(n3 - n0, 3.0, tol=1e-6)


def test_sensitivity_equals_noise_floor_plus_snr():
    sens = sensitivity_dbm(1_000_000, 3.0, 10.0, temperature_c=25.0)
    expected = thermal_noise_floor_dbm(1_000_000, 3.0, 25.0) + 10.0
    assert _close(sens, expected, tol=1e-6)


# ---------------------------------------------------------------------------
# SFDR
# ---------------------------------------------------------------------------

def test_sfdr_formula():
    """SFDR = (2/3) * (IIP3 - noise_floor)."""
    sf = sfdr_db(iip3_input_dbm=-10.0, noise_floor_in_dbm=-100.0)
    assert sf is not None
    assert _close(sf, 60.0, tol=1e-6)


# ---------------------------------------------------------------------------
# Full validate_cascade() + rules engine
# ---------------------------------------------------------------------------

def test_validate_cascade_happy_path():
    stages = [
        Stage(name="LNA", gain_db=20.0, nf_db=1.2, iip3_dbm=15.0, p1db_dbm=5.0, kind="LNA"),
        Stage(name="Filter", gain_db=-2.0, nf_db=2.0, kind="filter"),
        Stage(name="Mixer", gain_db=-7.0, nf_db=7.0, iip3_dbm=5.0, p1db_dbm=0.0, kind="mixer"),
        Stage(name="IF_Amp", gain_db=25.0, nf_db=3.0, iip3_dbm=20.0, p1db_dbm=10.0, kind="amp"),
    ]
    rep = validate_cascade(stages, bandwidth_hz=1_000_000, snr_required_db=10.0)
    # Total gain = 20 - 2 - 7 + 25 = 36 dB.
    assert _close(rep.total_gain_db, 36.0)
    assert rep.noise_figure_db < 3.0
    assert rep.iip3_dbm_input is not None
    assert rep.p1db_dbm_input is not None
    assert rep.sensitivity_dbm is not None
    assert rep.sfdr_db is not None
    assert rep.passed is True


def test_validate_cascade_fails_nf_target():
    stages = [
        # Deliberately bad: lossy first stage raises system NF.
        Stage(name="Filter", gain_db=-3.0, nf_db=3.0, kind="filter"),
        Stage(name="LNA", gain_db=15.0, nf_db=1.5, iip3_dbm=10.0, kind="LNA"),
        Stage(name="Mixer", gain_db=-7.0, nf_db=7.0, iip3_dbm=5.0, kind="mixer"),
    ]
    rep = validate_cascade(stages, target_nf_db=1.5)
    assert rep.passed is False
    assert any("NF" in e for e in rep.errors)
    # Should also warn about non-LNA first stage.
    assert any("first stage" in w.lower() or "first stage" in w for w in rep.warnings)


def test_validate_cascade_flags_missing_iip3():
    stages = [
        Stage(name="LNA", gain_db=20.0, nf_db=1.2, kind="LNA"),       # no IIP3
        Stage(name="IF_Amp", gain_db=25.0, nf_db=3.0, kind="amp"),    # no IIP3
    ]
    rep = validate_cascade(stages)
    assert any("IIP3 not specified" in w for w in rep.warnings)


def test_validate_cascade_low_gain_warning():
    stages = [Stage(name="LNA", gain_db=8.0, nf_db=1.0, iip3_dbm=10.0, kind="LNA")]
    rep = validate_cascade(stages)
    assert any("gain" in w.lower() for w in rep.warnings)


def test_validate_cascade_from_dicts_smoke():
    rep = validate_cascade_from_dicts(
        stages=[
            {"name": "LNA", "gain_db": 20, "nf_db": 1.2, "iip3_dbm": 15, "p1db_dbm": 5, "kind": "LNA"},
            {"name": "Mixer", "gain_db": -7, "nf_db": 7, "iip3_dbm": 5, "kind": "mixer"},
            {"name": "IF_Amp", "gain_db": 25, "nf_db": 3, "iip3_dbm": 20, "kind": "amp"},
        ],
        bandwidth_hz=2_000_000,
        snr_required_db=12.0,
    )
    d = rep.to_dict()
    assert "noise_figure_db" in d
    assert "iip3_dbm_input" in d
    assert d["bandwidth_hz"] == 2_000_000


# ---------------------------------------------------------------------------
# Temperature derating
# ---------------------------------------------------------------------------

def test_derating_raises_nf_at_high_temperature():
    stages = [
        Stage(name="LNA", gain_db=20.0, nf_db=1.0, nf_tc_db_per_c=0.01, kind="LNA"),
        Stage(name="Mixer", gain_db=-7.0, nf_db=7.0, kind="mixer"),
    ]
    rep25 = validate_cascade(stages, temperature_c=25.0)
    rep85 = validate_cascade(stages, temperature_c=85.0)
    # 60 C rise, 0.01 dB/C on LNA -> LNA NF +0.6 dB -> system NF rises.
    assert rep85.noise_figure_db > rep25.noise_figure_db


# ---------------------------------------------------------------------------
# Property-ish sanity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nf_db", [0.5, 1.0, 2.0, 4.0, 8.0])
def test_nf_monotonic_in_first_stage(nf_db):
    stages = [
        Stage(name="LNA", gain_db=20.0, nf_db=nf_db, kind="LNA"),
        Stage(name="Mixer", gain_db=-7.0, nf_db=7.0, kind="mixer"),
    ]
    nf, _ = cascade_nf_db(stages)
    # System NF >= first-stage NF always (cannot be improved by later stages).
    assert nf >= nf_db - 1e-6
