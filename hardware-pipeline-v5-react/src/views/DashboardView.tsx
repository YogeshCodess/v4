/*
 * P18 — Holographic Landing Dashboard
 *
 * Renders when no project is loaded (replaces LandingPage). Pulls data
 * from `/api/v1/projects` + per-project `/status`, then composes the
 * 5 sections from `dashboard-lab.html`:
 *
 *   1. Top nav (logo + menu + Re-run / Execute CTAs)
 *   2. Hero split pane (title/chips/stats + holo gauge)
 *   3. KPI strip (Time Saved / Error Reduction / Cost Impact / Confidence)
 *   4. Phase constellation (12 cards: 11 phases + Extend slot)
 *   5. Live + Events two-column
 *
 * Hard contract from `plans/P18-dashboard-holographic-landing.md`:
 *   - Zero-touch: never loads a project into the current tab via Load.
 *   - Load → opens new tab via window.open('/app?project=ID').
 *   - Create → SAME tab (user deliberately starts fresh) via the
 *     existing CreateProjectModal, surfaced through onCreate prop.
 *   - All CSS scoped under `.dashboard-root`.
 *   - No backend endpoints required — derives everything client-side
 *     from existing /api/v1/projects + per-project /status.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { Project, StatusesRaw } from '../types';
import { PHASES } from '../data/phases';
import { api } from '../api';
import '../styles/dashboard.css';

interface DashboardViewProps {
  onCreate: () => void;
  onLoadProject: (p: Project) => void;
  /** Show the Load Project modal in the current (dashboard) tab.
   *  Used as the fallback when window.open is blocked for the
   *  Projects nav link. */
  onShowLoadModal: () => void;
  /** Open the LLM Settings modal from the dashboard. */
  onLlmSettings: () => void;
}

interface ProjectAggregate {
  project: Project;
  raw: StatusesRaw;
  done: number;
  total: number;
  failed: number;
  running: { id: string; name: string } | null;
  lastUpdated: number;       // ms epoch of the most recent status update
  staleIds: string[];        // phases re-flagged stale by /status payload
}

interface DashboardData {
  projects: ProjectAggregate[];
  totalDone: number;
  totalPhases: number;
  totalFailed: number;
  loading: boolean;
  error: string | null;
  /** 14-element daily series of cumulative completed phases across all
   *  projects, oldest → newest. Powers the KPI sparklines so the curves
   *  reflect REAL pipeline activity, not placeholder shapes. */
  dailyCompletion: number[];
  /** Same indexing — daily failures (used to derive error-reduction trend). */
  dailyFailure: number[];
}

/** Parse the backend's updated_at field as UTC.
 *  Backend stores naive UTC datetimes (e.g. "2026-04-22T05:44:00.442754")
 *  with no timezone suffix. JavaScript's Date() treats those as LOCAL
 *  time, which displays as user-tz-offset hours in the future for any
 *  timezone east of UTC (5h30m off for IST). Appending 'Z' forces UTC
 *  parsing. If the backend ever starts sending tz-aware strings (with
 *  Z, +/-, or offset already), we leave them alone. */
function parseBackendTs(s: string | undefined): number {
  if (!s) return NaN;
  const hasTz = /[Zz]$|[+-]\d{2}:?\d{2}$/.test(s);
  return new Date(hasTz ? s : s + 'Z').getTime();
}

function fmtTimeAgo(ms: number): string {
  const delta = Date.now() - ms;
  if (delta < 60_000) return 'now';
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)} min`;
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)} hr`;
  return `${Math.floor(delta / 86_400_000)} d`;
}

/** "now" for fresh timestamps, "5 min ago" / "2 hr ago" / "1 d ago"
 *  otherwise. Wraps fmtTimeAgo so "now ago" never renders. */
function fmtTimeAgoLabel(ms: number): string {
  const s = fmtTimeAgo(ms);
  return s === 'now' ? 'now' : `${s} ago`;
}

