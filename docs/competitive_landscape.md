# Competitive Landscape — Silicon to Software (S2S) for Defense RF Electronics

**Status:** Draft, April 2026. Prepared for the Great AI Hack-A-Thon 2026
submission as part of deliverable D1.4 (IMPLEMENTATION_PLAN.md).

**Scope:** This document positions our "Silicon to Software (S2S)" submission
against the adjacent tools, frameworks, and prior art that a judge is
likely to ask about. It is deliberately company-agnostic; where a category
of product is named, we describe the class of functionality rather than
endorsing a specific vendor.

---

## 1. Problem framing

Defense RF and mixed-signal hardware design today is characterised by:

- **Long, sequential artefact generation:** requirements, Hardware Requirements
  Specification (HRS), compliance matrix, netlist, PCB, FPGA RTL, SRS/SDD,
  code review. Each artefact is mostly prose and is typically produced by a
  different engineer.
- **Standards-intensive compliance:** MIL-STD-461G, MIL-STD-810H, MIL-STD-704F,
  DO-254, DO-160G, STANAG 4586, MIL-STD-188-141/164/181, MIL-STD-1275, and
  part-screening references (MIL-STD-883, MIL-PRF-38535, MIL-STD-883 Method
  1019 for TID). Missing or misquoted clauses cost programme schedule.
- **High hallucination risk for LLMs:** manufacturers, clauses, and part
  numbers look plausible to a language model but are trivially falsifiable by
  a reviewer. The cost of a wrong citation is real.
- **Air-gapped and ITAR-constrained environments:** many defence primes cannot
  use a hosted LLM for some or all of a project's lifecycle.

The Silicon to Software (S2S) addresses those constraints by combining elicitation
(4-round, domain-adaptive), deterministic tooling (cascade validator,
requirements lock, datasheet verifier, red-team audit), and a clean
separation between manual and AI phases so that engineers retain authorship
of PCB and FPGA work.

---

## 2. Adjacent tool categories

### 2.1 Electronic Design Automation (EDA) suites

Examples include the major schematic/PCB suites (Cadence Allegro/OrCAD,
Altium, Siemens Xpedition, KiCad, Zuken CR-8000) and the FPGA vendor toolchains
(AMD Vivado, Intel Quartus, Lattice Radiant, Microchip Libero SoC).

- **What they do well:** schematic capture, layout, DRC, timing closure, place
  and route, signal integrity, production outputs.
- **What they do not do:** natural-language elicitation of system requirements,
  generation of standards-traceable HRS / SRS / SDD prose, or cross-artefact
  consistency checks between a written requirement and a component BOM.
- **Our relationship:** strictly upstream and complementary. The Hardware
  Pipeline terminates P4 at a *validated netlist* and P6 at a *GLR specification*,
  which are exactly the inputs a PCB/FPGA engineer imports into their EDA
  tool of choice. P5 (PCB layout) and P7 (FPGA design) are explicitly manual.

### 2.2 Requirements management and traceability

Examples include IBM DOORS / DOORS Next, Jama Connect, Polarion, Siemens
Polarion ALM, ReqIF-based tools.

- **What they do well:** requirement storage, versioning, traceability matrices,
  review workflows, integration into a safety case (DO-254 / DO-178C).
- **What they do not do:** *generate* the first-draft requirement set from a
  subject-matter conversation, recompute derived RF specs, or verify that a
  requirement's cited standard clause actually exists.
- **Our relationship:** the Silicon to Software (S2S) feeds these tools. Requirements
  frozen in P1 (via the SHA-256 `requirements_lock` — see `services/requirements_lock.py`
  and ADR-001) export cleanly as ReqIF or CSV into DOORS/Jama for the formal
  programme baseline. We do not replace the system of record.

### 2.3 LLM-powered code and documentation copilots

Examples include GitHub Copilot / Copilot Chat, Anthropic's Claude for
Enterprise, Google Gemini Code Assist, Cursor, Cody.

- **What they do well:** in-IDE code completion, snippet generation, interactive
  explanation, chat-driven editing of existing files.
- **What they do not do:** enforce a 4-round elicitation before producing a
  design, run deterministic Friis / IIP3 cascade math on the proposed BOM,
  validate citations against a clause database, detect fabricated part numbers,
  or freeze a hashable requirements baseline.
- **Our relationship:** Silicon to Software (S2S) is an *application-level* system that
  can use any of these LLM endpoints under the hood (see ADR-001 on model
  selection and fallback). The value is in the deterministic scaffolding —
  cascade validator, requirements lock, red-team audit — not in the underlying
  token predictor.

### 2.4 LLM orchestration frameworks

Examples include LangChain, LlamaIndex, Haystack, Semantic Kernel, Guidance,
DSPy.

- **What they do well:** prompt templating, tool-calling wrappers, retrieval
  plumbing, agent loops.
- **What they do not do:** provide defence-specific content — standards
  clauses, component datasheets, RF math tools, screening-class metadata, or
  an opinionated 4-phase elicitation.
- **Our relationship:** the pipeline's agents could be refactored to use any
  of these frameworks. We deliberately chose a thin custom agent layer
  (`agents/base_agent.py` + per-phase subclasses) to keep the deterministic
  tools first-class and to make ablation experiments easy (see the D1.3 matrix
  planned under `scripts/run_baseline_eval.py`).

### 2.5 Compliance / standards databases

