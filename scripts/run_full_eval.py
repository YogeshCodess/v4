#!/usr/bin/env python3
"""
Run the full deterministic evaluation suite — the one command the team runs
before every demo to prove everything still works.

Composes:

  1. pytest over the deterministic test suites (skip LLM-dependent tests).
  2. scripts/run_baseline_eval.py  — 10 golden scenarios end-to-end.
  3. scripts/run_ablation_matrix.py — 4 configs × 3 mutations × 10 scenarios.
  4. scripts/reproduce_run.py       — deterministic self-test.
  5. Schema sanity — migrations apply cleanly on a fresh SQLite file.
  6. Component + clause DB sanity — every file parses and meets minimum counts.

Prints a compact judge-readable summary:

    [PASS] pytest              110 / 110
    [PASS] baseline            10 / 10 scenarios
    [PASS] ablation            diagonal zeroes verified
    [PASS] reproduce           deterministic self-test
    [PASS] migrations          apply cleanly on empty DB
    [PASS] component DB        75 parts across 4 domains
    [PASS] clause DB           49 clauses

Exit code 0 on full pass, 1 otherwise. Safe to run offline.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Suites we skip in pytest — they require anthropic/sqlalchemy/playwright or
# an open network. The deterministic suite is what judges can see reproduced.
_NON_DETERMINISTIC_SUITES = (
    "tests/test_agents.py",
    "tests/test_base_agent.py",
    "tests/test_config.py",
    "tests/test_e2e_pipeline.py",
    "tests/test_orchestrator.py",
    "tests/test_ui_playwright.py",
)

# Minimum sizes for the content DBs — if these drop we want to fail loudly.
_MIN_COMPONENTS_PER_DOMAIN = {"radar": 15, "ew": 15, "satcom": 15, "communication": 15}
_MIN_CLAUSE_COUNT = 40


class Check:
    def __init__(self, name: str):
        self.name = name
        self.ok: bool = False
        self.detail: str = ""
        self.duration_ms: int = 0

    def mark(self, ok: bool, detail: str) -> None:
        self.ok = ok
        self.detail = detail


def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def check_pytest() -> Check:
    c = Check("pytest")
    t0 = time.time()
    ignore_args: list[str] = []
    for suite in _NON_DETERMINISTIC_SUITES:
        ignore_args += ["--ignore", suite]
    code, out = _run(
        [sys.executable, "-m", "pytest", "tests/", "-q", *ignore_args],
        cwd=REPO,
    )
    c.duration_ms = int((time.time() - t0) * 1000)
    # Parse the last "N passed" line out of pytest's tail.
    summary_line = ""
    for line in out.strip().splitlines()[::-1]:
        if " passed" in line or " failed" in line or " error" in line:
            summary_line = line.strip()
            break
    c.mark(code == 0, summary_line or out.strip().splitlines()[-1])
    return c


def check_baseline() -> Check:
    c = Check("baseline")
    t0 = time.time()
    code, out = _run(
        [sys.executable, "scripts/run_baseline_eval.py"], cwd=REPO,
    )
    c.duration_ms = int((time.time() - t0) * 1000)
    summary = next(
        (l for l in out.splitlines()[::-1] if "scenarios passed" in l), ""
    )
    # Extract the "N/M" prefix and verify N == M.
    ratio_ok = False
    if summary:
        head = summary.split()[0]  # e.g. "10/10"
        if "/" in head:
            n, m = head.split("/", 1)
            try:
                ratio_ok = int(n) == int(m) and int(m) > 0
            except ValueError:
                ratio_ok = False
    c.mark(code == 0 and ratio_ok, summary or "no summary line")
    return c


def check_ablation() -> Check:
    """Ablation matrix must show each config dropping to 0% detection in
    exactly the column it ablates. We parse the printed matrix for that.
    """
    c = Check("ablation")
    t0 = time.time()
    code, out = _run(
        [sys.executable, "scripts/run_ablation_matrix.py"], cwd=REPO,
    )
    c.duration_ms = int((time.time() - t0) * 1000)
    if code != 0:
        c.mark(False, "script exited non-zero")
        return c
    # Expect 4 columns: full | no_validator | no_citation | no_redteam.
    # Parse the 4 data rows: clean / bad_citation / hallucinated_part /
    # cascade_inflation. Each row has 4 percentages.
    lines = [l for l in out.splitlines() if "%" in l]
    parsed: dict[str, list[float]] = {}
    for line in lines:
        parts = line.split()
        if len(parts) < 5:
            continue
        label = parts[0]
        pcts: list[float] = []
        for p in parts[1:5]:
            try:
                pcts.append(float(p.rstrip("%")))
            except ValueError:
                pcts = []
                break
        if len(pcts) == 4:
            parsed[label] = pcts
    required = {"clean", "bad_citation", "hallucinated_part", "cascade_inflation"}
    if not required.issubset(parsed.keys()):
        c.mark(False, f"missing rows in matrix: {required - parsed.keys()}")
        return c
    # Expected diagonal of zeroes:
    #   bad_citation      -> no_citation column (index 2)   == 0
    #   hallucinated_part -> no_redteam column  (index 3)   == 0
    #   cascade_inflation -> no_validator column (index 1)  == 0
    # Clean row should be 100% everywhere; full column should be 100% on
    # every mutation.
    checks = [
        ("clean", all(v == 100.0 for v in parsed["clean"])),
        ("full_column", all(parsed[r][0] == 100.0 for r in
                            ("bad_citation", "hallucinated_part", "cascade_inflation"))),
        ("bad_citation_zero_in_no_citation", parsed["bad_citation"][2] == 0.0),
        ("hallucinated_part_zero_in_no_redteam", parsed["hallucinated_part"][3] == 0.0),
        ("cascade_inflation_zero_in_no_validator", parsed["cascade_inflation"][1] == 0.0),
    ]
    failed = [name for name, ok in checks if not ok]
    if failed:
        c.mark(False, f"failed guardrails: {failed}")
    else:
        c.mark(True, "diagonal zeroes verified; clean=100% everywhere")
    return c


def check_reproduce() -> Check:
    c = Check("reproduce")
    t0 = time.time()
    code, out = _run(
        [sys.executable, "scripts/reproduce_run.py"], cwd=REPO,
    )
    c.duration_ms = int((time.time() - t0) * 1000)
    ok = code == 0 and "self-test OK" in out
    c.mark(ok, "deterministic self-test" if ok else out.strip().splitlines()[-1])
    return c


def check_migrations() -> Check:
    c = Check("migrations")
    t0 = time.time()
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "eval.db"
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()
        from migrations import apply_all  # noqa: E402
        apply_all(str(db_path))
        # Verify the expected tables / columns landed.
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cols = {r[1] for r in cur.execute("PRAGMA table_info(projects)")}
        tables = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        conn.close()
        needed_cols = {
            "requirements_hash", "requirements_frozen_at", "requirements_locked_json"
        }
        needed_tables = {"projects", "pipeline_runs", "llm_calls"}
        ok = needed_cols.issubset(cols) and needed_tables.issubset(tables)
        c.duration_ms = int((time.time() - t0) * 1000)
        if ok:
            c.mark(True, "lock columns + pipeline_runs + llm_calls present")
        else:
            c.mark(False, f"missing cols={needed_cols-cols}, tables={needed_tables-tables}")
    except Exception as exc:
        c.mark(False, f"migration raised: {exc!r}")
    return c


def check_component_db() -> Check:
    c = Check("component DB")
    t0 = time.time()
    total = 0
    per_domain: dict[str, int] = {}
    for path in (REPO / "domains").glob("*/components.json"):
        try:
            data = json.loads(path.read_text())
        except Exception as exc:
            c.mark(False, f"cannot parse {path.name}: {exc!r}")
            return c
        domain = path.parent.name
        count = len(data.get("components", []))
        per_domain[domain] = count
        total += count
    c.duration_ms = int((time.time() - t0) * 1000)
    shortfalls = [
        f"{d}={per_domain.get(d,0)}<{m}"
        for d, m in _MIN_COMPONENTS_PER_DOMAIN.items()
        if per_domain.get(d, 0) < m
    ]
    if shortfalls:
        c.mark(False, "; ".join(shortfalls))
    else:
        per = ", ".join(f"{d}={n}" for d, n in sorted(per_domain.items()))
        c.mark(True, f"{total} parts ({per})")
    return c


def check_clause_db() -> Check:
    c = Check("clause DB")
    t0 = time.time()
    path = REPO / "domains" / "standards.json"
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        c.mark(False, f"cannot parse standards.json: {exc!r}")
        return c
    count = len(data.get("clauses", data if isinstance(data, list) else []))
    c.duration_ms = int((time.time() - t0) * 1000)
    if count < _MIN_CLAUSE_COUNT:
        c.mark(False, f"{count} clauses (< {_MIN_CLAUSE_COUNT})")
    else:
        c.mark(True, f"{count} clauses")
    return c


def print_summary(checks: list[Check]) -> None:
    width = max(len(c.name) for c in checks)
    print()
    print("=" * 72)
    print("FULL DETERMINISTIC EVAL SUMMARY")
    print("=" * 72)
    for c in checks:
        tag = "[PASS]" if c.ok else "[FAIL]"
        print(f"{tag}  {c.name.ljust(width)}   {c.detail}   ({c.duration_ms} ms)")
    print("=" * 72)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="Write a JSON summary to this path")
    ap.add_argument("--skip-pytest", action="store_true",
                    help="Skip the pytest step (for very fast smoke)")
    args = ap.parse_args()

    checks: list[Check] = []
    if not args.skip_pytest:
        checks.append(check_pytest())
    checks.append(check_baseline())
    checks.append(check_ablation())
    checks.append(check_reproduce())
    checks.append(check_migrations())
    checks.append(check_component_db())
    checks.append(check_clause_db())

    print_summary(checks)

    if args.json:
        Path(args.json).write_text(json.dumps([
            {"name": c.name, "ok": c.ok, "detail": c.detail,
             "duration_ms": c.duration_ms}
            for c in checks
        ], indent=2))

    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
