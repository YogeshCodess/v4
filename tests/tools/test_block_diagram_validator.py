"""Tests for tools/block_diagram_validator.py — P0.1.

Covers the three categories the RF review flagged:
  1. Downconversion variants need mixer + LO (+ two mixers for superhet_double)
  2. Digital back-end variants need ADC + clock
  3. Front-end-only variants must NOT contain mixer / ADC
Plus LNA-presence, preselector ordering, and the parser itself.
"""
from __future__ import annotations

import pytest

from tools.block_diagram_validator import (
    ParsedDiagram,
    Violation,
    format_violations,
    parse_mermaid,
    validate,
)


# ---------------------------------------------------------------------------
# parse_mermaid
# ---------------------------------------------------------------------------

def test_parse_mermaid_extracts_nodes_with_labels():
    md = """
    flowchart TD
        ANT[Antenna] --> BPF[Pre-select Filter]
        BPF --> LNA[LNA 2-18 GHz]
        LNA --> MIX[Mixer]
        LO[Synthesizer LO] --> MIX
        MIX --> IF[IF Filter]
        IF --> ADC[ADC 14-bit]
    """
    d = parse_mermaid(md)
    ids = {n.node_id for n in d.nodes}
    assert {"ANT", "BPF", "LNA", "MIX", "LO", "IF", "ADC"} <= ids


def test_parse_mermaid_assigns_roles_from_label_keywords():
    d = parse_mermaid("flowchart TD\n A[LNA] --> B[Mixer]")
    a = d.node_by_id("A")
    b = d.node_by_id("B")
    assert a is not None and "lna" in a.roles
    assert b is not None and "mixer" in b.roles


def test_parse_mermaid_captures_edges():
    d = parse_mermaid("flowchart TD\n A[LNA] --> B[Mixer]\n B --> C[ADC]")
    assert ("A", "B") in d.edges
    assert ("B", "C") in d.edges


def test_parse_mermaid_handles_piped_edge_labels():
    d = parse_mermaid("flowchart TD\n A[LNA] -->|2-18 GHz| B[Mixer]")
    assert ("A", "B") in d.edges


def test_parse_mermaid_empty_input_returns_empty_diagram():
    d = parse_mermaid("")
    assert d.nodes == [] and d.edges == []


def test_parse_mermaid_bare_identifiers_still_register_nodes():
    d = parse_mermaid("flowchart TD\n A --> B")
    ids = {n.node_id for n in d.nodes}
    assert ids == {"A", "B"}


# ---------------------------------------------------------------------------
# Common checks — LNA presence, preselector ordering
# ---------------------------------------------------------------------------

def test_no_lna_triggers_critical_violation():
    md = "flowchart TD\n ANT[Antenna] --> MIX[Mixer]\n LO[LO] --> MIX\n MIX --> ADC[ADC]"
    vs = validate(md, architecture="superhet_single")
    severities = [(v.severity, v.category) for v in vs]
    assert ("critical", "topology") in severities
    assert any("No LNA" in v.detail for v in vs)


def test_lna_satisfies_the_lna_rule_across_all_archs():
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> BPF[Pre-select]\n"
        " BPF --> LNA[LNA]\n"
        " LNA --> MIX[Mixer]\n"
        " LO[Synthesizer] --> MIX\n"
        " MIX --> IF[IF Filter]\n"
        " IF --> ADC[ADC]\n"
        " CLK[Sample Clock] --> ADC\n"
    )
    vs = validate(md, architecture="digital_if")
    assert not any("No LNA" in v.detail for v in vs)


def test_preselector_after_lna_flagged_high():
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> LNA[LNA]\n"
        " LNA --> BPF[Band-pass Filter]\n"
        " BPF --> MIX[Mixer]\n"
        " LO[LO] --> MIX\n"
        " MIX --> IF[IF Filter]\n"
    )
    vs = validate(md, architecture="superhet_single")
    assert any(
        v.severity == "high" and "preselector" in v.detail.lower()
        for v in vs
    )


