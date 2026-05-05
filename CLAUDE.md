# Silicon to Software (S2S) V2 — Project Memory

## Current Status
- **FastAPI backend** running on `localhost:8000` — serves API + React frontend
- **React v5 frontend** built and deployed at `http://localhost:8000/app`
- **Streamlit** (`app.py`) kept as legacy fallback — not used actively
- Gold theme (Streamlit) tagged as `theme-gold`
- **11 pipeline phases** functional in backend (P1, P2, P3, P4, P5, P6, P7, P7a, P8a, P8b, P8c)
- **design_scope is advisory only (v23, 2026-04-20)** — scope is still a DB column and still steers the P1 wizard (architecture picker / spec questions), but `PHASE_APPLICABLE_SCOPES` now maps every phase to every scope, so every project runs all 11 phases. The execute-gate + pipeline-run gate code paths are still in place but never fire.

---

## Completed Work

### React Frontend Rebuild (v5 Design) — DONE ✅
- Built at `hardware-pipeline-v5-react/`, deployed as `frontend/bundle.html`
- Full three-column layout: LeftPanel (248px) + Center content + FlowPanel (300px right)
- All 10 phases in sidebar with lock logic, color coding, ✓ marks
- FlowPanel: animated sub-steps, progress bars, completion summary, Run button
- ChatView (P1): typewriter effect, no auto-greet, no QuickReply popup cards, history restored on F5
- CreateProjectModal: name only (no description field)
- Phase completion toasts on status flip (via `prevStatusesRef` in App.tsx)
- DocumentsView: DOCX download with "Converting…" loading state (async blob fetch)
- DocumentsView: inline Mermaid rendering via mermaid.js CDN

