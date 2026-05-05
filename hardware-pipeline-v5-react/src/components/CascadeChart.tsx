import { useEffect, useState } from 'react';
import { api } from '../api';

interface Stage {
  name: string;
  part_number: string;
  category: string;
  // RX
  nf_db: number | null;
  gain_db: number | null;
  iip3_dbm: number | null;
  cum_gain_db: number | null;
  cum_nf_db: number | null;
  cum_iip3_dbm: number | null;
  nf_contribution_db?: number | null;
  iip3_contribution_dbm?: number | null;
  // TX
  oip3_dbm?: number | null;
  pout_dbm?: number | null;
  pae_pct?: number | null;
  pdc_w?: number | null;
  pin_dbm?: number | null;
  pout_computed_dbm?: number | null;
  cum_oip3_dbm?: number | null;
  compression_warning?: boolean;
}

interface Totals {
  // RX
  nf_db?: number | null;
  iip3_dbm?: number | null;
  // TX
  pout_dbm?: number | null;
  oip3_dbm?: number | null;
  pae_pct?: number | null;
  pdc_total_w?: number | null;
  input_power_dbm?: number | null;
  compression_warnings?: string[];
  // shared
  gain_db: number | null;
  stage_count: number;
}

interface Claims {
  nf_db?: number | null;
  iip3_dbm?: number | null;
  total_gain_db: number | null;
  pout_dbm?: number | null;
  oip3_dbm?: number | null;
  pae_pct?: number | null;
}

interface Verdict {
  // RX
  nf_pass?: boolean | null;
  iip3_pass?: boolean | null;
  nf_headroom_db?: number | null;
  iip3_headroom_db?: number | null;
  // TX
  pout_pass?: boolean | null;
  oip3_pass?: boolean | null;
  pae_pass?: boolean | null;
  pout_headroom_db?: number | null;
  pae_delta_pct?: number | null;
  no_compression?: boolean;
  // shared
  gain_pass: boolean | null;
  gain_delta_db: number | null;
}

interface CascadeData {
  direction?: 'rx' | 'tx';
  stages: Stage[];
  totals: Totals;
  claims: Claims;
  verdict: Verdict;
}

interface Props {
  projectId: number;
  color?: string;
}

const COLORS = {
  bar_gain: '#00c6a7',
  bar_nf: '#f59e0b',
  bar_iip3: '#8b5cf6',
  claim: '#3b82f6',
  pass: '#10b981',
  fail: '#dc2626',
  grid: 'rgba(148,163,184,0.15)',
  text: '#e2e8f0',
  muted: '#94a3b8',
  dim: '#64748b',
};

function fmt(n: number | null | undefined, digits = 1, suffix = ''): string {
  if (n === null || n === undefined || !isFinite(n)) return '—';
  return n.toFixed(digits) + suffix;
}

