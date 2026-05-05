import type { Project, Statuses, StatusesRaw, DesignScope, ProjectType } from './types';

// Same-origin when served by FastAPI (port 8000 or behind a proxy).
// Fall back to explicit localhost:8000 when opened directly as file:// or via Vite dev server.
const BASE = (
  window.location.protocol === 'file:' ||
  (window.location.port !== '8000' && window.location.port !== '')
)
  ? 'http://localhost:8000'
  : '';

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    let detail = '';
    try { const j = await res.json(); detail = j.detail || ''; } catch { /* ignore */ }
    throw new Error(`HTTP ${res.status}: ${res.statusText}${detail ? ' — ' + detail : ''} [${path}]`);
  }
  return res.json();
}

export interface ChatResult {
  text: string;
  phaseComplete: boolean;
  draftPending: boolean;
  /**
   * Raw card JSON from the backend when the agent called
   * `show_clarification_cards` this turn. Frontend renders this directly as
   * clickable chip cards — no prose parsing needed. `null` if the agent
   * answered with free text this turn.
   */
  clarificationCards?: {
    intro: string;
    questions: { id: string; question: string; why: string; options: string[] }[];
    prefilled?: Record<string, string>;
  } | null;
}

export const api = {
  listProjects: () => req<Project[]>('/api/v1/projects'),

  createProject: (data: {
    name: string;
    description: string;
    design_type: string;
    design_scope?: DesignScope;
    project_type?: ProjectType;
  }) =>
    req<Project>('/api/v1/projects', { method: 'POST', body: JSON.stringify(data) }),

  /** PATCH the project's design_scope after the wizard has narrowed or widened it.
   *  The backend uses this to enforce applicableScopes in /phases/.../execute. */
  setDesignScope: (id: number, scope: DesignScope) =>
    req<Project>(`/api/v1/projects/${id}/design-scope`, {
      method: 'PATCH',
      body: JSON.stringify({ design_scope: scope }),
    }),

  getProject: (id: number) => req<Project>(`/api/v1/projects/${id}`),

  getStatus: async (id: number): Promise<Statuses> => {
    const r = await req<{ phase_statuses: Record<string, unknown> }>(`/api/v1/projects/${id}/status`);
    const raw = r.phase_statuses || {};
    // Backend stores phase_statuses as {"P1": {"status": "completed", "updated_at": "..."}, ...}
    // Flatten to {"P1": "completed", ...} for the UI
    const flat: Statuses = {};
    for (const [key, val] of Object.entries(raw)) {
      if (typeof val === 'string') {
        flat[key] = val as Statuses[string];
      } else if (val && typeof val === 'object' && 'status' in val) {
        flat[key] = (val as { status: string }).status as Statuses[string];
      } else {
        flat[key] = 'pending';
      }
    }
    return flat;
  },

  getStatusRaw: async (id: number): Promise<StatusesRaw> => {
    const r = await req<{ phase_statuses: Record<string, unknown> }>(`/api/v1/projects/${id}/status`);
    const raw = r.phase_statuses || {};
    const result: StatusesRaw = {};
    for (const [key, val] of Object.entries(raw)) {
      if (typeof val === 'string') {
        result[key] = { status: val as StatusesRaw[string]['status'] };
      } else if (val && typeof val === 'object' && 'status' in val) {
        const entry = val as { status: string; updated_at?: string; duration_seconds?: number };
        result[key] = {
          status: entry.status as StatusesRaw[string]['status'],
          updated_at: entry.updated_at,
          duration_seconds: entry.duration_seconds,
        };
      }
    }
    return result;
  },

  /** Full /status payload including the authoritative design_scope and
   *  applicable_phase_ids computed by the backend. Prefer this over
   *  getStatus/getStatusRaw whenever you need scope info. */
  getFullStatus: (id: number) =>
    req<{
      project_id: number;
      current_phase: string;
      design_scope: DesignScope;
      applicable_phase_ids: string[];
      phase_statuses: Record<string, unknown>;
      requirements_hash: string | null;
      requirements_frozen_at: string | null;
      stale_phase_ids: string[];
    }>(`/api/v1/projects/${id}/status`),

  runPipeline: (id: number) =>
    req(`/api/v1/projects/${id}/pipeline/run`, { method: 'POST' }),

  executePhase: (id: number, phaseId: string) =>
    req(`/api/v1/projects/${id}/phases/${phaseId}/execute`, { method: 'POST' }),

  cancelPhase: (id: number, phaseId: string) =>
    req(`/api/v1/projects/${id}/phases/${phaseId}/cancel`, { method: 'POST' }),

  // Reset stale phases to 'pending' then re-run the pipeline
  resetAndRerun: (id: number, phaseIds: string[]) =>
    req(`/api/v1/projects/${id}/phases/reset`, {
      method: 'POST',
      body: JSON.stringify({ phase_ids: phaseIds }),
    }),

  // E1 — Judge-mode wipe-state. Clears phase_statuses, conversation_history,
  // design_parameters, and every requirements-lock column. Preserves identity
  // fields so the project tile in the left panel stays put.
  resetState: (id: number) =>
    req<{
      status: string;
      project_id: number;
      cleared_columns: string[];
      was_non_empty: boolean;
      counts: Record<string, number>;
      current_phase: string;
    }>(`/api/v1/projects/${id}/reset-state`, { method: 'POST' }),

  // E2 — dry-run preview of the stale re-run plan.
  getRerunPlan: (id: number) =>
    req<{
      project_id: number;
      current_hash: string | null;
      stale: string[];
      order: string[];
      blocked_by_manual: string[];
      status_summary: Record<string, string>;
      summary: string;
    }>(`/api/v1/projects/${id}/pipeline/rerun-plan`),

  // E2 — execute the plan surfaced by getRerunPlan.
  rerunStale: (id: number) =>
    req<{
      status: string;
      project_id: number;
      reset_phases: string[];
    }>(`/api/v1/projects/${id}/pipeline/rerun-stale`, { method: 'POST' }),

  // Export project documents as a ZIP — returns a download URL.
  // Pass `phaseId` (e.g. "P2") to limit the ZIP to one phase's files.
  // Without `phaseId` the whole-project deliverable bundle is returned.
  exportZipUrl: (id: number, phaseId?: string) => {
    const base = `${BASE}/api/v1/projects/${id}/export`;
    return phaseId ? `${base}?phase_id=${encodeURIComponent(phaseId)}` : base;
  },

  /**
   * Spawn a chat message as a backend background task. Returns immediately
   * with `{ taskId, status: 'running' }` (HTTP 202). Use for long
   * operations like P1 finalize that can take 5–15 min on dense RF.
   * Poll `getChatTask` until `status === 'complete'` or `'failed'`.
   */
  chatAsync: async (id: number, message: string): Promise<{ taskId: string; status: string }> => {
    const res = await fetch(`${BASE}/api/v1/projects/${id}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, async: true }),
    });
    if (!res.ok && res.status !== 202) {
      let detail = '';
      try { const j = await res.json(); detail = j.detail || ''; } catch { /* ignore */ }
      throw new Error(`HTTP ${res.status}: ${res.statusText}${detail ? ' — ' + detail : ''}`);
    }
    const j = await res.json();
    return { taskId: j.task_id, status: j.status };
  },

  /** Poll a backend chat task spawned via `chatAsync`. */
  getChatTask: async (id: number, taskId: string): Promise<{
    taskId: string;
    status: 'running' | 'complete' | 'failed';
    elapsedS: number;
    result: {
      response?: string;
      phase_complete?: boolean;
      draft_pending?: boolean;
      clarification_cards?: ChatResult['clarificationCards'];
    } | null;
    error: string | null;
  }> => {
    const r = await req<{
      task_id: string;
      status: 'running' | 'complete' | 'failed';
      elapsed_s: number;
      result: Record<string, unknown> | null;
      error: string | null;
    }>(`/api/v1/projects/${id}/chat/tasks/${taskId}`);
    return {
      taskId: r.task_id,
      status: r.status,
      elapsedS: r.elapsed_s,
      result: r.result as {
        response?: string;
        phase_complete?: boolean;
        draft_pending?: boolean;
        clarification_cards?: ChatResult['clarificationCards'];
      } | null,
      error: r.error,
    };
  },

  chat: async (id: number, message: string): Promise<ChatResult> => {
    const result = await req<{
      response?: string; message?: string; content?: string;
      phase_complete?: boolean;
      draft_pending?: boolean;
      clarification_cards?: ChatResult['clarificationCards'];
    }>(
      `/api/v1/projects/${id}/chat`,
      { method: 'POST', body: JSON.stringify({ message }) }
    );
    // Use `result.response` if it is defined (even if it's an empty string).
    // Only fall through to other fields or JSON.stringify when the key is truly absent.
    const text = (result.response != null)
      ? result.response
      : (result.message != null)
        ? result.message
        : (result.content != null)
          ? result.content
          : JSON.stringify(result);
    return {
      text,
      phaseComplete: !!result.phase_complete,
      draftPending: !!result.draft_pending,
      clarificationCards: result.clarification_cards ?? null,
    };
  },

  listDocuments: (id: number): Promise<{ name: string; size: number }[]> =>
    req(`/api/v1/projects/${id}/documents`),

  getDocumentText: async (id: number, filename: string, signal?: AbortSignal): Promise<string> => {
    const res = await fetch(`${BASE}/api/v1/projects/${id}/documents/${encodeURIComponent(filename)}`, { signal });
    if (!res.ok) throw new Error(`${res.status}`);
    return res.text();
  },

  getConversationHistory: async (id: number): Promise<{ role: string; content: string }[]> => {
    const proj = await req<{ conversation_history?: { role: string; content: string }[] }>(
      `/api/v1/projects/${id}`
    );
    return (proj.conversation_history || []).filter(
      m => (m.role === 'user' || m.role === 'assistant') && m.content
    );
  },

  /** Call POST /clarify — returns structured card data (tool_use forced, zero parse failures).
   *  Round-1: pass just requirement + designType.
   *  Round-N (N>=2): pass the accumulated conversation history so the backend can produce
   *  follow-up cards that build on prior turns. */
  clarifyRequirement: async (
    id: number,
    requirement: string,
    designType: string = 'RF',
    conversationHistory?: { role: string; content: string }[],
    roundLabel?: string
  ): Promise<{
    intro: string;
    questions: Array<{ id: string; question: string; why: string; options: string[] }>;
  }> => {
    const body: Record<string, unknown> = { requirement, design_type: designType };
    if (conversationHistory && conversationHistory.length > 0) {
      body.conversation_history = conversationHistory;
    }
    if (roundLabel) body.round_label = roundLabel;
    const res = await fetch(`${BASE}/api/v1/projects/${id}/clarify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`Clarify failed: ${res.status}`);
    return res.json();
  },
};
