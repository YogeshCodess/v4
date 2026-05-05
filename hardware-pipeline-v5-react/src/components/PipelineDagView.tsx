import type { PhaseMeta, Statuses } from '../types';
import { PHASES } from '../data/phases';

/**
 * Pipeline DAG view.
 *
 * Renders the full phase dependency graph as an interactive SVG:
 *
 *   P1 → P2 → P3 → P4 → P5
 *                   ↓
 *                   P6 → P7 → P7a
 *                   ↓
 *                   P8a → P8b → P8c
 *
 * When a project is loaded, nodes are coloured by phase_statuses
 * so the operator can see the full pipeline state at a glance
 * instead of scrolling the sidebar. Clicking a node jumps back
 * to the pipeline view selecting that phase.
 */

/** Dependency edges — source phase feeds the listed targets. */
const DEPENDENCIES: Record<string, string[]> = {
  P1:  ['P2'],
  P2:  ['P3'],
  P3:  ['P4'],
  P4:  ['P5', 'P6', 'P8a'],
  P6:  ['P7'],
  P7:  ['P7a'],
  P8a: ['P8b'],
  P8b: ['P8c'],
};

/** Grid layout — (col, row) positions on a 6×4 grid.
 *  Columns grow left→right by depth; rows 0–3 separate the three
 *  downstream branches (PCB, FPGA/DSP, SW). */
const LAYOUT: Record<string, { col: number; row: number }> = {
  P1:  { col: 0, row: 1 },
  P2:  { col: 1, row: 1 },
  P3:  { col: 2, row: 1 },
  P4:  { col: 3, row: 1 },
  P5:  { col: 4, row: 0 },   // PCB Layout (manual)
  P6:  { col: 4, row: 1 },   // GLR
  P7:  { col: 5, row: 1 },   // FPGA Design (manual)
  P7a: { col: 6, row: 1 },   // Register Map
  P8a: { col: 4, row: 2 },   // SRS
  P8b: { col: 5, row: 2 },   // SDD
  P8c: { col: 6, row: 2 },   // Code Review
};

const CELL_W = 140;
const CELL_H = 100;
const PAD_X = 48;
const PAD_Y = 48;
const NODE_W = 112;
const NODE_H = 64;

// Status → colour/glyph. Synced with LeftPanel conventions.
const STATUS_STYLE: Record<string, { ring: string; fill: string; glyph: string }> = {
  completed:     { ring: '#10b981', fill: 'rgba(16,185,129,0.14)',  glyph: '✓' },
  in_progress:   { ring: '#00c6a7', fill: 'rgba(0,198,167,0.14)',   glyph: '◐' },
  draft_pending: { ring: '#f59e0b', fill: 'rgba(245,158,11,0.12)',  glyph: '~' },
  failed:        { ring: '#dc2626', fill: 'rgba(220,38,38,0.12)',   glyph: '✗' },
  pending:       { ring: '#64748b', fill: 'transparent',            glyph: '·' },
};

function nodeCenter(id: string): { x: number; y: number } {
  const pos = LAYOUT[id];
  if (!pos) return { x: 0, y: 0 };
  return {
    x: PAD_X + pos.col * CELL_W + NODE_W / 2,
    y: PAD_Y + pos.row * CELL_H + NODE_H / 2,
  };
}

interface Props {
  statuses: Statuses;
  selectedId?: string | null;
  onSelect?: (phaseId: string) => void;
  onClose?: () => void;
}

