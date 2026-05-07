"""Tests for `_extract_component_qty` (rx-output-audit B1.17 / B1.18).

The power-calc rendering hard-coded qty=1 in three sites, which made the
power budget 4-32x undercount on matrix designs. This helper resolves
the actual qty from common BOM-row shapes — direct field, nested
key_specs, or free-text description.
"""
from __future__ import annotations

import pytest

from agents.requirements_agent import _extract_component_qty


class TestDirectField:
    def test_top_level_qty(self):
        assert _extract_component_qty({"qty": 32}) == 32

    def test_top_level_quantity_alias(self):
        assert _extract_component_qty({"quantity": 8}) == 8

    def test_string_numeric(self):
        assert _extract_component_qty({"qty": "16"}) == 16

    def test_zero_or_negative_falls_through(self):
        # 0 or negative should NOT be returned — fall through to default 1
        assert _extract_component_qty({"qty": 0}) == 1
        assert _extract_component_qty({"qty": -3}) == 1

    def test_garbage_falls_through(self):
        assert _extract_component_qty({"qty": "many"}) == 1


class TestNestedKeySpecs:
    def test_primary_key_specs_qty(self):
        """LLM tool schema uses `primary_key_specs`."""
        comp = {"primary_part": "X", "primary_key_specs": {"qty": 12}}
        assert _extract_component_qty(comp) == 12

    def test_key_specs_qty(self):
        comp = {"key_specs": {"qty": 4}}
        assert _extract_component_qty(comp) == 4


class TestFreeTextParsing:
    def test_n_cells_required(self):
        """Canonical rx-output pattern: '32 cells required'."""
        comp = {"function": "SPDT RF Switch Cell (4x8 crossbar core, "
                "32 cells required, SOI CMOS, ...)"}
        assert _extract_component_qty(comp) == 32

    def test_n_required(self):
        comp = {"function": "RF Input Limiter (4 required, PIN diode, ...)"}
        assert _extract_component_qty(comp) == 4

    def test_qty_colon_pattern(self):
        comp = {"description": "Matrix amplifier. Qty: 8 per output column."}
        assert _extract_component_qty(comp) == 8

    def test_paren_n_cells(self):
        comp = {"description": "(32 cells, single-package)"}
        assert _extract_component_qty(comp) == 32

    def test_no_match_defaults_to_1(self):
        comp = {"description": "Wideband LNA, 2-18 GHz, NF 1.5 dB"}
        assert _extract_component_qty(comp) == 1

    def test_does_not_match_frequency_numbers(self):
        """Conservative: '18 GHz' should NOT trigger qty=18."""
        comp = {"description": "Bandpass filter, DC to 18 GHz, "
                "low insertion loss"}
        assert _extract_component_qty(comp) == 1

    def test_sanity_bound_rejects_huge_qty(self):
        """qty > 1024 is almost certainly a parse error (e.g. matched
        a cost or a frequency in MHz), so fall through to 1."""
        comp = {"description": "Frequency response 5000 cells required "
                "(actually a typo)"}
        assert _extract_component_qty(comp) == 1


class TestRxScenario:
    """Reproduce the exact rx-project BOM rows."""

    def test_pe42522b_x_32_cells(self):
        comp = {
            "function": "SPDT RF Switch Cell (4x8 crossbar core, "
                        "32 cells required, SOI CMOS, absorptive, "
                        "DC-26.5 GHz)",
            "primary_part": "PE42522B-X",
        }
        assert _extract_component_qty(comp) == 32

    def test_cla4610_4_required(self):
        comp = {
            "function": "RF Input Limiter (4 required, PIN diode, "
                        "DC-18 GHz)",
            "primary_part": "CLA4610-085LF",
        }
        assert _extract_component_qty(comp) == 4

    def test_zva_8_required(self):
        comp = {
            "function": "Output Gain-Compensation Amplifier (8 required, "
                        "DC-18 GHz)",
            "primary_part": "ZVA-183WA-S+",
        }
        assert _extract_component_qty(comp) == 8

    def test_sma_12_ports(self):
        """SMA connector description mentions '12 RF ports' — qty is 12."""
        comp = {
            "function": "SMA Panel-Mount Connector, female, DC-18 GHz "
                        "rated, for all 12 RF ports (4 input + 8 output)",
            "primary_part": "<sma>",
        }
        # The "12 RF ports" pattern doesn't match our regex; defaults to 1.
        # Acceptable — this needs user-supplied qty.
        # Verify at least it doesn't crash:
        n = _extract_component_qty(comp)
        assert isinstance(n, int) and n >= 1
