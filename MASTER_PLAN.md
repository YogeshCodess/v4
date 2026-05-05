# MASTER PLAN: Silicon to Software (S2S) AI System
## Team Code Knights - Data Patterns AI Hack-A-Thon 2026
## Full Package: Synopsis + Tech Stack + Implementation

---

## 0. IEEE STANDARDS COMPLIANCE (Applied to All Documents)

All generated documents follow IEEE standards structure, numbering, and content requirements. This ensures outputs are **audit-ready** and directly usable in formal design reviews.

| Document | IEEE Standard | Key Sections Required |
|---|---|---|
| **HRS** | IEEE 29148:2018 (adapted for HW) | Introduction, System Overview, HW Requirements (functional/performance/interface/environmental), Design Constraints, Verification Requirements, Traceability Matrix |
| **SRS** | IEEE 830 / IEEE 29148:2018 | Introduction, Overall Description, Specific Requirements (external interfaces, functional, performance, design constraints, quality attributes), Verification & Validation |
| **SDD** | IEEE 1016-2009 | Introduction, Design Viewpoints (Context, Composition, Logical, Dependency, Information, Interface, Structure, Interaction, State, Algorithm, Resource), Design Views, Design Rationale |
| **GLR** | IEEE 29148 (adapted) | Introduction, I/O Requirements, Timing Constraints, Interface Specifications, Verification |

### How IEEE Is Enforced
1. **Templates** - Each `.md` template has IEEE-mandated section structure pre-built with section numbers
2. **Agent System Prompts** - Each document agent's system prompt includes the relevant IEEE standard requirements, so Claude generates content that fits mandated sections
3. **Post-Generation Validator** - Checks all required IEEE sections are present, non-empty, and properly numbered
4. **Traceability** - Requirements are numbered (REQ-HW-001, REQ-SW-001) and traced across HRS -> SRS -> SDD -> Code

---

## 1. CONTEXT

**Problem:** Data Patterns engineers waste 60-80% of time on manual documentation, component selection, compliance checking, and code generation. 35 engineers, ~Rs.39.5L/year lost.

**Goal:** Build "Silicon to Software (S2S)" - an AI-powered system that automates the hardware design lifecycle from natural language requirements to production-ready software drivers.

**Constraints:** Air-gapped capable, on-premise, defense-grade IP protection, 2+ month timeline, starting from scratch.

---

## 2. SYNOPSIS IMPROVEMENTS (For Higher Hackathon Score)

### What to Fix in the PDF

1. **Drop tool sprawl** - Remove references to GPT-4, Codellama, GLM-4, Pinecone, AntiGravity. Judges see indecision, not flexibility.
2. **Add architecture diagram** - A visual data flow diagram is mandatory for Product Design AI track.
3. **Fix the "LLM Model" row** - Currently contains Git/SonarQube info (copy-paste error in synopsis). Should list actual LLM models.
4. **Make timing claims realistic** - "4 minutes for Phases 1-4" needs breakdown per phase with caveats.
5. **Add a "How It Works" user journey** - Show the conversational flow: user types requirement -> AI asks questions -> AI generates outputs.
6. **Strengthen the "vs existing solutions" section** - Add comparison with Claude MCP ecosystem and AI agent frameworks (these are 2026 differentiators judges will know about).

---

## 3. DEFINITIVE TECHNOLOGY STACK

### Core Engine (Non-Negotiable)

| Layer | Tool | Why |
|---|---|---|
| **Primary LLM** | Claude API (Opus 4.6) | Best reasoning for complex hardware decisions, native tool_use |
| **Fast LLM** | Claude API (Haiku 4.5) | 10x cheaper for classification, template filling, compliance checks |
| **Air-Gap LLM** | Ollama + Qwen2.5-Coder-32B | Offline fallback, excellent code generation |
| **Vector DB** | ChromaDB (local) | Air-gapped, Python-native, zero infrastructure |
| **Embeddings** | text-embedding-3-large (online) / nomic-embed-text (offline) | Dual-mode for air-gap support |
| **Backend** | FastAPI (Python 3.12) | Async, fast, type-safe |
| **Database** | PostgreSQL 16 + SQLAlchemy | Production-grade, stores projects/components/BOM |
| **UI** | Streamlit | Ship fast, iterate fast, demo-ready in days |
| **Scraping** | Playwright | Best browser automation for manufacturer sites |

