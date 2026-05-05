import type { Project, Statuses, PhaseMeta } from '../types';

interface Props {
  project: Project | null;
  phases: PhaseMeta[];
  statuses: Statuses;
  stalePhaseIds?: string[];
  onRunPipeline?: () => void;
  onRerunStale?: (staleIds: string[]) => void;
  onShowDag?: () => void;
  pipelineRunning?: boolean;
  theme?: 'dark' | 'light';
  onToggleTheme?: () => void;
}

export default function MiniTopbar({ project, phases, statuses, stalePhaseIds = [], onRunPipeline, onRerunStale, onShowDag, pipelineRunning, theme = 'dark', onToggleTheme }: Props) {
  const completedCount = phases.filter(p => statuses[p.id] === 'completed').length;
  const runningPhase = phases.find(p => statuses[p.id] === 'in_progress');
  // All AI phases complete (excludes manual phases P5/P7)
  const aiPhases = phases.filter(p => !p.manual);
  const allAiDone = aiPhases.length > 0 && aiPhases.every(p => statuses[p.id] === 'completed');
  // Progress: when all AI phases are done, show 100% — manual phases don't block completion
  const aiCompletedCount = aiPhases.filter(p => statuses[p.id] === 'completed').length;
  const pct = allAiDone ? 100 : Math.round((aiCompletedCount / Math.max(aiPhases.length, 1)) * 100);
  const p1Done = statuses['P1'] === 'completed';
  // "All done" for gating purposes = all AI phases done (manual phases are external)
  const allDone = allAiDone;
  // Only show Run Pipeline when pipeline has already been started (P2+ has activity)
  // but is currently stopped — this is the recovery/restart case.
  // Hidden during initial Approve flow (user uses the Approve button in chat instead).
  const p2PlusActive = phases.filter(p => p.id !== 'P1').some(p =>
    ['completed', 'in_progress', 'failed'].includes(statuses[p.id] || ''));
  const showRunBtn = onRunPipeline && p1Done && !pipelineRunning && !allDone && p2PlusActive;

  return (
    <div style={{
      padding: '0 24px', borderBottom: '1px solid var(--border2)',
      background: 'var(--navy)', position: 'sticky', top: 0, zIndex: 5,
      minHeight: 52, display: 'flex', alignItems: 'center', gap: 12,
    }}>
      {/* Project name + type */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
        <span style={{
          fontSize: 14, fontWeight: 600, color: 'var(--text)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          maxWidth: 240,
        }}>
          {project?.name || 'No project'}
        </span>
      </div>

      {/* Re-run stale phases button — amber, shown when requirements changed after some phases ran */}
      {onRerunStale && stalePhaseIds.length > 0 && !pipelineRunning && (
        <button
          onClick={() => onRerunStale(stalePhaseIds)}
          title={`Re-run ${stalePhaseIds.join(', ')} — requirements updated since these ran`}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '5px 14px', borderRadius: 5,
            background: 'rgba(245,158,11,0.1)',
            border: '1px solid rgba(245,158,11,0.38)',
            color: '#f59e0b', fontSize: 11,
            fontFamily: "'DM Mono', monospace", fontWeight: 700,
            cursor: 'pointer', letterSpacing: '0.05em',
            transition: 'all 0.2s', flexShrink: 0,
          }}
          onMouseEnter={e => { e.currentTarget.style.background = 'rgba(245,158,11,0.2)'; e.currentTarget.style.boxShadow = '0 0 12px rgba(245,158,11,0.25)'; }}
          onMouseLeave={e => { e.currentTarget.style.background = 'rgba(245,158,11,0.1)'; e.currentTarget.style.boxShadow = 'none'; }}
        >
          ↺ Re-run {stalePhaseIds.length} stale
        </button>
      )}

      {/* Pipeline complete celebration */}
      {allAiDone && !pipelineRunning && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 7, flexShrink: 0,
          padding: '3px 12px', borderRadius: 20,
          background: 'rgba(0,198,167,0.1)',
          border: '1px solid rgba(0,198,167,0.35)',
          animation: 'pulse 2.5s ease infinite',
        }}>
          <span style={{ fontSize: 13 }}>✓</span>
          <span style={{ fontSize: 10, color: '#00c6a7', fontFamily: "'DM Mono', monospace", letterSpacing: '0.07em', fontWeight: 700 }}>
            PIPELINE COMPLETE
          </span>
        </div>
      )}

      {/* Running indicator */}
      {runningPhase && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          <div style={{
            width: 7, height: 7, borderRadius: '50%',
            background: runningPhase.color,
            boxShadow: `0 0 8px ${runningPhase.color}`,
            animation: 'pulse 1.5s ease infinite',
          }} />
          <span style={{ fontSize: 11, color: runningPhase.color, fontFamily: "'DM Mono', monospace", letterSpacing: '0.04em' }}>
            {runningPhase.code} running
          </span>
        </div>
      )}

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Phase progress pills */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 10, color: 'var(--text4)', fontFamily: "'DM Mono', monospace", marginRight: 4 }}>
          {pct}%
        </span>
        <div style={{ display: 'flex', gap: 2.5 }}>
          {phases.map(p => {
            const s = statuses[p.id];
            const isDone = s === 'completed';
            const isRunning = s === 'in_progress';
            return (
              <div
                key={p.id}
                title={`${p.code} — ${p.name}: ${s || 'pending'}`}
                style={{
                  width: 20, height: 6, borderRadius: 3,
                  background: isDone
                    ? p.color
                    : isRunning
                    ? p.color + '77'
                    : 'var(--panel3)',
                  transition: 'background 0.4s',
                  boxShadow: isRunning ? `0 0 6px ${p.color}55` : 'none',
                  position: 'relative',
                  overflow: isRunning ? 'hidden' : 'visible',
                }}
              >
                {isRunning && (
                  <div style={{
                    position: 'absolute', inset: 0, borderRadius: 3,
                    background: `linear-gradient(90deg, transparent, ${p.color}cc, transparent)`,
                    animation: 'shimmer 1.5s linear infinite',
                  }} />
                )}
              </div>
            );
          })}
        </div>
        <span style={{ fontSize: 10, color: 'var(--text4)', fontFamily: "'DM Mono', monospace", marginLeft: 4 }}>
          {aiCompletedCount}/{aiPhases.length}
        </span>
      </div>

      {/* P26 #23 (2026-05-04): pipeline DAG button hidden by user
          request. The viewer is now opened with Ctrl+Shift+M (Map),
          handled by the global keydown listener in App.tsx. The
          `onShowDag` prop is still threaded so the keyboard handler
          can call it via the same path. */}

      {/* Theme toggle */}
      {onToggleTheme && (
        <button
          onClick={onToggleTheme}
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 30, height: 30, borderRadius: 6,
            background: 'var(--panel2)',
            border: '1px solid var(--border)',
            color: 'var(--text2)',
            cursor: 'pointer', fontSize: 15,
            transition: 'all 0.2s', flexShrink: 0,
          }}
          onMouseEnter={e => {
            e.currentTarget.style.background = 'var(--panel3)';
            e.currentTarget.style.color = 'var(--teal)';
            e.currentTarget.style.borderColor = 'var(--teal-border)';
          }}
          onMouseLeave={e => {
            e.currentTarget.style.background = 'var(--panel2)';
            e.currentTarget.style.color = 'var(--text2)';
            e.currentTarget.style.borderColor = 'var(--border)';
          }}
        >
          {theme === 'dark' ? '☀' : '☽'}
        </button>
      )}
    </div>
  );
}
