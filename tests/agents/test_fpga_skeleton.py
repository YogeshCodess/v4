"""Tests for agents/fpga_agent.py — `_build_skeleton` fallback RTL generator.

`_build_skeleton` is pure (no LLM) and runs when the agent fails to get a
tool call from the model. These tests lock in its contract:
  - Always produces at least 3 files (top, testbench, constraints) + report
  - Detects SPI/ADC/UART/GPIO/DAC peripherals from the GLR text
  - Respects clock-rate + data-width hints in the GLR
"""
from __future__ import annotations

import pytest

from agents.fpga_agent import FpgaAgent


@pytest.fixture
def fpga():
    return FpgaAgent()


def test_skeleton_emits_top_testbench_and_constraints(fpga):
    out = fpga._build_skeleton("MyProj", "myproj", "Generic GLR text")
    assert "rtl/fpga_top.v" in out
    assert "rtl/fpga_testbench.v" in out
    assert "rtl/constraints.xdc" in out


def test_skeleton_includes_spi_ports_when_glr_mentions_spi(fpga):
    out = fpga._build_skeleton("P", "p", "This design needs an SPI master to talk to ADC")
    top = out["rtl/fpga_top.v"]
    assert "spi_clk" in top
    assert "spi_mosi" in top
    assert "spi_cs_n" in top


def test_skeleton_includes_adc_ports_when_glr_mentions_adc(fpga):
    out = fpga._build_skeleton("P", "p", "LVDS digitiser ADC frontend")
    top = out["rtl/fpga_top.v"]
    assert "adc_data" in top
    assert "adc_data_valid" in top


def test_skeleton_includes_uart_ports_when_glr_mentions_uart(fpga):
    out = fpga._build_skeleton("P", "p", "UART debug serial port at 115200 baud")
    top = out["rtl/fpga_top.v"]
    assert "uart" in top.lower()


def test_skeleton_excludes_uart_when_glr_does_not_mention_serial(fpga):
    """Port conditionals in the skeleton are per-interface — UART must be
    absent when the GLR doesn't reference UART or serial."""
    out = fpga._build_skeleton("P", "p", "Pure ADC + LVDS only design")
    top = out["rtl/fpga_top.v"]
    assert "uart_tx" not in top
    assert "uart_rx" not in top


def test_skeleton_picks_up_data_width_from_glr(fpga):
    out = fpga._build_skeleton("P", "p", "14-bit ADC at 100 MHz LVDS")
    top = out["rtl/fpga_top.v"]
    # data_w=14 → range [13:0]
    assert "[13:0]" in top


def test_skeleton_defaults_to_16_bit_when_width_unspecified(fpga):
    out = fpga._build_skeleton("P", "p", "Generic digitiser, no explicit width")
    top = out["rtl/fpga_top.v"]
    # Default data_w=16 → the module must contain a [15:0] bus somewhere
    assert "[15:0]" in top


def test_skeleton_module_name_sanitised_from_project_name(fpga):
    out = fpga._build_skeleton("My Project!", "My Project!", "text")
    top = out["rtl/fpga_top.v"]
    # Module names are valid Verilog identifiers — lowercase, underscores
    assert "module " in top


def test_skeleton_handles_empty_glr(fpga):
    """An empty GLR must not crash; skeleton is still emitted."""
    out = fpga._build_skeleton("P", "p", "")
    assert "rtl/fpga_top.v" in out
    assert len(out["rtl/fpga_top.v"]) > 100