/** Format an INR value into Indian denominations.
 *  Returns { value, unit } so the KpiCard can render the unit
 *  separately in the smaller emphasis style. */
function fmtInr(rupees: number): { value: string; unit: string } {
  const CRORE = 1e7;   // 1,00,00,000
  const LAKH = 1e5;    // 1,00,000
  if (rupees >= CRORE) return { value: (rupees / CRORE).toFixed(1), unit: 'Cr' };
  if (rupees >= LAKH)  return { value: (rupees / LAKH).toFixed(1),  unit: 'L'  };
  return { value: Math.round(rupees / 1000).toString(), unit: 'K' };
}

/** KPI values + error-reduction derived from REAL completion counts. */
function calcKpis(projects: ProjectAggregate[]) {
  const totalDone = projects.reduce((acc, p) => acc + p.done, 0);
  const totalPhases = projects.reduce((acc, p) => acc + p.total, 0);
  const totalFailed = projects.reduce((acc, p) => acc + p.failed, 0);
  // Time saved: 4.25 hrs/phase (manual-engineering baseline per the P18 plan).
  const hoursSaved = totalDone * 4.25;
  // Cost impact: hoursSaved × 52 wk × ₹3000/hr (Indian senior-eng rate).
  // Output is in INR; UI formats as Crore (1 Cr = 10^7).
  const INR_RATE = 3000;
  const costImpactPerYear = hoursSaved * 52 * INR_RATE;
  // Error reduction = (1 - failure_rate) × 100 over phases that ran.
  // Phases that ran = completed + failed; pending phases excluded.
  const ranPhases = totalDone + totalFailed;
  const errorReductionPct = ranPhases > 0
    ? Math.round((1 - totalFailed / ranPhases) * 100)
    : 0;
  // Confidence — average completed-phase fraction across projects.
  const confidence = projects.length > 0
    ? Math.round(
        (projects.reduce((a, p) => a + (p.total > 0 ? p.done / p.total : 0), 0)
          / projects.length) * 100
      )
    : 0;
  return { totalDone, totalPhases, totalFailed, hoursSaved, costImpactPerYear, errorReductionPct, confidence };
}

/** Build a 14-day series (oldest → newest, today = last) of cumulative
 *  completion counts across all projects. Used to power the KPI
 *  sparklines with REAL data instead of placeholder shapes. */
function buildDailySeries(projects: ProjectAggregate[]): { completed: number[]; failed: number[] } {
  const N_DAYS = 14;
  const today = new Date();
  today.setHours(23, 59, 59, 999);
  const todayMs = today.getTime();
  const dayMs = 24 * 60 * 60 * 1000;
  // Per-day counters (oldest → newest)
  const completed = new Array(N_DAYS).fill(0);
  const failed = new Array(N_DAYS).fill(0);

  for (const agg of projects) {
    for (const phase of PHASES) {
      const e = agg.raw[phase.id];
      if (!e || !e.updated_at) continue;
      const t = parseBackendTs(e.updated_at);
      if (Number.isNaN(t)) continue;
      const ageDays = Math.floor((todayMs - t) / dayMs);
      if (ageDays < 0 || ageDays >= N_DAYS) continue;
      const idx = (N_DAYS - 1) - ageDays;
      if (e.status === 'completed') completed[idx] += 1;
      else if (e.status === 'failed') failed[idx] += 1;
    }
  }
  // Cumulative (so the line trends UP over time — the "Time Saved"
  // story is "the total grew" not "today's daily count").
  for (let i = 1; i < N_DAYS; i++) {
    completed[i] += completed[i - 1];
    failed[i] += failed[i - 1];
  }
  return { completed, failed };
}

/** Normalise a numeric series to 0..1 for spark rendering. Empty / flat
 *  series fall back to a gentle linear ramp so the spark never disappears. */
function normalize(series: number[], fallbackMax = 1): number[] {
  const max = Math.max(fallbackMax, ...series);
  if (max === 0) return series.map(() => 0);
  return series.map(v => v / max);
}

