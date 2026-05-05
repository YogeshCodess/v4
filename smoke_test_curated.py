"""
smoke_test_curated.py - quick verification of the new curated spec
library + diff detection + second-pass validator.

Run with:
    python smoke_test_curated.py

Prints PASS/FAIL for each check. No external dependencies beyond what
the main project already needs (pydantic + the project's own modules).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


# -- 1. All curated specs parse against the schema --------------------
def test_curated_specs_parse() -> int:
    from schemas.component_spec import ComponentSpec
    spec_dir = Path(__file__).parent / "data" / "component_specs"
    files = [f for f in spec_dir.glob("*.json") if not f.name.startswith("_")]
    ok = 0
    fail = 0
    for f in sorted(files):
        try:
            ComponentSpec(**json.loads(f.read_text(encoding="utf-8")))
            ok += 1
        except Exception as e:
            print(f"  FAIL {f.name}: {e}")
            fail += 1
    print(f"[1] curated specs parse: {ok} OK, {fail} FAIL")
    return 0 if fail == 0 else 1


# -- 2. Resolver returns curated source for known parts --------------
def test_resolver_hits_curated() -> int:
    import services.component_spec_resolver as r
    sample = [
        "ADF4351", "LMX2594", "HMC7044", "AD9082", "AD9528", "LMK04828",
        "W25Q128JV", "N25Q256", "S25FL128S", "PCA9555", "INA226",
        "FT232H", "TMP102", "ADS1115", "ADMV1013", "ADMV1014",
        "ADRF6510", "LTC5594", "AD8367", "AD7193", "MCP4725",
        "TCA9548A", "MCP23017",
    ]
    bad = []
    for mpn in sample:
        spec = r.resolve(mpn=mpn)
        if spec.source != "curated" or spec.confidence < 0.99:
            bad.append((mpn, spec.source, spec.confidence))
    if bad:
        for mpn, src, c in bad:
            print(f"  FAIL {mpn}: source={src} conf={c}")
        return 1
    print(f"[2] resolver hits curated: {len(sample)}/{len(sample)} parts OK")
    return 0


# -- 3. Curated values flow into emitted Verilog ---------------------
def test_rtl_emission_uses_curated() -> int:
    import services.component_spec_resolver as r
    from agents import rtl_components as rc
    failures = []

    # Flash controller for W25Q128JV: must contain real opcodes
    flash_v = rc.flash_ctrl(r.resolve(mpn="W25Q128JV"))
    if not (("0x02" in flash_v or "8'h02" in flash_v) and
            ("0x06" in flash_v or "8'h06" in flash_v)):
        failures.append("flash_ctrl(W25Q128JV) missing opcode 0x02 or 0x06")

    # EEPROM driver for AT24C256C: must contain slave addr 0x50
    eep_v = rc.eeprom_driver(r.resolve(mpn="AT24C256C"))
    if not ("0x50" in eep_v or "7'h50" in eep_v or "h50" in eep_v.lower()):
        failures.append("eeprom_driver(AT24C256C) missing slave addr 0x50")

    # PLL config sequencer for ADF4351: must reference the part by name
    pll_v = rc.pll_config_sequencer(r.resolve(mpn="ADF4351"))
    if "ADF4351" not in pll_v:
        failures.append("pll_config_sequencer(ADF4351) missing part name reference")

    if failures:
        for f in failures:
            print(f"  FAIL {f}")
        return 1
    print("[3] RTL emission uses curated values: 3/3 components OK")
    return 0


# -- 4. Diff detection works (hash + diff queue) ---------------------
def test_diff_detection() -> int:
    import services.datasheet_extractor as dx
    # Wipe any TEST_ leftovers
    idx = dx._load_hash_index()
    for k in list(idx.keys()):
        if k.startswith("TEST_"):
            del idx[k]
    dx._save_hash_index(idx)

    pdf_v1 = b"%PDF-1.4\nfake content v1\n%%EOF"
    pdf_v2 = b"%PDF-1.4\nfake content v2 REVISED\n%%EOF"

    sha1, c1 = dx._record_pdf_hash("TEST_DIFF_PART", "https://x.invalid", pdf_v1)
    sha1b, c1b = dx._record_pdf_hash("TEST_DIFF_PART", "https://x.invalid", pdf_v1)
    sha2, c2 = dx._record_pdf_hash("TEST_DIFF_PART", "https://x.invalid", pdf_v2)

    fail = []
    if c1 is not False:
        fail.append("first fetch should not flag changed")
    if c1b is not False:
        fail.append("identical refetch should not flag changed")
    if c2 is not True:
        fail.append("modified PDF must flag changed")
    if sha1 == sha2:
        fail.append("hashes should differ for different content")

    queue = dx.list_diff_review_queue()
    test_events = [e for e in queue if e.get("mpn") == "TEST_DIFF_PART"]
    if not test_events:
        fail.append("diff event not queued")

    # Cleanup
    idx = dx._load_hash_index()
    if "TEST_DIFF_PART" in idx:
        del idx["TEST_DIFF_PART"]
    dx._save_hash_index(idx)
    qpath = dx._DIFF_REVIEW_QUEUE
    if qpath.exists():
        keep = [l for l in qpath.read_text().splitlines() if "TEST_DIFF_PART" not in l]
        qpath.write_text("\n".join(keep) + ("\n" if keep else ""))

    if fail:
        for f in fail:
            print(f"  FAIL {f}")
        return 1
    print("[4] diff detection: hash recorded + diff event queued")
    return 0


# -- 5. Second-pass validator behaves correctly (mocked LLM) ---------
def test_second_pass_validator() -> int:
    import asyncio
    import services.datasheet_extractor as dx
    from schemas.component_spec import ComponentSpec
    fail = []

    # Curated short-circuits
    curated = ComponentSpec(mpn="X", bus="i2c", source="curated", confidence=1.0)
    out, cons = dx.validate_spec_against_pdf(curated, "ds text")
    if out.confidence != 1.0 or cons != []:
        fail.append("curated spec should short-circuit unchanged")

    # Mock contradictions
    async def fake_contradictions(spec, text):
        return [
            {"field": "i2c_slave_addr_7bit", "claimed": 80,
             "datasheet_says": 104, "evidence": "addr=0x68"},
            {"field": "spi_max_clock_hz", "claimed": 100_000_000,
             "datasheet_says": 50_000_000, "evidence": "fSPI=50MHz"},
        ]
    dx._validate_via_llm_async = fake_contradictions
    extracted = ComponentSpec(mpn="FAKE", bus="spi",
                              source="llm_extracted", confidence=0.7)
    out, cons = dx.validate_spec_against_pdf(extracted, "ds text")
    if abs(out.confidence - 0.30) > 0.01:
        fail.append(f"2 contradictions should drop confidence to 0.30, got {out.confidence}")
    if len(cons) != 2:
        fail.append("should return both contradictions")

    # Clean validation
    async def fake_clean(spec, text):
        return []
    dx._validate_via_llm_async = fake_clean
    extracted2 = ComponentSpec(mpn="FAKE2", bus="i2c",
                               source="llm_extracted", confidence=0.7)
    out, cons = dx.validate_spec_against_pdf(extracted2, "ds text")
    if abs(out.confidence - 0.75) > 0.01:
        fail.append(f"clean validation should bump confidence to 0.75, got {out.confidence}")

    if fail:
        for f in fail:
            print(f"  FAIL {f}")
        return 1
    print("[5] second-pass validator: curated short-circuit + contradictions + clean all OK")
    return 0


# -- 6. Coverage emitter produces valid SystemVerilog ---------------
def test_coverage_emitter() -> int:
    from agents.rtl_coverage import render_coverage_sv
    from schemas.project_brief import ProjectBrief, Register, FSM
    fail = []

    # Empty brief -> stub covergroups (always compiles)
    empty = ProjectBrief(project_name="empty")
    sv = render_coverage_sv(empty)
    if "class fpga_coverage" not in sv or "endclass" not in sv:
        fail.append("empty brief should still emit a valid class skeleton")

    # Real brief with registers + FSMs
    brief = ProjectBrief(
        project_name="test",
        registers=[
            Register(name="STATUS", address="0x0001", reset_value="0x00"),
            Register(name="CONTROL", address="0x0002", reset_value="0x00"),
        ],
        fsms=[FSM(name="spi_master", states=["IDLE", "LOAD", "DONE"])],
    )
    sv = render_coverage_sv(brief)
    expected_markers = [
        "covergroup cg_register_address",
        "covergroup cg_access_type",
        "covergroup cg_reset_value",
        "covergroup cg_spi_master_state",
        "function void sample_register_access",
        "function void sample_spi_master_state",
        "function void report",
        "STATUS_b = { 32'h0001 }",
        "CONTROL_b = { 32'h0002 }",
        "IDLE_b = { 0 }",
        "LOAD_b = { 1 }",
        "DONE_b = { 2 }",
    ]
    for m in expected_markers:
        if m not in sv:
            fail.append(f"emitted SV missing expected marker: {m!r}")

    if fail:
        for f in fail:
            print(f"  FAIL {f}")
        return 1
    print("[6] coverage emitter: stub + 12 markers present in real-brief output")
    return 0


# -- main -------------------------------------------------------------
def main() -> int:
    sys.path.insert(0, str(Path(__file__).parent))
    print("=" * 60)
    print("Curated Spec Library + Diff Detection + Validator + Coverage Smoke Test")
    print("=" * 60)
    fails = 0
    for fn in [
        test_curated_specs_parse,
        test_resolver_hits_curated,
        test_rtl_emission_uses_curated,
        test_diff_detection,
        test_second_pass_validator,
        test_coverage_emitter,
    ]:
        try:
            fails += fn()
        except Exception as e:
            print(f"  CRASH in {fn.__name__}: {e}")
            fails += 1
    print("=" * 60)
    if fails == 0:
        print("ALL CHECKS PASSED")
    else:
        print(f"{fails} CHECK(S) FAILED")
    return fails


if __name__ == "__main__":
    sys.exit(main())
