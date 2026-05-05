# P18 — Holographic Landing Dashboard (Option C)

**Status:** PLAN LOCKED — do not implement until user says "start P18".
**Source:** `dashboard-lab.html` at repo root (659-line standalone mock).
**Authored:** 2026-04-24.
**Decisions (final, by user):**
  - **Q1 — Create New Project from Dashboard:** same tab.
  - **Q2 — 24h telemetry graph section:** DROP entirely. Fewer demo questions, more honest dashboard.
  - **Q3 — "Dashboard" button inside pipeline view:** DO NOT add. Zero-touch pipeline per user constraint. Dashboard is only reachable via fresh `/app` URL or new browser tab.

## 0. Top-level constraint (user's non-negotiable)

> "it should not affect current project. if we click load project it should
>  open in new tab not in same tab because currently it will running."

Translation: adding the Dashboard MUST cause zero change to the running
pipeline. Every design decision below is filtered through this constraint.

## 1. Scope — the "Zero-Touch Pipeline" guarantee

Adding the Dashboard changes EXACTLY THREE things. Nothing else.

| # | Change | File(s) | Impact on running pipeline |
|---|---|---|---|
| 1 | New visual at the landing screen | `App.tsx` renders `<DashboardView />` instead of `<LandingPage />` when `project == null` | None. Pipeline view only renders when `project != null`. |
| 2 | Additive URL-param handler | `App.tsx` on mount: `if (?project=X in URL && no project loaded) → auto-load X` | None. Additive. No URL param → existing code path runs identically. |
| 3 | Dashboard's Load button uses `window.open(..., '_blank')` | `DashboardView.tsx` wraps its own onSelect | None. Pipeline's own `LoadProjectModal` unchanged. |

### What is explicitly NOT touched

- `LoadProjectModal.tsx` — untouched; Dashboard wraps its own `onSelect`.
- `CreateProjectModal.tsx` — untouched.
- Pipeline view (`LeftPanel`, center content, `PhaseHeader`) — untouched.
- All 4 phase views (`ChatView`, `DetailsView`, `MetricsView`, `DocumentsView`) — untouched.
- Polling logic — untouched (3s interval on current project).
- State management (`project`, `phaseStatuses`, etc.) — untouched.
- localStorage keys — untouched; existing F5-recovery path preserved.
- Backend agents / `main.py` — untouched. Dashboard uses existing endpoints.
- Current CSS variables / teal theme — untouched; dashboard CSS scoped.

## 2. URL & navigation model

| URL | Shows | How entered |
|---|---|---|
| `/app` | Dashboard (new holographic view) | Fresh tab, new-window navigation |
| `/app?project={id}` | Pipeline view for that project | `window.open` from Dashboard; F5 on pipeline tab |

### Navigation rules

| Action | From | Behavior | Rationale |
|---|---|---|---|
| Load existing project | Dashboard | `window.open('/app?project=' + id, '_blank')` → NEW tab | Preserve any running tab elsewhere |
| Create new project | Dashboard | `window.location.href = '/app?project=' + newId` → SAME tab | User deliberately starts fresh; no prior work on dashboard |
| F5 on Pipeline tab | - | URL has `?project=xxx` → auto-load that project | Restores view without disruption |
| F5 on Dashboard tab | - | URL is `/app` → fresh dashboard | No state to preserve |
| Phase card click (Dashboard) | - | `window.open('/app?project=' + id, '_blank')` → NEW tab | Click to watch = new tab |
| Closing a pipeline tab | - | Backend run continues; reopen via URL or dashboard list | Tab-scoped frontend, project-scoped backend |

### Isolation walk-through (proves zero-touch)

Your running 12-min P1 in Tab A is safe when:

- ✅ Someone opens `/app` in Tab B → hits Dashboard. Tab A unaffected.
- ✅ You F5 Tab A → URL `/app?project=abc` → auto-loads abc (same as localStorage today).
- ✅ You click "Load Project" from Tab B's Dashboard → new Tab C opens at `/app?project=other_id` → Tab A untouched.
- ✅ You type in P1 chat in Tab A, switch to Tab B, return → Tab A state exactly as left.
- ✅ Dashboard fetch fails → shows error state in Tab B. Tab A still runs.

The ONLY way to affect Tab A from Dashboard is to deliberately navigate
Tab A's URL bar yourself or close Tab A.

## 3. Content & data wiring

### What the Dashboard shows (7 sections in source HTML → 6 after Q2 decision)

1. **Top nav** — logo (orbital) + menu (Dashboard / Projects / BOM / Compliance / Docs) + "Start new run" CTA.
   - Menu items are anchors for now; only "Dashboard" (current page) and "Start new run" (→ CreateProjectModal) are wired.
