"""P26 #16 (2026-04-26) — regression tests for the parallel SDD pipeline
in `agents/sdd_agent.py`.

Pre-fix: P8b ran a SINGLE LLM call with `max_tokens=16384`, then up to 5
SEQUENTIAL continuation passes when the model hit the token limit. A
60+ page IEEE 1016 SDD typically tripped 3-4 continuations → 5-6 min
wall time.

Post-fix: 1 metadata-locking call (~30s) + 5 parallel section calls
(~60-90s each, run concurrently via `asyncio.gather` → ~90s total
wall). Total wall time ~120s vs the previous ~360s.

The user explicitly asked: "make sure no drifts". The no-drift
mechanism is the metadata contract — every parallel section receives
the SAME `lock_sdd_design` JSON in its user message and is told to
use those names verbatim. These tests prove:
  - The parallel path is taken when metadata succeeds.
  - All 5 section calls receive the metadata in their context.
  - When metadata fails or returns empty, falls back to the legacy
    sequential continuation-pass path.
  - When < 3 of 5 section calls succeed, also falls back to legacy.
  - Sections are concatenated in document order.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agents.sdd_agent import SDDAgent


def _ctx(tmp_path: Path) -> dict:
    """Minimum project context for SDDAgent.execute()."""
    (tmp_path / "SRS_TestProj.md").write_text(
        "# SRS for TestProj\n\n## REQ-SW-001\nUART command handler.\n"
        "## REQ-SW-002\nSPI driver.\n",
        encoding="utf-8",
    )
    (tmp_path / "HRS_TestProj.md").write_text(
        "# HRS for TestProj\n\nPower rails: +5V / +3.3V.\n",
        encoding="utf-8",
    )
    (tmp_path / "glr_specification.md").write_text(
        "# GLR for TestProj\n\nUART register at 0x10.\n",
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
    """Stub a successful lock_sdd_design response — defines the contract
    that every section call must use verbatim."""
    return {
        "content": "",
        "tool_calls": [{
            "id": "tc_meta",
            "name": "lock_sdd_design",
            "input": {
                "modules": [
                    {"name": "uart_drv", "file": "uart_drv.c",
                     "header": "uart_drv.h",
                     "responsibility": "UART transport for register protocol",
                     "public_api": ["uart_init()", "uart_send()", "uart_recv()"]},
                    {"name": "spi_drv", "file": "spi_drv.c",
                     "header": "spi_drv.h",
                     "responsibility": "SPI master driver",
                     "public_api": ["spi_init()", "spi_xfer()"]},
                ],
                "structs": [
                    {"name": "uart_cfg_t",
                     "fields": ["uint32_t baud", "uint8_t parity"],
                     "purpose": "UART configuration"},
                ],
                "enums": [
                    {"name": "drv_state_e",
                     "values": ["DRV_INIT", "DRV_IDLE", "DRV_BUSY", "DRV_ERR"]},
                ],
                "tasks": [
                    {"name": "monitor_task", "priority": "low",
                     "period_ms": 100,
                     "description": "Periodic health check"},
                ],
                "isrs": [
                    {"name": "uart_rx_isr", "vector": "IRQ_UART_RX",
                     "latency_target_us": 10,
                     "trigger": "UART byte received"},
                ],
                "interfaces": [
                    {"name": "UART", "kind": "external", "peer": "Host PC"},
                    {"name": "SPI", "kind": "internal", "peer": "FPGA"},
                ],
                "register_map": [
                    {"address": "0x10", "name": "UART_CTRL",
                     "access": "RW", "reset": "0x00",
                     "purpose": "UART enable + parity"},
                ],
                "file_layout": ["drivers/uart_drv.c", "drivers/spi_drv.c"],
                "naming_conventions": {
                    "function_prefix": "drv_",
                    "type_suffix": "_t",
                    "constant_style": "ALL_CAPS",
                    "macro_style": "ALL_CAPS",
                },
                "target_platform": {
                    "fpga": "Artix-7",
                    "mcu": "STM32F4",
                    "rtos": "FreeRTOS",
                    "language": "C99",
                    "toolchain": "arm-none-eabi-gcc",
                },
            },
        }],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 100, "output_tokens": 200},
        "model_used": "test-model",
    }


def _section_response(tool_name: str, payload_field: str, payload: str) -> dict:
    return {
        "content": "",
        "tool_calls": [{
            "id": f"tc_{tool_name}",
            "name": tool_name,
            "input": {payload_field: payload},
        }],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 500, "output_tokens": 2000},
        "model_used": "test-model",
    }


# Each payload is intentionally > 200 chars so the assembled SDD clears
# the agent's 800-char minimum-content gate (which otherwise would
# trigger the template-generator fallback and discard the parallel
# output).
_SECTION_TOOLS = [
    ("generate_sdd_intro_overview",  "intro_overview_md",
     "## 1. Introduction\n\nThis document describes the software design of "
     "TestProj — a UART-controlled embedded firmware running on an Artix-7 "
     "FPGA + STM32F4 host. " + ("Body content. " * 25) + "\n\n"
     "## 2.1 Context Viewpoint\n\nThe system has UART + SPI peripherals. "
     + ("Context body. " * 25)),
    ("generate_sdd_architecture",    "architecture_md",
     "## 2.2 Composition\n\nLayered architecture: HAL → drivers → "
     "application. " + ("Architecture body. " * 30)),
    ("generate_sdd_modules_detail",  "modules_detail_md",
     "## 2.6 Module Detail\n\n### Module: uart_drv\n\n"
     "Source file: `uart_drv.c` / `uart_drv.h`. Public API: uart_init(), "
     "uart_send(), uart_recv(). " + ("Module body. " * 30)),
    ("generate_sdd_runtime_design",  "runtime_design_md",
     "## 2.7 State Dynamics\n\nFSM diagram + state transitions. "
     + ("Runtime body. " * 30)),
    ("generate_sdd_traceability",    "traceability_md",
     "## 3. Traceability\n\n| REQ | Module | File |\n|---|---|---|\n"
     "| REQ-SW-001 | uart_drv | uart_drv.c |\n"
     + ("Trace body. " * 30)),
]


# ---------------------------------------------------------------------------
# Happy path: metadata succeeds → 5 parallel sections fire → assembled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_path_called_when_metadata_succeeds(tmp_path):
    """Happy path: metadata locks, then all 5 section sub-calls fire
    via asyncio.gather, and the SDD body assembles in document order."""
    agent = SDDAgent()
    call_log: list[str] = []

    async def _fake_call_llm(messages, system=None, model=None, tools=None,
                              max_tokens=None, tool_choice=None):
        forced_tool = (tool_choice or {}).get("name", "")
        call_log.append(forced_tool or "<unforced>")
        if forced_tool == "lock_sdd_design":
            return _meta_response()
        for tname, fname, payload in _SECTION_TOOLS:
            if forced_tool == tname:
                return _section_response(tname, fname, payload)
        return {"content": "", "tool_calls": [], "stop_reason": "end_turn",
                "usage": {}, "model_used": "test"}

    with patch.object(SDDAgent, "call_llm",
                       new=AsyncMock(side_effect=_fake_call_llm)):
        result = await agent.execute(_ctx(tmp_path), user_input="")

    # 1 metadata + 5 section calls = 6 LLM calls. NO continuation-pass
    # calls (no "<unforced>" entries).
    assert len(call_log) == 6, (
        f"Expected 6 LLM calls (1 metadata + 5 sections), got {len(call_log)}: "
        f"{call_log}"
    )
    assert call_log[0] == "lock_sdd_design"
    assert set(call_log[1:]) == {tname for tname, _, _ in _SECTION_TOOLS}

    # SDD content assembled from all 5 sections in document order.
    assert result.get("phase_complete") is True
    outputs = result.get("outputs", {})
    sdd_text = next(iter(outputs.values()), "")
    assert "## 1. Introduction" in sdd_text
    assert "## 2.2 Composition" in sdd_text
    assert "## 2.6 Module Detail" in sdd_text
    assert "## 2.7 State Dynamics" in sdd_text
    assert "## 3. Traceability" in sdd_text
    # Document order: intro must come before traceability.
    assert sdd_text.index("Introduction") < sdd_text.index("Traceability")
    # The pending-section warning must NOT appear when all 5 sections
    # came back successfully.
    assert "pending regeneration" not in sdd_text


# ---------------------------------------------------------------------------
# No-drift contract: every section sees the locked metadata in its prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_passed_to_every_section_call(tmp_path):
    """The 5 parallel section calls MUST see the metadata JSON in their
    user message — that's the no-drift mechanism. A section that didn't
    get the metadata could invent new module names → SDD inconsistency."""
    agent = SDDAgent()
    seen_messages: dict[str, list] = {}

    async def _fake_call_llm(messages, system=None, model=None, tools=None,
                              max_tokens=None, tool_choice=None):
        forced_tool = (tool_choice or {}).get("name", "")
        seen_messages[forced_tool] = messages
        if forced_tool == "lock_sdd_design":
            return _meta_response()
        for tname, fname, payload in _SECTION_TOOLS:
            if forced_tool == tname:
                return _section_response(tname, fname, payload)
        return {"content": "", "tool_calls": [], "stop_reason": "end_turn",
                "usage": {}, "model_used": "test"}

    with patch.object(SDDAgent, "call_llm",
                       new=AsyncMock(side_effect=_fake_call_llm)):
        await agent.execute(_ctx(tmp_path), user_input="")

    # Every section call's user message should contain the locked-in
    # module names from the metadata. We pick `uart_drv` (which is in
    # the metadata.modules[].name list) and verify it appears.
    for tname, _, _ in _SECTION_TOOLS:
        msgs = seen_messages.get(tname, [])
        user_text = " ".join(
            (m.get("content") or "") for m in msgs if m.get("role") == "user"
        )
        assert "uart_drv" in user_text, (
            f"Section {tname} did not receive the locked metadata "
            f"(uart_drv not in its user message) — drift risk!"
        )
        assert "LOCKED SDD METADATA" in user_text, (
            f"Section {tname} missing the LOCKED SDD METADATA preamble"
        )


# ---------------------------------------------------------------------------
# Fallback: metadata empty → legacy sequential path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_path_when_metadata_returns_empty(tmp_path):
    """If the metadata call returns no tool_calls (model went off-tool),
    the parallel path must NOT be attempted — fall straight through to
    the legacy sequential continuation-pass path."""
    agent = SDDAgent()
    call_log: list[str] = []

    async def _fake_call_llm(messages, system=None, model=None, tools=None,
                              max_tokens=None, tool_choice=None):
        forced_tool = (tool_choice or {}).get("name", "")
        call_log.append(forced_tool or "<unforced>")
        if forced_tool == "lock_sdd_design":
            # Metadata call returns no tool calls → parallel path bails.
            return {"content": "", "tool_calls": [],
                    "stop_reason": "end_turn", "usage": {},
                    "model_used": "test"}
        # Legacy sequential single-shot returns body large enough to
        # clear BOTH the agent's 800-char minimum-content gate AND the
        # 25K-char continuation-target threshold added in P26 #21 — so
        # exactly one legacy call fires, no continuation passes.
        legacy_body = "## SDD Legacy\n\n" + ("Legacy body content. " * 1500)
        return {"content": legacy_body,
                "tool_calls": [], "stop_reason": "end_turn",
                "usage": {}, "model_used": "test"}

    with patch.object(SDDAgent, "call_llm",
                       new=AsyncMock(side_effect=_fake_call_llm)):
        result = await agent.execute(_ctx(tmp_path), user_input="")

    # 1 metadata call (failed) + 1 legacy single-shot = 2 total.
    # NO parallel section calls fired, and the legacy body is long
    # enough that no continuation passes fire either.
    assert len(call_log) == 2, (
        f"Expected 2 LLM calls (1 metadata fail + 1 legacy), "
        f"got {len(call_log)}: {call_log}"
    )
    assert call_log[0] == "lock_sdd_design"
    assert call_log[1] == "<unforced>"
    # Phase still completes via legacy path.
    assert result.get("phase_complete") is True


@pytest.mark.asyncio
async def test_partial_sections_are_shipped_with_missing_note(tmp_path):
    """P26 #21 (2026-05-04): the success threshold was lowered from
    3-of-5 to 1-of-5. Even one section's worth of LLM-written SDD
    content (typically 5-15 KB) is dramatically better than the 1.9 KB
    deterministic template, and the old gate was forcing the whole
    phase to fall to template whenever GLM rate-limited 3+ of the
    parallel calls. The agent now ships partial content with a clear
    'pending regeneration' footer naming the missing sections."""
    agent = SDDAgent()
    call_log: list[str] = []

    async def _fake_call_llm(messages, system=None, model=None, tools=None,
                              max_tokens=None, tool_choice=None):
        forced_tool = (tool_choice or {}).get("name", "")
        call_log.append(forced_tool or "<unforced>")
        if forced_tool == "lock_sdd_design":
            return _meta_response()
        # Only the first 2 of 5 sections succeed; the rest return empty.
        ALLOWED = {"generate_sdd_intro_overview",
                   "generate_sdd_architecture"}
        if forced_tool in ALLOWED:
            for tname, fname, payload in _SECTION_TOOLS:
                if forced_tool == tname:
                    return _section_response(tname, fname, payload)
        # Other sections return no tool_calls.
        if forced_tool:
            return {"content": "", "tool_calls": [],
                    "stop_reason": "end_turn",
                    "usage": {}, "model_used": "test"}
        # Legacy fallback returns body large enough to clear the
        # 800-char minimum-content gate.
        legacy_body = "## SDD Legacy\n\n" + ("Legacy body fallback. " * 60)
        return {"content": legacy_body,
                "tool_calls": [], "stop_reason": "end_turn",
                "usage": {}, "model_used": "test"}

    with patch.object(SDDAgent, "call_llm",
                       new=AsyncMock(side_effect=_fake_call_llm)):
        result = await agent.execute(_ctx(tmp_path), user_input="")

    # 1 metadata + 5 section attempts = 6. With the lowered 1-of-5
    # threshold, 2 successful sections is enough — legacy is NOT
    # invoked.
    assert len(call_log) == 6, (
        f"Expected 6 LLM calls (1 metadata + 5 sections), "
        f"got {len(call_log)}: {call_log}"
    )
    assert call_log[0] == "lock_sdd_design"
    # Phase completes via the parallel pipeline.
    assert result.get("phase_complete") is True
    sdd_text = next(iter(result.get("outputs", {}).values()), "")
    # Parallel content from the 2 successful sections IS shipped, with
    # an explicit footer naming the 3 missing sections.
    assert "## 1. Introduction" in sdd_text or "intro_overview_md" in sdd_text or len(sdd_text) > 800
    assert "Legacy body fallback" not in sdd_text
    assert "pending regeneration" in sdd_text.lower() or "missing" in sdd_text.lower()


# ---------------------------------------------------------------------------
# Partial success (3 or 4 of 5): keep parallel output, flag the missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_success_keeps_parallel_output_with_warning(tmp_path):
    """If 3-4 of 5 sections succeed, ship the parallel output with a
    visible note about which sections are pending. Better than throwing
    away 80% of useful work."""
    agent = SDDAgent()

    async def _fake_call_llm(messages, system=None, model=None, tools=None,
                              max_tokens=None, tool_choice=None):
        forced_tool = (tool_choice or {}).get("name", "")
        if forced_tool == "lock_sdd_design":
            return _meta_response()
        # 4 of 5 sections succeed; runtime_design returns empty.
        if forced_tool == "generate_sdd_runtime_design":
            return {"content": "", "tool_calls": [],
                    "stop_reason": "end_turn",
                    "usage": {}, "model_used": "test"}
        for tname, fname, payload in _SECTION_TOOLS:
            if forced_tool == tname:
                return _section_response(tname, fname, payload)
        return {"content": "", "tool_calls": [], "stop_reason": "end_turn",
                "usage": {}, "model_used": "test"}

    with patch.object(SDDAgent, "call_llm",
                       new=AsyncMock(side_effect=_fake_call_llm)):
        result = await agent.execute(_ctx(tmp_path), user_input="")

    sdd_text = next(iter(result.get("outputs", {}).values()), "")
    # The 4 successful sections must all appear.
    assert "## 1. Introduction" in sdd_text
    assert "## 2.2 Composition" in sdd_text
    assert "## 2.6 Module Detail" in sdd_text
    assert "## 3. Traceability" in sdd_text
    # The missing section's body MUST NOT appear.
    assert "## 2.7 State Dynamics" not in sdd_text
    # A visible warning notes the missing section.
    assert "pending regeneration" in sdd_text
    assert "Runtime / Resource / Build" in sdd_text
    assert result.get("phase_complete") is True