### LLM Fallback Strategy (Token Exceeded / Rate Limits)
```
Primary:  Claude Opus 4.6  (complex reasoning, orchestration, code gen)
    |
    +--> Fallback 1: Claude Haiku 4.5  (same API, same prompts, cheaper tokens)
            |
            +--> Fallback 2: Ollama + Qwen2.5-Coder-32B  (air-gapped, no token limits)
                    |
                    +--> Fallback 3: GLM-4  (last resort, different prompt format)
```

### Engineering & Generation Tools

| Tool | Purpose |
|---|---|
| **NetworkX** | Netlist graph generation, connectivity validation, cycle detection |
| **pandoc** | Markdown to .docx/.pdf conversion (MCP-compatible) |
| **OpenPyXL** | BOM spreadsheet generation (.xlsx) |
| **Jinja2** | Template engine for all document types |
| **tree-sitter + tree-sitter-c** | AST-based C/C++ code review (replaces SonarQube) |
| **NumPy/SciPy** | RF link budget, S-parameter calculations |
| **Mermaid.js** (in Streamlit) | Visual netlist/block diagrams in the UI |
| **GitPython** | Programmatic Git operations for version control |

### Document Output Strategy: Markdown-First
All documents are generated as `.md` files with embedded Mermaid diagrams. Conversion to `.docx` / `.pdf` is done on-demand via `pandoc` or MCP server.

```
AI Agent generates .md (source of truth)
    |
    +--> Rendered in Streamlit (live preview)
    +--> Converted to .docx via pandoc (for management)
    +--> Converted to .pdf via pandoc (for distribution)
    +--> Version controlled in Git (diffable)
```

### What's Explicitly NOT in the Stack
- ~~LangChain~~ -> Claude native tool_use is simpler and more reliable
- ~~n8n~~ -> Python agent orchestration is more flexible and debuggable
- ~~React/Material-UI~~ -> Streamlit for hackathon; React is post-hackathon
- ~~SonarQube/Semgrep~~ -> tree-sitter AST parsing is lightweight and air-gapped
- ~~Pinecone~~ -> Cloud-only, violates air-gap requirement
- ~~python-docx as primary~~ -> Markdown-first, convert via pandoc/MCP when needed
- ~~ReportLab~~ -> pandoc handles PDF conversion from markdown

---

## 4. SYSTEM ARCHITECTURE

```
                         STREAMLIT UI
                    (Chat + Dashboard + Viewers)
                              |
                              v
                    +---------+---------+
                    |   FASTAPI SERVER  |
                    |   /api/v1/...     |
                    +---------+---------+
                              |
                    +---------+---------+
                    | ORCHESTRATOR AGENT|  <-- Claude Opus 4.6
                    | (Master Controller)|     Fallback: Haiku -> Ollama -> GLM-4
                    +---------+---------+
                              |
     +---+---+---+---+---+---+---+---+---+
     |   |   |   |   |   |   |   |   |   |
     v   v   v   v   v   v   v   v   v   v
   [P1] [P2] [P3] [P4] [P5] [P6] [P7] [P8a][P8b][P8c]
   Req  HRS  Comp Net  PCB  GLR  FPGA SRS  SDD  Code
   Agent Agent Agent Agent (M) Agent (M) Agent Agent Agent
     |         |   |         |          |    |    |
     v         v   v         v          v    v    v
  ChromaDB  Rules NetworkX  Jinja2   Jinja2 Jinja2 tree-sitter
  Playwright Engine         pandoc   pandoc pandoc GitPython
```

### Agent Design Pattern

Each phase is an **autonomous agent** with:
- Its own system prompt with domain expertise
- Access to specific tools (not all tools)
- Input/output schema (structured Markdown + JSON metadata)
- Ability to ask clarifying questions back to user
- Persistent state in PostgreSQL

```python
# Core agent pattern (simplified)
class PhaseAgent:
    def __init__(self, phase_number, model, tools, system_prompt):
        self.phase = phase_number
        self.client = anthropic.Anthropic()
        self.model = model  # "claude-opus-4-6" or "claude-haiku-4-5-20251001"
        self.tools = tools
        self.system_prompt = system_prompt
        self.fallback_chain = ["claude-haiku-4-5-20251001", "ollama/qwen2.5-coder", "glm-4"]

    async def execute(self, project_context, user_input):
        try:
            response = self.client.messages.create(
                model=self.model,
                system=self.system_prompt,
                tools=self.tools,
                messages=[{"role": "user", "content": user_input}]
            )
            return self.process_response(response)
        except TokenLimitExceeded:
            return await self.execute_with_fallback(project_context, user_input)
```

