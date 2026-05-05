"""
patch_bundle_p7.py - Patches frontend/bundle.html in place to add the
v23.4.x RTL files (fpga_coverage.sv, flash_ctrl.v, gpio_ctrl.v, etc.)
to the P7 documents whitelist.

Use when you can't rebuild the React app (npm install hangs / no Node).
Idempotent: running twice is safe.

Run from the project root:
    python patch_bundle_p7.py
"""
from __future__ import annotations
import sys
from pathlib import Path

BUNDLE = Path(__file__).parent / "frontend" / "bundle.html"

# The OLD whitelist (4 files) — exact substring as bundled by vite
OLD = ('"fpga_design_report.md","rtl/fpga_top.v",'
       '"rtl/fpga_testbench.v","rtl/constraints.xdc"')

# The NEW whitelist (13 files) — covers every file the v23.4.x emitter writes
NEW = ('"fpga_design_report.md","rtl/fpga_top.v",'
       '"rtl/fpga_testbench.v","rtl/fpga_coverage.sv","rtl/constraints.xdc",'
       '"rtl/uart_engine.v","rtl/spi_master.v","rtl/i2c_master.v",'
       '"rtl/pll_config.v","rtl/adc_capture.v","rtl/eeprom_driver.v",'
       '"rtl/flash_ctrl.v","rtl/gpio_ctrl.v"')

# Optional: also bump the static "outputs" description shown in the OUTPUTS panel.
OLD_OUT = ('"fpga_top.v (Verilog RTL)","fpga_testbench.v (SystemVerilog TB)",'
           '"constraints.xdc (Vivado)","FPGA design report (.md)"')
NEW_OUT = ('"fpga_top.v (Verilog RTL)","fpga_testbench.v (SystemVerilog TB)",'
           '"fpga_coverage.sv (SV covergroups)","constraints.xdc (Vivado)",'
           '"rtl/*.v component controllers (uart/spi/i2c/pll/adc/flash/gpio/eeprom)",'
           '"FPGA design report (.md)"')


def main() -> int:
    if not BUNDLE.exists():
        print(f"ERROR: {BUNDLE} not found", file=sys.stderr)
        return 1

    src = BUNDLE.read_text(encoding="utf-8", errors="replace")
    before_size = len(src)
    changes = 0

    if NEW in src:
        print("[SKIP] P7 whitelist already patched (NEW substring present)")
    elif OLD in src:
        src = src.replace(OLD, NEW, 1)
        changes += 1
        print("[OK]   P7 whitelist patched: 4 files -> 13 files")
    else:
        print("[WARN] P7 whitelist substring not found — bundle may be from a "
              "different vite build. No-op.", file=sys.stderr)

    if NEW_OUT in src:
        print("[SKIP] P7 outputs description already patched")
    elif OLD_OUT in src:
        src = src.replace(OLD_OUT, NEW_OUT, 1)
        changes += 1
        print("[OK]   P7 outputs description patched")
    else:
        print("[NOTE] P7 outputs description substring not found — non-fatal "
              "(just affects the OUTPUTS panel text, not the file list).")

    if changes:
        BUNDLE.write_text(src, encoding="utf-8")
        print(f"[OK]   bundle.html written ({before_size} -> {len(src)} bytes, "
              f"{changes} change(s))")
        print("[OK]   Restart the FastAPI backend OR hard-refresh the browser "
              "(Ctrl+Shift+R) to see the new file list in P7 Documents.")
    else:
        print("[OK]   No changes needed — bundle is already up to date.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
