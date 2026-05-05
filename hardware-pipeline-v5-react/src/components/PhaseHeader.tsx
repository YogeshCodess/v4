import type { PhaseMeta, PhaseStatusValue, CenterTab, DesignScope } from '../types';
import { isPhaseApplicable } from '../data/phases';

interface Props {
  phase: PhaseMeta;
  status: PhaseStatusValue;
  tab: CenterTab;
  onTabChange: (t: CenterTab) => void;
  onExecute?: () => void;
  pipelineRunning?: boolean;
  isStale?: boolean;
  /** True once the user has clicked "Approve & Run" at least once for this project.
   *  The ▶ Execute button is hidden until pipelineStarted — forcing the user to
   *  use the Approve & Run flow in the P1 Chat tab rather than accidentally
   *  running an individual phase before the pipeline has been approved. */
  pipelineStarted?: boolean;
  /** Wizard-selected design scope — when the phase is not applicable for this
   *  scope we suppress the Execute button and show a NOT APPLICABLE badge. */
  scope?: DesignScope | null;
  /** Real wall-clock seconds the phase took. Pulled from the backend's
   *  `phase_statuses[id].duration_seconds` field. Shown next to the
   *  status pill so the user always sees the elapsed time — even when
   *  a fast phase (P8c often 5-30 s) completes before the frontend
   *  elapsed counter has a chance to start. (P26 #20, 2026-04-26.) */
  durationSeconds?: number;
}

