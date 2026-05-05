#!/usr/bin/env python3
"""Run the golden-regression harness. Equivalent of `make golden`."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    cmd = [sys.executable, "-m", "pytest", "tests/test_golden.py", "-v"]
    return subprocess.call(cmd, cwd=str(REPO))


if __name__ == "__main__":
    sys.exit(main())
