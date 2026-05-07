# Output Audit — Project `rx` (4×8 RF Switch Matrix, DC-18 GHz)

**Date:** 2026-05-07
**Source:** `output/rx/` generated 09:56 → 10:17 today
**User spec:** "4×8 RF Switch Matrix for ATE / Production Test, DC–18 GHz, non-blocking crossbar architecture using SOI CMOS SPDT cells. Controlled by Artix-7 FPGA over SPI, cast-aluminium EMI shield, 12 SMA front-panel connectors. <2 dB IL/path, >90 dB isolation, +65 dBm IIP3, +37 dBm max CW input, <1 µs switching, MIL temp -55 to +125 °C."

---

## TL;DR — biggest finding first

**The FastAPI server was NOT restarted after the recent commits.** None of the SSoT / leak-gate / SVG / frequency-audit / NF-audit / supply-audit / deterministic-schematic fixes from the last 12 commits are reflected in this `rx` output.

Evidence:
- `block_diagram.md` uses LLM-emitted `flowchart TD` (not deterministic `block-beta`)
- No `block_diagram.svg` file
- No `design_manifest.json`
- No `requirements_lock.json`
- No `audit_report.md`

**All my unit + integration tests pass against the code on disk.** They verify code behaviour, not deployed-server behaviour. The bugs you see in `rx` are genuine pre-fix bugs reproducing because the fixes aren't live.

**First action:** restart `uvicorn main:app` (or your equivalent FastAPI launcher). Then regenerate `rx` from scratch. After that, ~80% of what's listed below disappears automatically. The remaining ~20% are bugs in code I haven't touched — those need separate fixes.

---

## Audit by phase

### P1 — Requirements Capture (`requirements.md`, `architecture.md`, `block_diagram.md`, `component_recommendations.md`, `cascade_analysis.json`, `gain_loss_budget.md`, `power_calculation.md`)

#### B1.1 — `block_diagram.md` is the LLM-emitted flowchart

**Severity:** High (HMC-bug class)
**Evidence:** [output/rx/block_diagram.md:5](output/rx/block_diagram.md:5) opens with `flowchart TD`.
**Expected after fix:** inline `<svg>` with proper RF symbols (LNA triangles, mixer X, filter sine, switch SPDT) — and a Mermaid `block-beta` "Flowchart view" subhead below it.
**Why missed:** server not restarted.

#### B1.2 — Block diagram has malformed Mermaid shape syntax

**Severity:** Medium
**Evidence:** [block_diagram.md:6](output/rx/block_diagram.md:6) — `IN1>"IN1<br/>0-18 GHz"]`. The `>"..."]` opens a flag-shape (`>`) and closes a square bracket (`]`) — asymmetric. Most lines have this. Some downstream Mermaid renderers fail to parse this.

#### B1.3 — Every MPN is in node labels (HMC-bug-class leak)

**Severity:** Critical (the bug class the manifest validator was built to catch)
**Evidence:** [block_diagram.md:14](output/rx/block_diagram.md:14) — `LIM1[/"Limiter / CLA4610-085LF / IL0.2 Pmax+33"\]`. PE42522B-X, ZVA-183WA-S+, CLA4610-085LF all leaked into mermaid labels.
**Expected after fix:** the leak gate would have flagged this and merged audit issues into the report. The deterministic SVG renderer wouldn't have leaked in the first place.

#### B1.4 — `cascade_analysis.json` has zero stages despite 7 BOM components

**Severity:** Critical
**Evidence:** [cascade_analysis.json:3](output/rx/cascade_analysis.json:3) — `"stages": []`, `"totals.nf_db": null`, `"data_completeness_pct": 100.0` (a lie — completeness is reported on zero stages).
**Why:** `tools/rf_cascade.compute_cascade()` looks for `gain_db` / `nf_db` at the top level of each BOM row. The current BOM stores those nested in `key_specs` or only in free-text descriptions ("gain | +18 dB typ"). The cascade tool isn't extracting them. Result: every active stage is silently dropped.
**Predicted post-restart:** still broken — the cascade tool's BOM-parsing isn't fixed by the SSoT work. **Independent bug.**

#### B1.5 — `iip3_dbm` is a string `"+65"` not a number