export default function PhaseHeader({ phase, status, tab, onTabChange, onExecute, pipelineRunning, isStale = false, pipelineStarted = false, scope, durationSeconds }: Props) {
  const isComplete = status === 'completed';
  const isRunning = status === 'in_progress';
  const isFailed = status === 'failed';
  const applicable = isPhaseApplicable(phase, scope ?? undefined);

  const tabs: { id: CenterTab; label: string; show: boolean }[] = [
    { id: 'chat' as const,      label: '⚡ Chat',      show: phase.id === 'P1' },
    { id: 'documents' as const, label: '📄 Documents', show: true },
  ].filter(t => t.show);

  // Status pill config
  const statusConfig = isRunning
    ? { label: 'RUNNING', color: '#f59e0b', bg: 'rgba(245,158,11,0.1)', border: 'rgba(245,158,11,0.3)', animate: true }
    : isComplete
    ? { label: 'COMPLETE', color: phase.color, bg: `${phase.color}12`, border: `${phase.color}33`, animate: false }
    : isFailed
    ? { label: 'FAILED', color: '#dc2626', bg: 'rgba(220,38,38,0.1)', border: 'rgba(220,38,38,0.3)', animate: false }
    : { label: phase.manual ? 'MANUAL / EXTERNAL' : 'PENDING', color: 'var(--text4)', bg: 'var(--panel2)', border: 'var(--panel3)', animate: false };

  return (
    <div style={{ borderBottom: '1px solid var(--border2)' }}>
      {/* Phase title area */}
      <div style={{
        padding: '20px 24px 16px',
        background: `linear-gradient(180deg, ${phase.color}06 0%, transparent 100%)`,
      }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14 }}>
          {/* Phase circle */}
          <div style={{
            width: 48, height: 48, borderRadius: '50%', flexShrink: 0,
            background: isComplete
              ? `${phase.color}18`
              : `${phase.color}12`,
            border: `2px solid ${isRunning ? phase.color : isComplete ? phase.color + 'aa' : phase.color + '44'}`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: "'Syne', sans-serif", fontWeight: 800, fontSize: 17,
            color: isComplete ? phase.color : isRunning ? phase.color : `${phase.color}bb`,
            boxShadow: isRunning
              ? `0 0 20px ${phase.color}44, 0 0 40px ${phase.color}18`
              : isComplete
              ? `0 0 12px ${phase.color}25`
              : 'none',
            transition: 'all 0.3s',
          }}>
            {isRunning ? (
              <div style={{
                width: 17, height: 17, borderRadius: '50%',
                border: `2.5px solid ${phase.color}`,
                borderTopColor: 'transparent',
                animation: 'spin 0.8s linear infinite',
              }} />
            ) : isComplete && !phase.manual ? (
              <span>✓</span>
            ) : phase.manual ? (
              <span style={{ fontSize: 20 }}>⚙</span>
            ) : phase.num}
          </div>

          {/* Title + meta */}
          <div style={{ flex: 1 }}>
            {/* Code + badges row */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
              <span style={{
                fontSize: 11, color: phase.color,
                letterSpacing: '0.14em', fontFamily: "'DM Mono', monospace",
                fontWeight: 500,
              }}>
                {phase.code}
              </span>

              {/* Status pill */}
              <span style={{
                fontSize: 10, padding: '2px 9px', borderRadius: 12,
                color: statusConfig.color, background: statusConfig.bg,
                border: `1px solid ${statusConfig.border}`,
                letterSpacing: '0.06em',
                animation: statusConfig.animate ? 'pulse 1.5s ease infinite' : 'none',
              }}>
                {statusConfig.label}
              </span>

              {/* Auto / Manual badge */}
              <span style={{
                fontSize: 10, padding: '2px 9px', borderRadius: 12,
                color: phase.auto ? `${phase.color}cc` : 'var(--text4)',
                background: phase.auto ? `${phase.color}0e` : 'var(--panel2)',
                border: `1px solid ${phase.auto ? phase.color + '28' : 'var(--panel3)'}`,
              }}>
                {phase.manual ? 'EXTERNAL' : '⚡ AUTOMATED'}
              </span>

              {/* Time estimate (replaced by REAL elapsed once the phase
                  is completed and the backend has reported a duration). */}
              <span style={{
                fontSize: 11, color: isComplete && durationSeconds ? phase.color : 'var(--text4)',
                fontFamily: "'DM Mono', monospace",
              }}>
                {isComplete && durationSeconds
                  ? `✓ ran in ${
                      durationSeconds < 60
                        ? `${Math.round(durationSeconds)}s`
                        : `${Math.floor(durationSeconds / 60)}m ${Math.round(durationSeconds % 60)}s`
                    }`
                  : phase.time}
              </span>

              {/* Not-applicable badge — phase falls outside the project's scope */}
              {!applicable && (
                <span style={{
                  fontSize: 10, padding: '2px 9px', borderRadius: 12,
                  color: 'var(--text4)',
                  background: 'var(--panel2)',
                  border: '1px solid var(--panel3)',
                  letterSpacing: '0.06em',
                }}>
                  NOT APPLICABLE
                </span>
              )}

              {/* Stale badge */}
              {isStale && !isRunning && (
                <span style={{
                  fontSize: 10, padding: '2px 9px', borderRadius: 12,
                  color: '#f59e0b',
                  background: 'rgba(245,158,11,0.1)',
                  border: '1px solid rgba(245,158,11,0.3)',
                  letterSpacing: '0.06em',
                }}>
                  ⚠ STALE
                </span>
              )}
            </div>

            {/* Phase name */}
            <div style={{
              fontFamily: "'Syne', sans-serif", fontSize: 18, fontWeight: 800,
              color: 'var(--text)', lineHeight: 1.2, marginBottom: 4,
            }}>
              {phase.name}
            </div>

            {/* Tagline */}
            <div style={{ fontSize: 12.5, color: 'var(--text3)', lineHeight: 1.5, marginBottom: onExecute ? 10 : 0 }}>
              {phase.tagline}
            </div>

            {/* Execute button — shown ONLY for failed phases (retry) once the pipeline has
                been started. We intentionally exclude 'pending' status here: when the full
                pipeline is running, pending phases should not show Execute because the
                pipeline will reach them automatically. Showing Execute during the brief
                between-phase gap (P6 complete → P7a starting) would be confusing/dangerous.
                Before pipelineStarted (standalone mode), both pending and failed are allowed. */}
            {onExecute && !phase.manual && phase.id !== 'P1' && !pipelineRunning && applicable && (
              (pipelineStarted && status === 'failed') ||
              (!pipelineStarted && (status === 'pending' || status === 'failed'))
            ) && (
              <button
                onClick={onExecute}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 7,
                  padding: '8px 18px', borderRadius: 6,
                  background: phase.color, border: 'none',
                  color: '#070b14', fontSize: 12,
                  fontFamily: "'DM Mono', monospace", fontWeight: 700,
                  cursor: 'pointer', letterSpacing: '0.05em',
                  boxShadow: `0 0 16px ${phase.color}44`,
                  transition: 'all 0.2s',
                }}
                onMouseEnter={e => { e.currentTarget.style.boxShadow = `0 0 28px ${phase.color}66`; e.currentTarget.style.transform = 'translateY(-1px)'; }}
                onMouseLeave={e => { e.currentTarget.style.boxShadow = `0 0 16px ${phase.color}44`; e.currentTarget.style.transform = 'translateY(0)'; }}
              >
                ▶ Execute {phase.code}
              </button>
            )}

            {/* Stale warning + re-run button */}
            {isStale && !isRunning && !pipelineRunning && applicable && (
              <div style={{
                marginTop: 12,
                padding: '10px 14px', borderRadius: 7,
                background: 'rgba(245,158,11,0.07)',
                border: '1px solid rgba(245,158,11,0.28)',
                display: 'flex', alignItems: 'center', gap: 12,
                flexWrap: 'wrap',
              }}>
                <div style={{ flex: 1, minWidth: 200 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#f59e0b', marginBottom: 2, fontFamily: "'DM Mono', monospace" }}>
                    ⚠ Requirements updated
                  </div>
                  <div style={{ fontSize: 11.5, color: 'var(--text3)', lineHeight: 1.5 }}>
                    P1 was re-approved after this phase last ran. Re-run to regenerate with updated requirements.
                  </div>
                </div>
                {onExecute && !phase.manual && (
                  <button
                    onClick={onExecute}
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: 7,
                      padding: '7px 16px', borderRadius: 6,
                      background: 'rgba(245,158,11,0.15)',
                      border: '1px solid rgba(245,158,11,0.4)',
                      color: '#f59e0b', fontSize: 11.5,
                      fontFamily: "'DM Mono', monospace", fontWeight: 700,
                      cursor: 'pointer', letterSpacing: '0.05em',
                      transition: 'all 0.2s', whiteSpace: 'nowrap',
                    }}
                    onMouseEnter={e => { e.currentTarget.style.background = 'rgba(245,158,11,0.22)'; e.currentTarget.style.transform = 'translateY(-1px)'; }}
                    onMouseLeave={e => { e.currentTarget.style.background = 'rgba(245,158,11,0.15)'; e.currentTarget.style.transform = 'translateY(0)'; }}
                  >
                    ↺ Re-run {phase.code}
                  </button>
                )}
              </div>
            )}

            {/* Running indicator — shown below badge row while phase executes */}
            {!phase.manual && phase.id !== 'P1' && isRunning && (
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, marginTop: 10 }}>
                <div style={{ width: 10, height: 10, borderRadius: '50%', border: `2px solid ${phase.color}`, borderTopColor: 'transparent', animation: 'spin 0.8s linear infinite' }} />
                <span style={{ fontSize: 12, color: phase.color, fontFamily: "'DM Mono', monospace" }}>
                  AI agent running — see Documents tab for live progress
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Manual phase info banner */}
        {phase.manual && (
          <div style={{
            marginTop: 14,
            padding: '10px 14px', borderRadius: 7,
            background: 'rgba(71,85,105,0.12)', border: '1px solid rgba(71,85,105,0.3)',
            fontSize: 12, color: 'var(--text3)',
            display: 'flex', gap: 10, alignItems: 'flex-start',
          }}>
            <span style={{ fontSize: 16, flexShrink: 0 }}>⚙</span>
            <div>
              <div style={{ fontWeight: 600, color: 'var(--text2)', marginBottom: 2 }}>
                Completed in {phase.externalTool || 'external EDA tool'}
              </div>
              <div>This phase is performed manually. The pipeline continues automatically once the external design is complete.</div>
            </div>
          </div>
        )}
      </div>

      {/* Tab bar */}
      {tabs.length > 0 && (
        <div style={{
          display: 'flex', padding: '0 24px',
          borderTop: '1px solid var(--border2)',
          background: 'var(--navy)',
        }}>
          {tabs.map(t => (
            <button key={t.id} onClick={() => onTabChange(t.id)} style={{
              padding: '10px 20px', fontSize: 12.5,
              fontFamily: "'DM Mono', monospace",
              cursor: 'pointer', border: 'none', background: 'transparent',
              color: tab === t.id ? phase.color : 'var(--text4)',
              borderBottom: `2.5px solid ${tab === t.id ? phase.color : 'transparent'}`,
              transition: 'all 0.15s', whiteSpace: 'nowrap', letterSpacing: '0.04em',
              marginBottom: -1,
            }}
              onMouseEnter={e => { if (tab !== t.id) e.currentTarget.style.color = 'var(--text2)'; }}
              onMouseLeave={e => { if (tab !== t.id) e.currentTarget.style.color = 'var(--text4)'; }}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
