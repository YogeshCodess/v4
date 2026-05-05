# Silicon to Software (S2S) V2 — 5-Week Implementation Plan

Version 1.0 — companion to `CLAUDE.md`. Scope: next-round hackathon preparation for a live panel (CEO/COO/CTO/AI experts/hardware + software senior managers), written submission not mandatory, hands-on technical deep-dive by judges. Target domains: radar, electronic warfare (EW), satcom, and tactical communications. Architecture designed for future expansion into avionics, space, naval, and weapons-platform electronics.

---

## Team and Ownership

Four workstreams, one owner each. Project Lead (PL) is the tie-breaker on cross-stream decisions.

**WS-A Technical Foundation** — Technical Lead. Core pipeline, reliability, observability, deployment, judge-mode, integration ownership.

**WS-B AI Agents & Evaluation** — AI/ML Lead. Agents, cascade validator, red-team agent, baseline comparisons, ablations.

**WS-C Defense Domain** — Hardware Lead (RF expert). Component DB, standards coverage, reference design correctness, manual QA, domain knowledge transfer.

**WS-D Product, Eval & Panel** — Product/Eval Lead. Eval dataset, scoring, demo narrative, business case, mock panels, external reviewer relationships.

Workstream owners are single-approver on PRs within their stream. Cross-stream PRs need one reviewer per affected stream.

---

## Workstream Charters (End-of-Week-5 Exit Criteria)

### WS-A
Pipeline replays any historical run byte-identically. Every LLM call logged with cost/latency/outcome. Judge-mode boots to wiped state in under 3 seconds with three sample specs plus free-form input. 50 consecutive full-pipeline runs complete without crashes. Chaos tests cover 10 failure paths with graceful degradation. Venue deployment scripted, not manual.

### WS-B
Cascade validator mandatory tool-call before `generate_requirements`, 90%+ unit test coverage. Red-team audit agent catches 95%+ of planted hallucinations. Baseline comparison across 28 designs shows quantified hallucination-rate delta (raw GPT-4 vs scaffolded). Three ablations (elicitation, validator, citation) each show measurable contribution. 25 anticipated AI/ML questions rehearsed with numbers.

### WS-C
Component database contains 300+ defense-qualified parts across radar/EW/satcom/comms with verified datasheet URLs and manufacturer-grade metadata. Standards lookup covers 60-80 MIL-STD/DO/STANAG clauses with short descriptions. All 28 reference designs manually audited. One external independent expert provides a quotable paragraph. 25 anticipated hardware questions rehearsed.

### WS-D
28 reference designs with ground truth across four domains. Demo narrative in 90-second, 3-minute, 10-minute variants. Business case documented (TAM, unit economics, competitive landscape, moat). 20 anticipated CEO/COO questions rehearsed. Two full external mock panels run. 3-5 domain expert quotes curated.

---

## Reference Design Set (28 designs, 7 per domain)

**Radar (7):** X-band fire-control airborne receiver, Ku-band surveillance front-end, L-band maritime patrol receiver, S-band air-traffic IF, airborne monopulse tracking (X-band), naval fire-control receiver, airborne radar warning receiver (2-18 GHz wideband).

**EW (7):** HF search receiver (1.5-30 MHz), 3-channel V/UHF monitoring receiver (20-1000 MHz), wideband SIGINT receiver (2-3000 MHz), airborne ESM, digital direction finder, communication EW receiver, wideband ELINT.

**Satcom (7):** L-band downconverter (GPS/Iridium), C-band ground terminal, Ku-band satellite downlink, Ka-band user terminal, X-band milsatcom, S-band TT&C, GPS L1/L2/L5 anti-jam.

**Communication (7):** VHF/UHF tactical military radio front-end, HF SDR transceiver, tactical data-link receiver, combat net radio, satellite phone front-end, V/UHF multi-channel base station, software-defined COMINT receiver.

These are canonical defense RF receiver patterns (Skolnik / Tsui / Pozar / Maral). Company-agnostic.

---

## Week 1 — Foundation (parallel independent work)

### WS-A
- **A1.1 Requirements lock with SHA256 hash** — 1d — Generate SHA256 over confirmed Round-4 requirements JSON; store `projects.requirements_hash` and `projects.requirements_frozen_at`. Refuse downstream phases if hash differs. `GET /api/v1/projects/{id}/requirements/status` returns `{hash, frozen_at, is_stale}`.
- **A1.2 Reproducibility harness** — 2d — `pipeline_runs` table with `run_id, project_id, phase_id, model_version, prompt_version, input_json, output_json, started_at, completed_at, cost_usd, token_count`. `python replay.py <run_id>` re-executes and asserts byte-identical output at temperature 0.
- **A1.3 Observability logging** — 1d — Wrap every LLM call with `log_llm_call()` writing to `llm_calls` table: `call_id, run_id, phase_id, model, temperature, input_tokens, output_tokens, cost_usd, latency_ms, tool_calls_json, status`. Admin endpoint `GET /api/v1/admin/llm-calls?since=...`.
- **A1.4 CI and PR discipline** — 1d — GitHub Actions: pytest on PR, black/ruff pre-commit, branch protection requiring approval + passing CI, PR template with ticket/test plan/risks.