def test_preselector_before_lna_is_fine():
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> BPF[Preselector BPF]\n"
        " BPF --> LNA[LNA]\n"
        " LNA --> MIX[Mixer]\n"
        " LO[LO] --> MIX\n"
        " MIX --> IF[IF Filter]\n"
    )
    vs = validate(md, architecture="superhet_single")
    assert not any("preselector" in v.detail.lower() for v in vs)


# ---------------------------------------------------------------------------
# Downconversion — mixer / LO / two-mixer checks
# ---------------------------------------------------------------------------

def test_superhet_single_missing_mixer_flags_critical():
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> LNA[LNA]\n"
        " LNA --> IF[IF Filter]\n"
        " IF --> ADC[ADC]\n"
    )
    vs = validate(md, architecture="superhet_single")
    assert any(v.severity == "critical" and "mixer" in v.detail.lower() for v in vs)


def test_superhet_single_missing_lo_flags_critical():
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> LNA[LNA]\n"
        " LNA --> MIX[Mixer]\n"
        " MIX --> IF[IF Filter]\n"
    )
    vs = validate(md, architecture="superhet_single")
    assert any(
        v.severity == "critical" and
        ("local oscillator" in v.detail.lower() or "synthesizer" in v.detail.lower())
        for v in vs
    )


def test_superhet_double_requires_two_mixers():
    # One mixer — should fail
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> LNA[LNA]\n"
        " LNA --> MIX1[Mixer]\n"
        " LO1[Synthesizer] --> MIX1\n"
        " MIX1 --> IF[IF Filter]\n"
    )
    vs = validate(md, architecture="superhet_double")
    assert any("two mixer" in v.detail.lower() for v in vs)


def test_superhet_double_with_two_mixers_passes_mixer_count():
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> LNA[LNA]\n"
        " LNA --> MIX1[Mixer 1st IF]\n"
        " LO1[LO1 PLL] --> MIX1\n"
        " MIX1 --> IF1[IF Filter 1]\n"
        " IF1 --> MIX2[Mixer 2nd IF]\n"
        " LO2[LO2 PLL] --> MIX2\n"
        " MIX2 --> IF2[IF Filter 2]\n"
    )
    vs = validate(md, architecture="superhet_double")
    assert not any("two mixer" in v.detail.lower() for v in vs)


def test_superhet_missing_if_filter_flags_high():
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> LNA[LNA]\n"
        " LNA --> MIX[Mixer]\n"
        " LO[Synthesizer] --> MIX\n"
        " MIX --> ADC[ADC]\n"
    )
    vs = validate(md, architecture="superhet_single")
    assert any(v.severity == "high" and "IF" in v.detail for v in vs)


# ---------------------------------------------------------------------------
# Digital back-end — ADC / clock / filter bank
# ---------------------------------------------------------------------------

def test_direct_rf_sample_missing_adc_flags_critical():
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> BPF[Preselector]\n"
        " BPF --> LNA[LNA]\n"
        " LNA --> FPGA[FPGA DSP]\n"
    )
    vs = validate(md, architecture="direct_rf_sample")
    assert any(v.severity == "critical" and "ADC" in v.detail for v in vs)


def test_direct_rf_sample_missing_clock_flags_high():
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> BPF[Preselector]\n"
        " BPF --> LNA[LNA]\n"
        " LNA --> ADC[ADC 14-bit]\n"
        " ADC --> FPGA[FPGA DSP]\n"
    )
    vs = validate(md, architecture="direct_rf_sample")
    assert any(
        v.severity == "high" and "clock" in v.detail.lower()
        for v in vs
    )


