import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { marked } from 'marked';
import type { Project, PhaseMeta, PhaseStatusValue } from '../types';
import { api } from '../api';
import { getVisibleDocuments, isVisibleDocument } from '../data/phases';
import { loadMermaid, renderMermaid, purgeMermaidScratch } from '../utils/mermaid';
import SchematicView from '../components/schematic/SchematicView';
import CascadeChart from '../components/CascadeChart';
import MermaidErrorBoundary from '../components/MermaidErrorBoundary';
import { sanitizeMermaid } from '../utils/mermaidSanitize';

interface DocFile {
  name: string;
  size: number;
}

interface Props {
  project: Project | null;
  phase: PhaseMeta;
  status: PhaseStatusValue;
  pipelineRunning?: boolean;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function getExt(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : '';
}

const EXT_COLOR: Record<string, string> = {
  md: '#00c6a7', docx: '#3b82f6', pdf: '#f59e0b',
  json: '#f59e0b', net: '#8b5cf6', txt: '#94a3b8',
  html: '#f59e0b', csv: '#10b981', xdc: '#8b5cf6',
  py: '#3b82f6', c: '#00c6a7', h: '#94a3b8', cpp: '#00c6a7',
  xlsx: '#10b981', xlsm: '#10b981', xls: '#10b981',
};

const EXT_LABEL: Record<string, string> = {
  md: 'Markdown', docx: 'Word Doc', pdf: 'PDF',
  json: 'JSON', net: 'Netlist', txt: 'Text',
  html: 'HTML', csv: 'CSV', xdc: 'Constraints',
  py: 'Python', c: 'C Source', h: 'C Header', cpp: 'C++ Source',
  xlsx: 'Excel', xlsm: 'Excel', xls: 'Excel',
};

function extColor(ext: string): string { return EXT_COLOR[ext] || '#64748b'; }
function extLabel(ext: string): string { return EXT_LABEL[ext] || ext.toUpperCase(); }

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const VIEWABLE = new Set(['md', 'txt', 'json', 'html', 'csv', 'net', 'xdc', 'py', 'c', 'h', 'cpp']);

// ──── Mermaid sanitization ────
// P26 (2026-04-25): the canonical sanitiser lives in
//  and is used by both ChatView and
// DocumentsView. Previously this file had a 250-line duplicate copy
// that drifted out of sync — fixes to mermaidSanitize.ts had ZERO
// effect here, which is why the same mermaid bug kept reappearing
// for the user across multiple project runs ("again and again and
// again ufff"). Now both views import the same function.
const sanitizeMermaidCode = sanitizeMermaid;
// ── Marked setup (marked v17 — use marked.use() not deprecated setOptions) ───
// P26 #20 (2026-04-26): every external link rendered into the docs view
// gets `target="_blank"` + `rel="noopener noreferrer"` so clicking a
// datasheet / DigiKey / Mouser URL opens in a new tab and doesn't navigate
// away from the running pipeline. In-page anchors (`href="#section"`) and
// relative links keep their default behaviour.
marked.use({
  gfm: true,
  breaks: false,
  renderer: {
    link(token: { href: string; title?: string | null; text: string }) {
      const href = token.href || '';
      const title = token.title || '';
      const text = token.text || '';
      const isExternal = /^(https?:)?\/\//i.test(href) || href.startsWith('mailto:');
      const titleAttr = title ? ` title="${title.replace(/"/g, '&quot;')}"` : '';
      if (isExternal) {
        return `<a href="${href}" target="_blank" rel="noopener noreferrer"${titleAttr}>${text}</a>`;
      }
      return `<a href="${href}"${titleAttr}>${text}</a>`;
    },
  },
});

// ── MermaidBlock component ────────────────────────────────────────────────────

// Inject CSS overrides into the SVG so it always looks right regardless of
// which Mermaid version is loaded from CDN.
function patchSvg(raw: string, accentColor: string): string {
  // Make SVG fluid-width so it fills the container on all screen sizes
  let s = raw
    .replace(/<svg([^>]*)width="[^"]*"/, '<svg$1width="100%"')
    .replace(/<svg([^>]*)height="([^"]*)"/, '<svg$1height="auto" data-orig-height="$2"')
    .replace(/<svg(?![^>]*style=)([^>]*)>/, '<svg$1 style="max-width:100%;display:block;">');

  // Append our CSS overrides before </style> (or inject a new <style> block)
  const overrideCss = `
    /* Silicon to Software (S2S) — Mermaid visual overrides */
    svg { background: #0a0f1a !important; }
    .node rect, .node circle, .node ellipse, .node polygon, .node path {
      fill: #142030 !important;
      stroke: ${accentColor} !important; stroke-width: 1px !important;
      rx: 4; ry: 4;
    }
    .edgePath .path { stroke: ${accentColor}66 !important; stroke-width: 1px !important; }
    .arrowheadPath { fill: ${accentColor} !important; stroke: none !important; }
    .edgeLabel .label rect { fill: #0a0f1a !important; }
    .edgeLabel .label span, .edgeLabel span { color: #ffffff !important; font-size: 11px !important; }
    .cluster rect { fill: #0c1220 !important; stroke: ${accentColor}44 !important; stroke-width: 1px !important; rx: 6; ry: 6; }
    .cluster text, .cluster tspan, .cluster span { fill: #b4c4d4 !important; font-size: 11px !important; font-weight: 500 !important; letter-spacing: 0.04em !important; }
    text, tspan { fill: #ffffff !important; font-family: 'DM Mono', monospace !important; font-size: 11px !important; }
    .nodeLabel, .label, .label span, .labelText { color: #ffffff !important; fill: #ffffff !important; font-size: 11px !important; }
    .nodeLabel p { margin: 0 !important; color: #ffffff !important; }
    foreignObject div, foreignObject span, foreignObject p { color: #ffffff !important; font-size: 11px !important; font-family: 'DM Mono', monospace !important; }
    .messageText, .actor text, .note text, .labelBox text { fill: #ffffff !important; }
    .actor rect, .actor line { fill: #142030 !important; stroke: ${accentColor} !important; stroke-width: 1px !important; }
    .messageLine0, .messageLine1 { stroke: ${accentColor}66 !important; stroke-width: 1px !important; }
    .activation0, .activation1, .activation2 { fill: #1a2840 !important; stroke: ${accentColor} !important; stroke-width: 1px !important; }
    .loopLine { stroke: #2a3a50 !important; stroke-width: 1px !important; }
    .loopText, .loopText tspan { fill: #b4c4d4 !important; }
    .noteText, .noteText tspan { fill: #ffffff !important; }
    .note { fill: #142030 !important; stroke: ${accentColor}44 !important; stroke-width: 1px !important; }
  `;

  if (s.includes('</style>')) {
    s = s.replace('</style>', overrideCss + '</style>');
  } else {
    s = s.replace('<svg', `<svg><style>${overrideCss}</style><svg`).replace('<svg><style>', '<svg><style>');
    // simpler: just prepend a style block inside the <svg>
    s = s.replace(/(<svg[^>]*>)/, `$1<style>${overrideCss}</style>`);
  }
  return s;
}

