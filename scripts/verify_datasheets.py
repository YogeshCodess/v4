#!/usr/bin/env python3
"""
Walk every `domains/*/components.json` file and set `datasheet_verified` on
each entry based on a live HEAD/GET probe of its `datasheet_url`.

Usage:
    python scripts/verify_datasheets.py
    python scripts/verify_datasheets.py --dry-run            # print, don't write
    python scripts/verify_datasheets.py --timeout 10         # seconds per URL
    python scripts/verify_datasheets.py --offline            # skip HTTP, use vendor whitelist
    python scripts/verify_datasheets.py --report             # also write docs/datasheet_sweep_latest.{md,json}
    python scripts/verify_datasheets.py --md PATH --json PATH
                                                             # custom report paths

E3 — networked sweep harness:
The `--report` flag produces a stable, committable artefact that demonstrates
the Hardware Lead's curated URL set is still reachable. It runs cleanly in
air-gapped / CI sandboxes too: unreachable URLs simply show up as FAIL, and
the JSON summary is the machine-readable receipt.

Exit code: 0 always (unreachable URLs are normal and should not break CI).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.datasheet_verify import is_trusted_vendor_url, verify_url  # noqa: E402


def _probe_components(offline: bool, timeout: float) -> list[dict]:
    """
    Walk all domain components, run the probe, and return a flat list of
    per-part result dicts. Also mutates the in-memory components.json payload
    so callers can choose to persist the `datasheet_verified` flag.
    """
    results: list[dict] = []
    for comp_file in sorted((REPO / "domains").glob("*/components.json")):
        domain = comp_file.parent.name
        data = json.loads(comp_file.read_text())
        changed = False
        for part in data.get("components", []):
            url = part.get("datasheet_url")
            part_no = part.get("part_number", "?")

            if not url:
                live = False
                whitelist = False
                reason = "no-url"
            elif offline:
                live = False
                whitelist = is_trusted_vendor_url(url)
                reason = "offline-mode"
            else:
                live = verify_url(url, timeout=timeout)
                whitelist = is_trusted_vendor_url(url)
                reason = "live-ok" if live else ("whitelist" if whitelist else "unreachable")

            verified = bool(live or whitelist)

            if part.get("datasheet_verified") != verified:
                part["datasheet_verified"] = verified
                changed = True

            results.append({
                "domain": domain,
                "part_number": part_no,
                "url": url or "",
                "live_reachable": live,
                "trusted_vendor": whitelist,
                "verified": verified,
                "reason": reason,
            })

        # Stash a reference to the original file and whether it changed, so
        # the top-level writer can persist it without re-scanning.
        results.append({
            "_file": str(comp_file),
            "_changed": changed,
            "_data": data,
        })

    return results


def _write_updates(results: list[dict], dry_run: bool) -> None:
    for entry in results:
        if "_file" not in entry:
            continue
        if entry["_changed"] and not dry_run:
            Path(entry["_file"]).write_text(json.dumps(entry["_data"], indent=2))
            print(f"  -> wrote {Path(entry['_file']).relative_to(REPO)}")


def _split_results(results: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separate part rows from the _file/_changed marker rows."""
    parts = [r for r in results if "_file" not in r]
    markers = [r for r in results if "_file" in r]
    return parts, markers


def _summary_counts(parts: list[dict]) -> dict:
    total = len(parts)
    verified = sum(1 for p in parts if p["verified"])
    live_ok = sum(1 for p in parts if p["live_reachable"])
    whitelist_only = sum(1 for p in parts if p["trusted_vendor"] and not p["live_reachable"])
    missing_url = sum(1 for p in parts if not p["url"])
    unreachable = sum(1 for p in parts if p["url"] and not p["verified"])

    per_domain: dict[str, dict] = {}
    for p in parts:
        d = per_domain.setdefault(p["domain"], {"total": 0, "verified": 0})
        d["total"] += 1
        if p["verified"]:
            d["verified"] += 1

    return {
        "total_parts": total,
        "verified": verified,
        "live_reachable": live_ok,
        "whitelist_only": whitelist_only,
        "missing_url": missing_url,
        "unreachable": unreachable,
        "by_domain": per_domain,
    }