def test_direct_rf_sample_with_mixer_flagged_medium():
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> BPF[Preselector]\n"
        " BPF --> LNA[LNA]\n"
        " LNA --> MIX[Mixer]\n"
        " LO[LO] --> MIX\n"
        " MIX --> ADC[ADC]\n"
        " CLK[Sample Clock] --> ADC\n"
    )
    vs = validate(md, architecture="direct_rf_sample")
    assert any(
        v.severity == "medium" and "should NOT have an analog mixer" in v.detail
        for v in vs
    )


def test_channelized_requires_filter_bank():
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> BPF[Preselector]\n"
        " BPF --> LNA[LNA]\n"
        " LNA --> ADC[ADC]\n"
        " CLK[Sample Clock] --> ADC\n"
        " ADC --> DSP[DSP]\n"
    )
    vs = validate(md, architecture="channelized")
    assert any("filter bank" in v.detail.lower() for v in vs)


def test_digital_if_requires_mixer():
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> LNA[LNA]\n"
        " LNA --> ADC[ADC]\n"
        " CLK[Clock] --> ADC\n"
        " ADC --> FPGA[FPGA DSP]\n"
    )
    vs = validate(md, architecture="digital_if")
    assert any(v.severity == "high" and "mixer" in v.detail.lower() for v in vs)


# ---------------------------------------------------------------------------
# Front-end only — must not contain mixer/ADC
# ---------------------------------------------------------------------------

def test_front_end_with_mixer_flagged_medium():
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> BPF[Preselector]\n"
        " BPF --> LNA[LNA]\n"
        " LNA --> MIX[Mixer]\n"
    )
    vs = validate(md, architecture="std_lna_filter")
    assert any(v.severity == "medium" and "mixer" in v.detail.lower() for v in vs)


def test_lna_filter_limiter_requires_limiter():
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> BPF[Preselector]\n"
        " BPF --> LNA[LNA]\n"
    )
    vs = validate(md, architecture="lna_filter_limiter")
    assert any("limiter" in v.detail.lower() for v in vs)


def test_front_end_happy_path_has_no_violations():
    md = (
        "flowchart TD\n"
        " ANT[Antenna] --> LIM[PIN Diode Limiter]\n"
        " LIM --> BPF[Preselector BPF]\n"
        " BPF --> LNA[LNA 2-18 GHz]\n"
        " LNA --> OUT[IF Output]\n"
    )
    vs = validate(md, architecture="lna_filter_limiter")
    assert vs == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_diagram_returns_critical():
    vs = validate("", architecture="superhet_single")
    assert len(vs) == 1 and vs[0].severity == "critical"


def test_unknown_architecture_warns_not_blocks():
    md = "flowchart TD\n LNA[LNA] --> OUT[Output]"
    vs = validate(md, architecture="quantum-radio")
    assert any(v.severity == "medium" and "Unknown" in v.detail for v in vs)


def test_recommend_architecture_skips_topology_rules():
    """When the user picks 'Not sure — recommend', we don't have a target
    topology yet, so only the LNA / preselector common checks run."""
    md = "flowchart TD\n A[LNA] --> B[Output]"
    vs = validate(md, architecture="recommend")
    assert vs == []  # LNA present, no preselector → nothing to flag


def test_none_architecture_still_runs_common_checks():
    md = "flowchart TD\n ANT[Antenna] --> OUT[Output]"  # no LNA
    vs = validate(md, architecture=None)
    assert any("No LNA" in v.detail for v in vs)


# ---------------------------------------------------------------------------
# format_violations
# ---------------------------------------------------------------------------

def test_format_violations_empty_passes_message():
    assert "passed" in format_violations([])


def test_format_violations_renders_markdown_table():
    md = format_violations([Violation(
        severity="critical", category="topology",
        detail="missing mixer", suggested_fix="add mixer",
        architecture="superhet_single",
    )])
    assert "| critical |" in md
    assert "missing mixer" in md


# ---------------------------------------------------------------------------
# Transmitter architectures
# ---------------------------------------------------------------------------

