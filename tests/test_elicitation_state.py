"""Tests for services/elicitation_state.py — B3.1 state machine."""
from __future__ import annotations

from services.elicitation_state import (
    ElicitationStep,
    is_approval,
    is_regeneration,
    next_step,
)


def _turn(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def test_is_approval_recognises_common_forms():
    assert is_approval("approve")
    assert is_approval("approved, proceed")
    assert is_approval("yes, go ahead")
    assert is_approval("  OK  ")
    assert is_approval("lgtm")
    assert not is_approval("hmm, maybe later")
    assert not is_approval("")


def test_is_regeneration_detects_rerun_words():
    assert is_regeneration("please regenerate with new spec")
    assert is_regeneration("rerun")
    assert is_regeneration("redo the BOM")
    assert not is_regeneration("this is fine")


def test_round1_on_empty_history_without_prestage():
    msgs = [_turn("user", "Need UHF tactical radio front-end.")]
    plan = next_step(messages=msgs, user_input=msgs[0]["content"])
    assert plan.step == ElicitationStep.ASK_ROUND1
    assert plan.prior_user_turns == 0
    assert plan.prestage_used is False


def test_prestage_first_message_skips_round1():
    """Pre-stage: the frontend concatenates Q -> A pairs in the first message."""
    prestage_body = (
        "Frequency range -> 225-512 MHz\n"
        "Bandwidth -> 25 kHz channels\n"
        "Target NF -> 3 dB\n"
        "SFDR -> 70 dB"
    )
    msgs = [_turn("user", prestage_body)]
    plan = next_step(messages=msgs, user_input=prestage_body)
    assert plan.prestage_used is True
    assert plan.step == ElicitationStep.ASK_ROUND_1_5_AND_2


def test_second_turn_triggers_round_1_5_and_2():
    msgs = [
        _turn("user", "need VHF comms receiver"),
        _turn("assistant", "here are Round 1 questions"),
        _turn("user", "freq 30-88 MHz, NF 3 dB, gain 70 dB"),
    ]
    plan = next_step(messages=msgs, user_input=msgs[-1]["content"])
    assert plan.step == ElicitationStep.ASK_ROUND_1_5_AND_2
    assert plan.prior_user_turns == 1


def test_third_turn_triggers_round_3_and_4():
    msgs = [
        _turn("user", "need VHF comms"),
        _turn("assistant", "Round 1 cards"),
        _turn("user", "filled Round 1"),
        _turn("assistant", "Round 1.5 + 2 cards"),
        _turn("user", "superheterodyne architecture"),
    ]
    plan = next_step(messages=msgs, user_input=msgs[-1]["content"])
    assert plan.step == ElicitationStep.ASK_ROUND_3_AND_4
    assert plan.prior_user_turns == 2


def test_confirmation_after_three_turns_triggers_generate():
    msgs = [
        _turn("user", "need VHF comms"),
        _turn("assistant", "Round 1 cards"),
        _turn("user", "filled Round 1"),
        _turn("assistant", "Round 1.5 + 2 cards"),
        _turn("user", "superheterodyne"),
        _turn("assistant", "Round 3 + 4 cascade preview"),
        _turn("user", "approved"),
    ]
    plan = next_step(messages=msgs, user_input=msgs[-1]["content"])
    assert plan.step == ElicitationStep.GENERATE
    assert plan.is_confirmation is True
    assert plan.prior_user_turns == 3


def test_confirmation_before_three_turns_is_ignored():
    """The user cannot shortcut the flow — confirmation only triggers generation
    once the minimum round count has been reached."""
    msgs = [
        _turn("user", "need radar"),
        _turn("assistant", "Round 1 cards"),
        _turn("user", "approved"),   # approved too early
    ]
    plan = next_step(messages=msgs, user_input="approved")
    assert plan.step == ElicitationStep.ASK_ROUND_1_5_AND_2
    assert plan.is_confirmation is True
    # But the minimum gate blocks generation


def test_prestage_reaches_generate_in_fewer_turns():
    msgs = [
        _turn("user", (
            "Freq -> 225-512 MHz\nBW -> 25 kHz\nNF -> 3 dB\nSFDR -> 70 dB"
        )),
        _turn("assistant", "R1.5+R2 cards"),
        _turn("user", "superheterodyne"),
        _turn("assistant", "R3+R4 cascade preview"),
        _turn("user", "approved"),
    ]
    plan = next_step(messages=msgs, user_input="approved")
    assert plan.step == ElicitationStep.GENERATE
    assert plan.prestage_used is True


def test_finalize_sentinel_shortcuts_to_generate():
    msgs = [_turn("user", "__FINALIZE__")]
    plan = next_step(messages=msgs, user_input="__FINALIZE__")
    assert plan.step == ElicitationStep.GENERATE


def test_conversational_mode_when_phase_already_complete():
    msgs = [_turn("user", "what's the NF of the LNA?")]
    plan = next_step(msgs, user_input=msgs[-1]["content"],
                     phase_already_complete=True)
    assert plan.step == ElicitationStep.CONVERSATIONAL


def test_regeneration_trigger_even_after_completion():
    msgs = [_turn("user", "please regenerate with new cooling spec")]
    plan = next_step(msgs, user_input=msgs[-1]["content"],
                     phase_already_complete=True)
    assert plan.step == ElicitationStep.GENERATE
    assert plan.user_wants_regen is True


def test_tool_hint_mapping():
    assert ElicitationStep.ASK_ROUND1.tool_hint == "show_clarification_cards"
    assert ElicitationStep.GENERATE.tool_hint == "generate_requirements"
    assert ElicitationStep.CONVERSATIONAL.tool_hint is None
