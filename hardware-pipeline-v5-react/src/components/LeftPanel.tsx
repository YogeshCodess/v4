import type { PhaseMeta, Statuses, DesignScope } from '../types';
import { SCOPE_LABELS } from '../types';
import { isUnlocked, isPhaseApplicable } from '../data/phases';

interface Props {
  phases: PhaseMeta[];
  selectedIdx: number;
  statuses: Statuses;
  completedIds: string[];
  stalePhaseIds?: string[];
  /** True once the user has clicked "Approve & Run Pipeline" — used to
   *  distinguish P1 "requirements captured (review pending)" from "approved & running" */
  pipelineStarted?: boolean;
  /** v20 — Stage 0 design scope (optional; when set, non-applicable phases are greyed out). */
  scope?: DesignScope;
  onSelect: (idx: number) => void;
  onLanding: () => void;
}

// Visual groups to add separators between sections
const GROUPS: { label: string; ids: string[] }[] = [
  { label: 'DESIGN', ids: ['P1', 'P2', 'P3', 'P4'] },
  { label: 'PCB LAYOUT', ids: ['P5'] },
  { label: 'FPGA', ids: ['P6', 'P7'] },
  { label: 'SOFTWARE', ids: ['P8a', 'P8b', 'P8c'] },
];

function getGroupLabel(phaseId: string): string | null {
  for (const g of GROUPS) {
    if (g.ids.includes(phaseId) && g.ids.indexOf(phaseId) === 0) return g.label;
  }
  return null;
}

