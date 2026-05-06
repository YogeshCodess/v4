import { useState, useEffect, useCallback, useRef } from 'react';
import type { Project, Statuses, StatusesRaw, AppMode, CenterTab, DesignScope, ProjectType } from './types';
import type { ChatMessage } from './views/ChatView';
import { newMsgId } from './views/ChatView';
import { PHASES, isUnlocked } from './data/phases';
import { api } from './api';
import DashboardView from './views/DashboardView';
import LeftPanel from './components/LeftPanel';
import MiniTopbar from './components/MiniTopbar';
import PhaseHeader from './components/PhaseHeader';
import CreateProjectModal from './components/CreateProjectModal';
import PipelineDagView from './components/PipelineDagView';
import LoadProjectModal from './components/LoadProjectModal';
import LLMSettingsModal from './components/LLMSettingsModal';
import JudgeMode from './components/JudgeMode';
import RerunPlanDrawer from './components/RerunPlanDrawer';
import Toast from './components/Toast';
import ErrorBoundary from './components/ErrorBoundary';
import ChatView from './views/ChatView';
import DocumentsView from './views/DocumentsView';

export default function App() {
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    return (localStorage.getItem('hw-pipeline-theme') as 'dark' | 'light') || 'dark';
  });

  // Apply data-theme to <html> so CSS vars cascade everywhere
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('hw-pipeline-theme', theme);
  }, [theme]);

  const toggleTheme = () => setTheme(t => t === 'dark' ? 'light' : 'dark');

  const [mode, setMode] = useState<AppMode>('landing');
  const [showDag, setShowDag] = useState(false);
  const [modal, setModal] = useState<'create' | 'load' | null>(null);
  const [llmSettingsOpen, setLLMSettingsOpen] = useState(false);
  const [project, setProject] = useState<Project | null>(null);
  const [statuses, setStatuses] = useState<Statuses>({});
  const [selectedPhaseIdx, setSelectedPhaseIdx] = useState(0);
  const [tab, setTab] = useState<CenterTab>('documents');
  const [toast, setToast] = useState<string | null>(null);
  const [completedIds, setCompletedIds] = useState<string[]>([]);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  // Raw status entries with updated_at timestamps — used for staleness detection
  const [statusesRaw, setStatusesRaw] = useState<StatusesRaw>({});
  // DesignManifest top-level hash (migration 008). Used by DocumentsView as
  // a cache-bust key — when it changes, P1 has re-locked and every cached
  // document for this project is stale. Null until P1 finishes its first
  // lock. The backend returns it on every /status poll.
  const [manifestHash, setManifestHash] = useState<string | null>(null);
  // v20 — Stage 0 design scope, persisted per-project in localStorage.
  // null = not yet picked (user will see the scope picker card in ChatView).
  const [scope, setScope] = useState<DesignScope | null>(null);
  const scopeKey = (pid: number) => `hp-v20-scope-${pid}`;
  const handleScopeChange = useCallback((newScope: DesignScope) => {
    setScope(newScope);
    if (project) {
      try { localStorage.setItem(scopeKey(project.id), newScope); } catch { /* ignore quota */ }
      // Persist to backend so /phases/{id}/execute can enforce applicableScopes
      // and /status returns the authoritative scope on next reload.
      api.setDesignScope(project.id, newScope).catch(err => {
        console.warn('Failed to persist design_scope to backend:', err);
      });
    }
  }, [project]);

  // Reactive polling speed — 2s when running, 3s during active pipeline, 8s fully idle
  const [hasRunning, setHasRunning] = useState(false);
  // True from the moment runPipeline is called until all auto phases are done.
  // Keeps polling at 2s even in the brief gap between consecutive phases.
  // Changed from ref to state so polling useEffect responds immediately to changes.
  const [pipelineActive, setPipelineActive] = useState(false);

  // Refs to prevent duplicate pipeline starts
  const pipelineStartedRef = useRef(false);
  const prevP1StatusRef = useRef<string | undefined>(undefined);
  // Track previous statuses for completion toast detection
  const prevStatusesRef = useRef<Statuses>({});

  // Ref to handleP1Complete so refreshStatuses can call it without circular dep
  const handleP1CompleteRef = useRef<() => void>(() => {});

  // Tracks which phase ID was last auto-advanced to, so we only jump once per
  // new running phase. Without this, every 2-3s poll overrides the user's
  // manual phase selection while the pipeline is running.
  const autoAdvancedToRef = useRef<string | null>(null);

  // ── F5 / reload persistence ─────────────────────────────────────────────────
  // P18 (2026-04-26): URL param `?project=ID` takes priority over sessionStorage.
  // This is what powers the "Dashboard opens project in new tab" flow — the
  // new tab lands at /app?project=42 and auto-loads that project, never
  // touching the dashboard tab's session. The sessionStorage path remains as
  // the fallback for plain F5 in tabs without the URL param.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get('project');
    const action = params.get('action');
    const fromSession = sessionStorage.getItem('hw-pipeline-project-id');
    const targetId = fromUrl ?? fromSession;
    if (targetId && /^\d+$/.test(targetId)) {
      api.getProject(parseInt(targetId, 10))
        .then(p => handleLoadProject(p))
        .catch(() => {
          if (fromUrl) {
            // Bad URL param — strip it so we don't loop on F5.
            try {
              const url = new URL(window.location.href);
              url.searchParams.delete('project');
              window.history.replaceState({}, '', url.toString());
            } catch { /* ignore */ }
          }
          sessionStorage.removeItem('hw-pipeline-project-id');
        });
    } else if (action === 'create' || action === 'load') {
      // Opened from a dashboard nav action in a new tab. Pop the matching
      // modal immediately and strip the param so F5 doesn't re-pop it.
      // mode is already 'landing' by default.
      setModal(action);
      try {
        const url = new URL(window.location.href);
        url.searchParams.delete('action');
        window.history.replaceState({}, '', url.toString());
      } catch { /* ignore */ }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (project) {
      sessionStorage.setItem('hw-pipeline-project-id', String(project.id));
      // P18: keep the URL in sync so F5 reproduces the same view AND so the
      // user can copy / bookmark a project URL. Use replaceState (no
      // history entry) — back button still goes to wherever they came from.
      try {
        const url = new URL(window.location.href);
        if (url.searchParams.get('project') !== String(project.id)) {
          url.searchParams.set('project', String(project.id));
          window.history.replaceState({}, '', url.toString());
        }
      } catch { /* ignore — URL API failures are non-fatal */ }
    } else {
      sessionStorage.removeItem('hw-pipeline-project-id');
      try {
        const url = new URL(window.location.href);
        if (url.searchParams.has('project')) {
          url.searchParams.delete('project');
          window.history.replaceState({}, '', url.toString());
        }
      } catch { /* ignore */ }
    }
  }, [project]);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3500);
  };

  // Poll phase statuses — also detects P1 completion as backup trigger
  const refreshStatuses = useCallback(async () => {
    if (!project) return;
    try {
      const full = await api.getFullStatus(project.id);
      // Derive the simple and raw status shapes from the single /status payload
      // so we do not hit /status twice per poll.
      const rawEntries = full.phase_statuses || {};
      const s: Statuses = {};
      const raw: StatusesRaw = {};
      for (const [key, val] of Object.entries(rawEntries)) {
        if (typeof val === 'string') {
          s[key] = val as Statuses[string];
          raw[key] = { status: val as StatusesRaw[string]['status'] };
        } else if (val && typeof val === 'object' && 'status' in (val as object)) {
          const entry = val as { status: string; updated_at?: string; duration_seconds?: number };
          s[key] = entry.status as Statuses[string];
          raw[key] = {
            status: entry.status as StatusesRaw[string]['status'],
            updated_at: entry.updated_at,
            duration_seconds: entry.duration_seconds,
          };
        } else {
          s[key] = 'pending';
        }
      }
      // Backend is the source of truth for design_scope — if it differs from
      // what the UI thinks, reconcile to the backend value.
      if (full.design_scope && full.design_scope !== scope) {
        setScope(full.design_scope as DesignScope);
        try { localStorage.setItem(scopeKey(project.id), full.design_scope); } catch { /* ignore */ }
      }
      // DesignManifest cache-bust key. When P1 re-locks (user adds/edits
      // requirements and re-confirms), the manifest_hash changes and
      // DocumentsView clears its `contents` cache so the next render
      // re-fetches every document. Null check tolerates legacy projects.
      if (full.manifest_hash !== manifestHash) {
        setManifestHash(full.manifest_hash ?? null);
      }
      prevP1StatusRef.current = s['P1'];

      // Detect newly completed phases for toast notifications
      const prev = prevStatusesRef.current;
      const newlyCompleted = PHASES.filter(
        p => s[p.id] === 'completed' && prev[p.id] !== 'completed' && prev[p.id] !== undefined
      );
      if (newlyCompleted.length > 0) {
        const phase = newlyCompleted[0]; // toast one at a time
        showToast(`${phase.code} \u2014 ${phase.name} complete \u2713`);
      }
      prevStatusesRef.current = s;

      setStatuses(s);
      setStatusesRaw(raw);
      const done = PHASES.filter(p => s[p.id] === 'completed').map(p => p.id);
      setCompletedIds(done);
      const running = Object.values(s).some(v => v === 'in_progress');
      setHasRunning(running);

      // Clear pipelineActive once all auto phases have a terminal status (completed / failed)
      // and nothing is currently in_progress — this returns polling to idle speed.
      if (pipelineActive && !running) {
        const autoPhases = PHASES.filter(p => p.auto && p.id !== 'P1');
        const allDone = autoPhases.every(p => s[p.id] === 'completed' || s[p.id] === 'failed');
        if (allDone) setPipelineActive(false);
      }

      // NOTE: We no longer auto-start the pipeline from the status poll.
      // The user must explicitly click "Approve & Run" in ChatView.
    } catch (_) { /* silent */ }
  }, [project, scope, manifestHash]);

  // Reactive polling:
  //   2s   — while a phase is actively in_progress
  //   2s   — while pipelineActive (brief gap between consecutive phases)
  //   12s  — idle (project loaded but pipeline not running — reduces SQLAlchemy log noise)
  useEffect(() => {
    if (!project) return;
    refreshStatuses();
    const isFast = hasRunning || pipelineActive;
    const interval = setInterval(refreshStatuses, isFast ? 2000 : 12000);
    return () => clearInterval(interval);
  }, [project, refreshStatuses, hasRunning, pipelineActive]);

  // Page Visibility API — when user comes back to Chrome after minimizing/switching,
  // fire an immediate refresh so the UI catches up instantly instead of waiting
  // for the next throttled timer tick (browsers slow background tabs to ~1 min).
  useEffect(() => {
    if (!project) return;
    const onVisible = () => {
      if (document.visibilityState === 'visible') refreshStatuses();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [project, refreshStatuses]);

  // P26 #23 (2026-05-04): pipeline DAG opens via Ctrl+Shift+M (Map).
  // The MiniTopbar button was removed at the user's request; this keyboard
  // shortcut is the new entry point. Esc closes the modal as before.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && (e.key === 'M' || e.key === 'm')) {
        e.preventDefault();
        setShowDag(prev => !prev);
      } else if (e.key === 'Escape' && showDag) {
        setShowDag(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [showDag]);

  // When P1 goes back to draft_pending (user chatted again with new requirements),
  // reset pipelineStartedRef so the Approve & Run button works again.
  useEffect(() => {
    if (statuses['P1'] === 'draft_pending') {
      pipelineStartedRef.current = false;
    }
  }, [statuses]);

  // Auto-advance: when a NEW phase becomes in_progress, jump to it once.
  // Uses a ref to remember the last auto-advanced phase so subsequent polls
  // (every 2-3s) do NOT override the user's manual navigation.
  // If the user manually moves to P1 while P2 is running, they stay there.
  useEffect(() => {
    if (!project) return;
    const runningPhase = PHASES.find(p => statuses[p.id] === 'in_progress');
    if (runningPhase) {
      // Only auto-jump on the FIRST time we detect this particular phase running
      if (runningPhase.id !== autoAdvancedToRef.current) {
        autoAdvancedToRef.current = runningPhase.id;
        const idx = PHASES.findIndex(p => p.id === runningPhase.id);
        setSelectedPhaseIdx(idx);
        setTab('documents');
      }
      // If the same phase is still running, do nothing — user keeps their selection
    } else {
      // No phase running — reset so the next phase that starts can auto-advance
      autoAdvancedToRef.current = null;
    }
  }, [statuses]);

  // Called by ChatView "Approve & Run" button,
  // AND by status-poll fallback via handleP1CompleteRef
  const handleP1Complete = useCallback(async () => {
    if (!project) return;
    showToast('Phase 1 complete \u2014 starting full pipeline...');
    // IMPORTANT: Set pipeline active BEFORE the API call so fast polling starts immediately
    // This prevents the 12s slow poll from missing the early P2 in_progress state
    setPipelineActive(true);
    setHasRunning(true);
    // Switch to Documents tab so user sees generated files immediately
    setTab('documents');
    try {
      await api.runPipeline(project.id);
      // Poll aggressively for first ~10s to catch the in_progress transition fast
      setTimeout(() => refreshStatuses(), 500);
      setTimeout(() => refreshStatuses(), 1500);
      setTimeout(() => refreshStatuses(), 3000);
      setTimeout(() => refreshStatuses(), 6000);
    } catch (err) {
      console.error('[Pipeline] runPipeline FAILED:', err);
      // If pipeline start failed, clear the active flag so polling returns to normal
      setPipelineActive(false);
      setHasRunning(false);
      showToast('Could not auto-start pipeline: ' + (err instanceof Error ? err.message : 'unknown error'));
    }
  }, [project, refreshStatuses]);

  // Keep ref in sync with latest handleP1Complete
  useEffect(() => {
    handleP1CompleteRef.current = handleP1Complete;
  }, [handleP1Complete]);

  // Reset pipeline-started guard and status history when project changes.
  // NOTE: pipelineStartedRef is intentionally NOT reset to false here —
  // handleLoadProject sets it correctly after reading statuses from the DB.
  // Resetting it here would race with the async status fetch and cause
  // the guard to be false during the window where statuses haven't loaded yet.
  useEffect(() => {
    setPipelineActive(false);
    prevP1StatusRef.current = undefined;
    prevStatusesRef.current = {};
  }, [project]);

  const handleExecutePhase = useCallback(async (phaseId: string) => {
    if (!project) return;
    try {
      await api.executePhase(project.id, phaseId);
      setHasRunning(true);
      setTab('documents');
      // Select this phase in the left panel
      const idx = PHASES.findIndex(p => p.id === phaseId);
      if (idx >= 0) setSelectedPhaseIdx(idx);
      // Aggressive polls to catch transition quickly
      setTimeout(() => refreshStatuses(), 800);
      setTimeout(() => refreshStatuses(), 2000);
      setTimeout(() => refreshStatuses(), 4000);
      showToast(`${phaseId} started`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '';
      // Backend returns 409 when the phase is not in the project's design_scope.
      if (msg.includes('409')) {
        showToast(`${phaseId} is not applicable for this project's scope`);
      } else {
        showToast(`Failed to execute ${phaseId}. Check backend.`);
      }
    }
  }, [project, refreshStatuses]);

  const handleCancelPhase = useCallback(async (phaseId: string) => {
    if (!project) return;
    try {
      await api.cancelPhase(project.id, phaseId);
      showToast(`${phaseId} cancelled`);
      setTimeout(() => refreshStatuses(), 500);
      setTimeout(() => refreshStatuses(), 1500);
    } catch {
      showToast(`Failed to cancel ${phaseId}`);
    }
  }, [project, refreshStatuses]);

  const handleRerunStale = useCallback(async (staleIds: string[]) => {
    if (!project || staleIds.length === 0) return;
    try {
      await api.resetAndRerun(project.id, staleIds);
      setHasRunning(true);
      setTab('documents');
      // Select the first stale phase in the left panel
      const firstStaleIdx = PHASES.findIndex(p => staleIds.includes(p.id));
      if (firstStaleIdx >= 0) setSelectedPhaseIdx(firstStaleIdx);
      showToast(`Re-running ${staleIds.length} stale phase${staleIds.length > 1 ? 's' : ''}...`);
      setTimeout(() => refreshStatuses(), 800);
      setTimeout(() => refreshStatuses(), 2000);
      setTimeout(() => refreshStatuses(), 4000);
    } catch {
      showToast('Could not re-run stale phases. Check backend.');
    }
  }, [project, refreshStatuses]);

  const handleRunPipeline = useCallback(async () => {
    if (!project) return;
    try {
      // Set active BEFORE API call so fast polling starts immediately
      setPipelineActive(true);
      setHasRunning(true);
      await api.runPipeline(project.id);
      setTab('documents');
      showToast('Pipeline started — running P2 → P8c...');
      setTimeout(() => refreshStatuses(), 800);
      setTimeout(() => refreshStatuses(), 2000);
      setTimeout(() => refreshStatuses(), 4000);
    } catch (err: unknown) {
      setPipelineActive(false);
      const msg = err instanceof Error ? err.message : '';
      if (msg.includes('400') || msg.includes('Phase 1 must be completed')) {
        showToast('P1 must be completed first. Use the Chat tab to finish Phase 1.');
      } else {
        showToast('Could not start pipeline. Check backend.');
      }
    }
  }, [project, refreshStatuses]);

  const handleCreateProject = async (
    name: string,
    description: string,
    design_type: string,
    project_type: ProjectType,
    design_scope?: DesignScope,
  ) => {
    try {
      const p = await api.createProject({
        name, description, design_type, project_type,
        design_scope: design_scope ?? 'full',
      });
      setProject(p);
      setModal(null);
      setMode('pipeline');
      setSelectedPhaseIdx(0);
      setTab('chat');
      setChatMessages([]);
      pipelineStartedRef.current = false;
      prevP1StatusRef.current = undefined;
      // New project: initial scope comes from the creator (or defaults to 'full'
      // on the backend). If the creator did not pick, leave null so the wizard
      // picker still appears on first load.
      if (design_scope) {
        setScope(design_scope);
        try { localStorage.setItem(scopeKey(p.id), design_scope); } catch { /* ignore */ }
      } else {
        setScope(null);
        try { localStorage.removeItem(scopeKey(p.id)); } catch { /* ignore */ }
      }
    } catch {
      showToast('Failed to create project');
    }
  };

  const handleLoadProject = async (p: Project) => {
    setProject(p);
    setModal(null);
    setMode('pipeline');
    // pipelineStartedRef will be set correctly after statuses load below —
    // do NOT reset it to false here yet (set at end of try block instead).
    prevP1StatusRef.current = undefined;
    // Backend is the source of truth for scope. Prefer the value baked into
    // the project record (p.design_scope); fall back to localStorage for
    // legacy projects still in-flight during this migration.
    const validScope = (s: string | null | undefined): DesignScope | null => {
      if (s === 'full' || s === 'front-end' || s === 'downconversion' || s === 'dsp') return s;
      return null;
    };
    const backendScope = validScope((p as Project).design_scope);
    if (backendScope) {
      setScope(backendScope);
      try { localStorage.setItem(scopeKey(p.id), backendScope); } catch { /* ignore */ }
    } else {
      try {
        const saved = validScope(localStorage.getItem(scopeKey(p.id)));
        setScope(saved);
      } catch { setScope(null); }
    }
    try {
      // Restore P1 chat history from DB so F5 doesn't blank the conversation
      api.getConversationHistory(p.id)
        .then(history => {
          if (history.length > 0) {
            const restored: ChatMessage[] = history.map(m => ({
              role: m.role === 'assistant' ? 'ai' : 'user',
              text: m.content,
              id: newMsgId(),
            }));
            setChatMessages(restored);
          } else {
            setChatMessages([]);
          }
        })
        .catch(() => setChatMessages([]));
    } catch (_) { setChatMessages([]); }
    try {
      const s = await api.getStatus(p.id);
      setStatuses(s);
      const done = PHASES.filter(ph => s[ph.id] === 'completed').map(ph => ph.id);
      setCompletedIds(done);
      const running = Object.values(s).some(v => v === 'in_progress');
      setHasRunning(running);
      const firstIncomplete = PHASES.findIndex(ph => !ph.manual && !done.includes(ph.id));
      const idx = firstIncomplete >= 0 ? firstIncomplete : 0;
      setSelectedPhaseIdx(idx);
      // P1: chat if still pending, documents if already complete
      const landingPhase = PHASES[idx];
      if (landingPhase.id === 'P1') {
        setTab(s['P1'] === 'completed' ? 'documents' : 'chat');
      } else {
        setTab('documents');
      }

      // IMPORTANT: restore pipelineStartedRef from DB state so "Approve & Run"
      // cannot fire a second runPipeline call if the pipeline already ran.
      // If ANY non-P1 AI phase has ever been touched (in_progress/completed/failed),
      // the pipeline was already started — block the guard.
      pipelineStartedRef.current = PHASES.some(
        ph => !ph.manual && ph.id !== 'P1' &&
          (s[ph.id] === 'in_progress' || s[ph.id] === 'completed' || s[ph.id] === 'failed')
      );
    } catch (_) { setTab('documents'); }
  };

  const handleSaveLLMSettings = async (settings: {
    glm_api_key?: string;
    deepseek_api_key?: string;
    anthropic_api_key?: string;
    glm_base_url?: string;
    deepseek_base_url?: string;
    primary_model?: string;
    fast_model?: string;
  }) => {
    const res = await fetch('/api/v1/settings/llm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to save settings');
    }
    return await res.json();
  };

  const handleSelectPhase = (idx: number) => {
    const phase = PHASES[idx];
    if (phase.manual) {
      showToast(`Completed externally in ${phase.externalTool || 'external EDA tool'}`);
      return;
    }
    const phaseStatus = statuses[phase.id] || 'pending';
    // Always allow navigation to completed or failed phases (user may want to view docs or retry)
    const alreadyRan = phaseStatus === 'completed' || phaseStatus === 'failed';
    if (!alreadyRan && !isUnlocked(phase, completedIds) && phase.id !== 'P1') {
      // Find the actual blocking AI phase (skip manual phases in the chain)
      const blockingPhase = [...PHASES].slice(0, idx).reverse().find(p => !p.manual);
      const toastMsg = blockingPhase
        ? `Complete ${blockingPhase.code} \u2014 ${blockingPhase.name} first`
        : 'Complete the previous phase first';
      showToast(toastMsg);
      return;
    }
    setSelectedPhaseIdx(idx);
    // P1: go to Chat if pending (user still designing), Documents if complete (can review outputs)
    // All other phases: always go to Documents
    if (phase.id === 'P1') {
      const p1Done = phaseStatus === 'completed';
      setTab(p1Done ? 'documents' : 'chat');
    } else {
      setTab('documents');
    }
  };

  const selectedPhase = PHASES[selectedPhaseIdx];
  const selectedStatus = statuses[selectedPhase?.id] || 'pending';

  // Staleness: a downstream phase is "stale" if P1 was re-approved AFTER that phase last ran.
  // We compare updated_at timestamps: if P1.updated_at > phase.updated_at, the phase is stale.
  const stalePhaseIds: string[] = (() => {
    const p1Updated = statusesRaw['P1']?.updated_at;
    if (!p1Updated) return [];
    const p1Time = new Date(p1Updated).getTime();
    return PHASES
      .filter(p => !p.manual && p.id !== 'P1' && statuses[p.id] === 'completed')
      .filter(p => {
        const phaseUpdated = statusesRaw[p.id]?.updated_at;
        if (!phaseUpdated) return false;
        return p1Time > new Date(phaseUpdated).getTime();
      })
      .map(p => p.id);
  })();

  if (mode === 'landing') {
    // P18 (2026-04-26): the holographic Dashboard replaces LandingPage as
    // the project==null surface. LandingPage is kept imported above as a
    // rollback target — flip the JSX back if Dashboard ever fails on demo.
    // DashboardView's "open project" button calls handleLoadProject only
    // as a last-resort fallback (when window.open is blocked); the normal
    // path opens the project in a NEW tab via window.open('?project=ID').
    // The Create CTA goes through the existing CreateProjectModal in the
    // SAME tab — that's the user's deliberate "start fresh" path.
    return (
      <>
        <DashboardView
          onCreate={() => {
            // Zero-touch: open the create flow in a new tab so the dashboard
            // tab stays on the dashboard. Fall back to in-tab modal if the
            // popup is blocked.
            try {
              const w = window.open(
                `${window.location.pathname}?action=create`,
                '_blank',
              );
              if (!w) setModal('create');
            } catch {
              setModal('create');
            }
          }}
          onLoadProject={handleLoadProject}
          onShowLoadModal={() => setModal('load')}
          onLlmSettings={() => setLLMSettingsOpen(true)}
        />
        {modal === 'create' && (
          <CreateProjectModal
            onConfirm={handleCreateProject}
            onCancel={() => setModal(null)}
          />
        )}
        {modal === 'load' && (
          <LoadProjectModal
            onSelect={handleLoadProject}
            onCancel={() => setModal(null)}
          />
        )}
        <LLMSettingsModal
          open={llmSettingsOpen}
          onClose={() => setLLMSettingsOpen(false)}
          onSave={handleSaveLLMSettings}
        />
        {toast && <Toast message={toast} />}
      </>
    );
  }

  return (
    <>
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', background: 'var(--navy)', fontFamily: "'DM Mono', monospace" }}>
      {/* Left Panel */}
      <LeftPanel
        phases={PHASES}
        selectedIdx={selectedPhaseIdx}
        statuses={statuses}
        completedIds={completedIds}
        stalePhaseIds={stalePhaseIds}
        pipelineStarted={PHASES.some(
          ph => !ph.manual && ph.id !== 'P1' &&
          ['in_progress', 'completed', 'failed'].includes(statuses[ph.id])
        )}
        scope={scope ?? undefined}
        onSelect={handleSelectPhase}
        onLanding={() => {
          setMode('landing');
          setProject(null);
          setStatuses({});
          setCompletedIds([]);
          setChatMessages([]);
          setHasRunning(false);
          setPipelineActive(false);
          pipelineStartedRef.current = false;
          prevP1StatusRef.current = undefined;
        }}
      />

      {/* Center Content */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--navy)' }}>
        <MiniTopbar
          project={project}
          phases={PHASES}
          statuses={statuses}
          stalePhaseIds={stalePhaseIds}
          onRunPipeline={handleRunPipeline}
          onRerunStale={handleRerunStale}
          onShowDag={() => setShowDag(true)}
          pipelineRunning={hasRunning}
          theme={theme}
          onToggleTheme={toggleTheme}
        />
        <div style={{ flex: 1, overflowY: 'auto' }}>
          <div className="fade-up" key={selectedPhaseIdx}>
          <PhaseHeader
            phase={selectedPhase}
            status={selectedStatus}
            tab={tab}
            onTabChange={setTab}
            onExecute={() => handleExecutePhase(selectedPhase.id)}
            pipelineRunning={hasRunning}
            isStale={stalePhaseIds.includes(selectedPhase?.id)}
            pipelineStarted={Object.entries(statuses).some(
              ([k, v]) => k !== 'P1' && (v === 'completed' || v === 'in_progress' || v === 'failed')
            )}
            scope={scope}
            durationSeconds={statusesRaw[selectedPhase?.id]?.duration_seconds}
          />
          </div>
          <div style={{ padding: '0 26px 26px' }}>
            {/* ChatView: only for P1 — kept mounted while on P1 so state is preserved */}
            {selectedPhase.id === 'P1' && (
              <div style={{ display: tab === 'chat' ? 'block' : 'none' }}>
                <ErrorBoundary>
                  <ChatView
                    project={project}
                    phase={selectedPhase}
                    phaseStatus={statuses['P1'] || 'pending'}
                    pipelineStarted={
                      // When P1 is draft_pending the user has new requirements — always show Approve.
                      // Old P2+ completed statuses must NOT suppress the button in this case.
                      statuses['P1'] !== 'draft_pending' &&
                      Object.entries(statuses).some(
                        ([k, v]) => k !== 'P1' && (v === 'completed' || v === 'in_progress')
                      )
                    }
                    messages={chatMessages}
                    onMessages={setChatMessages}
                    onStatusChange={refreshStatuses}
                    onPhaseComplete={() => {
                      if (!pipelineStartedRef.current) {
                        pipelineStartedRef.current = true;
                        handleP1Complete();
                      }
                    }}
                    scope={scope}
                    onScopeChange={handleScopeChange}
                  />
                </ErrorBoundary>
              </div>
            )}
            {/* DocumentsView: always mounted, never remounted on phase switch.
                Phase changes propagate via props so the file cache is preserved. */}
            <div style={{ display: tab === 'documents' ? 'block' : 'none' }}>
              <ErrorBoundary>
                <DocumentsView project={project} phase={selectedPhase} status={selectedStatus} pipelineRunning={hasRunning} manifestHash={manifestHash} />
              </ErrorBoundary>
            </div>
          </div>
        </div>
      </div>

      {/* Right-side Flow Panel intentionally removed — user feedback was
          that the step-by-step execution flow on the right was clutter.
          Run/Re-run actions are already available via the phase header
          and the Rerun Plan drawer, so the panel added no information
          that wasn't accessible elsewhere. */}

    </div>
      {/* Modals rendered OUTSIDE the overflow:hidden flex container so position:fixed works correctly */}
      {modal === 'create' && (
        <CreateProjectModal
          onConfirm={handleCreateProject}
          onCancel={() => setModal(null)}
        />
      )}
      {modal === 'load' && (
        <LoadProjectModal
          onSelect={handleLoadProject}
          onCancel={() => setModal(null)}
        />
      )}
      <LLMSettingsModal
        open={llmSettingsOpen}
        onClose={() => setLLMSettingsOpen(false)}
        onSave={handleSaveLLMSettings}
      />
      <JudgeMode projectId={project?.id ?? null} />
      <RerunPlanDrawer projectId={project?.id ?? null} />
      {showDag && (
        <PipelineDagView
          statuses={statuses}
          selectedId={selectedPhase?.id}
          onSelect={(id) => {
            const idx = PHASES.findIndex(p => p.id === id);
            if (idx >= 0) setSelectedPhaseIdx(idx);
            setShowDag(false);
          }}
          onClose={() => setShowDag(false)}
        />
      )}
      {toast && <Toast message={toast} />}
    </>
  );
}