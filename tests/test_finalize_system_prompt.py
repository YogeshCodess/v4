"""Regression tests for the P21 finalize-turn system-prompt swap.

User report (2026-04-24, server log):
  > `call_llm_with_tools finished in 563.64s
  >  (finalize=True, gen_captured=False, cards_captured=False)`
  > chat.payload preview={"response": "", ...}

Root cause: the full SYSTEM_PROMPT (~500 lines of wizard / architecture
selection / anti-hallucination rules) was being sent to the model on
EVERY turn — including the finalize turn where the specs are already
captured. On dense RF designs the model spent its entire reasoning
budget parsing the prompt and returned empty content.

Fix (P21): swap to a compact FINALIZE_SYSTEM_PROMPT (~50 lines) on the
terminal `generate_requirements` call. Same hard constraints (MPN-shape
gate, candidate-pool membership, structured-diagram preference) — just
without the elicitation-phase boilerplate the model doesn't need at
finalize.

These tests guard:
  - FINALIZE_SYSTEM_PROMPT exists as a module-level constant.
  - It contains every hard constraint (no loss of correctness
    guarantees during the compaction).
  - It's dramatically shorter than SYSTEM_PROMPT.
  - The agent's tool-use path actually swaps to it when `_is_finalize`
    or `_is_wizard_payload` is true.
"""
from __future__ import annotations

import inspect

from agents.requirements_agent import (
    FINALIZE_SYSTEM_PROMPT,
    RequirementsAgent,
    SYSTEM_PROMPT,
)


# ---------------------------------------------------------------------------
# FINALIZE_SYSTEM_PROMPT content guards
# ---------------------------------------------------------------------------

def test_finalize_prompt_is_module_level_constant():
    """Must be importable from the module top level for observability
    and unit-testability. Private/nested strings can't be grepped or
    tested without reflection."""
    assert isinstance(FINALIZE_SYSTEM_PROMPT, str)
    assert len(FINALIZE_SYSTEM_PROMPT) > 500, (
        "FINALIZE_SYSTEM_PROMPT is suspiciously short — are we stripping "
        "too aggressively?"
    )


def test_finalize_prompt_is_substantially_shorter_than_full_prompt():
    """The whole point of the swap is to reduce the model's context
    burden on finalize. If this ratio drops below 30% reduction we've
    lost the latency benefit — flag loudly."""
    ratio = len(FINALIZE_SYSTEM_PROMPT) / len(SYSTEM_PROMPT)
    assert ratio < 0.30, (
        f"FINALIZE_SYSTEM_PROMPT is {ratio:.1%} of SYSTEM_PROMPT — "
        f"compaction target is <30%. Either SYSTEM_PROMPT shrank or "
        f"FINALIZE_SYSTEM_PROMPT grew; either way, re-examine before "
        f"shipping."
    )


def test_finalize_prompt_preserves_mpn_shape_rule():
    """P9's MPN-shape gate rejects BOM rows where `part_number` is
    descriptive ("Discrete thin-film 50 Ohm pad"). The LLM must know
    about this rule even in the compact prompt, otherwise it emits
    garbage that the gate rejects and we waste a retry."""
    text = FINALIZE_SYSTEM_PROMPT.lower()
    assert "mpn-shaped" in text or "mpn shape" in text or "part_number" in text
    assert "no whitespace" in text or "no internal" in text
    # Must give a concrete counterexample so the rule is unambiguous.
    assert "discrete thin-film" in text or "description" in text


def test_finalize_prompt_preserves_candidate_pool_rule():
    """The P9/P10 audit catches `not_from_candidate_pool` blockers when
    the LLM picks MPNs outside the `find_candidate_parts` shortlist. The
    finalize prompt must keep this rule explicit."""
    text = FINALIZE_SYSTEM_PROMPT.lower()
    assert "candidate_pool" in text or "candidate pool" in text
    assert "find_candidate_parts" in text


def test_finalize_prompt_prefers_structured_diagrams():
    """P13+ mermaid saga: raw `block_diagram_mermaid` strings are the
    source of every parse error we've chased. The finalize prompt must
    prefer the structured `block_diagram` / `architecture` JSON fields."""
    text = FINALIZE_SYSTEM_PROMPT
    assert "block_diagram" in text
    assert "architecture" in text
    # Must tell the LLM to prefer structured over raw.
    text_low = text.lower()
    assert "structured" in text_low
    assert (
        "preferred" in text_low
        or "mandatory" in text_low
        or "NOT the raw" in text
        or "not the raw" in text_low
    )


def test_finalize_prompt_discourages_extended_thinking():
    """Observed failure mode: model spent 563s on internal reasoning and
    emitted nothing. The prompt must explicitly tell it not to do that."""
    text = FINALIZE_SYSTEM_PROMPT.lower()
    assert (
        "extended" in text
        or "internal reasoning" in text
        or "over-think" in text
        or "over-thinks" in text
        or "stall" in text
    )


def test_finalize_prompt_forbids_reasking():
    """Deterministic wizard has already gathered every spec. The LLM
    must NOT emit clarification cards at finalize — doing so breaks the
    contract and the frontend dead-ends."""
    text = FINALIZE_SYSTEM_PROMPT.lower()
    assert "do not re-ask" in text or "not re-ask" in text or "do not ask" in text


# ---------------------------------------------------------------------------
# Swap-point guard — the agent must actually use FINALIZE_SYSTEM_PROMPT
# on finalize / wizard turns, not just define it in a module.
# ---------------------------------------------------------------------------

def test_agent_swaps_to_finalize_prompt_on_finalize_turn():
    """Whitebox check: the tool-use path in RequirementsAgent must
    reassign `system = FINALIZE_SYSTEM_PROMPT` when `_is_finalize` OR
    `_user_confirmed_generation` OR `_is_wizard_payload` is true.

    If the swap logic is removed / bypassed, every finalize turn goes
    back to the full 500-line prompt and we regress to 9-min LLM calls."""
    # Find the tool-use method that calls call_llm_with_tools.
    src = inspect.getsource(RequirementsAgent)
    assert "system = FINALIZE_SYSTEM_PROMPT" in src, (
        "Agent must reassign `system = FINALIZE_SYSTEM_PROMPT` on the "
        "finalize turn. If this reassignment is missing, the full "
        "~500-line SYSTEM_PROMPT is sent to every finalize call and "
        "we regress to 9-min stalls observed on 2026-04-24."
    )
    # Guard the trigger conditions so a refactor can't silently drop
    # one of them.
    lines_with_swap = [
        ln for ln in src.split("\n")
        if "FINALIZE_SYSTEM_PROMPT" in ln and "system" in ln
    ]
    # At least one line assigns system = FINALIZE_SYSTEM_PROMPT.
    assert lines_with_swap, "expected a `system = FINALIZE_SYSTEM_PROMPT` line"
    # The guarding condition above the assignment mentions at least
    # one of the three trigger flags.
    # (Check existence by scanning the full source — the specific
    # control-flow context is hard to assert precisely without parsing
    # AST, so we just ensure the trigger names exist in the same
    # function.)
    for trigger in ("_is_finalize", "_is_wizard_payload"):
        assert trigger in src, (
            f"agent source must reference {trigger!r} — the prompt "
            f"swap depends on it."
        )
