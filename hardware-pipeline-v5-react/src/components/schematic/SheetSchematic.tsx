// Renders one sheet of a schematic — components as SVG symbols + nets as
// L-shaped Manhattan polylines between pin anchors.
//
// This is the primary drawing component. It is pure: given SheetData + a hovered
// net, returns an SVG forwardRef so the PDF exporter can grab the node directly.

import { forwardRef, useMemo } from 'react';
import type { ComponentData, NetData, SheetData } from './symbols/types';
import { GRID, NET_COLORS } from './symbols/types';
import { Symbol, getPinAnchor, getIcSize } from './symbols';

export interface SheetSchematicProps {
  sheet: SheetData;
  hoveredNet?: string | null;
  onNetHover?: (netName: string | null) => void;
  onNetClick?: (netName: string) => void;
  /** Extra SVG viewBox padding in grid units. Default 2. */
  padding?: number;
  /** Show grid background? */
  showGrid?: boolean;
  /** Show pin numbers/names on IC symbols. Default false. */
  showPinNumbers?: boolean;
}

const SheetSchematic = forwardRef<SVGSVGElement, SheetSchematicProps>(function SheetSchematic(
  { sheet, hoveredNet, onNetHover, onNetClick, padding = 2, showGrid = true, showPinNumbers = false }, ref
) {
  const compMap = useMemo(() => {
    const m: Record<string, ComponentData> = {};
    for (const c of sheet.components) m[c.ref] = c;
    return m;
  }, [sheet.components]);

  // Visibly-rendered components — the synthesizer emits MANY standalone
  // `ground` and `vcc` symbols (one per closure cap, plus 5 rail flags at
  // top-of-sheet). With per-pin rail stubs they are visually redundant and
  // the duplication produces stacked-symbol columns next to every IC. Hide
  // them in the renderer; keep them in the data model so DRC / KiCad export
  // still see the connectivity.
  const visibleComps = useMemo(
    () => sheet.components.filter(c => c.type !== 'ground' && c.type !== 'vcc'),
    [sheet.components],
  );

  // Compute sheet bounds so the SVG viewBox fits everything visible
  const { vbW, vbH } = useMemo(() => {
    let maxX = 30, maxY = 20;
    for (const c of visibleComps) {
      const { w, h } = c.type === 'ic' ? getIcSize(c) : symbolBoundingBox(c);
      maxX = Math.max(maxX, c.x + w + 2);
      maxY = Math.max(maxY, c.y + h + 2);
    }
    return { vbW: (maxX + padding) * GRID, vbH: (maxY + padding) * GRID };
  }, [visibleComps, padding]);

  // Pre-compute polylines for every net. Pass the FULL component map so pin
  // anchors resolve, but the buildNetLines helper itself filters out endpoints
  // on hidden ground/vcc symbols (no orphan stubs in empty space).
  const netLines = useMemo(() => buildNetLines(sheet.nets, compMap), [sheet.nets, compMap]);

  return (
    <svg
      ref={ref}
      xmlns="http://www.w3.org/2000/svg"
      viewBox={`0 0 ${vbW} ${vbH}`}
      style={{ width: '100%', height: '100%', display: 'block', background: '#070b14' }}
      preserveAspectRatio="xMidYMid meet"
    >
      {/* Grid background */}
      {showGrid && (
        <g opacity={0.15}>
          {Array.from({ length: Math.ceil(vbW / GRID) + 1 }).map((_, i) => (
            <line key={`v-${i}`} x1={i * GRID} y1={0} x2={i * GRID} y2={vbH} stroke="#2a3a50" strokeWidth={0.5} />
          ))}
          {Array.from({ length: Math.ceil(vbH / GRID) + 1 }).map((_, i) => (
            <line key={`h-${i}`} x1={0} y1={i * GRID} x2={vbW} y2={i * GRID} stroke="#2a3a50" strokeWidth={0.5} />
          ))}
        </g>
      )}

      {/* Nets — drawn BEFORE symbols so symbols sit on top.
          Power / ground rails render as stubs at each pin (KiCad convention)
          rather than polylines, because 39+ power-net wires criss-crossing
          a single sheet is the dominant source of visual chaos. */}
      <g>
        {netLines.map((nl, ni) => {
          const isHovered = hoveredNet === nl.net.name;
          const baseColor = NET_COLORS[nl.net.type || 'signal'] || NET_COLORS.default;
          const color = isHovered ? '#00ffe0' : baseColor;
          const strokeWidth = isHovered ? 2.5 : 1.5;
          const opacity = hoveredNet && !isHovered ? 0.25 : 0.95;
          // Stub-mode rendering: power & ground nets become per-pin stubs.
          if (nl.stubs && nl.stubs.length > 0) {
            return (
              <g key={`${nl.net.name}-${ni}`} opacity={opacity}
                 onMouseEnter={() => onNetHover?.(nl.net.name)}
                 onMouseLeave={() => onNetHover?.(null)}
                 onClick={() => onNetClick?.(nl.net.name)}
                 style={{ cursor: 'pointer' }}>
                {nl.stubs.map((s, i) => (
                  <RailStub key={i} cx={s.pos[0]} cy={s.pos[1]}
                            kind={s.kind} label={s.label}
                            color={color} highlighted={isHovered} />
                ))}
              </g>
            );
          }
          return (
            <g key={`${nl.net.name}-${ni}`} opacity={opacity}
               onMouseEnter={() => onNetHover?.(nl.net.name)}
               onMouseLeave={() => onNetHover?.(null)}
               onClick={() => onNetClick?.(nl.net.name)}
               style={{ cursor: 'pointer' }}>
              {nl.segments.map((seg, i) => (
                <polyline key={i} points={seg.map(p => `${p[0]},${p[1]}`).join(' ')}
                          stroke={color} strokeWidth={strokeWidth}
                          fill="none" strokeLinejoin="round" strokeLinecap="round" />
              ))}
              {/* Junction dots at shared pin anchors */}
              {nl.junctions.map((j, i) => (
                <circle key={`j-${i}`} cx={j[0]} cy={j[1]} r={4} fill={color} stroke="#0a0e18" strokeWidth={1} />
              ))}
              {/* Net-name label near the first segment midpoint */}
              {nl.labelPos && (
                <text x={nl.labelPos[0]} y={nl.labelPos[1]} fill={color} fontSize={9}
                      fontFamily="'JetBrains Mono', monospace" opacity={0.85}
                      style={{ pointerEvents: 'none' }}>{nl.net.name}</text>
              )}
            </g>
          );
        })}
      </g>

      {/* Component symbols — ground / vcc filtered out (replaced by per-pin stubs) */}
      <g>
        {visibleComps.map((c) => <Symbol key={c.ref} comp={c} />)}
      </g>

      {/* Pin number labels (Phase 3 polish) — rendered on top of everything */}
      {showPinNumbers && (
        <g opacity={0.7}>
          {sheet.components.filter(c => c.type === 'ic' && c.pins && c.pins.length > 0).map(c =>
            c.pins!.map(pin => {
              const anchor = getPinAnchor(c, pin.name);
              if (!anchor) return null;
              const px = anchor.x * GRID;
              const py = anchor.y * GRID;
              // Offset label slightly away from pin anchor based on side
              const isLeft = pin.side === 'left';
              const isTop = pin.side === 'top';
              const ox = isLeft ? -14 : pin.side === 'right' ? 14 : 0;
              const oy = isTop ? -8 : pin.side === 'bottom' ? 12 : 0;
              return (
                <text
                  key={`${c.ref}-${pin.name}`}
                  x={px + ox} y={py + oy}
                  textAnchor={isLeft ? 'end' : pin.side === 'right' ? 'start' : 'middle'}
                  dominantBaseline="middle"
                  fill="#64748b" fontSize={7}
                  fontFamily="'JetBrains Mono', monospace"
                  style={{ pointerEvents: 'none' }}
                >
                  {pin.num || pin.name}
                </text>
              );
            })
          )}
        </g>
      )}
    </svg>
  );
});