### WS-B
- **B1.1 Cascade validator tool** — 3d — `tools/cascade_validator.py` exposing `validate_cascade(bom, system_spec) -> CascadeReport`. Friis NF cascade, gain cascade, IIP3 input-referred addition, P1dB cascade, temperature derating. 10+ unit tests covering edge cases. Document math in `docs/cascade_math.md`.
- **B1.2 Golden regression tests** — 1d — Snapshot one reference project's phase outputs as `tests/golden/<project>/<phase>.json`. `pytest tests/test_golden.py` diffs at temperature 0.
- **B1.3 Model version pinning ADR** — 1d — `docs/adr/ADR-001-model-selection.md` covering which model for which phase, why, cost, latency, alternatives considered. Pin versions in `config/models.yaml`.

### WS-C
- **C1.1 Component DB v1 (75 parts)** — 4d — `domains/<domain>/components.json`. Schema: `part_number, manufacturer, category, freq_min_hz, freq_max_hz, noise_figure_db, gain_db, iip3_dbm, p1db_dbm, screening_class, temp_min_c, temp_max_c, rad_tolerance, itar_controlled, package, datasheet_url, notes`. 20 radar, 20 EW, 20 satcom, 15 comms. Every datasheet URL verified manually.
- **C1.2 Domain-tier Round-1 question spec** — 1d — 4 per-domain question sets in markdown. Radar: PRF, PW, coherent, range res, MTI. EW: signal type, co-site, TEMPEST, pulse handling. Satcom: G/T, link budget, polarization, tracking. Comms: modulation, channels, reference accuracy.
- **C1.3 Consultant outreach** — 0.5d (Day 1) — Send 10-12 cold emails to senior defense/aerospace RF engineers (primes, integrators, retired senior IEEE MTT-S members). Pitch: 8h paid independent review over weeks 4-5 at $100-130/hr, quotable paragraph required.
- **C1.4 First knowledge share** — 1h Wednesday — Defense RF receiver architectures: superhet vs direct-conversion vs direct-RF-sampling, radar vs EW context.

### WS-D
- **D1.1 Recruit free domain reviewers** — 1d (Day 1) — Cold outreach to 8-10 professors, senior engineers, LinkedIn connections. Separate from paid consultants. Goal: quotable paragraphs.
- **D1.2 Eval scoring rubric** — 2d — 5 dimensions: cascade math correctness, spec field coverage, hallucination count, citation validity, production-readiness (1-5 expert scale). Automated where possible. `docs/eval_rubric.md`.
- **D1.3 Demo narrative drafts** — 2d — 90s/3min/10min variants. Industry-wide framing (radar/EW/satcom/comms as dominant defense RF receiver sub-domains worldwide), quantified productivity delta, extensibility commitment.
- **D1.4 Competitive landscape research** — 0.5d — Survey HRS/compliance cycle times, engineer rates, EDA AI vendors (Cadence Allegro X AI, Siemens NX AI, Altium, Flux, JITX). One-page competitive note.
- **D1.5 Calendar-block RF expert** — 0.5d — Published weekly calendar: 80% execution wk1-2, 50/50 execution/review wk3-4, 30/70 execution/rehearsal wk5. Enforced in Monday planning.

### Week 1 Integration Contracts (merged to main by Friday EOD)
- `tools/cascade_validator.py` signature merged
- `domains/<domain>/components.json` schema frozen
- `pipeline_runs` and `llm_calls` tables migrated
- Round-1 defense question spec merged as markdown

### Week 1 Exit Gate
Replay works on one phase. Cascade validator has 10+ tests passing. 75 components curated, datasheets verified. Eval rubric approved. First knowledge share delivered. 3+ consultant replies received.

---

## Week 2 — Depth (cross-stream dependencies activate)

### WS-A
- **A2.1 Prompt injection hardening** — 1d — Strip instruction-like content from chat messages, refuse jailbreak patterns, rate-limit 30 msg/project/hr, log `security_events`. Test with 20 known attacks.
- **A2.2 Datasheet verifier background job** — 1d — Async HEAD request per part URL, cache results in `part_verifications` table. Verified/unverified badge in DocumentsView BOM.
- **A2.3 Judge mode scaffolding** — 3d — `/judge` route wipes context, presents 3 pre-written specs (radar/EW/satcom) plus free-form. Admin drawer shows current prompt, tool call, token count, cost, latency.

### WS-B
- **B2.1 Red-team audit agent** — 3d — New phase post each AI phase. Validates cascade claims via `cascade_validator`, checks part numbers exist in DB, verifies datasheet URLs resolve, confirms cited clauses exist in standards lookup, flags confidence > 0.9 without evidence. Outputs severity-tagged `AuditReport`. Renders as "Trust Report" tab.
- **B2.2 Mandatory cascade tool call** — 1d — Requirements agent must call `validate_cascade` before `generate_requirements`. Refuse to proceed otherwise. Return validation errors to agent for self-correction.
- **B2.3 Confidence scoring per section** — 1d — Agents emit `{content, confidence, confidence_rationale}`. Low-confidence sections render with amber tint and hover tooltip.

