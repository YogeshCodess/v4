import { useEffect, useState, useCallback } from 'react';
import { api } from '../api';

/**
 * Judge Mode — Ctrl+Shift+J toggles a floating overlay that exposes the
 * deterministic verification evidence for the currently-loaded project:
 *
 *   - requirements_lock SHA-256 hash and frozen_at timestamp
 *   - stale-phase list
 *   - red-team audit PASS/FAIL
 *   - cascade NF / gain computed vs. claimed
 *   - number of citations that resolved
 *
 * The component is read-only and pulls directly from /api/v1/projects/{id}/status
 * plus whatever verification artefacts the backend has persisted for P1.
 *
 * The feature is aimed at demo judges who need to see *why* the pipeline's
 * answers are trustworthy without having to read the codebase.
 */

interface VerificationSnapshot {
  project_id: number;
  requirements_hash: string | null;
  requirements_frozen_at: string | null;
  stale_phase_ids: string[];
  audit_overall_pass: boolean | null;
  audit_cascade_errors: number | null;
  audit_unresolved_citations: number | null;
  computed_nf_db: number | null;
  claimed_nf_db: number | null;
  computed_gain_db: number | null;
  claimed_gain_db: number | null;
  resolved_citation_count: number | null;
  part_check_count: number | null;
}

/** P26 #21 (2026-04-26): the requirements_frozen_at field comes from
 *  the backend as an ISO-8601 string with microseconds + tz offset
 *  (e.g. "2026-04-26T08:01:05.354398+00:00"). Display that verbatim
 *  is wide and unreadable. Render as "26 Apr 2026 · 08:01 UTC". */
function fmtFrozenAt(iso: string | null): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const dd = String(d.getUTCDate()).padStart(2, '0');
    const mon = months[d.getUTCMonth()];
    const yyyy = d.getUTCFullYear();
    const hh = String(d.getUTCHours()).padStart(2, '0');
    const mm = String(d.getUTCMinutes()).padStart(2, '0');
    return `${dd} ${mon} ${yyyy} · ${hh}:${mm} UTC`;
  } catch {
    return iso;
  }
}

const EMPTY: VerificationSnapshot = {
  project_id: 0,
  requirements_hash: null,
  requirements_frozen_at: null,
  stale_phase_ids: [],
  audit_overall_pass: null,
  audit_cascade_errors: null,
  audit_unresolved_citations: null,
  computed_nf_db: null,
  claimed_nf_db: null,
  computed_gain_db: null,
  claimed_gain_db: null,
  resolved_citation_count: null,
  part_check_count: null,
};

interface Props {
  projectId: number | null;
}

