# Silicon to Software (S2S) V2 — Complete Technical Component Inventory
**Date:** 2026-04-07
**Purpose:** In-depth analysis of ALL components used (mentioned in PPTX or not)

---

## EXECUTIVE SUMMARY

| Metric | Count | Details |
|--------|-------|---------|
| **Phases** | 11 | P1, P2, P3, P4, P5, P6, P7, **P7a**, P8a, P8b, P8c |
| **AI Agents** | 9 | requirements, document, compliance, netlist, glr, **rdt_psq**, srs, sdd, code |
| **Manual Phases** | 2 | P5 (PCB Layout), P7 (FPGA Design) |
| **Document Outputs** | 44+ | Across all phases (not just 8!) |

---

## ALL COMPONENTS IN THE SYSTEM

### Backend (Python/FastAPI)

| Component | Purpose | Status | Used In |
|----------|---------|--------|---------|
| **FastAPI** | REST API server, auto-docs | ✅ Active | `main.py` |
| **Uvicorn** | ASGI server | ✅ Active | Auto-reload in dev |
| **SQLAlchemy** | ORM for SQLite | ✅ Active | `database/models/` |
| **Alembic** | Database migrations | ✅ Active | `alembic/` |
| **SQLite** | Embedded database | ✅ Active | `hardware_pipeline.db` (963KB) |
| **Pydantic** | Data validation | ✅ Active | Request/response models |
| **Anthropic SDK** | Claude API | ✅ Active | `anthropic>=0.42.0` |
| **OpenAI SDK** | OpenAI API (embeddings) | ✅ Active | `openai>=1.50.0` |
| **Z.AI / GLM** | Alternative LLM | ✅ Active | `glm-4.7`, Z.AI API |
| **DeepSeek-V3** | Primary LLM (configurable) | ✅ Available | `deepseek-chat` |
| **DeepSeek-R1** | Reasoning LLM | ✅ Available | `deepseek-reasoner` |
| **Ollama** | Air-gap local LLM | ✅ Available | `qwen2.5-coder:32b` |

---

### AI/ML Infrastructure

| Component | Purpose | Status | Details |
|----------|---------|--------|---------|
| **ChromaDB** | Vector DB for component datasheets | ✅ Implemented | `chromadb>=0.5.0` |
| | ComponentSearchTool | Semantic search on datasheets | `tools/component_search.py` |
| | Stores: component names, descriptions, specs | Collection: `component_datasheets` |
| | Embedding model: `text-embedding-3-large` (OpenAI) | Alternative: `nomic-embed-text` |
| | Fallback: DigiKey/Mouser API scraping | ✅ Active |
| **NetworkX** | Netlist graph DRC | ✅ Active | `netlist_agent.py` |
| | Nodes: components, Edges: connections | Graph analysis, isolated nodes, cycles |
| **Tree-sitter** | C/C++ AST parsing (P8c) | ✅ Active | MISRA-C rule checking |
| | `tree-sitter-c` | C language grammar | Parses C/C++ code for static analysis |
| **Lizard** | Cyclomatic complexity | ✅ Active | Code quality metrics |
| **Cppcheck** | Static analysis (MISRA-C) | ✅ Active | Binary tool, integrated in P8c |
| **CycloneDX** | SBOM generation | ✅ Active | `cyclonedx-bom>=4.0.0` |
| | Format: CycloneDX 1.4 JSON | Generated from BOM, importable to Dependency-Track |
| **OpenPyXL** | DOCX generation | ✅ Active | IEEE document export |
| **Jinja2** | Template engine | ✅ Active | Document generation |

---

### Git / CI/CD Components

| Component | Purpose | Status | Details |
|----------|---------|--------|---------|
| **GitPython** | Git operations | ✅ Active | `gitpython>=3.1.40` |
| | `git_agent.py` | Git automation | Commit, push, PR creation |
| **PyGithub** | GitHub API | ✅ Active | `PyGithub>=2.1.1` |
| | Creates GitHub PRs from analysis | Includes review summary |
| | **Git CI/CD Workflow** | GitHub Actions | ✅ Generated | `.github/workflows/hardware_pipeline_ci.yml` |
| | Validates with `actionlint` | Linting GitHub Actions workflows |
| | **Playwright** | UI testing | ✅ Active | `playwright>=1.48.0` |
| | Used for automated UI testing (E2E) | `tests/test_ui_playwright.py` |

---

### Document Generation System