### WS-C
- **C2.1 Component DB to 300 parts** — 3d — Grow with defense metadata focus: screening_class (commercial/industrial/military/space), temp grade (-55/+125 where MIL), ITAR, rad tolerance. Include VPX/XMC form-factor modules for future.
- **C2.2 MIL-STD/DO/STANAG clause lookup** — 1d — `domains/standards.json` with ~70 clauses: MIL-STD-461G (CE/CS/RE/RS), MIL-STD-810H (500-516), MIL-STD-704F, DO-254 (DAL A-D), DO-160G, MIL-STD-188-110/165/181, STANAG 4193/4285/4586. Clause + short description + applicability.
- **C2.3 First consultant call** — 2h midweek — Review of component DB selections and reference design priorities. Apply corrections same day.
- **C2.4 Knowledge share #2** — 1h Wed — Cascade math and Friis derivation. Team-level fluency.

### WS-D
- **D2.1 First 12 reference designs with ground truth** — 4d — 3 per domain. Each has Round-1 answers, expected BOM, expected cascade summary, expected HRS section coverage, required standards. Hardware Lead reviews for correctness before freezing.
- **D2.2 Send first 3 designs to free reviewers** — 0.5d — Expect 5-7 day turnaround; that's why week 2 not week 3.
- **D2.3 Business case v1** — 1d — TAM (global defense RF engineer count × loaded cost × HRS cycle time). Unit economics (pipeline run cost vs engineer hours saved). Competitive landscape. Moat (eval dataset, elicitation IP, component DB, workflow). Two pages in Notion.

### Week 2 Exit Gate
Every output traceable to source in one click. Red-team catches 3/3 planted hallucinations. 300 components in DB. 70-clause standards lookup merged. 12 reference designs exist, 3 have external feedback pending. First consultant call complete.

---

## Week 3 — Empirical Proof (most important week for AI judges)

### WS-A
- **A3.1 Judge mode complete** — 3d — Full 8-phase live run from sample spec in <30 min. Admin drawer real-time. Fail gracefully: error surface, retry, never crash. Test all 3 pre-written specs.
- **A3.2 Side-by-side comparison view scaffolding** — 2d — Two-output diff viewer on BOM, spec fields, standards. WS-D supplies content.

### WS-B
- **B3.1 Baseline comparison** — 2d — Raw GPT-4 with minimal prompt on all 28 reference inputs. Full scaffolded system on same 28. Score both via rubric. Headline metrics: hallucination rate, cascade error rate, spec coverage %, citation validity %. These are your pitch numbers.
- **B3.2 Three ablations** — 2d — Config A: 4-round elicitation off. Config B: cascade validator off. Config C: citation layer off. Each on 15 of 28 designs. Plot hallucination rate across configs.
- **B3.3 Results compilation** — 1d — Charts (5-config hallucination rate), table (per-domain breakdown), one-page summary. `docs/eval_results_v1.md`.

### WS-C
- **C3.1 Second domain polish (satcom)** — 2d — Satcom-specific Round-1 (G/T, link budget, polarization), 50 additional satcom components (Ka LNAs, block converters, synthesizers), extended standards (MIL-STD-188-165, DVB-S2 ref).
- **C3.2 Second consultant call** — 2h — Satcom + comms review. Apply corrections.
- **C3.3 Manual QA on first 10 outputs** — 2d — Line-by-line: real parts? datasheets match? cascade right? standards correct? Log issues to AI/ML Lead.
- **C3.4 Free reviewer feedback collection** — 0.5d — First batch due.
- **C3.5 Knowledge share #3** — 1h Wed — MIL-STD-461 CE/CS/RE/RS structure, MIL-STD-810 method numbering.

### WS-D
- **D3.1 Scale eval to 28** — 3d — 4 more radar, 4 more EW, 4 more satcom, 4 more comms.
- **D3.2 Side-by-side content** — 2d — For 5 designs, craft reference "expert output" (adapted public reference or Hardware Lead assisted). Used in judge-mode demo.
- **D3.3 Execute scoring rubric on all 28** — 1d — Both scaffolded and baseline. Feeds B3.3.

### Week 3 Exit Gate
Concrete hallucination rate numbers: full-system, baseline, 3 ablations, across 28 designs. Two domains production-quality. Judge mode reliable. 5+ paired comparisons.

---

## Week 4 — Polish and Credibility Locks (no new features after Wednesday)

### WS-A
- **A4.1 Chaos test suite** — 2d — 10 scripted failures: malformed LLM response, network timeout, 404 datasheet, blank input, prompt injection, concurrent runs, DB lock, out-of-tokens, invalid phase transition, corrupted project state. Each has graceful recovery + automated test.
- **A4.2 Reliability soak test** — 2d — 50 full-pipeline runs with randomized specs. Fix every crash/timeout/silent failure. Target 50/50 clean by Thu EOD.
- **A4.3 Backup demo video** — 1d — 8-10 min narrated full-pipeline screen recording on flagship radar spec. Host locally on demo laptop.

