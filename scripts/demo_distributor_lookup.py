"""Demo — live component lookups against DigiKey + Mouser.

Prints a table showing whether each MPN was found, who answered, and
what lifecycle state the distributor reported. Useful for:

  - Sanity-checking API keys after a .env change.
  - Confirming an MPN the LLM emitted is real before the red-team audit
    flags it as hallucinated.

Usage:
    python scripts/demo_distributor_lookup.py
    python scripts/demo_distributor_lookup.py STM32F407VGT6 NE555 BGA7210,115

Requires DIGIKEY_CLIENT_ID / DIGIKEY_CLIENT_SECRET and/or MOUSER_API_KEY
in the process environment (load via your usual .env loader before running).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the project root importable whether the script is run from the
# repo root or from scripts/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Load .env if python-dotenv is available — otherwise assume the user
# exported the vars themselves.
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from tools import digikey_api, mouser_api
from tools.distributor_search import lookup


# A mix of parts chosen to exercise the fallback chain:
#   - real + common:         NE555, LM317T, STM32F407VGT6
#   - real RF/MW:            MGA-62563-BLKG (Broadcom LNA, obsolete)
#   - real Mini-Circuits:    ZFSC-2-1+ (splitter; '+' tests URL encoding)
#   - invented by the LLM:   ZFBSC-2-1+, MADL-011017, AFB-8500
DEFAULT_SAMPLE = [
    "NE555",
    "LM317T",
    "STM32F407VGT6",
    "MGA-62563-BLKG",
    "ZFSC-2-1+",
    "ZFBSC-2-1+",    # hallucination — real part is ZFSC-2-1+
    "MADL-011017",   # hallucination — MADL prefix is real, this MPN is not
    "AFB-8500",      # hallucination — Anatech filter; MPN invented
]


def _status_row(pn: str) -> dict:
    """Resolve a part against DigiKey → Mouser → seed and return a row."""
    info = lookup(pn, timeout_s=10)
    if info is None:
        return {
            "mpn": pn,
            "found": False,
            "source": "-",
            "manufacturer": "-",
            "lifecycle": "-",
            "description": "NOT FOUND (candidate hallucination)",
        }
    return {
        "mpn": pn,
        "found": True,
        "source": info.source,
        "manufacturer": info.manufacturer or "-",
        "lifecycle": info.lifecycle_status or "-",
        "description": (info.description or "")[:60],
    }


def main(argv: list[str]) -> int:
    parts = argv[1:] if len(argv) > 1 else DEFAULT_SAMPLE

    print("DigiKey configured:", digikey_api.is_configured())
    print("Mouser  configured:", mouser_api.is_configured())
    print()

    if not (digikey_api.is_configured() or mouser_api.is_configured()):
        print(
            "No distributor API keys in the environment — every lookup "
            "will fall through to the local seed (data/sample_components.json)."
        )
        print()

    print(f"{'MPN':<22} {'FOUND':<6} {'SOURCE':<9} {'MFR':<22} {'LIFECYCLE':<10} DESCRIPTION")
    print("-" * 110)
    hallucinated: list[str] = []
    for pn in parts:
        row = _status_row(pn)
        found_mark = "YES" if row["found"] else "NO"
        print(
            f"{row['mpn']:<22} {found_mark:<6} {row['source']:<9} "
            f"{row['manufacturer']:<22.22} {row['lifecycle']:<10} {row['description']}"
        )
        if not row["found"]:
            hallucinated.append(pn)

    print()
    if hallucinated:
        print(f"Missing ({len(hallucinated)}): " + ", ".join(hallucinated))
        print(
            "These would be flagged `hallucinated_part` (critical) by "
            "services/rf_audit if an LLM emitted them."
        )
    else:
        print("All parts resolved.")
    return 0 if not hallucinated else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
