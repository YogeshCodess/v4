// Schematic symbol library — each symbol is rendered as a <g> with its origin at
// the component's (x, y) grid position. Pin anchors are exposed via
// getPinAnchor(comp, pinName) so the net router can join them up.
//
// Design convention:
//   • Horizontal (rot=0) resistor/cap/inductor/diode has pins "1" on the left, "2" on the right.
//   • Vertical (rot=90) rotates the body; pin "1" ends up on top, "2" on bottom.
//   • Symbol bodies live in a local unrotated coord system; rotation is applied in the wrapper.
//   • For ICs, pin anchors come from the user-provided `pins` array.

import type { ComponentData, PinSpec, PinAnchor } from './types';
import { GRID } from './types';

const STROKE = '#e2e8f0';
const FILL_BG = '#1a2235';
const LABEL_COLOR = '#94a3b8';
const VALUE_COLOR = '#00c6a7';
const PIN_COLOR = '#e2e8f0';

// ──────────────────────────────────────────────────────────────────────────────
// Pin anchor resolver — returns the grid-unit (x, y) of a named pin on a component.
// Called by the net router to find endpoint coordinates.

export function getPinAnchor(comp: ComponentData, pinName: string): PinAnchor | null {
  const rot = comp.rot ?? 0;
  const local = getLocalPinAnchor(comp, pinName);
  if (!local) return null;
  const { dx, dy } = local;
  // Rotate around (0, 0) — component origin is top-left of symbol bbox
  let rx = dx, ry = dy;
  if (rot === 90)  { rx = -dy; ry = dx; }
  if (rot === 180) { rx = -dx; ry = -dy; }
  if (rot === 270) { rx = dy;  ry = -dx; }
  return { x: comp.x + rx, y: comp.y + ry };
}

// Local pin offset from the component origin, BEFORE rotation.
// All symbols have their origin at the left/top of their bounding box.
function getLocalPinAnchor(comp: ComponentData, pinName: string): { dx: number; dy: number } | null {
  const p = (pinName || '').trim();
  switch (comp.type) {
    case 'resistor':
    case 'capacitor':
    case 'capacitor_polar':
    case 'inductor':
    case 'diode':
    case 'diode_zener':
    case 'diode_tvs':
    case 'diode_led':
      // Two-terminal horizontal symbol, body 2 units wide
      if (p === '1' || p === 'A' || p === '+' || p.toLowerCase() === 'in') return { dx: 0, dy: 0.5 };
      if (p === '2' || p === 'K' || p === '-' || p.toLowerCase() === 'out') return { dx: 2, dy: 0.5 };
      return { dx: 0, dy: 0.5 };

    case 'ground':
      return { dx: 0.5, dy: 0 };

    case 'vcc':
      return { dx: 0.5, dy: 1 };

    case 'connector': {
      const pinCount = parseInt((comp.value || 'CON_2').replace(/\D+/g, ''), 10) || 2;
      const idx = Math.max(0, parseInt(p, 10) - 1);
      if (Number.isNaN(idx) || idx < 0 || idx >= pinCount) return null;
      // Connector is 1 unit wide, pinCount units tall; pin N on the right side
      return { dx: 1, dy: 0.5 + idx };
    }

    case 'ic': {
      if (!comp.pins || comp.pins.length === 0) return null;
      const sides: Record<PinSpec['side'], PinSpec[]> = { left: [], right: [], top: [], bottom: [] };
      for (const pin of comp.pins) sides[pin.side].push(pin);
      const { w, h } = getIcSize(comp);
      const findOn = (side: PinSpec['side']) => {
        const list = sides[side];
        const idx = list.findIndex(x => x.name === p || x.num === p);
        if (idx < 0) return null;
        const step = (side === 'left' || side === 'right') ? h / (list.length + 1) : w / (list.length + 1);
        const pos  = step * (idx + 1);
        if (side === 'left')   return { dx: 0, dy: pos };
        if (side === 'right')  return { dx: w, dy: pos };
        if (side === 'top')    return { dx: pos, dy: 0 };
        if (side === 'bottom') return { dx: pos, dy: h };
        return null;
      };
      return findOn('left') || findOn('right') || findOn('top') || findOn('bottom');
    }

    default:
      return null;
  }
}