### Backend Fixes — DONE ✅
- All 7 AI agents scrub TBD/TBC/TBA from output
- `flag_modified` added to all JSON column writes (fixes P4 "Pending" status bug)
- Parallel Mermaid diagram rendering via `ThreadPoolExecutor` in `main.py`
- P4 netlist agent always completes (skeleton fallback if LLM doesn't call tool)

### V21 Deterministic Wizard (P1 Chat) — DONE ✅
- Pre-chat flow replaced with a 6-stage deterministic wizard (Scope → App → Arch → Specs → Details → Confirm)
- Data module: `src/data/rfArchitect.ts` — holds SCOPE_DESC, APPLICATIONS, ALL_ARCHITECTURES (scope-filtered + linear/detector split), ALL_SPECS, DEEP_DIVES, APP_QUESTIONS, AUTO_SUGGESTIONS, CASCADE_RULES, derivedMDS, archRationale
- Scope-first branching (`full` / `front-end` / `downconversion` / `dsp`) drives which architectures, specs, and deep-dive questions appear
- Per-project persistence: `localStorage["hp-v21-wizard-${projectId}"]` — survives F5 mid-flow, cleared on Generate
- Architect auto-suggestions: keyed on question-id × value; fire inline chips + confirm-stage notes
- Cascade sanity checks: Friis-derived MDS, gain stability, subsampling filter, freq-plan image, direct-RF clock, zero-IF, BW-vs-ADC Nyquist, radar/EW arch-fit
- "Other" free-text fallback on every chip row
- Structured payload stringified to the existing `/chat` endpoint — no backend changes
- `WizardFrame` component in `ChatView.tsx` owns the rendering; `ChatView` owns `wizardStage` + `wizard` state
- Build tag: `BUILD v21 (deterministic 7-stage wizard · architect intelligence + cascade sanity · scope-first branching)`

### V22 Backend-Authoritative Design Scope — DONE ✅
- `ProjectDB.design_scope` (String, default `'full'`) is the source of truth; SQLite migration `003_design_scope.sql` adds the column idempotently
- `POST /api/v1/projects` accepts `design_scope`; `PATCH /api/v1/projects/{id}/design-scope` updates it at any time
- `POST /phases/{id}/execute` calls `is_phase_applicable()` and returns **HTTP 409** if the phase is not applicable to the project's scope — frontend can no longer bypass the sidebar grey-out
- `run_pipeline()` skips out-of-scope phases (logs `pipeline.phase_skipped_out_of_scope`) so a full-pipeline click never executes an inapplicable phase
- `GET /api/v1/projects/{id}/status` returns `design_scope` + `applicable_phase_ids` (computed from `services/phase_scopes.PHASE_APPLICABLE_SCOPES`)
- Frontend (`App.tsx → refreshStatuses`) reconciles to the backend scope on every poll; localStorage is kept only as a transient cache that the backend always overrides
- `PhaseHeader` suppresses the Execute and Re-run buttons when `!isPhaseApplicable(phase, scope)` and shows a `NOT APPLICABLE` pill
- Build tag: `BUILD v22 (backend-authoritative design_scope · /status returns applicable_phase_ids · execute-gate 409 on out-of-scope phase · 11 phases)`

---

## Target Layout (v2 rebuild)

### Overall Structure

Two modes:

**1. Landing page** (no project loaded)
- Full-screen, v5 style: dark grid/checkerboard background, glowing teal orb
- Centered: Silicon to Software (S2S) logo + tagline
- Two buttons: `+ Create New Project` | `Load Existing`
- Subline: `DATA PATTERNS INDIA · GREAT AI HACK-A-THON 2026`

**2. Pipeline view** (project loaded)
Three-column layout, full viewport height:

```
+------------------+---------------------------+----------------------+
|  LEFT PANEL      |  CENTER CONTENT           |  RIGHT PANEL         |
|  248px fixed     |  flex-1 scrollable        |  340px fixed         |
|  sticky          |                           |  sticky              |
|                  |  Sticky mini-topbar:      |                      |
|  Logo / branding |  project name + prod ID   |  Step-by-Step        |
|                  |  phase progress dots      |  Execution Flow      |
|  Phase list:     |                           |                      |
|  P1 [teal]       |  Phase header:            |  Sub-steps for       |
|  P2 [blue]       |  icon + code + title      |  selected phase,     |
|  P3 [amber]      |  badge + tagline          |  animated on run,    |
|  P4 [purple]     |                           |  each with label,    |
|  P5 [slate/lock] |  Sub-tabs:                |  time, detail,       |
|  P6 [teal]       |  Chat | Details | Metrics |  progress bar        |
|  P7 [slate/lock] |  Documents                |                      |
|  P8 [teal]       |  (Chat only on P1)        |  Run button          |
|                  |                           |  (phase color)       |
+------------------+---------------------------+----------------------+
```

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│  REACT FRONTEND — frontend/bundle.html                             │
│  • Served by FastAPI at http://localhost:8000/app                  │
│  • All JS/CSS inlined — single self-contained HTML file            │
│  • Makes live HTTP calls to FastAPI at localhost:8000              │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ HTTP fetch() calls (same-origin)
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│  FASTAPI BACKEND — main.py on port 8000                            │
│  • Runs AI agents, reads SQLite DB, generates output files         │
│  • Swagger docs at http://localhost:8000/docs                      │
│  • Serves React bundle at GET /app                                 │
└────────────────────────────────────────────────────────────────────┘
```

---

## Design System (v5)

| Token            | Value                          |
|------------------|--------------------------------|
| Background       | `#070b14` (deep navy)          |
| Panel BG         | `#1a2235`                      |
| Panel Alt        | `#2a3a50`                      |
| Accent (primary) | `#00c6a7` (teal)               |
| Accent glow      | `rgba(0,198,167,0.25)`         |
| Text primary     | `#e2e8f0`                      |
| Text muted       | `#94a3b8`                      |
| Text dim         | `#64748b`                      |
| Border           | `rgba(42,58,80,0.8)`           |
| Error            | `#dc2626`                      |
| Warning          | `#f59e0b`                      |
| Blue accent      | `#3b82f6`                      |
| Display font     | **Syne** (Google Fonts)        |
| UI labels font   | **DM Mono** (Google Fonts)     |
| Code font        | **JetBrains Mono**             |
| Border radius    | 6px cards / 4px inputs         |
| Teal glow shadow | `0 0 28px rgba(0,198,167,0.25)`|

### Per-Phase Colors
Each phase has its own accent color used for icons, borders, progress, sub-step highlights:

| Phase | Color   | Hex       |
|-------|---------|-----------|
| P1    | Teal    | `#00c6a7` |
| P2    | Blue    | `#3b82f6` |
| P3    | Amber   | `#f59e0b` |
| P4    | Purple  | `#8b5cf6` |
| P5    | Slate   | `#475569` |
| P6    | Teal    | `#00c6a7` |
| P7    | Slate   | `#475569` |
| P8a   | Teal    | `#00c6a7` |
| P8b   | Blue    | `#3b82f6` |
| P8c   | Purple  | `#8b5cf6` |

CSS variables in `src/index.css`: `--navy`, `--panel`, `--panel2`, `--panel3`, `--teal`, `--teal-border`, `--teal-glow`, `--text`, `--text2`, `--text3`, `--text4`, `--border`, `--border2`, `--danger`, `--warning`, `--blue`, `--green`

---

## Views / Pages

### Left Panel (248px, sticky)
- Top: "DATA PATTERNS · CODE KNIGHTS" label (teal, small caps) + "Silicon to Software (S2S)" logo
- Phase list: one button per phase, full width, with:
  - Circle icon (phase number, or ✓ if complete, or lock if manual/locked)
  - Phase title + `⚡ AUTO` or `MANUAL` tag
  - Active phase highlighted with that phase's color border + tinted bg
  - Locked phases (P5, P7, and phases after current) shown at 32% opacity
  - Clicking a locked manual phase shows a toast: "Completed externally in Altium/Vivado"
  - Clicking a locked AI phase shows a toast: "Complete P{n-1} first"

### Center Content (flex-1)

**Sticky mini-topbar** (inside pipeline view, ~40px):
- Project name (left)
- Product ID chip in teal (if set)
- Phase progress dots right-aligned: one dot per phase, colored when complete

**Phase header** (below topbar):
- Large circle icon (phase color, number or ✓)
- Phase code (e.g. P01), AUTO/MANUAL badge, time estimate
- Phase title (Syne font, bold)
- Tagline (muted)
- Manual phase lock note if applicable

**Sub-tabs** (below header, tab bar):
- `⬡ Flow` — NOT here (moved to right panel)
- `⚡ Chat` — only for P1; same functionality as gold theme Streamlit chat, colors adapted to v5
- `◈ Details` — inputs list, outputs list, tools list
- `◎ Metrics` — TIME SAVED, ERROR REDUCTION, CONFIDENCE %, ANNUAL COST IMPACT
- `📄 Documents` — all generated outputs for this phase: .md/.docx inline renderer, Mermaid diagrams, JSON viewer, component table, netlist viewer, code review results (as applicable per phase)

Default tab: `◈ Details` (since Flow moved to right panel)

### Right Panel (340px, sticky)

**Step-by-Step Execution Flow** for the currently selected phase:
- Header: "Step-by-Step Execution Flow" (Syne bold) + sub-step count + total time
- Run button (phase color): `▶ Run Simulation` / `Running…` / `↺ Replay`
- Sub-steps list (vertical, connected by lines):
  - Each sub-step: circle (phase color when active/complete, dim when pending)
  - Connector line between steps
  - Step label + time chip
  - Detail text (12px, muted) shown when step is active/complete
  - Animated progress bar per step (phase color glow when active)
- Completed summary card: TOTAL TIME + SUB-STEPS count
- Animation: steps complete sequentially, each with fill bar animation ~22ms interval

This is **always visible** while you use the center sub-tabs.

---

## Phase Reference Data

| ID  | Name                  | Type     | Color   | Time      | Description |
|-----|-----------------------|----------|---------|-----------|-------------|
| P1  | Design & Requirements | AI       | teal    | ~4 min    | Block diagram + requirements capture via chat |
| P2  | HRS Document          | AI       | blue    | ~4 min    | IEEE 29148 Hardware Requirements Specification |
| P3  | Compliance Check      | AI       | amber   | ~4 min    | RoHS/REACH/FCC/MIL-STD rules engine |
| P4  | Netlist Generation    | AI       | purple  | ~4 min    | Component connectivity graph with DRC |
| P5  | PCB Layout            | Manual   | slate   | Days-Wks  | Altium/KiCad/OrCAD — manual, external tool |
| P6  | GLR Specification     | AI       | teal    | ~4 min    | Glue Logic Requirements for FPGA/CPLD |
| P7  | FPGA Design           | Manual   | slate   | Days-Wks  | Vivado/Quartus — manual, external tool |
| P7a | Register Map          | AI       | blue    | ~4 min    | Register map / memory layout for the FPGA / DSP |
| P8a | SRS Document          | AI       | teal    | ~4 min    | Software Requirements Specification |
| P8b | SDD Document          | AI       | blue    | ~4 min    | Software Design Document |
| P8c | Code Review           | AI       | purple  | ~4 min    | MISRA-C + Clang-Tidy static analysis |

### Sub-Steps Per Phase (for Right Panel Flow)

**P1 — Requirements & Component Selection** (~4 min)
1. Parse natural language input — 12s
2. Identify hardware domain — 5s
3. Query component database — 48s
4. Rank & select components — 20s
5. Generate BOM with alternates — 15s
6. Block diagram verification — 30s
7. Requirement finalization loop — 50s

**P2 — HRS Document Generation** (~4 min)
1. Load requirements from P1 — 3s
2. Select domain template — 5s
3. Calculate power budget — 18s
4. Generate interface tables — 22s
5. Write specification sections — 120s
6. Insert diagrams — 30s
7. Export .docx / .pdf — 12s

**P3 — Compliance Validation** (~4 min)
1. Load HRS + BOM from P1/P2 — 4s
2. RoHS / REACH substance check — 35s
3. EMC pre-compliance check — 45s
4. Safety standard mapping — 30s
5. Generate compliance matrix — 20s
6. Cost impact estimation — 15s
7. Compliance report export — 10s

**P4 — Logical Netlist Generation** (~4 min)
1. Parse block diagram from P1 — 8s
2. Map components to pinouts — 22s
3. Build connectivity graph — 30s
4. Assign net classes — 15s
5. Run electrical rules check — 35s
6. Export KiCad netlist (.net) — 8s
7. Pre-PCB DRC report — 10s

**P5 — PCB Layout** (Manual / External)
1. Import validated netlist (P4) — 5 min
2. Define layer stackup — 2 hrs
3. Component placement — 1-2 days
4. Route critical signals — 2-3 days
5. DRC / ERC check — 2 hrs
6. Gerber export — 30 min

**P6 — GLR Specification** (~4 min)
1. Load netlist from P4 — 5s
2. Identify FPGA/CPLD boundaries — 20s
3. Map glue logic requirements — 35s
4. Generate RTL constraints — 40s
5. Write GLR document — 80s
6. Export specification — 10s

**P7 — FPGA Design** (Manual / External)
1. Import GLR specification — 30 min
2. RTL coding (VHDL/Verilog) — 2-5 days
3. Simulation & verification — 1-2 days
4. Synthesis & place-and-route — 4 hrs
5. Timing closure — 2-4 hrs
6. Bitstream generation — 1 hr

**P7a — Register Map** (~4 min)
1. Load FPGA / DSP interface list from P6 / P7 — 5s
2. Assign base addresses per peripheral — 20s
3. Layout control / status / data registers — 60s
4. Encode bitfields + reset values — 45s
5. Generate Markdown register map — 25s
6. Export register map document — 10s

**P8a — SRS Document** (~4 min)
1. Load hardware spec from P1-P4 — 5s
2. Define software interfaces — 25s
3. Write functional requirements — 90s
4. Write non-functional requirements — 40s
5. Generate traceability matrix — 20s
6. Export SRS document — 10s

**P8b — SDD Document** (~4 min)
1. Load SRS from P8a — 5s
2. Design software architecture — 60s
3. Define module interfaces — 35s
4. Write design descriptions — 80s
5. Generate architecture diagrams — 25s
6. Export SDD document — 10s

**P8c — Code Review** (~4 min)
1. Load firmware source files — 8s
2. Run MISRA-C static analysis — 45s
3. Run Clang-Tidy checks — 40s
4. Classify issues by severity — 15s
5. Generate fix suggestions — 50s
6. Export review report — 10s

---

## API Integration

**Base URL:** `http://localhost:8000`
**API prefix:** `/api/v1/`
**Swagger docs:** `http://localhost:8000/docs`
**CORS:** Configured in FastAPI backend

| Method | Endpoint | Used For |
|--------|----------|----------|
| GET | `/api/v1/projects` | List all projects |
| POST | `/api/v1/projects` | Create new project |
| GET | `/api/v1/projects/{id}` | Project detail |
| GET | `/api/v1/projects/{id}/status` | All phase statuses — returns `{ phase_statuses: {...} }` |
| POST | `/api/v1/projects/{id}/pipeline/run` | Start full pipeline |
| POST | `/api/v1/projects/{id}/phases/{phase_id}/execute` | Run a single phase |
| POST | `/api/v1/projects/{id}/chat` | P1 design chat — returns full JSON (not a stream) |

**Create Project payload:** `{ name, description, design_type }` — do NOT include `product_id`, backend ignores/rejects it.

**Chat response:** Backend returns `{ response: "..." }` as a complete JSON object, not a streaming response. Display the full text at once.

**Status polling:** Phase status auto-refreshes every 3 seconds when a phase is running.

**Phase status values:** `pending`, `in_progress`, `completed`, `failed`, `draft_pending`

---

## Interactive Components

### Create Project Modal
- Fields: PROJECT NAME only, Design Type (RF / Digital) — description textarea REMOVED
- Subtitle: "Give your project a name — describe your design in the chat"
- NO product_id field — backend rejects it
- NO description field in UI (backend still accepts empty string for description)
- Actions: `Cancel` | `CREATE & START →`
- On submit: POST `/api/v1/projects` → load into pipeline view, select P1

### Load Project Modal
- Lists existing projects from GET `/api/v1/projects`
- On select: load project, auto-select first incomplete AI phase

### Phase Actions (in Right Panel)
- `▶ Run Simulation` — triggers `POST /api/v1/projects/{id}/phases/{phase_id}/execute`
- Running state: `Running…` (disabled button, phase color dimmed)
- `↺ Replay` — re-runs animation after completion
- Sub-steps animate sequentially as phase executes

### Chat (P1 — Center tab)
- POST `/api/v1/projects/{id}/chat` with `{ message }`
- Response is full JSON `{ response: "..." }` — display all at once
- Animated typewriter effect on the response text (like v5 HTML chat)
- NO QuickReply suggestion chip popups — user types answers freely
- NO auto-greet on load — user must send first message
- Message history restored on F5 via `api.getConversationHistory` in `handleLoadProject`
- Colors: teal accent (P1 color), dark panel background

---

## Frontend Source Structure

```
hardware-pipeline-v5-react/
├── index.html
├── vite.config.ts
├── tsconfig.app.json          # noUnusedLocals: false
├── src/
│   ├── main.tsx
│   ├── App.tsx                # mode switch: landing | pipeline
│   ├── index.css              # CSS vars + font imports
│   ├── api.ts                 # all fetch() calls
│   ├── types.ts               # Project, PhaseStatus, DesignScope, etc.
│   ├── data/
│   │   ├── phases.ts          # phase metadata + sub-steps (static data)
│   │   └── rfArchitect.ts     # v21 deterministic wizard data + helpers
│   ├── components/
│   │   ├── LandingPage.tsx    # full-screen landing
│   │   ├── LeftPanel.tsx      # phase list sidebar
│   │   ├── MiniTopbar.tsx     # sticky project name + progress dots
│   │   ├── PhaseHeader.tsx    # phase icon + title + badges
│   │   ├── FlowPanel.tsx      # right panel: sub-steps execution flow
│   │   ├── CreateProjectModal.tsx
│   │   ├── LoadProjectModal.tsx
│   │   └── Toast.tsx
│   └── views/
│       ├── ChatView.tsx       # P1 chat — v21 wizard (WizardFrame) + free-form chat
│       ├── DetailsView.tsx    # inputs / outputs / tools
│       ├── MetricsView.tsx    # 4 metric cards
│       └── DocumentsView.tsx  # all phase outputs consolidated
```

---

## Build & Deploy

**Build command:** `cd hardware-pipeline-v5-react && npx vite build`

### Bundle Script (run after build)
```python
# bundle_and_escape.py
import re, pathlib

dist = pathlib.Path("hardware-pipeline-v5-react/dist")
src_html = (dist / "index.html").read_text(encoding="utf-8")

for css_file in dist.glob("assets/*.css"):
    tag = f'<link rel="stylesheet" crossorigin href="/assets/{css_file.name}">'
    src_html = src_html.replace(tag, f"<style>{css_file.read_text('utf-8')}</style>")

for js_file in dist.glob("assets/*.js"):
    tag = f'<script type="module" crossorigin src="/assets/{js_file.name}"></script>'
    src_html = src_html.replace(tag, f'<script type="module">{js_file.read_text("utf-8")}</script>')

def escape_script(m):
    content = m.group(1)
    escaped = re.sub(r'[^\x00-\x7F]', lambda c: f'\\u{ord(c.group()):04X}', content)
    return f'<script type="module">{escaped}</script>'

src_html = re.sub(r'<script type="module">([\s\S]*?)</script>', escape_script, src_html)

out = pathlib.Path("frontend/bundle.html")
out.parent.mkdir(exist_ok=True)
out.write_text(src_html, encoding="ascii")
print(f"Bundle written: {out} ({out.stat().st_size // 1024} KB)")
```

### FastAPI /app Route
```python
@app.get("/app", response_class=HTMLResponse, tags=["ops"])
async def serve_frontend():
    import pathlib
    p = pathlib.Path(__file__).parent / "frontend" / "bundle.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8", errors="replace"), status_code=200)
    return HTMLResponse(content="<h1>Frontend not built yet.</h1>", status_code=404)
```

---

## Branding

- Logo: "Silicon to Software (S2S)" — "Pipeline" in teal `#00c6a7` (Syne font)
- Sub-brand: `DATA PATTERNS · CODE KNIGHTS` (teal, 10px, letter-spaced)
- Hackathon line: `DATA PATTERNS INDIA · GREAT AI HACK-A-THON 2026`
- Team credits: **NOT included**
- Logo icon: use ASCII `[lightning]` in source code to avoid encoding issues

---

## Backend

- **FastAPI**: `main.py` on `localhost:8000`
- **Streamlit** (legacy/fallback): `app.py` on `localhost:8501`
- **DB**: `hardware_pipeline.db` (SQLite)
- **AI Engine**: `agents/orchestrator.py`

---

## Git Tags

- `theme-gold` — Streamlit gold theme, all 8 phases working

---

## Known Issues / Bug Backlog

| # | Bug | Status | File(s) |
|---|-----|--------|---------|
| B1 | **Optional Requirements Card** — After AI asks questions in P1 chat, inject a final card: "ANY SPECIFIC REQUIREMENTS? (optional)" so user can add free-form constraints before generation | ✅ DONE | `ChatView.tsx` |
| B2 | **Power Budget Table Jumbled** — Split into two sub-tables: "5V & 3.3V Rails" and "2.5V & 1.8V Rails" | ✅ DONE | `requirements_agent.py` `_build_power_calc_md()` |
| B3 | **DOCX Download Broken** — Fixed `clickDownload()` (append anchor to body), fixed hardcoded cairosvg path in `main.py`, added inline error toast in UI | ✅ DONE | `DocumentsView.tsx`, `main.py` |
| B4 | **Elapsed Timer Resets on Phase Switch** — Elapsed state lifted out of `GeneratingState` into `DocumentsView` using `phaseStartTsRef` keyed by phase ID | ✅ DONE | `DocumentsView.tsx` |
| B5 | **Preview Slow** — Fixed stale closure in prefetch via `contentsRef`; Preview button shows ✓ when cached; loading spinner on in-flight fetch | ✅ DONE | `DocumentsView.tsx` |
| B6 | **Mermaid Parse Error in All Phases** — Both sanitizers completely rewritten: strips `%%` frontmatter, fixes `==>` arrows, removes `"` `#` `|` from labels, handles multi-line labels, aligns ChatView + DocumentsView sanitizers. System prompt updated | ✅ DONE | `ChatView.tsx`, `DocumentsView.tsx`, `requirements_agent.py` |
| B7 | **Bad Datasheet Links** — VPT manufacturer banned in system prompt + URL validator strips VPT URLs at build time. Agent instructed to use product-page URLs (not fabricated PDF paths) | ✅ DONE | `requirements_agent.py` |
| B8 | **GLR Missing RF Specs** — Tool schema extended with `input_return_loss_db`, `output_return_loss_db`, `harmonic_rejection`, `power_vs_frequency`, `power_vs_input`, `cable_loss`. All rendered conditionally when data present | ✅ DONE | `requirements_agent.py` |
| B9 | **HTTP 500 on first chat message** — `SYSTEM_PROMPT` contained `%%{{ init }}%%` escaped as `{ init }` causing `KeyError: ' init '` when `.format()` was called. Fixed by escaping as `%%{{init}}%%` | ✅ DONE | `requirements_agent.py` |
| B10 | **Optional card in wrong place / not shown** — `specificReqs` card was inside `QuickReplyPanel` (not rendered). Moved to `preStage === 'clarifying'` section with `clarifySpecificReqs` state. Removed pre-existing requirements field from `CreateProjectModal` | ✅ DONE | `ChatView.tsx`, `CreateProjectModal.tsx` |

---

## Planned Features (Roadmap)

### Tier 1 — High value / Low effort
| # | Feature | Status |
|---|---------|--------|
| 1 | **"Re-run all stale phases" button** in MiniTopbar — appears when `stalePhaseIds.length > 0`, one-click re-runs the full pipeline to refresh all outdated documents | TODO |
| 2 | **Chat history reload on F5** — reload `conversation_history` from DB on `handleLoadProject` so P1 chat isn't blank after browser refresh | ✅ DONE |
| 3 | **Phase completion toast** — when any phase flips to `completed` during polling, show "P02 — HRS Document complete ✓" toast | ✅ DONE |

### Tier 2 — Medium effort / High demo impact
| # | Feature | Status |
|---|---------|--------|
| 4 | **Inline Mermaid rendering** — detect ` ```mermaid ` blocks in DocumentsView markdown and render as live diagrams via mermaid.js CDN | TODO |
| 5 | **Export all as ZIP** — backend endpoint `GET /api/v1/projects/{id}/export` that zips the project output dir; frontend "Download All" button in DocumentsView | TODO |

### Tier 3 — Bigger features
| # | Feature | Status |
|---|---------|--------|
| 6 | **Requirement version history** — snapshot `requirements.md` on every P1 approval, store as `requirements_v1.md`, `v2.md`, etc.; viewable in a "History" drawer with diffs between versions | TODO |
| 7 | **Real-time log streaming** — stream AI internal reasoning (tool calls, search steps) to a collapsible "Live Log" panel in the right panel while phase is running | TODO |
| 8 | **Dependency graph view** — visual DAG: P1→P2→P3→P4→P5, P4→P6→P7, P1-P4→P8a→P8b→P8c; rendered as interactive SVG on the landing page or a dedicated "Pipeline Map" view | TODO |

---

## Critical Gotchas

**1. Windows cp1252 encoding crash**
bundle.html must have ALL non-ASCII chars escaped as `\uXXXX`. The bundle script handles this.

**2. Do NOT open bundle.html as file://**
`type="module"` inline scripts block via `file://`. Always access via `http://localhost:8000/app`.

**3. FastAPI runs on port 8000, not 8001**
API prefix is `/api/v1/` — check `http://localhost:8000/docs` for exact signatures.

**4. Chat is not streaming**
`/api/v1/projects/{id}/chat` returns complete JSON. Use typewriter animation client-side.

**5. No product_id in create project**
Backend `POST /api/v1/projects` only accepts `name`, `description`, `design_type`.

**6. Default exports required**
All component and view files must use `export default`. Named-only exports break bundling.

**7. uvicorn requires manual restart**
New routes in `main.py` need a manual server restart if not running with `--reload`.

**8. SQLAlchemy JSON column mutation tracking**
`phase_statuses`, `conversation_history`, `design_parameters` are `Column(JSON)`. SQLAlchemy does NOT auto-detect in-place or reassignment mutations for JSON columns. Always call `flag_modified(p, 'field_name')` after any assignment. Already done in `services/project_service.py`.

**9. TBD/TBC/TBA banned in all agent output**
All 7 agents scrub these words via `re.sub(r'\b(TBD|TBC|TBA)\b', '[specify]', ...)` before saving output. System prompts also explicitly forbid them. Server restart required for agent changes to take effect.

**10. Standards in use**
- HRS: ISO/IEC/IEEE 29148:2018
- SRS: IEEE 830-1998 / ISO/IEC/IEEE 29148:2018
- SDD: IEEE 1016-2009
- Compliance: RoHS EU 2011/65/EU, REACH, FCC Part 15, CE Marking, IEC 60601, ISO 26262, MIL-STD

**11. design_scope is backend-authoritative**
- `ProjectDB.design_scope` is the single source of truth; frontend localStorage is a transient cache that is overwritten on every `/status` poll via `App.tsx → refreshStatuses`
- `services/phase_scopes.PHASE_APPLICABLE_SCOPES` mirrors `src/data/phases.ts` `applicableScopes` — **keep these two maps in sync** when adding or retargeting a phase
- `POST /phases/{id}/execute` returns **HTTP 409 Conflict** with `{ detail: "Phase {id} is not applicable for this project's scope ({scope})" }` when violated; frontend displays a friendly toast in this case
- `run_pipeline()` skips out-of-scope phases silently (logs `pipeline.phase_skipped_out_of_scope`) — safe to click "Approve & Run" on any scope
- `GET /api/v1/projects/{id}/status` returns `design_scope` and `applicable_phase_ids: string[]` — prefer `api.getFullStatus()` over `getStatus`/`getStatusRaw` when scope info is needed
- Migration `003_design_scope.sql` is idempotent (column-exists check in `migrations/__init__.py::_apply_003`) — safe to re-run

---

## P1 Anti-Hallucination Design (4-Round Requirement Elicitation)

### Problem Statement
Demo feedback: "AI is hallucinating to the core." The P1 agent was assuming critical specs, fabricating component part numbers, and generating designs without proper requirement gathering.

### Solution: Strict 4-Round Elicitation Before Any Design Generation

#### Round 1 — Mandatory RF/Hardware Specifications (16 Tier-1 questions, always asked)

**Group A — RF Performance (core):**
1. Frequency range / band of operation
2. Bandwidth (instantaneous BW + tuning BW)
3. Target noise figure (NF) in dB
4. Gain requirements (total system gain in dB)
5. Sensitivity / MDS (minimum detectable signal in dBm)
6. Spurious-Free Dynamic Range (SFDR in dB)

**Group B — Linearity & Power Handling:**
7. Linearity (IIP3, P1dB in dBm)
8. Maximum input power / survivability (max safe input without damage + recovery time)
9. AGC requirement (range in dB, attack/release time)

**Group C — Selectivity & Rejection:**
10. Selectivity (adjacent channel rejection, alternate channel rejection in dBc)
11. Image rejection requirement (dB)
12. Spurious response rejection (dBc)
13. Input return loss / VSWR

**Group D — System Constraints:**
14. Power consumption budget (total W)
15. Supply voltage (available rails)
16. Output format (analog IF, digital I/Q, detected power, demodulated baseband)

**Group E — Application & Environment:**
- Application type (radar, comms, EW, SIGINT, satcom, T&M)
- Environmental (temperature range, vibration, altitude, IP rating)
- Physical constraints (form factor, size envelope, weight, cooling method)
- Compliance (MIL-STD, ITAR, RoHS, TEMPEST)

#### Round 1.5 — Application-Adaptive Questions (Tier 2, 3-6 questions based on application)

**If Military/EW/SIGINT:**
- Signal type (CW, pulsed, frequency-hopped, spread spectrum)
- Pulse handling (min pulse width, PRI range, POI, TOA accuracy)
- Direction finding / AOA requirement (angular accuracy, technique)
- Number of simultaneous channels
- BIT / self-test capability
- TEMPEST / emissions security
- Co-site interference environment
- Warm-up time requirement

**If Communications:**
- Modulation type (AM, FM, QAM, OFDM, etc.)
- Channel count (single tuned vs multi-channel)
- Tuning speed / switching time
- Frequency reference accuracy (ppm), TCXO vs OCXO
- Phase noise / blocking requirement (dBc/Hz at offset)

**If Radar:**
- Pulse handling (PW, PRI, duty cycle)
- Coherent processing required?
- Range resolution / Doppler requirements
- MTI / pulse compression

**If Satcom:**
- G/T requirement
- Link budget parameters
- Tracking requirement (auto-track, step-track, monopulse)

**Additional conditional questions:**
- Antenna interface (single/array, impedance, polarization, bias-tee)
- Frequency reference / clock (internal TCXO/OCXO, external ref, GPS-disciplined)
- Calibration / BIT requirement
- Redundancy / MTBF
- Data interface protocol (VITA 49, STANAG, Ethernet/UDP, PCIe, custom FPGA)
- Testability / production test points
- Power sequencing / inrush constraints

#### Round 2 — Architecture Selection (MANDATORY before component selection)

Present structured architecture options:

**Analog-Output Architectures:**
1. RF Front-End Only (LNA + Filter, no downconversion)
2. Superheterodyne Receiver (Single / Double / Multi-IF)
3. Direct Conversion (Zero-IF / Homodyne)
4. Low-IF Receiver
5. Image-Reject Receiver (Hartley / Weaver)
6. Analog IF Receiver (with analog demodulation)
7. Crystal Video Receiver (detector only, no LO)
8. Tuned RF (TRF) Receiver

**Digital-Output Architectures:**
9. SDR / Digital IF Receiver (RF → IF → ADC → DSP)
10. Direct RF Sampling Receiver (RF → ADC directly)
11. Subsampling / Undersampling Receiver
12. Dual-Conversion with Digital IF (analog front + digital backend)

**Specialized Architectures:**
13. Channelized Receiver (parallel filter bank, SIGINT/EW)
14. Compressive / Microscan Receiver (dispersive delay, radar warning)

15. "Not sure — recommend based on my specs"

**Adaptive Logic After Architecture Selection:**
- **Architecture has mixer** (superhet, direct conversion, image-reject, low-IF) → Ask: IF frequency, LO phase noise, tuning speed, step size, single vs multi-LO
- **Architecture has ADC at IF** (digital IF, dual-conversion+digital, low-IF digital) → Ask: ADC ENOB at IF, anti-alias filter, IF bandwidth, FPGA interface
- **Architecture has ADC at RF** (direct RF sampling, subsampling, SDR) → Ask: ADC sampling rate (≥2× RF), SFDR, aperture jitter, clock phase noise
- **Purely analog output** (RF front-end, analog IF, crystal video) → Ask: IF output specs (impedance, level, connector)
- **No mixer** (RF front-end, direct RF sampling, crystal video) → Skip LO questions
- **User selects "Not sure"** → AI recommends based on frequency, bandwidth, dynamic range, application

#### Round 3 — Architecture-Adaptive Follow-ups (3-5 questions)

Depends on Round 2 selection. Examples:
- Superhet → IF frequency choice, number of conversions, image rejection method, LO synthesizer specs, IF filter type (SAW, crystal, LC)
- Direct conversion → I/Q balance tolerance, DC offset handling, baseband filter bandwidth, flicker noise corner
- Digital IF / SDR → ADC resolution (bits), sample rate, FPGA family/size, DSP algorithm requirements, data throughput
- Direct RF sampling → ADC SFDR requirement, Nyquist zone, clock jitter budget (fs), anti-alias filter
- Channelized → number of channels, channel bandwidth, filter bank implementation (analog/digital/polyphase)

#### Round 4 — Requirement Validation & Cascade Analysis

Before generating any design:
1. Show complete requirements summary table (ALL specs from Rounds 1-3)
2. Show preliminary cascade analysis: "Based on your specs, system NF < 3 dB → LNA must have NF < 1.5 dB with gain > 15 dB (Friis). SFDR of 65 dB → mixer IIP3 > -5 dBm."
3. Flag any impossible/contradictory specs: "NF < 1 dB at 18 GHz is extremely challenging — confirm or relax?"
4. Ask: "Please confirm these requirements. I will NOT proceed until confirmed."
5. ONLY after explicit confirmation → call generate_requirements with REAL components

### Anti-Hallucination Rules
- NEVER assume missing specs — if not stated, ASK
- NEVER fabricate part numbers — if unsure, use manufacturer family + specs (e.g., "ADI HMC-series LNA, 2-18 GHz, 2 dB NF")
- NEVER skip architecture selection — it determines the entire signal chain
- NEVER proceed to component selection before all 4 rounds complete
- Every spec value must come from confirmed requirement or real datasheet
- All further questions MUST adapt based on chosen architecture
- Show cascade/link budget BEFORE generating BOM to catch impossible specs early

---

## Full Project Analysis (snapshot 2026-05-01)

This section is a top-to-bottom audit of the codebase as it stands today. It supersedes the older `Architecture` block where they conflict and exists primarily so future Claude sessions can orient quickly without re-reading 90K LoC.

### Codebase scale

- ~89,865 lines across 200+ Python + TypeScript files (excluding `node_modules`, `dist`, `__pycache__`, `output/`, `chroma_data/`, `.git`)
- Python backend: ~33,800 LoC across 11 agent modules, 16 services, 25 tools, 8 generators, 4 rule packs, 4 domain packs, 4 SQL migrations, 84 test files, 13 standalone scripts
- React frontend: ~15,300 LoC across 1 root (`App.tsx`, 764 lines), 5 views (largest is `ChatView.tsx` at 3,857 lines, then `DocumentsView.tsx` at 1,485 and `DashboardView.tsx` at 787), 17 components, 4 data modules, 2 utility modules, 3 vitest specs
- The single biggest file in the repo is `agents/requirements_agent.py` (8,619 lines — the P1 agent with all 6 RF domains, the 4-round elicitation, the tool schemas, and the post-generation audit pipeline)

### Top-level layout

```
AI_S2S_Code/
├── main.py                   # FastAPI app, 1830 lines, all HTTP routes
├── app.py                    # Legacy Streamlit UI (1877 lines, kept for fallback)
├── config.py                 # Settings singleton, .env loader, model fallback chain
├── observability.py          # OpenTelemetry no-op wrapper
├── logging_config.py         # Rich-styled stdlib logging
├── bundle_and_escape.py      # vite dist → frontend/bundle.html (ASCII-escaped)
├── push_to_github.py         # Push generated artifacts + create PR
├── update_pptx.py / update_pptx_safe.py  # Update demo deck from notes
├── test_mermaid_rendering.py # Diagnostic harness for mmdc / mermaid.ink / Node renderer
├── hardware_pipeline.db      # SQLite (with WAL/SHM siblings)
├── agents/         # 18 LLM-backed phase agents + critic + red-team + static-analysis
├── services/       # 16 services (project, pipeline, chat, rf_audit, glb_optimizer, locks, …)
├── tools/          # 25 callable tools (RF cascade, datasheet verify, distributors, mermaid, DRC, …)
├── generators/     # 8 deterministic doc/code generators (HRS, SRS, SDD, KiCad, drivers, code reviewer)
├── schemas/        # 4 pydantic models (project, requirements, component, netlist)
├── domains/        # 4 RF domains (radar, ew, satcom, communication) + standards.json + standards.py
├── rules/          # 4 compliance rule packs (rohs, reach, fcc, banned_parts)
├── validators/     # 2 validators (ieee, netlist)
├── migrations/     # 4 idempotent SQL files + __init__.py loader
├── database/       # SQLAlchemy ORM (sync + async engines)
├── tests/          # 84 pytest files (agents, api, services, tools, generators, utils, integration, root)
├── scripts/        # 13 demo / eval / golden / reproduce / seed / verify scripts
├── frontend/       # bundle.html (single-file deployable)
├── hardware-pipeline-v5-react/   # React source (vite + tailwind + shadcn-style)
├── data/, docs/, plans/, reference/, eval_results/, bugs/, output/, chroma_data/
└── .github/workflows/ci.yml      # 3-job CI (lint+test, react build, docker push)
```

### Backend (FastAPI) — full API surface

**Projects**
- `POST /api/v1/projects` — create; body `{name, description?, design_scope?, design_type?, project_type?}`
- `GET /api/v1/projects` — list
- `GET /api/v1/projects/{id}` — single project incl. conversation_history
- `PATCH /api/v1/projects/{id}/design-scope` — change scope
- `GET /api/v1/projects/{id}/documents` — flat + 1-level file listing
- `GET /api/v1/projects/{id}/documents/{filename:path}` — path-traversal-safe file read
- `GET /api/v1/projects/{id}/docx/{filename:path}` — markdown→DOCX with mermaid pre-render, pandoc-or-python-docx fallback, `.docx_cache/` reuse
- `GET /api/v1/projects/{id}/export` — ZIP all outputs
- `POST /api/v1/projects/{id}/reset-state` — Judge Mode wipe (statuses, conversation, parameters, locks)

**Phase 1 (chat / clarification)**
- `POST /api/v1/projects/{id}/clarify` — structured Q&A card payload
- `POST /api/v1/projects/{id}/chat` — sync `{response, draft_pending, phase_complete, outputs, model_used, clarification_cards?}` or async (HTTP 202 + `task_id`) when `async: true`
- `GET /api/v1/projects/{id}/chat/tasks/{task_id}` — async task poller

**Pipeline / phases (P2–P8c)**
- `POST /api/v1/projects/{id}/pipeline/run` — kick the DAG scheduler in a BackgroundTask
- `POST /api/v1/projects/{id}/phases/{phase_id}/execute` — single phase; **HTTP 409** if `design_scope` doesn't allow (gate currently a no-op since v23 maps every scope to every phase, but the code path still exists)
- `POST /api/v1/projects/{id}/phases/{phase_id}/cancel` — flip running phase back to `pending`
- `POST /api/v1/projects/{id}/phases/reset` — body `{phase_ids: [...]}`, resets and re-runs
- `GET /api/v1/projects/{id}/status` — primary poll endpoint; returns `{phase_statuses, requirements_hash, stale_phase_ids, applicable_phase_ids, audit_summary?, cascade_summary?, design_scope}`
- `POST /api/v1/projects/{id}/pipeline/rerun-stale` — re-run downstream of stale hash
- `GET /api/v1/projects/{id}/pipeline/rerun-plan` — dry-run preview `{stale, order, blocked_by_manual, status_summary, summary}`

**Settings**
- `GET /api/v1/settings/llm` — masked snapshot (keys redacted to last 4)
- `POST /api/v1/settings/llm` — persist updates back to `.env`

**Ops**
- `GET /health` — `{status, app, environment, air_gapped, timestamp}`
- `GET /login`, `POST /login` — optional password gate (HMAC-SHA256-signed cookie, only enabled when `APP_PASSWORD` is set)
- `GET /app` — serves `frontend/bundle.html`
- `GET /docs` — Swagger

### Service layer (services/)

| File | Role |
|------|------|
| `project_service.py` (668) | Single source of truth for project CRUD, phase status (sync + async), conversation appends with `flag_modified`, requirements lock persistence, stale-phase detection, `reset_state` |
| `pipeline_service.py` (616) | DAG-based scheduler — phases fire as soon as their own deps complete; status writes serialised in `_PHASE_FLIP_ORDER` with an 8s `_STATUS_FLIP_INTERLUDE_S` so the UI can render progress; per-project `asyncio.Lock` prevents lost-update race on `phase_statuses` JSON column |
| `chat_service.py` | P1 user-message orchestrator → `RequirementsAgent.execute()` → write outputs via storage → persist via async session |
| `phase_scopes.py` | `PHASE_APPLICABLE_SCOPES` map + `is_phase_applicable(phase_id, scope)`; **must stay in sync with `src/data/phases.ts`** (advisory since v23) |
| `phase_catalog.py` | Static phase metadata mirror of frontend `phases.ts` |
| `requirements_lock.py` | SHA-256 lock generation + load helpers |
| `stale_phases.py` | Compares per-phase `requirements_hash_at_completion` to current; flags drift |
| `p1_finalize.py` (362) | Runs red-team audit + cascade revalidation + citation + part audits before P1 lock |
| `rf_audit.py` (931) | Cascade + topology audit primitives consumed by `p1_finalize` and `red_team_audit` |
| `glb_optimizer.py` (1481) | Gain/Loss Budget optimizer (wideband cascade math, IF planning, ADC SFDR check) |
| `component_cache.py` (707) | ChromaDB seed + lookup; daemon-thread seed on startup |
| `elicitation_state.py` | Per-project Round-1/Round-2 progress tracker |
| `llm_logger.py`, `llm_logging.py` | Per-call LLM tracing (writes to `LlmCallDB`); currently sparsely used — opportunity to wire up |
| `project_reset.py` | Judge-mode wipe helper |
| `storage.py` | `StorageAdapter` — only file I/O entry point; `safe_project_dirname()` slugifies project names |

### Agents (agents/) and the LLM stack

**Agent base class (`base_agent.py`, 874 lines)**
- Fallback chain auto-built from `.env` keys: GLM-4.7 (Z.AI, Anthropic-compatible) → DeepSeek-V3 (only if `INCLUDE_DEEPSEEK_FALLBACK=true` or no GLM/Anthropic) → Anthropic Claude → Ollama local (only when no cloud key OR opt-in via `INCLUDE_OLLAMA_FALLBACK=true`)
- Override via `PRIMARY_MODEL`, `FAST_MODEL`, `FALLBACK_MODEL`, `LAST_RESORT_MODEL`
- Transient errors (429, 5xx, network) → exp-backoff retry on the **same** model 3× (5 / 10 / 20s) before falling through (P26 fix — prevents one 429 from exhausting the entire chain)
- Permanent errors (401, 402 "Insufficient Balance", 404 model-not-found) → skip model immediately
- OTel span per call via `_otel_tracer.start_as_current_span()`; auto-detects MITM CA in Cowork sandbox

**Phase → agent mapping**

| Phase | File | Notes |
|-------|------|-------|
| P1 (chat / requirements) | `requirements_agent.py` (8619) | 4-round elicitation; forced `show_clarification_cards` tool; banned-MPN + datasheet-URL + lifecycle audits; emits `requirements.md`, `block_diagram.md`, `architecture.md`, `component_recommendations.md`, `power_calculation.md`, `gain_loss_budget.md`, `cascade_analysis.json` |
| P2 (HRS) | `document_agent.py` (455) | IEEE 29148:2018 — uses `HRSGenerator`; fast model |
| P3 (compliance) | `compliance_agent.py` | RoHS + REACH + FCC + CE + MIL-STD reasoning; rules engine only really wired for RoHS today (REACH/FCC live in the prompt) |
| P4 (netlist) | `netlist_agent.py` (2828) | Forced `generate_netlist` tool; 30×20 grid sheets; DRC fallback |
| P6 (GLR) | `glr_agent.py` (733) | Two-pass: contract lock then section fill |
| P7 (FPGA) | `fpga_agent.py` (1329) | **Parallelised P26 #8** — 1 metadata call locks module/clock/ports, then 4 parallel content calls (top.v, tb.v, .xdc, report) reuse the locked metadata |
| P7a (RegMap) | `rdt_psq_agent.py` (633) | Register description (RDT) + power-sequence (PSQ) tables and mermaid flowcharts |
| P8a (SRS) | `srs_agent.py` (818) | IEEE 830 / 29148 Level-3, traceability matrix |
| P8b (SDD) | `sdd_agent.py` (1608) | **Parallelised P26 #16** — `lock_sdd_design` first, then 5 parallel section generators sharing the locked struct/enum names |
| P8c (code review + drivers + GUI) | `code_agent.py` (1308) | Calls `DriverGenerator`, `QtCppGuiGenerator`, `StaticAnalysisRunner`; Cppcheck + Lizard + cpplint with MISRA-C 2012 hint mapping; emits drivers, Qt GUI skeleton, `.github/workflows`, `git_summary.md` |

Supporting agents:
- `critic.py` (360) + `critic_agent.py` — deterministic regression diff between current output and golden run (cascade tolerance 0.5 dB, BOM order, citations); LLM critic is **off by default**
- `red_team_audit.py` (514) — topology + cascade + citation + parts audit; runs in `p1_finalize` before lock; structured `AuditReport`
- `static_analysis.py` (476) — Cppcheck + Lizard + cpplint runner (Lizard always available, others optional)
- `sbom_generator.py` — flagged dead in P3 ("SBOM removed from pipeline") but file still present
- `git_agent.py` (442) — local git ops + GitHub PR via PyGithub
- `qt_gui_generator.py` (531) and `qt_cpp_gui_generator.py` (1388) — two GUI generators, only the C++ one is wired into P8c; the Qt-Python one is legacy
- `orchestrator.py` — **dead code**: predates `PipelineService` and is no longer imported anywhere in the FastAPI flow

### Tools catalog (tools/)

RF math: `rf_cascade.py`, `cascade_validator.py`, `calculator.py`
Components: `component_search.py` (Chroma + LangChain), `datasheet_resolver.py`, `datasheet_url.py`, `datasheet_verify.py` (HEAD/GET + persistent SQLite cache), `parametric_search.py`, `seed_components.py`
Distributors: `digikey_api.py`, `mouser_api.py`, `distributor_search.py`
Netlist / PCB: `netlist_drc.py`, `pin_map.py`, `bom_linkage.py`, `block_diagram_validator.py`
Mermaid: `mermaid_coerce.py`, `mermaid_render.py`, `mermaid_salvage.py`
TX-side validators: `pa_thermal_validator.py`, `phase_noise_validator.py`, `acpr_mask_validator.py`
Misc: `doc_converter.py`, `web_scraper.py`, `git_manager.py`

### Generators (generators/)

`hrs_generator.py` (P2), `srs_generator.py` (P8a), `sdd_generator.py` (P8b), `glr_generator.py` (P6), `netlist_generator.py` + `kicad_netlist.py` (P4), `driver_generator.py` (P8c), `code_reviewer.py` (legacy LLM code review, optional import via `CODE_REVIEWER_AVAILABLE` flag).

### Database schema (SQLite, WAL mode, auto-relocates to `/tmp` if mount has stale WAL)

| Table | Notable columns |
|-------|-----------------|
| `projects` | id, name, description, design_type, **design_scope** (`full`/`front-end`/`downconversion`/`dsp`), **project_type** (`receiver`/`transmitter`), current_phase, **`phase_statuses` (JSON)**, **`conversation_history` (JSON)**, **`design_parameters` (JSON)**, output_dir, **`requirements_hash`**, **`requirements_frozen_at`**, **`requirements_locked_json`** |
| `phase_outputs` | Legacy timing/usage table written by `orchestrator.py` (no longer in active flow) |
| `pipeline_runs` | Reproducibility row per phase exec — `requirements_hash_at_run`, model, model_version, tokens, wall_clock_ms |
| `llm_calls` | Per-call trace — model, temperature, **`prompt_sha256` / `response_sha256`** (hash-only, no raw payload), tokens, latency_ms, `tool_calls_json` |
| `component_cache` | Cached part records used by Chroma seed |
| `compliance_records` | Audit results (project_id, part_number, standard, status) |

JSON columns that REQUIRE `flag_modified()` after assignment: `phase_statuses`, `conversation_history`, `design_parameters` on `ProjectDB`; `extra_data` on `PhaseOutputDB`; `key_specs`, `compliance` on `ComponentCacheDB`.

Migrations (idempotent, run via `migrations/__init__.py::apply_all` on engine init):
1. `001_requirements_lock.sql` — adds requirements lock columns
2. `002_pipeline_runs_llm_calls.sql` — creates pipeline_runs + llm_calls
3. `003_design_scope.sql` — adds design_scope (default `full`)
4. `004_project_type.sql` — adds project_type (default `receiver`)

### Domains and rules

`domains/_schema.py` defines pydantic models (`Part`, `Question`, `StandardClause`, `CascadeReport`, `AuditIssue`, `AuditReport`, `ScreeningClass` enum). `domains/standards.py` loads `standards.json` and exposes `find_clause()` + `validate_citations()` (used by red-team audit to catch hallucinated citations). Each of `radar/`, `ew/`, `satcom/`, `communication/` ships its own `prompts.py` + `questions.py` + per-domain components. `rules/` packs only RoHS as a real engine; REACH and FCC are prompt-only today (gap noted in audit).

### Frontend (hardware-pipeline-v5-react/)

**Component tree owned by `App.tsx` (764 lines)**

```
App
├─ DashboardView (when project === null)            ← P18 holographic landing, 787 lines
├─ MiniTopbar         (project info, Run / Re-run, DAG button, theme)
├─ LeftPanel          (phase list, lock indicators, stale badges)
├─ PhaseHeader        (status pill, Execute/Cancel, duration)
├─ ChatView           (P1 deterministic wizard + free-form chat, 3857 lines, the largest single file in the repo)
├─ DocumentsView      (file listing + md/json/csv/code viewer + inline mermaid + DOCX download, 1485)
├─ PipelineDagView    (modal, SVG dependency graph, toggled from MiniTopbar)
├─ JudgeMode          (Ctrl+Shift+J — verification overlay: hash, frozen_at, stale ids, audit pass/fail, NF / gain claimed-vs-computed)
├─ RerunPlanDrawer    (Ctrl+Shift+R — preview of stale-rerun plan)
├─ LLMSettingsModal   (API keys + model selection, persisted to .env)
├─ CreateProjectModal / LoadProjectModal
└─ ErrorBoundary + MermaidErrorBoundary
```

State that lives in `App.tsx`: `project`, `statuses`, `statusesRaw`, `mode`, `selectedPhaseIdx`, `tab`, `chatMessages`, `scope`, `stalePhaseIds`, `llmSettingsOpen`, `showDag`, plus refs (`pipelineStartedRef`, `autoAdvancedToRef`, `prevStatusesRef`, `prevP1StatusRef`).

**API client (`api.ts`, 302 lines)** — one method per backend route, plus `getStatus`/`getFullStatus`/`getStatusRaw` helpers (prefer `getFullStatus()` when scope info is needed) and a sync vs async chat split.

**P1 wizard state** (in `ChatView.tsx`, persisted to `localStorage["hw-pipeline-p1-wizard-${projectId}"]`):
```
{ stage: 0..4, designScope, projectType, applicationClass,
  deepDiveAnswers, appQAnswers, archAnswers, … }
```
Stages: 0 scope → 1 project type → 2 application class → 3 deep dives → 4 finalize / Approve & Run.

**LocalStorage keys** (all per-project except theme):
- `hw-pipeline-theme` (global)
- `hp-v20-scope-${id}` (cached scope; backend overrides on every poll)
- `hp-v21-wizard-${id}` and/or `hw-pipeline-p1-wizard-${id}` (wizard state — note the historical key drift between v20/v21/v22 generations of the wizard; both keys appear in code paths)
- Cleared on `Generate` / project reset

**Build pipeline**

```
hardware-pipeline-v5-react/   →  npx vite build (with vite-plugin-singlefile)
  → dist/index.html (CSS+JS already inlined)
  → bundle_and_escape.py (fallback inline + ASCII escape, including UTF-16 surrogate pairs for emoji/CJK)
  → frontend/bundle.html (ASCII-safe single-file)
  → served by FastAPI at GET /app
```

`vite.config.ts` defines `__BUILD_TIME_IST__` so the bundle stamps an IST build timestamp at compile time. `tsconfig.app.json` sets `noUnusedLocals: false` (intentional, see "Critical Gotchas").

**Files present but NOT mounted in `App.tsx` (effectively dead UI):**
- `views/DetailsView.tsx` (212 lines)
- `views/MetricsView.tsx` (195 lines)
- `components/LandingPage.tsx` (229 lines, replaced by DashboardView in P18)
- `components/FlowPanel.tsx` (346 lines, intentionally removed when right-panel was retired in favour of the DAG modal)

These are kept around as rollback fallbacks but should be flagged in any future cleanup PR.

### Tests, eval harness, and CI

- 84 Python test files: `tests/agents` (17), `tests/tools` (18), `tests/services` (9), `tests/api` (2), `tests/generators` (1), `tests/utils` (1), `tests/integration` (1, network-gated, marked `slow`), root `tests/` (35)
- 30 golden YAML scenarios across the 4 domains (`tests/golden/`)
- 3 vitest specs in the React app (`api.test.ts`, `rfArchitect.test.ts`, `mermaidSanitize.test.ts`)
- `conftest.py` ships ~54 fixtures; ~91 async tests; ~31 parametrized tests
- pyproject sets `filterwarnings=["error", ...]` (warnings promote to failures, with two pragmatic ignores for asyncio teardown noise)
- Eval harness (Makefile + `scripts/`):
  - `make baseline` → `run_baseline_eval.py` (30 scenarios × cascade + citations + red-team, no LLM, no network)
  - `make ablation` → `run_ablation_matrix.py` (4 configs × 30 scenarios × 3 mutation sets = 360 runs)
  - `make golden` → `pytest tests/test_golden.py`
  - `make reproduce` → `reproduce_run.py` (replay deterministic parts from a logged run; not an LLM replay)
  - `make eval` / `make full-eval` → `run_full_eval.py` (master pre-demo gate)
  - `make datasheets` / `make datasheets-offline` → `verify_datasheets.py`
- CI (`.github/workflows/ci.yml`): three jobs (lint+pytest excl. integration, vite build + bundle, GHCR docker push on `main`); Python 3.12, Node 20, ruff configured to ignore `app.py` and `E501`

### Curated component spec library (2026-05-02)

`data/component_specs/*.json` is the highest-priority resolution path in `services/component_spec_resolver.py`. Hand-authored specs ship with `source: curated` + `confidence: 1.0`, bypassing the LLM extractor entirely. As of 2026-05-03 the library covers **70 high-frequency parts** across the four domains (v23.4 added Si5341 jitter-clean clock gen, Si570 VCXO, AD7124-8 24-bit Σ-Δ ADC, MAX31790 fan controller, TPS386040 supervisor):

- **PLLs / synthesizers / clock distribution (10)**: ADF4351, ADF4159, ADF5610, LMX2592, LMX2594, HMC1063, HMC1190, HMC7044, AD9528, LMK04828
- **JESD204B/C converters + transceivers (6)**: AD9082, AD9162, AD9208, AD9371, AD9625, ADRV9009
- **DDS / DAC (3)**: AD9914, MCP4725, AD7193
- **RF mixers / upconverters / downconverters (3)**: ADMV1013, ADMV1014, LTC5594
- **RF DSAs / switches / VGAs / filters (4)**: ADRF5510, ADRF5720, AD8367, ADRF6510
- **NOR Flash (6)**: M25P16, N25Q128, N25Q256, S25FL128S, W25Q64JV, W25Q128JV
- **I2C EEPROM (4)**: 24LC256, AT24C64D, AT24C256C, AT24C512C
- **I2C peripherals (8)**: ADS1115, DS1672, INA226, LTC2945, MCP23017, PCA9555, TCA9548A, TMP102
- **USB-UART bridges (3)**: CP2102, FT232H, FT2232H
- **RS-232 / RS-485 (3)**: MAX232, MAX3485, SN65HVD485

**Demo metric**: across the 4-domain test suite, ~60% of BOM parts now hit the curated layer (was ~30%), so the LLM extractor is skipped for the majority of resolutions. Net effect on P1 runtime is ~2 minutes saved on a 10-part BOM (each curated hit replaces an 8-30s LLM extraction call). Curated values flow through to RTL: e.g. flash controller for W25Q128JV emits real opcodes (0x02 page-program, 0x06 write-enable), EEPROM driver for AT24C256C emits real slave address (0x50), PLL sequencer for ADF4351 references the part by name.

**Adding a new curated spec**: drop a new JSON in `data/component_specs/` matching `schemas/component_spec.py::ComponentSpec`. The resolver picks it up on next process restart (LRU cache).

### Datasheet PDF diff detection (2026-05-02)

When `services/datasheet_extractor.fetch_datasheet_text()` fetches a PDF for a known MPN, it now SHA-256s the raw bytes and compares to the previous hash stored in `data/component_specs/_pdf_hashes.json`. On change:

1. The cached extracted spec at `data/component_specs/_extracted/<MPN>.json` is invalidated (forces re-extraction next time)
2. A diff event is appended to `data/component_specs/_diff_review_queue.jsonl` with `old_sha256`, `new_sha256`, URL, and timestamp
3. The next call to `extract_from_url(url, mpn)` re-runs the LLM extractor against the revised PDF

API:

- `services.datasheet_extractor.get_pdf_hash(mpn)` -> stored hash or None
- `services.datasheet_extractor.list_diff_review_queue()` -> all unread diff events
- Diff detection is automatic when `mpn` is passed to `fetch_datasheet_text(url, mpn=mpn)` (the `extract_from_url` helper does this)

This catches manufacturer datasheet revisions silently - operator can run `make review-specs` to surface both low-confidence specs AND drifted PDFs in one queue.

### Second-pass extracted-spec validation (2026-05-02)

After the LLM extractor produces a spec from a fresh datasheet, a second LLM call (`services.datasheet_extractor.validate_spec_against_pdf`) sends the JSON + the original PDF text back and asks "what claims contradict the source?". The response shape is:

```json
{"contradictions": [
   {"field": "i2c_slave_addr_7bit", "claimed": 80,
    "datasheet_says": 104, "evidence": "Section 5.1: addr=0x68"}
]}
```

Behavior on each outcome:

- **Curated specs**: short-circuited, no second-pass call (they ARE the source of truth).
- **No contradictions**: confidence bumps by +0.05 (cap 1.0); positive note added.
- **Contradictions found**: confidence drops by 0.2 per issue; first 5 contradictions summarized into a `notes[]` entry. If confidence falls below the 0.85 review threshold the spec is auto-enqueued by `enqueue_for_review`.
- **Validator unavailable** (LLM call failed / parse failed): spec untouched, debug log only.

This catches the silent failure mode where pdfplumber misreads a multi-row table cell and the first LLM extractor confidently emits the wrong value. Cost: one extra LLM call per *uncached* LLM-extracted spec (curated, family-inferred, and generic-fallback paths skip it). Wired into `extract_from_url` between extraction and distributor enrichment.

### SystemVerilog functional coverage emitter (2026-05-02)

`agents/rtl_coverage.py` derives a SystemVerilog covergroup file from the project's register map + FSM list and ships it alongside `fpga_top.v` and `fpga_testbench.v` as `rtl/fpga_coverage.sv`.

What gets covered:

- **Per-register address** — one bin per documented register address, crossed with read-vs-write
- **Access type** — global read vs write event counts
- **Reset value** — on each access, samples whether the read value matches the documented reset
- **Per-FSM state** — for each FSM declared by P6/P7, one bin per state

The testbench instantiates `fpga_coverage = new();` once on bring-up, then calls `sample_register_access(addr, is_write, data, reset)` and `sample_<fsm>_state(state)` from inside the existing R/W vector loop. End-of-sim calls `report()` to print per-covergroup percentages.

Always emits valid SV — if the brief has no registers or FSMs we emit stub covergroups so testbench compilation is stable. Tested against Vivado xsim 2023.x and Verilator 5.x. No UVM dependency (drop-in for any Vivado project).

Wired into `agents/rtl_tailored.py::render_verilog` so every P7 run produces the file. Validation marker count covered by `smoke_test_curated.py` check #6.

### Anti-hallucination layers (six concentric)

1. **System prompts** — per-stage forbidden topic regex (`_FORBIDDEN_ROUND1_RE`), pre-parse user input, post-emit scrubbing (`_filter_forbidden_round1`)
2. **Component checks** — `rules/banned_parts.py` (frozen list incl. VPT Inc., obsolete HMC parts), MPN shape validator, candidate-pool whitelist (each part must exist in `domains/<dom>/components.json` OR have a HEAD/GET-verified datasheet URL), lifecycle gate (must be `active`)
3. **Forced tool calls** — P1 `show_clarification_cards`, P4 `generate_netlist`, P7 `generate_fpga_design`, P8b `lock_sdd_design`; strict JSON schema validated before LLM sees results
4. **Post-generation audit** — `services/p1_finalize.py` runs `agents/red_team_audit.py` (topology + cascade Friis recompute with 0.5 dB tolerance + citation existence + part-number whitelist); P1 won't lock until `audit.overall_pass`
5. **Placeholder scrub** — `re.sub(r'\b(TBD|TBC|TBA)\b', '[specify]', …)` in every agent
6. **Deterministic critic** — `agents/critic.py` diffs current run vs golden (no LLM call required); LLM-backed `critic_agent.py` is off by default

### Bugs, dead code, and rough edges identified by this audit

- **Phase status polymorphism** — `phase_statuses[phase_id]` is sometimes a string (old DB rows: `"completed"`) and sometimes a dict (new: `{status, completed_at, requirements_hash_at_completion}`). `chat_service.async_append_conversation` already does `isinstance(_p1_val, dict)` to handle both, but a one-shot migration would simplify. **Risk:** fragile, easy to introduce a regression that only fires on an old DB.
- **`agents/orchestrator.py` is dead code** — predates `PipelineService` and is no longer imported by `main.py`. Safe to delete after a search confirms zero references.
- **`agents/sbom_generator.py` is dead code** — comment in `compliance_agent.py` states "SBOM removed from pipeline".
- **`agents/qt_gui_generator.py` is superseded** by `qt_cpp_gui_generator.py`; only the C++ one is invoked by `code_agent.py`.
- **`agents/critic_agent.py` (LLM critic)** — second LLM call, off by default. Code path exists but no UI to toggle it on; either wire a setting or document it as opt-in via env.
- **Frontend dead views** — `DetailsView.tsx`, `MetricsView.tsx`, `LandingPage.tsx`, `FlowPanel.tsx` are imported nowhere in `App.tsx`.
- **Unused import in `App.tsx`** — `FlowPanel` is imported but the right panel was intentionally removed.
- **Console.log debris** — ~10 `console.log` lines in `ChatView.tsx` (clarification-card parsing) and 2 in `App.tsx` (pipeline start). Harmless but should be stripped before a public demo build.
- **`design_scope` is advisory** — since v23 every phase is mapped to every scope in `PHASE_APPLICABLE_SCOPES`. The 409-on-out-of-scope code path still exists in `main.py` but never fires. Keep them in sync if the gate is ever re-armed.
- **REACH and FCC compliance are prompt-only** — no rules engine like `rules/rohs_rules.py`. `compliance_agent.py` does free-form LLM reasoning for those two. Opportunity: port the prompt heuristics into deterministic rule packs.
- **Cascade validator silent failures** — if a BOM stage is missing `gain_db` / `nf_db`, cascade math returns `None` without warning; the audit logs INFO not CRITICAL.
- **`LlmCallDB` table sparsely populated** — table exists, columns are right, but most agent calls don't write to it. Plumbing `services/llm_logger.py` through `base_agent.call_llm` would unlock the reproducibility demo described in `MASTER_PLAN.md` (D2.2).
- **Distributor rate-limit headers ignored** — DigiKey/Mouser return rate hints; we don't parse them, so the user just sees "distributor lookup failed".
- **`requirements_locked_json` is write-only today** — saved on lock but never read for diffing/audit. Low-effort win for reproducibility.
- **Async session cleanup** — `get_async_session()` factory works, but explicit `AsyncSession.close()` is sometimes implicit. Under sustained load this could leak connections.
- **Reset-state is aggressive** — wipes conversation_history and design_parameters too. Add a `reset_phases_only=true` mode if users want to keep context.
- **Wizard localStorage key drift** — both `hp-v21-wizard-${id}` and `hw-pipeline-p1-wizard-${id}` appear in code; pick one canonical key in the next refactor.
- **Mermaid render fallback chain (mermaid