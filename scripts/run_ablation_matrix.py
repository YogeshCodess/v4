#!/usr/bin/env python3
"""
Run the ablation matrix — D1.3 deliverable.

Four configurations:
  - full        : validator on, citation check on, red-team on
  - no_validator: skip cascade math; trust whatever the scenario claims
  - no_citation : skip standards DB lookup
  - no_redteam  : run the validator but do not flag BOM issues

For each golden scenario, we record whether the configuration produces
overall_pass=True. The "full" column is the ground truth for "should have
passed"; the ablations are expected to either pass trivially (they don't
check anything) or fail differently depending on what's broken in the
scenario's requirement claims.

To exercise the ablations, we also inject a set of *mutated* scenarios
that intentionally contain one of:
  - a fabricated citation
  - a hallucinated part number
  - a cascade claim that does not match the BOM

and report the detection rate per configuration.

Writes eval_results/ablation_<timestamp>.json and prints a matrix.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agents.red_team_audit import audit  # noqa: E402
from domains.standards import validate_citations  # noqa: E402
from scripts.run_baseline_eval import _load_scenario  # noqa: E402
from tools.cascade_validator import validate_cascade_from_dicts  # noqa: E402

CONFIGS = ["full", "no_validator", "no_citation", "no_redteam"]


def _known_parts_for(domain: str) -> set:
    """Load every part number from the union of all domain component DBs.

    Parts used across domains (e.g. a wideband LNA appears in both radar and
    EW scenarios) should not be treated as hallucinations in one context and
    not another — the clause/standards DB is the per-domain filter.
    """
    known: set = set()
    for path in (REPO / "domains").glob("*/components.json"):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        known.update({
            c.get("part_number", "")
            for c in data.get("components", [])
            if c.get("part_number")
        })
    _ = domain  # kept in signature for future per-domain filtering
    return known

# Each mutation takes (scenario_dict) -> mutated scenario_dict. The label
# indicates which failure the ablation *should* catch.
def _mutate_bad_citation(s):
    s = copy.deepcopy(s)
    s["expected_citations"] = list(s.get("expected_citations", [])) + [["FAKE-STD", "Z99"]]
    return s

def _mutate_hallucinated_part(s):
    s = copy.deepcopy(s)
    bom = list(s.get("bom", []))
    bom.append({"name": "FAKE_STAGE", "gain_db": 0.0, "nf_db": 0.0,
                "kind": "amp", "part_number": "FAKE-NONEXISTENT-9000"})
    s["bom"] = bom
    return s

def _mutate_cascade_inflation(s):
    s = copy.deepcopy(s)
    expected = s.setdefault("expected_cascade", {})
    # Drop NF by 5 dB vs the true cascade -- well beyond tolerance.
    if "noise_figure_db" in expected:
        expected["noise_figure_db"] = float(expected["noise_figure_db"]) - 5.0
    else:
        expected["noise_figure_db"] = 0.1
    expected["tolerance_db"] = expected.get("tolerance_db", 0.5)
    return s


MUTATIONS = {
    "bad_citation": _mutate_bad_citation,
    "hallucinated_part": _mutate_hallucinated_part,
    "cascade_inflation": _mutate_cascade_inflation,
}


def _evaluate(scenario: dict, config: str) -> dict:
    bom = scenario.get("bom", [])
    expected = scenario.get("expected_cascade", {})
    tol = float(expected.get("tolerance_db", 0.5))
    citations = [(c[0], c[1]) for c in scenario.get("expected_citations", [])]
    claimed_parts = [{"part_number": s.get("part_number")}
                     for s in bom if s.get("part_number")]
    known = _known_parts_for(scenario.get("domain", ""))

    # Cascade check
    cascade_ok = True
    if config != "no_validator" and expected:
        rep = validate_cascade_from_dicts(
            stages=bom, bandwidth_hz=1e6, snr_required_db=10.0, temperature_c=25.0
        )
        for key, attr in [("noise_figure_db", "noise_figure_db"),
                          ("total_gain_db", "total_gain_db")]:
            if key in expected:
                delta = abs(getattr(rep, attr) - float(expected[key]))
                if delta > tol:
                    cascade_ok = False

    # Citation check
    citation_ok = True
    if config != "no_citation":
        missing = validate_citations(citations)
        if missing:
            citation_ok = False

    # Red-team pass (wraps cascade + citation + hallucinated-part detection).
    # When a particular sub-check is ablated we also suppress its input into
    # the audit so the audit cannot compensate for the missing standalone check.
    redteam_ok = True
    if config != "no_redteam":
        audit_claimed_cascade = (
            {}
            if config == "no_validator"
            else {
                "noise_figure_db": expected.get("noise_figure_db"),
                "total_gain_db": expected.get("total_gain_db"),
            }
        )
        audit_citations = [] if config == "no_citation" else citations
        rep = audit(
            phase_id="P1",
            bom_stages=bom,
            claimed_cascade=audit_claimed_cascade,
            citations=audit_citations,
            claimed_parts=claimed_parts,
            known_parts=known,
            bandwidth_hz=1e6,
            snr_required_db=10.0,
            cascade_tolerance_db=tol,
        )
        redteam_ok = rep.overall_pass

    passed = cascade_ok and citation_ok and redteam_ok
    return {
        "cascade_ok": cascade_ok,
        "citation_ok": citation_ok,
        "redteam_ok": redteam_ok,
        "passed": passed,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="eval_results")
    args = ap.parse_args()

    out_dir = REPO / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    scenarios = sorted((REPO / "tests" / "golden").glob("*/*.yaml"))
    rows = []

    for s_path in scenarios:
        base = _load_scenario(s_path)
        base_label = f"{base.get('domain')}/{base.get('id')}"
        # Clean baseline: every config should pass.
        for cfg in CONFIGS:
            r = _evaluate(base, cfg)
            rows.append({"scenario": base_label, "mutation": "clean", "config": cfg, **r})

        # Mutation sweep: ablation matrix tells us which config catches what.
        for mut_name, mut_fn in MUTATIONS.items():
            mutated = mut_fn(base)
            for cfg in CONFIGS:
                r = _evaluate(mutated, cfg)
                rows.append({"scenario": base_label, "mutation": mut_name,
                             "config": cfg, **r})

    # Aggregate detection rate per (mutation, config): detection means the
    # config produced passed=False on a mutated scenario. We want this to be
    # high for "full" and to drop for the config that was turned off.
    summary: dict[str, dict[str, float]] = {}
    for mut in ["clean"] + list(MUTATIONS.keys()):
        summary[mut] = {}
        for cfg in CONFIGS:
            subset = [r for r in rows if r["mutation"] == mut and r["config"] == cfg]
            if mut == "clean":
                # Count pass rate.
                summary[mut][cfg] = sum(1 for r in subset if r["passed"]) / len(subset)
            else:
                # Count detection rate = fraction where passed=False.
                summary[mut][cfg] = sum(1 for r in subset if not r["passed"]) / len(subset)

    # Print matrix.
    hdr = "mutation".ljust(20) + "".join(c.ljust(16) for c in CONFIGS)
    print(hdr)
    print("-" * len(hdr))
    for mut in ["clean"] + list(MUTATIONS.keys()):
        line = mut.ljust(20) + "".join(f"{summary[mut][c]*100:6.1f}%".ljust(16) for c in CONFIGS)
        print(line)

    ts = int(time.time())
    out_path = out_dir / f"ablation_{ts}.json"
    out_path.write_text(json.dumps({
        "timestamp": ts,
        "configs": CONFIGS,
        "mutations": list(MUTATIONS.keys()),
        "rows": rows,
        "summary": summary,
    }, indent=2))
    print(f"\nReport: {out_path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