/** Pick latest project for the hero pane (most recently updated). */
function pickFeatured(projects: ProjectAggregate[]): ProjectAggregate | null {
  if (projects.length === 0) return null;
  return [...projects].sort((a, b) => b.lastUpdated - a.lastUpdated)[0];
}

/** Derive flat event list from all projects' status timestamps. */
function deriveEvents(projects: ProjectAggregate[]) {
  const events: { id: string; cls: 'ok' | 'run' | 'warn' | 'info' | 'bad'; ic: string; msg: React.ReactNode; ts: number; project: Project }[] = [];
  for (const agg of projects) {
    for (const phase of PHASES) {
      const e = agg.raw[phase.id];
      if (!e || !e.updated_at) continue;
      const ts = parseBackendTs(e.updated_at);
      if (Number.isNaN(ts)) continue;
      let cls: 'ok' | 'run' | 'warn' | 'info' | 'bad' = 'info';
      let ic = 'i';
      if (e.status === 'completed') { cls = 'ok'; ic = '✓'; }
      else if (e.status === 'in_progress') { cls = 'run'; ic = '⟳'; }
      else if (e.status === 'failed') { cls = 'bad'; ic = '!'; }
      else if (e.status === 'draft_pending') { cls = 'warn'; ic = '✎'; }
      events.push({
        id: `${agg.project.id}-${phase.id}-${ts}`,
        cls, ic, ts,
        msg: <><b>{phase.code} {phase.name}</b> · {agg.project.name} · {e.status}</>,
        project: agg.project,
      });
    }
  }
  events.sort((a, b) => b.ts - a.ts);
  return events.slice(0, 8);
}

/** Holo gauge with 48 radial ticks rendered once on mount. */
function HoloGauge({ pct, liveLabel }: { pct: number; liveLabel: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const holo = ref.current;
    if (!holo) return;
    holo.querySelectorAll('.tick').forEach(t => t.remove());
    for (let i = 0; i < 48; i++) {
      const tick = document.createElement('div');
      tick.className = 'tick';
      tick.style.opacity = i % 4 === 0 ? '0.9' : '0.25';
      tick.style.height = i % 4 === 0 ? '12px' : '6px';
      tick.style.transform = `rotate(${i * (360 / 48)}deg)`;
      holo.appendChild(tick);
    }
  }, []);
  return (
    <div className="holo" ref={ref}>
      <div className="ring r1"></div>
      <div className="ring r2"></div>
      <div className="orbit"></div>
      <div className="center">
        <div>
          <div className="num serif">{pct}<span style={{ fontSize: 22 }}>%</span></div>
          <div className="lab">{liveLabel}</div>
        </div>
      </div>
    </div>
  );
}

/** Single KPI card — gradient sparkline rendered as an SVG path. */
function KpiCard({
  label, value, unit, color, sparkPoints, footnote, delta,
}: {
  label: string;
  value: string;
  unit?: string;
  color: string;
  sparkPoints: number[];        // 0..1 normalized values, length 11
  footnote?: string;
  delta?: string;
}) {
  // Build the spark path from points (200 wide x 36 tall).
  const path = useMemo(() => {
    const xs = sparkPoints.length;
    const w = 200, h = 36;
    if (xs === 0) return { line: '', area: '' };
    const points = sparkPoints.map((v, i) => {
      const x = (i * w) / (xs - 1 || 1);
      const y = h - 4 - v * (h - 8);
      return [x, y];
    });
    const line = points.map(([x, y], i) => (i === 0 ? `M${x},${y}` : `L${x},${y}`)).join(' ');
    const area = `${line} L${w},${h} L0,${h} Z`;
    return { line, area };
  }, [sparkPoints]);
  const gradId = `dash-spark-${label.replace(/[^a-z]/gi, '')}`;
  return (
    <div className="kcard" style={{ ['--col' as never]: color + '38' }}>
      <div className="bg"></div>
      <div className="lbl">{label}</div>
      <div className="val serif">{value}{unit && <em> {unit}</em>}</div>
      <div className="spark">
        <svg viewBox="0 0 200 36" width="100%" height="100%" preserveAspectRatio="none">
          <defs>
            <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.5" />
              <stop offset="100%" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={path.area} fill={`url(#${gradId})`} />
          <path d={path.line} fill="none" stroke={color} strokeWidth="2" />
        </svg>
      </div>
      {delta && <div className="delta">{delta}</div>}
      {footnote && <div className="footnote">{footnote}</div>}
    </div>
  );
}

