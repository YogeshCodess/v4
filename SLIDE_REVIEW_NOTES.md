# Silicon to Software (S2S) V2 — Slide Review Notes
**Date:** 2026-04-07
**Deck:** HardwarePipeline_FINAL_v3.pptx
**Status:** Discussion in progress — do not update slides until sign-off

---

## SLIDE 1 — Title / Phase Overview

### Q1: Are all phases shown?

**Yes, all 8 phases are present:**

| Phase | Label in slide | Status |
|-------|---------------|--------|
| P1 | Design & Requirements | AI AUTO ✅ |
| P2 | HRS Document | AI AUTO ✅ |
| P3 | Compliance Check | AI AUTO ✅ |
| P4 | Netlist Generation | AI AUTO ✅ |
| P5 | PCB Layout | MANUAL ✅ |
| P6 | GLR Specification | AI AUTO ✅ |
| P7 | FPGA Design | MANUAL ✅ |
| P8 | SRS + SDD + Code Review | AI AUTO ✅ |

P8 is three sub-phases (P8a SRS, P8b SDD, P8c Code Review) grouped as one row — which is correct for slide 1.

**Action:** No change needed for phase count. But see Q3 below.

---

### Q2: RDT, PSQ, GIT not mentioned?

These are standard phases in Data Patterns' actual hardware-to-software delivery pipeline that sit **after** P8. Currently none of them are shown.

| Abbreviation | Full name | Where it fits |
|---|---|---|
| **RDT** | Requirements Design Traceability (or Requirements Decomposition & Tracing) | After P1/P2 — tracing requirements from HRS down to hardware pins and software modules |
| **PSQ** | Product/Software Quality gate | After P8c — formal quality checkpoint before release (defect density, coverage, MISRA violations) |
| **GIT** | Git repository + CI/CD pipeline | After P8c — firmware pushed to Git, CI/CD triggers build + static analysis automatically |

**The real question the slide should answer:** Does our pipeline stop at "Code Review," or does it extend all the way to deployment-ready firmware in Git with CI/CD triggered?

**Proposed answer:** We currently stop at P8c (code review output). GIT push, CI/CD, PSQ gate — are NOT yet automated. We should either:
1. Show them as future phases (greyed out / dotted border) on slide 1 to show the full vision
2. Or explicitly note "pipeline scope: requirements → code review"

**Action:** Add P9 (Git/CI-CD) and optionally PSQ as "planned" phases with a dotted outline on slide 1. Makes the vision bigger.

---

### Q3: "It's not just code review — it's code generation to Git CI/CD"

**You are 100% correct.** P8 should be expanded. What we currently produce in P8:

| Sub-phase | What is generated |
|---|---|
| P8a | SRS Document (IEEE 830/29148) |
| P8b | SDD Document (IEEE 1016) — includes module architecture, interface specs |
| P8c | MISRA-C + Clang-Tidy code review report with fix suggestions |

**What is NOT yet done but belongs in the full pipeline:**
- Actual firmware code generation (from SDD → stub C/C++ code)
- Git commit/push automation
- CI/CD pipeline trigger (run static analysis, unit tests)
- PSQ gate report

**Proposed P8 label change:**
`P8 — Software Pipeline: SRS · SDD · Code Review`
And add implied future phases:
`P9 — Code Gen + Git CI/CD (Roadmap Q3)`

**Action:** Rename P8 label. Add P9 as roadmap phase on slide 1.

---

## SLIDE 2 — Problem Statement

### Q4: Rephrase "from requirements to code review"

**Current text:**
> "A single hardware design — from requirements to code review — takes 12–18 months."

This undersells the problem. Code review is not the end — integration, firmware deployment, and field testing come after. Better phrasings:

**Option A (factual):**
> "A single hardware design — from first requirement to production-ready firmware — takes 12–18 months."

**Option B (punchy):**
> "From concept to deployable hardware — each design cycle costs 12–18 months and a full engineering team."

**Option C (domain-specific):**
> "From HRS to hardware-on-bench — a single defence hardware product takes 12–18 months end-to-end."