| Component | Output | Format | Phase |
|----------|--------|--------|-------|
| **HRS Generator** | Hardware Requirements Spec | DOCX + PDF | P2 |
| **SRS Generator** | Software Requirements Spec | DOCX + PDF | P8a |
| **SDD Generator** | Software Design Document | DOCX + PDF | P8b |
| **Netlist Generator** | Netlist connectivity + DRC | .net + markdown | P4 |
| | Netlist visual | Mermaid diagram | KiCad import format |
| | DRC validation | NetworkX analysis | Errors: isolated nodes, cycles |
| **GLR Generator** | Glue Logic Requirements | DOCX + markdown | P6 |
| | Interface tables, pin mappings | From netlist analysis |
| **Driver Generator** | C/C++ device drivers | .c, .h files | P8c |
| | HAL layer, interrupts, DMA, error codes | MISRA-C:2012 compliant |
| **Qt GUI Generator** | Qt 5.14.2 GUI application | .py, .ui files | P8c |
| | MainWindow, DashboardPanel, ControlPanel | Complete app framework |
| | SerialWorker | Serial communication | `pyserial>=3.5` |
| **Code Reviewer** | MISRA-C analysis report | DOCX + JSON | P8c |
| | tool_report.md | LLM deep analysis + CWE classification | Maps to MISRA-C:2023 |

---

### Legacy / Alternative UI

| Component | Purpose | Status | Details |
|----------|---------|--------|---------|
| **Streamlit** | Legacy web UI (fallback) | ✅ Maintained | `streamlit>=1.40.0` |
| | `app.py` | Legacy single-page app | Runs on `localhost:8501` |
| | `streamlit-mermaid` | Mermaid diagram rendering | `>=0.2.0` |
| | **NOT actively used** — React v5 frontend replaced it | Marked as "legacy fallback" in docs |

---

### Web Scraping / External APIs

| Component | Purpose | Status | Details |
|----------|---------|--------|---------|
| **Playwright** | Web scraping | ✅ Available | `playwright>=1.48.0` |
| | Scrapes component datasheets | Used when ChromaDB has no match |
| | **NOT actively used** — Feature available for future |
| **DigiKey API** | Component search | ✅ Active | `api.digikey.com/v3` |
| | Client ID/Secret authentication | Used for component alternatives |
| **Mouser API** | Component search | ✅ Active | `api.mouser.com/api/v2` |
| | API Key authentication | Used for component alternatives |

---

### React Frontend (v5)

| Component | Library | Purpose | Status |
|----------|--------|---------|--------|
| **Three-panel layout** | Custom CSS Grid | ✅ Active | Left 248px + Center flex-1 + Right 340px |
| | **Radix UI** | UI components | ✅ Active | Accordion, Dialog, Progress, Slider, Tabs, Toast |
| | **Tailwind CSS** | Styling framework | ✅ Active | All UI styling |
| | **Vite** | Build tool | ✅ Active | Bundles to single HTML |
| | **React 19** | UI framework | ✅ Active | Client-side rendering |
| | **TypeScript** | Type safety | ✅ Active | All new code is TypeScript |
| | **Marked** | Markdown rendering | ✅ Active | `marked>=17.0.4` |
| | **Mermaid.js** | Diagram rendering | ✅ Active | CDN-based, async |
| | **Lucide React** | Icons | ✅ Active | `lucide-react>=0.577.0` |
| | **Sonner** | Toast notifications | ✅ Active | `sonner>=2.0.7` |
| | **React Resizable** | Panel resizing | ✅ Active | Drag panel borders |

---

### Data Storage

| Storage | Technology | Purpose | Details |
|--------|------------|---------|---------|
| **SQLite** | Primary database | ✅ Active | `hardware_pipeline.db` (963KB) |
| | Tables | projects, phase_outputs, component_cache, compliance_records |
| **ChromaDB** | Vector database | ✅ Available (optional) | `./chroma_data/` |
| | Stores | Component datasheet embeddings | |
| | Status | May fail on Windows (file descriptor limit) | Graceful degradation to API scraping |

---

### Developer Tools

| Tool | Purpose | Status |
|------|---------|--------|
| **Ruff** | Linting | ✅ Configured | Line-length: 120, Target: Python 3.12 |
| | **Black** | Code formatting | ✅ Available | Dev dependency |
| | **MyPy** | Type checking | ✅ Available | Dev dependency |
| | **pytest** | Testing | ✅ Available | Unit + integration + API tests |
| | **Coverage** | Code coverage | ✅ Configured | `pytest-cov` for HTML reports |

---

## NOT in Slides (But Implemented!)

### Major Omissions from PPTX:

| Item | What It Is | Why It Matters |
|------|-----------|----------------|
| **P7a (rdt_psq_agent.py)** | RDT + PSQ generation | Real implemented phase! |
| | **RDT** | Register Description Table | FPGA registers: address, bit-fields, access type, reset value |
| | **PSQ** | Programming Sequence | Ordered FPGA init: power-on → clock → peripherals |
| **ChromaDB** | Vector database for component search | Enables semantic datasheet queries |
| | **Embedding Model** | text-embedding-3-large | Powers component similarity search |
| | **Tree-sitter** | C/C++ AST parsing | Enables MISRA-C checking |
| | **Lizard + Cppcheck** | Static analysis | Real binaries, MISRA-C compliance checking |
| | **CycloneDX** | SBOM generation | Industry standard format (CycloneDX 1.4) |
| | **PySide6 + Qt** | Qt GUI code generation | Full Qt 5.14.2 app, not just stubs |
| | **Git Agent + PyGithub** | Git automation | Auto-commit + PR to GitHub |
| | **GitHub Actions** | CI/CD pipeline | Generated workflow YAML, validated with actionlint |
| | **Playwright** | E2E UI testing | Automated browser testing |
| | **OpenPyXL + Jinja2** | DOCX/PDF generation | IEEE documents with proper formatting |
| | **NetworkX** | Graph algorithms for netlist | Connectivity validation, path finding |