---

## 5. PHASE-BY-PHASE IMPLEMENTATION PLAN

### Phase 1: Requirements Capture + Component Selection
**Agent:** RequirementsAgent (Claude Opus 4.6)
**What it does:**
- Conversational interface: user describes project in natural language
- AI asks clarifying questions (voltage? frequency? temperature range? quantity?)
- Extracts structured requirements
- Searches ChromaDB for matching components from cached datasheets
- Falls back to Playwright scraping if component not in cache
- Suggests 2-3 alternatives with trade-off analysis (cost vs performance vs availability)
- Generates system block diagram and architecture diagram using Mermaid syntax
- **Outputs:**
  - `requirements.md` - Structured requirements with all specs
  - `block_diagram.md` - System block diagram (Mermaid)
  - `architecture.md` - System architecture diagram (Mermaid)
  - `component_recommendations.md` - Component options with trade-offs

**Key files to create:**
- `agents/requirements_agent.py`
- `tools/component_search.py` (ChromaDB RAG)
- `tools/web_scraper.py` (Playwright for DigiKey/Mouser)
- `schemas/requirements.py` (Pydantic models)

### Phase 2: HRS Document Generation (IEEE 29148 Compliant)
**Agent:** DocumentAgent (Claude Haiku 4.5 for speed)
**What it does:**
- Takes requirements.md + component data
- Fills Jinja2 markdown templates following **IEEE 29148:2018** section structure
- Claude generates technical prose for descriptions, calculations, rationale
- Auto-calculates: power budgets, thermal analysis, timing diagrams
- Embeds Mermaid diagrams for block diagrams, timing, and data flow
- IEEE section numbering: 1. Introduction, 2. System Overview, 3. HW Requirements, 4. Design Constraints, 5. Verification, 6. Traceability Matrix
- Requirements tagged with IDs (REQ-HW-001, REQ-HW-002...) for traceability
- **Outputs:**
  - `HRS_[project_name].md` - IEEE 29148-compliant Hardware Requirements Specification
  - Convertible to `.docx` / `.pdf` via pandoc or MCP server on demand

**Key files:**
- `agents/document_agent.py`
- `templates/hrs_template.md` (IEEE 29148 structure in Jinja2 markdown)
- `generators/hrs_generator.py`
- `calculators/power_budget.py`, `calculators/rf_link_budget.py`

### Phase 3: Compliance Validation
**Agent:** ComplianceAgent (Claude Haiku 4.5)
**What it does:**
- Rules engine with compliance databases (RoHS substances, REACH SVHC list, FCC limits)
- Checks each selected component against all applicable standards
- Claude classifies edge cases where rules are ambiguous
- Generates compliance matrix with PASS/FAIL/REVIEW status
- **Outputs:**
  - `compliance_report.md` - Detailed compliance analysis
  - `compliance_matrix.xlsx` - Spreadsheet matrix for management

**Key files:**
- `agents/compliance_agent.py`
- `rules/rohs_rules.py`, `rules/reach_rules.py`, `rules/fcc_rules.py`
- `data/restricted_substances.json`

### Phase 4: Logical Netlist Generation (KEY INNOVATION)
**Agent:** NetlistAgent (Claude Opus 4.6 - needs complex reasoning)
**What it does:**
- Takes requirements + selected components
- Claude generates connectivity graph from component datasheets
- NetworkX builds and validates the netlist (cycle detection, DRC checks)
- Generates visual block diagram via Mermaid.js
- Validates pin compatibility, voltage levels, signal integrity
- **Outputs:**
  - `netlist.json` - Machine-readable netlist data
  - `netlist_visual.md` - Visual netlist with Mermaid diagram
  - `block_diagram.svg` - Exportable block diagram

**Key files:**
- `agents/netlist_agent.py`
- `generators/netlist_generator.py`
- `validators/netlist_validator.py` (NetworkX-based DRC)
- `visualizers/netlist_visualizer.py` (Mermaid.js output)

