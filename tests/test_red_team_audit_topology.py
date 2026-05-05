"""P2.9 — red_team_audit now runs topology checks when a diagram + arch pass in.

The rules themselves are covered by tests/tools/test_block_diagram_validator.py;
here we only prove the wiring inside `audit()` works.
"""
from __future__ import annotations

from agents.red_team_audit import audit


_MINIMAL_BOM = [
    {"name": "LNA", "gain_db": 24.0, "nf_db": 1.8, "iip3_dbm": 30.0,
     "p1db_dbm": 18.0, "kind": "LNA"},
]


def _run(mermaid, architecture, *, claimed_cascade=None, parts=None):
    return audit(
        phase_id="P1",
        bom_stages=_MINIMAL_BOM,
        claimed_cascade=claimed_cascade or {"total_gain_db": 24.0, "noise_figure_db": 1.8},
        citations=[("MIL-STD-461G", "RE102")],
        claimed_parts=parts or [],
        block_diagram_mermaid=mermaid,
        architecture=architecture,
    )


def test_audit_flags_missing_mixer_when_mermaid_passed_in():
    rep = _run(
        mermaid="flowchart TD\n ANT[Antenna] --> LNA[LNA]\n LNA --> ADC[ADC]",
        architecture="superhet_single",
    )
    topo_issues = [i for i in rep.issues if i.category == "topology"]
    assert topo_issues, "topology checks should have fired"
    assert any("mixer" in i.detail.lower() for i in topo_issues)
    assert rep.overall_pass is False  # critical topology issue → fail


def test_audit_skips_topology_when_mermaid_not_passed():
    rep = _run(mermaid=None, architecture="superhet_single")
    assert not any(i.category == "topology" for i in rep.issues)


def test_audit_topology_clean_superhet_passes():
    mermaid = (
        "flowchart TD\n"
        " ANT[Antenna] --> BPF[Preselector]\n"
        " BPF --> LNA[LNA]\n"
        " LNA --> MIX[Mixer]\n"
        " LO[Synthesizer] --> MIX\n"
        " MIX --> IF[IF Filter]\n"
    )
    rep = _run(mermaid=mermaid, architecture="superhet_single")
    topo = [i for i in rep.issues if i.category == "topology"]
    assert topo == []


def test_audit_topology_respects_architecture_choice():
    mermaid = (
        "flowchart TD\n"
        " ANT[Antenna] --> LNA[LNA]\n"
        " LNA --> ADC[ADC]\n"
        " CLK[Sample Clock] --> ADC\n"
    )
    # Under direct_rf_sample, this is fine. Under superhet_single, mixer+LO missing.
    rep_drf = _run(mermaid=mermaid, architecture="direct_rf_sample")
    rep_sh  = _run(mermaid=mermaid, architecture="superhet_single")

    drf_topo = [i for i in rep_drf.issues if i.category == "topology"]
    sh_topo  = [i for i in rep_sh.issues  if i.category == "topology"]
    assert drf_topo == []
    assert any("mixer" in i.detail.lower() for i in sh_topo)