class TestTxLinearPaChain:

    def _full_linear_tx(self):
        return (
            "flowchart LR\n"
            "  BB[Baseband Source] --> DRV[Driver Amp]\n"
            "  DRV --> PA[Class-AB Power Amplifier]\n"
            "  PA --> HF[Harmonic Filter]\n"
            "  HF --> ANT[Antenna]\n"
        )

    def test_full_linear_tx_chain_passes(self):
        v = validate(self._full_linear_tx(), architecture="tx_driver_pa_classab")
        severities = [x.severity for x in v if x.category == "topology"]
        assert "critical" not in severities
        assert "high" not in severities

    def test_missing_pa_flagged_critical(self):
        m = (
            "flowchart LR\n"
            "  BB[Baseband] --> HF[Harmonic Filter]\n"
            "  HF --> ANT[Antenna]\n"
        )
        v = validate(m, architecture="tx_driver_pa_classab")
        assert any(
            x.severity == "critical" and "driver" in x.detail.lower()
            for x in v
        )

    def test_missing_harmonic_filter_flagged_high(self):
        m = (
            "flowchart LR\n"
            "  BB[Baseband] --> DRV[Driver]\n"
            "  DRV --> PA[Class-AB PA]\n"
            "  PA --> ANT[Antenna]\n"
        )
        v = validate(m, architecture="tx_driver_pa_classab")
        assert any(
            x.severity == "high" and "harmonic" in x.detail.lower()
            for x in v
        )

    def test_lna_in_tx_chain_flagged_medium(self):
        """A receiver LNA accidentally placed in a TX design — flag it."""
        m = (
            "flowchart LR\n"
            "  BB[Baseband] --> LNA[LNA]\n"
            "  LNA --> PA[Class-AB PA]\n"
            "  PA --> HF[Harmonic Filter]\n"
            "  HF --> ANT[Antenna]\n"
        )
        v = validate(m, architecture="tx_driver_pa_classab")
        assert any(
            x.severity == "medium"
            and "lna" in x.detail.lower()
            and "receiver" in x.detail.lower()
            for x in v
        )

    def test_rx_lna_check_skipped_when_tx_architecture(self):
        """TX projects must NOT trigger the 'no LNA found' receiver check."""
        v = validate(
            "flowchart LR\n"
            "  DRV[Driver] --> PA[Class-AB PA]\n"
            "  PA --> HF[Harmonic Filter] --> ANT[Antenna]\n",
            architecture="tx_driver_pa_classab",
        )
        assert not any(
            "noise figure" in x.detail.lower()
            or "friis sensitivity" in x.detail.lower()
            for x in v
        )

    def test_dpd_without_dac_or_fpga_flagged(self):
        m = (
            "flowchart LR\n"
            "  BB[Baseband] --> DRV[Driver]\n"
            "  DRV --> PA[Doherty PA]\n"
            "  PA --> HF[Harmonic Filter] --> ANT[Antenna]\n"
        )
        v = validate(m, architecture="tx_dpd_linearized")
        assert any(
            "dpd" in x.detail.lower() or "predistortion" in x.detail.lower()
            for x in v
        )


