"""Tests for agents/critic_agent.py — B2.5.

The critic talks to an LLM in production; here we inject a fake `base_agent`
with a stubbed `call_llm` so the tests are deterministic and offline.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from agents.critic_agent import (
    disagreements_to_issues,
    parse_critic_response,
    run_critic,
)


def test_parse_agreed_means_no_disagreements():
    assert parse_critic_response("AGREED") == []
    assert parse_critic_response("  agreed  ") == []


def test_parse_multiline_disagree_lines():
    resp = (
        "DISAGREE: LNA NF of 4 dB exceeds the 3 dB system target.\n"
        "DISAGREE: Mixer IIP3 of -10 dBm violates SFDR of 75 dB.\n"
        "DISAGREE: MIL-STD-999X is not a real standard."
    )
    d = parse_critic_response(resp)
    assert len(d) == 3
    assert any("LNA NF" in x for x in d)
    assert any("MIL-STD-999X" in x for x in d)


def test_parse_empty_and_whitespace():
    assert parse_critic_response("") == []
    assert parse_critic_response("   ") == []


def test_disagreements_to_issues_maps_severity_category_location():
    issues = disagreements_to_issues(["X is wrong", "Y is wrong"], phase_id="P1")
    assert len(issues) == 2
    for i in issues:
        assert i.severity == "medium"
        assert i.category == "model_disagreement"
        assert i.location.startswith("P1.critic[")
        assert i.suggested_fix


def test_run_critic_returns_empty_when_model_agrees():
    calls: list[dict] = []

    class FakeAgent:
        model = "glm-4.7"
        fallback_chain = ["glm-4.7", "deepseek-chat"]

        async def call_llm(self, **kw: Any):
            calls.append(kw)
            return {"content": "AGREED"}

    issues = asyncio.run(run_critic(
        design_summary="design summary goes here",
        base_agent=FakeAgent(),
    ))
    assert issues == []
    assert calls[0]["model"] == "deepseek-chat"  # skipped the primary


def test_run_critic_emits_medium_issues_on_disagreement():
    class FakeAgent:
        model = "glm-4.7"
        fallback_chain = ["glm-4.7", "deepseek-chat"]

        async def call_llm(self, **kw: Any):
            return {"content": "DISAGREE: LNA NF exceeds system target."}

    issues = asyncio.run(run_critic(
        design_summary="some design", base_agent=FakeAgent(),
    ))
    assert len(issues) == 1
    assert issues[0].severity == "medium"
    assert issues[0].category == "model_disagreement"
    assert "LNA NF" in issues[0].detail


def test_run_critic_returns_empty_on_empty_summary():
    class FakeAgent:
        model = "x"
        fallback_chain = ["x", "y"]

        async def call_llm(self, **kw: Any):  # should NOT be called
            raise AssertionError("call_llm must not run for empty summary")

    issues = asyncio.run(run_critic("", base_agent=FakeAgent()))
    assert issues == []


def test_run_critic_tolerates_llm_failure():
    class FailingAgent:
        model = "x"
        fallback_chain = ["x", "y"]

        async def call_llm(self, **kw: Any):
            raise RuntimeError("all providers down")

    issues = asyncio.run(run_critic("design", base_agent=FailingAgent()))
    assert issues == []
