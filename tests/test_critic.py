"""Tests for agents/critic.py — structured-output diff.

Deterministic only: no LLM, no network.
"""
from __future__ import annotations

import pytest

from agents.critic import compare_designs, summarise


def _base_design() -> dict:
    return {
        "domain": "satcom",
        "architecture": "superheterodyne",
        "cascade": {
            "noise_figure_db": 1.5,
            "total_gain_db": 36.5,
            "iip3_dbm": 8.0,
            "p1db_dbm": -5.0,
        },
        "bom": [
            {"name": "LNA", "part_number": "HMC8411", "kind": "LNA"},
            {"name": "Filter", "part_number": None, "kind": "filter"},
            {"name": "Mixer", "part_number": "LTC5548", "kind": "mixer"},
            {"name": "IF_Amp", "part_number": "HMC8410", "kind": "amp"},
        ],
        "cited_standards": [
            ("MIL-STD-188-164", "Section 5"),
            ("MIL-STD-461G", "RE102"),
        ],
    }


def test_identical_designs_produce_no_issues():
    d = _base_design()
    assert compare_designs(d, dict(d)) == []


def test_identical_copy_independent_of_object_identity():
    import copy
    a = _base_design()
    b = copy.deepcopy(a)
    assert compare_designs(a, b) == []


def test_architecture_mismatch_is_high_severity():
    p = _base_design()
    f = _base_design()
    f["architecture"] = "direct_conversion"
    issues = compare_designs(p, f)
    assert len(issues) == 1
    assert issues[0].severity == "high"
    assert issues[0].location == "P1.architecture"
    assert "superheterodyne" in issues[0].detail
    assert "direct_conversion" in issues[0].detail


def test_cascade_nf_within_tolerance_is_quiet():
    p = _base_design()
    f = _base_design()
    f["cascade"]["noise_figure_db"] = 1.7  # 0.2 delta < 0.5 tol
    assert compare_designs(p, f, tolerance_db=0.5) == []


def test_cascade_nf_beyond_tolerance_flags_medium():
    p = _base_design()
    f = _base_design()
    f["cascade"]["noise_figure_db"] = 2.2  # 0.7 delta > 0.5 tol, < 5x
    issues = compare_designs(p, f, tolerance_db=0.5)
    assert len(issues) == 1
    assert issues[0].severity == "medium"
    assert "noise_figure_db" in issues[0].location


def test_cascade_gain_beyond_5x_tolerance_flags_high():
    p = _base_design()
    f = _base_design()
    f["cascade"]["total_gain_db"] = 42.0  # 5.5 delta vs 0.5 tol -> >5x
    issues = compare_designs(p, f, tolerance_db=0.5)
    sev = {i.severity for i in issues}
    assert "high" in sev


def test_bom_length_mismatch_is_high_severity():
    p = _base_design()
    f = _base_design()
    f["bom"] = f["bom"][:-1]
    issues = compare_designs(p, f)
    assert any(
        i.severity == "high" and "bom.length" in i.location
        for i in issues
    )


def test_bom_part_number_mismatch_is_high_severity():
    p = _base_design()
    f = _base_design()
    f["bom"][0] = {**f["bom"][0], "part_number": "BGA614"}
    issues = compare_designs(p, f)
    part_issues = [i for i in issues if "bom[0].part_number" in i.location]
    assert len(part_issues) == 1
    assert part_issues[0].severity == "high"


def test_citation_added_by_one_side_flags_both_as_medium():
    p = _base_design()
    f = _base_design()
    f["cited_standards"] = list(f["cited_standards"]) + [
        ("MIL-STD-810H", "Method 514.8")
    ]
    issues = compare_designs(p, f)
    assert len(issues) == 1
    assert issues[0].severity == "medium"
    assert "MIL-STD-810H" in issues[0].detail
    assert "fallback only" in issues[0].detail


def test_missing_field_in_one_side_is_low_severity():
    p = _base_design()
    f = _base_design()
    del f["architecture"]
    issues = compare_designs(p, f)
    assert len(issues) == 1
    assert issues[0].severity == "low"
    assert issues[0].category == "missing_field"


def test_summarise_buckets_by_severity_and_category():
    p = _base_design()
    f = _base_design()
    f["architecture"] = "direct_conversion"
    f["cascade"]["noise_figure_db"] = 2.2
    issues = compare_designs(p, f, tolerance_db=0.5)
    s = summarise(issues)
    assert s["total"] == 2
    assert s["by_severity"]["high"] == 1
    assert s["by_severity"]["medium"] == 1
    assert s["by_category"]["model_disagreement"] == 2


def test_non_dict_inputs_raise_typeerror():
    with pytest.raises(TypeError):
        compare_designs("not a dict", {})
    with pytest.raises(TypeError):
        compare_designs({}, None)


def test_citations_order_insensitive():
    p = _base_design()
    f = _base_design()
    f["cited_standards"] = list(reversed(p["cited_standards"]))
    assert compare_designs(p, f) == []


def test_citation_dict_form_accepted():
    p = _base_design()
    f = _base_design()
    f["cited_standards"] = [
        {"standard": "MIL-STD-188-164", "clause": "Section 5"},
        {"standard": "MIL-STD-461G", "clause": "RE102"},
    ]
    assert compare_designs(p, f) == []


def test_deterministic_runs_produce_identical_output():
    p = _base_design()
    f = _base_design()
    f["cascade"]["noise_figure_db"] = 2.5
    f["bom"][0] = {**f["bom"][0], "part_number": "BGA614"}
    run1 = compare_designs(p, f)
    run2 = compare_designs(p, f)
    assert [i.model_dump() for i in run1] == [i.model_dump() for i in run2]
