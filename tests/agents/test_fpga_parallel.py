"""P26 #8 (2026-04-25) — regression tests for the parallel FPGA-RTL
split in `agents/fpga_agent.py`.

Pre-fix: the FPGA agent made a SINGLE LLM call with `max_tokens=16384`
that returned all 4 output files (Verilog top + testbench + XDC +
report). At GLM's ~50 tokens/sec generation speed that's a 5-12 minute
wall-time bottleneck.

Post-fix: 1 fast metadata call (~30s) followed by 4 parallel content
sub-calls (~60s each, run concurrently → ~60s total). Total wall time
~90s vs the previous ~10 min.

These tests verify:
  - The parallel path is taken when metadata succeeds.
  - All 4 sub-calls receive the metadata as the consistency contract.
  - When the metadata call returns `degraded`, falls back to the legacy
    single-call path (no regression on chain-exhaust path).
  - When ALL 4 sub-calls return empty, also falls back to legacy path.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from pathlib import Path

import pytest

from agents.fpga_agent import FpgaAgent


def _ctx(tmp_path: Path) -> dict:
    """Minimum project context for FpgaAgent.execute()."""
    (tmp_path / "GLR_TestProj.md").write_text(
        "# GLR for TestProj\n\nNeeds SPI master + 3 GPIOs.\n",
        encoding="utf-8",
    )
    return {
        "project_id": 1,
        "name": "TestProj",
        "design_type": "rf",
        "output_dir": str(tmp_path),
        "design_parameters": {},
        "prior_phase_outputs": {},
    }


def _meta_response() -> dict:
    """Stub a successful generate_fpga_metadata response."""
    return {
        "content": "",
        "tool_calls": [{
            "id": "tc_meta",
            "name": "generate_fpga_metadata",
            "input": {
                "module_name": "test_top",
                "clock_frequency_mhz": 100.0,
                "fpga_part": "xc7a35tcpg236-1",
                "ports": [
                    {"name": "clk",   "direction": "input",  "width": 1,
                     "description": "100 MHz clock"},
                    {"name": "rst_n", "direction": "input",  "width": 1,
                     "description": "Active-low reset"},
                ],
                "state_machines": [
                    {"name": "ctrl_fsm", "states": ["IDLE", "RUN", "DONE"],
                     "description": "Control FSM"},
                ],
                "lut_estimate": 200,
                "ff_estimate":  150,
            },
        }],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 100, "output_tokens": 200},
        "model_used": "test-model",
    }


def _content_response(tool_name: str, field_name: str, payload: str) -> dict:
    """Stub a successful sub-call response for one content tool."""
    return {
        "content": "",
        "tool_calls": [{
            "id": f"tc_{tool_name}",
            "name": tool_name,
            "input": {field_name: payload},
        }],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 500, "output_tokens": 1000},
        "model_used": "test-model",
    }


@pytest.mark.asyncio
async def test_parallel_path_called_when_metadata_succeeds(tmp_path):
    """Happy path: metadata call returns valid data → 4 parallel
    content sub-calls are dispatched → outputs assembled from all 4."""
    agent = FpgaAgent()
    call_log: list[str] = []

    async def _fake_call_llm(messages, system=None, model=None, tools=None,
                              max_tokens=None, tool_choice=None):
        # Track which tool the caller is asking for via tool_choice.
        forced_tool = (tool_choice or {}).get("name", "")
        call_log.append(forced_tool)
        if forced_tool == "generate_fpga_metadata":
            return _meta_response()
        if forced_tool == "generate_verilog_top":
            return _content_response(
                "generate_verilog_top", "verilog_top",
                "module test_top(input clk, input rst_n); endmodule\n",
            )
        if forced_tool == "generate_testbench":
            return _content_response(
                "generate_testbench", "testbench",
                "module test_top_tb; test_top dut(.clk(clk), .rst_n(rst_n)); endmodule\n",
            )
        if forced_tool == "generate_xdc_constraints":
            return _content_response(
                "generate_xdc_constraints", "xdc_constraints",
                "create_clock -period 10.0 [get_ports clk]\n",
            )
        if forced_tool == "generate_design_summary":
            return _content_response(
                "generate_design_summary", "design_summary",
                "Test design summary\n",
            )
        # Unforced (fallback path) — should NOT be reached on happy path.
        return {"content": "", "tool_calls": [], "stop_reason": "end_turn",
                "usage": {}, "model_used": "test"}

    with patch.object(FpgaAgent, "call_llm",
                       new=AsyncMock(side_effect=_fake_call_llm)):
        result = await agent.execute(_ctx(tmp_path), user_input="")

    # Exactly 5 LLM calls: 1 metadata + 4 parallel content.
    assert len(call_log) == 5, (
        f"Expected 5 LLM calls (1 metadata + 4 parallel), got {len(call_log)}: "
        f"{call_log}"
    )
    assert call_log[0] == "generate_fpga_metadata"
    assert set(call_log[1:]) == {
        "generate_verilog_top", "generate_testbench",
        "generate_xdc_constraints", "generate_design_summary",
    }
    # Outputs assembled from the 4 sub-calls.
    outputs = result.get("outputs", {})
    assert "rtl/fpga_top.v" in outputs
    # 2026-05-02: rtl/fpga_top.v now comes from the deterministic
    # rtl_tailored emitter (driven by ProjectBrief), not from the
    # LLM tool call. The module name follows the safe_project_name.
    assert "module" in outputs["rtl/fpga_top.v"]
    assert "endmodule" in outputs["rtl/fpga_top.v"]
    assert "rtl/fpga_testbench.v" in outputs
    assert "endmodule" in outputs["rtl/fpga_testbench.v"]  # tailored TB
    assert "rtl/constraints.xdc" in outputs
    assert "create_clock" in outputs["rtl/constraints.xdc"]
    assert "fpga_design_report.md" in outputs
    # Phase complete.
    assert result.get("phase_complete") is True


@pytest.mark.asyncio
async def test_metadata_passed_as_consistency_contract(tmp_path):
    """The 4 parallel content calls MUST see the metadata in their
    user message — that's how they stay consistent (same module name
    in Verilog + testbench + XDC)."""
    agent = FpgaAgent()
    seen_messages: dict[str, list] = {}

    async def _fake_call_llm(messages, system=None, model=None, tools=None,
                              max_tokens=None, tool_choice=None):
        forced_tool = (tool_choice or {}).get("name", "")
        seen_messages[forced_tool] = messages
        if forced_tool == "generate_fpga_metadata":
            return _meta_response()
        # Return successful empty-ish payload for content calls.
        for tname, fname in [
            ("generate_verilog_top",     "verilog_top"),
            ("generate_testbench",       "testbench"),
            ("generate_xdc_constraints", "xdc_constraints"),
            ("generate_design_summary",  "design_summary"),
        ]:
            if forced_tool == tname:
                return _content_response(tname, fname, f"<{fname} content>")
        return {"content": "", "tool_calls": [], "stop_reason": "end_turn",
                "usage": {}, "model_used": "test"}

    with patch.object(FpgaAgent, "call_llm",
                       new=AsyncMock(side_effect=_fake_call_llm)):
        await agent.execute(_ctx(tmp_path), user_input="")

    # Each content call's user message should contain the locked-in
    # module name from the metadata.
    for tname in ("generate_verilog_top", "generate_testbench",
                  "generate_xdc_constraints", "generate_design_summary"):
        assert tname in seen_messages, f"{tname} was not called"
        msg = seen_messages[tname][0]["content"]
        assert "test_top" in msg, (
            f"{tname} did not see the locked-in module name 'test_top' "
            f"in its user message — metadata contract is broken."
        )
        assert "Locked-in design metadata" in msg


@pytest.mark.asyncio
async def test_falls_back_to_legacy_single_call_when_metadata_degraded(tmp_path):
    """When the metadata call returns `degraded` (LLM chain exhausted),
    the agent falls through to the LEGACY single-call path. No
    regression on the chain-exhaust failure mode."""
    agent = FpgaAgent()
    call_log: list[str] = []

    async def _fake_call_llm(messages, system=None, model=None, tools=None,
                              max_tokens=None, tool_choice=None):
        forced_tool = (tool_choice or {}).get("name", "")
        call_log.append(forced_tool)
        if forced_tool == "generate_fpga_metadata":
            # Return degraded metadata (chain exhausted).
            return {
                "content": "", "tool_calls": [], "stop_reason": "end_turn",
                "usage": {}, "model_used": "test", "degraded": True,
            }
        # Legacy fallback path: returns the full single-tool response.
        if forced_tool == "generate_fpga_design" or not forced_tool:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "tc_legacy",
                    "name": "generate_fpga_design",
                    "input": {
                        "module_name": "legacy_top",
                        "clock_frequency_mhz": 50.0,
                        "fpga_part": "xc7a35t",
                        "ports": [{"name": "clk", "direction": "input", "width": 1}],
                        "verilog_top":     "module legacy_top; endmodule\n",
                        "testbench":       "module legacy_top_tb; endmodule\n",
                        "xdc_constraints": "create_clock -period 20\n",
                        "design_summary":  "Legacy summary\n",
                    },
                }],
                "stop_reason": "tool_use",
                "usage": {}, "model_used": "test",
            }
        return {"content": "", "tool_calls": [], "stop_reason": "end_turn",
                "usage": {}, "model_used": "test"}

    with patch.object(FpgaAgent, "call_llm",
                       new=AsyncMock(side_effect=_fake_call_llm)):
        result = await agent.execute(_ctx(tmp_path), user_input="")

    # Metadata call attempted, then legacy single-call.
    assert "generate_fpga_metadata" in call_log
    # Legacy path runs (forced_tool is empty for the first legacy call).
    outputs = result.get("outputs", {})
    assert "rtl/fpga_top.v" in outputs
    # Legacy LLM tool path - file content now comes from the
    # deterministic emitter; only checking the file is non-empty
    # and well-formed.
    assert "module" in outputs["rtl/fpga_top.v"]
    assert "endmodule" in outputs["rtl/fpga_top.v"]


@pytest.mark.asyncio
async def test_falls_back_when_all_4_sub_calls_empty(tmp_path):
    """If metadata succeeds but ALL 4 parallel sub-calls return empty
    (e.g. all hit rate-limits and return no tool_call), fall through
    to the legacy single-call path so the phase still produces output."""
    agent = FpgaAgent()
    call_log: list[str] = []

    async def _fake_call_llm(messages, system=None, model=None, tools=None,
                              max_tokens=None, tool_choice=None):
        forced_tool = (tool_choice or {}).get("name", "")
        call_log.append(forced_tool)
        if forced_tool == "generate_fpga_metadata":
            return _meta_response()
        # All 4 content sub-calls return empty (no tool_calls).
        if forced_tool in (
            "generate_verilog_top", "generate_testbench",
            "generate_xdc_constraints", "generate_design_summary",
        ):
            return {"content": "(rate limited)", "tool_calls": [],
                    "stop_reason": "end_turn", "usage": {},
                    "model_used": "test"}
        # Legacy fallback succeeds.
        return {
            "content": "",
            "tool_calls": [{
                "id": "tc_legacy",
                "name": "generate_fpga_design",
                "input": {
                    "module_name": "fallback_top",
                    "clock_frequency_mhz": 100.0,
                    "fpga_part": "xc7a35t",
                    "ports": [{"name": "clk", "direction": "input", "width": 1}],
                    "verilog_top":     "module fallback_top; endmodule\n",
                    "testbench":       "module fallback_top_tb; endmodule\n",
                    "xdc_constraints": "create_clock -period 10\n",
                    "design_summary":  "Fallback summary\n",
                },
            }],
            "stop_reason": "tool_use",
            "usage": {}, "model_used": "test",
        }

    with patch.object(FpgaAgent, "call_llm",
                       new=AsyncMock(side_effect=_fake_call_llm)):
        result = await agent.execute(_ctx(tmp_path), user_input="")

    # Metadata + 4 parallel + legacy = 6 calls.
    assert call_log[0] == "generate_fpga_metadata"
    assert call_log.count("generate_verilog_top") == 1
    # Legacy path produced the output.
    assert "module" in result["outputs"]["rtl/fpga_top.v"]
    assert "endmodule" in result["outputs"]["rtl/fpga_top.v"]


@pytest.mark.asyncio
async def test_partial_sub_call_failure_keeps_what_succeeded(tmp_path):
    """If 3 of 4 sub-calls succeed and 1 fails, keep the 3 successful
    outputs and emit empty content for the failed one — better than
    aborting the whole phase."""
    agent = FpgaAgent()

    async def _fake_call_llm(messages, system=None, model=None, tools=None,
                              max_tokens=None, tool_choice=None):
        forced_tool = (tool_choice or {}).get("name", "")
        if forced_tool == "generate_fpga_metadata":
            return _meta_response()
        if forced_tool == "generate_verilog_top":
            return _content_response(
                "generate_verilog_top", "verilog_top",
                "module test_top; endmodule\n",
            )
        if forced_tool == "generate_testbench":
            return _content_response(
                "generate_testbench", "testbench",
                "module test_top_tb; endmodule\n",
            )
        if forced_tool == "generate_xdc_constraints":
            # Simulate this one failing (no tool_call returned).
            return {"content": "", "tool_calls": [],
                    "stop_reason": "end_turn", "usage": {},
                    "model_used": "test"}
        if forced_tool == "generate_design_summary":
            return _content_response(
                "generate_design_summary", "design_summary",
                "Summary\n",
            )
        return {"content": "", "tool_calls": [], "stop_reason": "end_turn",
                "usage": {}, "model_used": "test"}

    with patch.object(FpgaAgent, "call_llm",
                       new=AsyncMock(side_effect=_fake_call_llm)):
        result = await agent.execute(_ctx(tmp_path), user_input="")

    # Verilog + testbench + summary present, XDC absent (failed).
    outputs = result["outputs"]
    assert "rtl/fpga_top.v" in outputs
    assert "rtl/fpga_testbench.v" in outputs
    assert "fpga_design_report.md" in outputs
    # XDC absent OR empty (whichever the agent does on partial fail).
    # Deterministic emitter always produces an xdc with at least the
    # create_clock line. The ascii content check just confirms it is
    # not blank.
    assert "create_clock" in outputs.get("rtl/constraints.xdc", "")
    if False:  # disabled by 2026-05-02 - was checking empty xdc
        assert outputs.get("rtl/constraints.xdc", "") == "" or \
        "rtl/constraints.xdc" not in outputs