### Phase 5: PCB Layout (Manual - Future Scope)
- User downloads netlist and designs PCB in their EDA tool
- Future: KiCad Python API integration

### Phase 6: GLR Generation
**Agent:** GLRAgent (Claude Opus 4.6)
**What it does:**
- Takes netlist + requirements
- Generates Glue Logic Requirements with complete I/O specs
- Pin assignments, voltage levels, drive strengths, timing constraints
- Output bridges hardware design to FPGA implementation
- **Outputs:**
  - `glr_specification.md` - Full GLR document
  - `io_table.xlsx` - I/O pin assignment spreadsheet

**Key files:**
- `agents/glr_agent.py`
- `generators/glr_generator.py`
- `templates/glr_template.md`

### Phase 7: FPGA HDL (Manual - Future Scope)
- Engineer writes Verilog/VHDL using GLR as specification
- Future: Automated HDL generation from GLR

### Phase 8: Software Specification + Generation + Code Review

Phase 8 is split into **3 sequential sub-phases**, each with its own dedicated agent:

#### Phase 8a: SRS Generation (IEEE 830 / IEEE 29148 Compliant)
**Agent:** SRSAgent (Claude Opus 4.6)
**What it does:**
- Takes HRS + GLR + requirements as input
- Generates IEEE 830/29148-compliant Software Requirements Specification
- **IEEE Section Structure:**
  - 1. Introduction (Purpose, Scope, Definitions, References, Overview)
  - 2. Overall Description (Product perspective, functions, user characteristics, constraints)
  - 3. Specific Requirements (External interfaces, functional, performance, design constraints, quality attributes)
  - 4. Verification & Validation
  - 5. Appendices
- Maps hardware registers (from HRS) to software APIs
- Requirements tagged (REQ-SW-001, REQ-SW-002...) with traceability back to REQ-HW-xxx
- **Outputs:**
  - `SRS_[project_name].md` - IEEE 29148-compliant Software Requirements Specification

**Key files:**
- `agents/srs_agent.py`
- `generators/srs_generator.py`
- `templates/srs_template.md` (IEEE 830/29148 structure)

#### Phase 8b: SDD Generation (IEEE 1016 Compliant)
**Agent:** SDDAgent (Claude Opus 4.6)
**What it does:**
- Takes SRS as primary input (plus HRS + GLR for context)
- Generates IEEE 1016-2009-compliant Software Design Document
- **IEEE 1016 Design Viewpoints:**
  - Context viewpoint (system boundaries, external entities)
  - Composition viewpoint (modules, subsystems)
  - Logical viewpoint (classes, objects, relationships)
  - Dependency viewpoint (module dependencies)
  - Interface viewpoint (APIs, function signatures)
  - Interaction viewpoint (sequence diagrams in Mermaid)
  - State viewpoint (state machines in Mermaid)
  - Algorithm viewpoint (key algorithm descriptions)
- Class diagrams, sequence diagrams, state machines (all in Mermaid)
- Traceability: Each design element traces back to REQ-SW-xxx
- **Outputs:**
  - `SDD_[project_name].md` - IEEE 1016-compliant Software Design Document with Mermaid diagrams

**Key files:**
- `agents/sdd_agent.py`
- `generators/sdd_generator.py`
- `templates/sdd_template.md` (IEEE 1016 viewpoint structure)

#### Phase 8c: Code Generation + Review
**Agent:** CodeAgent (Claude Opus 4.6)
**What it does:**
- Takes SRS + SDD as blueprints for code generation
- Generates C/C++ device drivers following SDD architecture
- Generates Qt GUI application skeleton
- Generates test suites (unit + integration) based on SRS test requirements
- tree-sitter AST analysis for MISRA-C compliance checking
- Auto-generates Git commits with meaningful messages
- Code quality scoring (0-100)
- **Outputs:**
  - `drivers/` - C/C++ device driver source files
  - `gui/` - Qt GUI application skeleton
  - `tests/` - Unit and integration tests
  - `code_review_report.md` - Quality analysis with scores and recommendations

**Key files:**
- `agents/code_agent.py`
- `generators/driver_generator.py`
- `generators/gui_generator.py`
- `generators/test_generator.py`
- `reviewers/code_reviewer.py` (tree-sitter based)
- `reviewers/misra_rules.py`