export default function JudgeMode({ projectId }: Props) {
  const [open, setOpen] = useState(false);
  const [snap, setSnap] = useState<VerificationSnapshot>(EMPTY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // E1 — wipe-state button UI state
  const [resetConfirm, setResetConfirm] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetInfo, setResetInfo] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!projectId) {
      setSnap(EMPTY);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const base =
        window.location.protocol === 'file:' ||
        (window.location.port !== '8000' && window.location.port !== '')
          ? 'http://localhost:8000'
          : '';
      const r = await fetch(`${base}/api/v1/projects/${projectId}/status`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();

      const audit = j?.audit_report_summary || j?.audit_summary || {};
      const cascade = j?.cascade_summary || {};

      setSnap({
        project_id: projectId,
        requirements_hash: j?.requirements_hash || null,
        requirements_frozen_at: j?.requirements_frozen_at || null,
        stale_phase_ids: j?.stale_phase_ids || [],
        audit_overall_pass:
          typeof audit?.overall_pass === 'boolean' ? audit.overall_pass : null,
        audit_cascade_errors:
          typeof audit?.cascade_errors === 'number'
            ? audit.cascade_errors
            : null,
        audit_unresolved_citations:
          typeof audit?.unresolved_citations === 'number'
            ? audit.unresolved_citations
            : null,
        computed_nf_db:
          typeof cascade?.computed_nf_db === 'number'
            ? cascade.computed_nf_db
            : null,
        claimed_nf_db:
          typeof cascade?.claimed_nf_db === 'number'
            ? cascade.claimed_nf_db
            : null,
        computed_gain_db:
          typeof cascade?.computed_gain_db === 'number'
            ? cascade.computed_gain_db
            : null,
        claimed_gain_db:
          typeof cascade?.claimed_gain_db === 'number'
            ? cascade.claimed_gain_db
            : null,
        resolved_citation_count:
          typeof j?.resolved_citation_count === 'number'
            ? j.resolved_citation_count
            : null,
        part_check_count:
          typeof j?.part_check_count === 'number'
            ? j.part_check_count
            : null,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  // Keyboard shortcut — Ctrl+Shift+J.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && (e.key === 'J' || e.key === 'j')) {
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

  // Refresh whenever the panel opens OR the project changes while it's open.
  useEffect(() => {
    if (open) refresh();
  }, [open, projectId, refresh]);

  // E1 — wipe-state handler. Double-click pattern: first click arms the
  // confirm, second click performs the reset. Keeps the overlay read-only
  // until the user explicitly opts in.
  const handleReset = useCallback(async () => {
    if (!projectId) return;
    if (!resetConfirm) {
      setResetConfirm(true);
      setResetInfo(null);
      // Auto-disarm after 5s so an accidental click doesn't stay loaded.
      window.setTimeout(() => setResetConfirm(false), 5000);
      return;
    }
    setResetting(true);
    setResetConfirm(false);
    setError(null);
    try {
      const r = await api.resetState(projectId);
      const counts = r?.counts || {};
      const pieces: string[] = [];
      if (counts.phase_statuses) pieces.push(`${counts.phase_statuses} phase statuses`);
      if (counts.conversation_history) pieces.push(`${counts.conversation_history} messages`);
      if (counts.design_parameters) pieces.push(`${counts.design_parameters} design params`);
      setResetInfo(r.was_non_empty
        ? `Cleared ${pieces.join(', ') || 'lock columns'}.`
        : 'Project was already clean.');
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setResetting(false);
    }
  }, [projectId, resetConfirm, refresh]);

  if (!open) return null;

  const nfDelta =
    snap.computed_nf_db != null && snap.claimed_nf_db != null
      ? Math.abs(snap.computed_nf_db - snap.claimed_nf_db).toFixed(2)
      : '—';
  const gainDelta =
    snap.computed_gain_db != null && snap.claimed_gain_db != null
      ? Math.abs(snap.computed_gain_db - snap.claimed_gain_db).toFixed(2)
      : '—';

  const passBadge = (ok: boolean | null) =>
    ok === null ? (
      <span style={{ color: 'var(--text3)' }}>—</span>
    ) : ok ? (
      <span style={{ color: 'var(--teal, #00c6a7)' }}>PASS</span>
    ) : (
      <span style={{ color: 'var(--danger, #dc2626)' }}>FAIL</span>
    );

  return (
    <div
      role="dialog"
      aria-label="Judge Mode verification panel"
      style={{
        position: 'fixed',
        top: 72,
        right: 24,
        width: 420,
        maxHeight: '75vh',
        overflow: 'auto',
        zIndex: 2000,
        background: 'var(--panel, #1a2235)',
        color: 'var(--text, #e2e8f0)',
        border: '1px solid var(--teal, #00c6a7)',
        borderRadius: 8,
        boxShadow: '0 0 28px rgba(0,198,167,0.25)',
        padding: 20,
        fontFamily:
          'DM Mono, JetBrains Mono, "Fira Code", "Courier New", monospace',
        fontSize: 12,
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
          Judge Mode
        </h3>
        <span style={{ color: 'var(--text3, #64748b)', fontSize: 11 }}>
          Ctrl+Shift+J · Esc to close
        </span>
      </div>

      {error && (
        <div style={{ color: 'var(--danger, #dc2626)', marginBottom: 8 }}>
          {error}
        </div>
      )}
      {loading && (
        <div style={{ color: 'var(--text3, #64748b)', marginBottom: 8 }}>
          Refreshing…
        </div>
      )}

      {!projectId ? (
        <div style={{ color: 'var(--text3, #64748b)' }}>
          No project loaded. Open a project to see verification evidence.
        </div>
      ) : (
        <>
          <Row label="Project ID" value={String(snap.project_id)} />
          <Row
            label="Requirements SHA-256"
            value={
              snap.requirements_hash
                ? snap.requirements_hash.slice(0, 12) + '…'
                : '— (P1 not finalised)'
            }
          />
          <Row
            label="Frozen at"
            value={fmtFrozenAt(snap.requirements_frozen_at)}
          />
          <Row
            label="Stale phases"
            value={
              snap.stale_phase_ids.length
                ? snap.stale_phase_ids.join(', ')
                : 'none'
            }
          />

          <Divider />

          <RowReact
            label="Red-team audit"
            node={passBadge(snap.audit_overall_pass)}
          />
          <Row
            label="Cascade errors"
            value={
              snap.audit_cascade_errors != null
                ? String(snap.audit_cascade_errors)
                : '—'
            }
          />
          <Row
            label="Unresolved citations"
            value={
              snap.audit_unresolved_citations != null
                ? String(snap.audit_unresolved_citations)
                : '—'
            }
          />

          <Divider />

          <Row
            label="NF (dB) computed / claimed"
            value={
              snap.computed_nf_db != null && snap.claimed_nf_db != null
                ? `${snap.computed_nf_db.toFixed(2)} / ${snap.claimed_nf_db.toFixed(2)} (Δ ${nfDelta})`
                : '—'
            }
          />
          <Row
            label="Gain (dB) computed / claimed"
            value={
              snap.computed_gain_db != null && snap.claimed_gain_db != null
                ? `${snap.computed_gain_db.toFixed(2)} / ${snap.claimed_gain_db.toFixed(2)} (Δ ${gainDelta})`
                : '—'
            }
          />
          <Row
            label="Citations resolved"
            value={
              snap.resolved_citation_count != null
                ? String(snap.resolved_citation_count)
                : '—'
            }
          />
          <Row
            label="Parts checked"
            value={
              snap.part_check_count != null
                ? String(snap.part_check_count)
                : '—'
            }
          />

          <div
            style={{
              marginTop: 14,
              paddingTop: 10,
              borderTop: '1px solid var(--border, rgba(42,58,80,0.8))',
              color: 'var(--text3, #64748b)',
              lineHeight: 1.5,
            }}
          >
            Every number here is computed deterministically (cascade
            validator, citation DB, red-team audit). None of it comes from
            the LLM. Press Ctrl+Shift+J again to close.
          </div>

          {/* E1 — Wipe-state button */}
          <div
            style={{
              marginTop: 12,
              paddingTop: 10,
              borderTop: '1px dashed var(--border, rgba(42,58,80,0.8))',
            }}
          >
            <button
              type="button"
              onClick={handleReset}
              disabled={resetting || !projectId}
              style={{
                background: resetConfirm
                  ? 'var(--danger, #dc2626)'
                  : 'transparent',
                color: resetConfirm
                  ? 'white'
                  : 'var(--danger, #dc2626)',
                border: '1px solid var(--danger, #dc2626)',
                borderRadius: 4,
                padding: '6px 12px',
                fontSize: 11,
                fontFamily: 'DM Mono, JetBrains Mono, monospace',
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                cursor: resetting ? 'wait' : 'pointer',
                width: '100%',
              }}
            >
              {resetting
                ? 'Clearing…'
                : resetConfirm
                  ? 'Click again to confirm — this wipes all phase output'
                  : 'Clear project state (judge-mode reset)'}
            </button>
            {resetInfo && (
              <div
                style={{
                  marginTop: 8,
                  color: 'var(--teal, #00c6a7)',
                  fontSize: 11,
                }}
              >
                {resetInfo}
              </div>
            )}
            <div
              style={{
                marginTop: 8,
                color: 'var(--text3, #64748b)',
                fontSize: 10,
                lineHeight: 1.5,
              }}
            >
              Clears phase statuses, chat history, design parameters, and the
              requirements lock. Project identity (name, design type) is
              preserved. Safe to run between demos.
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', marginBottom: 6, gap: 8 }}>
      <div
        style={{
          flex: '0 0 180px',
          color: 'var(--text3, #64748b)',
          textTransform: 'uppercase',
          fontSize: 10,
          letterSpacing: '0.06em',
          paddingTop: 2,
        }}
      >
        {label}
      </div>
      <div style={{ flex: 1, wordBreak: 'break-all' }}>{value}</div>
    </div>
  );
}

function RowReact({ label, node }: { label: string; node: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', marginBottom: 6, gap: 8 }}>
      <div
        style={{
          flex: '0 0 180px',
          color: 'var(--text3, #64748b)',
          textTransform: 'uppercase',
          fontSize: 10,
          letterSpacing: '0.06em',
          paddingTop: 2,
        }}
      >
        {label}
      </div>
      <div style={{ flex: 1 }}>{node}</div>
    </div>
  );
}

function Divider() {
  return (
    <div
      style={{
        margin: '10px 0',
        borderTop: '1px dashed var(--border, rgba(42,58,80,0.8))',
      }}
    />
  );
}
