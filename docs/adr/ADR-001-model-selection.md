# ADR-001 — Model Selection for the Silicon to Software (S2S)

- **Status:** Accepted
- **Date:** 2026-04-18
- **Deciders:** Workstream A (Technical Lead), Workstream B (AI/ML Lead)
- **Consulted:** RF Expert, Workstream D (Product/Eval)

## Context

The pipeline runs 7 LLM-backed agents across 10 phases (P1 Requirements, P2 HRS,
P3 Compliance, P4 Netlist, P6 GLR, P8a SRS, P8b SDD, P8c Code Review). Each
agent must:

1. Reason numerically about RF cascades (noise figure, IIP3, link budget).
2. Cite real parts with verified datasheets (no fabricated part numbers).
3. Cite real standards clauses (MIL-STD-461 RE102, not "MIL-STD-461-XYZ").
4. Produce structured output (tool calls with strict schemas).
5. Run in an air-gapped / ITAR-adjacent environment (no cross-border traffic
   for classified requirement sets).

We need to pick (a) a **primary** model, (b) a **fallback** model, and (c) an
**air-gap / local** model, and commit to those decisions for the 5-week
hackathon cycle. We will also define rules for when to swap models.

## Decision

| Tier          | Provider / Model                 | Used for                                           |
|---------------|----------------------------------|----------------------------------------------------|
| Primary       | **GLM-4.7** via Z.AI API         | All 7 agents, interactive P1 chat                  |
| Fallback      | **DeepSeek-V3** via DeepSeek API | When GLM-4.7 API unavailable / rate-limited        |
| Air-gap / demo-offline | **Ollama / Qwen2.5-32B-Instruct (local)** | Judge mode + customer-premise demos    |

All three model endpoints are wired into `agents/base_agent.py` with an
automatic fallback chain already implemented (see summary above). This ADR
formalizes the ordering and the triggers.

### Why GLM-4.7 primary
- Competitive reasoning quality on structured-output / tool-call tasks in our
  internal P1 elicitation benchmark.
- Generous context window for the full 4-round conversation + BOM + standards
  citations in one call.
- Cost per 1M tokens is low enough to burn-and-rerun during development.
- Supports strict JSON schema tool calls (required by our agents).
- Licensing terms compatible with commercial redistribution of outputs.

### Why DeepSeek-V3 fallback
- Distinct vendor / distinct infrastructure from Z.AI — avoids single-vendor
  downtime blocking a live demo.
- Similar tool-call behaviour; our agent wrappers need near-zero adaptation.
- Comparable quality on numerical reasoning per our internal spot checks.

### Why local Ollama for air-gap
- Judge day must not depend on the public internet. Even with caching, an
  outbound DNS or API failure mid-demo is an unacceptable risk.
- Allows a customer-premise / defense integrator to evaluate without their
  data leaving their network (the dominant enterprise concern).
- Qwen2.5-32B-Instruct fits on a single 48 GB GPU at 4-bit quant; an RTX A6000
  or equivalent runs it at ~30 tok/s, adequate for the pipeline's pacing.

## Model-swap policy

We DO NOT change models during the 5-week cycle without:

1. Re-running the full **golden regression set** (`tests/golden/` — 15
   canonical Round-1 scenarios) at temperature=0.
2. Recording the delta on the **5-dimension evaluation rubric**
   (see `docs/eval_rubric.md`).
3. Updating the **baseline comparison** (raw GPT-4 vs scaffolded GPT-4 vs
   scaffolded GLM) in `docs/eval_report.md`.

Any model swap requires ADR-002 (or subsequent) to be written and accepted.

## Decoding parameters

| Phase  | Temperature | top_p | Tool calls enabled | Notes                              |
|--------|-------------|-------|-----|---------------------------------------------|
| P1 chat (elicitation) | 0.2         | 0.9   | yes | Slight stochasticity for natural dialog    |
| P1 generate_requirements | 0.0      | 1.0   | yes | Deterministic — feeds hashed lock           |
| P2 HRS                | 0.1         | 0.9   | yes | Near-deterministic                          |
| P3 Compliance         | 0.0         | 1.0   | yes | Deterministic rules engine with LLM shell   |
| P4 Netlist            | 0.0         | 1.0   | yes | Deterministic                               |
| P6 GLR                | 0.1         | 0.9   | yes | Near-deterministic                          |
| P8a SRS / P8b SDD     | 0.2         | 0.9   | yes | Slight stochasticity for readable prose     |
| P8c Code Review       | 0.0         | 1.0   | yes | Deterministic static-analysis wrapper       |
| Red-team audit        | 0.0         | 1.0   | yes | Must be deterministic for reproducibility   |

## Logging

Every LLM call is recorded in the `llm_calls` table with:
- model name, model version tag, temperature, top_p
- prompt hash (SHA256), tool-call result hash
- timestamp, wall-clock latency, token counts in/out
- `requirements_hash` the call was made against (if applicable)
- `pipeline_run_id` (foreign key to `pipeline_runs`)

This lets us replay any pipeline run exactly, which is required for:
- the golden regression set (detect silent model regressions)
- judge-day reproducibility ("run this project again, show the same output")
- an eventual DO-254-style traceability story, if we take the product that
  direction

## Consequences

**Positive**
- Three-tier fallback = no single-vendor blast radius.
- Deterministic settings for design-critical phases = reproducible outputs.
- Air-gap path already in the repo = defensible answer to "can we run this on
  our classified network?".

**Negative**
- We commit to one vendor pair for 5 weeks; switching requires re-running
  the golden set and updating the baseline comparison — non-trivial work.
- Local Ollama path adds a second prompt-template profile to maintain
  (some tool-call shapes differ between GLM and Qwen2.5).
- DeepSeek endpoints have had intermittent availability issues historically;
  we accept this because they are the fallback, not the primary.

## Follow-ups

- **B1.3:** Wire `llm_calls` table logging in `agents/base_agent.py`.
- **B2.2:** Build a single-command `make golden` target that runs the 15
  canonical scenarios at temp=0 and diffs against recorded outputs.
- **D1.1:** Record the first full baseline run on all three tiers before
  any further agent-prompt changes.