export default SheetSchematic;

// ──────────────────────────────────────────────────────────────────────────────

interface NetLineDrawable {
  net: NetData;
  segments: Array<Array<[number, number]>>; // pixel coords
  junctions: Array<[number, number]>;
  /** When set, the net is rendered as per-pin rail stubs instead of polylines. */
  stubs?: Array<{ pos: [number, number]; kind: 'power' | 'ground'; label: string }>;
  labelPos?: [number, number];
}

// Detect rail nets — type field is authoritative, but we also fall back on
// canonical rail-name patterns so an LLM-emitted net with a missing/wrong
// `type` still renders as a stub (e.g. an "EN_HIGH" net the agent typed as
// "signal" should still look like a rail).
function classifyRail(net: NetData): 'power' | 'ground' | null {
  const t = (net.type || '').toLowerCase();
  if (t === 'ground') return 'ground';
  if (t === 'power') return 'power';
  const n = (net.name || '').toUpperCase();
  if (/^([AD]?GND|VSS|VEE)\b/.test(n)) return 'ground';
  if (/^(VCC|VDD|AVDD|DVDD|VBAT|VIN|VOUT|EN_HIGH|VPP|V[123]V[358]?|V\d+V\d+)/.test(n)) return 'power';
  return null;
}

function buildNetLines(nets: NetData[], compMap: Record<string, ComponentData>): NetLineDrawable[] {
  const out: NetLineDrawable[] = [];
  for (const net of nets) {
    const anchors: Array<[number, number]> = [];
    for (const ep of net.endpoints) {
      const c = compMap[ep.ref];
      if (!c) continue;
      // Skip endpoints on hidden ground/vcc standalone symbols. The OTHER
      // endpoint of the rail net (a real cap/IC pin) gets a stub, which is
      // sufficient — placing a stub where no symbol is drawn would leave
      // floating "GND" labels in empty whitespace.
      if (c.type === 'ground' || c.type === 'vcc') continue;
      const a = getPinAnchor(c, ep.pin);
      if (!a) continue;
      anchors.push([a.x * GRID, a.y * GRID]);
    }
    if (anchors.length === 0) {
      out.push({ net, segments: [], junctions: [] });
      continue;
    }
    // POWER / GROUND nets — render as per-pin stubs (KiCad convention).
    // We do NOT draw any wire between endpoints; logical connectivity stays
    // intact in the data, only the visual is suppressed. This is the single
    // biggest source of visual noise on dense receivers (30+ rail wires
    // criss-crossing a single sheet).
    const railKind = classifyRail(net);
    if (railKind) {
      const stubs = anchors.map(([x, y]) => ({
        pos: [x, y] as [number, number],
        kind: railKind,
        label: net.name,
      }));
      out.push({ net, segments: [], junctions: [], stubs });
      continue;
    }
    if (anchors.length < 2) {
      out.push({ net, segments: [], junctions: [] });
      continue;
    }
    // If waypoints are provided, render one polyline through all of them in order
    const segments: Array<Array<[number, number]>> = [];
    if (net.waypoints && net.waypoints.length > 0) {
      const path: Array<[number, number]> = [anchors[0]];
      for (const wp of net.waypoints) path.push([wp.x * GRID, wp.y * GRID]);
      // Close out to each remaining anchor
      for (let i = 1; i < anchors.length; i++) path.push(anchors[i]);
      segments.push(path);
    } else {
      // Auto-route: L-shaped Manhattan from anchor[0] to anchor[i] for each i>0
      const hub = anchors[0];
      for (let i = 1; i < anchors.length; i++) {
        segments.push(manhattan(hub, anchors[i]));
      }
    }
    // Junctions: every point where the rendered polyline visually passes
    // *through* a pin anchor instead of terminating at it. P26.7 (2026-05-05):
    //   • Auto-route mode (no waypoints) — the hub `anchors[0]` is shared by
    //     all N-1 manhattan branches; the rest are leaves. One dot at hub.
    //   • Waypoint mode — a SINGLE polyline runs anchor[0] → wp* → anchor[1]
    //     → anchor[2] → … → anchor[N-1]. Every anchor *except the last* is
    //     mid-polyline, so each needs a junction dot. Pre-fix only anchor[0]
    //     got a dot, so 3-pin connections (e.g. VCC bus tapping into multiple
    //     decoupling caps) looked like the wire was "passing through" the
    //     middle pins without electrically connecting.
    let junctions: Array<[number, number]> = [];
    if (anchors.length >= 3) {
      if (net.waypoints && net.waypoints.length > 0) {
        junctions = anchors.slice(0, -1);
      } else {
        junctions = [anchors[0]];
      }
    }
    const mid = segments[0]?.[Math.floor(segments[0].length / 2)];
    const labelPos: [number, number] | undefined = mid ? [mid[0] + 6, mid[1] - 6] : undefined;
    out.push({ net, segments, junctions, labelPos });
  }
  return out;
}