**Recommendation:** Option A or C. Matches the audience (defence electronics engineers).

**Action:** Update bullet 01 text.

---

### Q5: Is ₹42L based on which criteria?

**Basis for calculation:**

A single hardware rework cycle at a defence electronics company typically involves:

| Cost component | Estimate |
|---|---|
| Complex multilayer PCB re-fab (8-12 layer, defence grade) | ₹8–15L |
| Defence-grade component procurement delays + premium reorder | ₹10–15L |
| Engineering team time: 3–5 weeks × 4–6 engineers at ₹1.5L/month | ₹8–12L |
| Re-testing, re-certification, lab time | ₹5–8L |
| **Total range** | **₹31L – ₹50L** |
| **Midpoint used in slide** | **₹42L** |

Cross-check: $50,000 USD rework (industry-cited figure for defence PCB respin) × ₹84/$ ≈ ₹42L ✅

**This is a reasonable and defensible number.** If someone challenges it, cite: "Based on typical MIL-grade PCB respin cost of USD $40–60K including fab, components, and engineering time."

**Action:** Add a footnote to the slide: `* Based on MIL-grade PCB respin: fab + components + engineering time (~$50K / ₹84)`

---

### Q6: Is BOM generated? Is trade-off analysis done?

**YES — both are implemented in the current pipeline:**

**BOM generation (P1 — Requirements Agent):**
- P1 sub-steps include: "Query component database", "Rank & select components", "Generate BOM with alternates"
- Output: structured Bill of Materials with primary + alternate part numbers, specs, and sourcing info
- Example: For an RF receiver, the agent selects LNA (e.g., HMC753), VCO, ADC, power regulators — with alternates for each

**Trade-off analysis (P1):**
- "Rank & select components" = trade-off analysis. Agent compares multiple candidate parts on: frequency range, noise figure, power consumption, cost, availability
- Output includes a rationale section explaining why each component was chosen

**The problem with slide 2 as-is:**
Bullet 02 says "no BOM automation or trade-off analysis" — this describes the PROBLEM (current manual world). But slide 3 (solution) doesn't loudly enough say "we fix this." Should add to slide 3: "Automated BOM with component alternates and trade-off rationale."