### WS-B
- **B4.1 Anticipated Qs — AI/ML (25)** — 2d — "What's your model? Why?" "Hallucination rate?" "Show ablation." "Why not fine-tuning?" "Prompt injection defense?" "Cost per run?" "Evaluation methodology?" "Confidence calibration?" "Red-team agent trust?" "Tool call refusal?" One-paragraph answers with numbers. Notion `/panel-prep/ai-ml-questions`.
- **B4.2 Cost/performance panel** — 1d — Live display: tokens, USD, wall-clock, cache hit rate. Production-readiness signal.
- **B4.3 Red-team polish** — 1d — Tune thresholds, add missed failure modes from manual QA.
- **B4.4 Roleplay session with Hardware Lead** — 1d — HW Lead attacks for 90 min; AI/ML Lead defends. Recorded.

### WS-C
- **C4.1 Full audit remaining 18 designs** — 3d — Same rigor as C3.3 across remaining 18. Complete before Friday.
- **C4.2 Third consultant session (3h, Thu)** — Independent validation: consultant reviews 4 fresh outputs (1 per domain), drafts quotable paragraph. Script the ask explicitly.
- **C4.3 Anticipated Qs — Hardware (25)** — 1d — "Where did this LNA come from?" "Why this architecture?" "RE102 strategy?" "Temp derating?" "Show cascade math." "Image rejection?" "Pulse handling?" "Rad-hard story?"
- **C4.4 Knowledge share #4** — 1h Wed — STANAG 4193 (IFF), DO-254 DAL structure.

### WS-D
- **D4.1 Anticipated Qs — CEO/COO (20)** — 2d — "Market size?" "Unit economics?" "Moat?" "Cadence threat?" "ITAR?" "Deployment?" "3-year plan?" "Adoption risks?"
- **D4.2 One-page eval results PDF** — 1d — Typeset (LaTeX or Word): architecture diagram, eval methodology, headline numbers + charts. Handout for judges.
- **D4.3 Expert quotes formatted for live use** — 1d — 3-5 paragraphs with name, role, credentials. Presentation slide format.
- **D4.4 First external mock panel** — 1d Fri — 2-3 friends brief on archetypes, 45-min panel sim. Recorded. Identify weakest answers for week-5 rehearsal.

### Week 4 Exit Gate
70+ questions rehearsed. Consultant quote locked. All 28 outputs audited. Soak test clean. Backup video exists. Mock panel held.

---

## Week 5 — Rehearsal and Logistics (no new features at all)

**Every day:** 15-min live standup + 30-min end-of-day system demo.

- **Mon** — All-hands mock panel #2 (second cohort, 60 min) + retro + buffer for last-week bugs.
- **Tue** — Individual rehearsal blocks per archetype. AI Lead defends AI Qs (P/E Lead attacks). HW Lead defends HW Qs (AI Lead attacks). P/E Lead defends biz Qs (Tech Lead attacks). Cross-coverage drilled.
- **Wed** — Third consultant session as hostile mock judge (2h, paid). Uses same 3 sample specs the judges will see. Record, review, fix.
- **Thu** — Venue prep complete. Laptops on venue network if possible. API keys with 2x-expected cost caps. Fallbacks verified. Travel locked.
- **Fri** — Full dress rehearsal with timing: 10-min demo + 30-min Q&A from friends. Final gate — fix or cut by EOD.

---

## Coordination Cadence

Daily 15-min live standup at fixed team time. Yesterday/today/blockers. Blockers trigger immediate 10-min breakout.

Monday 45-min planning call. Workstream owners walk the week's tickets + dependencies + exit-gate contribution. Risk register 5-min review at end.

Wednesday 60-min knowledge share. Hardware Lead teaches one topic (schedule above). Recorded. Missed sessions async.

Friday 45-min demo + retro. 5-min screen-record per person. 15-min retro: worked / didn't / one change.

**Discord only** for async chat (no Slack / no DMs for decisions). **Loom** for async updates longer than a paragraph.

---

## Interface Contracts (stubs merged week 1, implementations fill in later)

```python
# tools/cascade_validator.py
def validate_cascade(bom: List[Part], system_spec: SystemSpec) -> CascadeReport: ...
# CascadeReport: noise_figure_db, total_gain_db, iip3_dbm, p1db_dbm, warnings, errors

# domains/<domain>/__init__.py
def get_questions() -> List[Question]: ...
def get_components(**filters) -> List[Part]: ...
def get_standards(**filters) -> List[StandardClause]: ...

# agents/red_team_audit.py
def audit(phase_output: dict, requirements: dict) -> AuditReport: ...
# AuditReport: severity, issues: List[Issue], overall_pass: bool

# POST /api/v1/projects/{id}/requirements/confirm -> {hash: str, frozen_at: datetime}
# POST /api/v1/admin/judge-mode/reset -> {url: str}
```