// IC size helper — width/height in grid units depending on pin layout
export function getIcSize(comp: ComponentData): { w: number; h: number } {
  if (comp.type !== 'ic' || !comp.pins) return { w: 4, h: 3 };
  const sides: Record<PinSpec['side'], number> = { left: 0, right: 0, top: 0, bottom: 0 };
  for (const pin of comp.pins) sides[pin.side]++;
  const maxLR = Math.max(sides.left, sides.right, 2);
  const maxTB = Math.max(sides.top, sides.bottom, 0);
  const w = Math.max(4, maxTB + 2);
  const h = Math.max(3, maxLR + 1);
  return { w, h };
}

// ──────────────────────────────────────────────────────────────────────────────
// Symbol renderer — picks the right SVG body for each component type.

export function Symbol({ comp }: { comp: ComponentData }) {
  const rot = comp.rot ?? 0;
  // Top-left of bbox in SVG coords
  const tx = comp.x * GRID;
  const ty = comp.y * GRID;
  return (
    <g
      transform={`translate(${tx} ${ty}) rotate(${rot})`}
      data-ref={comp.ref}
    >
      {renderSymbolBody(comp)}
    </g>
  );
}

function renderSymbolBody(comp: ComponentData) {
  switch (comp.type) {
    case 'resistor':        return <Resistor comp={comp} />;
    case 'capacitor':       return <Capacitor comp={comp} polar={false} />;
    case 'capacitor_polar': return <Capacitor comp={comp} polar={true} />;
    case 'inductor':        return <Inductor comp={comp} />;
    case 'diode':           return <Diode comp={comp} variant="standard" />;
    case 'diode_zener':     return <Diode comp={comp} variant="zener" />;
    case 'diode_tvs':       return <Diode comp={comp} variant="tvs" />;
    case 'diode_led':       return <Diode comp={comp} variant="led" />;
    case 'ic':              return <ICBlock comp={comp} />;
    case 'ground':          return <Ground comp={comp} />;
    case 'vcc':             return <Vcc comp={comp} />;
    case 'connector':       return <Connector comp={comp} />;
    case 'net_label':       return <NetLabel comp={comp} />;
    default:                return null;
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Two-terminal helper: lead — label — body — label — lead
// Body sits between grid cols 0 and 2 of the component, centred at y=0.5

function RefLabel({ ref, value, y = -6, fontSize = 10 }: { ref?: string; value?: string; y?: number; fontSize?: number }) {
  return (
    <>
      {ref && (
        <text x={GRID} y={y} textAnchor="middle" fill={LABEL_COLOR} fontSize={fontSize}
              fontFamily="'JetBrains Mono', monospace" fontWeight="600">{ref}</text>
      )}
      {value && (
        <text x={GRID} y={GRID + 16} textAnchor="middle" fill={VALUE_COLOR} fontSize={fontSize - 1}
              fontFamily="'JetBrains Mono', monospace">{value}</text>
      )}
    </>
  );
}

function Resistor({ comp }: { comp: ComponentData }) {
  // Zigzag body — 4 triangles between x=GRID*0.4 and x=GRID*1.6, y=GRID/2
  const cy = GRID / 2;
  const x0 = GRID * 0.4, x1 = GRID * 1.6;
  const width = x1 - x0;
  const zig = [];
  const steps = 6;
  for (let i = 0; i <= steps; i++) {
    const x = x0 + (width * i) / steps;
    const y = cy + (i % 2 === 0 ? -4 : 4);
    zig.push(`${i === 0 ? 'M' : 'L'} ${x} ${y}`);
  }
  return (
    <g>
      {/* Leads */}
      <line x1={0} y1={cy} x2={x0} y2={cy} stroke={STROKE} strokeWidth={1.5} />
      <line x1={x1} y1={cy} x2={GRID * 2} y2={cy} stroke={STROKE} strokeWidth={1.5} />
      <path d={zig.join(' ')} stroke={STROKE} strokeWidth={1.5} fill="none" />
      <RefLabel ref={comp.ref} value={comp.value} />
    </g>
  );
}

function Capacitor({ comp, polar }: { comp: ComponentData; polar: boolean }) {
  const cy = GRID / 2;
  const plateGap = 6;
  const plateH = 18;
  const cx = GRID; // centre of symbol
  return (
    <g>
      <line x1={0} y1={cy} x2={cx - plateGap / 2} y2={cy} stroke={STROKE} strokeWidth={1.5} />
      <line x1={cx + plateGap / 2} y1={cy} x2={GRID * 2} y2={cy} stroke={STROKE} strokeWidth={1.5} />
      {/* Left plate */}
      <line x1={cx - plateGap / 2} y1={cy - plateH / 2} x2={cx - plateGap / 2} y2={cy + plateH / 2} stroke={STROKE} strokeWidth={2} />
      {/* Right plate — curved if polar, straight if not */}
      {polar ? (
        <path d={`M ${cx + plateGap / 2} ${cy - plateH / 2} Q ${cx + plateGap / 2 + 6} ${cy} ${cx + plateGap / 2} ${cy + plateH / 2}`}
              stroke={STROKE} strokeWidth={2} fill="none" />
      ) : (
        <line x1={cx + plateGap / 2} y1={cy - plateH / 2} x2={cx + plateGap / 2} y2={cy + plateH / 2} stroke={STROKE} strokeWidth={2} />
      )}
      {polar && (
        <text x={cx - 12} y={cy - plateH / 2 - 4} fill={LABEL_COLOR} fontSize={11} fontFamily="'JetBrains Mono', monospace">+</text>
      )}
      <RefLabel ref={comp.ref} value={comp.value} />
    </g>
  );
}

function Inductor({ comp }: { comp: ComponentData }) {
  const cy = GRID / 2;
  const x0 = GRID * 0.35, x1 = GRID * 1.65;
  const bumps = 4;
  const bumpW = (x1 - x0) / bumps;
  const arcs: string[] = [`M ${x0} ${cy}`];
  for (let i = 0; i < bumps; i++) {
    const sx = x0 + i * bumpW;
    arcs.push(`A ${bumpW / 2} 8 0 0 1 ${sx + bumpW} ${cy}`);
  }
  return (
    <g>
      <line x1={0} y1={cy} x2={x0} y2={cy} stroke={STROKE} strokeWidth={1.5} />
      <line x1={x1} y1={cy} x2={GRID * 2} y2={cy} stroke={STROKE} strokeWidth={1.5} />
      <path d={arcs.join(' ')} stroke={STROKE} strokeWidth={1.5} fill="none" />
      <RefLabel ref={comp.ref} value={comp.value} />
    </g>
  );
}

function Diode({ comp, variant }: { comp: ComponentData; variant: 'standard' | 'zener' | 'tvs' | 'led' }) {
  const cy = GRID / 2;
  const cx = GRID;
  const tri = `M ${cx - 8} ${cy - 8} L ${cx + 6} ${cy} L ${cx - 8} ${cy + 8} Z`;
  return (
    <g>
      <line x1={0} y1={cy} x2={cx - 8} y2={cy} stroke={STROKE} strokeWidth={1.5} />
      <line x1={cx + 6} y1={cy} x2={GRID * 2} y2={cy} stroke={STROKE} strokeWidth={1.5} />
      <path d={tri} stroke={STROKE} strokeWidth={1.5} fill="none" />
      {/* Cathode bar */}
      {variant === 'zener' ? (
        <path d={`M ${cx + 6} ${cy - 8} L ${cx + 6} ${cy + 8} L ${cx + 10} ${cy + 8}`}
              stroke={STROKE} strokeWidth={1.5} fill="none" />
      ) : variant === 'tvs' ? (
        <g>
          <line x1={cx + 6} y1={cy - 8} x2={cx + 6} y2={cy + 8} stroke={STROKE} strokeWidth={1.5} />
          <line x1={cx + 6} y1={cy - 8} x2={cx + 10} y2={cy - 12} stroke={STROKE} strokeWidth={1.5} />
          <line x1={cx + 6} y1={cy + 8} x2={cx + 2} y2={cy + 12} stroke={STROKE} strokeWidth={1.5} />
        </g>
      ) : (
        <line x1={cx + 6} y1={cy - 8} x2={cx + 6} y2={cy + 8} stroke={STROKE} strokeWidth={1.5} />
      )}
      {variant === 'led' && (
        <g>
          <line x1={cx + 6} y1={cy - 12} x2={cx + 12} y2={cy - 18} stroke={STROKE} strokeWidth={1} />
          <polyline points={`${cx + 10},${cy - 17} ${cx + 12},${cy - 18} ${cx + 11},${cy - 15}`}
                    stroke={STROKE} strokeWidth={1} fill="none" />
        </g>
      )}
      <RefLabel ref={comp.ref} value={comp.value || (variant === 'standard' ? '' : variant.toUpperCase())} />
    </g>
  );
}

function Ground({ comp }: { comp: ComponentData }) {
  // Origin at top of symbol; pin is on top at (0.5 GRID, 0)
  const cx = GRID * 0.5;
  return (
    <g>
      <line x1={cx} y1={0} x2={cx} y2={14} stroke={STROKE} strokeWidth={1.5} />
      <line x1={cx - 12} y1={14} x2={cx + 12} y2={14} stroke={STROKE} strokeWidth={2} />
      <line x1={cx - 8} y1={19} x2={cx + 8} y2={19} stroke={STROKE} strokeWidth={2} />
      <line x1={cx - 4} y1={24} x2={cx + 4} y2={24} stroke={STROKE} strokeWidth={2} />
      <text x={cx} y={38} textAnchor="middle" fill={LABEL_COLOR} fontSize={9}
            fontFamily="'JetBrains Mono', monospace">{comp.value || 'GND'}</text>
    </g>
  );
}

function Vcc({ comp }: { comp: ComponentData }) {
  // Origin at top; pin is at bottom (0.5 GRID, 1 GRID)
  const cx = GRID * 0.5;
  const rail = comp.value || 'VCC';
  return (
    <g>
      {/* Flag-shaped power marker */}
      <path
        d={`M ${cx - 10} 14 L ${cx + 10} 14 L ${cx + 14} 8 L ${cx + 10} 2 L ${cx - 10} 2 L ${cx - 14} 8 Z`}
        stroke="#f59e0b" strokeWidth={1.5} fill="rgba(245,158,11,0.08)" />
      <text x={cx} y={12} textAnchor="middle" fill="#f59e0b" fontSize={10}
            fontFamily="'JetBrains Mono', monospace" fontWeight="700">{rail}</text>
      <line x1={cx} y1={14} x2={cx} y2={GRID} stroke={STROKE} strokeWidth={1.5} />
    </g>
  );
}

function Connector({ comp }: { comp: ComponentData }) {
  const pinCount = parseInt((comp.value || 'CON_2').replace(/\D+/g, ''), 10) || 2;
  const w = GRID;
  const h = pinCount * GRID;
  return (
    <g>
      <rect x={0} y={0} width={w} height={h} stroke="#3b82f6" strokeWidth={1.5} fill={FILL_BG} rx={3} />
      {Array.from({ length: pinCount }).map((_, i) => {
        const py = GRID * 0.5 + i * GRID;
        return (
          <g key={i}>
            {/* Pin connection point — right side */}
            <line x1={w} y1={py} x2={w + 12} y2={py} stroke={STROKE} strokeWidth={1.5} />
            <circle cx={w} cy={py} r={3} fill="#3b82f6" />
            <text x={w - 6} y={py + 4} textAnchor="end" fill={LABEL_COLOR} fontSize={9}
                  fontFamily="'JetBrains Mono', monospace">{i + 1}</text>
          </g>
        );
      })}
      <text x={w / 2} y={-6} textAnchor="middle" fill={LABEL_COLOR} fontSize={10}
            fontFamily="'JetBrains Mono', monospace" fontWeight="600">{comp.ref}</text>
      <text x={w / 2} y={h + 16} textAnchor="middle" fill={VALUE_COLOR} fontSize={9}
            fontFamily="'JetBrains Mono', monospace">{comp.value || `CON_${pinCount}`}</text>
    </g>
  );
}

function ICBlock({ comp }: { comp: ComponentData }) {
  const { w, h } = getIcSize(comp);
  const wPx = w * GRID;
  const hPx = h * GRID;
  const pins = comp.pins || [];
  const sides = { left: [] as PinSpec[], right: [] as PinSpec[], top: [] as PinSpec[], bottom: [] as PinSpec[] };
  for (const p of pins) sides[p.side].push(p);

  return (
    <g>
      <rect x={0} y={0} width={wPx} height={hPx} stroke={VALUE_COLOR} strokeWidth={1.8} fill={FILL_BG} rx={4} />
      {/* Ref + value */}
      <text x={wPx / 2} y={-10} textAnchor="middle" fill={LABEL_COLOR} fontSize={11}
            fontFamily="'JetBrains Mono', monospace" fontWeight="700">{comp.ref}</text>
      <text x={wPx / 2} y={hPx + 18} textAnchor="middle" fill={VALUE_COLOR} fontSize={9.5}
            fontFamily="'JetBrains Mono', monospace">{comp.value || comp.part_number || 'IC'}</text>

      {/* Pin stubs + labels */}
      {(['left','right','top','bottom'] as const).flatMap((side) => {
        const list = sides[side];
        if (list.length === 0) return [];
        const step = (side === 'left' || side === 'right') ? hPx / (list.length + 1) : wPx / (list.length + 1);
        return list.map((p, i) => {
          const pos = step * (i + 1);
          let x1 = 0, y1 = 0, x2 = 0, y2 = 0, tx = 0, ty = 0;
          // SVG textAnchor accepts "start" | "middle" | "end" — type the
          // variable so the <text textAnchor=...> prop isn't handed a plain
          // string (verbatimModuleSyntax tightens this check).
          let anchor: 'start' | 'middle' | 'end' = 'start';
          if (side === 'left')   { x1 = 0;    y1 = pos; x2 = -10; y2 = pos; tx = 4;       ty = pos + 3; anchor = 'start'; }
          if (side === 'right')  { x1 = wPx;  y1 = pos; x2 = wPx + 10; y2 = pos; tx = wPx - 4; ty = pos + 3; anchor = 'end'; }
          if (side === 'top')    { x1 = pos;  y1 = 0;   x2 = pos; y2 = -10; tx = pos;     ty = 10; anchor = 'middle'; }
          if (side === 'bottom') { x1 = pos;  y1 = hPx; x2 = pos; y2 = hPx + 10; tx = pos; ty = hPx - 4; anchor = 'middle'; }
          return (
            <g key={`${side}-${i}`}>
              <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={PIN_COLOR} strokeWidth={1.4} />
              <circle cx={x1} cy={y1} r={2.2} fill={VALUE_COLOR} />
              <text x={tx} y={ty} textAnchor={anchor} fill={LABEL_COLOR} fontSize={8.5}
                    fontFamily="'JetBrains Mono', monospace">{p.name}</text>
            </g>
          );
        });
      })}
    </g>
  );
}

function NetLabel({ comp }: { comp: ComponentData }) {
  return (
    <g>
      <text x={0} y={0} fill={VALUE_COLOR} fontSize={10}
            fontFamily="'JetBrains Mono', monospace">{comp.value || comp.ref}</text>
    </g>
  );
}
