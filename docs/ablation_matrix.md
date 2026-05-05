# Ablation Matrix — Silicon to Software (S2S) V2

**Driver:** `scripts/run_ablation_matrix.py`
**Latest report:** `eval_results/ablation_1776488033.json`
**Date:** 2026-04-18

## What It Measures

The ablation matrix isolates which of the three deterministic defences —
cascade validator, citation resolver, red-team audit — catches each class
of hallucination. We mutate every one of the 30 golden scenarios in three
ways and then run it through four configurations.

### Mutations

| Mutation              | What it does                                                             |
| --------------------- | ------------------------------------------------------------------------ |
| `clean`               | No mutation. Every configuration must still pass.                        |
| `bad_citation`        | Injects a fake `("FAKE-STD", "Z99")` into `expected_citations`.          |
| `hallucinated_part`   | Adds a BOM stage with part number `FAKE-NONEXISTENT-9000`.               |
| `cascade_inflation`   | Lowers the claimed noise figure by 5 dB — well outside tolerance.        |

### Configurations

| Config         | Cascade | Citations | Red-team |
| -------------- | :-----: | :-------: | :------: |
| `full`         |   on    |   on      |   on     |
| `no_validator` |   off   |   on      |   on     |
| `no_citation`  |   on    |   off     |   on     |
| `no_redteam`   |   on    |   on      |   off    |

When a sub-check is ablated, its result is also suppressed as *input*
to the red-team audit — so the audit cannot compensate for a check
that has been explicitly turned off.

## Results (30 scenarios × 4 configs × 4 mutations = 480 evaluations)

`clean` row is a pass-rate (higher is better). Every other row is a
*detection* rate — the fraction of scenarios where the mutation was
caught (`passed=False`).

| Mutation             | full   | no_validator | no_citation | no_redteam |
| -------------------- | :----: | :----------: | :---------: | :--------: |
| `clean`              | 100.0% | 100.0%       | 100.0%      | 100.0%     |
| `bad_citation`       | 100.0% | 100.0%       | **0.0%**    | 100.0%     |
| `hallucinated_part`  | 100.0% | 100.0%       | 100.0%      | **0.0%**   |
| `cascade_inflation`  | 100.0% | **0.0%**     | 100.0%      | 100.0%     |

The 0.0 % cells are the expected ones — the ablated check is the only
one that catches that mutation class. Every other cell stays at 100 %
because the other defences still fire. This is the structural
confirmation that the three defences are *independent*: losing any one
of them creates a class of hallucination the system can no longer
detect, and no defence is masking another.

## Implication for the Demo

Judges can ask "what if the LLM gets something wrong?" and the answer is
concrete:

- If it fabricates a standard clause → citation check fails, P1 cannot
  finalise.
- If it invents a part number → red-team audit fails, P1 cannot finalise.
- If it claims a cascade figure that the BOM cannot support → validator
  fails, P1 cannot finalise.

There is no "full off" column — by design, the pipeline refuses to
finalise if any of the three defences is skipped.

## Re-Run

```bash
python scripts/run_ablation_matrix.py
```

Exits 0 on clean run. JSON report dropped under `eval_results/`.