// L-shaped Manhattan path — prefers horizontal-then-vertical. If either endpoint is
// exactly on the outside of an IC (dx=0 or dx=w), we offset first so the line doesn't
// run through the symbol body.
function manhattan(a: [number, number], b: [number, number]): Array<[number, number]> {
  const [ax, ay] = a;
  const [bx, by] = b;
  if (ax === bx || ay === by) return [a, b];
  // Horizontal-then-vertical
  return [a, [bx, ay], b];
}

// Per-pin rail stub. Power rails get an upward tick + name above the pin;
// ground rails get a triangle pointing down + name below. We don't draw any
// wire between endpoints — the rail name is the contract.
function RailStub({ cx, cy, kind, label, color, highlighted }: {
  cx: number; cy: number; kind: 'power' | 'ground'; label: string;
  color: string; highlighted: boolean;
}) {
  const sw = highlighted ? 2 : 1.4;
  if (kind === 'ground') {
    // Three horizontal ticks of decreasing width, centered below the pin.
    return (
      <g style={{ pointerEvents: 'none' }}>
        <line x1={cx} y1={cy} x2={cx} y2={cy + 8} stroke={color} strokeWidth={sw} />
        <line x1={cx - 7} y1={cy + 8} x2={cx + 7} y2={cy + 8} stroke={color} strokeWidth={sw} />
        <line x1={cx - 4.5} y1={cy + 11} x2={cx + 4.5} y2={cy + 11} stroke={color} strokeWidth={sw} />
        <line x1={cx - 2} y1={cy + 14} x2={cx + 2} y2={cy + 14} stroke={color} strokeWidth={sw} />
        <text x={cx} y={cy + 26} fill={color} fontSize={8} textAnchor="middle"
              fontFamily="'JetBrains Mono', monospace" opacity={0.75}>{label}</text>
      </g>
    );
  }
  // Power: upward stub line + horizontal cap + name above
  return (
    <g style={{ pointerEvents: 'none' }}>
      <line x1={cx} y1={cy} x2={cx} y2={cy - 10} stroke={color} strokeWidth={sw} />
      <line x1={cx - 5} y1={cy - 10} x2={cx + 5} y2={cy - 10} stroke={color} strokeWidth={sw} />
      <text x={cx} y={cy - 14} fill={color} fontSize={8} textAnchor="middle"
            fontFamily="'JetBrains Mono', monospace" opacity={0.85}>{label}</text>
    </g>
  );
}

function symbolBoundingBox(comp: ComponentData): { w: number; h: number } {
  switch (comp.type) {
    case 'resistor':
    case 'capacitor':
    case 'capacitor_polar':
    case 'inductor':
    case 'diode':
    case 'diode_zener':
    case 'diode_tvs':
    case 'diode_led':
      return { w: 2, h: 1 };
    case 'ground':
    case 'vcc':
      return { w: 1, h: 1 };
    case 'connector': {
      const n = parseInt((comp.value || 'CON_2').replace(/\D+/g, ''), 10) || 2;
      return { w: 1, h: n };
    }
    default:
      return { w: 3, h: 2 };
  }
}
