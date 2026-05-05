"""Tests for agents/sbom_generator.py — CycloneDX SBOM builder.

Targets the pure helpers (`_parse_components`, `_normalize_component`,
`_build_sbom_manually`, `_build_sbom_summary`) and the end-to-end
`generate_sbom` with an empty and a populated BOM.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.sbom_generator import (
    _build_sbom_manually,
    _build_sbom_summary,
    _normalize_component,
    _parse_components,
    generate_sbom,
)


# ---------------------------------------------------------------------------
# _parse_components
# ---------------------------------------------------------------------------

def test_parse_components_extracts_markdown_table_rows():
    md = """
| Part    | Vendor  | Description         |
|---------|---------|---------------------|
| STM32F4 | STMicro | ARM Cortex-M4 MCU   |
| LM7805  | TI      | 5V linear regulator |
"""
    parts = _parse_components(md)
    names = [p["name"] for p in parts]
    assert "STM32F4" in names
    assert "LM7805" in names


def test_parse_components_skips_header_separator_rows():
    md = (
        "| Component | Vendor | Description |\n"
        "|-----------|--------|-------------|\n"
        "| STM32F4   | STMicro| MCU         |\n"
    )
    parts = _parse_components(md)
    names = [p["name"] for p in parts]
    assert "Component" not in names
    assert "---" not in names
    assert "STM32F4" in names


def test_parse_components_falls_back_to_bullet_list():
    md = (
        "- STM32F407 (STMicro): ARM Cortex-M4 MCU\n"
        "- LM7805 (TI): Linear regulator 5V\n"
    )
    parts = _parse_components(md)
    assert {p["name"] for p in parts} == {"STM32F407", "LM7805"}


def test_parse_components_deduplicates_case_insensitive():
    md = (
        "| STM32 | ST | one |\n"
        "| stm32 | ST | dup |\n"
    )
    parts = _parse_components(md)
    names_lower = [p["name"].lower() for p in parts]
    assert names_lower.count("stm32") == 1


def test_parse_components_caps_at_100_rows():
    rows = "\n".join(f"| PART{i:04d} | Vendor | desc |" for i in range(150))
    md = "| Part | Vendor | Description |\n|---|---|---|\n" + rows
    parts = _parse_components(md)
    assert len(parts) <= 100


# ---------------------------------------------------------------------------
# _normalize_component
# ---------------------------------------------------------------------------

def test_normalize_component_classifies_firmware():
    c = _normalize_component("bootloader", "Vendor", "embedded firmware image")
    assert c["type"] == "firmware"


def test_normalize_component_classifies_library():
    c = _normalize_component("libfoo", "Vendor", "shared SDK library")
    assert c["type"] == "library"


def test_normalize_component_defaults_to_hardware():
    c = _normalize_component("STM32F4", "STMicro", "ARM MCU")
    assert c["type"] == "hardware"


def test_normalize_component_assigns_unknown_vendor_when_missing():
    c = _normalize_component("X", "", "desc")
    assert c["vendor"] == "Unknown"


def test_normalize_component_issues_bom_ref_uuid():
    c = _normalize_component("X", "v", "d")
    # UUID4 string shape
    assert len(c["bom_ref"]) == 36
    assert c["bom_ref"].count("-") == 4


# ---------------------------------------------------------------------------
# _build_sbom_manually
# ---------------------------------------------------------------------------

def test_build_sbom_manually_returns_valid_cyclonedx_json():
    comps = [
        _normalize_component("STM32F4", "STMicro", "MCU"),
        _normalize_component("LM7805", "TI", "Linear reg"),
    ]
    sbom_str = _build_sbom_manually(comps, "TestRx")
    sbom = json.loads(sbom_str)

    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.4"
    assert sbom["serialNumber"].startswith("urn:uuid:")
    assert sbom["metadata"]["component"]["name"] == "TestRx"
    assert len(sbom["components"]) == 2
    for c in sbom["components"]:
        assert c["purl"].startswith("pkg:generic/")


# ---------------------------------------------------------------------------
# _build_sbom_summary
# ---------------------------------------------------------------------------

def test_build_sbom_summary_contains_counts_and_rows():
    comps = [_normalize_component("A", "V", "hardware")]
    md = _build_sbom_summary(comps, "Proj")
    assert "Proj" in md
    assert "Total Components" in md
    assert "| A | V |" in md


# ---------------------------------------------------------------------------
# generate_sbom (end-to-end)
# ---------------------------------------------------------------------------

def test_generate_sbom_writes_json_and_summary_files(tmp_path: Path):
    md = "| STM32F4 | STMicro | MCU |\n| LM7805 | TI | 5V reg |\n"
    result = generate_sbom(
        project_name="E2E",
        output_dir=tmp_path,
        components_text=md,
    )
    # Files produced
    assert (tmp_path / "sbom.json").exists()
    assert (tmp_path / "sbom_summary.md").exists()
    # Return dict shape
    assert result["component_count"] >= 1
    payload = json.loads((tmp_path / "sbom.json").read_text("utf-8"))
    assert payload["bomFormat"] == "CycloneDX"


def test_generate_sbom_handles_empty_components_gracefully(tmp_path: Path):
    result = generate_sbom(
        project_name="Empty",
        output_dir=tmp_path,
        components_text="(nothing here)",
    )
    assert result["component_count"] == 0
    # Still emits valid JSON with 0 components
    payload = json.loads((tmp_path / "sbom.json").read_text("utf-8"))
    assert payload.get("components", []) == []