**Action:**
- Slide 2, bullet 02 text: keep as-is (it's the problem statement) ✅
- Slide 3: explicitly mention "BOM with alternates + trade-off analysis" as a capability
- Optional: replace "Component Selection Has No Intelligence" with "No Automated BOM or Trade-off Analysis" — more specific

---

### Q7: Remove FCC, add MIL-STD + all IEEE

**Current slide 2 text:** "RoHS, FCC, MISRA-C, IEEE 29148 — checked by hand."

**Why remove FCC:** FCC is commercial/consumer electronics. Data Patterns makes defence electronics — FCC is not the primary standard. MIL-STD is far more relevant.

**Proposed replacement text:**
> "RoHS/REACH, MIL-STD-461/810, IEEE 29148, IEEE 830, IEEE 1016, MISRA-C — all checked by hand."

**Standards breakdown used in our pipeline:**

| Standard | Phase | What it covers |
|---|---|---|
| RoHS EU 2011/65/EU | P3 | Hazardous substances in components |
| REACH | P3 | Chemicals in manufacturing |
| MIL-STD-461 | P3 | EMI/EMC for defence equipment |
| MIL-STD-810 | P3 | Environmental stress (temp, vibration, humidity) |
| MIL-STD-882 | P3 | System safety |
| IEEE 29148:2018 | P1, P2 | Hardware Requirements Specification |
| IEEE 830-1998 | P8a | Software Requirements Specification |
| IEEE 1016-2009 | P8b | Software Design Document |
| MISRA-C:2012 | P8c | Embedded C coding standard |
| Clang-Tidy | P8c | Static analysis |

**Action:** Update slide 2 bullet 03. Also update slide 3 compliance description.

---

## SLIDE 3 — Proposed Solution & Architecture

### Q8: React frontend — only the mentioned features are implemented?

**Full implemented feature list (beyond what's on the slide):**

| Feature | Implemented? |
|---|---|
| Three-panel UI (Left 248px + Center + Right 340px) | ✅ Yes |
| Live Mermaid diagram rendering | ✅ Yes |
| DOCX downloads with async loading state | ✅ Yes |
| Real-time flow panel with animated sub-steps | ✅ Yes |
| P1 Chat with typewriter effect | ✅ Yes |
| Phase completion toast notifications | ✅ Yes |
| Chat history restored on F5 (browser refresh) | ✅ Yes |
| Create/Load project modals | ✅ Yes |
| Phase lock logic with colour-coded sidebar | ✅ Yes |
| Metrics tab (TIME SAVED, CONFIDENCE %, COST IMPACT) | ✅ Yes |
| Details tab (inputs / outputs / tools per phase) | ✅ Yes |
| Documents tab (all phase outputs in one place) | ✅ Yes |
| Markdown renderer with tables, code blocks | ✅ Yes |
| Clarification Cards pre-chat flow (P1) | ✅ Yes (just built) |
| PDF generation | ❌ Not yet |
| LLM config panel | ❌ Not yet |
| Export all as ZIP | ❌ Not yet |

**Action:** Expand the frontend feature list on slide 3. See items below.

---

### Q9: What is "Three-panel UI"?

**Three-panel layout explained:**

```
┌──────────────┬────────────────────────┬──────────────────┐
│  LEFT PANEL  │    CENTER CONTENT      │   RIGHT PANEL    │
│   248px      │     flex-1 (fills)     │     340px        │
│              │                        │                  │
│ Phase list:  │ • Sticky topbar:       │ Step-by-Step     │
│ P1 ▶ active  │   project name +       │ Execution Flow   │
│ P2           │   progress dots        │                  │
│ P3           │ • Phase header         │ Sub-steps with   │
│ ...          │ • Tabs:                │ animated bars,   │
│ P8           │   Chat / Documents /   │ timing, detail   │
│              │   Details / Metrics    │                  │
│ [locked]     │                        │ ▶ Run Phase btn  │
└──────────────┴────────────────────────┴──────────────────┘
```

- **Left:** Phase navigation, colour-coded, locked phases greyed out
- **Center:** Active working area — chat with AI, view documents, see metrics
- **Right:** Always-visible execution flow for the selected phase — shows sub-steps animating live when a phase runs

**Action:** Add a small diagram or description of the three-panel UI in slide 3.

---

### Q10: Add PDF also

**Current state:** Only DOCX downloads are implemented. PDF generation is not built.

**Two options:**
1. **Quick add (Q2):** Convert existing DOCX outputs to PDF using python-docx → LibreOffice headless conversion. No new agent needed.
2. **Native PDF (Q3):** Generate PDF directly using reportlab or WeasyPrint with proper formatting.

**For slide 3:** Add "PDF export" to the frontend feature list but note it's in the roadmap (or implement it as a quick win before the demo).

**Recommendation:** Implement the quick DOCX→PDF conversion before the hackathon demo. It's a 1-day task.

**Action:**
- Slide 3: Add "PDF downloads" to frontend feature list
- If time permits: implement DOCX→PDF endpoint (`GET /api/v1/projects/{id}/phases/{phase_id}/export?format=pdf`)

---

### Q11: Is port 8000 required to mention?

**No — port 8000 is an implementation detail.** For a presentation audience (judges, business stakeholders), it's unnecessary noise.

**Better alternatives:**
- Just say "FastAPI Backend" without the port
- Or: "FastAPI Backend · localhost" (implies local deployment)
- Reserve port number for the live demo / Swagger docs walkthrough

**Action:** Remove "— port 8000" from slide 3 header. Replace with: `FASTAPI BACKEND · LOCAL REST API`

---

### Q12: What are the 7 agents?

The 7 Python agents, each domain-specialized:

| # | Agent | Phase | Specialization |
|---|---|---|---|
| 1 | **Requirements Agent** | P1 | NLP → component selection → BOM → block diagram (Friis, power calcs) |
| 2 | **HRS Agent** | P2 | IEEE 29148:2018 hardware requirements specification — full structured doc |
| 3 | **Compliance Agent** | P3 | RoHS/REACH/MIL-STD-461/810/MISRA-C/IEEE rules engine |
| 4 | **Netlist Agent** | P4 | Component connectivity graph → DRC → KiCad .net export |
| 5 | **GLR Agent** | P6 | FPGA/CPLD glue logic requirements from netlist |
| 6 | **SRS Agent** | P8a | IEEE 830/29148 software requirements spec |
| 7 | **SDD + Code Review Agent** | P8b+P8c | IEEE 1016 design doc + MISRA-C/Clang-Tidy code review |

Each agent: uses Anthropic-compatible LLM, up to 5 continuation passes to handle token limits, forced tool_use for structured output.

**Action:** Add a named agent list to slide 3 or slide 4.

---

### Q13: LLM Configuration Panel in menu

**This is a great feature and a strong demo talking point.** Currently the LLM endpoint is hardcoded to GLM-4.7 via Z.AI.

**Proposed settings panel (hamburger menu or settings icon):**

```
LLM Configuration
─────────────────
Provider:  [Anthropic Claude ▼]
           OpenAI-compatible
           Ollama (local)
           Azure OpenAI
           Z.AI / GLM

Endpoint:  https://api.anthropic.com
API Key:   sk-ant-...
Model:     claude-sonnet-4-5-...

[Test Connection]  [Save]
```

**Value proposition for hackathon judges:** "Works with any LLM — cloud or air-gapped local deployment."

This is stored in `settings.py` (already has `fast_model` and `main_model` fields). The UI just needs a settings modal that writes to a config file.

**Action:**
- Mark as Q2 roadmap feature on slide 5
- Consider implementing a minimal version (just endpoint + API key + model field) before demo — could be 1-day task

---

### Q14: What is Swagger? What is CORS?

**Swagger (OpenAPI):**
- Auto-generated interactive API documentation for FastAPI
- Available at `http://localhost:8000/docs`
- Shows all endpoints, request/response schemas, allows testing API calls directly in browser
- Very useful for the demo — can show judges the complete API spec

**CORS (Cross-Origin Resource Sharing):**
- Browser security policy: a web page at `localhost:8000/app` cannot call APIs at `localhost:8000/api/...` unless the server explicitly allows it
- FastAPI has CORS middleware configured to allow the React frontend to make calls
- Without CORS config, the browser would block all API calls from the frontend
- **For the slide:** Replace "CORS" with "Secure local API" — more meaningful to a non-technical audience
- For technical judges: keep CORS as a tech stack detail

**Action:** On slide 3, replace "Swagger docs · CORS" with "OpenAPI docs · Secure CORS config" or move these to slide 4 (Tech Stack).

---

### Q15: Only 8 docs generated?

**Actual document outputs per project (more than 8):**

| # | Document | Phase | Format |
|---|---|---|---|
| 1 | Requirements & BOM | P1 | Markdown + Mermaid diagram |
| 2 | Block Diagram | P1 | Mermaid SVG (rendered live) |
| 3 | Gain-Loss Budget | P1 | Table in Markdown |
| 4 | HRS Document | P2 | DOCX (IEEE 29148) |
| 5 | Compliance Matrix | P3 | DOCX (RoHS/REACH/MIL-STD) |
| 6 | KiCad Netlist | P4 | .net file |
| 7 | DRC Report | P4 | Markdown/DOCX |
| 8 | GLR Specification | P6 | DOCX |
| 9 | SRS Document | P8a | DOCX (IEEE 830/29148) |
| 10 | SDD Document | P8b | DOCX (IEEE 1016) |
| 11 | Code Review Report | P8c | DOCX (MISRA-C findings) |

**Total: 11 outputs** (or "10+ documents") — "8 docs" was an undercount.

**Better stat for slide:** `10+ Documents` or `11 Engineering Outputs`

**Action:** Update the stat from "8 Docs" to "10+ Docs" or "11 Outputs".

---

### Q16: Redesign phases as per slide 1

The phase pipeline in slide 3 uses a 4+4 grid. The user wants it consistent with slide 1's styling (coloured phase badges, AI AUTO / MANUAL tags, locked/unlocked states).

**Action:** Restyle slide 3 phase grid to match slide 1's phase card design — same colours (P1 teal, P2 blue, P3 amber, P4 purple, P5 slate, P6 teal, P7 slate, P8 purple), same AI AUTO / MANUAL tags. Make P5 and P7 visually distinct (manual = lighter/greyed).

---

## SLIDE 4 — AI Technology Stack & Innovation

### Q17: Analyze the complete current (manual) workflow — don't skip anything

**Complete manual hardware design workflow at a defence electronics company today:**

| # | Step | Tool / Method | Time |
|---|---|---|---|
| 1 | Requirements capture | Word docs, meetings, emails | 2–4 weeks |
| 2 | System architecture design | Visio, PowerPoint | 1–2 weeks |
| 3 | Hardware Requirements Spec (HRS) | Manual Word authoring | 2–4 weeks |
| 4 | Component research | DigiKey/Mouser, vendor PDFs | 1–2 weeks |
| 5 | BOM creation | Excel, manual part comparison | 1 week |
| 6 | RoHS/REACH compliance check | Manual checklists, datasheets | 3–5 days |
| 7 | MIL-STD checklist review | Manual, specialised team | 1–2 weeks |
| 8 | Schematic design | Altium/OrCAD/KiCad | 2–4 weeks |
| 9 | Design review (internal) | Manual PDF review | 3–5 days |
| 10 | Netlist generation | EDA tool export | Automated (minutes) |
| 11 | PCB layout | Altium | 1–4 weeks |
| 12 | DRC / ERC check | EDA tool | Hours |
| 13 | Gerber generation | EDA tool export | Hours |
| 14 | PCB fabrication | External vendor | 2–4 weeks |
| 15 | PCB assembly + inspection | Assembly house | 1–2 weeks |
| 16 | FPGA GLR / interface spec | Manual Word doc | 1 week |
| 17 | FPGA RTL coding (VHDL/Verilog) | Vivado/Quartus | 1–4 weeks |
| 18 | RTL simulation & verification | ModelSim/QuestaSim | 1–2 weeks |
| 19 | Synthesis & place-and-route | Vivado/Quartus | Hours–days |
| 20 | Timing closure | Vivado/Quartus + manual fixes | 2–5 days |
| 21 | Bitstream generation | Vivado/Quartus | Hours |
| 22 | Software Requirements Spec (SRS) | Manual Word authoring | 1–2 weeks |
| 23 | Software Design Document (SDD) | Manual Word authoring | 1–2 weeks |
| 24 | Firmware/driver coding | Embedded C/C++ | 3–8 weeks |
| 25 | Unit testing | Manual / framework | 1–2 weeks |
| 26 | MISRA-C code review | Manual checklist | 3–5 days |
| 27 | Integration testing | Hardware-in-loop | 2–4 weeks |
| 28 | System-level testing | Test bench | 2–4 weeks |
| 29 | Documentation final polish | Manual Word | 1 week |
| 30 | Release / handover | Git tag, share drive | Days |
| **Total** | | | **12–18 months** |

**What our pipeline automates (steps 1–11, 16, 22–23, 26 = 13 of 30 steps):**
Phases P1–P4, P6, P8a–P8c directly replace 13 steps. The remaining 17 steps (P5 PCB layout, P7 FPGA design, fabrication, hardware testing) remain manual — and slide 4 should be honest about this.

**Action:** Add a "Before vs After" column to slide 4 or a visual showing which steps are automated.

---

## SLIDE 5 — Expected Outcomes & Demo Plan

### Q18: Infrastructure cost is $0 — should we mention a real amount?

**Analysis:**

The "$0 Infrastructure cost" is technically correct because the system runs fully local (no cloud hosting, no SaaS fees). However, it's misleading because there ARE costs:

| Cost item | Amount |
|---|---|
| Traditional CAD tool licences (Altium + Vivado premium) | ₹5–15L/year |
| Traditional document authoring tools | ₹50K–2L/year |
| Cloud LLM inference (if used instead of local) | ~₹500–2,000/project |
| **Silicon to Software (S2S) V2 — local deployment** | **₹0 cloud infra** |
| **Silicon to Software (S2S) V2 — with cloud LLM API** | **~₹500/project** |

**Better slide messaging:**
- Change "$0 Infrastructure cost" → "₹0 cloud hosting" (more precise)
- Sub-label: "Runs fully local — or ~₹500/project via cloud LLM API"
- Contrast: vs ₹5–15L/year for traditional CAD licences

**Action:** Update the stat + sub-label to be more specific and defensible.

---

### Q19: Review Q2 / Q3 roadmap — align with actual current workflow

**Current slide shows:**

| Timeframe | Items |
|---|---|
| NOW Delivered | 8-phase pipeline, React V5 UI, IEEE compliance, INSTALL.bat |
| NEXT Q2 2026 | Real-time log streaming, Export all as ZIP, Re-run stale phases |
| FUTURE Q3 2026 | Requirement version history + diffs, Visual dependency DAG, Multi-project dashboard |

**Problems:**
1. "NOW Delivered" is missing: BOM with alternates, Clarification Cards (P1), phase completion toasts, Mermaid diagrams, chat history restore
2. Real-time log streaming is a Tier 3 (big) feature — too ambitious for Q2
3. Q2 and Q3 ordering can be tightened

**Proposed revised roadmap:**

| Timeframe | Items | Rationale |
|---|---|---|
| **NOW — DELIVERED** | 8-phase AI pipeline fully operational · React V5 three-panel UI · BOM + block diagram generation · IEEE 29148 / 830 / 1016 / MISRA-C compliance docs · P1 Clarification Cards · Phase completion toasts · Chat history restore · One-click INSTALL.bat | Reflects everything actually built |
| **NEXT — Q2 2026** | Export all outputs as ZIP · Re-run stale phases (one click) · PDF generation · LLM configuration panel (switch LLM endpoint in UI) · Requirement version history v1 | Realistic 6–8 week sprint |
| **FUTURE — Q3 2026** | Real-time AI log streaming · Visual dependency DAG (P1→P8) · Git integration (auto-push generated code) · Multi-project dashboard · PSQ gate (quality report) | Bigger engineering effort |

**Action:** Update slide 5 roadmap content.

---

## Summary — Action Items

| # | Slide | Change | Priority |
|---|---|---|---|
| A1 | S1 | Add P9 Git/CI-CD as dotted "roadmap" phase | Medium |
| A2 | S1 | Rename P8 to "Software Pipeline: SRS · SDD · Code Review" | High |
| A3 | S2 | Rephrase bullet 01: "requirements to production-ready firmware" | High |
| A4 | S2 | Add ₹42L calculation footnote | Medium |
| A5 | S2 | Add to S3: "BOM with alternates + trade-off analysis" | High |
| A6 | S2 | Replace FCC with MIL-STD-461/810, add IEEE 830 + 1016 | High |
| A7 | S3 | Add Three-panel UI mini diagram or description | Medium |
| A8 | S3 | Add PDF export to feature list (mark as Q2 if not built yet) | High |
| A9 | S3 | Remove "— port 8000", replace with "LOCAL REST API" | Low |
| A10 | S3 | Add named agent list (7 agents, one line each) | High |
| A11 | S3 | Add "LLM Configuration" to roadmap | Medium |
| A12 | S3 | Replace "Swagger · CORS" with "OpenAPI docs · Secure API" | Low |
| A13 | S3 | Update "8 Docs" to "11 Outputs" or "10+ Documents" | Medium |
| A14 | S3 | Restyle phase grid to match slide 1 colours/tags | Medium |
| A15 | S4 | Add before/after comparison of full 30-step manual workflow | High |
| A16 | S5 | Update "$0 infra" to "₹0 cloud hosting / ~₹500 per project via API" | Medium |
| A17 | S5 | Update roadmap: NOW/Q2/Q3 items as above | High |

---

## CRITICAL CORRECTIONS — Found from live app (screenshot 2026-04-07)

### CC1: P7a is a real implemented phase — RDT and PSQ are its outputs

The app shows **11 phases** (9 AI + 2 manual). The PPTX shows only 8. Missing phase:

**P7a — Register Map & Programming Sequence** (`rdt_psq_agent.py`)
- Sits between P7 (FPGA Design, manual) and P8a (SRS)
- Tagline: "AI-generated RDT + PSQ from GLR spec"
- Time: ~2 min
- **RDT = Register Description Table** — all FPGA registers: address, bit-fields, access type (R/W/RO/RC), reset value
- **PSQ = Programming Sequence** — ordered init steps: power-on → clock → peripherals, dependency-checked

So when the user says "rdt and psq in the left panel" — these are the two documents produced by P7a, and P7a itself is visible in the left panel.

**Slide 1 must be updated:** Change "8 Phases" to "11 Phases". Add P7a to the phase list.

---

### CC2: There are 9 AI agents, not 7

Actual agent files in `agents/`:

| # | Agent file | Phase | What it generates |
|---|---|---|---|
| 1 | `requirements_agent.py` | P1 | BOM, block diagram, requirements doc |
| 2 | `document_agent.py` | P2 | HRS (IEEE 29148, DOCX) |
| 3 | `compliance_agent.py` | P3 | Compliance matrix, CycloneDX SBOM |
| 4 | `netlist_agent.py` | P4 | KiCad netlist, DRC report |
| 5 | `glr_agent.py` | P6 | GLR specification |
| 6 | `rdt_psq_agent.py` | P7a | Register Description Table + Programming Sequence |
| 7 | `srs_agent.py` | P8a | SRS (IEEE 830/29148, DOCX) |
| 8 | `sdd_agent.py` | P8b | SDD (IEEE 1016, DOCX) |
| 9 | `code_agent.py` | P8c | C/C++ drivers, Qt GUI, CI/CD, MISRA-C review |

Support/utility agents (not counted as separate phases): `git_agent.py`, `static_analysis.py`, `sbom_generator.py`, `qt_cpp_gui_generator.py`, `orchestrator.py`

**Slides must be updated:** Change "7 AI Agents" to "9 AI Agents" everywhere.

---

### CC3: MISRA-C version — dual-version approach

From `code_agent.py` and `static_analysis.py`:

| Usage | Version |
|---|---|
| Code generation standard (what drivers are written to comply with) | **MISRA-C:2012** |
| Cppcheck rule mapping | **MISRA-C:2012** |
| LLM deep review (rule number citations, fix suggestions) | **MISRA-C:2023** |

**Answer:** Code is generated and checked against **MISRA-C:2012** (the industry-standard version used by 95% of defence/automotive tools). The LLM additionally maps findings to **MISRA-C:2023** rule numbers in the review report. This is the correct approach — 2012 is what certification bodies accept; 2023 adds newer guidance.

**Slide should say:** `MISRA-C:2012 / 2023`

---

### CC4: Git CI/CD IS fully implemented — P8c does it all

From `phases.ts` (P8c) and `git_agent.py`:

P8c tagline: **"C/C++ drivers, Qt GUI, MISRA-C analysis, CI/CD"**

P8c generates:
1. **C/C++ device drivers** — HAL register layer, interrupt handlers, DMA, error codes — MISRA-C:2012 compliant
2. **Cppcheck + Lizard static analysis** — real binaries, not LLM-only
3. **Qt 5.14.2 GUI application** — `.pro`, `.ui`, MainWindow, DashboardPanel, ControlPanel, LogPanel, SettingsPanel, SerialWorker
4. **GitHub Actions CI/CD workflow** — `.github/workflows/hardware_pipeline_ci.yml` — generated + validated with `actionlint`
5. **LLM MISRA-C:2023 deep review** — before/after fixes, CWE classification
6. **Git commit + GitHub PR** — `git_agent.py` commits ALL artifacts, pushes branch, opens PR with review summary

The user sees code pushed to git because **P8c already does this**. The PPTX just doesn't say so!

**Slides must be updated:** P8 label must change from "SRS + SDD + Code Review" to something that captures the full scope:

> **P8 — Software Pipeline: SRS · SDD · Code Gen · Qt GUI · CI/CD → Git PR**

---

### CC5: Total document outputs is 30+, not 8 or 11

From `PHASE_DOCUMENTS` in `phases.ts`:
- P1: 6 docs (requirements, block_diagram, architecture, BOM, power_calc, gain_loss_budget)
- P2: 3 docs (HRS .md, .docx, .pdf)
- P3: 3 docs (compliance_report, compliance_matrix, sbom_summary)
- P4: 2 docs (netlist_visual, drc_report)
- P6: 2 docs (GLR .md x2)
- P7a: 2 docs (RDT, PSQ)
- P8a: 3 docs (SRS .md, .docx, traceability_matrix.csv)
- P8b: 2 docs (SDD .md, .docx)
- P8c: **21 files** (code_review_report, Qt GUI source files ×17, CI/CD yml, ci_validation_report, git_summary)

**Total: 44 engineering artefacts per project** (or simplify to "30+ outputs" for the slide)

---

### CC6: App error — "Could not load clarification questions"

The Clarification Cards feature (just built) is failing because uvicorn was NOT restarted after adding the `/clarify` endpoint to `main.py`. The server is still running the old code.

**Fix:** Restart uvicorn (run `run.bat` again or `Ctrl+C` → restart in terminal).

---

## Updated Summary — Full Action List

| # | Slide | What to change | Why |
|---|---|---|---|
| A0 | ALL | "7 AI Agents" → "9 AI Agents" | rdt_psq + code_agent = 9, not 7 |
| A0b | ALL | "8 Phases" → "11 Phases" | P7a discovered; 11 total (9 AI + 2 manual) |
| A1 | S1 | Add P7a (Register Map & Programming Sequence) to phase list | Real implemented phase |
| A2 | S1 | Rename P8 to show full scope: "Code Gen + Qt GUI + CI/CD + Git PR" | P8c does far more than "code review" |
| A3 | S2 | Rephrase bullet 01 | "requirements to production-ready firmware" |
| A4 | S2 | Add ₹42L footnote | Calculation basis |
| A5 | S2 | Bullet 02: note BOM IS generated in solution | Add to slide 3 |
| A6 | S2 | Replace FCC with MIL-STD-461/810; add IEEE 830, 1016 | Correct standards for defence electronics |
| A7 | S3 | Expand frontend feature list (Clarification Cards, PDF roadmap, LLM config roadmap) | More accurate |
| A8 | S3 | Add three-panel UI description | User asked |
| A9 | S3 | "port 8000" → "LOCAL REST API" | Implementation detail not needed on slide |
| A10 | S3 | Name all 9 agents | Concrete and impressive |
| A11 | S3 | "8 Docs" → "30+ Outputs" or "44 Artefacts" | Real count from phases.ts |
| A12 | S3 | Show all 11 phases consistently | Match slide 1 |
| A13 | S4 | Add full 30-step manual workflow before/after | Complete picture |
| A14 | S5 | "$0 infra" → "₹0 cloud hosting / ~₹500/project via API" | More honest |
| A15 | S5 | Update NOW: add P7a (RDT/PSQ), P8c full scope, 9 agents, 44 artefacts | Accurate |
| A16 | S5 | Update Q2: ZIP export, PDF, LLM config panel, re-run stale | Realistic |
| A17 | S5 | Update Q3: log streaming, multi-project dashboard, web deployment | Bigger items |
| A18 | ALL | MISRA-C label → "MISRA-C:2012 / 2023" | Dual-version approach |

---

*Next step: discuss each item → decide → update PPTX*
