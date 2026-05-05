import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';

/**
 * E2 — Rerun Plan Drawer
 *
 * Ctrl+Shift+R toggles a right-side drawer that previews what the "Re-run
 * stale phases" button will actually do before the user hits execute.
 *
 * Pulls deterministic data from GET /api/v1/projects/{id}/pipeline/rerun-plan:
 *   - current_hash       SHA-256 of the frozen requirements (or null)
 *   - stale              phase ids whose last completion used a stale hash
 *   - order              canonical re-run order
 *   - blocked_by_manual  phases (P5/P7) that will also need rework
 *   - status_summary     {phaseId: "fresh"|"stale"|"pending"|"manual"|...}
 *
 * Confirm button POSTs /pipeline/rerun-stale. The drawer stays open to show
 * the reset_phases it returned.
 */

interface RerunPlan {
  project_id: number;
  current_hash: string | null;
  stale: string[];
  order: string[];
  blocked_by_manual: string[];
  status_summary: Record<string, string>;
  summary: string;
}

interface Props {
  projectId: number | null;
}

// Canonical phase ordering mirrors `services.phase_catalog.AUTO_PHASE_IDS`
// plus P1 (lock owner) and P5 (manual PCB). P7 (FPGA RTL) and P7a (Register
// Map) are both automated — omitting either under-reports stale FPGA work
// in the rerun plan and the per-phase status badges.
const PHASE_ORDER = ['P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7', 'P7a', 'P8a', 'P8b', 'P8c'];