**Citation source taxonomy (enum):** `user_input | datasheet_component | cascade_calc | mil_std_clause | assumption | domain_expert_review`

---

## Budget ($1500 total)

- **$800** — paid external validator (6-8h × $100-130/hr). Split: 2h wk2 (DB review), 2h wk3 (domain spread), 3h wk4 (independent validation + quote), 2h wk5 (hostile mock judge).
- **$350** — API costs: $150 development wk1-3, $200 eval runs wk3-4. Cost cap on key at $500.
- **$200** — venue/travel misc: adapters, cables, snacks, printed backups.
- **$150** — domain expert honorariums: small token gifts to 3-5 free reviewers.

---

## Risk Register

| ID | Risk | Owner | Mitigation | Trigger |
|----|------|-------|------------|---------|
| R1 | RF expert bottleneck | PL | Calendar-block, async docs, weekly planning enforce | Blocked >48h on their review |
| R2 | API cost / rate-limit overrun | TL | $500 cap, per-phase token budget, cached fallback | Daily spend >$50 before wk4 |
| R3 | Component DB accuracy gaps | HL | External review wk2+4, verified badge, red-team agent | Reviewer flags >2 wrong parts / 50 |
| R4 | Demo reliability under live conditions | TL | Chaos suite, 50-run soak, offline fallback, backup video, spare laptop | Any crash in mock panel |
| R5 | Domain scope creep | PL | Locked scope (4 domains), PR approval on expansion in `#decisions` | New domain/feature proposed wk3-5 |
| R6 | Team member illness/unavailability | PL | Cross-training via knowledge shares, workstream #2 documented, repo-committed artifacts | — |
| R7 | Venue network reliability | TL | Mobile hotspot backup, offline sample project | — |
| R8 | Consultant unresponsive/cancels | HL | Recruit 2 not 1 wk1, lean on free reviewers, budget flex | No reply after 7 days |
| R9 | Mock panel reveals narrative weakness | PE | Mock panel wk4 not wk5, buffer day wk5 for rework | — |
| R10 | Integration bugs accumulate | TL | Daily main-branch smoke test, small frequent PRs, CI gating | — |

---

## Six Numbers to Memorize (All Team Members by EOW4)

1. Hallucination rate — full scaffolded system on 28-design eval (target <5%)
2. Hallucination rate — raw GPT-4 baseline (expected >25%)
3. Cost per full pipeline run in USD
4. Time per full pipeline run in minutes vs estimated engineer-hours
5. Coverage — 4 domains / 60+ standards clauses / 300+ components / 28 reference designs
6. Ablation delta — percentage-point drop when cascade validator removed

---

## Day 1 Actions

- Kickoff call (90 min) — walk this plan, assign workstream owners, agree on daily standup time, set up Discord channels, create GitHub Projects board with all week-1 tickets.
- **PL** — calendar-block Hardware Lead wk1-2 execution, set up Notion (Architecture/Decisions/Demo), create all tickets.
- **Hardware Lead** — send 10 consultant outreach emails by EOD. Begin component DB.
- **Technical Lead** — GitHub Actions CI setup, branch protection rules.
- **AI/ML Lead** — scaffold `cascade_validator.py` module + test file.
- **Product/Eval Lead** — begin eval rubric, start competitive landscape research.

---

## Domain Module Structure (modular = extensible)

```
backend/domains/
├── radar/
│   ├── __init__.py
│   ├── questions.py       # Round-1 application-adaptive questions
│   ├── components.json    # Curated parts for this domain
│   ├── standards.py       # Standards lookup subset
│   ├── reference_designs/ # Ground-truth specs + expected outputs
│   └── prompts.py         # Domain-specific prompt additions
├── ew/
├── satcom/
└── communication/
```

Adding a new domain (avionics, space, naval, weapons) = drop in a new folder. This is a pitch talking point: "adding a new defense sub-domain is one week of work, not a rewrite."

---

## Appendix — Pass-5 Polish (Demo-Day Readiness)

After all 24 original tickets closed in passes 1-4, the fifth autonomous pass
targeted the three remaining demo-day seams. Each is small, deterministic,
and already validated in the `/tmp/iso` harness:

- **E1 (Judge-mode wipe-state, extends D2.1)** — lets the judge witness the
  full pipeline starting from an empty DB without restarting the backend.
  Pure-helper pattern: `services/project_reset.py` (stdlib only, 11/11 tests)
  is the contract; `ProjectService.reset_state` re-uses it with SQLAlchemy
  flag_modified on every JSON column. Frontend entry is a double-click
  confirm button inside the existing `Ctrl+Shift+J` overlay. Backed by
  `POST /api/v1/projects/{id}/reset-state`.
- **E2 (Rerun-plan drawer, extends A2.2)** — puts a judge-facing preview on
  top of the already-landed `rerun-plan` endpoint. `Ctrl+Shift+R` opens a
  right-side drawer showing which phases are fresh / stale / manual and
  which will actually re-run, with an amber callout for manual phases that
  cannot be touched by the AI pipeline. Executing commits through
  `POST /pipeline/rerun-stale`.
