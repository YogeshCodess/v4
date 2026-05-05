"""Tests for services/p1_finalize.py — A1.2."""
from __future__ import annotations

import json

import pytest

from services.p1_finalize import (
    _collect_citations,
    _collect_parts,
    _infer_domain,
    audit_report_to_md,
    finalize_p1,
)


def test_infer_domain_from_design_type():
    assert _infer_domain("Radar Front-End", {}) == "radar"
    assert _infer_domain("UHF Tactical Comms", {}) == "communication"
    assert _infer_domain("EW / SIGINT Receiver", {}) == "ew"
    assert _infer_domain("SATCOM Ka-band", {}) == "satcom"


def test_infer_domain_falls_back_to_requirements_blob():
    reqs = {"project_summary": "A wideband SIGINT receiver for aircraft"}
    assert _infer_domain(None, reqs) == "ew"


def test_collect_citations_handles_dicts_and_tuples():
    cites = _collect_citations({
        "citations": [
            {"standard": "MIL-STD-461G", "clause": "RE102"},
            ("DO-160G", "Section 20"),
            {"standard": "", "clause": "skipme"},
        ],
    })
    assert ("MIL-STD-461G", "RE102") in cites
    assert ("DO-160G", "Section 20") in cites
    assert len(cites) == 2


def test_collect_parts_normalises_mpn_keys():
    parts = _collect_parts({
        "component_recommendations": [
            {"part_number": "ADL8107", "manufacturer": "ADI"},
            {"mpn": "TGA2578", "vendor": "Qorvo"},
            {"description": "no part number"},
        ],
    })
    assert [p["part_number"] for p in parts] == ["ADL8107", "TGA2578"]


def test_finalize_p1_produces_lock_and_audit():
    # Minimal tool-input mimicking the generate_requirements schema
    tool_input = {
        "project_summary": "2–18 GHz wideband EW receiver",
        "design_parameters": {
            "freq_range": "2-18 GHz",
            "total_gain_db": 40.0,
            "noise_figure_db": 3.5,
            "iip3_dbm_input": 5.0,
        },
        "requirements": [
            {"req_id": "REQ-HW-001", "title": "Instantaneous bandwidth >= 1 GHz",
             "priority": "Must have"},
        ],
        "architecture": "superheterodyne",
        "component_recommendations": [
            {"name": "LNA", "part_number": "ADL8107", "manufacturer": "ADI",
             "gain_db": 24.0, "nf_db": 1.8, "iip3_dbm": 30.0, "kind": "LNA",
             "datasheet_url": "https://www.analog.com/en/products/adl8107.html"},
            {"name": "Mixer", "part_number": "HMC1049LP5E", "manufacturer": "ADI",
             "gain_db": -7.0, "nf_db": 7.0, "iip3_dbm": 28.0, "kind": "mixer",
             "datasheet_url": "https://www.analog.com/en/products/hmc1049.html"},
        ],
        "citations": [
            {"standard": "MIL-STD-461G", "clause": "RE102"},
        ],
    }

    bundle = finalize_p1(
        tool_input=tool_input,
        project_id="proj-123",
        design_type="EW / SIGINT",
        llm_model="glm-4.7",
    )

    # Lock present + hash computed
    assert bundle["lock"] is not None
    assert bundle["lock"]["requirements_hash"]
    assert len(bundle["lock"]["requirements_hash"]) == 64
    assert bundle["lock"]["domain"] == "ew"

    # lock_row is ready for an UPDATE projects SET ... statement
    row = bundle["lock_row"]
    assert set(row.keys()) == {
        "requirements_hash", "requirements_frozen_at", "requirements_locked_json"
    }
    # The locked JSON is a valid payload we can round-trip
    payload = json.loads(row["requirements_locked_json"])
    assert payload["project_id"] == "proj-123"
    assert payload["architecture"] == "superheterodyne"

    # Audit report produced
    rep = bundle["audit_report"]
    assert rep["phase_id"] == "P1"
    assert "overall_pass" in rep
    assert "confidence_score" in rep

    # Markdown outputs written
    assert "requirements_lock.json" in bundle["outputs"]
    assert "audit_report.md" in bundle["outputs"]
    assert "# Red-Team Audit Report" in bundle["outputs"]["audit_report.md"]

    # Summary is a non-empty markdown blurb
    assert "Requirements lock:" in bundle["summary_md"]


def test_finalize_p1_does_not_raise_on_empty_input():
    bundle = finalize_p1(
        tool_input={}, project_id="x", design_type=None,
    )
    # Lock is still produced — hash over an empty content dict is valid.
    assert bundle["lock"] is not None
    assert bundle["lock"]["requirements_hash"]
    assert bundle["audit_report"]["phase_id"] == "P1"


def test_audit_report_to_md_handles_empty_issue_list():
    from domains._schema import AuditReport
    rep = AuditReport(
        phase_id="P1", issues=[],
        hallucination_count=0, unresolved_citations=0, cascade_errors=0,
        overall_pass=True, confidence_score=1.0,
    )
    md = audit_report_to_md(rep)
    assert "passed all red-team checks" in md
