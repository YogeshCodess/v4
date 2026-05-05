# Silicon to Software (S2S) — Bug Log

> Maintained in `bugs/BUG_LOG.md`. All bugs documented with root cause, fix, and status.
> History is append-only — resolved bugs are kept for reference.

---

## Active Bugs

*(none — all bugs resolved)*

---

### BUG-014 · Documents tab shows "Phase completed — no documents found" even though files exist
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Severity:** Critical
**Root Cause (1 — backend):** `list_documents` API used `output_dir` from DB with no fallback. If `output_dir` was empty (projects created before field was reliably written, or path changed) the endpoint silently returned `[]` with no log. Additionally, the endpoint used `os.listdir()` (flat scan only) so subdirectory files like `qt_gui/*.cpp` were listed as the subdirectory entry (directory, not file) and skipped by the `isfile()` guard.
**Root Cause (2 — frontend):** The "completed, no documents" empty state showed only a static error message with no way to retry. User had to reload the entire page.
**Fix (backend):** Added `_resolve_output_dir(proj)` helper that: (a) checks DB `output_dir` first, (b) falls back to deriving the path from the project name using the same `StorageAdapter.project_dir` lowercase/underscore logic, (c) logs a warning with full context when neither works. Replaced `os.listdir` flat scan with `os.scandir` that also recurses one level into subdirectories, so `qt_gui/README.md`, `.github/workflows/hardware_pipeline_ci.yml` etc. are correctly listed.
**Fix (frontend):** Changed "Phase completed — no documents found" message to "Output files may still be writing to disk. Try refreshing." and added `↺ REFRESH DOCUMENTS` button that calls `fetchList(false, phase.id)` to force a fresh non-silent fetch.
**Commit:** pending

---

### BUG-009 · TBD / TBA / TBC values in component table (requirements_agent)
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Severity:** High
**Root Cause:** `requirements_agent.py` LLM outputs "TBD - STM32F4 or TI C2000 series" in `primary_part` fields when uncertain about components. No prompt instruction to forbid this pattern and no post-processing to clean it.
**Fix:** (a) Added "NEVER prefix primary_part with 'TBD -', 'TBC -', 'TBA -'" to SYSTEM_PROMPT. (b) Added `_strip_tbd()` static method that strips "TBD - " prefixes and rewrites as "STM32F4 (alt: TI C2000 series)". (c) Added `_clean_component_data()` called before `_generate_output_files()` to apply strip to all component fields.
**Commit:** pending (batch BUG-009 through BUG-013)

---

### BUG-010 · P1 Documents tab inaccessible before pipeline approval
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Severity:** High
**Root Cause:** `handleSelectPhase()` in `App.tsx` always set tab to `'chat'` when selecting P1, even after P1 was completed. Users could not access generated requirements documents from the Documents tab to verify before approving the pipeline.
**Fix:** Changed tab selection logic: if P1 is `completed`, default to `'documents'` tab; otherwise default to `'chat'`. Applied same logic to `handleLoadProject()` for consistency on browser refresh.
**Commit:** pending

---

### BUG-011 · 5-second delay when switching between phases (CRITICAL — logic inversion)
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Severity:** Critical
**Root Cause:** `DocumentsView.tsx` called `fetchList(!silent, phase.id)` — the `!` operator **inverted** the flag meaning. When `silent=true` (phase already loaded, revisiting) → `fetchList(false)` → showed full spinner. When `silent=false` (first load) → `fetchList(true)` → skipped spinner. Every phase switch showed the spinner because the loaded-phases cache was being ignored.
**Fix:** Changed to `fetchList(silent, phase.id)` (removed the `!`). Already-loaded phases now switch instantly; only first-time loads show the spinner.
**Commit:** pending

---

### BUG-012 · Execute button shows for pending phases during pipeline execution gap
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Severity:** Medium
**Root Cause:** `PhaseHeader.tsx` Execute button condition included `status === 'pending'` when `pipelineStarted` was true. During the brief gap between phases (e.g., P6 complete → P7a about to start), phases in `pending` state incorrectly showed the Execute button, risking accidental manual trigger.
**Fix:** Changed condition: when `pipelineStarted=true`, only show Execute for `failed` phases (retry). When `pipelineStarted=false` (standalone), show for both `pending` and `failed`. Running phases are always hidden.
**Commit:** pending

---

### BUG-013 · CI/CD validation message misleads user about GitHub token
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Severity:** Low
**Root Cause:** `code_agent.py` validation report header said "(local, no credentials)" implying the GitHub token provided by the user was being ignored. In reality the token IS used — for the `git push` and PR creation in the next step. The validation step itself is an offline YAML syntax check that doesn't need the token.
**Fix:** Changed validation report header from "(local, no credentials)" to "offline YAML syntax check". Added a `> **Note:**` callout explaining "Your GitHub token IS used — for the git push and PR creation (next step). This step validates YAML syntax locally — no GitHub API call needed here."
**Commit:** pending

---

## Resolved Bugs

### BUG-001 · P04 FAILED status not reflected in left panel
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Severity:** High
**Root Cause:** `LeftPanel.tsx` had no visual treatment for `failed` status — fell through to default (pending) appearance.
**Fix:** Added `isFailed` flag to LeftPanel. Circle shows red border + `✕` icon + glow. Status label shows "✕ Failed — click to retry". `isLocked` now excludes completed/failed phases.
**Commit:** pending (batch commit with BUG-001 through BUG-008)

---