- **E3 (Networked datasheet sweep report, extends C1.4)** — turns the
  one-shot `verify_datasheets.py` into a committable evidence artefact.
  `--report` writes `docs/datasheet_sweep_latest.md` and `.json` so we can
  walk the judge through "these 75 vendor URLs verified most recently at
  this timestamp." Offline mode is CI-safe and is the current committed
  baseline (75/75 via whitelist); live mode runs the full HEAD/GET probe
  when the network allows. `make datasheets` and `make datasheets-offline`
  targets added.

Pass-5 adds 11 tests (`test_project_reset.py`) for a new total of **228/228**
in the iso harness. No new workstream tickets are created — these are tagged
as E1/E2/E3 under WS-A (E1, E2) and WS-C (E3) in the ticket tracker.

---

## v21 — 7-Stage Deterministic P1 Wizard (Architect Mode)

### Context

v20.1 added scope-first branching + per-turn scope reminder to prevent the
backend LLM forgetting the user's design scope across rounds. Demo feedback
surfaced deeper issues that scope-filter alone cannot fix:

- Backend LLM still occasionally skips Round-2 architecture selection, jumping
  straight from Round-1 specs to component generation.
- Round-1 quick-starts were scope-agnostic (same bank for full/front-end/DSP),
  so the free-text cold-start still produced hallucinated specs.
- No deterministic sanity layer — the agent can accept contradictory specs
  (e.g. NF < 1 dB at 40 GHz) without flagging them.
- Quick-reply suggestions aren't application-aware.

**Solution.** Take flow-control off the backend entirely for P1 Round 1. Build
a deterministic **7-stage frontend wizard** that walks the user through
gated decisions (SCOPE → APP → ARCH → SPECS → DETAILS → CONFIRM) before any
component generation. Architect intelligence (Friis cascade, MDS derivation,
inline suggestions, cascade sanity rules) runs client-side. The backend
only sees a structured, validated payload at the Confirm step.

The HTML prototype at `v21-prototype.html` implements this flow end-to-end
and is currently awaiting port to React.

### Seven-Stage Flow

1. **Stage 1 — Scope.** Full receiver / Front-end / Downconversion / DSP.
   (Stage 0 "Project Type" from the prototype is skipped in the React port —
   only receiver is supported; the project's `design_type` is captured at
   project-creation time.)
2. **Stage 2 — Application.** Radar / EW / SIGINT / Comms / SATCOM / T&M /
   Instrumentation / Custom. Drives arch ranking.
3. **Stage 3 — Architecture.** Filtered by scope + application. Split into
   linear signal-chain and detector-only (crystal-video, log-video) sections;
   detector topologies gated to Radar + EW apps only.
4. **Stage 4 — Tier-1 Specs.** Scope-filtered spec deck with `q_override`
   labels per scope (`"LNA chain gain"` for FE, `"RF + IF gain"` for
   downconversion, etc.). MDS derived from NF + IBW via Friis; advanced
   toggle exposes an explicit MDS-lock override. Environmental specs
   (temp class, vibration, IP rating) are Tier-1.
5. **Stage 5 — Deep-Dive Details.** Scope-specific follow-ups +
   application-specific addendum, each question scope-filtered via
   `scopes: [...]`. Conditional questions via `show_if(state)`:
   TX-leakage only when T/R switch selected, double-IF only when
   `superhet_double`, subsampling BP filter only when `subsampling` arch, etc.
6. **Stage 6 — Validate & Confirm.** Architect summary panel aggregates
   every derived value + inline suggestion + cascade rule fired across
   the full state tree. Confirm button serialises to a single structured
   message posted to existing `POST /api/v1/projects/{id}/chat`.

### Architect Intelligence Layer

- **Friis-cascade derivation** — NF/IBW → MDS (−174 + 10·log₁₀(BW) + NF)
  rendered live at Stage 4 + summary.
- **`AUTO_SUGGESTIONS` table** — question-id × value → deterministic advice
  string. Rendered inline beneath the answer AND aggregated in Stage 6
  summary panel.
- **`CASCADE_RULES` array** — 8 sanity checks covering gain stability,
  subsampling filter requirements, frequency-plan image, direct-RF clock
  jitter, zero-IF watchlist, BW-vs-ADC Nyquist, radar/EW arch-fit.

### Port Plan — Files to Change