export default function PipelineDagView({ statuses, selectedId, onSelect, onClose }: Props) {
  const maxCol = Math.max(...Object.values(LAYOUT).map(p => p.col));
  const maxRow = Math.max(...Object.values(LAYOUT).map(p => p.row));
  const vbW = PAD_X * 2 + (maxCol + 1) * CELL_W;
  const vbH = PAD_Y * 2 + (maxRow + 1) * CELL_H;

  // Phases in a map keyed by id for quick lookup
  const phaseById: Record<string, PhaseMeta> = Object.fromEntries(
    PHASES.map(p => [p.id, p]),
  );

  const edges: Array<{ from: string; to: string }> = [];
  for (const [from, tos] of Object.entries(DEPENDENCIES)) {
    for (const to of tos) edges.push({ from, to });
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(7,11,20,0.94)',
      display: 'flex', flexDirection: 'column', zIndex: 9998,
    }}>
      {/* Header */}
      <div style={{
        padding: '20px 28px 14px',
        borderBottom: '1px solid var(--border2)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div>
          <div style={{
            fontFamily: 'Syne', fontSize: 22, fontWeight: 800,
            color: 'var(--text)',
          }}>Pipeline Dependency Map</div>
          <div style={{
            fontSize: 12, color: 'var(--text3)', marginTop: 3,
            fontFamily: "'DM Mono', monospace", letterSpacing: 0.3,
          }}>
            P1 → P2 → P3 → P4 fans out to PCB (P5), FPGA (P6 → P7 → P7a), and Software (P8a → P8b → P8c)
          </div>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            style={{
              background: 'transparent', border: '1px solid var(--border2)',
              color: 'var(--text3)', padding: '6px 14px', borderRadius: 5,
              fontSize: 11, fontFamily: "'DM Mono', monospace",
              cursor: 'pointer', letterSpacing: 0.5,
            }}
          >✕ CLOSE</button>
        )}
      </div>

      {/* Legend */}
      <div style={{
        padding: '10px 28px', display: 'flex', gap: 18, flexWrap: 'wrap',
        fontSize: 11, color: 'var(--text3)',
        fontFamily: "'DM Mono', monospace",
        borderBottom: '1px solid var(--border2)',
      }}>
        {(['completed', 'in_progress', 'draft_pending', 'failed', 'pending'] as const).map(s => (
          <span key={s} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{
              display: 'inline-block', width: 10, height: 10, borderRadius: '50%',
              border: `1.5px solid ${STATUS_STYLE[s].ring}`,
              background: STATUS_STYLE[s].fill,
            }} />
            {s.replace('_', ' ')}
          </span>
        ))}
      </div>

      {/* SVG canvas */}
      <div style={{ flex: 1, overflow: 'auto', padding: 20 }}>
        <svg width="100%" viewBox={`0 0 ${vbW} ${vbH}`}
             style={{ display: 'block', fontFamily: "'DM Mono', monospace" }}>

          {/* Grid watermark */}
          <defs>
            <pattern id="dag-grid" width={CELL_W / 2} height={CELL_H / 2}
                     patternUnits="userSpaceOnUse">
              <path d={`M ${CELL_W / 2} 0 L 0 0 0 ${CELL_H / 2}`}
                    fill="none" stroke="rgba(148,163,184,0.05)" strokeWidth={1} />
            </pattern>
          </defs>
          <rect width={vbW} height={vbH} fill="url(#dag-grid)" />

          {/* Edges — drawn first so nodes sit on top */}
          {edges.map(({ from, to }, i) => {
            const s = nodeCenter(from);
            const e = nodeCenter(to);
            // Bezier curve for visual flow; flat horizontals for same-row edges.
            const sameRow = LAYOUT[from].row === LAYOUT[to].row;
            const sx = s.x + NODE_W / 2;
            const ex = e.x - NODE_W / 2;
            const mid = (sx + ex) / 2;
            const d = sameRow
              ? `M ${sx} ${s.y} L ${ex} ${e.y}`
              : `M ${sx} ${s.y} C ${mid} ${s.y}, ${mid} ${e.y}, ${ex} ${e.y}`;
            const fromStatus = statuses[from] || 'pending';
            const stroke = fromStatus === 'completed' ? '#10b981'
                         : fromStatus === 'in_progress' ? '#00c6a7'
                         : fromStatus === 'failed' ? '#dc2626'
                         : 'rgba(148,163,184,0.4)';
            return (
              <g key={`e-${i}`}>
                <path d={d} stroke={stroke} strokeWidth={1.8} fill="none"
                      strokeDasharray={fromStatus === 'pending' ? '4 3' : undefined}
                      markerEnd={`url(#arrow-${fromStatus})`} />
              </g>
            );
          })}

          {/* Arrow markers (one per status colour) */}
          <defs>
            {(['completed', 'in_progress', 'failed', 'pending', 'draft_pending'] as const).map(s => (
              <marker key={s} id={`arrow-${s}`}
                      viewBox="0 0 10 10" refX="9" refY="5"
                      markerWidth="6" markerHeight="6" orient="auto">
                <path d="M0,0 L10,5 L0,10 z"
                      fill={
                        s === 'completed' ? '#10b981' :
                        s === 'in_progress' ? '#00c6a7' :
                        s === 'failed' ? '#dc2626' :
                        s === 'draft_pending' ? '#f59e0b' :
                        'rgba(148,163,184,0.5)'
                      } />
              </marker>
            ))}
          </defs>

          {/* Nodes */}
          {Object.entries(LAYOUT).map(([id, pos]) => {
            const phase = phaseById[id];
            if (!phase) return null;
            const status = (statuses[id] || 'pending') as keyof typeof STATUS_STYLE;
            const style = STATUS_STYLE[status] || STATUS_STYLE.pending;
            const x = PAD_X + pos.col * CELL_W;
            const y = PAD_Y + pos.row * CELL_H;
            const isSelected = selectedId === id;
            return (
              <g key={id}
                 onClick={() => onSelect && onSelect(id)}
                 style={{ cursor: onSelect ? 'pointer' : 'default' }}>
                <rect
                  x={x} y={y} width={NODE_W} height={NODE_H} rx={8}
                  fill={style.fill}
                  stroke={isSelected ? '#ffffff' : style.ring}
                  strokeWidth={isSelected ? 2.5 : 1.5}
                  filter={isSelected ? 'drop-shadow(0 0 12px rgba(255,255,255,0.25))' : undefined}
                />
                {/* Manual / auto badge */}
                {phase.manual && (
                  <text x={x + NODE_W - 8} y={y + 14}
                        fill="rgba(148,163,184,0.8)" fontSize={8}
                        textAnchor="end" letterSpacing={0.5}>MANUAL</text>
                )}
                {/* Code (P01 / P08a) */}
                <text x={x + 10} y={y + 20}
                      fill={phase.color} fontSize={11} fontWeight={700}
                      letterSpacing={0.6}>{phase.code}</text>
                {/* Name */}
                <text x={x + 10} y={y + 38}
                      fill="var(--text)" fontSize={12} fontWeight={600}
                      fontFamily="Syne">{phase.name.length > 14 ? phase.name.slice(0, 14) + '…' : phase.name}</text>
                {/* Status glyph + label */}
                <text x={x + 10} y={y + NODE_H - 10}
                      fill={style.ring} fontSize={10}>
                  <tspan fontWeight={700}>{style.glyph}</tspan>
                  <tspan dx={5}>{status.replace('_', ' ')}</tspan>
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