2. **Hero split pane** — tagline + chips + stats on left; animated holo gauge on right.
3. **4 KPI cards with sparklines** — Time Saved / Error Reduction / Cost Impact / Confidence.
4. **Phase constellation** — 11 phase cards + 1 "Extend" plugin slot.
5. **Live running + Events** two-column.
6. ~~**24h telemetry graph**~~ — **DROPPED per Q2.**

### Data sources per element (honest labelling)

| Element | Source | Notes |
|---|---|---|
| Holo gauge — overall % | `sum(completed_phases) / sum(total_phases)` across all projects | Real |
| Chips in hero ("RADAR · SDR · ZYNQ US+") | Derived from latest project's `design_parameters` | Real, falls back to generic tags if empty |
| Hero stats (hrs / phases / confidence) | Aggregated from `/api/v1/projects` | Real |
| **Time Saved (hrs)** | `completed_phases_across_all_projects × 4.25 hrs/phase` | **Footnote:** "based on manual-engineering baseline (4.25 hrs/phase avg)" |
| **Error Reduction (%)** | `1 - (avg_audit_blockers / avg_audit_checks)` across projects | Real |
| **Cost Impact ($/yr)** | `time_saved × 52 × $150/hr` | **Footnote:** "engineering cost at $150/hr × 52 wk" |
| **Confidence (%)** | Weighted avg of phase `confidence_score` | Real |
| Phase constellation cards | Latest project's `phase_statuses`, colored by P-number | Real |
| Live running panel | Latest project's in-progress phase + substeps from `phases.ts` | Real |
| Events feed | Derived client-side from all projects' `phase_statuses` timestamps | Real |

### No-data states

- **Zero projects:** Dashboard shows 0/11 progress; KPI cards show "—"; Events feed shows "No activity yet"; CTA highlights "Create New Project."
- **Backend offline:** Graceful error banner at top; empty placeholders below; "Retry" button.
- **All projects complete:** Live running panel shows "All systems nominal — no phase running."

## 4. Theme

- Dashboard = **purple/pink/cyan holographic** (Instrument Serif + Space Grotesk + IBM Plex Mono). Palette:
  - `--iris-a: #b388ff`, `--iris-b: #ff5ca8`, `--iris-c: #5ce1ff`, `--iris-d: #ffc65c`
  - Background `#08040f` with aurora ambient gradients
- Pipeline view = **existing teal/navy**. UNCHANGED.
- Two themes never mix in one view. Scoped via `.dashboard-root` wrapper class on DashboardView's root `<div>`. Dashboard CSS uses class-scoped selectors (`.dashboard-root .pane`, not just `.pane`) so it can't leak into pipeline view.

## 5. File structure

```
hardware-pipeline-v5-react/src/
├── App.tsx                              [MODIFIED] ~15 lines: URL-param handler + render DashboardView when project==null
├── api.ts                               [MODIFIED — optional] add summary() helper if we add /api/v1/projects/summary backend
├── views/
│   ├── DashboardView.tsx                [NEW ~250 lines] composes the 5 remaining sections
│   ├── ChatView.tsx                     [UNCHANGED]
│   ├── DetailsView.tsx                  [UNCHANGED]
│   ├── MetricsView.tsx                  [UNCHANGED]
│   └── DocumentsView.tsx                [UNCHANGED]
├── components/
│   ├── dashboard/                       [NEW folder]
│   │   ├── HoloGauge.tsx                [NEW] animated conic-gradient ring + ticks + center readout
│   │   ├── KPIRow.tsx                   [NEW] 4-card row with inline SVG sparklines
│   │   ├── PhaseConstellation.tsx       [NEW] 12-card grid (11 phases + extend slot)
│   │   ├── LiveRunning.tsx              [NEW] 3 spec rings + flow chain steps
│   │   └── EventsFeed.tsx               [NEW] 8-row derived activity feed
│   ├── LandingPage.tsx                  [KEPT, unused] — kept as rollback
│   ├── LoadProjectModal.tsx             [UNCHANGED]
│   ├── CreateProjectModal.tsx           [UNCHANGED]
│   └── (rest unchanged)
└── styles/
    └── dashboard.css                    [NEW ~300 lines] scoped under .dashboard-root
```

### Lines-changed estimate

- `App.tsx` delta: ~+15 lines (URL-param handler + import + conditional render).
- `DashboardView.tsx`: ~250 lines (compose 5 sections).
- 5 dashboard components: ~120-180 lines each.
- `dashboard.css`: ~300 lines (ported from dashboard-lab.html, scoped to `.dashboard-root`).
- Total new code: ~1200-1400 lines.
- Total changed pipeline-view code: **15 lines** in App.tsx, all additive.

