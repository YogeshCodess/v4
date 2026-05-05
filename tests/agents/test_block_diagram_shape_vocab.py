"""Pins the shape + spec contract for `block_diagram_mermaid`.

The block diagram description tells the LLM how to render the RF signal
chain — what shape vocabulary to use (flag for amps, hexagon for filters,
etc.) and how to annotate per-stage specs. If someone removes the shape
vocabulary or the cascade subgraph requirement during a refactor, the
diagram silently regresses to a generic flowchart. These tests fail loud.
"""
from __future__ import annotations

from agents.requirements_agent import GENERATE_REQUIREMENTS_TOOL


def _block_diagram_desc() -> str:
    schema = GENERATE_REQUIREMENTS_TOOL["input_schema"]["properties"]
    return schema["block_diagram_mermaid"]["description"]


class TestShapeVocabulary:
    def test_flag_shape_for_amplifier(self):
        desc = _block_diagram_desc()
        assert ">LNA1]" in desc, "amplifier flag-shape example missing"

    def test_hexagon_shape_for_filter(self):
        desc = _block_diagram_desc()
        assert "{{BPF}}" in desc, "filter hexagon-shape example missing"

    def test_rhombus_shape_for_bias_t_and_splitter(self):
        desc = _block_diagram_desc()
        assert "{BiasT}" in desc
        assert "{Split}" in desc

    def test_parallelogram_shape_for_connector(self):
        desc = _block_diagram_desc()
        assert "[/SMA/]" in desc

    def test_trapezoid_shape_for_limiter(self):
        desc = _block_diagram_desc()
        # Use raw substring without the python-escaped backslash.
        assert "[/Lim\\]" in desc

    def test_rounded_shape_for_mixer(self):
        desc = _block_diagram_desc()
        assert "(MIX)" in desc

    def test_parallelogram_alt_for_adc(self):
        desc = _block_diagram_desc()
        assert "[\\ADC\\]" in desc

    def test_flag_shape_for_antenna_and_output(self):
        desc = _block_diagram_desc()
        assert ">Ant1]" in desc
        assert ">Out]" in desc


class TestSpecAnnotation:
    def test_per_stage_spec_format_specified(self):
        desc = _block_diagram_desc()
        # The contract is: Role / MPN / G+xx NFy.y P1+zz on a single line.
        assert "Role / MPN" in desc
        assert "G+" in desc and "NF" in desc and "P1+" in desc

    def test_separator_is_forward_slash_not_pipe(self):
        desc = _block_diagram_desc()
        # `|` is forbidden inside Mermaid labels — sanitiser converts it to `/`.
        assert "use `/`" in desc or "` / `" in desc


class TestCascadeSubgraph:
    def test_cascade_subgraph_required(self):
        desc = _block_diagram_desc()
        assert "subgraph CASCADE" in desc

    def test_cascade_metrics_listed(self):
        desc = _block_diagram_desc()
        for metric in ("Net Gain", "System NF", "Output P1dB", "Output IIP3"):
            assert metric in desc, f"cascade subgraph missing {metric!r}"

    def test_cascade_uses_friis_from_design_parameters(self):
        desc = _block_diagram_desc()
        assert "Friis" in desc
        assert "design_parameters" in desc


class TestForbiddenPatterns:
    def test_plain_rectangles_forbidden_for_blocks(self):
        desc = _block_diagram_desc()
        assert "FORBIDDEN" in desc
        assert "[Rectangle]" in desc

    def test_br_html_tags_forbidden(self):
        desc = _block_diagram_desc()
        # The sanitiser strips `<br/>` so multi-line labels would silently break.
        assert "<br/>" in desc and "renderer" in desc.lower()


class TestTopologyMandate:
    def test_canonical_chain_still_pinned(self):
        # Regression guard — the new shape contract must NOT have eaten the
        # original topology mandate.
        desc = _block_diagram_desc()
        assert "Limiter" in desc
        assert "Preselector" in desc
        assert "MANDATORY" in desc
        assert "MULTI-ANTENNA" in desc
        assert "CHANNELISED FILTER BANK" in desc
        assert "HIGH-GAIN STABILITY" in desc