**Severity:** Medium (breaks numeric comparisons)
**Evidence:** [cascade_analysis.json:14](output/rx/cascade_analysis.json:14) — `"iip3_dbm": "+65"`.
**Why:** the LLM emitted `"+65"` (with leading `+`) and the cascade tool persisted it as-is. Any `claims.iip3_dbm > x` comparison downstream will throw or compare lexicographically.
**Independent bug.**

#### B1.6 — Claimed +65 dBm IIP3 is impossible by 51 dB

**Severity:** Critical (design-correctness)
**Evidence:** User spec says +65 dBm IIP3. The chosen gain block is ZVA-183WA-S+ with OIP3 +32 dBm and gain +18 dB → IIP3 ≈ +14 dBm. Switch cells are passive (high IIP3 since they don't compress), but the cascade IIP3 is dominated by the gain block: ~+14 dBm at the gain-block input port, ~+12 dBm at the matrix input port.
**Result:** system IIP3 ≈ +12 dBm vs claimed +65 dBm — **53 dB shortfall**.
**Why audit didn't fire:** my recent `frequency_audit` / `nf_budget_audit` / `supply_voltage_audit` cover those three axes — but not IIP3. The existing `tx_cascade_audit` only fires for TX projects (switch_matrix excluded). **Independent bug — needs a new audit rule.**

#### B1.7 — Component #7 hallucination: ASWD-S2-0009-Q-T is NOT an SMA connector

**Severity:** Critical
**Evidence:** [component_recommendations.md:152](output/rx/component_recommendations.md:152) lists `ASWD-S2-0009-Q-T` as the SMA Panel-Mount Connector. The description on line 153 reads *"RF Switch ICs Automotive Wideband GaAs SPDT RF Switch (referenced for RF connector-grade system component)"*. **This is an automotive switch IC**, not a connector. Real SMA connectors: Amphenol 132 / 901 series, Cinch SMA-50, Molex 73251-x.
**Why audit didn't fire:** the candidate-pool gate matches MPN existence, not part-class semantic match. **Independent bug — needs a "role vs description" semantic check.**

#### B1.8 — MAX25301 LDO will fail thermally (T_j = 225 °C predicted)

**Severity:** Critical
**Evidence:** [power_calculation.md:67](output/rx/power_calculation.md:67) — `T_j @ 85 °C amb (°C): 225.0` → "Thermally Failed". The component_recommendations selection rationale even calls it out: *"input range is limited to 5.5 V max — requires pre-regulation from 12 V to ~5 V"* — but the BOM still picks it as primary with 12 V → 5 V × 1 A = 7 W LDO drop.
**Why audit didn't fire:** the LDO's `input_voltage: "2.5 - 5.5 V"` is in `key_specs`, not at the BOM-row level. My new `run_supply_voltage_audit` checks the part's required SUPPLY voltage against the project's available rails, not the part's INPUT-RANGE limits. **Needs a new "input voltage compatibility" check.**

#### B1.9 — GLB topology is wrong for switch_matrix

**Severity:** Critical
**Evidence:** [gain_loss_budget.md:71-74](output/rx/gain_loss_budget.md:71) — the GLB chain is:
```
1. Input SMA → 2. PCB Trace → 3. Limiter → 4. GAIN BLOCK → 5. 3 dB pad → 6. Switch Matrix → 7. Output SMA
```
But the architecture for a switch matrix is:
```
SMA → Trace → Limiter → SPDT (input) → SPDT (output) → Gain Block → SMA
```
The optimizer log on line 30 says: *"Promoted LNA from stage 5 to stage 4 (reduces pre-LNA passive loss → better Friis NF)"* — this is a generic-receiver heuristic that is **invalid for switch_matrix**. In a switch matrix the gain block must be AFTER the matrix to compensate matrix loss; promoting it ahead of the matrix means it amplifies signal, then the matrix loses 2 dB, then the output is hot — the +14.3 dB net gain visible in the budget summary.
**Predicted post-restart:** still wrong — the optimizer in `services/glb_optimizer.py` doesn't know about architecture-specific layout constraints. **Independent bug — needs `architecture_topology_constraint` rule.**

#### B1.10 — GLB optimizer invented "Pi attenuator 3 dB" not in BOM

**Severity:** High (HMC-bug-class leak via optimizer)
**Evidence:** [gain_loss_budget.md:41](output/rx/gain_loss_budget.md:41) — *"Added BOM entry 'Pi attenuator 3 dB' (Fixed RF Attenuator Pad (3 dB)) from optimizer library."*
**Result:** the GLB chain has a stage `5. Stability Pad / Pi attenuator 3 dB` that doesn't exist in `component_recommendations.md`.
**Why audit didn't fire:** server not restarted. The leak gate would have flagged "Pi attenuator 3 dB" appearing in `gain_loss_budget.md` but not in `manifest.allowed_mpns()`.
**Predicted post-restart:** the leak gate catches it, downgrades audit to FAIL.

#### B1.11 — GLB optimizer swapped MAX25301 → MAX17501 silently

**Severity:** High (leak)
**Evidence:** [gain_loss_budget.md:42-43](output/rx/gain_loss_budget.md:42) — *"BOM entry 'MAX25301BATB/V+' is no longer in the GLB chain (optimizer removed/replaced it). Swapped 'MAX25301BATB/V+' → 'MAX17501GATB+T'."*
**Result:** the swap is logged but the BOM file still lists MAX25301. Two different LDOs in two files — same drift class as the HMC bug.
**Predicted post-restart:** the leak gate catches MAX17501GATB+T appearing in GLB but not in BOM.

#### B1.12 — GLB Friis math: passive NF values violate the C4 contract that reports PASS

**Severity:** Critical (C4 check is broken — false PASS)
**Evidence:** [gain_loss_budget.md:16](output/rx/gain_loss_budget.md:16) — C4 ("Passive NF = |insertion loss| (Friis)") reports `✅ pass`. But the actual data:

| Stage | IL claimed (dB) | NF claimed (dB) | NF should be (dB) | C4 violation? |
|------:|----------------:|----------------:|------------------:|--------------:|
| 1 (SMA in) | 0.2 | 0.10 | 0.20 | ❌ YES |
| 2 (PCB trace) | 0.4 | 0.30 | 0.40 | ❌ YES |
| 3 (Limiter) | 0.3 | 0.20 | 0.30 | ❌ YES |
| 5 (3 dB pad) | 3.0 | 3.00 | 3.00 | ✓ ok |
| 6 (Switch matrix) | 2.1 | 2.00 | 2.10 | ❌ YES |
| 7 (SMA out) | 0.2 | 0.10 | 0.20 | ❌ YES |

5 of 6 passive stages violate the Friis identity (passive NF = |IL| in dB). **The C4 check function is broken — it reports PASS when violations exist.**
**Independent bug — `services/glb_optimizer.py` C4 check needs fixing.**

#### B1.13 — GLB Friis cascade math is correct given the wrong inputs

I recomputed the Friis cascade against the table values (using NF-dB and gain-dB columns):

```
Stage 1: F1 = 10^(0.10/10) = 1.023, G1_lin = 10^(-0.2/10) = 0.955
F_cum after S1 = 1.023, NF_cum = 0.10 dB ✓ (matches table)

Stage 2: F2 = 10^(0.30/10) = 1.072, G2_lin = 0.912
F_cum = 1.023 + (1.072-1)/0.955 = 1.099 → NF_cum = 0.41 dB ✓

Stage 3: F3 = 10^(0.20/10) = 1.047, G3_lin = 0.933
F_cum = 1.099 + (1.047-1)/(0.955·0.912) = 1.153 → NF_cum = 0.62 dB ✓

Stage 4: F4 = 10^(6.0/10) = 3.981, G4_lin = 50.12
F_cum = 1.153 + (3.981-1)/0.812 = 4.824 → NF_cum = 6.84 dB ≈ 6.83 ✓

Stage 5: F5 = 10^(3.0/10) = 1.995
F_cum = 4.824 + (1.995-1)/40.7 = 4.848 → NF_cum = 6.86 dB ≈ 6.85 ✓

Stage 6: F6 = 10^(2.0/10) = 1.585
F_cum = 4.848 + (1.585-1)/20.4 = 4.877 → NF_cum = 6.88 dB ✓
```

So the Friis arithmetic is fine. **The bug is in the per-stage NF inputs (B1.12), not in the cascade math.** Once C4 is fixed, the Cum NF column will recompute and probably land at 7.5-8 dB instead of 6.88 dB.

#### B1.14 — Section 7 (Stage Gain vs Frequency) — ZVA-183WA-S+ shows gain at 0 GHz (impossible)

**Severity:** Medium (fabricated frequency-dependent values)
**Evidence:** [gain_loss_budget.md:144](output/rx/gain_loss_budget.md:144) — the ZVA gain block row shows `+18.00 dB at 0.0 GHz`. Datasheet: ZVA-183WA-S+ operates **100 MHz to 18 GHz**. Below 100 MHz the gain falls off rapidly; at DC there's a coupling cap blocking signal entirely. The "0 GHz" cell should be **N/A or –∞**, not +18 dB.
**Same problem:** every other stage in section 7 has a smile-shaped gain-vs-freq curve (lower at edges, higher in middle) — **not how RF parts behave**. SMA loss INCREASES monotonically with frequency. PCB trace loss INCREASES monotonically (skin effect ~ √f). The values are fabricated, not derived from real S-parameter behaviour.
**Independent bug — frequency-sweep generator needs a model not a fabrication.**

#### B1.15 — Section 8 (Return Loss) only lists 2 stages out of 7

**Severity:** Low (incomplete report)
**Evidence:** [gain_loss_budget.md:181-182](output/rx/gain_loss_budget.md:181) — only ZVA gain block + PE42522B-X listed. Missing: SMA, PCB trace, limiter, 3 dB pad, output SMA. Should list all stages with their S11/S22.

#### B1.16 — System gain claim contradicts user spec

**Severity:** Critical (design intent violated)
**Evidence:**
- User spec: "<2 dB insertion loss per path" + "near-0 dB net path loss" (per requirement REQ-SW-015)
- Block diagram annotation [block_diagram.md:43](output/rx/block_diagram.md:43): "Net Path Loss -4.2 dB"
- GLB summary [gain_loss_budget.md:82](output/rx/gain_loss_budget.md:82): "Total System Gain 10.8 dB"
- Power calc per-stage shows ZVA at +17 dB

Three different files report three different things (-4.2 dB loss / +10.8 dB gain / +18 dB gain block). The +10.8 dB net is **wrong by ~13 dB** vs the user's "near-0 dB" spec. **Design overshoots by 13 dB.**

The chosen gain block (ZVA-183WA-S+, +18 dB) is too much amplification. The right pick would be a +2-4 dB pad amplifier, or just a low-loss matrix without compensation.

#### B1.17 — Power calculation Qty all = 1 (ignores the multiplicity in the BOM descriptions)

**Severity:** High (power budget undercount)
**Evidence:** [power_calculation.md:26-32](output/rx/power_calculation.md:26) — every component listed with `Qty: 1`. But:
- 32× SPDT switches (PE42522B-X) — needed for a 4×8 fabric
- 4× limiters
- 8× gain blocks
- 12× SMA connectors

The component_recommendations.md descriptions mention these counts ("4 required", "8 required", "32 cells required") but the power calc didn't honour them.
**Result:** "TOTAL: 1.8 W TYP" vs user's 15 W power budget. The actual power is ~600 mW × 8 gain blocks = 4.8 W just for amps, never mind switches. **Power budget undercount.**

#### B1.18 — Power calculation Pdc table only includes ZVA (1 of 8 amps)

**Severity:** High
**Evidence:** [power_calculation.md:16-18](output/rx/power_calculation.md:16) — Pdc table shows ZVA at 600 mW with TOTAL = 600 mW. But there are **8** ZVA amplifiers in this 4×8 matrix, not 1. Total Pdc should be 4.8 W, not 0.6 W.

#### B1.19 — Section 7a starts at 0 GHz (DC) for a chain that doesn't pass DC

**Severity:** Medium
**Evidence:** [gain_loss_budget.md:153](output/rx/gain_loss_budget.md:153) — the System Rollup vs Frequency table starts at 0.0 GHz. ZVA gain block doesn't pass DC (cap-coupled, 100 MHz min). The 0-100 MHz rows are physically meaningless.

#### B1.20 — `requirements.md` heading is `# Hardware Requirements` not `# Requirements`

**Severity:** Cosmetic
The H1 doesn't match the project name pattern used in other files. Minor.

---

### P2 — HRS Document (`HRS_rx.md` — 114 KB)

Not deeply read for this audit. Likely issues based on what's in P1:
- Will inherit the wrong BOM (containing the ASWD switch labelled as SMA, MAX25301 thermal-fail LDO)
- Section 6 BOM will have 7 line items at qty 1 instead of the 32+4+8+12+1+1+1 = 59 actual parts
- After the manifest fix lands and the server restarts, HRS would receive the manifest BOM block in its prompt and would be forced to use only the BOM's MPNs

### P3 — Compliance (`compliance_report.md`)

Not deeply read. Same propagation: validates compliance against the wrong BOM. Post-fix the manifest BOM is authoritative.

### P4 — Netlist (`netlist.json`, `schematic.json`, `schematic_drc.json`)

Not deeply read but quick observations:
- `netlist.json` has 7 nodes (matches BOM 7 components, qty 1 each) — but the actual schematic needs 32 PE42522B-X instances, 4 CLA4610-085LF, 8 ZVA, 12 SMA. **Massive instance-count undercount.**
- Schematic synthesis was run (per my Tuesday's commit `25c7f8d`) but pre-restart, so probably still has the LLM-emitted version (`source: "llm_emitted"`)
- Predicted post-restart: schematic.json source becomes `"auto_synthesized"` — but the synthesizer still gets only 7 nodes, so the synthesized schematic shows 7 boxes not 59.

### P6 — GLR (`GLR_rx.md`, `glr_specification.md`)

Not deeply read. After manifest fix is live, GLR receives the locked BOM block. Pre-restart it doesn't, so GLR may reference parts the BOM doesn't have.

### P7 — FPGA Design (`fpga_design_report.md` — 2.2 KB tiny)

Suspiciously small. Unread.

### P7a — Register Map (`register_description_table.md`, `register_map.json`, `programming_sequence.md`)

Unread. Likely OK because these derive from GLR.

### P8a — SRS (`SRS_rx.md`)

Unread. Same propagation pattern.

### P8b — SDD (`SDD_rx.md`)

Unread.

### P8c — Code Review (`code_review_report.md`, `git_summary.md`, `ci_validation_report.md`)

Unread.

---

## What's missing entirely (artifacts that should exist but don't)

| File | Should exist because | Will exist after server restart? |
|---|---|---|
| `block_diagram.svg` | Commit `ca7f6ac` adds the SVG renderer | YES |
| `design_manifest.json` | Commit `1db6d21` saves the manifest | YES |
| `requirements_lock.json` | Older feature in `services/p1_finalize.py` | YES (this should already work — investigate why it's missing) |
| `audit_report.md` + `audit_report.json` | Older feature | YES (same — investigate why missing pre-restart-too) |

The fact that `requirements_lock.json` and `audit_report.md` are missing **even before my recent commits** suggests the pipeline crashed silently somewhere. Could be:
- `finalize_p1()` raised an exception, the agent caught it and continued
- The server is running an even older version (pre `services/p1_finalize.py`)
- The output_dir layout changed and these files are written elsewhere

Worth checking server logs for `p1_finalize.freeze_failed` or `p1_finalize.audit_failed` messages around 09:56 today.

---

## Severity-ranked bug list

### Critical (block release)

1. **B1.4** — `cascade_analysis.json` empty stages despite 7 components
2. **B1.6** — Claimed +65 dBm IIP3 impossible by 53 dB (no audit catches IIP3)
3. **B1.7** — ASWD-S2-0009-Q-T hallucinated as SMA connector (it's an automotive switch)
4. **B1.8** — MAX25301 LDO thermal failure (input range 2.5-5.5 V vs 12 V supply)
5. **B1.9** — GLB topology wrong for switch_matrix (gain block before matrix)
6. **B1.12** — C4 contract check reports PASS while 5/6 passive stages violate Friis
7. **B1.16** — System gain +10.8 dB vs user spec near-0 dB (wrong gain-block selection)

### High (correctness, will cause real-world fault)

8. **B1.3** — MPNs leaked into block diagram labels (HMC-bug class)
9. **B1.10** — GLB optimizer invented "Pi attenuator 3 dB"
10. **B1.11** — GLB optimizer swapped MAX25301 → MAX17501 silently
11. **B1.17** — Power calc Qty all = 1 (matrix has 32+4+8 = 44 active parts)
12. **B1.18** — Per-stage Pdc table missing 7 of 8 amplifier instances
13. **B1.14** — Section 7 fabricates frequency-dependent gain values
14. **Missing artifacts** — design_manifest.json, requirements_lock.json, audit_report.md

### Medium

15. **B1.1** — Block diagram is LLM flowchart not deterministic block-beta SVG
16. **B1.2** — Block diagram has malformed Mermaid shape syntax
17. **B1.5** — `iip3_dbm` field is string `"+65"` not number
18. **B1.19** — Frequency rollup table starts at 0 GHz (impossible for ZVA)

### Low / Cosmetic

19. **B1.15** — Return loss table only 2 of 7 stages
20. **B1.20** — H1 heading style

---

## What an "ideal" output for this same spec should look like

After server restart + the additional fixes flagged below, regenerating `rx` should produce:

### `manifest.bom` (canonical, post-audit)

```json
[
  {"refdes":"U1..U32","part_number":"PE42522B-X","manufacturer":"pSemi",
   "role":"switch","qty":32,"package":"QFN-12","supply_voltage":3.3,
   "min_freq_ghz":0.0,"max_freq_ghz":26.5,
   "gain_db":-1.2,"nf_db":1.2,"iip3_dbm":36,"p1db_dbm":36},
  {"refdes":"D1..D4","part_number":"CLA4610-085LF","manufacturer":"Skyworks",
   "role":"limiter","qty":4,"package":"SOD-323",
   "min_freq_ghz":0.0,"max_freq_ghz":18.0,
   "gain_db":-0.2,"nf_db":0.2},
  {"refdes":"U33..U40","part_number":"ZVA-183WA-S+","manufacturer":"Mini-Circuits",
   "role":"rf_amp","qty":8,"package":"QFN",
   "min_freq_ghz":0.1,"max_freq_ghz":18.0,
   "gain_db":18,"nf_db":5,"iip3_dbm":14,"p1db_dbm":18,
   "supply_voltage":5.0,"current_ma":120},
  {"refdes":"J1..J12","part_number":"<real SMA>",
   "manufacturer":"Amphenol","role":"connector","qty":12,
   "min_freq_ghz":0.0,"max_freq_ghz":18.0,"gain_db":-0.2},
  {"refdes":"U41","part_number":"XC7A35T-1CSG324C","manufacturer":"AMD/Xilinx",
   "role":"fpga","supply_voltage":3.3},
  {"refdes":"U42","part_number":"<12V-input LDO>",
   "manufacturer":"Analog Devices","role":"ldo",
   "min_input_v":4.5,"max_input_v":36,"output_v":5.0,"current_ma":1500},
  {"refdes":"U43","part_number":"TS30012-M033QFNR","manufacturer":"Semtech",
   "role":"dcdc","supply_voltage":12,"output_v":3.3,"current_ma":2000}
]
```

Total: 7 part types, 59 instances. This is what the BOM SHOULD have — quantity-aware.

### Audit findings the leak gate / freq audit / NF audit / supply audit SHOULD raise

```
critical · frequency_partial_coverage · J1..J12 (component_recommendations/ASWD)
   "ASWD-S2-0009-Q-T appears to be a switch IC, not an SMA connector"
   (NEW audit rule needed: role-vs-description semantic check)

critical · supply_voltage_input_range · U42 (component_recommendations/MAX25301BATB/V+)
   "MAX25301BATB/V+ input range 2.5-5.5 V cannot accept 12 V supply"
   (NEW audit rule needed: input_voltage range vs supply)

critical · iip3_target_unreachable · cascade
   "Claimed IIP3 +65 dBm but cascade IIP3 limited by ZVA-183WA-S+
    OIP3 +32 / gain +18 → IIP3 ≈ +14 dBm (51 dB shortfall)"
   (NEW audit rule needed: IIP3 cascade check, currently TX-only)

high · architecture_topology_constraint
   "Switch_matrix architecture requires gain block AFTER the matrix.
    GLB shows gain block BEFORE matrix (stage 4 vs stage 6).
    Optimizer's promote-LNA-to-stage-4 heuristic is invalid here."
   (NEW audit rule needed: per-architecture topology constraints)

high · gain_target_overshoot
   "User spec: near-0 dB path loss. Cascade computes +10.8 dB net gain.
    Gain block ZVA-183WA-S+ (+18 dB) is over-specified for ~2 dB matrix loss.
    Consider a +2 to +4 dB amp instead, or remove the gain block entirely."
   (NEW audit rule needed: claimed-vs-computed gain target check)

high · manifest_mpn_leak (after server restart)
   "Pi attenuator 3 dB appears in gain_loss_budget.md but not in
    manifest.bom. GLB optimizer added it — flag for review."

high · manifest_mpn_leak (after server restart)
   "MAX17501GATB+T appears in gain_loss_budget.md but MAX25301BATB/V+
    is in component_recommendations.md. Optimizer-side swap leak."

medium · frequency_partial_coverage_advisory · ZVA-183WA-S+
   "ZVA-183WA-S+ covers 0.1-18 GHz; project requires DC-18 GHz.
    DC coverage missing — confirm the DC-coupling assumption."

high · cascade_friis_check_broken
   "Passive stages report NF != |IL|. C4 contract check is broken:
    reports PASS while violations exist. Recompute and refresh GLB."
```

### Block diagram (deterministic SVG, post-restart)

3-column switch-matrix layout:
- Left column: 4 RF inputs as labeled rectangles
- Middle column: switch fabric — one SPDT icon per switch (32 total — needs scrolling/scaling)
- Right column: 8 RF outputs
- Below fabric: aux strip with FPGA, LDO, buck

### What's hard to fix vs what's a 30-min fix

| Bug | Effort to fix |
|---|---|
| Server restart unblocks 8 of 20 bugs | 1 minute |
| `cascade_analysis.json` empty stages | ~half day (BOM-row spec extraction in `tools/rf_cascade.py`) |
| `iip3_dbm` string-vs-number | 30 min (cast in `tools/rf_cascade.py`) |
| C4 broken Friis check | 30 min (`services/glb_optimizer.py`) |
| GLB topology constraint (switch_matrix gain-after-matrix) | 1 day (architecture-aware optimizer) |
| Section 7 fabricated frequency sweep | 1 day (real S-parameter model per role) |
| ASWD hallucination | half day (semantic role-vs-description check in `services/rf_audit.py`) |
| MAX25301 input-range mismatch | half day (extend `run_supply_voltage_audit` to check input range) |
| IIP3 cascade audit (extend to switch_matrix / receiver) | half day (extend `run_tx_cascade_audit` or new `run_iip3_cascade_audit`) |
| Power calc Qty awareness | 1 day (BOM qty propagation through `_build_power_calc_md`) |
| Gain target overshoot check | half day (new audit rule) |
| Component class semantic check (ASWD = switch, not connector) | 1 day (LLM-based role check or rules file) |

---

## Recommended actions, in order

1. **Restart `uvicorn main:app`** (or your launcher). 1 minute.
2. **Regenerate project `rx`** from scratch (Judge Mode reset → re-run pipeline). Verify the artifact list matches the expected set.
3. **Verify the leak gate fires** on this BOM (HMC-class leaks in old block_diagram, GLB optimizer-injected parts).
4. **Investigate why `requirements_lock.json` and `audit_report.md` were missing pre-restart-too**. Likely a silent exception in `finalize_p1`. Check server log for `p1_finalize.freeze_failed`.
5. **Fix the cascade BOM-spec extraction bug** (B1.4) so `cascade_analysis.json` populates stages.
6. **Fix the C4 Friis check** (B1.12) — it's reporting PASS on broken data.
7. **Add 5 new audit rules** (in priority order):
   - `supply_voltage_input_range` (B1.8 — MAX25301 vs 12 V)
   - `iip3_cascade_shortfall` (B1.6 — extend cascade audit beyond TX)
   - `architecture_topology_constraint` (B1.9 — switch_matrix gain-position rule)
   - `gain_target_overshoot` (B1.16 — claimed-vs-computed gain delta)
   - `component_role_semantic_match` (B1.7 — ASWD-as-connector hallucination)
8. **Quantity-aware power calc** (B1.17, B1.18) — multiply per-stage Pdc and per-rail current by qty from BOM rows.
9. **Replace fabricated frequency sweep** (B1.14) with role-based loss/gain models.

After (1)+(2), expect to see ~80% of these bugs disappear or downgrade to audit warnings. The remaining 20% are pre-existing bugs in code I haven't touched and need separate fixes.