function MermaidBlock({ code, color }: { code: string; color: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const id = useRef(`mmd-${Math.random().toString(36).slice(2)}`);

  useEffect(() => {
    let cancelled = false;
    const rid = id.current;

    // 12-second timeout — if mermaid hangs (invalid syntax), show fallback
    const timeout = setTimeout(() => {
      if (!cancelled) {
        cancelled = true;
        purgeMermaidScratch(rid);
        setErr('Diagram render timed out — showing source');
      }
    }, 12000);

    (async () => {
      try {
        await loadMermaid(); // instant — mermaid is bundled, not CDN
        const rawSvg = await renderMermaid(rid, code);
        clearTimeout(timeout);
        purgeMermaidScratch(rid);
        if (!cancelled) {
          if (rawSvg?.includes('<svg') && !rawSvg.includes('class="error"')) {
            setSvg(patchSvg(rawSvg, color));
          } else {
            setErr('Diagram produced no SVG output');
          }
        }
      } catch (e: unknown) {
        clearTimeout(timeout);
        purgeMermaidScratch(rid);
        if (!cancelled) {
          const msg = e instanceof Error ? e.message : String(e);
          setErr(`Syntax error: ${msg.slice(0, 120)}`);
        }
      }
    })();

    return () => {
      cancelled = true;
      clearTimeout(timeout);
      purgeMermaidScratch(rid);
    };
  }, [code]);

  if (err) {
    // Graceful fallback: show the raw Mermaid source with the error reason
    return (
      <div style={{ margin: '4px 0' }}>
        <div style={{ fontSize: 10, color: '#f59e0b', fontFamily: "'DM Mono', monospace", letterSpacing: '0.08em', marginBottom: 5 }}>
          ⚠ DIAGRAM SOURCE — {err}
        </div>
        <pre style={{
          background: 'var(--panel2)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: 6,
          padding: '12px 14px', margin: 0, fontSize: 11, color: 'var(--text3)',
          fontFamily: "'JetBrains Mono', monospace",
          overflowX: 'auto', lineHeight: 1.65, whiteSpace: 'pre-wrap',
        }}>
          {code}
        </pre>
      </div>
    );
  }
  if (!svg) {
    return (
      <div style={{ padding: '14px', display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text4)', fontSize: 12 }}>
        <div style={{ width: 12, height: 12, borderRadius: '50%', border: `2px solid ${color}`, borderTopColor: 'transparent', animation: 'spin 0.8s linear infinite' }} />
        Rendering diagram...
      </div>
    );
  }
  return (
    <div ref={ref}
      style={{
        padding: '20px 16px',
        overflowX: 'auto',
        background: '#0a1628',
        borderRadius: 8,
        border: `1px solid ${color}30`,
        boxShadow: `0 0 32px rgba(0,0,0,0.4), inset 0 0 60px rgba(0,0,0,0.2)`,
      }}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

// ── MarkdownRenderer ──────────────────────────────────────────────────────────

function MarkdownRenderer({ content, color }: { content: string; color: string }) {
  const parts: Array<{ type: 'md' | 'mermaid'; text: string }> = [];
  // Normalise Windows line endings so the regex works regardless of how the
  // file was written (Python on Windows produces \r\n in write_text())
  const normalised = content.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

  // Match ```mermaid or any code block whose first non-blank line starts with
  // a known Mermaid diagram type (handles agents that omit the language tag).
  const MERMAID_TYPES = /^(flowchart|graph|sequencediagram|classdiagram|statediagram|erdiagram|gantt|pie\s|gitgraph|mindmap|timeline)/i;
  // Regex captures: group 1 = optional lang tag (e.g. "mermaid"), group 2 = code body
  const mermaidRe = /```([a-z]*)[ \t]*\n([\s\S]*?)```/gi;
  let lastIdx = 0;
  let m: RegExpExecArray | null;

  while ((m = mermaidRe.exec(normalised)) !== null) {
    const lang = m[1].toLowerCase();
    const body = m[2];
    const firstLine = body.trimStart().split('\n')[0].trim();
    // Accept if language tag is "mermaid" OR the code body starts with a known Mermaid type
    const isMermaid = lang === 'mermaid' || (lang === '' && MERMAID_TYPES.test(firstLine));
    if (!isMermaid) {
      // Not a mermaid block — keep as markdown (include the full fenced block)
      if (m.index > lastIdx) parts.push({ type: 'md', text: normalised.slice(lastIdx, m.index) });
      parts.push({ type: 'md', text: m[0] });
      lastIdx = m.index + m[0].length;
      continue;
    }
    if (m.index > lastIdx) parts.push({ type: 'md', text: normalised.slice(lastIdx, m.index) });
    // Run full sanitization: strip HTML, fix \n escapes, escape > < in labels
    const cleanCode = sanitizeMermaidCode(body);
    parts.push({ type: 'mermaid', text: cleanCode });
    lastIdx = m.index + m[0].length;
  }
  if (lastIdx < normalised.length) parts.push({ type: 'md', text: normalised.slice(lastIdx) });

  return (
    <div style={{ padding: '22px 26px', lineHeight: 1.75 }}>
      {parts.map((part, i) => {
        if (part.type === 'mermaid') {
          return (
            <div key={i} style={{ margin: '18px 0' }}>
              <div style={{ fontSize: 10, color: 'var(--text4)', fontFamily: "'DM Mono', monospace", marginBottom: 8, letterSpacing: '0.1em' }}>DIAGRAM</div>
              <MermaidErrorBoundary source={part.text} color={color} label="DIAGRAM">
                <MermaidBlock code={part.text} color={color} />
              </MermaidErrorBoundary>
            </div>
          );
        }
        const html = marked.parse(part.text) as string;
        return <div key={i} className="md-body" dangerouslySetInnerHTML={{ __html: html }} />;
      })}
    </div>
  );
}

// ── FileIcon ──────────────────────────────────────────────────────────────────

function FileIcon({ ext, color }: { ext: string; color: string }) {
  const icons: Record<string, string> = {
    md: '📝', docx: '📄', pdf: '📋', json: '{ }', net: '⬡', txt: '📃',
    html: '</>', csv: '⊞', xdc: '◈',
    xlsx: '⊞', xlsm: '⊞', xls: '⊞',
  };
  const icon = icons[ext] || '📄';
  return (
    <div style={{
      width: 40, height: 40, borderRadius: 8, flexShrink: 0,
      background: `${color}12`, border: `1px solid ${color}28`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: ext === 'json' ? 11 : ext === 'html' || ext === 'xdc' ? 12 : 18,
      color: ['json', 'html', 'xdc', 'net'].includes(ext) ? color : undefined,
      fontFamily: ['json', 'html', 'xdc', 'net'].includes(ext) ? "'JetBrains Mono', monospace" : undefined,
      fontWeight: 700,
    }}>
      {icon}
    </div>
  );
}

// ── PhaseDetails component — shows inputs/outputs/tools/metrics ───────────────

function PhaseDetails({ phase, color, collapsed = false }: { phase: PhaseMeta; color: string; collapsed?: boolean }) {
  const [open, setOpen] = useState(!collapsed);

  const Section = ({ title, items }: { title: string; items: string[] }) => (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 10, color, letterSpacing: '0.1em', fontFamily: "'DM Mono',monospace", marginBottom: 6 }}>{title}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {items.map((item, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
            <span style={{ color: `${color}80`, fontSize: 9, marginTop: 4, flexShrink: 0 }}>▸</span>
            <span style={{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.5 }}>{item}</span>
          </div>
        ))}
      </div>
    </div>
  );

  const m = phase.metrics;

  return (
    <div style={{ border: `1px solid ${color}18`, borderRadius: 8, overflow: 'hidden', marginBottom: 16 }}>
      {/* Header row — always visible */}
      <div
        onClick={() => setOpen(v => !v)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '10px 14px', cursor: 'pointer', background: `${color}06`,
          borderBottom: open ? `1px solid ${color}18` : 'none',
        }}
      >
        <div style={{ fontSize: 11, color, letterSpacing: '0.08em', fontFamily: "'DM Mono',monospace" }}>
          ◈ PHASE DETAILS — {phase.code} {phase.name.toUpperCase()}
        </div>
        <span style={{ fontSize: 11, color: 'var(--text4)' }}>{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <div style={{ padding: '16px 18px' }}>
          {/* Metrics row */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 16 }}>
            {[
              { label: 'TIME SAVED', value: m.timeSaved },
              { label: 'ERROR REDUCTION', value: m.errorReduction },
              { label: 'CONFIDENCE', value: m.confidence },
              { label: 'COST IMPACT', value: m.costImpact },
            ].map(({ label, value }) => (
              <div key={label} style={{ background: `${color}08`, border: `1px solid ${color}18`, borderRadius: 6, padding: '8px 10px' }}>
                <div style={{ fontSize: 9, color: 'var(--text4)', letterSpacing: '0.1em', marginBottom: 4, fontFamily: "'DM Mono',monospace" }}>{label}</div>
                <div style={{ fontSize: 12, color, fontWeight: 700 }}>{value}</div>
              </div>
            ))}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
            <Section title="INPUTS" items={phase.inputs} />
            <Section title="OUTPUTS" items={phase.outputs} />
            <Section title="TOOLS" items={phase.tools} />
          </div>
        </div>
      )}
    </div>
  );
}

// ── GeneratingState — shown when phase is in_progress but no files yet ─────────
// elapsed / startTs are lifted from the parent so switching phases doesn't reset the timer.