class TestTxSaturatedPa:

    def test_pulsed_radar_without_isolator_flagged_medium(self):
        m = (
            "flowchart LR\n"
            "  BB[Pulse Gen] --> DRV[Driver]\n"
            "  DRV --> PA[Class-C PA]\n"
            "  PA --> HF[Harmonic Filter] --> ANT[Antenna]\n"
        )
        v = validate(m, architecture="tx_pulse_radar")
        assert any(
            x.severity == "medium"
            and ("isolator" in x.detail.lower() or "circulator" in x.detail.lower())
            for x in v
        )

    def test_pulsed_radar_with_circulator_passes(self):
        m = (
            "flowchart LR\n"
            "  BB[Pulse Gen] --> DRV[Driver]\n"
            "  DRV --> PA[Class-C PA]\n"
            "  PA --> HF[Harmonic Filter] --> CIRC[Circulator] --> ANT[Antenna]\n"
        )
        v = validate(m, architecture="tx_pulse_radar")
        assert not any(
            "isolator" in x.detail.lower() or "circulator" in x.detail.lower()
            for x in v
        )

    def test_saturated_without_harmonic_filter_flagged_high(self):
        m = (
            "flowchart LR\n"
            "  BB[Source] --> DRV[Driver]\n"
            "  DRV --> PA[Class-E PA]\n"
            "  PA --> ANT[Antenna]\n"
        )
        v = validate(m, architecture="tx_class_c_pulsed")
        assert any(
            x.severity == "high" and "harmonic" in x.detail.lower()
            for x in v
        )


class TestTxUpconversion:

    def test_iq_mod_upconvert_requires_iq_modulator(self):
        m = (
            "flowchart LR\n"
            "  DAC[Baseband DAC] --> DRV[Driver]\n"
            "  DRV --> PA[PA] --> HF[Harmonic Filter] --> ANT[Antenna]\n"
        )
        v = validate(m, architecture="tx_iq_mod_upconvert")
        assert any(
            x.severity == "critical" and "iq" in x.detail.lower()
            for x in v
        )

    def test_iq_mod_upconvert_with_modulator_passes(self):
        m = (
            "flowchart LR\n"
            "  DAC[Baseband DAC] --> MOD[IQ Modulator]\n"
            "  MOD --> DRV[Driver] --> PA[PA] --> HF[Harmonic Filter] --> ANT[Antenna]\n"
        )
        v = validate(m, architecture="tx_iq_mod_upconvert")
        assert not any(x.severity == "critical" for x in v)

    def test_superhet_tx_requires_mixer_and_lo(self):
        m = (
            "flowchart LR\n"
            "  IF[IF Source] --> DRV[Driver] --> PA[PA] --> HF[Output Filter] --> ANT[Antenna]\n"
        )
        v = validate(m, architecture="tx_superhet_upconvert")
        criticals = [x for x in v if x.severity == "critical"]
        assert any("mixer" in x.detail.lower() for x in criticals)

    def test_direct_dac_requires_dac_node(self):
        m = (
            "flowchart LR\n"
            "  BB[Source] --> DRV[Driver] --> PA[PA] --> HF[Harmonic Filter] --> ANT[Antenna]\n"
        )
        v = validate(m, architecture="tx_direct_dac")
        assert any(
            x.severity == "critical" and "dac" in x.detail.lower()
            for x in v
        )

    def test_direct_dac_with_rf_dac_passes(self):
        m = (
            "flowchart LR\n"
            "  FPGA[FPGA] --> DAC[RF DAC]\n"
            "  DAC --> DRV[Driver] --> PA[PA] --> HF[Harmonic Filter] --> ANT[Antenna]\n"
        )
        v = validate(m, architecture="tx_direct_dac")
        assert not any(x.severity == "critical" for x in v)


class TestTxRecommendFallback:

    def test_tx_recommend_gets_common_checks_only(self):
        """tx_recommend is the 'architect picks' option — no arch-specific
        rules fire, but the common PA-presence check still applies."""
        m = (
            "flowchart LR\n"
            "  BB[Baseband] --> ANT[Antenna]\n"
        )
        v = validate(m, architecture="tx_recommend")
        assert any(x.severity == "critical" and "driver" in x.detail.lower()
                   for x in v)

    def test_tx_recommend_full_chain_passes(self):
        m = (
            "flowchart LR\n"
            "  BB[Source] --> DRV[Driver] --> PA[PA] --> HF[Harmonic Filter] --> ANT[Antenna]\n"
        )
        v = validate(m, architecture="tx_recommend")
        assert not any(x.severity == "critical" for x in v)
