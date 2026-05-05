# Silicon to Software (S2S) V2 — Architecture Overview (One Page)

*A judge-friendly tour of where each defense lives, what evidence it generates,
and how to reproduce the eval results on a machine that has never touched the
model APIs.*

---

## 1. Ten pipeline phases, three tiers of automation

| Tier              | Phases            | Who runs it  | What we claim |
|-------------------|-------------------|--------------|---------------|
| **AI-automated**  | P1, P2, P3, P4, P6, P8a, P8b, P8c | Our agents | All outputs are citation-bound and validator-checked before the user sees them |
| **Manual / external** | P5 (PCB layout), P7 (FPGA) | Altium / Vivado / KiCad / Quartus | The pipeline tracks completion but never fakes results |
| **Deterministic tools** | cascade validator, lock service, audit, critic, logger | `tools/*.py`, `services/*.py` | Ground-truth math that never calls an LLM |

The line between "AI agent" and "deterministic tool" is the anti-hallucination
seam. Anything quantitative — NF, IIP3, power budget, standards citations — is
produced or verified by a tool, not by the model.

---

## 2. The anti-hallucination fence

```
  user chat (P1)
      │
      ▼
  ┌──────────────────────────────────────────┐
  │  4-round elicitation (ADR-002)           │   16 Tier-1 specs, arch choice,
  │  agents/requirements_agent.py            │   arch-adaptive Tier-2/3, cascade
  └──────────────┬───────────────────────────┘   preview before generation
                 │
                 ▼
  ┌──────────────────────────────────────────┐
  │  cascade validator (Friis)               │   tools/cascade_validator.py
  │  NF, gain, IIP3, P1dB, SFDR, thermal     │   24 unit tests, temp derating
  └──────────────┬───────────────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────────────┐
  │  red-team audit                          │   agents/red_team_audit.py
  │  cascade inflation / fake citation /     │   co-site IMD check included
  │  hallucinated part / co-site IMD         │   AuditIssue severities → fail gate
  └──────────────┬───────────────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────────────┐
  │  requirements lock (ADR-003)             │   services/requirements_lock.py
  │  SHA-256 canonical JSON of confirmed     │   hash mismatch = stale phase
  │  requirements + frozen_at timestamp      │   (services/stale_phases.py)
  └──────────────┬───────────────────────────┘
                 │
                 ▼
        downstream phases P2-P8c
```

Every LLM round-trip is logged by `services/llm_logger.py` with SHA-256 of the
canonical prompt and response text. Raw payloads are never persisted; only the
hashes, token counts, and latency. A `pipeline_run_id` contextvar threads the
run through async awaits without arg plumbing.

---

## 3. How to reproduce the eval results (offline)

One command runs everything:

```
make full-eval        # or: python scripts/run_full_eval.py
```

This composes seven independent checks and prints a judge-readable summary:

1. `pytest` — 150 deterministic tests (cascade, lock, audit, golden,
   migrations, llm-logger, stale-phases, critic, datasheet-verify).
2. `scripts/run_baseline_eval.py` — 30/30 golden scenarios, validator-confirmed
   NF and gain within 0.5 dB, citation resolution 100%.
3. `scripts/run_ablation_matrix.py` — 4 configs × 3 mutations × 30 scenarios.
   Each defense drops to 0% detection only in its own column; the diagonal of
   zeroes is the evidence that each guardrail is pulling its weight.
4. `scripts/reproduce_run.py` — deterministic self-test: same inputs →
   NF = 1.708 dB, gain = 11.0 dB, every time.
5. Fresh-DB migration — `migrations/apply_all` on an empty SQLite file adds
   the lock columns and `pipeline_runs` / `llm_calls` tables idempotently.
6. Component DB sanity — 75 real parts across radar / ew / satcom /
   communication; minimum 15 per domain enforced.
7. Clause DB sanity — 49 MIL-STD / DO / STANAG / FCC / IEC clauses, minimum
   40 enforced.

Exits 0 on full pass. Safe to run on an air-gapped laptop.

---

## 4. Where to look for judge evidence

| Claim                                           | File on disk                                  |
|-------------------------------------------------|-----------------------------------------------|
| 30/30 golden pass                               | `tests/golden/*/*.yaml` (30 YAMLs)           |
| Deterministic test suite                        | `tests/test_*.py` (150 tests)                 |
| Ablation matrix & diagonal zeroes               | `docs/ablation_matrix.md`, `scripts/run_ablation_matrix.py` |
| Component DB with real part numbers             | `domains/*/components.json`                   |
| Defense standards clauses                       | `domains/*/clauses.json`                      |
| Lock semantics (hash + frozen_at)               | `docs/adr/ADR-003-requirements-lock-semantics.md` |
| Four-round elicitation rationale                | `docs/adr/ADR-002-elicitation-order.md`       |
| Model tier / fallback / air-gap policy          | `docs/adr/ADR-001-model-selection.md`         |
| Eval rubric (5 × 0-5, pass ≥ 20/25)             | `docs/eval_rubric.md`                         |
| One-page exec summary (for printing)            | `docs/eval_summary.pdf`                       |
| Air-gap rehearsal playbook                      | `docs/air_gap_rehearsal.md`                   |
| Competitive landscape analysis                  | `docs/competitive_landscape.md`               |

---

## 5. Trust boundary summary

- **Trusted**: cascade validator output, clause DB lookups, component DB
  fields, red-team audit verdicts, requirements-lock hashes, datasheet URL
  verifier results. These are deterministic; identical inputs give identical
  outputs.
- **Advisory**: model chat-of-thought, architecture recommendations,
  free-form commentary. Never persisted without a deterministic cross-check.
- **Ground truth for NF/gain**: cascade validator. Golden YAMLs are written
  with the validator's own numbers ± 0.5 dB tolerance — we never let the
  model declare its own NF and then check it against itself.
