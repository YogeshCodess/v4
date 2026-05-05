"""
services/elicitation_state.py — B3.1.

Pure state-machine for the P1 4-round elicitation flow (see CLAUDE.md §"P1
Anti-Hallucination Design"). The `RequirementsAgent` delegates round
computation to this module so the logic is unit-testable without spinning up
an LLM.

Rounds
------
1.  **Tier-1 RF/hardware specs** (frequency, bandwidth, NF, sensitivity …)
1.5 **Application-adaptive questions** (mil/ew/sigint vs comms vs radar …)
2.  **Architecture selection** (superhet, direct conversion, SDR, …)
3.  **Architecture follow-ups** (IF frequency, ADC rate, LO phase noise …)
4.  **Validation + cascade preview → explicit user confirmation**

The state machine emits a single `ElicitationStep` per turn that tells the
agent what kind of reply it should produce:
- `ask_round1`         — Tier-1 cards
- `ask_round15_and_2`  — adaptive questions + architecture options
- `ask_round3_and_4`   — follow-ups + cascade preview + ask for confirmation
- `generate`           — user confirmed, call generate_requirements

Each enum value carries a short `tool_hint` the agent can forward to the LLM
to force the right structured output.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Approval / regeneration keywords
# ---------------------------------------------------------------------------

_APPROVAL_KEYWORDS: frozenset[str] = frozenset({
    "approve", "approved", "yes", "ok", "okay", "looks good", "good",
    "correct", "proceed", "go ahead", "lgtm", "perfect", "great",
    "confirm", "confirmed", "agreed", "accept", "finalize", "generate",
})

_REGEN_KEYWORDS: frozenset[str] = frozenset({
    "regenerate", "re-generate", "re generate", "rerun", "re-run",
    "update requirements", "redo", "refresh", "rebuild", "recreate",
})


def is_approval(text: str) -> bool:
    """True iff `text` is a positive confirmation of the Round-4 summary."""
    if not text:
        return False
    t = text.strip().lower()
    if not t:
        return False
    # An exact approval keyword
    if t in _APPROVAL_KEYWORDS:
        return True
    # Approval phrase at the start
    for kw in _APPROVAL_KEYWORDS:
        if t.startswith(kw + " ") or t.startswith(kw + ","):
            return True
    return False


def is_regeneration(text: str) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    return any(kw in t for kw in _REGEN_KEYWORDS)


# ---------------------------------------------------------------------------
# Round enum
# ---------------------------------------------------------------------------

class ElicitationStep(Enum):
    """What the agent should do on this turn."""
    ASK_ROUND1 = "ask_round1"
    ASK_ROUND_1_5_AND_2 = "ask_round1_5_and_2"
    ASK_ROUND_3_AND_4 = "ask_round3_and_4"
    GENERATE = "generate"
    CONVERSATIONAL = "conversational"

    @property
    def tool_hint(self) -> Optional[str]:
        return {
            ElicitationStep.ASK_ROUND1: "show_clarification_cards",
            ElicitationStep.ASK_ROUND_1_5_AND_2: "show_clarification_cards",
            ElicitationStep.ASK_ROUND_3_AND_4: "show_clarification_cards",
            ElicitationStep.GENERATE: "generate_requirements",
            ElicitationStep.CONVERSATIONAL: None,
        }.get(self)


@dataclass(frozen=True)
class ElicitationPlan:
    """Result of `next_step` — step + flags for the agent to consume."""
    step: ElicitationStep
    prior_user_turns: int
    prestage_used: bool
    phase_already_complete: bool
    user_wants_regen: bool
    is_confirmation: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_prestage(first_user_msg: str) -> bool:
    """The frontend's pre-stage clarifier posts the first message as a block of
    'Q -> A' pairs (one per line). Detecting this lets us skip Round 1, since
    those Tier-1 questions have already been answered."""
    if not first_user_msg:
        return False
    return " -> " in first_user_msg and first_user_msg.count("\n") >= 3


# ---------------------------------------------------------------------------
# Main entry point — pure function, no I/O
# ---------------------------------------------------------------------------

def next_step(
    messages: list[dict],
    user_input: str,
    phase_already_complete: bool = False,
) -> ElicitationPlan:
    """
    Given the conversation history and the latest user message, decide which
    elicitation step the agent should take next.

    `messages` is the full list of {role, content} dicts INCLUDING the user's
    latest turn. The function counts user turns BEFORE the latest one to
    decide the round.

    Semantics:
    - 0 prior turns & no pre-stage           → ASK_ROUND1
    - 0 prior turns &  pre-stage             → ASK_ROUND_1_5_AND_2
      (first message already contains Round-1 Q&A pairs)
    - 1 prior turn without pre-stage         → ASK_ROUND_1_5_AND_2
    - 2 prior turns without pre-stage        → ASK_ROUND_3_AND_4
    - 3+ prior turns AND user confirms       → GENERATE
    - phase_already_complete & regen request → GENERATE
    - phase_already_complete & plain question → CONVERSATIONAL
    """
    prior_user_turns = sum(
        1 for m in messages[:-1] if (m or {}).get("role") == "user"
    )

    first_user_msg = next(
        (m.get("content", "") for m in messages if (m or {}).get("role") == "user"),
        "",
    )
    prestage = _detect_prestage(first_user_msg)

    confirmation = is_approval(user_input)
    regen = is_regeneration(user_input)

    # FINALIZE sentinel: immediate generation
    if user_input.strip() == "__FINALIZE__":
        return ElicitationPlan(
            step=ElicitationStep.GENERATE,
            prior_user_turns=prior_user_turns,
            prestage_used=prestage,
            phase_already_complete=phase_already_complete,
            user_wants_regen=regen,
            is_confirmation=False,
        )

    if phase_already_complete:
        step = ElicitationStep.GENERATE if regen else ElicitationStep.CONVERSATIONAL
        return ElicitationPlan(
            step=step,
            prior_user_turns=prior_user_turns,
            prestage_used=prestage,
            phase_already_complete=True,
            user_wants_regen=regen,
            is_confirmation=confirmation,
        )

    # Minimum turns before generating: 3 without pre-stage, 2 with pre-stage.
    min_turns = 2 if prestage else 3

    if prior_user_turns >= min_turns and confirmation:
        step = ElicitationStep.GENERATE
    elif prior_user_turns == 0 and not prestage:
        step = ElicitationStep.ASK_ROUND1
    elif (prior_user_turns == 0 and prestage) or prior_user_turns == 1:
        step = ElicitationStep.ASK_ROUND_1_5_AND_2
    else:
        step = ElicitationStep.ASK_ROUND_3_AND_4

    return ElicitationPlan(
        step=step,
        prior_user_turns=prior_user_turns,
        prestage_used=prestage,
        phase_already_complete=False,
        user_wants_regen=regen,
        is_confirmation=confirmation,
    )
