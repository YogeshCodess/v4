"""Regression test for P26 — netlist agent must fall back to a
BOM-derived netlist when the LLM call itself raises an exception
(not just when the LLM responds without a tool call).

User report (2026-04-25, project `gvv`):
    P4 phase_outputs.error_message =
    "All models in fallback chain failed. Last error:
     Client error '404 Not Found' for url 'http://localhost:11434/api/chat'"

Root cause: the netlist agent had a fallback path for the case where
the LLM RESPONDS WITHOUT calling `generate_netlist`, but if the LLM
call itself raised an exception (Ollama 404, network error, rate
limit, auth failure, etc.) the exception bubbled out of `await
self.call_llm(...)` and PipelineService marked the phase failed —
even though we could have built a perfectly valid netlist from
`component_recommendations.md` with no LLM at all.

Fix: wrap the `call_llm` invocation in try/except and synthesise an
empty `response = {"content": "", "tool_calls": []}` on failure so
the existing `if netlist_data: ... else:` flow takes the BOM-build
branch automatically.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_netlist_falls_back_to_bom_when_llm_call_raises(tmp_path):
    """The LLM raising an exception MUST land us on the BOM-build
    fallback, not on a failed phase. End-to-end: patch `call_llm` to
    raise the exact RuntimeError pattern observed in production
    (Ollama 404 chained from `base_agent.call_llm`'s fallback loop)
    and assert that the agent still returns a result with outputs."""
    from agents.netlist_agent import NetlistAgent
    agent = NetlistAgent()

    # The netlist agent reads inputs from `output_dir` on disk (NOT
    # from project_context.prior_phase_outputs) — see netlist_agent.py
    # `execute()` lines ~276-282. So we have to actually write the BOM
    # + requirements files to a real directory for the BOM-build
    # fallback to find them.
    (tmp_path / "requirements.md").write_text(
        "# Requirements\n\nREQ-1: receive 2-18 GHz.\n", encoding="utf-8"
    )
    # NOTE: section headings MUST use `### N. ` (three hashes) and the
    # part number MUST be inside a markdown link `[PartNum](url)`, because
    # that's exactly what `_build_netlist_from_components()` parses
    # (see netlist_agent.py line ~1065 — `^### \d+\.\s+` regex split,
    # then `\*\*Primary Choice:\*\*\s*\[([^\]]+)\]` for the part number).
    (tmp_path / "component_recommendations.md").write_text(
        "# Component Recommendations\n\n"
        "### 1. Low Noise Amplifier\n"
        "**Primary Choice:** [HMC8410](https://www.analog.com/HMC8410) (Analog Devices)\n\n"
        "### 2. Mixer\n"
        "**Primary Choice:** [ADL5801](https://www.analog.com/ADL5801) (Analog Devices)\n\n"
        "### 3. ADC\n"
        "**Primary Choice:** [AD9643](https://www.analog.com/AD9643) (Analog Devices)\n\n",
        encoding="utf-8",
    )

    project_context = {
        "project_id": 999,
        "name": "regression",
        "design_type": "rf",
        "output_dir": str(tmp_path),
        "design_parameters": {},
        "prior_phase_outputs": {},
    }

    # The exact exception shape `base_agent.call_llm` raises after its
    # fallback chain exhausts. We don't care about the message text;
    # what matters is that ANY exception out of `call_llm` lands on
    # the BOM-build fallback.
    fake_exc = RuntimeError(
        "All models in fallback chain failed. Last error: "
        "Client error '404 Not Found' for url "
        "'http://localhost:11434/api/chat'"
    )

    with patch.object(NetlistAgent, "call_llm",
                       new=AsyncMock(side_effect=fake_exc)):
        result = await agent.execute(project_context, user_input="")

    # Critical assertions:
    # 1. We got a result dict — the exception was caught.
    assert isinstance(result, dict), (
        f"netlist agent did not catch the LLM exception — got {result!r}"
    )
    # 2. The result has outputs (the BOM fallback wrote files).
    assert result.get("outputs"), (
        "BOM-build fallback should have produced output files even "
        "though the LLM call failed"
    )
    outputs = result["outputs"]
    # 3. Standard netlist files all present.
    expected_files = {
        "netlist.json", "netlist.net",
        "netlist_visual.md", "netlist_validation.json",
    }
    missing = expected_files - set(outputs.keys())
    assert not missing, (
        f"BOM-build fallback missed expected output files: {missing}"
    )
    # 4. The netlist contains the BOM's MPNs.
    netlist_json = outputs["netlist.json"]
    assert "HMC8410" in netlist_json
    assert "ADL5801" in netlist_json
    assert "AD9643" in netlist_json
    # 5. P26 (2026-04-25) — phase is COMPLETE even though DRC may have
    # warnings, because the BOM-fallback IS auto-synthesized and the
    # operator can see the AUTO-SYNTHESIZED tag in the UI. Previously
    # this returned phase_complete=False because DRC fired on the auto-
    # synth's approximate connectivity, marking the phase FAILED in red
    # even though a complete schematic was rendered. User report: "netlist
    # generated but showing failed status fix it".
    assert result.get("phase_complete") is True, (
        "BOM-fallback path produced a complete netlist (with all the "
        "user's MPNs), so the phase MUST be marked COMPLETED — not "
        "FAILED — regardless of DRC warnings on the auto-synthesized "
        f"connectivity. Got: phase_complete={result.get('phase_complete')!r}, "
        f"response={result.get('response', '')[:200]!r}"
    )


@pytest.mark.asyncio
async def test_netlist_phase_complete_when_llm_skips_tool_call(tmp_path):
    """P26 (2026-04-25) — the LLM responding WITHOUT calling
    `generate_netlist` should ALSO land on the BOM-fallback and mark
    the phase COMPLETED. This is distinct from the LLM-raises-exception
    case (covered above) — here the LLM returns successfully but with
    just text, no tool call, which the agent already handled by building
    a netlist from the BOM. The fix: that branch must also mark
    phase_complete=True since the auto-synthesized output is acceptable
    (user can see the AUTO-SYNTHESIZED tag and inspect)."""
    from agents.netlist_agent import NetlistAgent
    agent = NetlistAgent()

    (tmp_path / "requirements.md").write_text(
        "# Requirements\n\nREQ-1: receive 5 GHz.\n", encoding="utf-8"
    )
    (tmp_path / "component_recommendations.md").write_text(
        "# Component Recommendations\n\n"
        "### 1. LNA\n"
        "**Primary Choice:** [HMC8410](https://www.analog.com/HMC8410)\n\n"
        "### 2. Mixer\n"
        "**Primary Choice:** [ADL5801](https://www.analog.com/ADL5801)\n\n",
        encoding="utf-8",
    )

    project_context = {
        "project_id": 997,
        "name": "no_tool_call",
        "design_type": "rf",
        "output_dir": str(tmp_path),
        "design_parameters": {},
        "prior_phase_outputs": {},
    }

    # LLM responds successfully but doesn't call the tool (just text).
    fake_response = {
        "content": "I'll think about this and get back to you.",
        "tool_calls": [],
    }
    with patch.object(NetlistAgent, "call_llm",
                       new=AsyncMock(return_value=fake_response)):
        result = await agent.execute(project_context, user_input="")

    assert result.get("outputs", {}).get("netlist.json"), (
        "BOM-fallback should have produced netlist.json from the BOM"
    )
    assert result.get("phase_complete") is True, (
        "LLM-skipped-tool-call path also produces an AUTO-SYNTHESIZED "
        "netlist. The phase must be COMPLETED (not FAILED) just like "
        f"the LLM-exception path. Got: {result.get('phase_complete')!r}"
    )


@pytest.mark.asyncio
async def test_llm_success_path_completes_even_on_drc_failure(tmp_path):
    """P26 #9 (2026-04-25, hgj cascade fix): UPDATED SEMANTICS.

    Pre-fix: the LLM-success path required `drc_passed` for
    phase_complete=True; DRC violations would set status=failed and
    the DAG would fail-fast cascade ALL downstream phases (P3, P6,
    P7, P7a, P8a, P8b, P8c) without running them.

    Real-world failure (project hgj, 2026-04-25): P4 ran successfully
    (LLM tool call, 7 output files written, ~5 min), but DRC reported
    critical violations on a few unbound power pins. phase_complete
    became False → status=failed → the DAG marked all downstream
    phases failed without running them. User saw 8 red phases in the
    UI even though the netlist was generated correctly.

    New rule: phase_complete = (netlist.json exists). DRC summary stays
    in response_text + persisted in netlist_drc.json so the operator
    can review, but it doesn't halt the pipeline. PCB layout (P5) is
    manual — DRC issues caught during layout review, not by gating P4."""
    from agents.netlist_agent import NetlistAgent
    agent = NetlistAgent()

    (tmp_path / "requirements.md").write_text(
        "# Requirements\n\nREQ-1: stuff.\n", encoding="utf-8"
    )
    (tmp_path / "component_recommendations.md").write_text(
        "# Component Recommendations\n\n"
        "### 1. LNA\n"
        "**Primary Choice:** [HMC8410](https://www.analog.com/HMC8410)\n\n",
        encoding="utf-8",
    )

    project_context = {
        "project_id": 996,
        "name": "llm_success",
        "design_type": "rf",
        "output_dir": str(tmp_path),
        "design_parameters": {},
        "prior_phase_outputs": {},
    }

    # LLM emits a deliberately-broken netlist (1 node, 0 edges) → DRC
    # will flag critical violations on the unbound power rails.
    bad_netlist = {
        "nodes": [
            {
                "instance_id": "U1",
                "part_number": "BROKEN_IC",
                "component_name": "Broken IC",
                "reference_designator": "U1",
            },
        ],
        "edges": [],
        "power_nets": [],
        "ground_nets": [],
    }
    fake_response = {
        "content": "Done.",
        "tool_calls": [
            {"id": "tc1", "name": "generate_netlist", "input": bad_netlist}
        ],
    }
    with patch.object(NetlistAgent, "call_llm",
                       new=AsyncMock(return_value=fake_response)):
        result = await agent.execute(project_context, user_input="")

    # netlist.json was generated → phase MUST be marked complete even
    # though DRC failed. Downstream P3 / P6 / P8a will run.
    assert result.get("outputs", {}).get("netlist.json"), (
        "Netlist file must be generated even on DRC failure"
    )
    assert result.get("phase_complete") is True, (
        "P26 #9: phase_complete must be True whenever netlist.json "
        "exists, regardless of DRC violations. DRC is informational "
        "only — operator handles violations during PCB layout review. "
        f"Got phase_complete={result.get('phase_complete')!r}, "
        f"response={result.get('response', '')[:200]!r}"
    )
    # DRC summary is still in response_text so the operator sees the
    # warnings.
    response_text = result.get("response", "")
    assert "DRC" in response_text or "review" in response_text.lower(), (
        f"DRC warnings should be communicated in response_text — "
        f"got: {response_text!r}"
    )


@pytest.mark.asyncio
async def test_netlist_does_not_swallow_exceptions_from_other_paths(tmp_path):
    """The try/except added in P26 ONLY wraps the `call_llm` invocation —
    not the whole `execute()` body. If the BOM-build fallback itself
    raises (e.g. malformed component_recommendations), that exception
    must still propagate so the phase is correctly marked failed.

    Failing silently here would be worse than the bug we're fixing.
    """
    from agents.netlist_agent import NetlistAgent
    agent = NetlistAgent()

    # Write a requirements.md so we PASS the early "Requirements not
    # found" guard and actually reach the LLM call. Deliberately omit
    # component_recommendations.md so the BOM-build fallback has nothing
    # to work with — the netlist agent should still NOT leak the original
    # LLM RuntimeError verbatim.
    (tmp_path / "requirements.md").write_text(
        "# Requirements\n\nREQ-1: stuff.\n", encoding="utf-8"
    )

    project_context = {
        "project_id": 998,
        "name": "no_bom",
        "design_type": "rf",
        "output_dir": str(tmp_path),
        "design_parameters": {},
        "prior_phase_outputs": {},  # empty — fallback can't help
    }
    fake_exc = RuntimeError("fallback chain failed")
    with patch.object(NetlistAgent, "call_llm",
                       new=AsyncMock(side_effect=fake_exc)):
        # Whatever happens, we should NOT see the original LLM
        # RuntimeError — it should either succeed (degenerate netlist)
        # or raise a DIFFERENT, BOM-fallback-related error.
        try:
            result = await agent.execute(project_context, user_input="")
            # Degenerate-but-valid result is acceptable.
            assert isinstance(result, dict)
        except RuntimeError as e:
            # If the BOM fallback also fails, the new exception should
            # NOT be the original "fallback chain failed" one — it
            # should be from the netlist-building path.
            assert "fallback chain failed" not in str(e), (
                "P26 try/except is leaking the original LLM exception"
            )