---

## Agent Files (9 Total)

| # | File | Phase | Tool/Framework |
|---|------|-------|---------------|
| 1 | `requirements_agent.py` | P1 | Anthropic SDK, ComponentSearchTool (ChromaDB) |
| 2 | `document_agent.py` | P2 | OpenPyXL, Jinja2, pypandoc (PDF) |
| 3 | `compliance_agent.py` | P3 | Rules engine, CycloneDX SBOM |
| 4 | `netlist_agent.py` | P4 | NetworkX, KiCad netlist format |
| 5 | `glr_agent.py` | P6 | GLR generator, netlist parsing |
| 6 | `rdt_psq_agent.py` | P7a | GLR parsing, register extraction |
| 7 | `srs_agent.py` | P8a | SRS generator, IEEE 830/29148 |
| 8 | `sdd_agent.py` | P8b | SDD generator, IEEE 1016 |
| 9 | `code_agent.py` | P8c | Tree-sitter, Cppcheck, Qt generator, Git agent |

---

## Phases (11 Total)

| # | Code | Phase | Type | Agent | Key Outputs |
|---|------|-------|------|------|-------------|
| P1 | `requirements_agent.py` | Design & Requirements | AI | requirements.md, block_diagram.md, architecture.md, BOM |
| P2 | `document_agent.py` | HRS Document | AI | HRS_{name}.md (DOCX + PDF) |
| P3 | `compliance_agent.py` | Compliance Check | AI | compliance_report.md, compliance_matrix.csv, sbom.json |
| P4 | `netlist_agent.py` | Netlist Generation | AI | netlist.json, netlist_visual.md, drc_report.md |
| P5 | - | PCB Layout | Manual | External (Altium/KiCad) |
| P6 | `glr_agent.py` | GLR Specification | AI | glr_specification.md (DOCX) |
| P7 | - | FPGA Design | Manual | External (Vivado/Quartus) |
| **P7a** | `rdt_psq_agent.py` | Register Map & Prog Seq | AI | rdt.md, psq.md |
| P8a | `srs_agent.py` | SRS Document | AI | SRS_{name}.md (DOCX + PDF) |
| P8b | `sdd_agent.py` | SDD Document | AI | SDD_{name}.md (DOCX + PDF) |
| P8c | `code_agent.py` | Code Gen + Review | AI | 17 Qt GUI files, drivers, C/C++ headers, CI/CD YAML |

---

## Missing from PPTX but in Code

### Should Be Mentioned:

1. **ChromaDB Vector Database**
   - Semantic component datasheet search
   - Embedding: `text-embedding-3-large`
   - Collection: `component_datasheets`

2. **Git CI/CD Pipeline**
   - GitHub Actions workflow: `.github/workflows/hardware_pipeline_ci.yml`
   - Validated with `actionlint`
   - Auto-commit + PR functionality

3. **Qt GUI Generation**
   - Full Qt 5.14.2 application (17 files generated)
   - PySide6 + Python serial communication
   - Dashboard, Control, Settings, Log panels

4. **CycloneDX SBOM**
   - Industry-standard SBOM format
   - Generated from component BOM automatically
   - Importable into Dependency-Track

5. **Tree-sitter Static Analysis**
   - C/C++ AST parsing
   - MISRA-C:2012 rule checking
   - CWE classification

6. **Playwright E2E Testing**
   - Automated browser testing
   - Tests: `tests/test_ui_playwright.py`

7. **Vector Database (Embeddings)**
   - OpenAI text-embedding-3-large
   - Used for semantic component search in P1

8. **NetworkX Graph Analysis**
   - Netlist DRC: isolated nodes, cycles
   - Connectivity validation between components

---

## Summary for PPTX Updates

The slides mention:
- **7 AI Agents** → Should be **9 AI Agents**
- **8 Phases** → Should be **11 Phases**
- **"Code Review"** → Should be **"Code Gen + Qt GUI + CI/CD → Git PR"**

Also missing:
- ChromaDB (vector DB for component search)
- Qt GUI (17 files generated)
- Git automation (PyGithub, git_agent)
- GitHub Actions CI/CD
- Tree-sitter + Cppcheck (static analysis)
- CycloneDX SBOM
- Playwright (testing)

---

*Generated from live codebase analysis 2026-04-07*