---

## 6. PROJECT STRUCTURE

```
S2S_V2/
├── app.py                      # Streamlit main entry point
├── main.py                     # FastAPI server
├── config.py                   # All configuration (LLM keys, DB, fallbacks)
├── requirements.txt
├── docker-compose.yml          # PostgreSQL + ChromaDB + App
├── Dockerfile
├── .env.example
│
├── agents/                     # AI Agents (one per phase)
│   ├── base_agent.py           # Base agent class with Claude tool_use + fallback chain
│   ├── orchestrator.py         # Master agent that routes phases
│   ├── requirements_agent.py   # Phase 1
│   ├── document_agent.py       # Phase 2 (HRS)
│   ├── compliance_agent.py     # Phase 3
│   ├── netlist_agent.py        # Phase 4
│   ├── glr_agent.py            # Phase 6
│   ├── srs_agent.py            # Phase 8a (SRS)
│   ├── sdd_agent.py            # Phase 8b (SDD)
│   └── code_agent.py           # Phase 8c (Code Gen + Review)
│
├── tools/                      # Tools available to agents
│   ├── component_search.py     # ChromaDB RAG search
│   ├── web_scraper.py          # Playwright scraper
│   ├── calculator.py           # Engineering calculations
│   ├── doc_converter.py        # Markdown to docx/pdf via pandoc
│   └── git_manager.py          # GitPython operations
│
├── generators/                 # Document & code generators
│   ├── hrs_generator.py
│   ├── netlist_generator.py
│   ├── glr_generator.py
│   ├── srs_generator.py        # NEW: SRS generation
│   ├── sdd_generator.py        # NEW: SDD generation
│   ├── driver_generator.py
│   ├── gui_generator.py
│   └── test_generator.py
│
├── reviewers/                  # Code review engine
│   ├── code_reviewer.py        # tree-sitter AST analysis
│   └── misra_rules.py          # MISRA-C rule implementations
│
├── validators/                 # Validation engines
│   ├── netlist_validator.py    # NetworkX DRC
│   ├── compliance_checker.py   # Rules-based compliance
│   └── ieee_validator.py       # Validates IEEE section structure in generated docs
│
├── schemas/                    # Pydantic data models
│   ├── requirements.py
│   ├── component.py
│   ├── netlist.py
│   └── project.py
│
├── templates/                  # Jinja2 markdown templates
│   ├── hrs_template.md
│   ├── glr_template.md
│   ├── srs_template.md         # NEW
│   ├── sdd_template.md         # NEW
│   └── compliance_template.xlsx
│
├── rules/                      # Compliance rule databases
│   ├── rohs_rules.py
│   ├── reach_rules.py
│   └── fcc_rules.py
│
├── data/                       # Static data files
│   ├── restricted_substances.json
│   ├── component_cache/        # Cached datasheets
│   └── sample_projects/        # Demo project data
│
├── database/                   # Database setup
│   ├── models.py               # SQLAlchemy models
│   ├── migrations/
│   └── seed_data.py
│
├── pages/                      # Streamlit pages
│   ├── 1_New_Project.py
│   ├── 2_Component_Search.py
│   ├── 3_Documents.py          # HRS, SRS, SDD viewer + download
│   ├── 4_Netlist_Viewer.py
│   ├── 5_Code_Review.py
│   └── 6_Dashboard.py
│
├── tests/                      # Project tests
│   ├── test_agents/
│   ├── test_generators/
│   └── test_validators/
│
└── docs/                       # Internal documentation
    └── architecture.md
```

---

## 7. IMPLEMENTATION TIMELINE (8 Weeks)

### Week 1-2: Foundation
- [ ] Project scaffolding (folder structure, configs, Docker)
- [ ] FastAPI server with health check
- [ ] Streamlit UI shell with navigation
- [ ] PostgreSQL schema + SQLAlchemy models
- [ ] Base agent class with Claude API tool_use + fallback chain (Haiku -> Ollama -> GLM-4)
- [ ] ChromaDB setup with sample datasheet embeddings (100 components)
- [ ] pandoc integration for markdown -> docx/pdf conversion