| # | File | Action | Notes |
|---|---|---|---|
| 1 | `src/data/rfArchitect.ts` | **NEW** | ~450 LOC of PROJECT_TYPES, SCOPE_*, APPLICATIONS, ALL_ARCHITECTURES, ALL_SPECS, DEEP_DIVES, APP_QUESTIONS, AUTO_SUGGESTIONS, CASCADE_RULES + helpers (`derivedMDS`, `filterSpecsByScope`, `filterArchByScopeAndApp`, `resolveDeepDiveQs`, `resolveAppQs`, `allInlineSuggestions`, `archRationale`). Keeps ChatView.tsx under control. |
| 2 | `src/views/ChatView.tsx` | REWRITE pre-stage | Replace `preStage: 'scope' \| 'waiting' \| 'loading-clarify' \| 'clarifying' \| 'done'` state machine with `wizardStage: 1 \| 2 \| 3 \| 4 \| 5 \| 6 \| 'done'`. Existing `'done'` semantics preserved — falls through to free-form chat once finalized. |
| 3 | `src/types.ts` | MINOR | Add `WizardState` interface for localStorage round-trip. |
| 4 | `IMPLEMENTATION_PLAN.md` | APPEND | This section. |
| 5 | `v21-prototype.html` | FIX BUGS A–D | Keep HTML + React aligned; prototype stays as the spec reference. |
| 6 | `hardware-pipeline-v5-react/dist/**` → `frontend/bundle.html` | REBUILD | `npx vite build && python3 bundle_and_escape.py`. |

### Key Design Decisions

1. **Backend payload path — stringify, no new endpoint.** Stage 6 confirm
   serialises the full wizard state into a deterministic multi-line text
   payload and posts to existing `POST /api/v1/projects/{id}/chat` as the
   first user message. Zero backend changes. Keeps parity with the current
   `handleConfirmAnswers` pattern that already stringifies clarify answers.
2. **Skip Stage 0 TYPE in the React port.** Only receiver is implemented;
   project `design_type` is already set at create-project time. Saves a
   dead-click. Re-introduce when transmitter / transceiver / power-supply
   flows land.
3. **Per-project localStorage wizard state** (`hp-v21-wizard-${projectId}`).
   F5-safe — user doesn't lose stage 4 answers if the browser refreshes
   during the 5-minute flow. Cleared on finalize or on project switch.
4. **Replace `/clarify` backend call for Round 1.** The 7-stage wizard
   obviates `clarifyRequirement` for the initial elicitation. Endpoint is
   kept in place for future follow-up elicitation from the free-form chat
   after finalize.
5. **`preStage: 'done'` semantics preserved.** Finalize transitions to
   `wizardStage: 'done'` → existing free-form chat takes over → existing
   follow-up-card flow (`FollowUpCardGroup`, `filterCardsByScope`) is
   untouched.

### Bugs Found in `v21-prototype.html` (fix before porting)

| # | Bug | Fix |
|---|---|---|
| A | `AUTO_SUGGESTIONS.adc_enob['16-bit']` never fires in Full scope — full-scope ENOB chip values are bare `'12' / '14' / '16'` (no `-bit` suffix). | Normalize full-scope chips to `'12-bit' / '14-bit' / '16-bit'` OR add bare keys to AUTO_SUGGESTIONS. |
| B | `bw_vs_adc` cascade rule parses `sample_rate` as a bare number — `'1 Gsps'` and `'1 Msps'` both `parseFloat` to `1`. The check `sr < 3` is nonsensical. | Replace with an Hz-normalised map: `{'125 Msps': 125e6, '1 Gsps': 1e9, '> 3 Gsps': 3e9, ...}` and compare in Hz. |
| C | `radar_arch_fit` cascade rule false-alarms in front-end scope — fires for `std_lna_filter`, `balanced_lna`, etc. even though coherency is not the front-end's responsibility. | Add scope guard: `fires: s => ['downconversion','full'].includes(s.scope) && s.application === 'radar' && ...`. |
| D | `freq_plan_image` cascade rule references `s.details.if_freq` but `superhet_double` architecture uses `if1_freq` + `if2_freq` instead. Rule never fires for double-IF. | Read `s.details.if_freq \|\| s.details.if1_freq`. |
| E | (already fixed this session) `tx_leakage` showed under T/R switch = "No T/R switch (separate antennas)" — logically impossible. | Added `&& s.details?.tr_switch !== 'No T/R switch (separate antennas)'` to `show_if`. |

### Test Plan

1. `npx vite build` compiles without TS errors.
2. **Golden path A — Full Rx + Radar + Superhet-double:** all 6 stages,
   derived MDS populates at stage 4 after NF+IBW, cascade rule
   `freq_plan_image` fires at stage 6 after IF selected, `radar_arch_fit`
   does NOT fire.
3. **Golden path B — Front-end + EW + balanced-LNA:** SFDR and P1dB are
   hidden from the spec deck, `interferer_env` shows inline suggestion when
   set to "Severe", TX-leakage does not show (no T/R switch), architect
   summary at stage 6 lists triggered suggestions.
4. **Golden path C — DSP + Radar + Direct-RF-sample:** `clock_jitter`
   question visible, `direct_rf_clock` cascade rule fires, subsampling
   conditionals (`nyquist_zone`, `bp_filter_q`) NOT visible.
5. **F5 mid-flow:** at stage 4, refresh browser — localStorage restores
   wizard state, user lands back on stage 4 with prior answers intact.
6. **Finalize payload inspection:** check the message POST'd to `/chat`
   contains a parseable block with `[Design scope: X]`, architecture,
   every Tier-1 spec, deep-dive answers, app-specific answers, derived
   MDS, and the triggered suggestions list.