// Horizontal bar chart: one row per stage, the stage value (per-stage)
// drawn as a coloured bar, and the cumulative value drawn as a thin
// overlay so the reader can trace the Friis accumulation visually.
function StageChart({
  stages, metric, unit, domain, color, title, subtitle,
  accessor, cumAccessor, claim,
}: {
  stages: Stage[];
  metric: string;
  unit: string;
  domain: [number, number];
  color: string;
  title: string;
  subtitle: string;
  accessor: (s: Stage) => number | null;
  cumAccessor: (s: Stage) => number | null;
  claim?: number | null;
}) {
  const W = 560;
  const ROW_H = 30;
  const LEFT = 150;
  const RIGHT = 50;
  const TOP = 44;
  const H = TOP + stages.length * ROW_H + 28;
  const [lo, hi] = domain;
  const innerW = W - LEFT - RIGHT;
  const scale = (v: number) => LEFT + ((v - lo) / (hi - lo)) * innerW;

  // Gridlines every 5 dB (or 10 depending on span)
  const span = hi - lo;
  const step = span > 50 ? 10 : span > 20 ? 5 : span > 10 ? 2 : 1;
  const gridValues: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) gridValues.push(v);

  return (
    <div style={{
      background: 'var(--panel)',
      border: '1px solid var(--border2)',
      borderRadius: 8,
      padding: '16px 18px 10px',
      marginBottom: 14,
    }}>
      <div style={{
        display: 'flex', alignItems: 'baseline',
        justifyContent: 'space-between', marginBottom: 4,
      }}>
        <div style={{
          fontFamily: 'Syne', fontWeight: 700, fontSize: 15,
          color: 'var(--text)',
        }}>{title}</div>
        <div style={{
          fontSize: 11, color: COLORS.muted,
          fontFamily: "'DM Mono', monospace", letterSpacing: 0.5,
        }}>{metric} · {unit}</div>
      </div>
      <div style={{
        fontSize: 11, color: COLORS.dim, marginBottom: 8,
      }}>{subtitle}</div>

      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: 'block' }}>
        {/* Gridlines */}
        {gridValues.map(v => (
          <g key={v}>
            <line
              x1={scale(v)} x2={scale(v)}
              y1={TOP - 10} y2={H - 18}
              stroke={COLORS.grid} strokeWidth={1}
            />
            <text
              x={scale(v)} y={H - 6}
              fill={COLORS.dim} fontSize={10}
              fontFamily="DM Mono, monospace" textAnchor="middle"
            >{v.toFixed(0)}</text>
          </g>
        ))}

        {/* Claim line (pass/fail reference) */}
        {claim !== null && claim !== undefined && claim >= lo && claim <= hi && (
          <g>
            <line
              x1={scale(claim)} x2={scale(claim)}
              y1={TOP - 10} y2={H - 18}
              stroke={COLORS.claim} strokeWidth={1.8}
              strokeDasharray="4 3"
            />
            <text
              x={scale(claim)} y={TOP - 14}
              fill={COLORS.claim} fontSize={10}
              fontFamily="DM Mono, monospace" textAnchor="middle"
              fontWeight={700}
            >claim {claim}{unit}</text>
          </g>
        )}

        {/* Rows */}
        {stages.map((s, i) => {
          const y = TOP + i * ROW_H;
          const stageVal = accessor(s);
          const cumVal = cumAccessor(s);
          const stageX = stageVal !== null ? scale(stageVal) : LEFT;
          const zeroX = scale(0);
          const barStart = Math.min(zeroX, stageX);
          const barEnd = Math.max(zeroX, stageX);
          const cumX = cumVal !== null ? scale(cumVal) : null;
          const label = s.part_number || s.name || `Stage ${i + 1}`;
          return (
            <g key={i}>
              <text
                x={LEFT - 10} y={y + 14}
                fill={COLORS.text} fontSize={11.5}
                fontFamily="DM Mono, monospace" textAnchor="end"
              >{label.length > 18 ? label.slice(0, 17) + '…' : label}</text>
              {/* Per-stage bar */}
              {stageVal !== null && (
                <>
                  <rect
                    x={barStart} y={y + 5}
                    width={Math.max(2, barEnd - barStart)} height={16}
                    fill={color} fillOpacity={0.75} rx={2}
                  />
                  <text
                    x={barEnd + 6} y={y + 17}
                    fill={COLORS.text} fontSize={10.5}
                    fontFamily="DM Mono, monospace"
                  >{fmt(stageVal, 1, unit)}</text>
                </>
              )}
              {stageVal === null && (
                <text
                  x={LEFT + 4} y={y + 17}
                  fill={COLORS.dim} fontSize={10.5}
                  fontFamily="DM Mono, monospace" fontStyle="italic"
                >no spec</text>
              )}
              {/* Cumulative overlay (small diamond marker) */}
              {cumX !== null && (
                <g>
                  <polygon
                    points={`${cumX - 4},${y + 13} ${cumX},${y + 5} ${cumX + 4},${y + 13} ${cumX},${y + 21}`}
                    fill="none" stroke={COLORS.text} strokeWidth={1.5}
                    opacity={0.85}
                  />
                </g>
              )}
            </g>
          );
        })}
      </svg>

      <div style={{
        marginTop: 6, fontSize: 10.5, color: COLORS.dim,
        display: 'flex', gap: 18, flexWrap: 'wrap',
      }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{
            display: 'inline-block', width: 14, height: 8,
            background: color, opacity: 0.75, borderRadius: 2,
          }} />
          per-stage
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <svg width={12} height={10}>
            <polygon points="6,0 12,5 6,10 0,5" fill="none"
                     stroke={COLORS.text} strokeWidth={1.3} />
          </svg>
          cumulative (Friis)
        </span>
        {claim !== null && claim !== undefined && (
          <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <svg width={14} height={8}>
              <line x1={0} y1={4} x2={14} y2={4}
                    stroke={COLORS.claim} strokeWidth={1.8}
                    strokeDasharray="4 3" />
            </svg>
            P1 claim
          </span>
        )}
      </div>
    </div>
  );
}

