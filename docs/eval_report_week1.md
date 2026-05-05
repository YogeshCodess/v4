# Week-1 Baseline Evaluation — Silicon to Software (S2S) V2

**Date:** 2026-04-18
**Scenarios:** 30 golden reference designs (radar / EW / satcom / communication)
**Driver:** `scripts/run_baseline_eval.py`
**Raw report:** `eval_results/baseline_1776487987.json`

---

## Headline

| Metric                                       | Value    |
| -------------------------------------------- | -------- |
| Scenarios exercised                          | 30 / 30  |
| Scenarios passing every deterministic check  | 30 / 30  |
| Cascade-math failures (validator vs claim)   | 0        |
| Unresolved standard-clause citations         | 0        |
| Red-team audit failures                      | 0        |

Every golden scenario lands within its declared noise-figure and gain
tolerance (0.5 dB or 1.0 dB depending on the scenario). Every cited
standard-clause pair resolves in `domains/standards.json`. The red-team
audit flags zero cascade or citation issues against any scenario's
claimed performance.

---

## Coverage by Domain

| Domain        | Scenarios | Passing | Range                               |
| ------------- | :-------: | :-----: | ----------------------------------- |
| radar         | 8         | 8       | L, S, C, X, Ku, Ka, W               |
| EW            | 7         | 7       | HF COMINT through mm-wave RWR       |
| satcom        | 7         | 7       | L, S, C, X, Ku, Ka, GNSS L1/L5      |
| communication | 8         | 8       | HF ALE, VHF, UHF, ISM, BLE, Zigbee  |

Band, platform, architecture, and modulation mix were all varied by hand
so no two scenarios share the same column set. That's why the cascade
numbers cover a wide range (NF 0.7-7.1 dB, total gain 23-62 dB) and yet
every one lands in its stated tolerance.

---

## Per-Check Breakdown

Each scenario runs four deterministic checks:

1. **NF within tolerance** — `validate_cascade_from_dicts()` recomputes
   noise figure from the BOM and compares against `expected_cascade.noise_figure_db`.
   30/30 pass.
2. **Gain within tolerance** — same, for total gain.
   30/30 pass.
3. **All cited clauses resolve** — every `(standard, clause)` pair in
   `expected_citations` is looked up in the 49-clause standards DB.
   30/30 pass, zero misses across the corpus.
4. **Red-team audit** — `tools/red_team_audit.py` is called with the
   BOM and the scenario's claimed cascade. It re-derives cascade
   values, checks citations, sweeps for suspect part numbers, and
   reports `overall_pass`. 30/30 scenarios come back clean.

No scenario depends on the LLM. The full sweep runs in < 2 seconds on
the laptop we demo on.

---

## What This Does Not Prove

- **LLM quality.** Every answer here is deterministic (validator + DB
  lookup). The agent's elicitation, part selection, and prose quality
  are evaluated separately via the judge-mode flow in `scripts/run_full_eval.py`.
- **Datasheet freshness.** `scripts/verify_datasheets.py` is the owner
  of that check and runs separately.
- **PCB / firmware correctness.** The pipeline stops at the requirements,
  HRS, compliance, netlist, GLR, and SRS/SDD/Code-Review stages. P5 (PCB
  layout) and P7 (FPGA design) are manual / external.

---

## How to Re-Run

```bash
python scripts/run_baseline_eval.py
```

The script writes a fresh JSON to `eval_results/baseline_<timestamp>.json`
and prints one `[PASS]` / `[FAIL]` line per scenario. Non-zero exit code
means at least one scenario regressed.

For the full eval loop (pytest + baseline + ablation matrix +
reproducibility self-test), run:

```bash
python scripts/run_full_eval.py
```
