"""Tests for agents/rdt_psq_agent.py — pure markdown builders.

`_build_rdt_md` and `_build_psq_md` are deterministic: given a register /
sequence dict they produce a markdown document. Lock in the bits the
downstream documents rely on (address decoding tables, UART frame formats,
register count line).
"""
from __future__ import annotations

import pytest

from agents.rdt_psq_agent import RdtPsqAgent


@pytest.fixture
def agent():
    return RdtPsqAgent()


# ---------------------------------------------------------------------------
# _build_rdt_md
# ---------------------------------------------------------------------------

def test_rdt_md_includes_project_name_heading(agent):
    md = agent._build_rdt_md({"registers": []}, "MyProject")
    assert "MyProject" in md
    assert md.startswith("# Register Description Table")


def test_rdt_md_reports_register_count(agent):
    md = agent._build_rdt_md({"registers": [{"name": "A", "address": "0x0000"}]}, "P")
    assert "Total registers:** 1" in md


def test_rdt_md_always_includes_address_decoding_table(agent):
    """Downstream FPGA generation depends on this table shape — lock it in."""
    md = agent._build_rdt_md({"registers": []}, "P")
    assert "Register Address Decoding" in md
    assert "R/W#" in md
    assert "BASE_ADDR" in md
    assert "OFFSET" in md


def test_rdt_md_always_includes_base_address_map(agent):
    md = agent._build_rdt_md({"registers": []}, "P")
    assert "Board Information" in md
    assert "Communication & Interface" in md
    assert "RF / Phase Control" in md


def test_rdt_md_always_includes_uart_frame_formats(agent):
    md = agent._build_rdt_md({"registers": []}, "P")
    assert "Single Register Write" in md
    assert "Single Register Read" in md
    assert "Bulk Write" in md
    assert "Bulk Read" in md
    # Wire-level markers
    assert "0x57" in md  # 'W'
    assert "0x52" in md  # 'R'
    assert "0x06" in md  # ACK


def test_rdt_md_emits_a_row_per_register_in_summary_table(agent):
    regs = [
        {"name": "CTRL",    "address": "0x0100", "reset_value": "0x0000", "description": "Control"},
        {"name": "STATUS",  "address": "0x0101", "reset_value": "0x0001", "description": "Status"},
    ]
    md = agent._build_rdt_md({"registers": regs}, "P")
    # Each register gets a table row with its address + name.
    assert "| `0x0100` | `CTRL` |" in md
    assert "| `0x0101` | `STATUS` |" in md


def test_rdt_md_uses_placeholder_when_address_missing(agent):
    md = agent._build_rdt_md(
        {"registers": [{"name": "BOGUS", "description": "no addr"}]},
        "P",
    )
    assert "0x????" in md
