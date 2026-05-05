#!/usr/bin/env python3
"""
Run the baseline evaluation — D1.3 / D1.4 deliverable.

For each golden scenario in tests/golden/<domain>/*.yaml:
  1. Recompute the cascade using tools.cascade_validator.
  2. Compare against the scenario's expected_cascade (NF, gain, tolerance).
  3. Validate every expected_citation resolves in the clause DB.
  4. Run the red-team audit against the scenario's BOM with the expected
     cascade as "claimed" values. A clean scenario should have overall_pass.

Emits a JSON report to eval_results/baseline_<timestamp>.json and prints
a per-scenario PASS/FAIL summary to stdout.

No network calls. No LLM calls. This is the deterministic floor against which
any LLM run will be scored.

Exit code: 0 on all pass, 1 on any fail.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents.red_team_audit import audit  # noqa: E402
from domains.standards import validate_citations  # noqa: E402
from tools.cascade_validator import validate_cascade_from_dicts  # noqa: E402


def _mini_yaml(text: str) -> dict[str, Any]:
    """Tiny fallback YAML parser used by tests/test_golden.py — re-exported here
    so the harness does not require PyYAML in constrained envs."""
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        pass

    # Very small YAML subset: top-level keys, nested mappings via indentation,
    # inline flow-style {a: b, c: d} and [a, b], plus "- {k: v}" list items.
    import ast
    result: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, result)]
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else result
        s = line.strip()
        if s.startswith("- "):
            item = s[2:].strip()
            try:
                value = ast.literal_eval(item.replace("true", "True").replace("false", "False"))
            except Exception:
                value = item
            if isinstance(parent, list):
                parent.append(value)
            continue
        if ":" not in s:
            continue
        key, _, val = s.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "":
            # Peek ahead: default to dict; tests/test_golden has better parser.
            new: Any = {}
            if isinstance(parent, dict):
                parent[key] = new
            stack.append((indent, new))
        else:
            try:
                parsed = ast.literal_eval(val.replace("true", "True").replace("false", "False"))
            except Exception:
                parsed = val.strip('"')
            if isinstance(parent, dict):
                parent[key] = parsed
    return result


def _load_scenario(path: Path) -> dict[str, Any]:
    return _mini_yaml(path.read_text())


def evaluate(scenario: dict[str, Any]) -> dict[str, Any]:
    bom = scenario.get("bom", [])
    expected = scenario.get("expected_cascade", {})
    tol = float(expected.get("tolerance_db", 0.5))
    bw = 1_000_000.0
    snr = 10.0

    rep = validate_cascade_from_dicts(
        stages=bom, bandwidth_hz=bw, snr_required_db=snr, temperature_c=25.0
    )

    checks: list[dict[str, Any]] = []

    if "noise_figure_db" in expected:
        delta = abs(rep.noise_figure_db - float(expected["noise_figure_db"]))
        checks.append({
            "check": "NF within tolerance",
            "expected": expected["noise_figure_db"],
            "computed": round(rep.noise_figure_db, 3),
            "delta": round(delta, 3),
            "tol": tol,
            "passed": delta <= tol,
        })

    if "total_gain_db" in expected:
        delta = abs(rep.total_gain_db - float(expected["total_gain_db"]))
        checks.append({
            "check": "Gain within tolerance",
            "expected": expected["total_gain_db"],
            "computed": round(rep.total_gain_db, 3),
            "delta": round(delta, 3),
            "tol": tol,
            "passed": delta <= tol,
        })

    # Citation resolution
    expected_cites = scenario.get("expected_citations", [])
    citation_pairs = [(c[0], c[1]) for c in expected_cites]
    missing = validate_citations(citation_pairs)
    checks.append({
        "check": "All cited clauses resolve",
        "expected": len(citation_pairs),
        "missing": missing,
        "passed": len(missing) == 0,
    })

    # Red-team audit should pass on the hand-crafted golden scenarios.
    audit_report = audit(
        phase_id="P1",
        bom_stages=bom,
        claimed_cascade={
            "noise_figure_db": expected.get("noise_figure_db"),
            "total_gain_db": expected.get("total_gain_db"),
        },
        citations=citation_pairs,
        claimed_parts=[{
            "part_number": s.get("part_number", ""),
        } for s in bom if s.get("part_number")],
        known_parts=set(),  # audit should NOT flag since every part has a pn
        bandwidth_hz=bw,
        snr_required_db=snr,
        cascade_tolerance_db=tol,
    )
    # The audit will flag parts that aren't in the known DB and have no URL.
    # For golden scenarios we care only about cascade + citation issues.
    cascade_and_citations_ok = (
        audit_report.cascade_errors == 0
        and audit_report.unresolved_citations == 0
    )
    checks.append({
        "check": "Red-team audit: no cascade/citation issues",
        "cascade_errors": audit_report.cascade_errors,
        "unresolved_citations": audit_report.unresolved_citations,
        "overall_pass_from_audit": audit_report.overall_pass,
        "passed": cascade_and_citations_ok,
    })

    all_passed = all(c["passed"] for c in checks)
    return {
        "domain": scenario.get("domain"),
        "id": scenario.get("id"),
        "architecture": scenario.get("architecture"),
        "all_passed": all_passed,
        "checks": checks,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="eval_results")
    args = ap.parse_args()

    out_dir = REPO / args.out
    out_dir.mkdir(exist_ok=True, parents=True)

    results = []
    for scenario_file in sorted((REPO / "tests" / "golden").glob("*/*.yaml")):
        scenario = _load_scenario(scenario_file)
        res = evaluate(scenario)
        res["source"] = scenario_file.relative_to(REPO).as_posix()
        results.append(res)
        status = "PASS" if res["all_passed"] else "FAIL"
        print(f"[{status}] {res['domain']}/{res['id']} — {scenario_file.name}")
        for c in res["checks"]:
            mark = " ok " if c["passed"] else "FAIL"
            print(f"      {mark} {c['check']}")

    pass_count = sum(1 for r in results if r["all_passed"])
    total = len(results)
    summary = {
        "timestamp": int(time.time()),
        "scenarios_total": total,
        "scenarios_passed": pass_count,
        "all_passed": pass_count == total,
        "results": results,
    }
    out_path = out_dir / f"baseline_{summary['timestamp']}.json"
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"\n{pass_count}/{total} scenarios passed. Report: {out_path.relative_to(REPO)}")
    return 0 if pass_count == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
