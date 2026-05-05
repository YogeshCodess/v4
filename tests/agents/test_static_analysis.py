"""Tests for agents/static_analysis.py — pure helpers.

The runner shells out to `cppcheck` / `cpplint` when available; those are
exercised integration-style in E2E. Here we lock in the pure pieces:
  - _complexity_severity thresholds
  - _parse_cppcheck_xml / _parse_cpplint_output
  - _build_summary quality score formula
"""
from __future__ import annotations

import pytest

from agents.static_analysis import (
    CPPCHECK_SEVERITY_MAP,
    MISRA_RULE_HINTS,
    StaticAnalysisRunner,
)


@pytest.fixture
def runner():
    return StaticAnalysisRunner()


# ---------------------------------------------------------------------------
# _complexity_severity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cc,expected", [
    (5, "OK"),
    (10, "OK"),
    (11, "MEDIUM"),
    (15, "MEDIUM"),
    (16, "HIGH"),
    (20, "HIGH"),
    (21, "CRITICAL"),
    (100, "CRITICAL"),
])
def test_complexity_severity_bins(runner, cc, expected):
    assert runner._complexity_severity(cc) == expected


# ---------------------------------------------------------------------------
# _parse_cppcheck_xml
# ---------------------------------------------------------------------------

def test_parse_cppcheck_xml_empty_returns_empty_list(runner):
    assert runner._parse_cppcheck_xml("") == []
    assert runner._parse_cppcheck_xml("<results/>") == []


def test_parse_cppcheck_xml_extracts_error_and_maps_severity(runner):
    xml = """
    <results>
      <errors>
        <error id="nullPointer" severity="error" msg="Null pointer dereference">
          <location file="main.c" line="42"/>
        </error>
        <error id="unusedVariable" severity="style" msg="Unused variable 'x'">
          <location file="util.c" line="7"/>
        </error>
      </errors>
    </results>
    """
    findings = runner._parse_cppcheck_xml(xml)
    assert len(findings) == 2

    err = next(f for f in findings if f["id"] == "nullPointer")
    assert err["severity"] == "CRITICAL"
    assert err["file"] == "main.c"
    assert err["line"] == 42
    assert "Null pointer" in err["misra_rule"]

    style = next(f for f in findings if f["id"] == "unusedVariable")
    assert style["severity"] == "LOW"
    assert "Dead code" in style["misra_rule"]


def test_parse_cppcheck_xml_handles_malformed_input(runner):
    # Must not raise
    assert runner._parse_cppcheck_xml("not xml at all <error>") == []


# ---------------------------------------------------------------------------
# _parse_cpplint_output
# ---------------------------------------------------------------------------

def test_parse_cpplint_output_parses_line_format(runner):
    out = (
        "/tmp/foo.cpp:10:  Line too long  [whitespace/line_length] [2]\n"
        "/tmp/foo.cpp:20:  Missing copyright  [legal/copyright] [5]\n"
    )
    findings = runner._parse_cpplint_output(out, "foo.cpp")
    assert len(findings) == 2
    low_severity = [f for f in findings if f["severity"] == "LOW"]
    medium_severity = [f for f in findings if f["severity"] == "MEDIUM"]
    assert len(low_severity) == 1  # confidence 2 → LOW
    assert len(medium_severity) == 1  # confidence 5 → MEDIUM


def test_parse_cpplint_output_ignores_non_matching_lines(runner):
    findings = runner._parse_cpplint_output("garbage line\n\n", "x.cpp")
    assert findings == []


# ---------------------------------------------------------------------------
# _build_summary — quality score formula + tool list
# ---------------------------------------------------------------------------

def test_build_summary_perfect_score_for_no_issues(runner):
    summary = runner._build_summary({
        "cppcheck": [], "complexity": [], "style": [],
        "tool_versions": {},
    })
    assert summary["quality_score"] == 100
    assert summary["total_issues"] == 0
    assert summary["tools_used"] == "LLM-based analysis"


def test_build_summary_deducts_for_critical_high_medium(runner):
    summary = runner._build_summary({
        "cppcheck": [
            {"severity": "CRITICAL"},
            {"severity": "HIGH"},
            {"severity": "MEDIUM"},
        ],
        "complexity": [
            {"cyclomatic_complexity": 25, "misra_rule": "x"},  # counts
            {"cyclomatic_complexity": 5},                      # does not
        ],
        "style": [],
        "tool_versions": {},
    })
    # -15 (crit) -8 (high) -3 (med) -5 (one complexity issue > 10) = 69
    assert summary["quality_score"] == 69
    assert summary["critical"] == 1
    assert summary["high"] == 1
    assert summary["medium"] == 1
    assert summary["complexity_violations"] == 1


def test_build_summary_score_never_goes_negative(runner):
    summary = runner._build_summary({
        "cppcheck": [{"severity": "CRITICAL"}] * 20,
        "complexity": [], "style": [], "tool_versions": {},
    })
    assert summary["quality_score"] == 0


def test_build_summary_lists_tools_when_present(runner):
    summary = runner._build_summary({
        "cppcheck": [{"severity": "LOW"}],
        "complexity": [{"cyclomatic_complexity": 5}],
        "style": [{"severity": "LOW"}],
        "tool_versions": {"cppcheck": "2.15.0"},
    })
    assert "Cppcheck" in summary["tools_used"]
    assert "Lizard" in summary["tools_used"]
    assert "cpplint" in summary["tools_used"]


# ---------------------------------------------------------------------------
# Severity / MISRA lookup tables — shape
# ---------------------------------------------------------------------------

def test_severity_map_covers_all_cppcheck_levels():
    for level in ["error", "warning", "style", "performance", "portability", "information"]:
        assert level in CPPCHECK_SEVERITY_MAP


def test_misra_hints_contain_rule_numbers():
    for hint in MISRA_RULE_HINTS.values():
        assert "MISRA C 2012 Rule" in hint