export default function DashboardView({ onCreate, onLoadProject, onShowLoadModal, onLlmSettings }: DashboardViewProps) {
  const [data, setData] = useState<DashboardData>({
    projects: [], totalDone: 0, totalPhases: 0, totalFailed: 0,
    loading: true, error: null,
    dailyCompletion: new Array(14).fill(0),
    dailyFailure: new Array(14).fill(0),
  });
  // Tracks the timestamp of the last successful refresh — drives the
  // "Updated Xs ago" label next to the Refresh button.
  const [lastRefreshAt, setLastRefreshAt] = useState<number>(0);
  // Bumps every 15s so fmtTimeAgo recomputes between polls (otherwise
  // "now" stays stuck for 30s until the next refresh).
  const [, setNowTick] = useState(0);
  useEffect(() => {
    const h = setInterval(() => setNowTick(n => n + 1), 15_000);
    return () => clearInterval(h);
  }, []);

  const refresh = async () => {
    try {
      const projects = await api.listProjects();
      // Use getFullStatus so we also pick up stale_phase_ids per project
      // (lets the constellation card show a "stale" badge later).
      const aggs = await Promise.all(projects.map(async (p): Promise<ProjectAggregate> => {
        let raw: StatusesRaw = {};
        let staleIds: string[] = [];
        try {
          const full = await api.getFullStatus(p.id);
          // Reuse the same flatten logic as api.getStatusRaw — duplicated
          // here so we don't double-fetch /status per project.
          for (const [key, val] of Object.entries(full.phase_statuses || {})) {
            if (typeof val === 'string') {
              raw[key] = { status: val as StatusesRaw[string]['status'] };
            } else if (val && typeof val === 'object' && 'status' in (val as object)) {
              const entry = val as { status: string; updated_at?: string };
              raw[key] = {
                status: entry.status as StatusesRaw[string]['status'],
                updated_at: entry.updated_at,
              };
            }
          }
          staleIds = full.stale_phase_ids || [];
        } catch { /* leave raw empty if a single project errors */ }

        // Only AI (auto) phases count toward Confidence + Pipeline Completion.
        // Manual phases (currently just P5 PCB Layout) require external EDA
        // tools and aren't part of the AI pipeline's success rate.
        const aiPhases = PHASES.filter(ph => !ph.manual);
        const all = aiPhases.map(ph => raw[ph.id]).filter(Boolean) as StatusesRaw[string][];
        const done = all.filter(e => e.status === 'completed').length;
        const failed = all.filter(e => e.status === 'failed').length;
        const total = aiPhases.length;
        const runningEntry = PHASES.find(ph => raw[ph.id]?.status === 'in_progress');
        let lastUpdated = 0;
        for (const e of all) {
          if (!e.updated_at) continue;
          const t = parseBackendTs(e.updated_at);
          if (Number.isNaN(t)) continue;
          if (t > lastUpdated) lastUpdated = t;
        }
        return {
          project: p,
          raw, done, total, failed,
          running: runningEntry ? { id: runningEntry.id, name: runningEntry.name } : null,
          lastUpdated,
          staleIds,
        };
      }));
      const totalDone = aggs.reduce((a, p) => a + p.done, 0);
      const totalPhases = aggs.reduce((a, p) => a + p.total, 0);
      const totalFailed = aggs.reduce((a, p) => a + p.failed, 0);
      const series = buildDailySeries(aggs);
      setData({
        projects: aggs,
        totalDone, totalPhases, totalFailed,
        loading: false, error: null,
        dailyCompletion: series.completed,
        dailyFailure: series.failed,
      });
      setLastRefreshAt(Date.now());
    } catch (err) {
      setData(d => ({ ...d, loading: false, error: err instanceof Error ? err.message : 'Backend offline' }));
    }
  };

  // Initial load + 30s polling (slow — dashboard isn't where active work happens).
  useEffect(() => {
    refresh();
    const handle = setInterval(refresh, 30_000);
    return () => clearInterval(handle);
  }, []);

  const featured = pickFeatured(data.projects);
  const kpis = calcKpis(data.projects);
  const events = deriveEvents(data.projects);
  const overallPct = data.totalPhases > 0 ? Math.round((data.totalDone / data.totalPhases) * 100) : 0;

  // Open a project in a NEW TAB (zero-touch contract — never loads in current tab).
  const handleOpenProject = (p: Project) => {
    try {
      const w = window.open(`${window.location.pathname}?project=${p.id}`, '_blank');
      if (!w) {
        // Pop-up blocked — fall back to in-tab load (rare; user can opt out by closing).
        onLoadProject(p);
      }
    } catch {
      onLoadProject(p);
    }
  };

  return (
    <div className="dashboard-root">
      <div className="canvas">

        {/* ───── 1. Top nav ───── */}
        <div className="nav">
          <div className="brand">
            <div className="orb"></div>
            <div>
              <div className="wm serif">Silicon to Software <em>(S2S)</em></div>
            </div>
          </div>
          <div className="menu">
            <span className="active">Dashboard</span>
            <a onClick={() => document.getElementById('dash-phases')?.scrollIntoView({ behavior: 'smooth' })} style={{ cursor: 'pointer' }}>Phases</a>
            <a
              onClick={() => {
                try {
                  const w = window.open(
                    `${window.location.pathname}?action=load`,
                    '_blank',
                  );
                  if (!w) onShowLoadModal();
                } catch {
                  onShowLoadModal();
                }
              }}
              style={{ cursor: 'pointer' }}
            >Projects</a>
            <a onClick={() => document.getElementById('dash-events')?.scrollIntoView({ behavior: 'smooth' })} style={{ cursor: 'pointer' }}>Activity</a>
          </div>
          <div className="actions">
            {lastRefreshAt > 0 && (
              <span
                style={{
                  fontFamily: "'IBM Plex Mono'", fontSize: 10,
                  letterSpacing: '0.18em', textTransform: 'uppercase',
                  color: 'var(--dim2)',
                }}
                title={`Last refresh: ${new Date(lastRefreshAt).toLocaleTimeString()}`}
              >
                Updated {fmtTimeAgoLabel(lastRefreshAt)}
              </span>
            )}
            <button className="btn" onClick={refresh}>
              {data.loading ? '⟳ Loading…' : '↺ Refresh'}
            </button>
            <button className="btn" onClick={onLlmSettings}>⚙ LLM Settings</button>
            <button className="btn btn-iris" onClick={onCreate}>▶ Start new project</button>
          </div>
        </div>

        {data.error && (
          <div className="err-banner">
            <span>Backend unavailable — {data.error}</span>
            <button onClick={refresh}>Retry</button>
          </div>
        )}

        {/* ───── 2. Hero split pane ───── */}
        <div className="hero">
          <div className="pane hero-left">
            <span className="edge-light"></span>
            <div className="sheen"></div>
            <div className="chiprow">
              {featured ? (
                <span className="chip good">
                  ● {featured.running
                    ? 'Live'
                    : featured.done === featured.total
                      ? 'Complete'
                      : featured.failed > 0 ? 'Has failures' : 'On Track'}
                </span>
              ) : (
                <span className="chip">No projects yet</span>
              )}
            </div>
            <div className="title serif">
              {featured
                ? <><em>{featured.project.name}</em>, designed in a conversation.</>
                : <>Hardware design, <em>compressed</em>.</>}
            </div>
            <p className="lead">
              {featured
                ? <>Eleven AI phases move a natural-language brief through requirements, compliance, netlist, glue logic and software — with the engineer sitting in the loop. {featured.done} done, {featured.running ? '1 live' : '0 live'}, {featured.total - featured.done - (featured.running ? 1 : 0)} queued.</>
                : <>Eleven AI phases move a natural-language brief through requirements, compliance, netlist, glue logic and software — with the engineer sitting in the loop. Start a project to see live telemetry.</>}
            </p>
            <div className="hero-bot">
              <div className="stat">
                <div className="lbl">In Pipeline</div>
                <div className="val serif">{data.projects.length}<em>{data.projects.length === 1 ? ' project' : ' projects'}</em></div>
                <div className="delta">▲ live status</div>
              </div>
              <div className="stat">
                <div className="lbl">Confidence</div>
                <div className="val serif">{kpis.confidence}<em>%</em></div>
                <div className="delta">avg across projects</div>
              </div>
              <div className="stat">
                <div className="lbl">Phases Done</div>
                <div className="val serif">{kpis.totalDone}<em>/{kpis.totalPhases}</em></div>
                <div className="delta">{overallPct}% overall</div>
              </div>
            </div>
          </div>

          <div className="pane hero-right">
            <span className="edge-light"></span>
            <div className="sheen"></div>
            <div className="hg-head">
              <div>
                <div className="lbl">Pipeline completion</div>
                <div className="serif" style={{ fontSize: 26, lineHeight: 1.1, marginTop: 4 }}>
                  Live <em style={{ color: 'var(--iris-b)', fontStyle: 'italic' }}>telemetry</em>
                </div>
              </div>
              {featured?.running && (
                <span className="chip pink">{featured.running.id} · Live</span>
              )}
            </div>
            <HoloGauge pct={overallPct} liveLabel="Overall" />
            <div className="hg-mini">
              <div className="mono"><span style={{ color: 'var(--dim)' }}>DONE</span><b>{kpis.totalDone} / {kpis.totalPhases}</b></div>
              <div className="mono"><span style={{ color: 'var(--dim)' }}>LIVE</span>
                <b style={{ color: 'var(--iris-b)' }}>{featured?.running ? `${featured.running.id} · ${featured.running.name}` : '—'}</b>
              </div>
              <div className="mono"><span style={{ color: 'var(--dim)' }}>PROJECTS</span><b>{data.projects.length}</b></div>
            </div>
          </div>
        </div>

        {/* ───── 3. KPI strip — real data from 14-day completion series ───── */}
        <div className="kpi-row">
          <KpiCard
            label="Time Saved"
            value={kpis.hoursSaved.toFixed(0)}
            unit="hrs"
            color="#b388ff"
            sparkPoints={normalize(data.dailyCompletion.map(c => c * 4.25))}
            delta={data.totalDone > 0
              ? `▲ ${data.totalDone} phases × 4.25 hrs each`
              : '— no completions yet'}
            footnote="based on 4.25 hrs/phase manual baseline"
          />
          <KpiCard
            label="Error Reduction"
            value={kpis.errorReductionPct.toString()}
            unit="%"
            color="#ff5ca8"
            sparkPoints={normalize(
              data.dailyCompletion.map((c, i) => {
                const f = data.dailyFailure[i];
                const ran = c + f;
                return ran > 0 ? c / ran : 0;
              })
            )}
            delta={kpis.totalFailed > 0
              ? `${kpis.totalFailed} failure${kpis.totalFailed > 1 ? 's' : ''} of ${kpis.totalDone + kpis.totalFailed} runs`
              : kpis.totalDone > 0
                ? `▲ 0 failures across ${kpis.totalDone} runs`
                : '— no runs yet'}
          />
          {(() => {
            const cost = fmtInr(kpis.costImpactPerYear);
            return (
              <KpiCard
                label="Cost Impact / yr"
                value={`₹${cost.value}`}
                unit={cost.unit}
                color="#5ce1ff"
                sparkPoints={normalize(data.dailyCompletion.map(c => c * 4.25 * 52 * 3000))}
                delta={data.totalDone > 0
                  ? `▲ projected · annualised`
                  : '—'}
                footnote="engineering cost at ₹3000/hr × 52 wk"
              />
            );
          })()}
          <KpiCard
            label="Confidence"
            value={kpis.confidence.toString()}
            unit="%"
            color="#ffc65c"
            sparkPoints={normalize(
              data.dailyCompletion.map(c =>
                Math.min(1, c / (data.totalPhases || 1))
              )
            )}
            delta="weighted across projects"
          />
        </div>

        {/* ───── 4. Phase constellation ───── */}
        <div id="dash-phases" className="section" style={{ scrollMarginTop: 100 }}>
          <div className="section-head">
            <div>
              <div className="lbl">// pipeline</div>
              <h2 className="serif">Eleven <em>phases</em></h2>
            </div>
            <div className="meta">
              {kpis.totalDone} done · {data.projects.filter(p => p.running).length} live
              · {PHASES.filter(p => p.manual).length} manual
            </div>
          </div>
          <div className="phase-grid">
            {PHASES.map(phase => {
              const phaseColor = phase.color || '#b388ff';
              // Aggregate phase status across all projects (most-recent wins).
              let status: 'completed' | 'in_progress' | 'failed' | 'manual' | 'pending' = 'pending';
              let footMeta: string | null = null;
              if (phase.manual) status = 'manual';
              for (const agg of data.projects) {
                const e = agg.raw[phase.id];
                if (!e) continue;
                if (e.status === 'in_progress') { status = 'in_progress'; break; }
                if (e.status === 'completed' && status !== 'in_progress') status = 'completed';
                if (e.status === 'failed' && status === 'pending') status = 'failed';
              }
              const completedCount = data.projects.filter(p => p.raw[phase.id]?.status === 'completed').length;
              if (completedCount > 0) footMeta = `${completedCount}/${data.projects.length} projects`;
              const stClass = status === 'completed' ? 'ok'
                : status === 'in_progress' ? 'run'
                : status === 'failed' ? 'bad'
                : status === 'manual' ? 'm' : '';
              const stLabel = status === 'completed' ? '✓ Done'
                : status === 'in_progress' ? '⟳ Live'
                : status === 'failed' ? '✗ Failed'
                : status === 'manual' ? '◼ Manual'
                : '⋯ Pending';
              const fillPct = status === 'completed' ? 100
                : status === 'in_progress' ? 60
                : status === 'manual' ? 0
                : 0;
              return (
                <div
                  key={phase.id}
                  className="pcard"
                  style={{ ['--c' as never]: phaseColor }}
                >
                  <div className="aura"></div>
                  <div className="row">
                    <div className="mk">{phase.id.replace('P', '')}</div>
                    <span className={`st ${stClass}`}>{stLabel}</span>
                  </div>
                  <div className="name serif">{phase.name}</div>
                  <div className="sub">{phase.tagline}</div>
                  <div className="bar">
                    <div className={`fill ${status === 'in_progress' ? 'live' : ''}`} style={{ width: `${fillPct}%` }}></div>
                  </div>
                  <div className="foot">
                    <span>{footMeta || phase.time}</span>
                    <b>{status === 'completed' ? '✓' : status === 'in_progress' ? '…' : '—'}</b>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* ───── 5. Live + Events ───── */}
        <div id="dash-projects" className="two" style={{ scrollMarginTop: 100 }}>

          <div className="pane live">
            <span className="edge-light"></span>
            <div className="sheen"></div>
            <div className="section-head" style={{ margin: '0 0 8px' }}>
              <div>
                <div className="lbl">// projects</div>
                <h2 className="serif" style={{ fontSize: 28 }}>
                  {data.projects.length === 0 ? 'No projects yet' : <>Open a <em>project</em></>}
                </h2>
              </div>
              <div className="meta">
                {data.projects.length === 0 ? 'Start one →' : `${data.projects.length} total · click → new tab`}
              </div>
            </div>
            {data.projects.length === 0 ? (
              <div className="empty">
                <b>No projects yet.</b>
                Click "Start new project" above to create your first hardware design.
              </div>
            ) : (
              <div className="flowchain">
                {data.projects.slice(0, 8).map((agg, i) => {
                  const isRunning = !!agg.running;
                  const isComplete = agg.done === agg.total;
                  const stepClass = isComplete ? 'done' : isRunning ? 'active' : '';
                  const verdict = isComplete
                    ? '✓ Complete'
                    : isRunning
                      ? `⟳ ${agg.running!.id}`
                      : agg.failed > 0
                        ? `✗ ${agg.failed} failed`
                        : `${agg.done}/${agg.total}`;
                  const verdictColor = isComplete
                    ? 'var(--good)'
                    : isRunning
                      ? 'var(--iris-b)'
                      : agg.failed > 0 ? 'var(--bad)' : 'var(--dim)';
                  return (
                    <div
                      key={agg.project.id}
                      className={`step ${stepClass}`}
                      style={{ cursor: 'pointer' }}
                      onClick={() => handleOpenProject(agg.project)}
                    >
                      <div className="node">{i + 1}</div>
                      <div className="b">
                        <div className="t serif">
                          {agg.project.name}
                          {agg.staleIds.length > 0 && (
                            <span style={{
                              marginLeft: 8, fontSize: 10, color: 'var(--warn)',
                              fontFamily: "'IBM Plex Mono'", letterSpacing: '0.1em',
                            }}>
                              · {agg.staleIds.length} STALE
                            </span>
                          )}
                        </div>
                        <div className="d">
                          {(agg.project.design_type ?? 'rf').toUpperCase()}
                          {' · '}
                          {(agg.project.project_type ?? 'receiver').replace('_', ' ').toUpperCase()}
                          {agg.project.design_scope && agg.project.design_scope !== 'full' && (
                            <>
                              {' · '}
                              <span style={{ color: 'var(--iris-c)' }}>
                                {agg.project.design_scope.toUpperCase().replace('-', ' ')}
                              </span>
                            </>
                          )}
                          {isRunning && agg.running && (
                            <> · <span style={{ color: 'var(--iris-a)' }}>
                              now: {agg.running.id} {agg.running.name}
                            </span></>
                          )}
                        </div>
                        <div className="miniprog">
                          <div
                            className="fill"
                            style={{ width: `${(agg.done / agg.total) * 100}%` }}
                          ></div>
                        </div>
                      </div>
                      <div className="time">
                        <b style={{ color: verdictColor }}>{verdict}</b>
                        <div style={{ marginTop: 4, fontSize: 10, color: 'var(--dim2)' }}>
                          {agg.lastUpdated > 0 ? fmtTimeAgoLabel(agg.lastUpdated) : '—'}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div id="dash-events" className="pane events" style={{ scrollMarginTop: 100 }}>
            <span className="edge-light"></span>
            <div className="sheen"></div>
            <div className="section-head" style={{ margin: '0 0 10px' }}>
              <div>
                <div className="lbl">// recent activity</div>
                <h2 className="serif" style={{ fontSize: 28 }}>Events</h2>
              </div>
              <span className="chip good">● Stream</span>
            </div>
            {events.length === 0 ? (
              <div className="empty">No activity yet.</div>
            ) : events.map(ev => (
              <div
                key={ev.id}
                className={`event ${ev.cls}`}
                style={{ cursor: 'pointer' }}
                onClick={() => handleOpenProject(ev.project)}
                title={`Open ${ev.project.name} in new tab`}
              >
                <div className="ic">{ev.ic}</div>
                <div className="msg">{ev.msg}</div>
                <div className="t">{fmtTimeAgo(ev.ts)}</div>
              </div>
            ))}
          </div>
        </div>

      </div>
    </div>
  );
}