export default function LeftPanel({ phases, selectedIdx, statuses, completedIds, stalePhaseIds = [], pipelineStarted = false, scope, onSelect, onLanding }: Props) {
  const completedCount = completedIds.length;
  const totalAI = phases.filter(p => p.auto).length;

  return (
    <div style={{
      width: 264, background: 'var(--navy)', borderRight: '1px solid var(--border2)',
      display: 'flex', flexDirection: 'column', flexShrink: 0,
      height: '100vh', position: 'sticky', top: 0,
    }}>
      {/* Logo + menu icon */}
      <div style={{ padding: '18px 16px 14px', borderBottom: '1px solid var(--border2)', position: 'relative' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
          {/* Clickable logo → home */}
          <button onClick={onLanding} title="Back to home" style={{
            background: 'transparent', border: 'none', cursor: 'pointer',
            textAlign: 'left', padding: 0, flex: 1,
          }}>
            <div style={{ fontSize: 10, color: 'var(--teal)', letterSpacing: '0.14em', marginBottom: 4 }}>
              DATA PATTERNS · CODE KNIGHTS
            </div>
            <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 17, fontWeight: 800, color: 'var(--text)' }}>
              Silicon to Software <span style={{ color: 'var(--teal)' }}>(S2S)</span>
            </div>
          </button>

          {/* P26.6 (2026-05-05): hamburger menu removed. LLM Settings is
              on the dashboard; New / Load Project also live on the
              dashboard; the logo above (onLanding) handles return to
              home. */}
        </div>

        {/* Mini progress bar */}
        <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ flex: 1, height: 3, background: 'var(--panel2)', borderRadius: 2, overflow: 'hidden' }}>
            <div style={{
              height: '100%', borderRadius: 2,
              background: 'var(--teal)',
              width: `${totalAI > 0 ? (completedCount / phases.length) * 100 : 0}%`,
              transition: 'width 0.5s ease',
            }} />
          </div>
          <span style={{ fontSize: 10, color: 'var(--text4)', fontFamily: "'DM Mono', monospace", whiteSpace: 'nowrap' }}>
            {completedCount}/{phases.length}
          </span>
        </div>
      </div>

      {/* Phase list */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '6px 8px' }}>
        {phases.map((phase, idx) => {
          const groupLabel = getGroupLabel(phase.id);
          const isActive = idx === selectedIdx;
          const isComplete = completedIds.includes(phase.id);
          const isStale = stalePhaseIds.includes(phase.id);
          const unlocked = isUnlocked(phase, completedIds);
          const status = statuses[phase.id] || 'pending';
          const isRunning = status === 'in_progress';
          const isFailed = status === 'failed';
          // v20 — scope awareness: phase is "not applicable" when current scope excludes it.
          // Such phases are visually dimmed and are not clickable (disabled).
          const applicable = isPhaseApplicable(phase, scope);
          const scopeLabel = scope ? SCOPE_LABELS[scope] : undefined;
          // Completed phases are always clickable even if downstream chain is locked.
          // Non-applicable phases are locked regardless of completion state (cannot be run
          // against a scope they don't belong to).
          const isLocked = (!applicable) || (!phase.manual && !unlocked && phase.id !== 'P1' && !isComplete && !isFailed);

          const textColor = isActive
            ? 'var(--text)'
            : isFailed
            ? '#ef4444'
            : isComplete
            ? 'var(--text2)'
            : isLocked
            ? 'var(--text4)'
            : 'var(--text3)';

          const subTextColor = phase.manual
            ? '#475569'
            : isFailed
            ? '#ef444499'
            : isRunning
            ? phase.color
            : isComplete
            ? `${phase.color}cc`
            : isActive
            ? `${phase.color}88`
            : isLocked
            ? 'var(--text4)'
            : `${phase.color}55`;

          return (
            <div key={phase.id}>
              {/* Group separator */}
              {groupLabel && (
                <div style={{
                  fontSize: 9, color: 'var(--text4)', letterSpacing: '0.14em',
                  padding: '10px 8px 5px', fontFamily: "'DM Mono', monospace",
                  display: 'flex', alignItems: 'center', gap: 8,
                }}>
                  {groupLabel}
                  <div style={{ flex: 1, height: 1, background: 'var(--border2)' }} />
                </div>
              )}

              <button
                key={phase.id}
                onClick={() => onSelect(idx)}
                title={
                  !applicable
                    ? `Not applicable for scope: ${scopeLabel ?? 'current scope'}`
                    : isLocked
                    ? `Complete previous phase first`
                    : phase.manual
                    ? `Completed in ${phase.externalTool || 'external EDA tool'}`
                    : phase.name
                }
                style={{
                  width: '100%',
                  background: isActive ? `${phase.color}12` : 'transparent',
                  border: `1px solid ${isActive ? phase.color + '50' : 'transparent'}`,
                  borderRadius: 7, padding: '9px 10px',
                  cursor: isLocked ? 'not-allowed' : 'pointer',
                  display: 'flex', alignItems: 'flex-start', gap: 10,
                  marginBottom: 2, transition: 'all 0.15s', textAlign: 'left',
                  opacity: isLocked ? 0.45 : 1,
                }}
                onMouseEnter={e => {
                  if (!isActive && !isLocked) {
                    e.currentTarget.style.background = `${phase.color}09`;
                    e.currentTarget.style.borderColor = `${phase.color}30`;
                  }
                }}
                onMouseLeave={e => {
                  if (!isActive) {
                    e.currentTarget.style.background = 'transparent';
                    e.currentTarget.style.borderColor = 'transparent';
                  }
                }}
              >
                {/* Circle icon */}
                <div style={{
                  width: 30, height: 30, borderRadius: '50%', flexShrink: 0, marginTop: 1,
                  background: isFailed
                    ? 'rgba(239,68,68,0.12)'
                    : isActive
                    ? `${phase.color}22`
                    : isComplete
                    ? `${phase.color}14`
                    : 'var(--panel2)',
                  border: `2px solid ${
                    isFailed
                      ? '#ef444480'
                      : isActive
                      ? phase.color
                      : isComplete
                      ? phase.color + '80'
                      : phase.manual
                      ? 'var(--panel3)'
                      : isLocked
                      ? 'var(--border2)'
                      : phase.color + '35'
                  }`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontFamily: "'Syne', sans-serif", fontSize: 11, fontWeight: 800,
                  color: isFailed
                    ? '#ef4444'
                    : isActive
                    ? phase.color
                    : isComplete
                    ? phase.color + 'cc'
                    : phase.manual
                    ? 'var(--text4)'
                    : isLocked
                    ? 'var(--panel3)'
                    : phase.color + '70',
                  transition: 'all 0.2s',
                  boxShadow: isFailed ? '0 0 10px rgba(239,68,68,0.3)' : isRunning ? `0 0 14px ${phase.color}55` : isActive ? `0 0 8px ${phase.color}25` : 'none',
                }}>
                  {isRunning ? (
                    <div style={{
                      width: 11, height: 11, borderRadius: '50%',
                      border: `2px solid ${phase.color}`,
                      borderTopColor: 'transparent',
                      animation: 'spin 0.8s linear infinite',
                    }} />
                  ) : isFailed ? (
                    <span style={{ fontSize: 13 }}>✕</span>
                  ) : isComplete && phase.id === 'P1' && !pipelineStarted ? (
                    // P1 requirements captured but not yet approved — show pending icon
                    <span style={{ fontSize: 12 }}>⭮</span>
                  ) : isComplete && !phase.manual ? (
                    <span style={{ fontSize: 13 }}>✓</span>
                  ) : phase.manual ? (
                    <span style={{ fontSize: 12 }}>⚙</span>
                  ) : phase.num}
                </div>

                {/* Label */}
                <div style={{ flex: 1, minWidth: 0, paddingTop: 1 }}>
                  <div style={{
                    fontSize: 12, color: textColor,
                    fontWeight: isActive ? 600 : 400,
                    lineHeight: 1.35,
                    transition: 'color 0.2s',
                    // Allow wrapping — no truncation
                    whiteSpace: 'normal',
                    wordBreak: 'break-word',
                  }}>
                    {phase.name}
                  </div>
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 5, marginTop: 3,
                    flexWrap: 'wrap',
                  }}>
                    <div style={{
                      fontSize: 10, color: !applicable ? 'var(--text4)' : subTextColor,
                      fontFamily: "'DM Mono', monospace",
                      letterSpacing: '0.04em',
                    }}>
                      {!applicable
                        ? 'NOT APPLICABLE'
                        : phase.manual
                        ? 'MANUAL / EDA'
                        : isFailed
                        ? '✕ Failed — click to retry'
                        : isRunning
                        ? '⚡ Running...'
                        : isComplete && phase.id === 'P1' && !pipelineStarted
                        ? '⭮ REVIEW READY'
                        : isComplete
                        ? '✓ Complete'
                        : '⚡ AUTO'}
                    </div>
                    {!isRunning && (
                      <div style={{ fontSize: 9, color: 'var(--text4)', fontFamily: "'DM Mono', monospace" }}>
                        {phase.time}
                      </div>
                    )}
                    {/* Stale warning chip */}
                    {isStale && !isRunning && (
                      <div style={{
                        fontSize: 9, padding: '1px 5px', borderRadius: 4,
                        background: 'rgba(245,158,11,0.12)',
                        border: '1px solid rgba(245,158,11,0.35)',
                        color: '#f59e0b',
                        fontFamily: "'DM Mono', monospace",
                        letterSpacing: '0.04em',
                        display: 'flex', alignItems: 'center', gap: 3,
                      }}>
                        ⚠ STALE
                      </div>
                    )}
                  </div>
                </div>
              </button>
            </div>
          );
        })}
      </div>

      {/* Footer */}
      <div style={{ padding: '10px 14px 12px', borderTop: '1px solid var(--border2)' }}>
        <div style={{ fontSize: 10, color: 'var(--text4)', lineHeight: 1.5 }}>
          <span style={{ color: 'var(--teal)' }}>{phases.filter(p => p.auto).length} AI phases</span>
          {' · '}
          <span>{phases.filter(p => p.manual).length} manual phases</span>
        </div>
      </div>
    </div>
  );
}
