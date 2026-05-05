"""
Critic agent — B2.5.

Runs AFTER the primary P1 agent has produced a design, BEFORE the requirements
are locked. Uses the fallback-tier LLM (e.g. DeepSeek-V3 if the primary was
GLM-4.7, or vice-versa) to re-read the generated design summary and flag any
factual disagreements. Any non-empty disagreement becomes a `medium` severity
AuditIssue in the category `model_disagreement`, which the red-team audit
merges into its combined AuditReport.

Design note: we keep this as a stand-alone module rather than folding it into
`red_team_audit.py` because the critic talks to an LLM (expensive, non-
deterministic) while the red-team is pure-python deterministic checks. Separate
files = separable tests.

Invocation contract
-------------------
    from agents.critic_agent import run_critic
    issues = await run_critic(
        design_summary="Full P1 BOM + cascade + architecture…",
        base_agent=my_base_agent_instance,
        fallback_model="deepseek-chat",
    )

If the fallback LLM is unreachable (air-gap, no key), `run_critic` returns an
empty list — we never block the pipeline because the critic cannot run.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from domains._schema import AuditIssue

logger = logging.getLogger(__name__)


_CRITIC_SYSTEM_PROMPT = (
    "You are an independent senior RF systems engineer reviewing a design that "
    "was produced by another AI agent. Your ONLY job is to list FACTUAL "
    "disagreements you have with the design — cascade numbers that look wrong, "
    "component selections that don't match the stated specs, impossible specs, "
    "non-existent standard citations, or violations of basic RF principles "
    "(e.g. LNA NF > system NF target, IIP3 lower than the dynamic-range claim).\n"
    "\n"
    "RULES:\n"
    "1. If you agree with everything, reply with EXACTLY the single word 'AGREED'.\n"
    "2. Otherwise, list each disagreement on its own line prefixed with 'DISAGREE: '. "
    "Keep each line under 30 words. Cite the specific number or claim you dispute.\n"
    "3. NEVER invent new concerns — only challenge things explicitly in the design.\n"
    "4. NEVER produce praise, preamble, or closing remarks. One line per disagreement "
    "or one 'AGREED' and nothing else.\n"
)


_DISAGREE_LINE = re.compile(r"^\s*DISAGREE\s*:\s*(.+?)\s*$", re.IGNORECASE)


def parse_critic_response(text: str) -> list[str]:
    """Extract the disagreement lines from a critic LLM response."""
    if not text:
        return []
    stripped = text.strip()
    if stripped.upper() == "AGREED":
        return []
    out: list[str] = []
    for line in stripped.splitlines():
        m = _DISAGREE_LINE.match(line)
        if m:
            out.append(m.group(1).strip())
    # If no structured DISAGREE lines matched but the response is non-empty and
    # is NOT just "agreed", treat the whole response as a single disagreement —
    # safer than silently ignoring a model that didn't follow format.
    if not out and stripped and stripped.upper() != "AGREED":
        # Strip obvious preamble-sentences that look like agreement.
        if any(kw in stripped.lower() for kw in ("agree", "no issues", "looks fine")):
            return []
        out.append(stripped.splitlines()[0][:200])
    return out


def disagreements_to_issues(
    disagreements: list[str],
    phase_id: str = "P1",
) -> list[AuditIssue]:
    """Convert raw critic strings into structured AuditIssue records."""
    issues: list[AuditIssue] = []
    for idx, d in enumerate(disagreements):
        if not d.strip():
            continue
        issues.append(AuditIssue(
            severity="medium",
            category="model_disagreement",
            location=f"{phase_id}.critic[{idx}]",
            detail=d.strip(),
            suggested_fix=(
                "Re-check this claim against the cascade validator / component "
                "DB / standards DB. If the critic is right, revise the design. "
                "If the critic is wrong, document why in the lock notes."
            ),
        ))
    return issues


async def run_critic(
    design_summary: str,
    base_agent: Any,
    fallback_model: Optional[str] = None,
    max_tokens: int = 1024,
) -> list[AuditIssue]:
    """Run the critic pass and return a list of AuditIssue records.

    Parameters
    ----------
    design_summary:
        Prose summary of the P1 output — BOM, cascade numbers, architecture,
        citations, notable design choices. Max ~3 K tokens is plenty.
    base_agent:
        Any object with an async `call_llm(messages=..., system=..., model=...,
        max_tokens=...)` method that matches `BaseAgent.call_llm`. Keeping the
        dependency duck-typed makes the critic trivial to unit test.
    fallback_model:
        Explicit fallback model id (e.g. "deepseek-chat"). If None, the critic
        picks the first member of `base_agent.fallback_chain` that differs from
        `base_agent.model`.

    Returns
    -------
    list[AuditIssue]:
        Zero or more `model_disagreement` issues at medium severity.
    """
    if not design_summary or not design_summary.strip():
        return []

    # Choose a model distinct from the primary.
    critic_model = fallback_model
    if critic_model is None:
        chain = list(getattr(base_agent, "fallback_chain", []) or [])
        primary = getattr(base_agent, "model", None)
        for m in chain:
            if m and m != primary:
                critic_model = m
                break
    if critic_model is None:
        logger.info("critic.no_fallback_model_available — skipping critic pass")
        return []

    messages = [{
        "role": "user",
        "content": (
            "Review the following P1 hardware-pipeline design for factual "
            "disagreements:\n\n" + design_summary.strip() + "\n\n"
            "Reply per the rules in your system prompt."
        ),
    }]

    try:
        resp = await base_agent.call_llm(
            messages=messages,
            system=_CRITIC_SYSTEM_PROMPT,
            model=critic_model,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("critic.call_failed model=%s: %s", critic_model, exc)
        return []

    text = (resp or {}).get("content", "") or ""
    disagreements = parse_critic_response(text)
    issues = disagreements_to_issues(disagreements)
    logger.info("critic.done model=%s disagreements=%d", critic_model, len(issues))
    return issues
