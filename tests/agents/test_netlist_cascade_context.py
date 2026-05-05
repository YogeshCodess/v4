"""P1.4 — netlist_agent surfaces P1 design_parameters in the LLM prompt."""
from __future__ import annotations

import pytest

from agents.netlist_agent import NetlistAgent


@pytest.fixture
def agent():
    return NetlistAgent()


# ---------------------------------------------------------------------------
# _format_cascade_targets
# ---------------------------------------------------------------------------

class TestCascadeTargetFormatter:

    def test_empty_design_parameters_returns_sentinel(self, agent):
        out = agent._format_cascade_targets({})
        assert "no design parameters" in out.lower()

    def test_none_input_returns_sentinel(self, agent):
        assert "no design parameters" in agent._format_cascade_targets(None).lower()

    def test_non_dict_input_returns_sentinel(self, agent):
        assert "no design parameters" in agent._format_cascade_targets("garbage").lower()

    def test_renders_relevant_keys(self, agent):
        out = agent._format_cascade_targets({
            "freq_range": "2-18 GHz",
            "noise_figure_db": 1.8,
            "total_gain_db": 40.0,
            "iip3_dbm_input": 10.0,
            "phase_noise_dbchz": -140.0,
            "architecture": "superhet_single",
        })
        assert "- freq_range: 2-18 GHz" in out
        assert "- noise_figure_db: 1.8" in out
        assert "- total_gain_db: 40.0" in out
        assert "- iip3_dbm_input: 10.0" in out
        assert "- phase_noise_dbchz: -140.0" in out
        assert "- architecture: superhet_single" in out

    def test_ignores_irrelevant_keys(self, agent):
        out = agent._format_cascade_targets({
            "project_summary": "a long prose string that should not leak in",
            "noise_figure_db": 2.0,
        })
        assert "project_summary" not in out
        assert "noise_figure_db: 2.0" in out

    def test_none_valued_keys_skipped(self, agent):
        out = agent._format_cascade_targets({
            "noise_figure_db": None,
            "total_gain_db": 40.0,
        })
        assert "noise_figure_db" not in out
        assert "total_gain_db: 40.0" in out

    def test_all_noise_keys_filtered_returns_sentinel(self, agent):
        out = agent._format_cascade_targets({
            "project_summary": "irrelevant",
            "unrelated_key": "ignored",
        })
        assert "no cascade-relevant keys" in out.lower()