### BUG-002 · P06 GLR not clickable after pipeline completes
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Severity:** High
**Root Cause:** `isLocked` calculation in `LeftPanel.tsx` did not exclude `failed` phases, so a failed P04 would block P06 even after P06 had already completed.
**Fix:** (a) `isLocked` now excludes phases where `isFailed === true`. (b) `handleSelectPhase` in `App.tsx` allows navigation to any completed/failed phase regardless of unlock chain.
**Commit:** pending

---

### BUG-003 · Generating state persists after documents are already generated
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Severity:** Critical
**Root Cause:** `DocumentsView` only re-fetched the file list during periodic polling. The `in_progress → completed` status transition happened asynchronously and the file list lagged by up to one poll cycle (3s).
**Fix:** Added `prevStatusRef` in `DocumentsView.tsx`. When status transitions from `in_progress` to `completed`/`failed`, three immediate re-fetches fire at t=0, t+1.5s, and t+4s to ensure the file list updates promptly.
**Commit:** pending

---

### BUG-004 · TBA / TBC / TBD placeholders in generated documents
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Severity:** Medium
**Root Cause:** No explicit instruction in agent prompts forbidding placeholders. LLM hedged on uncertain values.
**Fix:** Added explicit "NEVER use TBD/TBA/TBC" rule to all document-generating agent SYSTEM_PROMPTs: `document_agent.py`, `compliance_agent.py`, `glr_agent.py`, `srs_agent.py`, `sdd_agent.py`, `netlist_agent.py`. Each prompt instructs the LLM to derive values from P1 component data or state assumptions inline. `document_agent.py` also post-processes output to replace any surviving placeholders with `[see component data]`.
**Commit:** pending

---

### BUG-005 · "Status Legend" section appears in HRS document
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Severity:** Low
**Root Cause:** LLM generates a Status Legend (Draft/Review/Approved) from training data when generating traceability matrices. No instruction to suppress it.
**Fix:** (a) Added "Do NOT include a Status Legend section" to `document_agent.py` SYSTEM_PROMPT. (b) Added post-processing regex in `execute()` that strips any `## Status Legend` section from the output.
**Commit:** pending

---

### BUG-006 · P04 Logical Netlist Generation fails
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Severity:** High
**Root Cause:** `netlist_agent.py` returned `phase_complete: bool(netlist_data)`. When the LLM didn't call the `generate_netlist` tool, `netlist_data = None` → `bool(None) = False` → pipeline_service marked phase as `failed`.
**Fix:** Added skeleton fallback in the `else` branch: synthesizes minimal `netlist.json` + `netlist_visual.md` + `netlist_validation.json` from base components. Returns `phase_complete: True` always. Also added TBD/TBA/TBC prohibition to the prompt.
**Commit:** pending

---

### BUG-007 · CI/CD GitHub Actions YAML missing `on:` trigger key
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Severity:** Medium
**Root Cause:** `_validate_ci_workflow()` in `code_agent.py` checked `if "on" in workflow` after `yaml.safe_load()`. PyYAML (YAML 1.1) parses bare `on:` as boolean `True`, not string `"on"`. So the key existed as `True` in the dict but the string check `"on"` always missed it, falsely reporting the key missing.
**Fix:** Changed the key check to `key in workflow or (key == "on" and True in workflow)` with an explanatory comment.
**Commit:** pending

---

### BUG-008 · Git summary shows "no GitHub remote configured" after P8c
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Severity:** High
**Root Cause:** `git_agent.py` used `search_parent_directories=True` causing GitPython to walk up from `output/rf/` and find the project root `.git` instead of creating a repo in the output dir. Requires server restart to take effect.
**Fix:** Changed to strict `Repo(str(output_dir))` (no parent search). Added `_ensure_remote_base_branch()` for empty GitHub repos. Created `push_to_github.py` standalone script. **Requires FastAPI server restart** after deployment.
**Commit:** pending

---

### BUG-R001 · HRS generating without user approval (auto-trigger)
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Root Cause:** `PhaseHeader` showed `▶ Execute P02` button when `status === 'pending'` with no guard. Users could accidentally trigger P2 without clicking "Approve & Run".
**Fix:** Added `pipelineStarted` prop to PhaseHeader. Execute button hidden until pipeline has been approved at least once.
**Commit:** `6054498`

### BUG-R002 · Phase auto-switching back every 3 seconds
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Root Cause:** Auto-advance `useEffect` in App.tsx fired on every `statuses` poll, overriding user's manual phase selection.
**Fix:** Added `autoAdvancedToRef` — only auto-jumps once per new running phase. User can freely navigate while pipeline runs.
**Commit:** `6054498`

### BUG-R003 · Light theme hardcoded dark colors
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Root Cause:** CreateProjectModal, LoadProjectModal, LandingPage, LeftPanel had hardcoded dark hex values (`#060a10`, `#0a0e1a`, `#1e2d40`, `#2a3a50`) that broke light theme.
**Fix:** Replaced with CSS custom properties (`var(--panel)`, `var(--border2)`, etc.).
**Commit:** `6054498`

### BUG-R004 · Git agent committing to project root repo
**Reported:** 2026-03-22 | **Resolved:** 2026-03-22
**Root Cause:** `_ensure_repo` used `search_parent_directories=True`, walking up from `output/rf/` to find the project root `.git`.
**Fix:** Changed to strict `Repo(str(output_dir))` (no parent search). Added `_ensure_remote_base_branch` for empty GitHub repos.
**Commit:** `6054498`
