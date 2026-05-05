# Evaluation Rubric — Silicon to Software (S2S) V2

**Owner:** Workstream D (Product / Eval)
**Ticket:** D1.2
**Status:** v0.1 — to be validated with RF Expert in Week 1.

This rubric is how we score every pipeline run against a ground-truth
"reference design". It is used for:

1. The **golden regression set** (15 canonical scenarios, tracked per commit).
2. The **baseline vs scaffolded** comparison (raw GPT-4 vs our system).
3. The **3 ablation studies** (no-elicitation, no-validator, no-citation).
4. The **live judge-day demo** — we show the judges this scorecard.

A run is scored on five dimensions. Each dimension yields a 0-5 integer.
Total score is the sum (0-25). Pass threshold for a published run: **≥ 20**.
The rubric is intentionally STRICT on correctness and hallucination —
good prose with wrong numbers fails.

---

## D1 — Requirement Coverage (0-5)

Does the system capture every requirement a domain engineer would capture?

| Score | Criterion                                                                                                 |
|-------|-----------------------------------------------------------------------------------------------------------|
| 5     | All 16 Tier-1 Round-1 questions answered + all triggered Tier-2 questions + architecture confirmed.        |
| 4     | 1-2 non-critical Tier-2 questions missed; architecture confirmed.                                         |
| 3     | ≥ 3 Tier-2 questions missed OR architecture unconfirmed.                                                  |
| 2     | ≥ 2 Tier-1 questions missed.                                                                              |
| 1     | Critical Tier-1 question missed (frequency, architecture, sensitivity, or dynamic range).                 |
| 0     | No structured elicitation; system jumped straight to components.                                          |

---

## D2 — Component Realism (0-5)

Are the selected parts real, current-production, datasheet-verifiable, and
frequency-appropriate? The RF Expert adjudicates edge cases.

| Score | Criterion                                                                                                         |
|-------|-------------------------------------------------------------------------------------------------------------------|
| 5     | All parts real + in production + datasheet URL resolves + every cited spec matches the datasheet within 10%.      |
| 4     | All parts real + production; ≤ 1 spec cited slightly off (<15%) vs datasheet.                                     |
| 3     | 1 part end-of-life / not recommended for new design, or 1 spec-off-by-> 15%.                                      |
| 2     | ≥ 1 part is out of its rated frequency range for the application, OR ≥ 2 specs-off.                               |
| 1     | ≥ 1 part is clearly fabricated (no such PN exists).                                                               |
| 0     | Multiple fabricated parts OR completely wrong part category for the architecture.                                 |

---

## D3 — Cascade Correctness (0-5)

Do the numerical claims match the cascade validator output?

Every claim the agents make about system NF, IIP3, P1dB, gain, sensitivity,
or SFDR is re-computed by `tools/cascade_validator.py`. The rubric compares
claimed vs computed.

| Score | Criterion                                                                                                               |
|-------|-------------------------------------------------------------------------------------------------------------------------|
| 5     | Every claimed cascade number is within 0.5 dB of the computed value; no contradictions with targets.                    |
| 4     | All within 1.0 dB.                                                                                                      |
| 3     | All within 2.0 dB, or one number missing but rest correct.                                                              |
| 2     | One number off by > 2 dB, or two numbers off by > 1 dB.                                                                 |
| 1     | A headline figure (sensitivity or NF) is off by > 3 dB.                                                                 |
| 0     | The agent claims the design meets targets when the validator says it fails, OR claims impossible values (e.g. NF < 0).  |

---

## D4 — Standard / Clause Validity (0-5)

Are cited standards and clauses real and applicable?

All citations `(standard, clause)` are run through
`domains.standards.validate_citations()`. Hallucinated clauses fail here.

| Score | Criterion                                                                                   |
|-------|---------------------------------------------------------------------------------------------|
| 5     | 100% of cited clauses resolve to the clause DB; applicability matches the platform/domain.  |
| 4     | 100% resolve; one clause is applicable to an adjacent domain (minor mismatch).              |
| 3     | ≥ 95% resolve; at most one clearly wrong applicability.                                     |
| 2     | 80-95% resolve; or wrong severity (e.g. "informational" cited as "mandatory").              |
| 1     | < 80% resolve; at least one clearly fabricated clause number.                               |
| 0     | Multiple fabricated clauses; wrong standard invoked (e.g. DO-160 for a ground radar).       |

---

## D5 — Reproducibility & Provenance (0-5)

Can the run be re-executed deterministically from the frozen inputs?

| Score | Criterion                                                                                                            |
|-------|----------------------------------------------------------------------------------------------------------------------|
| 5     | Frozen requirements hash present; re-run at temp=0 matches byte-for-byte; every claim tagged with a citation source. |
| 4     | Hash + temp=0 match; ≤ 2 claims missing a citation tag.                                                              |
| 3     | Hash present but temp=0 re-run diverges on prose (same structure, different wording); citations mostly present.      |
| 2     | No hash OR no citation layer; re-run diverges substantially.                                                         |
| 1     | Requirements not frozen; each re-run gives materially different BOM/numbers.                                         |
| 0     | Run cannot be reproduced at all — missing inputs, missing model version, missing seeds.                              |

Citation-source vocabulary:
`user_input | datasheet_component | cascade_calc | mil_std_clause | assumption | domain_expert_review`

---

## Scorecard template (to paste into eval_report.md)

```
Project:            {project_id}
Domain:             {radar | ew | satcom | communication}
Architecture:       {architecture}
Model:              {model} {model_version}
Requirements hash:  {sha256[:12]}
Scored by:          {reviewer_name}
Date:               {YYYY-MM-DD}

D1 Requirement Coverage:      {score}/5   (notes: ...)
D2 Component Realism:         {score}/5   (notes: ...)
D3 Cascade Correctness:       {score}/5   (notes: ...)
D4 Standard / Clause Validity:{score}/5   (notes: ...)
D5 Reproducibility:           {score}/5   (notes: ...)
                               -----
Total:                        {sum}/25

PASS (≥ 20) / FAIL
```

---

## Scoring workflow

1. Workstream D prepares the 15 golden scenarios in `tests/golden/<domain>/<id>.yaml`
   — each contains: Round-1 user answers, confirmed architecture, expected
   headline cascade numbers (from RF Expert's hand calculation), and expected
   citations.
2. `make golden` runs all scenarios through the pipeline at temp=0, captures
   the output artifacts under `tests/golden/runs/<commit>/<id>/`, and invokes
   the rubric scorer.
3. Rubric scoring is split:
   - **D3, D4, D5** are machine-scorable (validator, clause resolver, hash diff).
   - **D1, D2** require a human RF reviewer. We target weekly review of any
     delta from the previous week's scorecard.
4. The last 4 weekly scorecards are published as a small line chart on the
   product landing page — transparency becomes the differentiator.

## Baseline (raw LLM, no scaffolding)

The baseline run is a single-shot GPT-4-class call with the system prompt
"You are an RF design engineer. The user will describe a receiver. Generate
requirements, components, and a block diagram." and no tools. Baseline
expectation per D1-D5 (based on prior spot checks):

| Dim | Baseline expected | Scaffolded target |
|-----|-------------------|-------------------|
| D1  | 2                 | 5                 |
| D2  | 1-2               | 4                 |
| D3  | 1                 | 4-5               |
| D4  | 1                 | 4-5               |
| D5  | 0                 | 5                 |
| Total | ~7/25           | ≥ 20/25           |

If we don't clear ≥ 10-point gap baseline → scaffolded in the live eval, the
thesis of the pipeline is not demonstrated and we ship the gap as a feature
request.