## 6. Optional backend endpoint (defer)

```
GET /api/v1/projects/summary
  → {
      projects: [{id, name, phase_statuses, created_at, design_parameters}],
      aggregates: {
        total_phases_done, total_phases,
        avg_confidence,
        recent_events: [{project_id, phase_id, status, ts}]
      }
    }
```

v1 implementation: compute client-side from existing `/api/v1/projects`
+ N per-project `/status` calls. Add the summary endpoint only if the
N+1 call pattern proves slow on-demo.

## 7. Edge cases — all handled

| Scenario | Behavior |
|---|---|
| User in Pipeline with P1 running, opens `/app` in new tab | Dashboard shows in new tab; Tab A pipeline untouched. |
| F5 on pipeline view | URL has `?project=xxx` → re-loads pipeline. LocalStorage fallback preserved if URL param missing. |
| F5 on dashboard | Fresh dashboard, running backends untouched. |
| 3 projects open in 3 tabs | 3 independent polling loops, no conflict. |
| Backend returns 500 on `/projects` | Dashboard shows "Backend offline, retry" with empty states. Pipeline tabs still running (their polling handles its own errors). |
| No projects yet (first run) | Dashboard shows 0/11 progress; CTA highlights "Create New Project." |
| User loads project in new tab, runs a phase, goes back to dashboard tab | Dashboard polls → sees new phase status → card animates to "Live". |
| User bookmarks `/app?project={id}` | Share-friendly URLs; colleague opening sees same project behind auth gate. |
| User types chat message in P1, then clicks Dashboard link | N/A — there IS no Dashboard link in pipeline view (Q3 decision). They must open new tab themselves, which keeps Tab A intact. |
| "Extend" 12th phase card is clicked | Opens a "Plugin slot coming soon" toast — deferred feature, no implementation. |
| User clicks nav menu items other than Dashboard/Start | They are inert anchors in v1. Console-log clicks for later analytics. |

## 8. Implementation order (when user says "start")

1. **Scaffold** — create `plans/`, `styles/dashboard.css`, `views/DashboardView.tsx`, `components/dashboard/*.tsx` empty shells.
2. **Port CSS** — extract styles from `dashboard-lab.html`, scope every selector under `.dashboard-root`, verify no leakage.
3. **Port structure** — port sections 1-5 (nav, hero, KPI, constellation, live+events) from HTML into React components. Hardcoded initially.
4. **Wire `App.tsx`** — add URL-param handler (additive) + render `<DashboardView />` when `project == null`.
5. **Wire data** — replace hardcoded values with fetches from existing `/api/v1/projects` + `/status` per project. Add footnotes on Time Saved / Cost Impact.
6. **Wire navigation** — Create→same tab, Load→new tab, Phase card→new tab.
7. **No-data states** — zero projects, backend offline.
8. **Rebuild bundle** → `npm run build` + copy to `frontend/bundle.html`.
9. **Tests** (lightweight):
   - Unit: URL-param handler parses correctly; `window.open` called for Load.
   - Integration: render DashboardView with mocked `api.listProjects()` → all sections populate.
   - Guard: no CSS selector in `dashboard.css` is unscoped (grep for rules that don't start with `.dashboard-root`).
10. **Manual verification checklist:**
    - Open `/app` → dashboard renders.
    - Create project from dashboard → same tab navigates to pipeline.
    - Open `/app` in another tab → dashboard again.
    - Load project from Tab B's dashboard → opens Tab C with pipeline.
    - Tab A unaffected throughout.
11. **Commit message:** `P18: holographic landing dashboard (Option C) — zero-touch pipeline`.

## 9. Rollback plan

Revert is trivial — every change is additive or contained:

- `App.tsx`: revert the ~15-line conditional branch. `LandingPage` still exists and becomes default again.
- Delete `views/DashboardView.tsx`, `components/dashboard/`, `styles/dashboard.css`.
- No backend changes to revert.
- No database migrations.
- Rebuild bundle.

Total rollback effort: ~5 minutes.

## 10. Effort estimate

- Scaffold + CSS port + structure port: 2 hrs
- Component data wiring (5 components): 2 hrs
- URL routing + Load new-tab: 30 min
- No-data states + honest footnotes: 30 min
- Tests + rebuild + commit: 1 hr
- **Total: ~6 hrs (one focused afternoon)**

## 11. Signal to start

User will say "start P18" or similar. Do NOT begin implementation before that signal.