function VerdictPill({ label, pass, detail }: {
  label: string; pass: boolean | null; detail: string;
}) {
  const col = pass === null ? COLORS.dim : pass ? COLORS.pass : COLORS.fail;
  const bg = pass === null ? 'rgba(100,116,139,0.1)'
    : pass ? 'rgba(16,185,129,0.08)' : 'rgba(220,38,38,0.08)';
  const glyph = pass === null ? '—' : pass ? '✓' : '✗';
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 8,
      padding: '6px 12px', background: bg,
      border: `1px solid ${col}`,
      borderRadius: 6, fontSize: 12,
      fontFamily: "'DM Mono', monospace",
    }}>
      <span style={{ color: col, fontWeight: 700 }}>{glyph}</span>
      <span style={{ color: COLORS.text }}>{label}</span>
      <span style={{ color: COLORS.muted }}>·</span>
      <span style={{ color: COLORS.muted }}>{detail}</span>
    </div>
  );
}

export default function CascadeChart({ projectId, color: _color }: Props) {
  const [data, setData] = useState<CascadeData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    api.getDocumentText(projectId, 'cascade_analysis.json')
      .then(txt => {
        if (cancelled) return;
        try {
          setData(JSON.parse(txt) as CascadeData);
        } catch (e) {
          setErr('cascade_analysis.json is not valid JSON');
        }
      })
      .catch(() => {
        if (!cancelled) setErr('not-generated');
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [projectId]);

  if (loading) {
    return (
      <div style={{
        padding: 20, color: COLORS.muted, fontSize: 12,
        fontFamily: "'DM Mono', monospace",
      }}>Loading cascade analysis…</div>
    );
  }
  if (err || !data) return null;  // silently hide when not applicable (non-RF designs)
  if (!data.stages || data.stages.length === 0) return null;

  const direction = data.direction ?? 'rx';
  const tot = data.totals;
  const v = data.verdict;
  const claims = data.claims;

  // --- Gain domain (shared by RX + TX) ---
  const gainVals = data.stages.flatMap(s => [
    s.gain_db, s.cum_gain_db,
  ]).filter((v): v is number => v !== null && isFinite(v));
  const gainDom: [number, number] = gainVals.length > 0
    ? [Math.min(0, Math.min(...gainVals) - 2), Math.max(0, Math.max(...gainVals)) + 5]
    : [0, 30];

  if (direction === 'tx') {
    // --- TX domains ---
    const poutVals = data.stages.flatMap(s => [
      s.pout_dbm, s.pout_computed_dbm,
    ]).filter((v): v is number => v !== null && v !== undefined && isFinite(v));
    const oip3Vals = data.stages.flatMap(s => [
      s.oip3_dbm, s.cum_oip3_dbm,
    ]).filter((v): v is number => v !== null && v !== undefined && isFinite(v));

    const poutDom: [number, number] = poutVals.length > 0
      ? [Math.min(...poutVals) - 3, Math.max(...poutVals) + 3]
      : [-20, 30];
    const oip3Dom: [number, number] = oip3Vals.length > 0
      ? [Math.min(...oip3Vals) - 3, Math.max(...oip3Vals) + 3]
      : [0, 50];

    return (
      <div style={{ marginTop: 18, marginBottom: 20 }}>
        <div style={{
          fontFamily: 'Syne', fontSize: 18, fontWeight: 700,
          color: COLORS.text, marginBottom: 4,
        }}>
          TX Cascade Analysis
          <span style={{
            marginLeft: 8, fontSize: 10,
            padding: '2px 8px', borderRadius: 4,
            background: 'rgba(220,38,38,0.1)',
            color: COLORS.fail, fontFamily: "'DM Mono', monospace",
            letterSpacing: 0.5, fontWeight: 600,
            verticalAlign: 'middle',
          }}>TRANSMITTER</span>
        </div>
        <div style={{ fontSize: 12, color: COLORS.muted, marginBottom: 14 }}>
          Forward-cascade output power + output-referred OIP3 + PAE roll-up
          across the {tot.stage_count}-stage TX chain
          {tot.input_power_dbm !== null && tot.input_power_dbm !== undefined
            ? ` (system input ${fmt(tot.input_power_dbm, 1)} dBm)`
            : ''}.
        </div>

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 18 }}>
          <VerdictPill
            label="System Pout"
            pass={v.pout_pass ?? null}
            detail={
              tot.pout_dbm !== null && tot.pout_dbm !== undefined
                ? `${fmt(tot.pout_dbm, 1)} dBm${claims.pout_dbm !== null && claims.pout_dbm !== undefined
                    ? ` vs ${fmt(claims.pout_dbm, 1)} claim (${fmt(v.pout_headroom_db, 1, ' dB')})`
                    : ''}`
                : 'no data'
            }
          />
          <VerdictPill
            label="Total Gain"
            pass={v.gain_pass}
            detail={
              tot.gain_db !== null
                ? `${fmt(tot.gain_db, 1)} dB${claims.total_gain_db !== null
                    ? ` vs ${fmt(claims.total_gain_db, 1)} claim (Δ ${fmt(v.gain_delta_db, 1, ' dB')})`
                    : ''}`
                : 'no data'
            }
          />
          <VerdictPill
            label="System OIP3"
            pass={v.oip3_pass ?? null}
            detail={
              tot.oip3_dbm !== null && tot.oip3_dbm !== undefined
                ? `${fmt(tot.oip3_dbm, 1)} dBm${claims.oip3_dbm !== null && claims.oip3_dbm !== undefined
                    ? ` vs ${fmt(claims.oip3_dbm, 1)} claim`
                    : ''}`
                : 'no data'
            }
          />
          <VerdictPill
            label="System PAE"
            pass={v.pae_pass ?? null}
            detail={
              tot.pae_pct !== null && tot.pae_pct !== undefined
                ? `${fmt(tot.pae_pct, 1)} %${claims.pae_pct !== null && claims.pae_pct !== undefined
                    ? ` vs ${fmt(claims.pae_pct, 1)} % claim`
                    : ''}`
                : 'no data'
            }
          />
          <VerdictPill
            label="Compression"
            pass={v.no_compression ?? null}
            detail={
              (tot.compression_warnings && tot.compression_warnings.length > 0)
                ? `${tot.compression_warnings.length} stage(s) over Pout spec`
                : 'all stages within Pout spec'
            }
          />
        </div>

        <StageChart
          stages={data.stages}
          metric="Pout"
          unit="dBm"
          domain={poutDom}
          color={COLORS.bar_iip3}
          title="Output Power Cascade"
          subtitle="Per-stage Pout spec vs. cumulative drive level (diamond) propagating from system input."
          accessor={s => s.pout_dbm ?? null}
          cumAccessor={s => s.pout_computed_dbm ?? null}
          claim={claims.pout_dbm ?? null}
        />
        <StageChart
          stages={data.stages}
          metric="Gain"
          unit="dB"
          domain={gainDom}
          color={COLORS.bar_gain}
          title="Gain Cascade"
          subtitle="Per-stage gain accumulating forward. Passive elements (harmonic filters, isolators) shown as negative."
          accessor={s => s.gain_db}
          cumAccessor={s => s.cum_gain_db}
          claim={claims.total_gain_db}
        />
        <StageChart
          stages={data.stages}
          metric="OIP3"
          unit="dBm"
          domain={oip3Dom}
          color={COLORS.bar_nf}
          title="Output IP3 Cascade"
          subtitle="Per-stage OIP3 vs. cumulative system OIP3 referred to each stage's output. Last-stage dominance."
          accessor={s => s.oip3_dbm ?? null}
          cumAccessor={s => s.cum_oip3_dbm ?? null}
          claim={claims.oip3_dbm ?? null}
        />
      </div>
    );
  }

  // --- Receiver (default) ---
  const nfVals = data.stages.flatMap(s => [
    s.nf_db, s.cum_nf_db,
  ]).filter((v): v is number => v !== null && isFinite(v));
  const iip3Vals = data.stages.flatMap(s => [
    s.iip3_dbm, s.cum_iip3_dbm,
  ]).filter((v): v is number => v !== null && isFinite(v));

  const nfDom: [number, number] = nfVals.length > 0
    ? [0, Math.max(...nfVals) + 2]
    : [0, 10];
  const iip3Dom: [number, number] = iip3Vals.length > 0
    ? [Math.min(...iip3Vals) - 5, Math.max(...iip3Vals) + 5]
    : [-20, 30];

  return (
    <div style={{ marginTop: 18, marginBottom: 20 }}>
      <div style={{
        fontFamily: 'Syne', fontSize: 18, fontWeight: 700,
        color: COLORS.text, marginBottom: 4,
      }}>RF Cascade Analysis</div>
      <div style={{
        fontSize: 12, color: COLORS.muted, marginBottom: 14,
      }}>
        Friis-derived stage-by-stage accumulation of noise figure, gain, and
        input-referred IP3 across the {tot.stage_count}-stage signal chain.
      </div>

      {/* Verdict pills — the top-line pass/fail against P1 claims */}
      <div style={{
        display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 18,
      }}>
        <VerdictPill
          label="System NF"
          pass={v.nf_pass ?? null}
          detail={
            tot.nf_db !== null && tot.nf_db !== undefined
              ? `${fmt(tot.nf_db, 2)} dB${claims.nf_db !== null && claims.nf_db !== undefined
                  ? ` vs ${fmt(claims.nf_db, 2)} claim (${fmt(v.nf_headroom_db, 2, ' dB')} headroom)`
                  : ''}`
              : 'no data'
          }
        />
        <VerdictPill
          label="Total Gain"
          pass={v.gain_pass}
          detail={
            tot.gain_db !== null
              ? `${fmt(tot.gain_db, 1)} dB${claims.total_gain_db !== null
                  ? ` vs ${fmt(claims.total_gain_db, 1)} claim (Δ ${fmt(v.gain_delta_db, 1, ' dB')})`
                  : ''}`
              : 'no data'
          }
        />
        <VerdictPill
          label="Cascade IIP3"
          pass={v.iip3_pass ?? null}
          detail={
            tot.iip3_dbm !== null && tot.iip3_dbm !== undefined
              ? `${fmt(tot.iip3_dbm, 1)} dBm${claims.iip3_dbm !== null && claims.iip3_dbm !== undefined
                  ? ` vs ${fmt(claims.iip3_dbm, 1)} claim (${fmt(v.iip3_headroom_db, 1, ' dB')} headroom)`
                  : ''}`
              : 'no data'
          }
        />
      </div>

      <StageChart
        stages={data.stages}
        metric="NF"
        unit="dB"
        domain={nfDom}
        color={COLORS.bar_nf}
        title="Noise Figure Cascade"
        subtitle="Per-stage NF vs. cumulative cascade NF (Friis: later stages divided by preceding gain)."
        accessor={s => s.nf_db}
        cumAccessor={s => s.cum_nf_db}
        claim={claims.nf_db ?? null}
      />
      <StageChart
        stages={data.stages}
        metric="Gain"
        unit="dB"
        domain={gainDom}
        color={COLORS.bar_gain}
        title="Gain Cascade"
        subtitle="Per-stage gain (passive filters shown as negative — insertion loss)."
        accessor={s => s.gain_db}
        cumAccessor={s => s.cum_gain_db}
        claim={claims.total_gain_db}
      />
      <StageChart
        stages={data.stages}
        metric="IIP3"
        unit="dBm"
        domain={iip3Dom}
        color={COLORS.bar_iip3}
        title="Input IP3 Cascade"
        subtitle="Per-stage IIP3 referred to that stage's input vs. cascade IIP3 referred to system input."
        accessor={s => s.iip3_dbm}
        cumAccessor={s => s.cum_iip3_dbm}
        claim={claims.iip3_dbm ?? null}
      />
    </div>
  );
}