export default function RerunPlanDrawer({ projectId }: Props) {
  const [open, setOpen] = useState(false);
  const [plan, setPlan] = useState<RerunPlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!projectId) {
      setPlan(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const p = await api.getRerunPlan(projectId);
      setPlan(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  // Ctrl+Shift+R → toggle. Esc → close.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && (e.key === 'R' || e.key === 'r')) {
        e.preventDefault();
        setOpen(o => {
          if (!o) refresh();
          return !o;
        });
      } else if (e.key === 'Escape' && open) {
        setOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, refresh]);

  // Poll when open & project changes.
  useEffect(() => {
    if (open) refresh();
  }, [open, projectId, refresh]);

  const handleExecute = useCallback(async () => {
    if (!projectId || !plan || plan.stale.length === 0) return;
    setExecuting(true);
    setError(null);
    setLastResult(null);
    try {
      const r = await api.rerunStale(projectId);
      setLastResult(
        r.reset_phases.length
          ? `Reset ${r.reset_phases.join(', ')} to pending — pipeline started.`
          : 'Nothing was stale by the time we confirmed.'
      );
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setExecuting(false);
    }
  }, [projectId, plan, refresh]);

  if (!open) return null;

  const badgeColour = (status: string): string => {
    switch (status) {
      case 'fresh': return 'var(--teal, #00c6a7)';
      case 'stale': return 'var(--warning, #f59e0b)';
      case 'in_progress': return 'var(--blue, #3b82f6)';
      case 'failed': return 'var(--danger, #dc2626)';
      case 'manual': return 'var(--text3, #64748b)';
      case 'pending':
      default: return 'var(--text3, #64748b)';
    }
  };

  return (
    <div
      role="dialog"
      aria-label="Re-run plan preview"
      style={{
        position: 'fixed',
        top: 0,
        right: 0,
        bottom: 0,
        width: 380,
        zIndex: 1900,
        background: 'var(--panel, #1a2235)',
        color: 'var(--text, #e2e8f0)',
        borderLeft: '1px solid var(--teal, #00c6a7)',
        boxShadow: '0 0 28px rgba(0,198,167,0.25)',
        padding: 20,
        fontFamily:
          'DM Mono, JetBrains Mono, "Fira Code", "Courier New", monospace',
        fontSize: 12,
        overflowY: 'auto',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 12,
          paddingBottom: 10,
          borderBottom: '1px solid var(--border, rgba(42,58,80,0.8))',
        }}
      >
        <h3
          style={{
            margin: 0,
            fontFamily: 'Syne, Inter, sans-serif',
            fontSize: 16,
            color: 'var(--teal, #00c6a7)',
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
          }}
        >
          Re-run Plan
        </h3>
        <span style={{ color: 'var(--text3, #64748b)', fontSize: 11 }}>
          Ctrl+Shift+R · Esc
        </span>
      </div>

      {error && (
        <div style={{ color: 'var(--danger, #dc2626)', marginBottom: 8 }}>{error}</div>
      )}
      {loading && (
        <div style={{ color: 'var(--text3, #64748b)', marginBottom: 8 }}>Refreshing…</div>
      )}

      {!projectId ? (
        <div style={{ color: 'var(--text3, #64748b)' }}>
          No project loaded.
        </div>
      ) : !plan ? (
        <div style={{ color: 'var(--text3, #64748b)' }}>
          Press Ctrl+Shift+R to load the plan.
        </div>
      ) : (
        <>
          <div
            style={{
              marginBottom: 10,
              color: 'var(--text2, #94a3b8)',
              lineHeight: 1.5,
            }}
          >
            {plan.summary}
          </div>

          <div style={{ marginBottom: 12 }}>
            <div
              style={{
                color: 'var(--text3, #64748b)',
                fontSize: 10,
                textTransform: 'uppercase',
                letterSpacing: '0.06em',
                marginBottom: 6,
              }}
            >
              Per-phase status
            </div>
            {PHASE_ORDER.map(pid => {
              const status = plan.status_summary[pid] || 'pending';
              const willReRun = plan.order.includes(pid);
              return (
                <div
                  key={pid}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '4px 0',
                    borderBottom: '1px solid rgba(42,58,80,0.4)',
                  }}
                >
                  <div style={{ flex: '0 0 60px' }}>{pid}</div>
                  <div
                    style={{
                      flex: 1,
                      color: badgeColour(status),
                      textTransform: 'uppercase',
                      fontSize: 10,
                      letterSpacing: '0.06em',
                    }}
                  >
                    {status}
                  </div>
                  <div style={{ flex: '0 0 110px', textAlign: 'right' }}>
                    {willReRun ? (
                      <span style={{ color: 'var(--warning, #f59e0b)' }}>
                        will re-run
                      </span>
                    ) : (
                      <span style={{ color: 'var(--text3, #64748b)' }}>
                        —
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {plan.blocked_by_manual.length > 0 && (
            <div
              style={{
                padding: 10,
                marginBottom: 12,
                background: 'rgba(245,158,11,0.08)',
                border: '1px solid var(--warning, #f59e0b)',
                borderRadius: 4,
                color: 'var(--warning, #f59e0b)',
                fontSize: 11,
                lineHeight: 1.5,
              }}
            >
              Downstream manual phase(s) likely need rework:{' '}
              <b>{plan.blocked_by_manual.join(', ')}</b>. The AI pipeline
              will not touch these — PCB or FPGA artefacts must be redone by
              hand.
            </div>
          )}

          <button
            type="button"
            onClick={handleExecute}
            disabled={executing || plan.stale.length === 0}
            style={{
              width: '100%',
              background: plan.stale.length === 0
                ? 'transparent'
                : 'var(--teal, #00c6a7)',
              color: plan.stale.length === 0
                ? 'var(--text3, #64748b)'
                : 'var(--navy, #070b14)',
              border: '1px solid var(--teal, #00c6a7)',
              borderRadius: 4,
              padding: '8px 12px',
              fontSize: 12,
              fontFamily: 'DM Mono, JetBrains Mono, monospace',
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              cursor: executing ? 'wait' : plan.stale.length === 0 ? 'default' : 'pointer',
            }}
          >
            {executing
              ? 'Resetting…'
              : plan.stale.length === 0
                ? 'Nothing stale — no action needed'
                : `Re-run ${plan.stale.length} stale phase${plan.stale.length === 1 ? '' : 's'}`}
          </button>

          {lastResult && (
            <div
              style={{
                marginTop: 10,
                color: 'var(--teal, #00c6a7)',
                fontSize: 11,
                lineHeight: 1.5,
              }}
            >
              {lastResult}
            </div>
          )}

          <div
            style={{
              marginTop: 14,
              paddingTop: 10,
              borderTop: '1px solid var(--border, rgba(42,58,80,0.8))',
              color: 'var(--text3, #64748b)',
              lineHeight: 1.5,
              fontSize: 10,
            }}
          >
            Stale = phase completed against an older requirements lock than
            the current one. Determined by SHA-256 comparison of
            <code style={{ margin: '0 4px' }}>requirements_hash_at_completion</code>
            vs the active <code>requirements_hash</code>.
          </div>
        </>
      )}
    </div>
  );
}
