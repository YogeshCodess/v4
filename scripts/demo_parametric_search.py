"""Demo — retrieval-augmented component selection.

For each RF-chain stage, query DigiKey + Mouser live and print the real
candidate parts the LLM would pick from. This is the shortlist that
replaces the "LLM invents MPNs from training data" step.

Usage:
    python scripts/demo_parametric_search.py
    python scripts/demo_parametric_search.py lna "2-18 GHz NF<2dB"
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from tools import digikey_api, mouser_api
from tools.parametric_search import find_candidates


# Mini RF receiver chain for the demo. Each (stage, hint) pair mirrors
# what the LLM would pass after the P1 wizard captures a spec sheet.
DEFAULT_CHAIN: list[tuple[str, str]] = [
    ("limiter",     "RF limiter 2-18 GHz"),
    ("preselector", "ceramic bandpass filter 2-18 GHz"),
    ("lna",         "2-18 GHz wideband low noise"),
    ("mixer",       "double balanced 4-8 GHz"),
    ("bpf",         "SAW filter 70 MHz IF"),
    ("adc",         "12-bit 1 GSPS JESD204B"),
    ("tcxo",        "10 MHz 0.5 ppm"),
]


def _print_stage(stage: str, hint: str, max_per_source: int = 5) -> int:
    print(f"=== {stage.upper():<14} hint={hint!r}")
    candidates = find_candidates(stage, hint, max_per_source=max_per_source)
    if not candidates:
        print("   (no candidates — both APIs returned empty)")
        print()
        return 0

    for i, c in enumerate(candidates, 1):
        ds = c.datasheet_url or "-"
        if len(ds) > 70:
            ds = ds[:67] + "..."
        print(f"  {i:>2}. [{c.source:7}] {c.part_number:<26.26} {c.manufacturer:<28.28} "
              f"lifecycle={c.lifecycle_status}")
        desc = (c.description or "").strip()
        if desc:
            print(f"       desc: {desc[:90]}")
        print(f"       ds  : {ds}")
    print()
    return len(candidates)


def main(argv: list[str]) -> int:
    print("DigiKey configured:", digikey_api.is_configured())
    print("Mouser  configured:", mouser_api.is_configured())
    print()

    if len(argv) > 1:
        stage = argv[1]
        hint = argv[2] if len(argv) > 2 else ""
        return 0 if _print_stage(stage, hint, max_per_source=8) else 1

    total = 0
    for stage, hint in DEFAULT_CHAIN:
        total += _print_stage(stage, hint)

    print(f"Total candidates across chain: {total}")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