### Week 3-4: Core Agents (Phase 1 + 2)
- [ ] Requirements Agent - conversational requirement extraction
- [ ] Component Search tool - ChromaDB RAG + Playwright scraper
- [ ] Document Agent - HRS generation as markdown with Mermaid diagrams
- [ ] Streamlit chat interface for Phase 1
- [ ] Document viewer/downloader for Phase 2 output (md + docx export)

### Week 5-6: Remaining Agents (Phase 3 + 4 + 6 + 8)
- [ ] Compliance Agent - rules engine + Claude classification
- [ ] Netlist Agent - NetworkX graph generation + Mermaid.js visualization
- [ ] GLR Agent - I/O specification generation
- [ ] SRS Agent (Phase 8a) - Software Requirements Specification from HRS+GLR
- [ ] SDD Agent (Phase 8b) - Software Design Document with Mermaid architecture diagrams
- [ ] Code Agent (Phase 8c) - driver generation + tree-sitter review
- [ ] Git integration with auto-commits

### Week 7: Integration + Polish
- [ ] Orchestrator agent connecting all phases end-to-end (P1->P2->P3->P4->P6->P8a->P8b->P8c)
- [ ] Streamlit dashboard with project tracking
- [ ] Air-gapped mode testing with Ollama
- [ ] Error handling, retry logic, loading states
- [ ] Cache 10K components in ChromaDB for demo reliability

### Week 8: Demo Preparation
- [ ] 3 demo scenarios: motor controller, RF system, digital controller
- [ ] Recorded backup video
- [ ] Performance optimization (parallel agent execution)
- [ ] Final synopsis document update
- [ ] Rehearsal runs

---

## 8. KILLER DIFFERENTIATOR FEATURES

### Must-Build (Hackathon Winners)
1. **Conversational Design** - Natural language chat that asks smart follow-up questions
2. **Pre-PCB Netlist** - Visual graph of connectivity BEFORE layout (your core innovation)
3. **IEEE-Compliant Documents** - HRS (IEEE 29148), SRS (IEEE 830), SDD (IEEE 1016) - audit-ready, not AI slop
4. **Full Software Lifecycle** - SRS -> SDD -> Code -> Review (no other hardware tool does this)
5. **End-to-End Traceability** - REQ-HW-001 traces through HRS -> SRS -> SDD -> Code -> Test
6. **Live Code Review** - Generated code with inline quality annotations

### Should-Build (If Time Permits)
6. **Specification Diff** - Change a requirement, see cascading impact across all outputs
7. **Component Risk Radar** - EOL/availability warnings on selected components
8. **Air-Gap Toggle** - Switch between cloud Claude and local Ollama with one button

---

## 9. VERIFICATION PLAN

### How to Test End-to-End
1. Start Docker services: `docker-compose up`
2. Open Streamlit: `http://localhost:8501`
3. Create new project, type: "Design a 3-phase BLDC motor controller, 48V bus, 10kW, FOC control"
4. Verify: AI asks clarifying questions about operating temperature, EMC requirements, target MCU
5. Verify: Component recommendations appear with 2-3 alternatives
6. Verify: Block diagram and architecture diagram render in Mermaid
7. Click "Generate HRS" -> view in Streamlit, download as .md or .docx
8. View compliance matrix with PASS/FAIL status
9. View netlist graph visualization (interactive Mermaid diagram)
10. Generate GLR document
11. Generate SRS (verify it maps HRS requirements to software functions)
12. Generate SDD (verify architecture diagrams, class diagrams in Mermaid)
13. Generate C drivers + review report (verify code follows SDD architecture)
14. Check Git repository for auto-commits
15. Toggle air-gap mode -> verify Ollama fallback works
16. Run `pytest tests/` -> all green

---

## 10. RISK MITIGATION

| Risk | Mitigation |
|---|---|
| Claude API rate limits during demo | Fallback chain: Haiku -> Ollama -> GLM-4; pre-cache common responses |
| Claude token limit exceeded | Haiku 4.5 as first fallback (same API, same prompts), then Ollama (no limits) |
| Playwright scraping blocked by sites | Pre-scrape 10K components; local cache |
| Demo scenario fails live | Recorded backup video ready |
| 50-page doc takes too long | Pre-generate skeleton; fill dynamically |
| Air-gap mode too slow on laptop | Use quantized Qwen2.5-7B for demo; 32B for production |
| Markdown rendering issues | Test all Mermaid diagrams in Streamlit and pandoc export beforehand |
