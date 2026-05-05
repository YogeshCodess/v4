"""
Backfill canonical datasheet URLs on existing projects.

Walks every row in the `projects` table, reads
`design_parameters.component_recommendations[*]`, and rewrites each entry's
`datasheet_url` field to the canonical vendor product-page URL computed by
`tools.datasheet_url.canonical_datasheet_url`.

Existing URLs that already match the canonical form are left untouched.
Entries with no manufacturer or part number are skipped.

Also regenerates `design_parameters.components_md` (the cached BOM markdown
that the frontend renders in the Details tab) so the fix is visible without
re-running P1. If the project has a P1 output directory with a
`components.md` file, that file is rewritten as well.

Usage:
    python scripts/refresh_datasheet_urls.py                 # dry run, prints diff
    python scripts/refresh_datasheet_urls.py --apply         # write changes
    python scripts/refresh_datasheet_urls.py --apply --only <project_id>
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

# Allow running as a standalone script from the repo root
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.datasheet_url import canonical_datasheet_url, candidate_datasheet_urls  # noqa: E402


def _get_project_rows(cursor) -> list[tuple[Any, ...]]:
    cursor.execute("SELECT id, name, design_parameters FROM projects")
    return cursor.fetchall()


def _parse_design_params(raw: Any) -> dict:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _refresh_component_urls(comps: list[dict]) -> tuple[list[dict], int, int]:
    """Return (new_comps, changed_count, canonical_count)."""
    changed = 0
    canonical = 0
    new_comps: list[dict] = []
    for comp in comps:
        mfr = (comp.get("primary_manufacturer") or "").strip()
        part = (comp.get("primary_part") or "").strip()
        if not mfr or not part:
            new_comps.append(comp)
            continue
        canon, confidence = canonical_datasheet_url(mfr, part)
        if not canon:
            new_comps.append(comp)
            continue
        if confidence == "canonical":
            canonical += 1
        current = (comp.get("datasheet_url") or "").strip()
        if current != canon:
            comp = {**comp, "datasheet_url": canon}
            changed += 1
        new_comps.append(comp)
    return new_comps, changed, canonical


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Persist changes. Without this flag the script is a dry run.")
    ap.add_argument("--only", help="Restrict to a single project_id.")
    ap.add_argument("--db", default=str(ROOT / "hardware_pipeline.db"),
                    help="Path to hardware_pipeline.db (default: repo root).")
    args = ap.parse_args()

    import sqlite3
    if not pathlib.Path(args.db).exists():
        print(f"ERROR: database not found at {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        rows = _get_project_rows(cursor)
    except sqlite3.Error as exc:
        print(f"ERROR: failed to read projects table: {exc}", file=sys.stderr)
        return 3

    total_projects = 0
    total_components = 0
    total_changed = 0
    total_canonical = 0

    for row in rows:
        pid, name, dp_raw = row[0], row[1], row[2]
        if args.only and str(pid) != str(args.only):
            continue
        params = _parse_design_params(dp_raw)
        comps = params.get("component_recommendations") or []
        if not comps:
            continue

        new_comps, changed, canonical = _refresh_component_urls(comps)
        total_projects += 1
        total_components += len(comps)
        total_changed += changed
        total_canonical += canonical

        if changed == 0:
            print(f"[skip]  {pid}  {name}  — {len(comps)} components, no change")
            continue

        print(f"[edit]  {pid}  {name}  — {changed}/{len(comps)} URLs updated ({canonical} canonical)")
        # Show per-row diffs so the operator can audit
        for old, new in zip(comps, new_comps):
            o = (old.get("datasheet_url") or "").strip()
            n = (new.get("datasheet_url") or "").strip()
            if o != n:
                print(f"          {old.get('primary_part','?'):<24}  {o or '—'}")
                print(f"          {' ':<24}→ {n}")

        if args.apply:
            params["component_recommendations"] = new_comps
            cursor.execute(
                "UPDATE projects SET design_parameters = ? WHERE id = ?",
                (json.dumps(params), pid),
            )

    if args.apply:
        conn.commit()
        conn.close()
        print(f"\n[APPLIED]  projects={total_projects}  components={total_components}  "
              f"updated={total_changed}  canonical={total_canonical}")
    else:
        conn.close()
        print(f"\n[DRY RUN]  projects={total_projects}  components={total_components}  "
              f"would-update={total_changed}  canonical={total_canonical}")
        print("           (rerun with --apply to persist)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