Examples include ASSIST (Defense Standardisation Program), IEEE Xplore,
SAE, IEC WebStore, ITU publications, NATO Standardization Office (NSO).

- **What they do well:** official canonical text of every standard, including
  revision history.
- **What they do not do:** machine-readable clause lookup, severity tagging,
  or domain applicability ("this clause applies to SATCOM ground terminals
  but not to HF radios").
- **Our relationship:** `domains/standards.json` curates a targeted subset
  (49 clauses at the time of writing, expanding with the programme) with
  severity and domain applicability tagged for runtime lookup. The
  authoritative text remains with the standards body; our DB is a *pointer*
  index backed by product-page URLs, with `validate_citations()` catching
  hallucinated or misquoted entries.

### 2.6 Specialist RF design aids

Examples include Keysight Genesys / SystemVue, Analog Devices ADIsimPLL /
ADIsimRF, Cadence AWR Design Environment, Skyworks SkyWorks DE, Mini-Circuits
Yoni-Da simulation tools.

- **What they do well:** physical-layer simulation, cascade analysis, PLL
  phase-noise math, filter synthesis.
- **What they do not do:** accept a natural-language description of a radar
  or EW system and produce a first-cut BOM with traceable standards
  references.
- **Our relationship:** our cascade validator (`tools/cascade_validator.py`)
  is deliberately thin and deterministic — it targets the "is this claim
  self-consistent" question, not the "exhaustive simulation" question. Real
  programmes will still run a SystemVue or AWR sweep before tape-out. Our
  output is a *first draft good enough to simulate*, not a replacement for
  simulation.

---

## 3. Differentiation summary

| Capability                                | EDA suites | Requirements tools | LLM copilots | LLM frameworks | Standards DBs | RF CAD | **Silicon to Software (S2S)** |
|-------------------------------------------|:----------:|:------------------:|:------------:|:--------------:|:-------------:|:------:|:---------------------:|
| NL elicitation, 4-round domain-adaptive   |            |                    |   partial    |    partial     |               |        |          yes          |
| Friis / IIP3 cascade math on BOM          |            |                    |              |                |               |  yes   |          yes          |
| Citation validation against clause DB     |            |                    |              |                |   source only |        |          yes          |
| Red-team audit of agent output            |            |                    |              |                |               |        |          yes          |
| SHA-256 requirements lock + staleness     |            |      via VCS       |              |                |               |        |          yes          |
| Datasheet URL verification pass           |            |                    |              |                |               |        |          yes          |
| Air-gap / model-swap ready                |    n/a     |        n/a         |   partial    |    partial     |      n/a      |  n/a   |          yes          |
| Standards-traceable HRS / SRS / SDD prose |            |     consumes       |   partial    |                |               |        |          yes          |

None of the adjacent categories were built for defence hardware elicitation
with deterministic anti-hallucination guardrails. The competitive moat is the
*scaffolding*, not any one agent.

---

## 4. Anti-patterns we explicitly avoid

- **"Trust the model":** every numeric claim (NF, gain, IIP3, sensitivity,
  SFDR) is recomputed from the BOM by `tools.cascade_validator` and compared
  against the agent's claim in `agents.red_team_audit.check_cascade_vs_claims`.
- **Fabricated citations:** `check_citations` walks `domains.standards.find_clause`
  over every (standard, clause) pair and surfaces unresolved ones as `high`
  severity audit issues.
- **Fabricated part numbers:** `check_part_numbers` cross-references the
  domain component DB and requires a resolvable datasheet URL when a part
  is not in the DB.
- **"Requirements drift":** once P1 completes the 4-round elicitation, the
  canonical-JSON SHA-256 hash is frozen (A1.1) and every downstream phase is
  marked stale if the hash changes (see migration 001).
- **Silent model swaps:** ADR-001 documents the primary + fallback chain and
  every LLM call is logged with model name, version, temperature, and
  prompt/response hashes in the `llm_calls` table (migration 002).

---

## 5. Open questions for the judges

- *"Does this replace my requirements engineer?"* — No. It replaces the
  first-draft keystrokes and the citation-lookup toil. Requirements engineers
  still own review, sign-off, and the trace into the formal record.
- *"Does this replace my PCB / FPGA engineer?"* — No. P5 and P7 are manual
  phases in the pipeline, on purpose. The AI's job is to give those engineers
  a validated starting point.
- *"What happens when the LLM is wrong?"* — The red-team agent catches the
  common failure modes (cascade lies, fake clauses, fake parts, co-site
  IMD3 blind spots). The user sees those issues surfaced in the P1 UI and
  must resolve them before the requirements lock is written.
- *"Can this run air-gapped?"* — Yes. The deterministic tools (cascade,
  clause DB, datasheet verify, requirements lock, red-team audit) have no
  network dependencies. The LLM layer falls back to a local Ollama endpoint
  per ADR-001; the air-gap rehearsal playbook is forthcoming in
  `docs/air_gap_rehearsal.md`.

---

## 6. References

- IMPLEMENTATION_PLAN.md — workstreams A/B/C/D.
- docs/adr/ADR-001-model-selection.md — model tiers and logging.
- docs/eval_rubric.md — five-dimension acceptance rubric.
- domains/standards.json — 49-clause curated index.
- domains/\*/components.json — 75-part curated defence RF catalogue.
- tools/cascade_validator.py, tools/datasheet_verify.py
- agents/red_team_audit.py, services/requirements_lock.py