function GeneratingState({ phase, elapsed }: { phase: PhaseMeta; elapsed: number }) {
  const mins = Math.floor(elapsed / 60);
  const secs = elapsed % 60;
  const elapsedStr = mins > 0
    ? `${mins}m ${secs.toString().padStart(2, '0')}s`
    : `${secs}s`;

  // Compute cumulative time thresholds from subSteps
  const steps = phase.subSteps || [];
  const stepTimesSeconds: number[] = [];
  let cumulative = 0;
  for (const step of steps) {
    // Parse time string: "12s" -> 12, "2 min" -> 120, "120s" -> 120, "48s" -> 48
    const t = step.time || '10s';
    let sec = 10;
    const minMatch = t.match(/([\d.]+)\s*min/i);
    const secMatch = t.match(/([\d.]+)\s*s/i);
    if (minMatch) sec = parseFloat(minMatch[1]) * 60;
    else if (secMatch) sec = parseFloat(secMatch[1]);
    cumulative += sec;
    stepTimesSeconds.push(cumulative);
  }

  // Figure out which step is active based on elapsed time
  let activeIdx = 0;
  for (let i = 0; i < stepTimesSeconds.length; i++) {
    if (elapsed >= stepTimesSeconds[i]) activeIdx = i + 1;
  }
  activeIdx = Math.min(activeIdx, steps.length - 1);

  // Overall progress percentage
  const totalEstSec = stepTimesSeconds.length > 0 ? stepTimesSeconds[stepTimesSeconds.length - 1] : 240;
  const overallPct = Math.min(95, Math.round((elapsed / totalEstSec) * 100));

  return (
    <>
      {/* Header with spinner */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, marginBottom: 8 }}>
        <div style={{ width: 14, height: 14, borderRadius: '50%', border: `2.5px solid ${phase.color}`, borderTopColor: 'transparent', animation: 'spin 0.8s linear infinite' }} />
        <div style={{ fontSize: 14, color: phase.color, fontWeight: 600 }}>
          {steps.length > 0 ? steps[activeIdx].label : 'AI agent running...'}
        </div>
      </div>

      {/* Elapsed / Estimated / Progress bar */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 20, marginBottom: 6 }}>
        <div style={{ fontSize: 12, color: 'var(--text4)', fontFamily: "'DM Mono', monospace" }}>
          Elapsed: <span style={{ color: phase.color }}>{elapsedStr}</span>
        </div>
        <div style={{ fontSize: 12, color: 'var(--text4)', fontFamily: "'DM Mono', monospace" }}>
          Estimated: <span style={{ color: phase.color }}>{phase.time}</span>
        </div>
      </div>

      {/* Overall progress bar */}
      <div style={{ maxWidth: 400, margin: '0 auto 16px', padding: '0 4px' }}>
        <div style={{ height: 4, borderRadius: 2, background: `${phase.color}18` }}>
          <div style={{
            height: '100%', borderRadius: 2, background: phase.color,
            width: `${overallPct}%`, transition: 'width 1s ease',
            boxShadow: `0 0 8px ${phase.color}40`,
          }} />
        </div>
        <div style={{ fontSize: 10, color: 'var(--text4)', textAlign: 'right', marginTop: 2, fontFamily: "'DM Mono', monospace" }}>
          {overallPct}%
        </div>
      </div>

      {/* Sub-step list */}
      {steps.length > 0 && (
        <div style={{ maxWidth: 440, margin: '0 auto' }}>
          {steps.map((step, i) => {
            const isDone = i < activeIdx;
            const isActive = i === activeIdx;
            const isPending = i > activeIdx;
            return (
              <div key={i} style={{
                display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 6,
                opacity: isPending ? 0.35 : 1, transition: 'opacity 0.5s',
              }}>
                {/* Step indicator */}
                <div style={{
                  width: 18, height: 18, borderRadius: '50%', flexShrink: 0, marginTop: 1,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 10, fontWeight: 700, fontFamily: "'DM Mono', monospace",
                  background: isDone ? phase.color : isActive ? `${phase.color}30` : 'transparent',
                  border: `1.5px solid ${isDone || isActive ? phase.color : 'var(--border)'}`,
                  color: isDone ? '#070b14' : isActive ? phase.color : 'var(--text4)',
                  boxShadow: isActive ? `0 0 8px ${phase.color}40` : 'none',
                }}>
                  {isDone ? '\u2713' : (i + 1)}
                </div>
                {/* Step text */}
                <div>
                  <div style={{
                    fontSize: 12, fontWeight: isActive ? 600 : 400,
                    color: isDone ? 'var(--text2)' : isActive ? phase.color : 'var(--text4)',
                    fontFamily: "'DM Mono', monospace",
                  }}>
                    {step.label}
                  </div>
                  {(isDone || isActive) && step.detail && (
                    <div style={{ fontSize: 10, color: 'var(--text4)', lineHeight: 1.5, marginTop: 1 }}>
                      {step.detail}
                    </div>
                  )}
                </div>
                {/* Time chip */}
                <div style={{
                  fontSize: 10, color: isDone ? phase.color : 'var(--text4)',
                  fontFamily: "'DM Mono', monospace", marginLeft: 'auto', flexShrink: 0, marginTop: 2,
                }}>
                  {step.time}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div style={{ fontSize: 11, color: 'var(--text4)', maxWidth: 380, margin: '12px auto 0', lineHeight: 1.7, textAlign: 'center' }}>
        Output files will appear here automatically once complete.
      </div>
    </>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function DocumentsView({ project, phase, status, pipelineRunning }: Props) {
  const [files, setFiles] = useState<DocFile[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  // Track which phase IDs have been loaded at least once — prevents spinner on phase re-visit
  const loadedPhaseIds = useRef<Set<string>>(new Set());
  // Track if we've ever successfully fetched any files — once true, phase switches are always silent
  const hasAnyFiles = useRef(false);
  // Track previous status to detect transitions (in_progress → completed/failed)
  const prevStatusRef = useRef<string>(status);
  const [contents, setContents] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loadingFile, setLoadingFile] = useState<Record<string, boolean>>({});
  // docxConverting: shown in UI (only when user actually clicks ↓ DOCX and pre-convert isn't ready)
  const [docxConverting, setDocxConverting] = useState<Record<string, boolean>>({});
  // docxBlobUrls: pre-converted blob URLs — download is instant when ready
  const [docxBlobUrls, setDocxBlobUrls] = useState<Record<string, string>>({});
  // docxPreconverting STATE — triggers re-render so button shows "Preparing…" during bg conversion
  const [docxPreconverting, setDocxPreconverting] = useState<Record<string, boolean>>({});
  // docxPreconvertingRef: same info but safe to read inside async callbacks without stale closure
  const docxPreconvertingRef = useRef<Set<string>>(new Set());
  // docxError: per-file error message shown under DOCX button (auto-clears)
  const [docxError, setDocxError] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  // ── Generation tracker: prevents stale async operations from setting state after phase switch ──
  // Each phase gets a unique generation number. When an async operation completes,
  // it checks if the generation still matches. If not, it skips the state update.
  const generationRef = useRef<number>(0);

  // ── User-initiated abort controllers ─────────────────────────────────────────
  // Tracks AbortControllers for in-flight Preview fetches and DOCX downloads.
  // All are aborted on phase switch so the UI never stays stuck in Loading.../Converting...
  const activeUserAborts = useRef(new Set<AbortController>());

  // ── Persistent elapsed timer — keyed by phase.id so switching phases doesn't reset it ──
  // phaseStartTs: maps phase.id → wall-clock ms when that phase first went in_progress
  const phaseStartTsRef = useRef<Record<string, number>>({});
  const [elapsedByPhase, setElapsedByPhase] = useState<Record<string, number>>({});

  // P4-only sub-tab: "files" (default document list) vs "schematic" (interactive gate-level schematic)
  const [p4SubTab, setP4SubTab] = useState<'files' | 'schematic'>('files');

  // Record start time when a phase first enters in_progress
  useEffect(() => {
    if (status === 'in_progress' && !phaseStartTsRef.current[phase.id]) {
      phaseStartTsRef.current[phase.id] = Date.now();
    }
    // Clear stored start time when phase completes/fails so next run starts fresh
    if (status === 'completed' || status === 'failed') {
      delete phaseStartTsRef.current[phase.id];
    }
  }, [phase.id, status]);

  // Clear all loading/preparing states when switching phases
  // IMPORTANT: Clear states IMMEDIATELY when phase.id changes, not just in cleanup.
  // This prevents stuck "Loading..." and "Converting..." states in all phases.
  useEffect(() => {
    // Increment generation to invalidate all in-flight async operations from previous phase
    generationRef.current += 1;

    // Abort ALL in-flight user-initiated fetches (Preview + DOCX download).
    // This immediately unblocks any hanging requests from the previous phase so
    // their loading/converting states are never left visible on the new phase.
    activeUserAborts.current.forEach(c => c.abort());
    activeUserAborts.current.clear();

    // Clear all states immediately on phase change
    setLoadingFile({});
    setDocxConverting({});
    setDocxPreconverting({});
    docxPreconvertingRef.current.clear();
    setDocxError({});
    // Also clear cached content and blob URLs to prevent cross-phase contamination
    setContents({});
    setExpanded(null);
    setDocxBlobUrls({});

    return () => {
      // Double-clear in cleanup to catch any async operations
      activeUserAborts.current.forEach(c => c.abort());
      activeUserAborts.current.clear();
      setLoadingFile({});
      setDocxConverting({});
      setDocxPreconverting({});
      docxPreconvertingRef.current.clear();
      setDocxError({});
    };
  }, [phase.id]);

  // Tick every second when ANY phase is in_progress
  useEffect(() => {
    const anyRunning = Object.keys(phaseStartTsRef.current).length > 0;
    if (!anyRunning && status !== 'in_progress') return;
    const t = setInterval(() => {
      const now = Date.now();
      const updates: Record<string, number> = {};
      for (const [pid, startMs] of Object.entries(phaseStartTsRef.current)) {
        updates[pid] = Math.floor((now - startMs) / 1000);
      }
      setElapsedByPhase(prev => ({ ...prev, ...updates }));
    }, 1000);
    return () => clearInterval(t);
  }, [status]);

  // Per-phase whitelist. Includes both exact-name entries and directory
  // prefixes (e.g. `drivers/`, `qt_gui/` for P8c). The actual matching
  // happens via `isVisibleDocument` below — the Set is kept for backward
  // compat with one-shot existence checks elsewhere in this file.
  const visibleFilenames = project
    ? new Set(getVisibleDocuments(phase.id, project.name))
    : new Set<string>();

  // Pipeline-internal JSON files are kept on disk for agent use but hidden from the UI.
  // Human-readable equivalents (netlist_visual.md, sbom_summary.md) cover them.
  // cascade_analysis.json is hidden because the CascadeChart component renders
  // it visually in-page — exposing the raw JSON in the file list was noise.
  const HIDDEN_FILES = new Set([
    'netlist.json', 'netlist_validation.json', 'sbom.json',
    'cascade_analysis.json',
  ]);
  const filteredFiles = useMemo(() => {
    if (!project) return [];
    const unique = new Set<string>();
    // P26 (2026-05-04): switched from `visibleFilenames.has(f.name)` exact
    // match to `isVisibleDocument()` so directory-prefix entries (e.g.
    // `drivers/`, `qt_gui/`, `rtl/` for P7/P8c) match every descendant
    // file. Pre-fix, the UI hid generated drivers and Qt panels that
    // were nonetheless being shipped in the per-phase Export ZIP.
    const allVisible = files.filter(f => isVisibleDocument(phase.id, f.name, project.name));
    // Build a set of stems that have a .md counterpart (backend caches DOCX next to .md)
    const mdStems = new Set(
      allVisible.filter(f => getExt(f.name) === 'md').map(f => f.name.replace(/\.md$/i, ''))
    );
    return allVisible.filter(f => {
      // Skip duplicates and hidden files
      if (unique.has(f.name) || HIDDEN_FILES.has(f.name)) return false;
      unique.add(f.name);
      // Hide the backend-cached .docx file when a same-stem .md already exists —
      // the .md row provides the "↓ DOCX" button for on-demand conversion.
      if (getExt(f.name) === 'docx') {
        const stem = f.name.replace(/\.docx$/i, '');
        if (mdStems.has(stem)) return false;
      }
      return true;
    });
  }, [files, phase.id, project]);

  const fetchList = useCallback((silent = false, currentPhaseId?: string) => {
    if (!project) return;
    if (!silent) { setLoadingList(true); setError(null); }

    // Timeout: if loading takes >8s, force-complete to avoid permanent spinner
    let timedOut = false;
    const timeout = !silent ? setTimeout(() => {
      timedOut = true;
      setLoadingList(false);
    }, 8000) : undefined;

    api.listDocuments(project.id)
      .then(list => {
        if (timeout) clearTimeout(timeout);
        if (timedOut) return;
        setFiles(list);
        setLoadingList(false);
        if (list.length > 0) hasAnyFiles.current = true;
        if (currentPhaseId) loadedPhaseIds.current.add(currentPhaseId);
      })
      .catch((err: Error) => {
        if (timeout) clearTimeout(timeout);
        if (timedOut) return;
        const msg = err?.message || 'Unknown error';
        if (!silent) {
          if (msg.includes('HTTP 404')) setError('Documents endpoint not found (HTTP 404). Restart the backend.');
          else if (msg.includes('HTTP 500')) setError('Server error (HTTP 500): ' + msg);
          else if (msg.includes('HTTP 405')) setError('Method not allowed (HTTP 405). Restart the backend.');
          else setError('API error: ' + msg);
        }
        setLoadingList(false);
      });
  }, [project]);

  // Load documents when project or phase changes.
  // Show spinner only on the very first project load when no files exist yet.
  // Once any files have been fetched (hasAnyFiles), all phase switches are silent
  // so cached data shows instantly while the list refreshes in the background.
  useEffect(() => {
    const alreadyLoaded = loadedPhaseIds.current.has(phase.id);
    // Silent if: visited this phase before, OR any files ever loaded (project-wide cache exists)
    // BUG FIX: was fetchList(!silent) — the ! inverted the logic so already-loaded phases
    // showed the spinner and first-load phases skipped it. Correct: pass silent directly.
    const silent = alreadyLoaded || hasAnyFiles.current;
    fetchList(silent, phase.id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, phase.id]);

  // Periodic refresh while phase is running
  useEffect(() => {
    const shouldRefresh = pipelineRunning || status === 'in_progress';
    if (!project || !shouldRefresh) return;
    const interval = setInterval(() => fetchList(true), 3000);
    return () => clearInterval(interval);
  }, [project, pipelineRunning, status, fetchList]);

  // Critical: when a phase transitions from in_progress → completed/failed,
  // do immediate re-fetches to catch files written in the final moments.
  // Without this, the file list may be stale if the last periodic poll happened
  // just before the backend flushed output files, leaving the spinner permanently.
  useEffect(() => {
    const prev = prevStatusRef.current;
    prevStatusRef.current = status;
    if (prev === 'in_progress' && (status === 'completed' || status === 'failed')) {
      // Immediate fetch + two follow-ups to handle slow file writes
      fetchList(true);
      const t1 = setTimeout(() => fetchList(true), 1500);
      const t2 = setTimeout(() => fetchList(true), 4000);
      return () => { clearTimeout(t1); clearTimeout(t2); };
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  // Safety net: if status is 'completed' but no files are visible yet (e.g. the
  // initial silent fetch ran before files were written, or loadingList timed out),
  // trigger one non-silent retry so the spinner shows and files load correctly.
  const retriedRef = useRef<string>('');
  useEffect(() => {
    const key = `${project?.id}-${phase.id}`;
    if (
      !loadingList &&
      filteredFiles.length === 0 &&
      status === 'completed' &&
      !error &&
      retriedRef.current !== key
    ) {
      retriedRef.current = key;
      fetchList(false, phase.id);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadingList, filteredFiles.length, status, phase.id, project?.id]);

  // Keep a ref always pointing at the latest contents map — avoids stale-closure in prefetch.
  const contentsRef = useRef<Record<string, string>>({});
  useEffect(() => { contentsRef.current = contents; }, [contents]);

  // Stable key that changes whenever the ACTUAL files change (phase switch, new files added).
  // Using only .length caused bugs when two phases had the same file count — the effect
  // would not re-run and would keep running with stale closures from the previous phase.
  const filteredFilesKey = filteredFiles.map(f => f.name).join('|');

  // Background prefetch all viewable documents after file list loads.
  // Uses parallel batches of 3 for speed — makes "Preview" feel instant.
  // Skip while pipeline is running — backend is busy with AI inference.
  useEffect(() => {
    if (!project || filteredFiles.length === 0) return;
    if (pipelineRunning) return;  // defer — backend busy with AI phase

    // Capture the generation at effect start — async ops check this before setting state
    const startGeneration = generationRef.current;

    let cancelled = false;
    const prefetch = async () => {
      const viewable = filteredFiles.filter(f => VIEWABLE.has(getExt(f.name))
        && contentsRef.current[f.name] === undefined);
      // Fetch in parallel batches of 3
      const BATCH = 3;
      for (let i = 0; i < viewable.length; i += BATCH) {
        if (cancelled) return;
        // Skip if generation changed (phase switched)
        if (generationRef.current !== startGeneration) return;
        const batch = viewable.slice(i, i + BATCH);
        await Promise.all(batch.map(async file => {
          if (cancelled || contentsRef.current[file.name] !== undefined) return;
          // Skip if generation changed (phase switched)
          if (generationRef.current !== startGeneration) return;
          try {
            const text = await api.getDocumentText(project.id, file.name);
            // Only set state if generation hasn't changed
            if (!cancelled && generationRef.current === startGeneration) {
              contentsRef.current = { ...contentsRef.current, [file.name]: text };
              setContents(prev => ({ ...prev, [file.name]: text }));
            }
          } catch { /* silent — user can still click to retry */ }
        }));
        // Small stagger between batches only
        if (i + BATCH < viewable.length && generationRef.current === startGeneration) {
          await new Promise(r => setTimeout(r, 30));
        }
      }
    };
    prefetch();
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id, filteredFilesKey, pipelineRunning]);

  // Background pre-convert all .md files to DOCX so downloads are instant.
  // Shows "Preparing…" label on DOCX button while background conversion is in progress.
  // With backend disk caching, second run is instant (served from cached .docx on disk).
  // IMPORTANT: Skip preconversion while the pipeline is running — the backend is busy
  // with AI inference and DOCX conversion would queue up and show "Preparing…" indefinitely.
  // The effect re-runs once pipelineRunning flips to false, kicking off conversions then.
  useEffect(() => {
    if (!project || filteredFiles.length === 0) return;
    if (pipelineRunning) return;  // defer — backend busy with AI phase

    // Capture the generation at effect start — async ops check this before setting state
    const startGeneration = generationRef.current;

    let cancelled = false;
    const abortControllers: AbortController[] = [];
    // Track which file names THIS run started preparing, so we can clean them up on cancel
    const thisRunFiles: string[] = [];

    const preconvert = async () => {
      const mdFiles = filteredFiles.filter(f => getExt(f.name) === 'md');
      // Convert one at a time — DOCX conversion is CPU-heavy on backend
      for (const file of mdFiles) {
        if (cancelled) return;
        // Skip if generation changed (phase switched)
        if (generationRef.current !== startGeneration) return;
        if (docxPreconvertingRef.current.has(file.name)) continue;

        // Mark as in-flight BEFORE the async fetch
        docxPreconvertingRef.current.add(file.name);
        thisRunFiles.push(file.name);

        // Only set state if generation hasn't changed
        if (generationRef.current === startGeneration) {
          setDocxPreconverting(prev => ({ ...prev, [file.name]: true }));
        }

        // AbortController with 90s timeout — prevents infinite "Preparing…" when
        // backend is busy running another AI phase
        const controller = new AbortController();
        abortControllers.push(controller);
        const timeoutId = setTimeout(() => controller.abort(), 90_000);

        try {
          const resp = await fetch(
            `/api/v1/projects/${project.id}/docx/${encodeURIComponent(file.name)}`,
            { signal: controller.signal }
          );
          clearTimeout(timeoutId);
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          const blob = await resp.blob();
          const url = URL.createObjectURL(blob);

          // Only set state if generation hasn't changed (phase didn't switch)
          if (generationRef.current === startGeneration && !cancelled) {
            setDocxBlobUrls(prev => ({ ...prev, [file.name]: url }));
          }
        } catch {
          clearTimeout(timeoutId);
          /* silent — user will see on-demand spinner if needed */
        }

        // Always clean up after each file (success, error, or abort)
        docxPreconvertingRef.current.delete(file.name);

        // Only set state if generation hasn't changed
        if (generationRef.current === startGeneration) {
          setDocxPreconverting(prev => { const n = { ...prev }; delete n[file.name]; return n; });
        }

        // Small gap between files so other UI interactions stay responsive
        if (!cancelled && generationRef.current === startGeneration) {
          await new Promise(r => setTimeout(r, 200));
        }
      }
    };

    preconvert();

    return () => {
      cancelled = true;
      abortControllers.forEach(c => c.abort());
      // Clear "Preparing…" state for every file this run started but didn't finish.
      // Without this, switching phases leaves the previous phase's files stuck at "Preparing…".
      thisRunFiles.forEach(name => docxPreconvertingRef.current.delete(name));
      if (thisRunFiles.length > 0 && generationRef.current === startGeneration) {
        setDocxPreconverting(prev => {
          const n = { ...prev };
          thisRunFiles.forEach(name => delete n[name]);
          return n;
        });
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id, filteredFilesKey, pipelineRunning]);

  const fetchContent = async (file: DocFile) => {
    if (!project) return;
    const ext = getExt(file.name);

    if (!VIEWABLE.has(ext)) {
      triggerDownload(file);
      return;
    }
    if (contents[file.name] !== undefined) {
      setExpanded(expanded === file.name ? null : file.name);
      return;
    }

    // Capture the generation at fetch start — check before setting state
    const startGeneration = generationRef.current;

    // AbortController: aborted on phase switch (via activeUserAborts) OR after 20s timeout.
    // Without this, if the backend is busy the fetch hangs forever and "Loading..." never clears.
    const controller = new AbortController();
    activeUserAborts.current.add(controller);
    const timeoutId = setTimeout(() => controller.abort(), 20_000);

    setLoadingFile(prev => ({ ...prev, [file.name]: true }));
    try {
      const text = await api.getDocumentText(project.id, file.name, controller.signal);
      clearTimeout(timeoutId);
      // Only set state if generation hasn't changed (phase didn't switch during fetch)
      if (generationRef.current === startGeneration) {
        contentsRef.current = { ...contentsRef.current, [file.name]: text };
        setContents(prev => ({ ...prev, [file.name]: text }));
        setExpanded(file.name);
      }
    } catch (err) {
      clearTimeout(timeoutId);
      const isAbort = err instanceof Error && (err.name === 'AbortError' || err.message === 'AbortError');
      // Only set state if generation hasn't changed
      if (generationRef.current === startGeneration && !isAbort) {
        setContents(prev => ({ ...prev, [file.name]: 'Could not load document.' }));
        setExpanded(file.name);
      }
    } finally {
      clearTimeout(timeoutId);
      activeUserAborts.current.delete(controller);
      // Always clear loading state (finally runs even if fetch was aborted or timed out)
      if (generationRef.current === startGeneration) {
        setLoadingFile(prev => ({ ...prev, [file.name]: false }));
      }
    }
  };

  /** Reliably trigger a browser download — appends anchor to body to satisfy Firefox/Safari */
  const clickDownload = (href: string, filename: string) => {
    const a = document.createElement('a');
    a.href = href;
    a.download = filename;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { document.body.removeChild(a); }, 200);
  };

  const triggerDownload = (file: DocFile) => {
    if (!project) return;
    clickDownload(
      `/api/v1/projects/${project.id}/documents/${encodeURIComponent(file.name)}`,
      file.name,
    );
  };

  const triggerDocxDownload = async (file: DocFile) => {
    if (!project || docxConverting[file.name]) return;

    // Capture the generation at download start — check before setting state
    const startGeneration = generationRef.current;

    const docxName = file.name.replace(/\.md$/i, '.docx');

    // If pre-conversion already finished, use cached blob URL — instant download
    if (docxBlobUrls[file.name]) {
      clickDownload(docxBlobUrls[file.name], docxName);
      // Revoke and clear so it can be re-fetched if needed (e.g. file changed)
      setTimeout(() => {
        // Only clear if generation hasn't changed
        if (generationRef.current === startGeneration) {
          URL.revokeObjectURL(docxBlobUrls[file.name]);
          setDocxBlobUrls(prev => { const n = { ...prev }; delete n[file.name]; return n; });
        }
      }, 5000);
      return;
    }

    // AbortController: aborted on phase switch OR after 90s timeout.
    // Prevents "Converting…" from being permanently stuck when backend is slow.
    const controller = new AbortController();
    activeUserAborts.current.add(controller);
    const timeoutId = setTimeout(() => controller.abort(), 90_000);

    // Pre-convert not ready yet — show "Converting…" spinner while we wait
    setDocxConverting(prev => ({ ...prev, [file.name]: true }));
    try {
      const resp = await fetch(
        `/api/v1/projects/${project.id}/docx/${encodeURIComponent(file.name)}`,
        { signal: controller.signal },
      );
      clearTimeout(timeoutId);
      if (!resp.ok) {
        let detail = '';
        try { const j = await resp.json(); detail = j.detail || ''; } catch { /* ignore */ }
        throw new Error(`HTTP ${resp.status}${detail ? ': ' + detail : ''}`);
      }
      const blob = await resp.blob();
      if (blob.size === 0) throw new Error('Empty response from server');
      const url = URL.createObjectURL(blob);
      clickDownload(url, docxName);
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (err) {
      clearTimeout(timeoutId);
      const isAbort = err instanceof Error && (err.name === 'AbortError' || err.message === 'AbortError');
      console.error('DOCX download failed:', err);
      // Only show error if generation hasn't changed and it wasn't a phase-switch abort
      if (generationRef.current === startGeneration && !isAbort) {
        setDocxError(prev => ({ ...prev, [file.name]: String(err) }));
        setTimeout(() => setDocxError(prev => { const n = { ...prev }; delete n[file.name]; return n; }), 6000);
      }
    } finally {
      clearTimeout(timeoutId);
      activeUserAborts.current.delete(controller);
      // Always clear converting state — finally runs even on abort/timeout/phase switch
      if (generationRef.current === startGeneration) {
        setDocxConverting(prev => ({ ...prev, [file.name]: false }));
      }
    }
  };

  // ── Render states ─────────────────────────────────────────────────────────

  if (loadingList) {
    return (
      <div style={{ paddingTop: 24, display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text3)', fontSize: 13 }}>
        <div style={{ width: 14, height: 14, borderRadius: '50%', border: `2px solid ${phase.color}`, borderTopColor: 'transparent', animation: 'spin 0.8s linear infinite' }} />
        Loading documents...
      </div>
    );
  }

  if (error) {
    return (
      <div style={{
        marginTop: 24, padding: '16px 18px',
        background: 'rgba(220,38,38,0.07)', border: '1px solid rgba(220,38,38,0.25)',
        borderRadius: 8, fontSize: 13, color: '#ef4444',
        display: 'flex', gap: 10, alignItems: 'flex-start',
      }}>
        <span style={{ fontSize: 16, flexShrink: 0 }}>⚠</span>
        <div>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Backend error</div>
          <div style={{ fontSize: 12, color: '#fca5a5' }}>{error}</div>
        </div>
      </div>
    );
  }

  if (filteredFiles.length === 0) {
    return (
      <div style={{ paddingTop: 24 }}>
        <div style={{
          padding: '28px', background: 'var(--panel)',
          border: `1px dashed ${phase.color}30`, borderRadius: 10,
          textAlign: 'center', marginBottom: 20,
        }}>
          {status === 'in_progress' ? (
            <GeneratingState phase={phase} elapsed={elapsedByPhase[phase.id] ?? 0} />
          ) : status === 'pending' ? (
            <>
              <div style={{ fontSize: 28, marginBottom: 10, opacity: 0.25 }}>📁</div>
              <div style={{ fontSize: 14, color: 'var(--text2)', marginBottom: 6, fontWeight: 600 }}>
                {phase.id === 'P1' ? 'Start with a design chat' : `${phase.code} will generate documents automatically`}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text4)', maxWidth: 360, margin: '0 auto', lineHeight: 1.65 }}>
                {phase.id === 'P1'
                  ? 'Use the ⚡ Chat tab to describe your hardware design. Once complete, the full pipeline runs automatically.'
                  : phase.manual
                  ? `This phase is completed manually in ${phase.externalTool || 'an external EDA tool'}.`
                  : 'The pipeline will run this phase automatically after previous phases complete.'
                }
              </div>
            </>
          ) : status === 'completed' ? (
            <>
              <div style={{ fontSize: 28, marginBottom: 10, opacity: 0.25 }}>📭</div>
              <div style={{ fontSize: 14, color: 'var(--text2)', marginBottom: 6 }}>Phase completed — no documents found</div>
              <div style={{ fontSize: 12, color: 'var(--text4)' }}>
                Output files will appear here automatically once the phase completes.
              </div>
            </>
          ) : (
            <div style={{ fontSize: 13, color: 'var(--text3)' }}>No documents for {phase.code} yet.</div>
          )}
        </div>

        {/* Phase Details — always show inputs/outputs/tools/metrics so user knows what's coming */}
        <PhaseDetails phase={phase} color={phase.color} />
      </div>
    );
  }

  // P4-only: render the Schematic view when the sub-tab is active
  if (phase.id === 'P4' && p4SubTab === 'schematic' && project) {
    return (
      <div style={{ paddingTop: 18, display: 'flex', flexDirection: 'column', minHeight: 700 }}>
        <P4SubTabSwitch current={p4SubTab} onChange={setP4SubTab} color={phase.color} />
        <div style={{
          flex: 1, marginTop: 12, border: '1px solid var(--border2)',
          borderRadius: 10, overflow: 'hidden', background: 'var(--panel)',
        }}>
          <SchematicView projectId={project.id} color={phase.color} />
        </div>
      </div>
    );
  }

  return (
    <div style={{ paddingTop: 18 }}>
      {/* P4-only: sub-tab switcher above the document list */}
      {phase.id === 'P4' && (
        <P4SubTabSwitch current={p4SubTab} onChange={setP4SubTab} color={phase.color} />
      )}

      {/* In-progress sub-step animation - keeps showing while the phase is
          still running, even after the first output files arrive. P1 is
          skipped because it has its own wizard progress UI. */}
      {status === 'in_progress' && phase.id !== 'P1' && (
        <div style={{
          marginBottom: 14, padding: '14px 18px',
          background: 'var(--panel)',
          border: `1px solid ${phase.color}30`, borderRadius: 8,
        }}>
          <GeneratingState phase={phase} elapsed={elapsedByPhase[phase.id] ?? 0} />
        </div>
      )}

      {/* Phase details accordion — collapsed by default when documents exist */}
      <PhaseDetails phase={phase} color={phase.color} collapsed />

      {/* Markdown style injection */}
      <style>{`
        .md-body { color: var(--text2); font-size: 13.5px; }
        .md-body h1 { font-size: 21px; font-weight: 800; color: var(--text); font-family: 'Syne', sans-serif; margin: 24px 0 10px; border-bottom: 1px solid var(--border2); padding-bottom: 8px; }
        .md-body h2 { font-size: 17px; font-weight: 700; color: var(--text); font-family: 'Syne', sans-serif; margin: 20px 0 8px; }
        .md-body h3 { font-size: 14px; font-weight: 700; color: var(--text2); margin: 16px 0 6px; }
        .md-body h4 { font-size: 13px; font-weight: 600; color: var(--text3); margin: 12px 0 5px; }
        .md-body p  { margin: 8px 0; line-height: 1.8; }
        .md-body ul, .md-body ol { margin: 8px 0 8px 20px; padding: 0; }
        .md-body li { margin: 5px 0; line-height: 1.7; }
        .md-body strong { color: var(--text); }
        .md-body em { color: var(--text3); }
        .md-body code { font-family: 'JetBrains Mono', monospace; font-size: 11.5px; background: var(--panel2); color: var(--teal); padding: 1px 6px; border-radius: 3px; }
        .md-body pre { background: var(--panel2); border: 1px solid var(--border2); border-radius: 6px; padding: 14px 18px; overflow-x: auto; margin: 14px 0; }
        .md-body pre code { background: none; color: var(--text2); padding: 0; font-size: 12px; }
        .md-body blockquote { border-left: 3px solid var(--teal); margin: 12px 0; padding: 8px 16px; background: rgba(0,198,167,0.05); color: var(--text3); border-radius: 0 5px 5px 0; }
        .md-body table { width: 100%; border-collapse: collapse; margin: 14px 0; font-size: 12.5px; }
        .md-body th { background: var(--panel2); color: var(--text); padding: 9px 13px; text-align: left; border: 1px solid var(--border2); font-weight: 600; font-size: 11.5px; letter-spacing: 0.04em; }
        .md-body td { padding: 8px 13px; border: 1px solid var(--border2); color: var(--text2); vertical-align: top; line-height: 1.55; }
        .md-body tr:nth-child(even) td { background: rgba(0,0,0,0.03); }
        .md-body hr { border: none; border-top: 1px solid var(--border2); margin: 18px 0; }
        .md-body a { color: var(--blue); text-decoration: underline; }
        @keyframes shimmer { from { transform: translateX(-100%); } to { transform: translateX(200%); } }
      `}</style>

      {/* Stale-status banner: phase is PENDING but previous outputs exist.
          Skip P1 — docs exist there because the user just chatted (awaiting approval), not a stale run. */}
      {status === 'pending' && filteredFiles.length > 0 && phase.id !== 'P1' && (
        <div style={{
          marginBottom: 14,
          padding: '9px 14px',
          background: 'rgba(245,158,11,0.07)',
          border: '1px solid rgba(245,158,11,0.28)',
          borderRadius: 7,
          display: 'flex', alignItems: 'center', gap: 9,
        }}>
          <span style={{ fontSize: 14, flexShrink: 0 }}>⚠</span>
          <div>
            <span style={{ fontSize: 11.5, color: '#f59e0b', fontFamily: "'DM Mono',monospace", fontWeight: 600 }}>
              Previous run output
            </span>
            <span style={{ fontSize: 11.5, color: 'var(--text4)', marginLeft: 6 }}>
              These documents are from a prior pipeline run. Status shows PENDING — re-run this phase to refresh.
            </span>
          </div>
        </div>
      )}

      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{
            fontSize: 10, color: 'var(--text4)', fontFamily: "'DM Mono', monospace",
            letterSpacing: '0.1em',
          }}>
            {filteredFiles.length} {filteredFiles.length === 1 ? 'DOCUMENT' : 'DOCUMENTS'}
          </div>
          {status === 'in_progress' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <div style={{ width: 7, height: 7, borderRadius: '50%', background: phase.color, animation: 'pulse 1.5s ease infinite' }} />
              <span style={{ fontSize: 10, color: phase.color, fontFamily: "'DM Mono', monospace" }}>UPDATING</span>
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 7, alignItems: 'center' }}>
          {/* Download THIS phase's files as ZIP. Passing phase.id makes
              the backend filter the deliverable bundle to just this
              phase's folder — without it every phase's button would
              download the whole-project bundle (was a real bug). */}
          {project && filteredFiles.length > 0 && (
            <a
              href={api.exportZipUrl(project.id, phase.id)}
              download
              style={{
                fontSize: 11, color: '#22c55e',
                background: 'rgba(34,197,94,0.07)',
                border: '1px solid rgba(34,197,94,0.3)',
                borderRadius: 5, cursor: 'pointer',
                fontFamily: "'DM Mono', monospace",
                padding: '4px 11px', transition: 'all 0.15s',
                display: 'flex', alignItems: 'center', gap: 5,
                textDecoration: 'none', whiteSpace: 'nowrap',
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLAnchorElement).style.background = 'rgba(34,197,94,0.14)'; (e.currentTarget as HTMLAnchorElement).style.boxShadow = '0 0 10px rgba(34,197,94,0.2)'; }}
              onMouseLeave={e => { (e.currentTarget as HTMLAnchorElement).style.background = 'rgba(34,197,94,0.07)'; (e.currentTarget as HTMLAnchorElement).style.boxShadow = 'none'; }}
            >
              ↓ Export ZIP
            </a>
          )}
        </div>
      </div>

      {/* P1: RF cascade analysis chart — renders when cascade_analysis.json exists.
          Hidden for non-RF designs where the JSON is absent / empty. */}
      {phase.id === 'P1' && project && (
        <CascadeChart projectId={project.id} color={phase.color} />
      )}

      {/* File list */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {filteredFiles.map(file => {
          const ext = getExt(file.name);
          const color = extColor(ext);
          const isViewable = VIEWABLE.has(ext);
          const isOpen = expanded === file.name;
          const isLoading = loadingFile[file.name];
          const contentLoaded = contents[file.name] !== undefined;

          return (
            <div key={file.name} style={{
              border: `1px solid ${isOpen ? phase.color + '60' : 'var(--border2)'}`,
              borderRadius: 10, overflow: 'hidden',
              transition: 'border-color 0.2s',
              background: isOpen ? 'var(--panel2)' : 'var(--panel)',
            }}>
              {/* File row */}
              <div style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '13px 16px',
                background: isOpen ? `${phase.color}06` : 'transparent',
              }}>
                {/* File icon */}
                <FileIcon ext={ext} color={color} />

                {/* File info */}
                <div
                  onClick={() => fetchContent(file)}
                  style={{ flex: 1, minWidth: 0, cursor: 'pointer' }}
                >
                  <div style={{
                    fontSize: 13.5, color: 'var(--text)', fontWeight: 600,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    marginBottom: 2,
                  }}>
                    {file.name}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{
                      fontSize: 10, padding: '1px 7px', borderRadius: 3,
                      background: `${color}12`, color, border: `1px solid ${color}22`,
                      fontFamily: "'DM Mono', monospace",
                    }}>
                      {extLabel(ext)}
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--text4)' }}>
                      {formatSize(file.size)}
                    </span>
                  </div>
                </div>

                {/* Loading spinner */}
                {isLoading && (
                  <div style={{ width: 14, height: 14, borderRadius: '50%', border: `2px solid ${phase.color}`, borderTopColor: 'transparent', animation: 'spin 0.8s linear infinite', flexShrink: 0 }} />
                )}

                {/* Action buttons */}
                <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                  {isViewable && (
                    <button
                      onClick={() => fetchContent(file)}
                      style={{
                        fontSize: 12, color: isOpen ? phase.color : 'var(--text3)',
                        background: isOpen ? `${phase.color}12` : 'var(--panel2)',
                        border: `1px solid ${isOpen ? phase.color + '44' : 'var(--panel3)'}`,
                        borderRadius: 6, cursor: 'pointer',
                        fontFamily: "'DM Mono', monospace",
                        padding: '5px 12px', transition: 'all 0.15s',
                        whiteSpace: 'nowrap',
                      }}
                      onMouseEnter={e => { if (!isOpen) { e.currentTarget.style.color = phase.color; e.currentTarget.style.borderColor = `${phase.color}44`; }}}
                      onMouseLeave={e => { if (!isOpen) { e.currentTarget.style.color = 'var(--text3)'; e.currentTarget.style.borderColor = 'var(--panel3)'; }}}
                    >
                      {isLoading
                        ? <><span style={{ width: 9, height: 9, borderRadius: '50%', border: `2px solid ${phase.color}`, borderTopColor: 'transparent', display: 'inline-block', animation: 'spin 0.7s linear infinite', marginRight: 5 }} />Loading…</>
                        : isOpen ? '▲ Close' : contentsRef.current[file.name] !== undefined ? '▼ Preview ✓' : '▼ Preview'}
                    </button>
                  )}

                  {/* ↓ DOCX button — only for .md files */}
                  {getExt(file.name) === 'md' && (() => {
                    const isConverting = !!docxConverting[file.name];
                    const isPreparing = !isConverting && !!docxPreconverting[file.name];
                    const isReady    = !!docxBlobUrls[file.name];
                    const busy = isConverting || isPreparing;
                    const btnColor = isConverting ? '#3b82f6' : isPreparing ? '#f59e0b' : 'var(--text3)';
                    const btnBg    = isConverting ? 'rgba(59,130,246,0.08)' : isPreparing ? 'rgba(245,158,11,0.08)' : 'var(--panel2)';
                    const btnBorder= isConverting ? '#3b82f666' : isPreparing ? '#f59e0b66' : 'var(--panel3)';
                    return (
                      <button
                        onClick={(e) => { e.stopPropagation(); if (!busy) triggerDocxDownload(file); }}
                        disabled={isConverting}
                        title={
                          isConverting ? 'Converting to Word document…' :
                          isPreparing  ? 'Preparing Word document in background — click to download when ready' :
                          isReady      ? `Word document ready — click to download` :
                          `Convert ${file.name} to Word document (.docx)`
                        }
                        style={{
                          fontSize: 12,
                          color: btnColor,
                          background: btnBg,
                          border: `1px solid ${btnBorder}`,
                          borderRadius: 6,
                          cursor: isConverting ? 'not-allowed' : 'pointer',
                          fontFamily: "'DM Mono', monospace",
                          padding: '5px 12px', transition: 'all 0.15s',
                          display: 'flex', alignItems: 'center', gap: 6,
                          whiteSpace: 'nowrap', opacity: busy ? 0.9 : 1,
                        }}
                        onMouseEnter={e => { if (!busy) { e.currentTarget.style.color = '#3b82f6'; e.currentTarget.style.borderColor = '#3b82f666'; e.currentTarget.style.background = 'rgba(59,130,246,0.08)'; }}}
                        onMouseLeave={e => { if (!busy) { e.currentTarget.style.color = 'var(--text3)'; e.currentTarget.style.borderColor = 'var(--panel3)'; e.currentTarget.style.background = 'var(--panel2)'; }}}
                      >
                        {isConverting ? (
                          <><span style={{ width: 10, height: 10, borderRadius: '50%', border: '2px solid #3b82f6', borderTopColor: 'transparent', display: 'inline-block', animation: 'spin 0.7s linear infinite', flexShrink: 0 }} />Converting…</>
                        ) : isPreparing ? (
                          <><span style={{ width: 10, height: 10, borderRadius: '50%', border: '2px solid #f59e0b', borderTopColor: 'transparent', display: 'inline-block', animation: 'spin 0.7s linear infinite', flexShrink: 0 }} />Preparing…</>
                        ) : isReady ? '↓ DOCX ✓' : '↓ DOCX'}
                      </button>
                    );
                  })()}
                  {/* Inline error message when DOCX conversion fails */}
                  {docxError[file.name] && (
                    <span style={{ fontSize: 11, color: '#dc2626', fontFamily: "'DM Mono',monospace", maxWidth: 200 }}>
                      ⚠ {docxError[file.name]}
                    </span>
                  )}

                  <button
                    onClick={(e) => { e.stopPropagation(); triggerDownload(file); }}
                    title={`Download ${file.name}`}
                    style={{
                      fontSize: 12, color: 'var(--text3)',
                      background: 'var(--panel2)',
                      border: '1px solid var(--panel3)',
                      borderRadius: 6, cursor: 'pointer',
                      fontFamily: "'DM Mono', monospace",
                      padding: '5px 12px', transition: 'all 0.15s',
                      display: 'flex', alignItems: 'center', gap: 5,
                      whiteSpace: 'nowrap',
                    }}
                    onMouseEnter={e => { e.currentTarget.style.color = '#22c55e'; e.currentTarget.style.borderColor = '#22c55e66'; e.currentTarget.style.background = 'rgba(34,197,94,0.08)'; }}
                    onMouseLeave={e => { e.currentTarget.style.color = 'var(--text3)'; e.currentTarget.style.borderColor = 'var(--panel3)'; e.currentTarget.style.background = 'var(--panel2)'; }}
                  >
                    ↓ Download
                  </button>
                </div>
              </div>

              {/* Content pane */}
              {isOpen && contentLoaded && (
                <div style={{
                  borderTop: `1px solid ${phase.color}25`,
                  background: 'var(--panel)',
                  maxHeight: 720,
                  overflowY: 'auto',
                }}>
                  {ext === 'md' || ext === 'txt' ? (
                    <MarkdownRenderer content={contents[file.name]} color={phase.color} />
                  ) : ext === 'json' ? (
                    /* JSON: pretty-print with syntax-tinted scrollable pane */
                    <div>
                      <div style={{ padding: '8px 22px 4px', background: 'rgba(245,158,11,0.06)', borderBottom: '1px solid rgba(245,158,11,0.15)', display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontSize: 10, color: '#f59e0b', fontFamily: "'DM Mono', monospace", letterSpacing: 1 }}>JSON</span>
                        {file.name.includes('sbom') && (
                          <span style={{ fontSize: 10, color: '#10b981', fontFamily: "'DM Mono', monospace", background: 'rgba(16,185,129,0.1)', padding: '2px 7px', borderRadius: 3, border: '1px solid rgba(16,185,129,0.3)' }}>CycloneDX SBOM</span>
                        )}
                      </div>
                      <pre style={{ margin: 0, padding: '14px 22px', fontSize: 11, color: '#f59e0b', lineHeight: 1.7, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: "'JetBrains Mono', monospace" }}>
                        {(() => { try { return JSON.stringify(JSON.parse(contents[file.name]), null, 2); } catch { return contents[file.name]; } })()}
                      </pre>
                    </div>
                  ) : ext === 'py' || ext === 'c' || ext === 'cpp' || ext === 'h' ? (
                    /* Code files: language-labelled monospace block */
                    <div>
                      <div style={{ padding: '8px 22px 4px', background: `${extColor(ext)}0d`, borderBottom: `1px solid ${extColor(ext)}25`, display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontSize: 10, color: extColor(ext), fontFamily: "'DM Mono', monospace", letterSpacing: 1 }}>{extLabel(ext).toUpperCase()}</span>
                        {file.name === 'gui_application.py' && (
                          <span style={{ fontSize: 10, color: '#8b5cf6', fontFamily: "'DM Mono', monospace", background: 'rgba(139,92,246,0.1)', padding: '2px 7px', borderRadius: 3, border: '1px solid rgba(139,92,246,0.3)' }}>PySide6 Qt GUI • pip install PySide6 pyserial && python {file.name}</span>
                        )}
                      </div>
                      <pre style={{ margin: 0, padding: '14px 22px', fontSize: 11, color: 'var(--text2)', lineHeight: 1.7, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: "'JetBrains Mono', monospace" }}>
                        {contents[file.name]}
                      </pre>
                    </div>
                  ) : (
                    <pre style={{
                      margin: 0, padding: '18px 22px',
                      fontSize: 12, color: 'var(--text2)', lineHeight: 1.8,
                      whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                      fontFamily: "'JetBrains Mono', monospace",
                    }}>
                      {contents[file.name]}
                    </pre>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── P4SubTabSwitch — toggles between "Files" (default document list) and
//    "Schematic" (interactive gate-level schematic viewer). P4 only.
function P4SubTabSwitch({
  current,
  onChange,
  color,
}: {
  current: 'files' | 'schematic';
  onChange: (v: 'files' | 'schematic') => void;
  color: string;
}) {
  const Tab = ({ id, label, icon }: { id: 'files' | 'schematic'; label: string; icon: string }) => {
    const active = current === id;
    return (
      <button
        onClick={() => onChange(id)}
        style={{
          fontSize: 11,
          fontFamily: "'DM Mono', monospace",
          letterSpacing: '0.08em',
          padding: '6px 14px',
          border: `1px solid ${active ? color + '66' : 'var(--border2)'}`,
          background: active ? `${color}14` : 'var(--panel2)',
          color: active ? color : 'var(--text3)',
          borderRadius: 5,
          cursor: 'pointer',
          whiteSpace: 'nowrap',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          transition: 'all 0.15s',
        }}
      >
        <span style={{ fontSize: 12 }}>{icon}</span>
        {label}
      </button>
    );
  };

  return (
    <div
      style={{
        display: 'flex',
        gap: 8,
        marginBottom: 14,
        padding: '4px 0',
        borderBottom: '1px solid var(--border2)',
        paddingBottom: 12,
      }}
    >
      <Tab id="files" label="FILES" icon="📄" />
      <Tab id="schematic" label="SCHEMATIC" icon="⬡" />
      <span
        style={{
          marginLeft: 'auto',
          alignSelf: 'center',
          fontSize: 10,
          color: 'var(--text4)',
          fontFamily: "'DM Mono', monospace",
          letterSpacing: '0.08em',
        }}
      >
        {current === 'schematic' ? 'GATE-LEVEL · INTERACTIVE · PDF EXPORT' : 'GENERATED OUTPUTS'}
      </span>
    </div>
  );
}