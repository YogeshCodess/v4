#!/usr/bin/env python3
"""One-shot mermaid-block sanitiser for existing project output files.

When a user clones the repo on a fresh machine OR pulls down a newer
agent that writes cleaner mermaid, the EXISTING `output/<project>/*.md`
files retain whatever the OLD agent wrote — including bracket-mismatch
bugs that break in-browser rendering. The sanitiser pass added in P26
#17 only runs at WRITE time inside the agents, so it never touches
files that were generated before the fix landed.

This script walks every markdown file under `output/` and runs each
fenced ```mermaid``` block through `tools.mermaid_coerce.
sanitize_mermaid_blocks_in_markdown` — replacing bracket-mismatched /
nested-bracket / glyph-leaking blocks with the deterministic re-render
of the same nodes + edges. Files that contain no mermaid (or whose
mermaid was already clean) are left bit-exact.

Usage:
    # Sanitise every project under ./output/
    python scripts/sanitise_existing_outputs.py

    # Sanitise just one project
    python scripts/sanitise_existing_outputs.py output/rx_band

    # Dry-run (show which files would change without writing)
    python scripts/sanitise_existing_outputs.py --dry-run

The script is IDEMPOTENT — running it twice on the same files
produces the exact same result the second time.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from anywhere — add repo root to sys.path.
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from tools.mermaid_coerce import sanitize_mermaid_blocks_in_markdown


def sanitise_one_file(path: Path, *, dry_run: bool) -> bool:
    """Returns True if the file was changed (or would be in dry-run)."""
    try:
        original = path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  SKIP  {path}  (read error: {e})")
        return False
    fixed = sanitize_mermaid_blocks_in_markdown(original)
    if fixed == original:
        return False
    if dry_run:
        print(f"  WOULD-FIX  {path}  (mermaid blocks would be re-rendered)")
        return True
    try:
        path.write_text(fixed, encoding="utf-8")
        # Show roughly what changed so the user can spot-check.
        n_orig = original.count("```mermaid")
        delta = len(fixed) - len(original)
        sign = "+" if delta >= 0 else ""
        print(
            f"  FIXED      {path}  "
            f"({n_orig} mermaid block{'s' if n_orig != 1 else ''}, "
            f"{sign}{delta} chars)"
        )
    except Exception as e:
        print(f"  ERROR      {path}  (write failed: {e})")
        return False
    return True


def walk(target: Path, *, dry_run: bool) -> tuple[int, int]:
    """Recursively sanitise every .md file under `target`. Returns
    (n_files_scanned, n_files_changed)."""
    if target.is_file():
        files = [target] if target.suffix.lower() == ".md" else []
    else:
        files = sorted(target.rglob("*.md"))
    scanned = 0
    changed = 0
    for f in files:
        # Skip cache directories that the docx pipeline writes into.
        if any(part.startswith(".") for part in f.parts):
            continue
        scanned += 1
        if sanitise_one_file(f, dry_run=dry_run):
            changed += 1
    return scanned, changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sanitise mermaid blocks in existing output markdown files.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="output",
        help="Path to a project dir, file, or `output/`. Defaults to `output/`.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report which files WOULD change without writing.",
    )
    args = parser.parse_args()

    target = Path(args.target).resolve()
    if not target.exists():
        print(f"ERROR: {target} does not exist.", file=sys.stderr)
        return 2

    print(f"Scanning {target} for *.md files (dry_run={args.dry_run})...")
    scanned, changed = walk(target, dry_run=args.dry_run)
    print()
    print(f"Done. Scanned {scanned} markdown file(s); "
          f"{'would-fix' if args.dry_run else 'fixed'} {changed}.")
    if args.dry_run and changed > 0:
        print("Re-run without --dry-run to apply the fixes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