def _write_json_report(path: Path, parts: list[dict], summary: dict, offline: bool) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "offline" if offline else "live",
        "summary": summary,
        "parts": parts,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _write_md_report(path: Path, parts: list[dict], summary: dict, offline: bool) -> None:
    lines: list[str] = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append("# Datasheet Sweep — Latest Run")
    lines.append("")
    lines.append(f"_Generated {ts} (mode: **{'offline' if offline else 'live'}**)_")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total parts scanned: **{summary['total_parts']}**")
    lines.append(f"- Verified (live OR trusted-vendor): **{summary['verified']}**")
    lines.append(f"- Live-reachable HEAD/GET 2xx: **{summary['live_reachable']}**")
    lines.append(f"- Whitelisted only (vendor domain, no live hit): **{summary['whitelist_only']}**")
    lines.append(f"- Unreachable with URL set: **{summary['unreachable']}**")
    lines.append(f"- Missing URL entirely: **{summary['missing_url']}**")
    lines.append("")
    lines.append("### By domain")
    lines.append("")
    lines.append("| Domain | Verified | Total |")
    lines.append("|--------|---------:|------:|")
    for dom, dcounts in sorted(summary["by_domain"].items()):
        lines.append(f"| {dom} | {dcounts['verified']} | {dcounts['total']} |")
    lines.append("")

    unreachable_rows = [p for p in parts if p["url"] and not p["verified"]]
    if unreachable_rows:
        lines.append("## Unreachable URLs (needs Hardware Lead review)")
        lines.append("")
        lines.append("| Domain | Part | URL |")
        lines.append("|--------|------|-----|")
        for p in unreachable_rows:
            lines.append(f"| {p['domain']} | `{p['part_number']}` | {p['url']} |")
        lines.append("")

    missing_rows = [p for p in parts if not p["url"]]
    if missing_rows:
        lines.append("## Components missing a datasheet URL")
        lines.append("")
        lines.append("| Domain | Part |")
        lines.append("|--------|------|")
        for p in missing_rows:
            lines.append(f"| {p['domain']} | `{p['part_number']}` |")
        lines.append("")

    lines.append("## Full table")
    lines.append("")
    lines.append("| Domain | Part | Reason | Verified |")
    lines.append("|--------|------|--------|:-------:|")
    for p in parts:
        mark = "[x]" if p["verified"] else "[ ]"
        lines.append(f"| {p['domain']} | `{p['part_number']}` | {p['reason']} | {mark} |")
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Do not write the updated components.json files.")
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--offline", action="store_true",
                    help="Skip HTTP, mark verified by vendor-domain whitelist.")
    ap.add_argument("--report", action="store_true",
                    help="Write docs/datasheet_sweep_latest.{md,json} report.")
    ap.add_argument("--md", default=None, type=str,
                    help="Markdown report path (implies --report).")
    ap.add_argument("--json", default=None, type=str,
                    help="JSON report path (implies --report).")
    args = ap.parse_args()

    results = _probe_components(offline=args.offline, timeout=args.timeout)
    parts, _ = _split_results(results)

    for p in parts:
        status = "OK " if p["verified"] else "FAIL"
        print(f"  [{status}] {p['domain']}/{p['part_number']}: {p['url']}  ({p['reason']})")

    _write_updates(results, dry_run=args.dry_run)

    summary = _summary_counts(parts)
    print(
        f"\nVerified {summary['verified']}/{summary['total_parts']} URLs "
        f"(live={summary['live_reachable']}, whitelist_only={summary['whitelist_only']}, "
        f"unreachable={summary['unreachable']}, missing_url={summary['missing_url']})."
    )

    wants_report = args.report or args.md or args.json
    if wants_report:
        md_path = Path(args.md) if args.md else REPO / "docs" / "datasheet_sweep_latest.md"
        json_path = Path(args.json) if args.json else REPO / "docs" / "datasheet_sweep_latest.json"
        _write_md_report(md_path, parts, summary, offline=args.offline)
        _write_json_report(json_path, parts, summary, offline=args.offline)
        print(f"  -> wrote {md_path.relative_to(REPO) if md_path.is_absolute() and REPO in md_path.parents else md_path}")
        print(f"  -> wrote {json_path.relative_to(REPO) if json_path.is_absolute() and REPO in json_path.parents else json_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
