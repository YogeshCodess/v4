"""Tests for TX prompt supplement injection on requirements_agent."""
from __future__ import annotations

import pytest

from agents.requirements_agent import RequirementsAgent, TX_PROMPT_SUPPLEMENT


@pytest.fixture
def agent():
    return RequirementsAgent()


class TestTxPromptInjection:

    def test_rx_project_does_not_inject_supplement(self, agent):
        ctx = {
            "design_type": "RF",
            "name": "Test RX",
            "project_type": "receiver",
        }
        prompt = agent.get_system_prompt(ctx)
        assert "TRANSMITTER MODE — OVERRIDE" not in prompt

    def test_tx_via_project_type_injects_supplement(self, agent):
        ctx = {
            "design_type": "RF",
            "name": "Test TX",
            "project_type": "transmitter",
        }
        prompt = agent.get_system_prompt(ctx)
        assert "TRANSMITTER MODE — OVERRIDE" in prompt
        # Supplement is prepended, so it appears before the base identity.
        supp_idx = prompt.index("TRANSMITTER MODE — OVERRIDE")
        id_idx = prompt.index("# IDENTITY")
        assert supp_idx < id_idx

    def test_tx_via_design_parameters_direction_injects_supplement(self, agent):
        ctx = {
            "design_type": "RF",
            "name": "Test TX",
            "design_parameters": {"direction": "tx"},
        }
        prompt = agent.get_system_prompt(ctx)
        assert "TRANSMITTER MODE — OVERRIDE" in prompt

    def test_supplement_mentions_tx_cascade_math(self, agent):
        """Smoke check that the LLM will see the specific TX cascade math
        references it needs to emit `direction=tx` in design_parameters."""
        ctx = {"project_type": "transmitter"}
        prompt = agent.get_system_prompt(ctx)
        assert "pout_dbm" in prompt
        assert "oip3_dbm" in prompt
        assert "PAE" in prompt
        assert 'direction = "tx"' in prompt or 'direction="tx"' in prompt

    def test_supplement_lists_tx_architectures(self, agent):
        ctx = {"project_type": "transmitter"}
        prompt = agent.get_system_prompt(ctx)
        # All 8 real TX architectures + "not sure"
        for arch in [
            "Driver + PA", "Doherty", "DPD-Linearized",
            "Class-C", "Radar Pulsed",
            "IQ-Modulator Upconvert", "Superhet TX", "Direct-DAC",
        ]:
            assert arch in prompt

    def test_supplement_bans_rx_specific_questions(self, agent):
        """The supplement must explicitly tell the LLM to skip sensitivity /
        MDS / NF questions that are meaningless for a TX project."""
        ctx = {"project_type": "transmitter"}
        prompt = agent.get_system_prompt(ctx)
        assert "DO NOT ASK" in prompt
        assert "sensitivity" in prompt.lower()
        assert "mds" in prompt.lower()

    def test_rx_description_still_appended(self, agent):
        """The pre-stated-requirements block must still be appended below
        the base prompt even when the TX supplement is prepended."""
        ctx = {
            "project_type": "transmitter",
            "description": "10 W GaN PA at 2.4 GHz, ISM band",
        }
        prompt = agent.get_system_prompt(ctx)
        assert "TRANSMITTER MODE — OVERRIDE" in prompt
        assert "10 W GaN PA at 2.4 GHz" in prompt

    def test_bare_supplement_constant_importable(self):
        """Sanity: the supplement constant is exportable for standalone use
        (e.g. NetlistAgent or HRSAgent later reusing the override block)."""
        assert "TRANSMITTER MODE" in TX_PROMPT_SUPPLEMENT
        assert "TX ROUND-1 TIER-1 SPECS" in TX_PROMPT_SUPPLEMENT