### Status

- v21-prototype.html — complete, 4 bugs identified above pending fix.
- React port — pending this plan approval.
- Backend changes — none required.

---

## Deferred — Option B: Shared RF Knowledge Base Refactor

**Status: backlog (do not execute yet).** Parked deliberately — a matching "Option A" consolidates the senior-RF-architect persona directly inside `requirements_agent.py` SYSTEM_PROMPT, which is proven, low-risk, and unblocks the 6-antenna × 4-channel EW demo. Option B is the architecturally correct follow-up once Option A is in production.

### Goal
Lift the consolidated RF-architect persona + project-specific rules out of `requirements_agent.py` into two standalone Markdown files, loaded via f-string substitution into every agent that needs them. This removes duplicate knowledge across the 7 AI agents and makes the persona independently testable/version-controllable.

### Target Layout
```
agents/
  knowledge/
    rf_architect_persona.md     # Identity, 20+ yr RF expertise, behavioral rules 1-10
    project_rules.md            # Mermaid syntax, tool-call forcing, anti-hallucination,
                                # topology enforcement, BOM qty multiplicity, compliance,
                                # thermal budget, datasheet URL / lifecycle gate
  requirements_agent.py          # loads both via {rf_kb} + {project_rules} placeholders
  hrs_agent.py                   # loads rf_architect_persona (read-only reference)
  compliance_agent.py            # loads project_rules (compliance section only)
  netlist_agent.py               # loads project_rules (Mermaid + anti-halluc only)
  glr_agent.py                   # loads rf_architect_persona + project_rules
  srs_agent.py                   # loads project_rules
  sdd_agent.py                   # loads project_rules
  red_team_agent.py              # loads BOTH — needed for cross-verification
```

### Precondition Checklist (do NOT start Option B until all of these are true)
- [ ] Option A merged, tested, and stable for ≥ 1 week on a live demo branch.
- [ ] At least one second agent (beyond requirements) provably needs the same RF knowledge block. Candidates: red-team audit agent (cascade re-verification), GLR agent (FPGA boundary definition against RF interfaces).
- [ ] Golden-test harness exists that runs all 7 agents against 3 canonical scenarios in < 2 minutes. Without this, regression risk across agents is unacceptable.
- [ ] `progress.md` shows Option A telemetry: hallucination rate, preselector-insertion rate, multi-antenna topology correctness. Option B must not regress any of these numbers.

### Migration Steps (when precondition cleared)
1. **Extract** the consolidated block from `requirements_agent.py::SYSTEM_PROMPT` into the two new Markdown files. Keep verbatim — no re-editing during extraction.
2. **Add a loader helper** in `agents/orchestrator.py`:
   ```python
   def _load_kb(name: str) -> str:
       from pathlib import Path
       p = Path(__file__).parent / "knowledge" / f"{name}.md"
       return p.read_text(encoding="utf-8")
   ```
3. **Replace** the inlined block with f-string placeholders:
   ```python
   SYSTEM_PROMPT = f"""
   {_load_kb('rf_architect_persona')}

   {_load_kb('project_rules')}

   ## DESIGN TYPE CONTEXT: {{design_type}}
   """
   ```
4. **Re-run golden tests** — confirm byte-identical output on the 3 canonical scenarios. Any drift = bug in the extraction, not a feature.
5. **Onboard the 6 other agents** one at a time, smallest first (netlist → compliance → srs → sdd → hrs → red-team → glr). Each gets its own golden-test gate.
6. **Document** the persona/rules contract in `docs/agent_knowledge_base.md` so future agent additions know what to reuse.

### Risks to Mitigate
- **File-path breakage on Windows / Linux ACL.** Loader must use `pathlib`, not string concat.
- **Caching staleness.** If any agent keeps a module-level cached `SYSTEM_PROMPT`, a `.md` edit won't pick up without restart. Add `reload()` hook or document "restart required".
- **Scope creep per agent.** It's tempting to let each agent customise the persona — resist. If an agent needs a variant, it gets a NEW knowledge file (`rf_architect_persona_glr.md`), not a sprinkle of in-code overrides.
- **Token budget blowout.** The combined persona + rules is ~5 KB. For GPT-4 class models this is fine, but run a token audit before adding to HRS (which already has its own large prompt).

### Rollback
Option B is a pure refactor. Any issue → revert the loader call and paste the Markdown back into the Python file. Keep the 6 agent changes in separate commits so each is individually revertable.

### Why Not Now
1. Option A is reversible, 30-min work, and unblocks today's demo.
2. The reusability case for Option B is thin in practice — only P1 requirements and red-team audit truly need the deep RF expertise. HRS/SRS/SDD format documents; compliance runs a rules engine; netlist maps pinouts. Premature abstraction.
3. Refactoring 7 agent files without a golden test harness is a 1–2 day regression risk right before the hackathon panel.

### Owner
WS-B (AI/ML Lead) is the right owner — this touches every AI agent's prompt. WS-A (Technical Lead) approves the loader helper and confirms no startup-latency regression.

