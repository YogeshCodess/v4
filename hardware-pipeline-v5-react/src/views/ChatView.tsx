import { useState, useRef, useEffect, useCallback, memo } from 'react';
import type { Project, PhaseMeta, DesignScope } from '../types';
import { SCOPE_LABELS } from '../types';
import { api, type ChatResult } from '../api';
import { ensureMermaid, purgeMermaidScratch, nextMermaidId } from '../utils/mermaid';
import { sanitizeMermaid } from '../utils/mermaidSanitize';
import MermaidErrorBoundary from '../components/MermaidErrorBoundary';
import { parseQuestionsFromAI, shouldShowQuestions, type QuestionCard as QuestionCardType } from '../data/questionSchema';
import {
  PROJECT_TYPES,
  SCOPE_DESC, APPLICATIONS, ALL_ARCHITECTURES,
  emptyWizardState, archById, specLabel,
  filterSpecsByScope, filterArchByScopeAndApp, filterTxArchByScopeAndApp,
  filterTrxArchByScope, filterPsuArch, filterSwmArch,
  applicationsForProjectType, scopesForProjectType,
  resolveDeepDiveQs, resolveAppQs, allInlineSuggestions,
  firedCascadeMessages, archRationale,
  AUTO_SUGGESTIONS,
  type WizardState, type SpecDef, type DeepDiveQ, type AppQDef, type ArchDef,
} from '../data/rfArchitect';

export interface ChatMessage { role: 'user' | 'ai'; text: string; id: string; }

/** v13 — stable per-message id so clarification-card state doesn't re-key
 *  when the messages array gets mutated (hydrate on F5, error retry, etc).
 *  crypto.randomUUID is available in all modern browsers but we fall back
 *  for old webviews. */
let _msgIdCounter = 0;
export function newMsgId(): string {
  _msgIdCounter += 1;
  try {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      return crypto.randomUUID();
    }
  } catch { /* ignore */ }
  return `msg-${Date.now()}-${_msgIdCounter}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Truncate long questions to fit in cards */
function truncateQuestion(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength - 3) + '...';
}

function MermaidBlock({ code, color }: { code: string; color: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const idRef = useRef(nextMermaidId());

  useEffect(() => {
    let cancelled = false;
    setSvg(null);
    setError(false);

    const safeCode = sanitizeMermaid(code);
    const id = idRef.current;

    ensureMermaid(() => {
      if (cancelled) return;

      (async () => {
        try {
          // render() only — no parse() to avoid Mermaid firing error toasts before our catch
          const result = await window.mermaid!.render(id, safeCode);
          const svgStr: string = result?.svg ?? '';
          purgeMermaidScratch(id);
          if (cancelled) return;
          if (svgStr.includes('<svg') && !svgStr.includes('Syntax error') && !svgStr.includes('class="error"')) {
            setSvg(svgStr);
          } else {
            setError(true);
          }
        } catch {
          purgeMermaidScratch(id);
          if (!cancelled) setError(true);
        }
      })();
    });

    return () => {
      cancelled = true;
      purgeMermaidScratch(id);
    };
  }, [code]);

  if (!svg) {
    return (
      <div style={{ margin: '10px 0' }}>
        <div style={{
          fontSize: 10, color, letterSpacing: '0.08em',
          background: `${color}0d`, padding: '4px 12px',
          borderRadius: '6px 6px 0 0', border: `1px solid ${color}22`,
          borderBottom: 'none',
        }}>
          {error ? 'BLOCK DIAGRAM (source)' : 'BLOCK DIAGRAM \u2014 rendering...'}
        </div>
        <pre style={{
          background: 'var(--panel2)', border: `1px solid ${color}22`,
          borderRadius: '0 0 6px 6px', padding: '12px 14px', margin: 0,
          fontSize: 12, color, fontFamily: "'JetBrains Mono',monospace",
          overflowX: 'auto', lineHeight: 1.65, whiteSpace: 'pre-wrap',
        }}>
          {code}
        </pre>
      </div>
    );
  }

  return (
    <div style={{ margin: '10px 0' }}>
      <div style={{
        fontSize: 10, color, letterSpacing: '0.08em',
        background: `${color}0d`, padding: '4px 12px',
        borderRadius: '6px 6px 0 0', border: `1px solid ${color}22`,
        borderBottom: 'none', display: 'flex', alignItems: 'center', gap: 6,
      }}>
        &#128202; SYSTEM ARCHITECTURE DIAGRAM
      </div>
      <div style={{
        background: 'var(--panel2)', border: `1px solid ${color}22`,
        borderRadius: '0 0 6px 6px', padding: '16px', overflowX: 'auto',
      }}
        dangerouslySetInnerHTML={{ __html: svg }}
      />
    </div>
  );
}


// ---- Lightweight markdown renderer with Mermaid diagram support ----

function renderMarkdown(text: string, color: string): React.ReactNode {
  const lines = text.split('\n');
  const elements: React.ReactNode[] = [];
  let i = 0;

  const inline = (raw: string): React.ReactNode => {
    const parts = raw.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g);
    return parts.map((p, j) => {
      if (p.startsWith('**') && p.endsWith('**'))
        return <strong key={j} style={{ color: 'var(--text)', fontWeight: 700 }}>{p.slice(2,-2)}</strong>;
      if (p.startsWith('*') && p.endsWith('*'))
        return <em key={j} style={{ color: 'var(--text2)', fontStyle: 'italic' }}>{p.slice(1,-1)}</em>;
      if (p.startsWith('`') && p.endsWith('`'))
        return <code key={j} style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 11, background: 'var(--panel2)', color, padding: '1px 5px', borderRadius: 3 }}>{p.slice(1,-1)}</code>;
      return p;
    });
  };

  while (i < lines.length) {
    const line = lines[i];

    // Headers
    if (line.startsWith('### ')) {
      elements.push(<div key={i} style={{ fontSize: 13, fontWeight: 700, color, margin: '12px 0 4px' }}>{inline(line.slice(4))}</div>);
      i++; continue;
    }
    if (line.startsWith('## ')) {
      elements.push(<div key={i} style={{ fontFamily:"'Syne',sans-serif", fontSize: 14, fontWeight: 800, color: 'var(--text)', margin: '14px 0 6px' }}>{inline(line.slice(3))}</div>);
      i++; continue;
    }
    if (line.startsWith('# ')) {
      elements.push(<div key={i} style={{ fontFamily:"'Syne',sans-serif", fontSize: 16, fontWeight: 800, color: 'var(--text)', margin: '16px 0 8px', borderBottom: `1px solid ${color}33`, paddingBottom: 6 }}>{inline(line.slice(2))}</div>);
      i++; continue;
    }

    // Code blocks — Mermaid gets rendered as diagrams, others as styled code
    if (line.startsWith('```')) {
      const lang = line.slice(3).trim();
      const isMermaid = lang.toLowerCase() === 'mermaid';
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) { codeLines.push(lines[i]); i++; }
      const codeText = codeLines.join('\n');

      if (isMermaid) {
        elements.push(
          <MermaidErrorBoundary key={`mermaid-${i}`} source={codeText} color={color} label="BLOCK DIAGRAM">
            <MermaidBlock code={codeText} color={color} />
          </MermaidErrorBoundary>
        );
      } else {
        elements.push(
          <div key={`code-${i}`} style={{ margin: '10px 0' }}>
            <pre style={{
              background: 'var(--panel2)',
              border: '1px solid var(--border2)',
              borderRadius: 6,
              padding: '12px 14px', margin: 0,
              fontSize: 12, color: 'var(--text2)',
              fontFamily: "'JetBrains Mono',monospace",
              overflowX: 'auto', lineHeight: 1.65, whiteSpace: 'pre-wrap',
            }}>
              {codeText}
            </pre>
          </div>
        );
      }
      if (i < lines.length) i++;
      continue;
    }

    // Table
    if (line.startsWith('|')) {
      const tableLines: string[] = [];
      while (i < lines.length && lines[i].startsWith('|')) { tableLines.push(lines[i]); i++; }
      const rows = tableLines.filter(l => !l.match(/^\|[-| :]+\|$/));
      elements.push(
        <div key={`tbl-${i}`} style={{ overflowX: 'auto', margin: '10px 0' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 12, width: '100%' }}>
            <tbody>
              {rows.map((row, ri) => {
                const cells = row.split('|').slice(1, -1);
                return (
                  <tr key={ri} style={{ borderBottom: `1px solid ${color}22` }}>
                    {cells.map((cell, ci) => (
                      <td key={ci} style={{ padding: '6px 12px', color: ri === 0 ? color : 'var(--text2)', fontWeight: ri === 0 ? 600 : 400, background: ri === 0 ? `${color}0d` : 'transparent', fontFamily: "'DM Mono',monospace", borderRight: `1px solid ${color}11` }}>
                        {inline(cell.trim())}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    // Bullet list
    if (line.startsWith('- ') || line.startsWith('* ')) {
      const items: string[] = [];
      while (i < lines.length && (lines[i].startsWith('- ') || lines[i].startsWith('* '))) { items.push(lines[i].slice(2)); i++; }
      elements.push(
        <ul key={`ul-${i}`} style={{ margin: '6px 0', padding: 0, listStyle: 'none' }}>
          {items.map((item, j) => (
            <li key={j} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 4 }}>
              <span style={{ color, marginTop: 3, fontSize: 9, flexShrink: 0 }}>&#9679;</span>
              <span style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.55 }}>{inline(item)}</span>
            </li>
          ))}
        </ul>
      );
      continue;
    }

    // Blank line
    if (!line.trim()) { elements.push(<div key={i} style={{ height: 6 }} />); i++; continue; }

    // Paragraph
    elements.push(<div key={i} style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.65, marginBottom: 2 }}>{inline(line)}</div>);
    i++;
  }
  return <>{elements}</>;
}

// ── FollowUpCardGroup ───────────────────────────────────────────────────────
// Rendered inline below an AI message when a follow-up elicitation round came
// back from POST /clarify. Same visual language as the pre-stage clarifying
// cards — multi-select chips, "Other" input, optional free-text, one submit.
function FollowUpCardGroup({
  color, cards, answers, extraText,
  otherActiveKey, otherInputs, msgIdx,
  onAnswer, onOtherActiveChange, onOtherInputChange, onExtraChange,
  onSubmit, onDismiss,
}: {
  color: string;
  cards: ClarificationData;
  answers: Record<string, string>;
  extraText: string;
  otherActiveKey: string | null;
  otherInputs: Record<string, string>;
  // v13 — now a stable per-bubble string id (was numeric array index).
  // Only used as a prefix inside `${msgIdx}:${qId}` to namespace "Other"
  // chip state across multiple AI bubbles.
  msgIdx: string;
  onAnswer: (qId: string, val: string) => void;
  onOtherActiveChange: (key: string | null) => void;
  onOtherInputChange: (key: string, val: string) => void;
  onExtraChange: (val: string) => void;
  onSubmit: () => void;
  onDismiss: () => void;
}) {
  const qs = cards.questions || [];
  const answeredCount = qs.filter(q => answers[q.id]).length;
  const allAnswered = qs.length > 0 && answeredCount === qs.length;
  const canSubmit = answeredCount > 0 || extraText.trim().length > 0;

  return (
    <div style={{
      marginBottom: 18, marginTop: -4,
      background: 'var(--panel2)',
      border: `1px solid ${color}33`,
      borderLeft: `2px solid ${color}88`,
      borderRadius: '0 12px 12px 4px',
      padding: '14px 18px 12px',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: 10,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 13 }}>&#9889;</span>
          <span style={{
            fontSize: 10, color, fontFamily: "'DM Mono',monospace",
            letterSpacing: '0.1em', fontWeight: 600,
          }}>
            QUICK ANSWERS &middot; {answeredCount}/{qs.length} selected
          </span>
        </div>
        <button
          onClick={onDismiss}
          title="Dismiss and type a free-form reply"
          style={{
            background: 'none', border: 'none', color: 'var(--text4)',
            fontSize: 16, cursor: 'pointer', lineHeight: 1, padding: '2px 6px',
          }}>×</button>
      </div>

      {/* Prefilled chips — shows values the LLM extracted from the user's
          opening message so the user knows those questions were skipped. */}
      {cards.prefilled && Object.keys(cards.prefilled).length > 0 && (
        <div style={{
          marginBottom: 12,
          padding: '8px 10px',
          background: `${color}11`,
          border: `1px dashed ${color}55`,
          borderRadius: 6,
        }}>
          <div style={{
            fontSize: 9, color, letterSpacing: '0.12em',
            textTransform: 'uppercase' as const, fontWeight: 600,
            fontFamily: "'DM Mono',monospace", marginBottom: 6,
            opacity: 0.9,
          }}>
            CAPTURED FROM YOUR MESSAGE
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap' as const, gap: 6 }}>
            {Object.entries(cards.prefilled).map(([k, v]) => (
              <span key={k} style={{
                padding: '3px 9px',
                fontSize: 11,
                fontFamily: "'DM Mono',monospace",
                background: `${color}22`,
                border: `0.5px solid ${color}66`,
                borderRadius: 3,
                color: 'var(--text)',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 5,
              }}>
                <span style={{ opacity: 0.6, fontSize: 9, letterSpacing: '0.08em' }}>
                  {(PREFILLED_LABELS[k] || k).toUpperCase()}
                </span>
                <span>&#10003; {v}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {cards.intro && (
        <div style={{
          fontSize: 12, color: 'var(--text3)', lineHeight: 1.55,
          marginBottom: 12, fontStyle: 'italic',
        }}>
          {cards.intro}
        </div>
      )}

      {qs.map((q, qi) => {
        const qColor = Q_COLORS_CLARIFY[qi % Q_COLORS_CLARIFY.length];
        const sel = answers[q.id];
        const otherKey = `${msgIdx}:${q.id}`;
        const isOtherActive = otherActiveKey === otherKey;
        return (
          <div key={q.id} style={{ marginBottom: 14 }}>
            <div style={{
              fontSize: 10, color: qColor, letterSpacing: '0.1em',
              textTransform: 'uppercase' as const, fontWeight: 600,
              fontFamily: "'DM Mono',monospace", marginBottom: 4,
            }}>
              Q{qi + 1} &middot; {q.why}
            </div>
            <div style={{
              fontSize: 13, color: 'var(--text)', lineHeight: 1.5, marginBottom: 8,
            }}>
              {q.question}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap' as const, gap: 7 }}>
              {(q.options || []).map(opt => {
                const isSel = sel === opt;
                // Hybrid chip: "Auto" / "Auto (estimate)" signals the backend
                // should derive this spec from cascade math. Render with a
                // dashed border + italic label to distinguish from fixed values.
                const isAutoChip = /^auto\b/i.test(opt.trim());
                return (
                  <button key={opt}
                    onClick={() => { onAnswer(q.id, opt); onOtherActiveChange(null); }}
                    style={{
                      padding: '6px 13px', fontSize: 12,
                      fontFamily: "'DM Mono',monospace",
                      background: isSel ? `${qColor}22` : 'var(--panel)',
                      border: isAutoChip
                        ? `0.5px dashed ${isSel ? qColor : qColor + '77'}`
                        : `0.5px solid ${isSel ? qColor : qColor + '44'}`,
                      borderRadius: 4, cursor: 'pointer',
                      color: isSel ? 'var(--text)' : 'var(--text3)',
                      fontStyle: isAutoChip ? ('italic' as const) : ('normal' as const),
                      transition: 'all 0.12s',
                    }}
                    title={isAutoChip ? 'Let the system estimate from cascade math' : undefined}
                    onMouseEnter={e => { if (!isSel) { e.currentTarget.style.borderColor = qColor; e.currentTarget.style.color = 'var(--text)'; } }}
                    onMouseLeave={e => { if (!isSel) { e.currentTarget.style.borderColor = qColor + (isAutoChip ? '77' : '44'); e.currentTarget.style.color = 'var(--text3)'; } }}>
                    {isSel ? '\u2713 ' : ''}{isAutoChip ? `\u2699 ${opt}` : opt}
                  </button>
                );
              })}
              {!isOtherActive ? (
                <button
                  onClick={() => onOtherActiveChange(otherKey)}
                  style={{
                    padding: '6px 13px', fontSize: 12,
                    fontFamily: "'DM Mono',monospace",
                    background: (sel && !q.options.includes(sel)) ? `${qColor}22` : 'var(--panel)',
                    border: `0.5px solid ${(sel && !q.options.includes(sel)) ? qColor : qColor + '44'}`,
                    borderRadius: 4, cursor: 'pointer',
                    color: (sel && !q.options.includes(sel)) ? 'var(--text)' : 'var(--text3)',
                    transition: 'all 0.12s',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor = qColor; e.currentTarget.style.color = 'var(--text)'; }}
                  onMouseLeave={e => { if (!(sel && !q.options.includes(sel))) { e.currentTarget.style.borderColor = qColor + '44'; e.currentTarget.style.color = 'var(--text3)'; } }}>
                  {(sel && !q.options.includes(sel)) ? `\u2713 ${sel}` : '\u270F Other'}
                </button>
              ) : (
                <div style={{ display: 'flex', gap: 6, width: '100%', marginTop: 4 }}>
                  <input
                    autoFocus
                    value={otherInputs[otherKey] || ''}
                    onChange={e => onOtherInputChange(otherKey, e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter' && (otherInputs[otherKey] || '').trim()) {
                        onAnswer(q.id, (otherInputs[otherKey] || '').trim());
                        onOtherActiveChange(null);
                      } else if (e.key === 'Escape') {
                        onOtherActiveChange(null);
                      }
                    }}
                    placeholder="Type your answer..."
                    style={{
                      flex: 1, background: 'var(--panel)',
                      border: `1px solid ${qColor}66`, borderRadius: 4,
                      padding: '5px 10px', fontSize: 12, color: 'var(--text)',
                      fontFamily: "'DM Mono',monospace", outline: 'none',
                    }}
                  />
                  <button
                    disabled={!(otherInputs[otherKey] || '').trim()}
                    onClick={() => {
                      const v = (otherInputs[otherKey] || '').trim();
                      if (v) { onAnswer(q.id, v); onOtherActiveChange(null); }
                    }}
                    style={{
                      padding: '5px 12px', fontSize: 11,
                      background: (otherInputs[otherKey] || '').trim() ? qColor : 'var(--panel2)',
                      border: 'none', borderRadius: 4,
                      cursor: (otherInputs[otherKey] || '').trim() ? 'pointer' : 'default',
                      color: (otherInputs[otherKey] || '').trim() ? '#070b14' : 'var(--text4)',
                      fontFamily: "'DM Mono',monospace", fontWeight: 700,
                    }}>OK</button>
                </div>
              )}
            </div>
          </div>
        );
      })}

      {/* Optional free-text field (collapsed until clicked) */}
      <div style={{
        marginTop: 6, marginBottom: 10,
        borderTop: '1px dashed var(--panel3)', paddingTop: 10,
      }}>
        <div style={{
          fontSize: 10, color: 'var(--text4)', letterSpacing: '0.1em',
          textTransform: 'uppercase' as const, fontWeight: 600,
          fontFamily: "'DM Mono',monospace", marginBottom: 4,
        }}>
          ADDITIONAL NOTES <span style={{ opacity: 0.6, textTransform: 'none' as const, letterSpacing: 0, fontWeight: 400 }}>optional</span>
        </div>
        <textarea
          value={extraText}
          onChange={e => onExtraChange(e.target.value)}
          placeholder="Anything not covered by the options above..."
          rows={2}
          style={{
            width: '100%', boxSizing: 'border-box' as const,
            background: 'var(--panel)',
            border: `1px solid ${extraText.trim() ? color + '55' : 'var(--panel3)'}`,
            borderRadius: 5, padding: '7px 10px', fontSize: 12,
            color: 'var(--text)', fontFamily: "'DM Mono',monospace",
            resize: 'vertical' as const, outline: 'none', lineHeight: 1.55,
            transition: 'border-color 0.15s',
          }}
        />
      </div>

      {/* Submit */}
      <div style={{
        display: 'flex', alignItems: 'center',
        justifyContent: 'space-between', gap: 10,
      }}>
        <span style={{
          fontSize: 11, color: 'var(--text4)',
          fontFamily: "'DM Mono',monospace",
        }}>
          {allAnswered ? 'All questions answered' : `${qs.length - answeredCount} remaining`}
        </span>
        <button
          onClick={onSubmit}
          disabled={!canSubmit}
          style={{
            padding: '8px 20px', borderRadius: 6,
            background: canSubmit ? color : 'var(--panel3)',
            color: canSubmit ? '#070b14' : 'var(--text4)',
            border: 'none', fontSize: 12,
            fontFamily: "'DM Mono',monospace", fontWeight: 700,
            cursor: canSubmit ? 'pointer' : 'default',
            transition: 'all 0.15s',
          }}>
          Send {answeredCount > 0 ? `${answeredCount} answer${answeredCount > 1 ? 's' : ''}` : 'answer'} &rarr;
        </button>
      </div>
    </div>
  );
}

// ---- Memoized message row — skips re-render when only `streaming` state changes ----
const ChatMessageItem = memo(function ChatMessageItem({ msg, color, overrideText }: { msg: ChatMessage; color: string; overrideText?: string }) {
  if (msg.role === 'user') {
    return (
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, alignItems: 'flex-end' }}>
          <div style={{
            maxWidth: '75%', padding: '11px 16px', borderRadius: '12px 12px 4px 12px',
            background: `linear-gradient(135deg, ${color}20, ${color}10)`,
            border: `1px solid ${color}33`, fontSize: 13, color: 'var(--text)',
            lineHeight: 1.6, fontFamily: "'DM Mono',monospace", whiteSpace: 'pre-wrap',
          }}>
            {msg.text}
          </div>
        </div>
      </div>
    );
  }
  const displayText = overrideText ?? msg.text;
  // If the override collapsed the text to nothing (all questions, no intro),
  // skip the whole bubble — the FollowUpCardGroup below carries everything.
  if (!displayText.trim()) return null;
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{
        padding: '16px 20px', borderRadius: '12px 12px 12px 4px',
        background: 'var(--panel2)', border: '1px solid var(--panel3)',
        borderLeft: `2px solid ${color}44`,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
          <div style={{ width: 6, height: 6, borderRadius: '50%', background: color, boxShadow: `0 0 6px ${color}66` }} />
          <span style={{ fontSize: 10, color, letterSpacing: '0.1em', fontWeight: 600, fontFamily: "'DM Mono',monospace" }}>AI RESPONSE</span>
        </div>
        {renderMarkdown(displayText, color)}
      </div>
    </div>
  );
});

// ── QuickReplyPanel ───────────────────────────────────────────────────────────
// Parses the last AI message for numbered questions and renders as a modal
// popup with clickable chips + an "Other..." option per question.

interface ParsedQuestion {
  index: number;       // 1-based question number
  label: string;       // short label e.g. "Supply voltage"
  body: string;        // full question text
  options: string[];   // extracted answer chips (empty = show only Other)
}

/**
 * Split a question body containing multiple independent questions into sub-cards.
 * Only splits on "? " followed by common English question-starter words so that
 * inline option lists like "A, B, or C?" are never broken apart.
 */
function splitMultiBody(q: ParsedQuestion): ParsedQuestion[] {
  if ((q.body.match(/\?/g) || []).length < 2) return [q];

  // Only split before recognised question-opener words (not mid-option-list)
  const parts = q.body.split(
    /\?\s+(?=(?:Do|Does|Did|Is|Are|Was|Were|Will|Would|Can|Could|Should|Have|Has|What|Which|How|Where|When|Who|Any|Please|Specify|Indicate|Select|If|For|In|With|Provide|List|Describe|Define|Confirm|Tell)\b)/
  );
  if (parts.length <= 1) return [q];

  return parts
    .map(p => p.trim())
    .filter(p => p.length > 5)
    .map(part => {
      const body = part.endsWith('?') || part.endsWith('!') ? part : part + '?';
      return { ...q, body, options: extractOptions(body) };
    });
}

/** Expand multi-sentence questions and renumber the whole list 1, 2, 3… */
function expandAndRenumber(questions: ParsedQuestion[]): ParsedQuestion[] {
  const expanded: ParsedQuestion[] = [];
  for (const q of questions) expanded.push(...splitMultiBody(q));
  return expanded.map((q, i) => ({ ...q, index: i + 1 }));
}

function parseQuestionsFromText(text: string): ParsedQuestion[] {
  // Debug: log a sample of the text for troubleshooting
  if (typeof window !== 'undefined' && (window as any).__debugQuickReply) {
    console.log('[QuickReply] Parsing text:', text.substring(0, 200));
  }

  // Helper: strip leading em-dash / dash that AIs emit after the label separator
  // e.g. "**Label** — body" → body captured as "— body" → we strip to "body"
  const stripLeadingDash = (s: string) => s.replace(/^[—–\-]\s+/, '').trim();

  // ── Format A: "1. **Label**: body?" — label + body on same line ──────────
  {
    const questions: ParsedQuestion[] = [];
    // More permissive regex: allows label to contain more chars, body can be shorter
    const lineRe = /^(\d+)\.\s+\*{0,2}([^:*\n]{1,60})\*{0,2}[:\s]+(.{3,500})$/gm;
    let m: RegExpExecArray | null;
    while ((m = lineRe.exec(text)) !== null) {
      const idx = parseInt(m[1]);
      const label = m[2].trim();
      const body = stripLeadingDash(m[3]);
      questions.push({ index: idx, label, body, options: extractOptions(body) });
    }
    if (questions.length > 0) {
      if (typeof window !== 'undefined' && (window as any).__debugQuickReply) {
        console.log('[QuickReply] Format A matched:', questions);
      }
      return expandAndRenumber(questions);
    }
  }

  // ── Format B: numbered section headers + bullet points underneath ─────────
  // e.g.:  "1. **Application**\n• What is this driving?\n• Temp range?"
  // FIX D: When a bullet ends with ":" it's an intro for sub-options.
  // Collect following non-"?" bullets as chip options for that intro question.
  {
    const questions: ParsedQuestion[] = [];
    const lines = text.split('\n');
    let sectionLabel = '';
    let qIdx = 0;
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      // Section header: "1. **Label**" or "1. Label" (no colon body after)
      const secM = line.match(/^\d+\.\s+\*{0,2}([^*\n:]{2,60})\*{0,2}\s*$/);
      if (secM) { sectionLabel = secM[1].trim(); i++; continue; }

      // Bullet line under a section
      if (sectionLabel) {
        const bulletM = line.match(/^\s*[•\-\*]\s+(.+)$/);
        if (bulletM) {
          const body = stripLeadingDash(bulletM[1]);

          // FIX D: If this bullet ends with ":" or "Do you need:" — collect
          // following non-"?" bullets as sub-options for this question
          if (/:\s*$/.test(body) || /\b(need|want|choose|select|prefer)\s*:\s*$/i.test(body)) {
            const subOptions: string[] = [];
            let j = i + 1;
            while (j < lines.length) {
              const subM = lines[j].match(/^\s*[•\-\*]\s+(.+)$/);
              if (!subM) break;
              const subBody = stripLeadingDash(subM[1]).replace(/[?!,]+$/, '').trim();
              // Sub-bullet that's a question itself (ends with ?) → stop merging
              if (/\?\s*$/.test(subM[1])) break;
              if (subBody.length > 2 && subBody.length < 60) {
                // Strip trailing ", or" and clean up
                const cleaned = subBody.replace(/,?\s*or\s*$/i, '').trim();
                if (cleaned.length > 2) subOptions.push(cleaned);
              }
              j++;
            }
            if (subOptions.length >= 2) {
              qIdx++;
              questions.push({
                index: qIdx, label: sectionLabel,
                body: body.replace(/:\s*$/, '?'),
                options: subOptions.slice(0, 5).map(s => {
                  const t = s.charAt(0).toUpperCase() + s.slice(1);
                  return t.split(/\s+/).length <= 5 ? t : t.split(/\s+/).slice(0, 5).join(' ');
                }),
              });
              i = j; // skip past the sub-bullets we consumed
              continue;
            }
          }

          // Regular bullet — standalone question
          // Skip horizontal rules and non-question content
          if (/^-{2,}$/.test(body.trim()) || body.trim().length < 6) { i++; continue; }
          qIdx++;
          questions.push({ index: qIdx, label: sectionLabel, body, options: extractOptions(body) });
          i++;
          continue;
        }
      }
      i++;
    }
    if (questions.length > 0) return expandAndRenumber(questions);
  }

  // ── Format C: standalone bold header + numbered questions below ───────────
  // e.g.:  "**Power & Performance:**\n1. What is the max current?\n2. What frequency?"
  {
    const questions: ParsedQuestion[] = [];
    const lines = text.split('\n');
    let sectionLabel = '';

    for (const line of lines) {
      // Standalone bold-only line: "**Section Header:**" or "**Section Header**"
      const boldM = line.match(/^\s*\*{2}([^*\n]{2,60})\*{2}:?\s*$/);
      if (boldM) {
        sectionLabel = boldM[1].replace(/:$/, '').trim();
        continue;
      }
      // Numbered question under a bold header (plain "1. question?" — no inline label)
      if (sectionLabel) {
        const numM = line.match(/^\s*(\d+)\.\s+(.+)$/);
        if (numM) {
          const body = stripLeadingDash(numM[2]);
          questions.push({ index: parseInt(numM[1]), label: sectionLabel, body, options: extractOptions(body) });
        }
        // Blank line between sections — reset so next bold header wins
        else if (line.trim() === '' && questions.length > 0) {
          sectionLabel = '';
        }
      }
    }
    if (questions.length > 0) return expandAndRenumber(questions);
  }

  // ── Format D: plain numbered questions "1. question?" — no labels ─────────
  // Fallback: catch any remaining "1. some question?" patterns
  {
    const questions: ParsedQuestion[] = [];
    // More permissive: reduce minimum body length from 10 to 5 chars
    const plainRe = /^(\d+)\.\s+(.{5,500}[?!])\s*$/gm;
    let dm: RegExpExecArray | null;
    while ((dm = plainRe.exec(text)) !== null) {
      const idx = parseInt(dm[1]);
      const body = stripLeadingDash(dm[2]);
      // Derive a short label from the first 4 words of the question
      const words = body.replace(/[?!.]/g, '').split(/\s+/);
      const label = words.slice(0, 4).join(' ');
      questions.push({ index: idx, label, body, options: extractOptions(body) });
    }
    if (questions.length > 0) {
      if (typeof window !== 'undefined' && (window as any).__debugQuickReply) {
        console.log('[QuickReply] Format D matched:', questions);
      }
      return expandAndRenumber(questions);
    }
  }

  // ── Format E: prose questions — bold headings + "Also/For" sentences ───────
  // Catches AI responses that don't use numbered lists, e.g.:
  //   "**Current sensing** — Do you want:\n• opt1\n• opt2?"
  //   "Also, for position — Hall sensors, encoder, or sensorless?"
  {
    const questions: ParsedQuestion[] = [];
    const lines = text.split('\n');
    let i = 0;

    while (i < lines.length) {
      const line = lines[i].trim();

      // Bold standalone heading: "**Topic** — optional body text"
      const boldM = line.match(/^\*{2}([^*\n]{3,60})\*{2}[—\-:\s]*(.*)$/);
      if (boldM) {
        const sectionLabel = boldM[1].replace(/:$/, '').trim();
        // Collect body: rest of heading line + following lines until blank/next heading
        const bodyLines: string[] = [];
        if (boldM[2].trim()) bodyLines.push(boldM[2].trim());
        i++;
        while (i < lines.length && lines[i].trim() && !/^\*{2}/.test(lines[i])) {
          bodyLines.push(lines[i].trim());
          i++;
        }
        const body = bodyLines.join('\n');
        if (body.length > 5 && (body.includes('?') || body.includes('•') || /\bor\b/i.test(body))) {
          questions.push({ index: questions.length + 1, label: sectionLabel, body, options: extractOptions(body) });
        }
        continue;
      }

      // Inline prose question: sentence ending in "?" with "or" / question words
      if (line.endsWith('?') && line.length > 15 &&
          /\b(or|do you|what|which|how|any|is there|will|can you|hall|encoder|sensorless)\b/i.test(line)) {
        const words = line.replace(/[?.,]/g, '').split(/\s+/);
        const label = words.slice(0, 4).join(' ');
        questions.push({ index: questions.length + 1, label, body: line, options: extractOptions(line) });
      }

      i++;
    }

    if (questions.length > 0) return expandAndRenumber(questions);
  }

  // ── Format F: ULTRA-PERMISSIVE CATCH-ALL ───────────────────────────────────
  // Last resort: find ANY numbered pattern "1." followed by some text
  // This catches edge cases where AI uses unusual formatting
  {
    const questions: ParsedQuestion[] = [];
    const lines = text.split('\n');
    let qIdx = 0;

    for (const line of lines) {
      // Match "1." or "1)" followed by any text (very permissive)
      const numMatch = line.match(/^\s*(\d+)[\.)]\s+(.{5,500})\s*$/);
      if (numMatch) {
        qIdx++;
        const body = stripLeadingDash(numMatch[2]);
        // Extract label from first few words
        const words = body.replace(/[?.,]/g, '').split(/\s+/);
        const label = words.slice(0, 3).join(' ');
        questions.push({ index: qIdx, label, body, options: extractOptions(body) });
      }
    }

    if (questions.length >= 2) {
      if (typeof window !== 'undefined' && (window as any).__debugQuickReply) {
        console.log('[QuickReply] Format F (catch-all) matched:', questions);
      }
      return expandAndRenumber(questions);
    }
  }

  if (typeof window !== 'undefined' && (window as any).__debugQuickReply) {
    console.log('[QuickReply] No format matched, returning empty array');
  }
  return [];
}

function extractOptions(body: string): string[] {
  // ── Normalise chip text ────────────────────────────────────────────────────
  const cleanBody = body.replace(/^[—–\-]\s+/, '').trim();

  const normalizeChip = (s: string): string => {
    let t = s.trim()
      .replace(/^(or|and)\s+/i, '')
      .replace(/^(a|an|the|just|only)\s+/i, '')
      .replace(/[?!.]+$/, '')
      .trim();
    return t.length > 0 ? t.charAt(0).toUpperCase() + t.slice(1) : t;
  };

  // Per-part word-count guard — filter individually, never reject entire group
  const chipWords = (s: string) => normalizeChip(s).split(/\s+/).length;
  const isShortChip = (s: string) => chipWords(s) <= 4;

  // ── Bullet list options — "• opt1\n• opt2\n• opt3?" ──────────────────────
  const bulletMatches = Array.from(cleanBody.matchAll(/[•\-\*]\s+\*{0,2}([^\n•\-\*]{3,60})\*{0,2}/g));
  if (bulletMatches.length >= 2) {
    const parts = bulletMatches
      .map(m => normalizeChip(m[1].replace(/\s*\([^)]*\).*$/, '').trim()))
      .filter(s => s.length > 1 && s.length < 40 && isShortChip(s));
    if (parts.length >= 2) return parts.slice(0, 5);
  }

  // ── Domain shortcuts (high-confidence) ─────────────────────────────────────

  const tempKeyword = /\b(temperature|thermal|operating\s+temp|temp\s+range|grade)\b/i.test(cleanBody);
  const tempGrades  = /\b(commercial|industrial|automotive|mil.?spec|military)\b/i.test(cleanBody) &&
                      /(-\d+|°[CF]|ambient|outdoor)/i.test(cleanBody);
  if (tempKeyword || tempGrades) {
    const chips: string[] = [];
    if (/commercial/i.test(cleanBody))         chips.push('Commercial (0-70C)');
    if (/industrial/i.test(cleanBody))         chips.push('Industrial (-40-85C)');
    if (/automotive/i.test(cleanBody))         chips.push('Automotive (-40-105C)');
    if (/mil.?spec|military/i.test(cleanBody)) chips.push('MIL-SPEC (-55-125C)');
    if (chips.length >= 2) return chips;
    return ['Commercial (0-70C)', 'Industrial (-40-85C)', 'Automotive (-40-105C)', 'MIL-SPEC (-55-125C)'];
  }

  if (/\b(forced\s+air|natural\s+convect|conduction.cool|heatsink|heat\s*sink)\b/i.test(cleanBody)) {
    return ['Forced air (fan)', 'Natural convection', 'Conduction-cooled'];
  }

  // ══════════════════════════════════════════════════════════════════════════
  // FIX A: Try main body "A, B, or C" list BEFORE e.g. parens
  // This ensures "CW tone, pulsed, or digitally modulated (e.g., QPSK, QAM)"
  // extracts [CW tone, Pulsed, Digitally modulated] — not [QPSK, QAM]
  // ══════════════════════════════════════════════════════════════════════════
  const bodyClean = cleanBody.replace(/\s*\([^)]*\)/g, '').replace(/\s+/g, ' ').trim();
  const lastClause = bodyClean.split(/[:\u2014\u2013]/).pop()?.trim() ?? bodyClean;

  const tryExtractOrList = (candidate: string): string[] | null => {
    const endOrRe = /\b(\w[\w\s/\-]{0,28})(?:,\s*\w[\w\s/\-]{0,28})*(?:,?\s*or\s+\w[\w\s/\-]{0,28})\s*[?!.]?\s*$/i;
    const m = candidate.match(endOrRe);
    if (!m) return null;
    const raw = m[0].replace(/[?!.]\s*$/, '').trim();
    const parts = raw
      .split(/,\s*(?:or\s+)?|\s+or\s+/)
      .map(s => normalizeChip(s))
      .filter(s => s.length > 1 && s.length < 40 && isShortChip(s));
    return parts.length >= 2 ? parts.slice(0, 5) : null;
  };

  const endResult = tryExtractOrList(lastClause);
  if (endResult) return endResult;

  const firstSentence = lastClause.split('?')[0];
  if (firstSentence && firstSentence !== lastClause) {
    const sentenceResult = tryExtractOrList(firstSentence + '?');
    if (sentenceResult) return sentenceResult;
  }

  // ── e.g./i.e. parentheticals — fallback when main body has no list ────────
  const egParens = Array.from(cleanBody.matchAll(/\(\s*(?:e\.g\.|i\.e\.)[.,]?\s*([^)]{4,120})\)/gi));
  for (const pm of egParens) {
    const inner = pm[1].replace(/^(?:e\.g\.|i\.e\.)[.,]?\s*/i, '');
    const parts = inner
      .split(/[,/]|\s+or\s+/)
      .map(s => normalizeChip(s))
      .filter(s => s.length > 1 && s.length < 32 && isShortChip(s));
    if (parts.length >= 2) return parts.slice(0, 5);
  }

  // ── Plain paren with 3+ comma-separated short options ────────────────────
  const plainParens = Array.from(cleanBody.matchAll(/\(([^)]{6,120})\)/gi));
  for (const pm of plainParens) {
    const inner = pm[1].trim();
    if (/\be\.g\b|\bi\.e\b/i.test(inner)) continue;
    const parts = inner
      .split(/[,/]|\s+or\s+/)
      .map(s => normalizeChip(s))
      .filter(s => s.length > 1 && s.length < 32 && isShortChip(s));
    if (parts.length >= 2) return parts.slice(0, 5);
  }

  // ══════════════════════════════════════════════════════════════════════════
  // FIX C: Binary "X or Y" mid-sentence — catches "isolated or non-isolated",
  // "on-board PLL or external reference", "FCC/CE compliance or controlled env"
  // ══════════════════════════════════════════════════════════════════════════
  {
    // Pattern: "need/want/use/prefer X or Y" where X,Y are 1-4 words
    const binaryM = bodyClean.match(/\b(?:need|want|use|prefer|require|provide|have)?\s*(\w[\w\s/\-]{0,30}?)\s+or\s+(?:is\s+(?:this|it)\s+(?:a\s+|an\s+|for\s+(?:a\s+)?)?)?(\w[\w\s/\-]{0,30}?)(?:\s+(?:for|to|in|on|at|from|with|that|which|buck|boost|converter|design|board|module|environment|reference|provided)\b|\?|$)/i);
    if (binaryM) {
      const a = normalizeChip(binaryM[1]);
      const b = normalizeChip(binaryM[2]);
      if (a.length > 1 && b.length > 1 && isShortChip(a) && isShortChip(b) && a !== b) {
        return [a, b];
      }
    }
    // Simpler fallback: "X or Y?" at or near end of sentence
    const simpleOr = bodyClean.match(/\b(\w[\w\s/\-]{0,20}?)\s+or\s+(\w[\w\s/\-]{0,20}?)\s*\?/i);
    if (simpleOr) {
      const a = normalizeChip(simpleOr[1]);
      const b = normalizeChip(simpleOr[2]);
      if (a.length > 1 && b.length > 1 && isShortChip(a) && isShortChip(b) && a !== b) {
        return [a, b];
      }
    }
  }

  // ── "whether X or Y" ───────────────────────────────────────────────────────
  const whetherM = cleanBody.match(/whether\s+(?:you\s+(?:can|need|should|want|prefer)\s+)?(.{3,35}?)\s+or\s+(?:need\s+|use\s+)?(.{3,35}?)(?:\?|$|\s*\()/i);
  if (whetherM) {
    const a = normalizeChip(whetherM[1]);
    const b = normalizeChip(whetherM[2]);
    if (isShortChip(a) && isShortChip(b) && a.length > 2 && b.length > 2) return [a, b];
  }

  // ══════════════════════════════════════════════════════════════════════════
  // FIX B: Yes/No — but NEVER for What/Which/How/Where/When questions
  // ══════════════════════════════════════════════════════════════════════════
  const isWh = /^\s*(what|which|how|where|when|describe|list|specify|name)\b/i.test(cleanBody);
  if (!isWh && /\?/.test(cleanBody) && !/\bor\b/i.test(cleanBody) &&
      /\b(do you|is there|are there|will|should|does|can you|have you|is it|would you)\b/i.test(cleanBody)) {
    return ['Yes', 'No'];
  }

  // ══════════════════════════════════════════════════════════════════════════
  // FIX E: Domain shortcuts for common hardware quantities (last resort)
  // Guarantees every question gets chips — never return empty.
  // ══════════════════════════════════════════════════════════════════════════
  if (/\b(bandwidth|modulation\s+bw|channel\s+bw)\b/i.test(cleanBody)) {
    return ['Narrowband <1 MHz', '1-50 MHz', 'Wideband 50-500 MHz', '>500 MHz'];
  }
  if (/\b(data\s+rate|bit\s*rate|throughput|baud)\b/i.test(cleanBody)) {
    return ['<1 Mbps', '1-100 Mbps', '100 Mbps - 1 Gbps', '>1 Gbps'];
  }
  if (/\b(frequency|freq)\b/i.test(cleanBody) && /\b(operating|center|carrier|lo)\b/i.test(cleanBody)) {
    return ['<1 GHz', '1-6 GHz', '6-18 GHz', '>18 GHz'];
  }
  if (/\b(input\s+voltage|supply\s+voltage|bus\s+voltage|main\s+voltage)\b/i.test(cleanBody)) {
    return ['5V', '12V', '24V', '48V', 'AC mains'];
  }
  if (/\b(max\s+current|output\s+current|load\s+current)\b/i.test(cleanBody)) {
    return ['<1A', '1-10A', '10-50A', '>50A'];
  }
  if (/\b(output\s+power|transmit\s+power|pa\s+power)\b/i.test(cleanBody)) {
    return ['<1W', '1-10W', '10-50W', '>50W'];
  }
  if (/\b(efficiency|pae|drain\s+efficiency)\b/i.test(cleanBody)) {
    return ['<20%', '20-40%', '40-60%', '>60%'];
  }
  if (/\b(interface|protocol|bus)\b/i.test(cleanBody) && /\b(data|baseband|digital|fpga)\b/i.test(cleanBody)) {
    return ['SPI', 'I2C', 'UART', 'JESD204B', 'Parallel'];
  }
  if (/\b(compliance|certification|regulatory|fcc|ce\b)/i.test(cleanBody)) {
    return ['FCC/CE required', 'MIL-STD', 'Lab/R&D only'];
  }
  if (/\b(form.?factor|enclosure|size|rack|handheld)\b/i.test(cleanBody)) {
    return ['1U rack-mount', 'Desktop', 'Handheld', 'PCB-only'];
  }
  if (/\b(gain|amplif)/i.test(cleanBody)) {
    return ['Low (<10 dB)', 'Medium (10-30 dB)', 'High (>30 dB)'];
  }
  if (/\b(isolated|non.?isolated)\b/i.test(cleanBody)) {
    return ['Isolated', 'Non-isolated'];
  }
  if (/\b(clock|reference|oscillator|pll|synthesizer)\b/i.test(cleanBody)) {
    return ['On-board PLL', 'External reference', 'Crystal oscillator'];
  }

  // Ultimate fallback: if question ends with "?", return Yes/No
  if (/\?\s*$/.test(cleanBody)) return ['Yes', 'No'];

  return [];
}

// Single question card inside the popup
function QuestionCard({
  q, color, selected, onSelect,
}: {
  q: ParsedQuestion;
  color: string;
  selected: string;
  onSelect: (val: string) => void;
}) {
  const [otherOpen, setOtherOpen] = useState(false);
  const [otherText, setOtherText] = useState('');

  const isOtherSelected = selected.startsWith('__other__:');
  const otherValue = isOtherSelected ? selected.slice(10) : otherText;

  const toggleOther = () => {
    if (otherOpen) {
      setOtherOpen(false);
      if (isOtherSelected) onSelect('');
    } else {
      setOtherOpen(true);
      onSelect('');
    }
  };

  const commitOther = (val: string) => {
    setOtherText(val);
    if (val.trim()) onSelect('__other__:' + val.trim());
    else onSelect('');
  };

  return (
    <div style={{
      background: 'var(--panel)', border: `1px solid ${color}22`,
      borderRadius: 8, padding: '12px 14px',
    }}>
      <div style={{ fontSize: 11, color, fontFamily: "'DM Mono',monospace", letterSpacing: '0.06em', marginBottom: 4 }}>
        Q{q.index}
      </div>
      <div style={{ fontSize: 12.5, color: 'var(--text)', marginBottom: 10, lineHeight: 1.5 }}>
        {q.body}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {q.options.map(opt => {
          const isSel = selected === opt;
          return (
            <button key={opt}
              onClick={() => { setOtherOpen(false); onSelect(isSel ? '' : opt); }}
              style={{
                padding: '5px 13px', borderRadius: 20, fontSize: 12,
                fontFamily: "'DM Mono',monospace", cursor: 'pointer',
                border: `1px solid ${isSel ? color : `${color}40`}`,
                background: isSel ? color : `${color}0a`,
                color: isSel ? '#070b14' : 'var(--text2)',
                fontWeight: isSel ? 700 : 400, transition: 'all 0.12s',
              }}>
              {opt}
            </button>
          );
        })}
        {/* Custom answer — small pencil icon, not "Other..." text */}
        <button
          onClick={toggleOther}
          title="Type a custom answer"
          style={{
            padding: '5px 10px', borderRadius: 20, fontSize: 12,
            fontFamily: "'DM Mono',monospace", cursor: 'pointer',
            border: `1px solid ${(otherOpen || isOtherSelected) ? color : `${color}25`}`,
            background: (otherOpen || isOtherSelected) ? `${color}18` : 'transparent',
            color: (otherOpen || isOtherSelected) ? color : 'var(--text4)',
            fontWeight: 400, transition: 'all 0.12s',
          }}>
          {isOtherSelected ? otherValue : '+'}
        </button>
      </div>
      {/* Inline text input when Other is open */}
      {otherOpen && (
        <div style={{ marginTop: 8, display: 'flex', gap: 6 }}>
          <input
            autoFocus
            value={otherText}
            onChange={e => commitOther(e.target.value)}
            onKeyDown={e => { if (e.key === 'Escape') toggleOther(); }}
            placeholder="Type your answer…"
            style={{
              flex: 1, background: 'var(--panel2)', border: `1px solid ${color}55`,
              borderRadius: 5, padding: '6px 10px', fontSize: 12,
              color: 'var(--text)', fontFamily: "'DM Mono',monospace",
              outline: 'none',
            }}
          />
          {otherText.trim() && (
            <button
              onClick={() => setOtherOpen(false)}
              style={{
                padding: '6px 12px', borderRadius: 5, background: color,
                color: '#070b14', border: 'none', fontSize: 11,
                fontFamily: "'DM Mono',monospace", fontWeight: 700, cursor: 'pointer',
              }}>
              ✓
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ── SchemaQuestionCard ──────────────────────────────────────────────────────
// Question card using pre-defined schema (QuestionCardType from questionSchema)
function SchemaQuestionCard({
  question, color, selected, onSelect,
}: {
  question: QuestionCardType;
  color: string;
  selected: string;
  onSelect: (val: string) => void;
}) {
  const [otherOpen, setOtherOpen] = useState(false);
  const [otherText, setOtherText] = useState('');

  const isOtherSelected = selected.startsWith('__other__:');
  const otherValue = isOtherSelected ? selected.slice(10) : otherText;

  const toggleOther = () => {
    if (otherOpen) {
      setOtherOpen(false);
      if (isOtherSelected) onSelect('');
    } else {
      setOtherOpen(true);
      onSelect('');
    }
  };

  const commitOther = (val: string) => {
    setOtherText(val);
    if (val.trim()) onSelect('__other__:' + val.trim());
    else onSelect('');
  };

  return (
    <div style={{
      background: 'var(--panel)', border: `1px solid ${color}22`,
      borderRadius: 8, padding: '12px 14px',
    }}>
      <div style={{ fontSize: 11, color, fontFamily: "'DM Mono',monospace", letterSpacing: '0.06em', marginBottom: 4 }}>
        {question.label.toUpperCase()}
      </div>
      <div style={{ fontSize: 12.5, color: 'var(--text)', marginBottom: 10, lineHeight: 1.5 }}>
        {truncateQuestion(question.question, 80)}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {question.options.map(opt => {
          const isSel = selected === opt;
          return (
            <button key={opt}
              onClick={() => { setOtherOpen(false); onSelect(isSel ? '' : opt); }}
              style={{
                padding: '5px 13px', borderRadius: 20, fontSize: 12,
                fontFamily: "'DM Mono',monospace", cursor: 'pointer',
                border: `1px solid ${isSel ? color : `${color}40`}`,
                background: isSel ? color : `${color}0a`,
                color: isSel ? '#070b14' : 'var(--text2)',
                fontWeight: isSel ? 700 : 400, transition: 'all 0.12s',
              }}>
              {opt}
            </button>
          );
        })}
        {/* Other... button if allowOther is true */}
        {question.allowOther !== false && (
          <button
            onClick={toggleOther}
            style={{
              padding: '5px 13px', borderRadius: 20, fontSize: 12,
              fontFamily: "'DM Mono',monospace", cursor: 'pointer',
              border: `1px solid ${(otherOpen || isOtherSelected) ? color : `${color}40`}`,
              background: (otherOpen || isOtherSelected) ? `${color}18` : 'transparent',
              color: (otherOpen || isOtherSelected) ? color : 'var(--text3)',
              fontWeight: 400, transition: 'all 0.12s',
            }}>
            {isOtherSelected ? `✎ ${otherValue}` : 'Other…'}
          </button>
        )}
      </div>
      {/* Inline text input when Other is open */}
      {otherOpen && (
        <div style={{ marginTop: 8, display: 'flex', gap: 6 }}>
          <input
            autoFocus
            value={otherText}
            onChange={e => commitOther(e.target.value)}
            onKeyDown={e => { if (e.key === 'Escape') toggleOther(); }}
            placeholder="Type your answer…"
            style={{
              flex: 1, background: 'var(--panel2)', border: `1px solid ${color}55`,
              borderRadius: 5, padding: '6px 10px', fontSize: 12,
              color: 'var(--text)', fontFamily: "'DM Mono',monospace",
              outline: 'none',
            }}
          />
          {otherText.trim() && (
            <button
              onClick={() => setOtherOpen(false)}
              style={{
                padding: '6px 12px', borderRadius: 5, background: color,
                color: '#070b14', border: 'none', fontSize: 11,
                fontFamily: "'DM Mono',monospace", fontWeight: 700, cursor: 'pointer',
              }}>
              ✓
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function QuickReplyPanel({
  aiMessage, designDescription, color, onSend, disabled,
}: {
  aiMessage: string;
  designDescription: string;
  color: string;
  onSend: (msg: string) => void;
  disabled: boolean;
}) {
  // Parse AI's questions to extract labels and options dynamically
  const questions = parseQuestionsFromAI(aiMessage);
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [dismissed, setDismissed] = useState(false);

  // Check if we should show questions at all
  if (questions.length === 0 || dismissed || !shouldShowQuestions(aiMessage, designDescription)) {
    return null;
  }

  // Reset when AI message changes (simple heuristic: check first 100 chars)
  const questionKey = questions.map(q => q.id).join(',') + aiMessage.slice(0, 100);
  const prevKey = useRef('');
  useEffect(() => {
    if (prevKey.current !== questionKey) {
      prevKey.current = questionKey;
      setSelected({});
      setDismissed(false);
    }
  }, [questionKey]);

  const allAnswered = questions.every(q => selected[q.id]);
  const selectedCount = questions.filter(q => selected[q.id]).length;

  // ── Optional "Any Specific Requirements?" free-text card ─────────────────
  const [specificReqs, setSpecificReqs] = useState('');

  // Reset when question set changes
  useEffect(() => {
    setSpecificReqs('');
  }, [questionKey]);

  const buildReply = () => {
    const lines = questions
      .filter(q => selected[q.id])
      .map(q => {
        const val = selected[q.id];
        const display = val.startsWith('__other__:') ? val.slice(10) : val;
        return `${q.label}: ${display}`;
      });
    if (specificReqs.trim()) {
      lines.push(`Additional requirements: ${specificReqs.trim()}`);
    }
    return lines.join('\n');
  };

  const canSend = selectedCount > 0 || specificReqs.trim().length > 0;

  return (
    /* Sticky popup anchored to bottom of chat scroll area */
    <div style={{
      position: 'sticky', bottom: 12, zIndex: 20,
      marginBottom: 8,
      background: 'var(--panel2)',
      border: `1px solid ${color}44`,
      borderRadius: 12,
      boxShadow: `0 -4px 32px rgba(0,0,0,0.55), 0 0 0 1px ${color}18`,
      overflow: 'hidden',
    }}>
      {/* Header bar */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 14px',
        background: `linear-gradient(90deg, ${color}18, transparent)`,
        borderBottom: `1px solid ${color}22`,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 14 }}>&#9889;</span>
          <span style={{ fontFamily: "'DM Mono',monospace", fontSize: 11, color, letterSpacing: '0.1em' }}>
            QUICK ANSWERS — {selectedCount}/{questions.length} selected
          </span>
        </div>
        <button
          onClick={() => setDismissed(true)}
          style={{
            background: 'none', border: 'none', color: 'var(--text4)',
            fontSize: 16, cursor: 'pointer', lineHeight: 1, padding: '2px 6px',
          }}
          title="Dismiss">
          ×
        </button>
      </div>

      {/* Question cards */}
      <div style={{ padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 500, overflowY: 'auto' }}>
        {questions.map(q => (
          <SchemaQuestionCard
            key={q.id}
            question={q}
            color={color}
            selected={selected[q.id] ?? ''}
            onSelect={val => setSelected(prev => ({ ...prev, [q.id]: val }))}
          />
        ))}

        {/* ── Optional free-text card ── */}
        <div style={{
          background: 'var(--panel)', border: `1px dashed ${color}33`,
          borderRadius: 8, padding: '12px 14px',
        }}>
          <div style={{ fontSize: 11, color: color, fontFamily: "'DM Mono',monospace", letterSpacing: '0.06em', marginBottom: 4 }}>
            SPECIFIC REQUIREMENTS
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 10, lineHeight: 1.5 }}>
            Any specific requirements? <span style={{ color: 'var(--text4)', fontSize: 11 }}>(optional)</span>
          </div>
          <textarea
            value={specificReqs}
            onChange={e => setSpecificReqs(e.target.value)}
            placeholder="e.g. Must pass MIL-STD-810G, operating altitude >10,000m, conformal coating required…"
            rows={2}
            style={{
              width: '100%', boxSizing: 'border-box',
              background: 'var(--panel2)', border: `1px solid ${specificReqs.trim() ? color + '55' : color + '22'}`,
              borderRadius: 5, padding: '7px 10px', fontSize: 12,
              color: 'var(--text)', fontFamily: "'DM Mono',monospace",
              outline: 'none', resize: 'vertical', lineHeight: 1.5,
              transition: 'border-color 0.15s',
            }}
          />
        </div>
      </div>

      {/* Footer / send */}
      <div style={{
        padding: '10px 12px',
        borderTop: `1px solid ${color}18`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10,
      }}>
        <span style={{ fontSize: 11, color: 'var(--text4)', fontFamily: "'DM Mono',monospace" }}>
          {allAnswered ? 'All questions answered' : `${questions.length - selectedCount} remaining`}
        </span>
        <button
          onClick={() => { onSend(buildReply()); setDismissed(true); }}
          disabled={disabled || !canSend}
          style={{
            padding: '8px 20px', borderRadius: 6,
            background: canSend ? color : 'var(--panel3)',
            color: canSend ? '#070b14' : 'var(--text4)',
            border: 'none', fontSize: 12,
            fontFamily: "'DM Mono',monospace", fontWeight: 700,
            cursor: disabled || !canSend ? 'default' : 'pointer',
            transition: 'all 0.15s',
          }}>
          Send {selectedCount > 0 ? `${selectedCount} answer${selectedCount > 1 ? 's' : ''}` : 'answers'} →
        </button>
      </div>
    </div>
  );
}

// ---- Welcome card ----
function WelcomeCard({ color, onSuggestion }: { color: string; onSuggestion: (s: string) => void }) {
  const examples = [
    { text: '3-phase BLDC motor controller, 10kW, 48V bus', icon: '\u26A1', tag: 'Motor' },
    { text: 'RF amplifier, 40dBm output, 2.4GHz', icon: '\uD83D\uDCE1', tag: 'RF' },
    { text: '48V to 3.3V/5V/12V power supply, 200W total', icon: '\uD83D\uDD0B', tag: 'Power' },
  ];
  return (
    <div style={{ background: `linear-gradient(135deg, var(--panel2), ${color}08)`, border: `1px solid ${color}22`, borderRadius: 12, padding: '24px 24px 20px', marginBottom: 20, position: 'relative', overflow: 'hidden' }}>
      {/* Decorative glow */}
      <div style={{ position: 'absolute', top: -40, right: -40, width: 120, height: 120, borderRadius: '50%', background: `radial-gradient(circle, ${color}12, transparent)`, pointerEvents: 'none' }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <div style={{ width: 32, height: 32, borderRadius: 8, background: `${color}18`, border: `1px solid ${color}33`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16 }}>
          <span style={{ filter: `drop-shadow(0 0 4px ${color})` }}>&#9889;</span>
        </div>
        <div>
          <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)', fontFamily: "'Syne',sans-serif" }}>
            Design Assistant
          </div>
          <div style={{ fontSize: 10, color: 'var(--text4)', letterSpacing: '0.08em', fontFamily: "'DM Mono',monospace" }}>HARDWARE PIPELINE</div>
        </div>
      </div>
      <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.7, marginBottom: 16 }}>
        Describe your hardware design &mdash; I'll generate a complete{' '}
        <strong style={{ color }}>block diagram</strong>,{' '}
        <strong style={{ color }}>requirements</strong>, and{' '}
        <strong style={{ color }}>BOM</strong>{' '}
        with real component selection in seconds.
      </div>
      <div style={{ fontSize: 11, color: 'var(--text4)', letterSpacing: '0.06em', fontFamily: "'DM Mono',monospace", marginBottom: 8 }}>TRY AN EXAMPLE</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
        {examples.map((ex, i) => (
          <button key={i} onClick={() => onSuggestion(ex.text)} style={{
            textAlign: 'left', background: 'var(--panel)', border: `1px solid ${color}18`,
            borderRadius: 8, padding: '10px 14px', fontSize: 12, color: 'var(--text2)',
            fontFamily: "'DM Mono',monospace", cursor: 'pointer', transition: 'all 0.2s',
            display: 'flex', alignItems: 'center', gap: 10,
          }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = `${color}66`; e.currentTarget.style.background = `${color}0a`; e.currentTarget.style.transform = 'translateX(4px)'; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = `${color}18`; e.currentTarget.style.background = 'var(--panel)'; e.currentTarget.style.transform = 'translateX(0)'; }}>
            <span style={{ fontSize: 16, width: 28, textAlign: 'center', flexShrink: 0 }}>{ex.icon}</span>
            <span style={{ flex: 1 }}>{ex.text}</span>
            <span style={{ fontSize: 9, color: `${color}88`, background: `${color}12`, padding: '2px 6px', borderRadius: 3, fontWeight: 600, letterSpacing: '0.05em' }}>{ex.tag}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/** Strip backend-generated boilerplate that references UI elements that don't exist */
function cleanAiText(text: string): string {
  return text
    .replace(/Click\s+["'\u2018\u2019\u201c\u201d]?Run\s+(?:Full\s+)?Pipeline["'\u2018\u2019\u201c\u201d]?\s+(?:button\s+)?to\s+generate[^\n]*/gi, '')
    .replace(/Click\s+the\s+["'\u2018\u2019\u201c\u201d]?Run\s+(?:Full\s+)?Pipeline["'\u2018\u2019\u201c\u201d]?\s+button[^.]*\./gi, '')
    .replace(/press\s+["'\u2018\u2019\u201c\u201d]?Run\s+(?:Full\s+)?Pipeline["'\u2018\u2019\u201c\u201d][^.]*\./gi, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

// ── Clarification-flow types & constants ─────────────────────────────────────
interface ClarificationQuestion { id: string; question: string; why: string; options: string[]; }
interface ClarificationData {
  intro: string;
  questions: ClarificationQuestion[];
  /**
   * Optional map of Stage-1 specs the LLM extracted from the user's opening
   * message. Rendered as captured-value chips above the question list so the
   * user sees their free-text inputs were recognised (and aren't re-asked).
   */
  prefilled?: {
    application?: string;
    frequency_range?: string;
    instantaneous_bandwidth?: string;
    sensitivity?: string;
    max_input?: string;
  };
}

/** Human-readable labels for each prefilled key (card UI). */
const PREFILLED_LABELS: Record<string, string> = {
  application: 'Application',
  frequency_range: 'Frequency',
  instantaneous_bandwidth: 'Bandwidth',
  sensitivity: 'Sensitivity',
  max_input: 'Max input',
};
const Q_COLORS_CLARIFY = ['var(--teal)', 'var(--blue)', '#f59e0b', '#8b5cf6', 'var(--teal)'];

/**
 * Heuristic: does this AI response look like another round of elicitation
 * (i.e. more questions to answer, not a summary / cascade / approval card)?
 * Triggers a follow-up /clarify call so we can render structured cards.
 *
 * Matches any of:
 *   • "**q1:** …" / "q1)" / "Q1." style prefixed questions
 *   • Two or more numbered "1. …?" lines
 *   • Pipe-separated option lists on their own line ("Opt A | Opt B | Opt C")
 * Excludes responses that clearly contain BOM tables, cascade analysis,
 * or explicit approval prompts — those aren't follow-up rounds.
 */
function looksLikeFollowUpElicitation(text: string): boolean {
  if (!text || text.length < 20) return false;
  // Negative guards — summary / spec output, not a question round
  if (/\b(BOM|Bill of Materials|Cascade Analysis|Link Budget)\b/i.test(text)) return false;
  if (/\bPlease (confirm|approve)\b/i.test(text) && /requirements?/i.test(text)) return false;
  // Positive signals — any ONE is enough, they're already strong indicators
  // 0. Short-prose "Please answer the N questions below" — emitted whenever
  //    the backend called show_clarification_cards successfully. This is the
  //    CURRENT format used by requirements_agent; it's the most reliable
  //    text signal we have because we emit it deterministically.
  if (/Please answer the \d+ (question|questions) below/i.test(text)) return true;
  // q1./Q1. / q1) / q1: / q_architecture / q_sigtype style prefixed questions
  if (/(^|\n)\s*\*{0,2}[qQ](?:\d+|_\w+)\s*[:.)]/.test(text)) return true;
  // ≥ 2 numbered "1. …?" lines
  const numberedQLines = (text.match(/(^|\n)\s*\d+\.\s+[^\n]{4,}\?/g) || []).length;
  if (numberedQLines >= 2) return true;
  // Pipe-separated options on their own line (e.g. "Opt A | Opt B | Opt C")
  const pipeLines = text.split('\n').filter(l => (l.match(/\s\|\s/g) || []).length >= 2).length;
  if (pipeLines >= 1 && /\?/.test(text)) return true;
  // Fallback: any line with "? " ending on that line, paired with at least
  // one pipe-option line somewhere else in the response
  if (/\?/.test(text) && pipeLines >= 1) return true;
  return false;
}

/**
 * Local parser — converts the AI's free-text elicitation response into
 * ClarificationData so we can render cards WITHOUT depending on the /clarify
 * endpoint. This is the guaranteed-to-work fallback when the backend is down
 * or the Pydantic schema drifts. Recognises blocks shaped like:
 *
 *   q1. <question text>
 *   (<optional "why" / rationale in parens>)
 *   Opt A | Opt B | Opt C
 *
 * Numbered "1. …?" and bare "Q1:" forms are also handled. Returns null if
 * we can't find at least one well-formed question block.
 */
// ─────────────────────────────────────────────────────────────────────────
// v19 — Client-side deterministic card deck (rescue "r3").
//
// Fires when the backend response has no cards, local-parse finds nothing,
// AND the /clarify REST endpoint fails (502, timeout, or returns q=0). At
// that point we stop asking the backend and hand-synthesise a topic-aware
// card set directly in the browser — identical behaviour to the v18 backend
// fallback, but zero network / zero LLM / zero uvicorn dependency.
//
// Bank covers the eight Tier-1 RF performance specs that appear in every
// receiver design. We scan the full conversation transcript for keyword
// matches on each topic and skip anything the user already answered. If
// everything's been touched, we emit a single "proceed to generate?" card
// so the user can always reach Generate Documents.
// ─────────────────────────────────────────────────────────────────────────
// v20 — per-scope fallback card banks. When the project's Stage-0 design
// scope is known we restrict the rescue deck to questions that are actually
// meaningful for that scope — no LO phase-noise for a pure RF front-end, no
// IIP3 for a DSP-only project, etc.  Falls back to the full bank when scope
// is undefined (projects predating v20).
type FallbackCard = { id: string; keys: string[]; q: string; why: string; opts: string[] };
const AUTO_FALLBACK = 'Auto (cascade-derived)';

const FALLBACK_BANK: Record<string, FallbackCard> = {
  total_gain: { id: 'total_gain', keys: ['total gain', 'system gain', 'gain (db)', 'gain in db'],
    q: 'Total system gain (dB)?',
    why: 'Sets cascade NF budget and saturation management.',
    opts: ['20 dB', '30 dB', '40 dB', '50 dB', '60 dB', AUTO_FALLBACK] },
  noise_figure: { id: 'noise_figure', keys: ['noise figure', 'nf <', 'nf (db)', 'nf in db'],
    q: 'Target system noise figure (dB)?',
    why: 'Drives LNA selection and sensitivity floor via Friis.',
    opts: ['< 2 dB', '2-3 dB', '3-5 dB', '5-8 dB', AUTO_FALLBACK] },
  iip3: { id: 'iip3', keys: ['iip3', 'input ip3', 'intercept point'],
    q: 'Input IP3 requirement (dBm)?',
    why: 'Linearity budget for mixer and amplifiers.',
    opts: ['-10 dBm', '0 dBm', '+10 dBm', '+20 dBm', AUTO_FALLBACK] },
  p1db: { id: 'p1db', keys: ['p1db', 'compression point', '1 db comp'],
    q: 'Input P1dB (dBm)?',
    why: 'Large-signal handling / compression threshold.',
    opts: ['-20 dBm', '-10 dBm', '0 dBm', '+10 dBm', AUTO_FALLBACK] },
  image_rejection: { id: 'image_rejection', keys: ['image rejection', 'image reject'],
    q: 'Image rejection requirement (dB)?',
    why: 'Spurious-image suppression before downconversion.',
    opts: ['> 30 dB', '> 50 dB', '> 70 dB', AUTO_FALLBACK] },
  phase_noise: { id: 'phase_noise', keys: ['phase noise', 'dbc/hz', 'lo phase'],
    q: 'LO phase noise at 10 kHz offset (dBc/Hz)?',
    why: 'Drives LO synthesiser (TCXO vs PLL+VCO vs DDS).',
    opts: ['-90 dBc/Hz', '-100 dBc/Hz', '-110 dBc/Hz', '-120 dBc/Hz', AUTO_FALLBACK] },
  if_frequency: { id: 'if_frequency', keys: ['if frequency', ' if freq', 'intermediate freq'],
    q: 'Intermediate frequency (IF) target?',
    why: 'Fixes mixer LO offset and IF-filter selection.',
    opts: ['70 MHz', '140 MHz', '455 MHz', '1 GHz', AUTO_FALLBACK] },
  adc_enob: { id: 'adc_enob', keys: ['enob', 'adc resolution', 'bits resolution'],
    q: 'ADC ENOB at Nyquist (bits)?',
    why: 'Drives digital dynamic range / SFDR and bit-true DSP headroom.',
    opts: ['10 bits', '12 bits', '14 bits', '16 bits', AUTO_FALLBACK] },
  sample_rate: { id: 'sample_rate', keys: ['sample rate', 'msps', 'gsps', 'sampling rate'],
    q: 'ADC sample rate target?',
    why: 'Determines Nyquist zone and anti-alias filter difficulty.',
    opts: ['100 MSPS', '500 MSPS', '1 GSPS', '3 GSPS', AUTO_FALLBACK] },
  fpga_family: { id: 'fpga_family', keys: ['fpga', 'zynq', 'artix', 'kintex', 'virtex', 'cyclone'],
    q: 'Target FPGA / SoC family?',
    why: 'Sets DSP slice budget, I/O standards, and tool-flow.',
    opts: ['Xilinx Zynq 7000', 'Xilinx Ultrascale+', 'Intel Cyclone V', 'Lattice ECP5', AUTO_FALLBACK] },
  power_budget: { id: 'power_budget', keys: ['power consumption', 'power budget', 'dc power', 'total power'],
    q: 'Power consumption budget (W)?',
    why: 'Sets amplifier biasing and DC-DC topology.',
    opts: ['< 5 W', '5-15 W', '15-30 W', '> 30 W', AUTO_FALLBACK] },
  supply_voltage: { id: 'supply_voltage', keys: ['supply voltage', 'rail', 'v rail', ' vdd ', ' vcc '],
    q: 'Primary supply voltage rail?',
    why: 'Drives regulator and active-device choice.',
    opts: ['+5 V', '+12 V', '+15 V', '+28 V', 'Multi-rail', AUTO_FALLBACK] },
};

/**
 * v20 — which bank cards apply to each scope, in priority order.
 * - 'full'          : everything (same as the pre-v20 bank).
 * - 'front-end'     : LNA/filter/mixer-input-only. No LO, no ADC.
 * - 'downconversion': mixer + LO + IF. Phase noise, image rejection, IF freq lead.
 * - 'dsp'           : ADC, DSP, FPGA. No RF performance specs.
 */
const SCOPE_CARD_ORDER: Record<string, string[]> = {
  'full':           ['total_gain', 'noise_figure', 'iip3', 'p1db', 'image_rejection', 'phase_noise', 'power_budget', 'supply_voltage'],
  'front-end':      ['total_gain', 'noise_figure', 'iip3', 'p1db', 'image_rejection', 'supply_voltage'],
  'downconversion': ['if_frequency', 'phase_noise', 'image_rejection', 'iip3', 'power_budget', 'supply_voltage'],
  'dsp':            ['sample_rate', 'adc_enob', 'fpga_family', 'power_budget', 'supply_voltage'],
};

function buildLocalFallbackCards(
  history: { role: string; text: string }[],
  scope?: DesignScope | null,
): ClarificationData {
  const hist = history.map(m => (m.text || '').toLowerCase()).join(' ');
  const seen = (keys: string[]) => keys.some(k => hist.includes(k));
  const order = SCOPE_CARD_ORDER[scope || 'full'] || SCOPE_CARD_ORDER['full'];
  const bank: FallbackCard[] = order
    .map(id => FALLBACK_BANK[id])
    .filter((c): c is FallbackCard => !!c);

  const remaining = bank.filter(b => !seen(b.keys));

  if (remaining.length === 0) {
    // Every core topic has been covered — offer the finalize escape hatch.
    const scopeLbl = scope ? ` (${SCOPE_LABELS[scope]})` : '';
    return {
      intro: `All core specs for this design${scopeLbl} have been covered. Ready to generate the design documents?`,
      questions: [{
        id: 'finalize_confirm',
        question: 'Proceed with the specs captured so far?',
        why: 'Any remaining values will be filled from the cascade analysis and standard RF best-practices.',
        options: [
          'Yes - generate documents now',
          'Wait - I want to add one more spec',
        ],
      }],
    };
  }

  const scopeIntroLabel = scope ? SCOPE_LABELS[scope] : 'Core RF performance targets';
  return {
    intro: `${scopeIntroLabel} — pick a value, or "Auto" for cascade-derived defaults.`,
    questions: remaining.slice(0, 5).map(b => ({
      id: b.id, question: b.q, why: b.why, options: b.opts,
    })),
  };
}

// ─────────────────────────────────────────────────────────────────────────
// v20.1 — Scope-aware filter for any ClarificationData the backend (or the
// local parser) hands us.  The backend LLM doesn't know about the Stage-0
// design scope, so its follow-up card decks frequently include questions
// that are irrelevant to the user's chosen scope (e.g. asking ADC ENOB /
// data output format / FPGA family for an RF-Front-End-only design). This
// filter drops those cards in the browser before they render.
//
// Keyword-based so it covers both structured JSON cards and prose-parsed
// cards equally. If a question's text, id, or why matches any "exclude"
// keyword for the active scope, it's dropped. If every question is dropped
// we return null so the caller can fall back to the rescue bank.
// ─────────────────────────────────────────────────────────────────────────
const SCOPE_EXCLUDE_KEYWORDS: Record<string, string[]> = {
  // RF Front-End Only — drop downstream / DSP topics.
  'front-end': [
    // DSP / digital
    'adc resolution', 'adc enob', 'enob', 'sample rate', 'sampling rate',
    'msps', 'gsps', 'data output format', 'i/q baseband', 'iq baseband',
    'digital output', 'ddc', 'digital down-conversion', 'digital downconversion',
    'fpga', 'zynq', 'xilinx', 'lattice', 'cyclone', 'firmware',
    // LO / mixer
    'lo phase noise', 'local oscillator', 'tuning plan', 'tuning step',
    'mixer topology', 'if bandwidth', 'if frequency', 'intermediate frequency',
  ],
  // Downconversion / IF Stage — drop DSP and drop pure-RF-front-end-only.
  'downconversion': [
    'adc resolution', 'adc enob', 'enob', 'sample rate', 'sampling rate',
    'msps', 'gsps', 'data output format', 'i/q baseband', 'iq baseband',
    'digital output', 'ddc', 'fpga', 'zynq', 'xilinx', 'lattice', 'cyclone',
    'firmware',
  ],
  // DSP / Baseband Only — drop RF-performance specs.
  'dsp': [
    'noise figure', 'nf target', 'lna gain', 'lna noise', 'iip3', 'input ip3',
    'p1db', 'image rejection', 'return loss', 'vswr',
    'rf gain', 'total rf gain', 'preselector',
    'antenna gain', 'g/t ', 'receiver sensitivity',
    'lo phase noise', 'local oscillator',
  ],
  // Full Receiver — allow everything.
  'full': [],
};

function filterCardsByScope(
  cards: ClarificationData | null | undefined,
  scope?: DesignScope | null,
): ClarificationData | null {
  if (!cards || !Array.isArray(cards.questions)) return cards ?? null;
  if (!scope || scope === 'full') return cards;
  const excludes = SCOPE_EXCLUDE_KEYWORDS[scope] || [];
  if (excludes.length === 0) return cards;
  const cleaned = cards.questions.filter(q => {
    const hay = `${q.id || ''} ${q.question || ''} ${q.why || ''}`.toLowerCase();
    return !excludes.some(k => hay.includes(k));
  });
  if (cleaned.length === 0) return null;
  return { ...cards, questions: cleaned };
}

function parseElicitationText(text: string): ClarificationData | null {
  if (!text) return null;
  const lines = text.split('\n');
  const questions: ClarificationQuestion[] = [];

  // Match the start of a question block — q1. / Q1: / 1) / **q1:** /
  // q_architecture. / q_sigtype. / q_nf: / etc.
  // Capture group 1 = question id prefix (q1 / 1 / Q1 / q_architecture),
  // group 2 = question text.  After the separator we allow an OPTIONAL
  // closing `**` (markdown bold) then REQUIRE whitespace or end-of-line —
  // this way "**q_architecture.** Pick the…" matches while still rejecting
  // numeric option values like "0.5 dB" / "1.5:1 VSWR" (where the char
  // after the dot is a digit, not whitespace).
  const qHead = /^\s*\*{0,2}([qQ](?:\d+|_\w+)|\d+)\s*[:.)]\*{0,2}(?=\s|$)\s*(.+?)\s*\*{0,2}\s*$/;

  let introLines: string[] = [];
  let seenFirstQuestion = false;
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const m = line.match(qHead);
    if (m) {
      seenFirstQuestion = true;
      // Group 1 is the id, possibly with "q"/"Q" prefix or a bare number.
      // Normalise to lowercase "q<N>" so both "q1" and "1" map to "q1".
      const idRaw = m[1].toLowerCase();
      const rawId = idRaw.startsWith('q') ? idRaw : 'q' + idRaw;
      // If the question itself wrapped onto subsequent source lines (common
      // with very long wordings), greedily append them into the question text
      // until we hit a blank line, a paren rationale, or a pipe-options line.
      let questionText = m[2].trim().replace(/^\*+|\*+$/g, '').trim();
      let why = '';
      let optionsBuffer = '';
      let j = i + 1;

      // Phase 1: gobble continuation lines into questionText
      while (j < lines.length) {
        const nxt = lines[j].trim();
        if (!nxt) { j++; break; }                                  // blank ends question
        if (qHead.test(nxt)) break;                                // next question
        if (/^\(.+\)$/.test(nxt)) break;                           // paren → why
        if (nxt.includes('|') && (nxt.match(/\s\|\s/g) || []).length >= 1) break; // options
        if (/^[-=_]{3,}$/.test(nxt)) break;                        // horizontal rule
        questionText += ' ' + nxt;
        j++;
      }

      // Phase 2: collect the (optional) why line
      while (j < lines.length) {
        const nxt = lines[j].trim();
        if (!nxt) { j++; continue; }
        if (qHead.test(nxt)) break;
        if (!why && /^\(.+\)$/.test(nxt)) {
          why = nxt.slice(1, -1).trim();
          j++;
          break;
        }
        // Not a why — leave it for phase 3
        break;
      }

      // Phase 3: collect the options block. Options may wrap across multiple
      // source lines (the AI occasionally breaks "Not sure — recommend based
      // on …" onto its own line). Join every non-empty, non-qHead line until
      // we hit a blank line or the next question head, then split on `|`.
      let sawPipe = false;
      while (j < lines.length) {
        const nxt = lines[j].trim();
        if (!nxt) {
          if (sawPipe) break;           // blank after options → done
          j++; continue;                 // still haven't seen pipes, keep searching
        }
        if (qHead.test(nxt)) break;
        if (/^[-=_]{3,}$/.test(nxt)) break;
        // If we're still searching for the opts line and hit a paren, that's why
        if (!sawPipe && !why && /^\(.+\)$/.test(nxt)) {
          why = nxt.slice(1, -1).trim();
          j++;
          continue;
        }
        if (nxt.includes('|')) sawPipe = true;
        if (sawPipe) {
          optionsBuffer += (optionsBuffer ? ' ' : '') + nxt;
        }
        j++;
      }

      const options: string[] = optionsBuffer
        .split('|')
        .map(s => s.trim().replace(/^\*+|\*+$/g, '').trim())
        .filter(Boolean);

      questionText = questionText.trim().replace(/\s+/g, ' ');

      if (questionText && options.length >= 2) {
        questions.push({
          id: rawId,
          question: questionText,
          why: why,
          options,
        });
      }
      i = j;
      continue;
    }
    if (!seenFirstQuestion) introLines.push(line);
    i++;
  }

  if (questions.length === 0) return null;
  const intro = introLines.join('\n').trim() ||
    'A few follow-up questions to refine the design.';
  return { intro, questions };
}

/**
 * Strip all q1./Q1:/1) question blocks + their option lines + trailing
 * "Please answer" boilerplate out of an AI response. Used when the follow-up
 * card group takes over — we don't want the questions rendered twice (once
 * as plain text, once as clickable cards). Returns only the intro sentence.
 */
function stripQuestionBlocks(text: string): string {
  if (!text) return '';
  const lines = text.split('\n');
  // Same whitespace-after-separator rule as parseElicitationText so that
  // option values ("0.5 dB", "1.5:1 VSWR") aren't mistaken for question heads.
  // Supports both numeric IDs (q1, Q1, 1.) and descriptive IDs
  // (q_architecture, q_sigtype, q_nf, etc.) emitted by the 8-stage flow.
  const qHead = /^\s*\*{0,2}(?:[qQ](?:\d+|_\w+)|\d+)\s*[:.)](?=\s|$)/;
  const out: string[] = [];
  for (const line of lines) {
    if (qHead.test(line)) break;
    out.push(line);
  }
  // Also drop a trailing "---" rule + filler line that the backend used to
  // append in older prose-rendering mode (harmless no-op for new builds).
  while (out.length && /^\s*(---|\*Please answer)/.test(out[out.length - 1])) {
    out.pop();
  }
  return out.join('\n').trim();
}

const CLARIFY_SUGGESTIONS = [
  { label: 'RF receiver, 5-18GHz wideband', icon: '[RF]' },
  { label: 'BLDC motor controller, 48V, 10kW', icon: '[Motor]' },
  { label: 'FPGA-based digital signal processor', icon: '[FPGA]' },
  { label: 'Power supply, 24V to 5V, 10A', icon: '[Power]' },
];

// ---- Props ----
interface Props {
  project: Project | null;
  phase: PhaseMeta;
  phaseStatus: string;
  pipelineStarted: boolean;
  messages: ChatMessage[];
  onMessages: (msgs: ChatMessage[]) => void;
  onStatusChange: () => void;
  onPhaseComplete: () => void;
  /** v20 — Stage 0 design scope. null = not yet picked. */
  scope?: DesignScope | null;
  /** v20 — Callback fired when the user picks a scope in the pre-stage card. */
  onScopeChange?: (s: DesignScope) => void;
}

// ---- Animated thinking indicator with elapsed timer ----
function ThinkingIndicator({ color }: { color: string }) {
  const [elapsed, setElapsed] = useState(0);
  const [dots, setDots] = useState('');
  useEffect(() => {
    const t = setInterval(() => setElapsed(e => e + 1), 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    const t = setInterval(() => setDots(d => d.length >= 3 ? '' : d + '.'), 400);
    return () => clearInterval(t);
  }, []);
  // v15 — extended phase labels past 30s so long generate_requirements emits
  // (can take 120-240s for dense RF projects with full cascade + BOM + mermaid)
  // don't look hung on a "Finalizing requirements" label that was sized for a
  // 45s budget. Cutoffs in seconds.
  const phasesWithCutoff: Array<{ at: number; label: string }> = [
    { at: 0,   label: 'Decoding your design intent' },
    { at: 6,   label: 'Scouting 500K+ components' },
    { at: 14,  label: 'Scoring and ranking candidates' },
    { at: 24,  label: 'Drafting block diagram' },
    { at: 36,  label: 'Computing RF cascade & datasheet links' },
    { at: 70,  label: 'Composing full design package' },
    { at: 120, label: 'Finalizing — this can take 2-3 minutes for dense RF' },
  ];
  let phaseLabel = phasesWithCutoff[0].label;
  for (const p of phasesWithCutoff) {
    if (elapsed >= p.at) phaseLabel = p.label;
  }
  const phases = [phaseLabel]; // kept as array for below
  const phaseIdx = 0;
  // Stretch the progress scale to 180s; still cap below 100% so the bar never
  // implies "done" while the backend is still working.
  const progress = Math.min((elapsed / 180) * 100, 95);
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <div style={{
          width: 20, height: 20, borderRadius: '50%',
          border: `2px solid ${color}44`, borderTopColor: color,
          animation: 'spin 0.7s linear infinite',
        }} />
        <span style={{ fontSize: 13, color: 'var(--text2)', fontFamily: "'DM Mono',monospace" }}>{phases[phaseIdx]}{dots}</span>
        <span style={{ fontSize: 11, color: 'var(--text4)', marginLeft: 'auto', fontFamily: "'DM Mono',monospace" }}>{elapsed}s</span>
      </div>
      <div style={{ height: 3, borderRadius: 2, background: `${color}15`, overflow: 'hidden' }}>
        <div style={{ height: '100%', borderRadius: 2, background: `linear-gradient(90deg, ${color}, ${color}88)`, width: `${progress}%`, transition: 'width 1s ease-out', boxShadow: `0 0 8px ${color}40` }} />
      </div>
    </div>
  );
}

// =================================================================
//  v21 — WIZARD FRAME
//  Renders the active stage of the 6-stage wizard. All data comes
//  from rfArchitect.ts. Parent (ChatView) owns the state and passes
//  callbacks back up.
// =================================================================

interface WizardFrameProps {
  stage: 0 | 1 | 2 | 3 | 4 | 5 | 6 | 'done';
  wizard: WizardState;
  color: string;
  onBack: (target: 0 | 1 | 2 | 3 | 4 | 5 | 6 | 'done') => void;
  onType: (typeId: string) => void;
  onScope: (s: DesignScope) => void;
  onApp: (appId: string) => void;
  onArch: (archId: string) => void;
  onSpec: (qid: string, value: string) => void;
  onDetail: (qid: string, value: string) => void;
  onAppAns: (qid: string, value: string) => void;
  otherActiveId: string | null;
  otherInput: Record<string, string>;
  onOtherActive: (qid: string | null) => void;
  onOtherInput: (qid: string, v: string) => void;
  wizardExtraNotes: string;
  onExtraNotes: (v: string) => void;
  onFinalize: () => void;
  onAdvance: (s: 0 | 1 | 2 | 3 | 4 | 5 | 6 | 'done') => void;
}

function WizardFrame(p: WizardFrameProps) {
  const { stage, wizard, color } = p;

  /** Shared chip-row renderer with "Other" free-text fallback.
   *  P26 #20 (2026-04-26): the rfArchitect.ts data lists `'Other'` as
   *  the LAST chip in many spec/question entries. The renderer also
   *  appends a separate `✏ Other` button (the free-text fallback). The
   *  user reported seeing "two Other options" — strip any literal
   *  `Other` (case-insensitive trim) from the data chips so only the
   *  pencil-prefixed free-text button remains. */
  const ChipRow = ({ qid, chips, selected, onPick, qColor }: {
    qid: string; chips: string[]; selected?: string;
    onPick: (v: string) => void; qColor: string;
  }) => {
    const visibleChips = chips.filter(c => c.trim().toLowerCase() !== 'other');
    const isOtherActive = p.otherActiveId === qid;
    const isOtherValue = selected !== undefined && !visibleChips.includes(selected);
    return (
      <div style={{ display: 'flex', flexWrap: 'wrap' as const, gap: 7 }}>
        {visibleChips.map(opt => {
          const isSel = selected === opt;
          return (
            <button key={opt} onClick={() => { onPick(opt); p.onOtherActive(null); }}
              style={{ padding: '6px 13px', fontSize: 12, fontFamily: "'DM Mono',monospace",
                background: isSel ? `${qColor}22` : 'var(--panel)',
                border: `0.5px solid ${isSel ? qColor : qColor + '44'}`,
                borderRadius: 4, cursor: 'pointer',
                color: isSel ? 'var(--text)' : 'var(--text3)',
                transition: 'all 0.12s' }}>
              {isSel ? '\u2713 ' : ''}{opt}
            </button>
          );
        })}
        {!isOtherActive ? (
          <button onClick={() => p.onOtherActive(qid)}
            style={{ padding: '6px 13px', fontSize: 12, fontFamily: "'DM Mono',monospace",
              background: isOtherValue ? `${qColor}22` : 'var(--panel)',
              border: `0.5px solid ${isOtherValue ? qColor : qColor + '44'}`,
              borderRadius: 4, cursor: 'pointer',
              color: isOtherValue ? 'var(--text)' : 'var(--text3)',
              transition: 'all 0.12s' }}>
            {isOtherValue ? `\u2713 ${selected}` : '\u270f Other'}
          </button>
        ) : (
          <div style={{ display: 'flex', gap: 6, width: '100%', marginTop: 4 }}>
            <input autoFocus value={p.otherInput[qid] || ''}
              onChange={e => p.onOtherInput(qid, e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && (p.otherInput[qid] || '').trim()) {
                  onPick((p.otherInput[qid] || '').trim()); p.onOtherActive(null);
                } else if (e.key === 'Escape') { p.onOtherActive(null); }
              }}
              placeholder="Type your answer..."
              style={{ flex: 1, background: 'var(--panel)',
                border: `1px solid ${qColor}66`, borderRadius: 4,
                padding: '5px 10px', fontSize: 12, color: 'var(--text)',
                fontFamily: "'DM Mono',monospace", outline: 'none' }} />
            <button disabled={!(p.otherInput[qid] || '').trim()}
              onClick={() => { const v = (p.otherInput[qid] || '').trim(); if (v) { onPick(v); p.onOtherActive(null); } }}
              style={{ padding: '5px 12px', fontSize: 11,
                background: (p.otherInput[qid] || '').trim() ? qColor : 'var(--panel2)',
                border: 'none', borderRadius: 4,
                cursor: (p.otherInput[qid] || '').trim() ? 'pointer' : 'default',
                color: (p.otherInput[qid] || '').trim() ? '#070b14' : 'var(--text4)',
                fontFamily: "'DM Mono',monospace", fontWeight: 700 }}>
              OK
            </button>
          </div>
        )}
      </div>
    );
  };

  /** Progress rail — shows the 7-stage breadcrumb (0 Type → 6 Confirm). */
  const StageRail = () => {
    const labels: Array<[number, string]> = [
      [0, 'Type'], [1, 'Scope'], [2, 'App'], [3, 'Architecture'],
      [4, 'Specs'], [5, 'Details'], [6, 'Confirm'],
    ];
    return (
      <div style={{ display: 'flex', justifyContent: 'center', gap: 10,
                    marginBottom: 18, flexWrap: 'wrap' as const }}>
        {labels.map(([n, lbl]) => {
          const current = stage === n;
          const done = typeof stage === 'number' && n < stage;
          const clickable = done;
          return (
            <button key={n}
              disabled={!clickable}
              onClick={() => clickable && p.onBack(n as 0|1|2|3|4|5|6)}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '4px 10px', borderRadius: 999,
                border: `1px solid ${current ? color : done ? color + '66' : 'var(--panel3)'}`,
                background: current ? `${color}22` : 'transparent',
                color: current ? color : done ? 'var(--text2)' : 'var(--text4)',
                fontFamily: "'DM Mono',monospace", fontSize: 10,
                letterSpacing: '0.08em',
                cursor: clickable ? 'pointer' : 'default' }}>
              <span style={{ fontWeight: 700 }}>{done ? '\u2713' : n}</span>
              <span style={{ textTransform: 'uppercase' as const }}>{lbl}</span>
            </button>
          );
        })}
      </div>
    );
  };

  /** Inline auto-suggestion rendered next to a chip row. */
  const SuggestionHint = ({ qid, bucket }: { qid: string; bucket: 'specs'|'details'|'appAnswers' }) => {
    const v = wizard[bucket][qid];
    const msg = v && AUTO_SUGGESTIONS[qid]?.[v];
    if (!msg) return null;
    return (
      <div style={{ marginTop: 6, padding: '6px 10px',
        border: `0.5px solid ${color}55`, borderLeft: `3px solid ${color}`,
        background: `${color}0c`, borderRadius: 3, fontSize: 11,
        color: 'var(--text2)', fontFamily: "'DM Mono',monospace",
        lineHeight: 1.5 }}>
        <span style={{ color, letterSpacing: '0.08em' }}>{'ARCHITECT \u00B7 '}</span>{msg}
      </div>
    );
  };

  // ── Stage 0 — PROJECT TYPE ───────────────────────────────────────────
  if (stage === 0) {
    return (
      <div style={{ padding: '4px 4px 24px' }}>
        <StageRail />
        <div style={{ textAlign: 'center', maxWidth: 640, margin: '0 auto 20px' }}>
          <div style={{ fontSize: 10, color, letterSpacing: '0.16em', fontFamily: "'DM Mono',monospace", marginBottom: 6 }}>
            {'STAGE 0 \u00B7 PROJECT TYPE'}
          </div>
          <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 19, fontWeight: 700, color: 'var(--text)', marginBottom: 6 }}>
            What are we building?
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.55, fontFamily: "'DM Mono',monospace" }}>
            Pick the top-level block — drives architectures, specs, and deep-dive questions.
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, maxWidth: 620, margin: '0 auto' }}>
          {Object.values(PROJECT_TYPES).map(t => {
            const isSel = wizard.projectType === t.id;
            const disabled = !t.supported;
            return (
              <button key={t.id}
                onClick={() => { if (!disabled) p.onType(t.id); }}
                disabled={disabled}
                style={{ display: 'flex', flexDirection: 'column', gap: 8,
                  padding: '14px 16px', textAlign: 'left' as const,
                  position: 'relative' as const,
                  background: isSel ? `${color}14` : 'var(--panel)',
                  border: `1px solid ${isSel ? color : color + '22'}`,
                  borderRadius: 8,
                  cursor: disabled ? 'not-allowed' : 'pointer',
                  color: 'var(--text2)',
                  opacity: disabled ? 0.45 : 1,
                  fontFamily: "'DM Mono',monospace",
                  transition: 'all 0.14s' }}>
                {disabled && (
                  <span style={{ position: 'absolute' as const, top: 8, right: 10,
                    fontSize: 9, color: 'var(--text4)', letterSpacing: '0.1em',
                    fontFamily: "'DM Mono',monospace", padding: '2px 6px',
                    border: '0.5px solid var(--border)', borderRadius: 3 }}>
                    SOON
                  </span>
                )}
                <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>
                  {t.name}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.5 }}>{t.desc}</div>
                <div style={{ fontSize: 10, color: 'var(--text4)', letterSpacing: '0.04em' }}>
                  e.g. {t.examples}
                </div>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  // ── Stage 1 — SCOPE ──────────────────────────────────────────────────
  if (stage === 1) {
    // P26 #13: per project_type the available scopes differ:
    //   power_supply  -> only 'full' (no RF scope distinctions)
    //   switch_matrix -> 'full' + 'front-end'
    //   receiver / transmitter / transceiver -> all 4
    const allowedScopes = scopesForProjectType(wizard.projectType);
    const SCOPE_CARDS_ALL: Array<{ id: DesignScope; title: string; oneLiner: string; covers: string; icon: string }> = [
      { id: 'full',           title: 'Full System',   oneLiner: SCOPE_DESC['full'].desc,           covers: SCOPE_DESC['full'].covers,           icon: '\u25C9' },
      { id: 'front-end',      title: 'RF Front-End Only',      oneLiner: SCOPE_DESC['front-end'].desc,      covers: SCOPE_DESC['front-end'].covers,      icon: '\u25E7' },
      { id: 'downconversion', title: 'Downconversion / IF',    oneLiner: SCOPE_DESC['downconversion'].desc, covers: SCOPE_DESC['downconversion'].covers, icon: '\u25D2' },
      { id: 'dsp',            title: 'Baseband / DSP Only',    oneLiner: SCOPE_DESC['dsp'].desc,            covers: SCOPE_DESC['dsp'].covers,            icon: '\u25E8' },
    ];
    const SCOPE_CARDS = SCOPE_CARDS_ALL.filter(c => allowedScopes.includes(c.id));
    return (
      <div style={{ padding: '4px 4px 24px' }}>
        <StageRail />
        <div style={{ textAlign: 'center', maxWidth: 600, margin: '0 auto 20px' }}>
          <div style={{ fontSize: 10, color, letterSpacing: '0.16em', fontFamily: "'DM Mono',monospace", marginBottom: 6 }}>
            {'STAGE 1 \u00B7 DESIGN SCOPE'}
          </div>
          <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 19, fontWeight: 700, color: 'var(--text)', marginBottom: 6 }}>
            What are you designing?
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.55, fontFamily: "'DM Mono',monospace" }}>
            Pick once. Steers questions asked + which pipeline phases run.
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, maxWidth: 620, margin: '0 auto' }}>
          {SCOPE_CARDS.map(s => (
            <button key={s.id} onClick={() => p.onScope(s.id)}
              style={{ display: 'flex', flexDirection: 'column', gap: 8,
                padding: '14px 16px', textAlign: 'left' as const,
                background: wizard.scope === s.id ? `${color}14` : 'var(--panel)',
                border: `1px solid ${wizard.scope === s.id ? color : color + '22'}`,
                borderRadius: 8, cursor: 'pointer', color: 'var(--text2)',
                fontFamily: "'DM Mono',monospace", transition: 'all 0.14s' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 17, color }}>{s.icon}</span>
                <span style={{ fontFamily: "'Syne',sans-serif", fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>
                  {s.title}
                </span>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.5 }}>{s.oneLiner}</div>
              <div style={{ fontSize: 10, color: 'var(--text4)', letterSpacing: '0.08em', textTransform: 'uppercase' as const }}>
                Covers: {s.covers}
              </div>
            </button>
          ))}
        </div>
      </div>
    );
  }

  // ── Stage 2 — APPLICATION ────────────────────────────────────────────
  if (stage === 2) {
    return (
      <div style={{ padding: '4px 4px 24px' }}>
        <StageRail />
        <div style={{ textAlign: 'center', maxWidth: 600, margin: '0 auto 20px' }}>
          <div style={{ fontSize: 10, color, letterSpacing: '0.16em', fontFamily: "'DM Mono',monospace", marginBottom: 6 }}>
            {'STAGE 2 \u00B7 APPLICATION'}
          </div>
          <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 19, fontWeight: 700, color: 'var(--text)', marginBottom: 6 }}>
            What's the target application?
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.55, fontFamily: "'DM Mono',monospace" }}>
            Drives architecture ranking + cascade rules.
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, maxWidth: 620, margin: '0 auto' }}>
          {applicationsForProjectType(wizard.projectType).map(a => (
            <button key={a.id} onClick={() => p.onApp(a.id)}
              style={{ display: 'flex', flexDirection: 'column', gap: 6,
                padding: '12px 14px', textAlign: 'left' as const,
                background: wizard.application === a.id ? `${color}14` : 'var(--panel)',
                border: `1px solid ${wizard.application === a.id ? color : color + '22'}`,
                borderRadius: 8, cursor: 'pointer',
                fontFamily: "'DM Mono',monospace", transition: 'all 0.14s' }}>
              <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>
                {a.name}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text3)', lineHeight: 1.5 }}>
                {a.desc}
              </div>
            </button>
          ))}
        </div>
      </div>
    );
  }

  // ── Stage 3 — ARCHITECTURE ───────────────────────────────────────────
  if (stage === 3) {
    if (!wizard.scope || !wizard.application) {
      return <div style={{ padding: 20, color: 'var(--text3)' }}>Missing scope/application, go back.</div>;
    }
    // Pick the architecture catalogue per project_type. We always end up
    // with `linear` + `detector` lists so the existing render code below
    // doesn't need to fork — each new type folds its categories into
    // those two slots with descriptive section headings handled
    // implicitly via grouping.
    const ptype = wizard.projectType ?? 'receiver';
    let linear: ArchDef[];
    let detector: ArchDef[];
    let strong: string[] = [];
    if (ptype === 'transmitter') {
      const tx = filterTxArchByScopeAndApp(wizard.scope, wizard.application);
      linear = [...tx.linear_pa, ...tx.upconvert];
      detector = tx.saturated_pa;
      strong = tx.strong;
    } else if (ptype === 'transceiver') {
      const trx = filterTrxArchByScope(wizard.scope);
      linear = trx.trx;
      detector = [];
    } else if (ptype === 'power_supply') {
      const psu = filterPsuArch();
      linear = psu.dcdc;          // switching DC-DC topologies
      detector = psu.linear;      // LDO / linear regulators (rendered as 2nd group)
    } else if (ptype === 'switch_matrix') {
      const swm = filterSwmArch();
      linear = swm.nonblocking;   // crossbar / Clos / MEMS
      detector = swm.blocking;    // tree / broadcast / PIN-diode (rendered as 2nd group)
    } else {
      const rx = filterArchByScopeAndApp(wizard.scope, wizard.application);
      linear = rx.linear;
      detector = rx.detector;
      strong = rx.strong;
    }
    const archBlock = (arch: typeof linear[number]) => {
      const isSel = wizard.architecture === arch.id;
      const isStrong = strong.includes(arch.id);
      return (
        <button key={arch.id} onClick={() => p.onArch(arch.id)}
          style={{ display: 'flex', flexDirection: 'column', gap: 6,
            padding: '12px 14px', textAlign: 'left' as const,
            background: isSel ? `${color}14` : 'var(--panel)',
            border: `1px solid ${isSel ? color : isStrong ? color + '55' : color + '22'}`,
            borderRadius: 6, cursor: 'pointer',
            fontFamily: "'DM Mono',monospace", transition: 'all 0.14s',
            position: 'relative' as const }}>
          {isStrong && (
            <span style={{ position: 'absolute' as const, top: 8, right: 10,
              fontSize: 9, color, letterSpacing: '0.1em',
              fontFamily: "'DM Mono',monospace" }}>
              RECOMMENDED
            </span>
          )}
          <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>
            {arch.name}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', lineHeight: 1.5 }}>
            {arch.desc}
          </div>
        </button>
      );
    };
    return (
      <div style={{ padding: '4px 4px 24px', maxWidth: 760, margin: '0 auto' }}>
        <StageRail />
        <div style={{ textAlign: 'center', margin: '0 auto 20px' }}>
          <div style={{ fontSize: 10, color, letterSpacing: '0.16em', fontFamily: "'DM Mono',monospace", marginBottom: 6 }}>
            {'STAGE 3 \u00B7 ARCHITECTURE'}
          </div>
          <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 19, fontWeight: 700, color: 'var(--text)', marginBottom: 6 }}>
            Pick an architecture
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.55, fontFamily: "'DM Mono',monospace" }}>
            Ranked by fit to your scope + application. Items marked RECOMMENDED match the app profile.
          </div>
        </div>
        {linear.length > 0 && (
          <>
            <div style={{ fontSize: 10, color: 'var(--text4)', letterSpacing: '0.1em',
              textTransform: 'uppercase' as const, fontFamily: "'DM Mono',monospace",
              margin: '0 0 8px 2px' }}>
              {ptype === 'transceiver'   ? 'Transceiver topologies'
              : ptype === 'power_supply' ? 'Switching DC-DC topologies'
              : ptype === 'switch_matrix'? 'Non-blocking topologies'
              : 'Linear topologies'}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 18 }}>
              {linear.map(archBlock)}
            </div>
          </>
        )}
        {detector.length > 0 && (
          <>
            <div style={{ fontSize: 10, color: 'var(--text4)', letterSpacing: '0.1em',
              textTransform: 'uppercase' as const, fontFamily: "'DM Mono',monospace",
              margin: '0 0 8px 2px' }}>
              {ptype === 'power_supply'  ? 'Linear regulators (LDOs)'
              : ptype === 'switch_matrix'? 'Blocking topologies (cheaper, restricted)'
              : ptype === 'transmitter'  ? 'Saturated PA topologies'
              : 'Detector topologies (special-purpose)'}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 18 }}>
              {detector.map(archBlock)}
            </div>
          </>
        )}
      </div>
    );
  }

  // ── Stage 4 — SPECS ──────────────────────────────────────────────────
  if (stage === 4) {
    if (!wizard.scope) return null;
    const { shown, hidden } = filterSpecsByScope(wizard.scope, wizard.mdsLockEnabled, wizard.projectType);
    const allAnswered = shown.filter(q => !q.advanced).every(q => wizard.specs[q.id]);
    return (
      <div style={{ padding: '4px 4px 24px', maxWidth: 820, margin: '0 auto' }}>
        <StageRail />
        <div style={{ textAlign: 'center', margin: '0 auto 16px' }}>
          <div style={{ fontSize: 10, color, letterSpacing: '0.16em', fontFamily: "'DM Mono',monospace", marginBottom: 6 }}>
            {'STAGE 4 \u00B7 TIER-1 SPECS'}
          </div>
          <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 19, fontWeight: 700, color: 'var(--text)', marginBottom: 6 }}>
            Core specifications
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.55, fontFamily: "'DM Mono',monospace" }}>
            RF front-end parameters: frequency, IBW, LNA NF, gain, filter topology.
          </div>
        </div>

        {shown.map((q: SpecDef, i: number) => {
          const qColor = Q_COLORS_CLARIFY[i % Q_COLORS_CLARIFY.length];
          const selected = wizard.specs[q.id];
          return (
            <div key={q.id} style={{ marginBottom: 18 }}>
              <div style={{ fontSize: 10, color: qColor, letterSpacing: '0.1em',
                textTransform: 'uppercase' as const, fontWeight: 600,
                fontFamily: "'DM Mono',monospace", marginBottom: 4 }}>
                Q{i + 1} {q.drives ? `\u00B7 ${q.drives}` : ''}
              </div>
              <div style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.5, marginBottom: 9 }}>
                {specLabel(q, wizard.scope)}
              </div>
              <ChipRow qid={q.id} chips={q.chips} selected={selected}
                onPick={v => p.onSpec(q.id, v)} qColor={qColor} />
              <SuggestionHint qid={q.id} bucket="specs" />
            </div>
          );
        })}

        {hidden.length > 0 && (
          <div style={{ marginTop: 8, padding: '8px 12px',
            background: 'transparent', border: `1px dashed var(--panel3)`,
            borderRadius: 4, fontSize: 10, color: 'var(--text4)',
            fontFamily: "'DM Mono',monospace", lineHeight: 1.5 }}>
            Scope-filtered out for {SCOPE_LABELS[wizard.scope]}: {hidden.map(h => h.id).join(', ')}
          </div>
        )}

        <div style={{ marginTop: 18, display: 'flex', justifyContent: 'space-between', gap: 10 }}>
          <button onClick={() => p.onBack(3)}
            style={{ padding: '9px 18px', background: 'transparent',
              border: '1px solid var(--panel3)', borderRadius: 4,
              color: 'var(--text3)', fontFamily: "'DM Mono',monospace",
              fontSize: 12, cursor: 'pointer' }}>
            {'\u2190 Back'}
          </button>
          <button disabled={!allAnswered} onClick={() => p.onAdvance(5)}
            style={{ padding: '9px 22px',
              background: allAnswered ? `${color}12` : 'var(--panel2)',
              border: `0.5px solid ${allAnswered ? color + '80' : 'var(--panel3)'}`,
              borderRadius: 4,
              color: allAnswered ? color : 'var(--text4)',
              fontFamily: "'DM Mono',monospace", fontSize: 13,
              cursor: allAnswered ? 'pointer' : 'default',
              letterSpacing: '0.02em' }}>
            {'Continue to details \u2192'}
          </button>
        </div>
      </div>
    );
  }

  // ── Stage 5 — DETAILS + APP ADDENDUM ─────────────────────────────────
  if (stage === 5) {
    const dd = resolveDeepDiveQs(wizard);
    const appQs = resolveAppQs(wizard);
    const allAnswered = dd.qs.every(q => wizard.details[q.id]) && appQs.every(q => wizard.appAnswers[q.id]);
    return (
      <div style={{ padding: '4px 4px 24px', maxWidth: 820, margin: '0 auto' }}>
        <StageRail />
        <div style={{ textAlign: 'center', margin: '0 auto 16px' }}>
          <div style={{ fontSize: 10, color, letterSpacing: '0.16em', fontFamily: "'DM Mono',monospace", marginBottom: 6 }}>
            {'STAGE 5 \u00B7 DEEP DIVE'}
          </div>
          <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 19, fontWeight: 700, color: 'var(--text)', marginBottom: 6 }}>
            {dd.dive ? dd.dive.title : 'Deep dive'}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.55, fontFamily: "'DM Mono',monospace" }}>
            {dd.dive?.note}
          </div>
        </div>

        {dd.qs.map((q: DeepDiveQ, i: number) => {
          const qColor = Q_COLORS_CLARIFY[i % Q_COLORS_CLARIFY.length];
          const selected = wizard.details[q.id];
          return (
            <div key={q.id} style={{ marginBottom: 18 }}>
              <div style={{ fontSize: 10, color: qColor, letterSpacing: '0.1em',
                textTransform: 'uppercase' as const, fontWeight: 600,
                fontFamily: "'DM Mono',monospace", marginBottom: 4 }}>
                Detail Q{i + 1}
              </div>
              <div style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.5, marginBottom: 9 }}>
                {q.q}
              </div>
              <ChipRow qid={q.id} chips={q.chips} selected={selected}
                onPick={v => p.onDetail(q.id, v)} qColor={qColor} />
              <SuggestionHint qid={q.id} bucket="details" />
            </div>
          );
        })}

        {appQs.length > 0 && (
          <>
            <div style={{ fontSize: 10, color: 'var(--text4)', letterSpacing: '0.1em',
              textTransform: 'uppercase' as const, fontFamily: "'DM Mono',monospace",
              margin: '22px 0 10px 2px' }}>
              Application addendum
            </div>
            {appQs.map((q: AppQDef, i: number) => {
              const qColor = Q_COLORS_CLARIFY[(i + 2) % Q_COLORS_CLARIFY.length];
              const selected = wizard.appAnswers[q.id];
              return (
                <div key={q.id} style={{ marginBottom: 18 }}>
                  <div style={{ fontSize: 10, color: qColor, letterSpacing: '0.1em',
                    textTransform: 'uppercase' as const, fontWeight: 600,
                    fontFamily: "'DM Mono',monospace", marginBottom: 4 }}>
                    App Q{i + 1}
                  </div>
                  <div style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.5, marginBottom: 9 }}>
                    {q.q}
                  </div>
                  <ChipRow qid={q.id} chips={q.chips} selected={selected}
                    onPick={v => p.onAppAns(q.id, v)} qColor={qColor} />
                  <SuggestionHint qid={q.id} bucket="appAnswers" />
                </div>
              );
            })}
          </>
        )}

        <div style={{ marginTop: 18, display: 'flex', justifyContent: 'space-between', gap: 10 }}>
          <button onClick={() => p.onBack(4)}
            style={{ padding: '9px 18px', background: 'transparent',
              border: '1px solid var(--panel3)', borderRadius: 4,
              color: 'var(--text3)', fontFamily: "'DM Mono',monospace",
              fontSize: 12, cursor: 'pointer' }}>
            {'\u2190 Back'}
          </button>
          <button disabled={!allAnswered} onClick={() => p.onAdvance(6)}
            style={{ padding: '9px 22px',
              background: allAnswered ? `${color}12` : 'var(--panel2)',
              border: `0.5px solid ${allAnswered ? color + '80' : 'var(--panel3)'}`,
              borderRadius: 4,
              color: allAnswered ? color : 'var(--text4)',
              fontFamily: "'DM Mono',monospace", fontSize: 13,
              cursor: allAnswered ? 'pointer' : 'default',
              letterSpacing: '0.02em' }}>
            {'Continue to confirm \u2192'}
          </button>
        </div>
      </div>
    );
  }

  // ── Stage 6 — CONFIRM + CASCADE CHECK ────────────────────────────────
  if (stage === 6) {
    const arch = archById(wizard.architecture);
    const cascade = firedCascadeMessages(wizard);
    const suggestions = allInlineSuggestions(wizard);
    const app = applicationsForProjectType(wizard.projectType).find(a => a.id === wizard.application);
    const rationale = arch && wizard.application ? archRationale(arch.id, wizard.application) : '';
    return (
      <div style={{ padding: '4px 4px 24px', maxWidth: 820, margin: '0 auto' }}>
        <StageRail />
        <div style={{ textAlign: 'center', margin: '0 auto 16px' }}>
          <div style={{ fontSize: 10, color, letterSpacing: '0.16em', fontFamily: "'DM Mono',monospace", marginBottom: 6 }}>
            {'STAGE 6 \u00B7 CONFIRM'}
          </div>
          <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 19, fontWeight: 700, color: 'var(--text)', marginBottom: 6 }}>
            Architect summary
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', lineHeight: 1.55, fontFamily: "'DM Mono',monospace" }}>
            Review before we generate the requirements spec + BOM.
          </div>
        </div>

        <div style={{ padding: '14px 16px', background: 'var(--panel)',
          border: `1px solid ${color}33`, borderRadius: 6, marginBottom: 14 }}>
          <div style={{ fontSize: 11, color: 'var(--text3)', fontFamily: "'DM Mono',monospace", marginBottom: 8 }}>
            Scope: <span style={{ color: 'var(--text)' }}>{wizard.scope ? SCOPE_LABELS[wizard.scope] : '—'}</span>
            {' \u00B7 App: '}<span style={{ color: 'var(--text)' }}>{app?.name ?? '—'}</span>
            {' \u00B7 Arch: '}<span style={{ color: 'var(--text)' }}>{arch?.name ?? '—'}</span>
          </div>
          {arch && rationale && (
            <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6,
              fontFamily: "'DM Mono',monospace", padding: '8px 12px',
              background: `${color}0c`, borderLeft: `3px solid ${color}`, borderRadius: 3 }}>
              <span style={{ color, letterSpacing: '0.08em' }}>{'WHY \u00B7 '}</span>
              {rationale}.
            </div>
          )}
        </div>

        {/* Derived architecture-level cascade rules */}
        {cascade.length > 0 && (
          <div style={{ padding: '12px 14px', background: 'var(--panel)',
            border: `1px solid ${color}22`, borderRadius: 6, marginBottom: 14 }}>
            <div style={{ fontSize: 10, color, letterSpacing: '0.1em',
              textTransform: 'uppercase' as const, fontWeight: 600,
              fontFamily: "'DM Mono',monospace", marginBottom: 8 }}>
              {'Cascade sanity \u00B7 '}{cascade.length} rule{cascade.length === 1 ? '' : 's'} fired
            </div>
            {cascade.map((c, i) => (
              <div key={i} style={{ fontSize: 11, color: 'var(--text2)',
                fontFamily: "'DM Mono',monospace", lineHeight: 1.55,
                marginBottom: 6 }}>
                <span style={{ color: c.level === 'warn' ? '#f59e0b' : color, marginRight: 6 }}>
                  {c.level === 'warn' ? '\u26A0' : '\u2022'}
                </span>
                {c.msg}
              </div>
            ))}
          </div>
        )}

        {suggestions.length > 0 && (
          <div style={{ padding: '12px 14px', background: 'var(--panel)',
            border: `1px solid ${color}22`, borderRadius: 6, marginBottom: 14 }}>
            <div style={{ fontSize: 10, color, letterSpacing: '0.1em',
              textTransform: 'uppercase' as const, fontWeight: 600,
              fontFamily: "'DM Mono',monospace", marginBottom: 8 }}>
              Architect auto-suggestions
            </div>
            {suggestions.map((s, i) => (
              <div key={i} style={{ fontSize: 11, color: 'var(--text2)',
                fontFamily: "'DM Mono',monospace", lineHeight: 1.55,
                marginBottom: 6 }}>
                {'\u2022 '}<span style={{ color: 'var(--text3)' }}>{s.qid} = {s.value}</span> {'\u2192'} {s.msg}
              </div>
            ))}
          </div>
        )}

        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 10, color: 'var(--text4)', letterSpacing: '0.1em',
            textTransform: 'uppercase' as const, fontWeight: 600,
            fontFamily: "'DM Mono',monospace", marginBottom: 4 }}>
            Any additional requirements?{' '}
            <span style={{ opacity: 0.6, textTransform: 'none' as const, letterSpacing: 0, fontWeight: 400 }}>optional</span>
          </div>
          <textarea value={p.wizardExtraNotes}
            onChange={e => p.onExtraNotes(e.target.value)}
            placeholder={'e.g. Must operate at -40\u00B0C to +85\u00B0C, use SMA connectors, IPC Class 3...'}
            rows={3}
            style={{ width: '100%', background: 'var(--panel)',
              border: `1px solid ${p.wizardExtraNotes.trim() ? color + '66' : 'var(--panel3)'}`,
              borderRadius: 5, padding: '9px 12px', fontSize: 12,
              color: 'var(--text)', fontFamily: "'DM Mono',monospace",
              resize: 'vertical' as const, outline: 'none', lineHeight: 1.65,
              boxSizing: 'border-box' as const, transition: 'border-color 0.2s' }} />
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
          <button onClick={() => p.onBack(5)}
            style={{ padding: '9px 18px', background: 'transparent',
              border: '1px solid var(--panel3)', borderRadius: 4,
              color: 'var(--text3)', fontFamily: "'DM Mono',monospace",
              fontSize: 12, cursor: 'pointer' }}>
            {'\u2190 Back'}
          </button>
          <button onClick={p.onFinalize}
            style={{ padding: '11px 28px', background: `${color}12`,
              border: `0.5px solid ${color}80`, borderRadius: 4, color,
              fontSize: 13, fontFamily: "'DM Mono',monospace",
              cursor: 'pointer', letterSpacing: '0.02em' }}>
            {'Generate requirements spec \u2192'}
          </button>
        </div>
      </div>
    );
  }

  return null;
}

// Silence "unused" for architectures list when someone tweaks types.
void ALL_ARCHITECTURES;

// ---- Main ChatView ----
export default function ChatView({ project, phase, phaseStatus, pipelineStarted, messages, onMessages, onStatusChange, onPhaseComplete, scope, onScopeChange }: Props) {
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState('');
  const [historyLoaded, setHistoryLoaded] = useState(false);
  // phaseCompleted: true when backend says P1 is completed — hides "Generate Documents" button.
  // Initialized from phaseStatus prop so it's correct even on page reload / project load.
  const [phaseCompleted, setPhaseCompleted] = useState(phaseStatus === 'completed');
  // showApproveCard: shows the approve / pipeline-running card
  const [showApproveCard, setShowApproveCard] = useState(phaseStatus === 'completed');
  // approveClicked: true once P2+ pipeline has actually been kicked off.
  // Driven by pipelineStarted prop (P2+ has in_progress or completed activity),
  // NOT just by phaseStatus — P1 can be done without the pipeline having started.
  const [approveClicked, setApproveClicked] = useState(pipelineStarted);

  // ── v21 — Deterministic 7-stage wizard state ────────────────────────────
  // Replaces the old "scope → waiting → clarifying" pre-stage. Now the entire
  // Round-1 elicitation runs client-side against rfArchitect.ts data. Only
  // on Stage 6 CONFIRM do we POST a single stringified payload to /chat.
  type WizardStage = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 'done';
  const [wizardStage, setWizardStage] = useState<WizardStage>(
    phaseStatus === 'completed' ? 'done' : 0
  );
  // Core wizard state — scope preloaded from parent prop (persisted in App.tsx
  // via handleScopeChange / localStorage) so a half-finished wizard survives
  // F5 across the Stage-1 decision.
  // project_type is set at project creation (CreateProjectModal) and stored
  // on the backend. Seed the wizard from it so TX projects automatically
  // land in the transmitter architecture + spec catalogues.
  const [wizard, setWizard] = useState<WizardState>(() => ({
    ...emptyWizardState(),
    scope: scope ?? null,
    projectType: project?.project_type ?? 'receiver',
  }));
  // "Other" free-text overrides per question id (shared across stages 4/5).
  const [wizOtherActive, setWizOtherActive] = useState<string | null>(null);
  const [wizOtherInput, setWizOtherInput] = useState<Record<string, string>>({});
  // Optional last-mile free-text constraint list on Stage 6.
  const [wizardExtraNotes, setWizardExtraNotes] = useState('');
  // Retained only so legacy follow-up card diagnostic branches compile.
  const [retryText, setRetryText] = useState('');

  // ── In-chat follow-up cards (post-round-1 elicitation) ───────────────────
  // When a subsequent AI /chat response looks like another elicitation round
  // (numbered questions, pipe-separated options, etc.) we fetch structured
  // cards via POST /clarify with conversation_history and render them inline
  // under the message. Keyed by AI message index in the messages array.
  // v13 — all follow-up state is keyed by stable message id (msg.id),
  // NOT by array index. This means the cards stay attached to the right
  // message even if the messages array is re-sorted, hydrated from DB, or
  // has new items spliced in.
  const [followUpCards, setFollowUpCards] = useState<Record<string, ClarificationData>>({});
  const [followUpAnswers, setFollowUpAnswers] = useState<Record<string, Record<string, string>>>({});
  const [followUpOtherActive, setFollowUpOtherActive] = useState<string | null>(null); // `${msgId}:${qId}`
  const [followUpOtherInput, setFollowUpOtherInput] = useState<Record<string, string>>({}); // keyed by `${msgId}:${qId}`
  const [followUpSubmitted, setFollowUpSubmitted] = useState<Record<string, boolean>>({});
  const [followUpLoadingFor, setFollowUpLoadingFor] = useState<string | null>(null);
  const [followUpExtra, setFollowUpExtra] = useState<Record<string, string>>({});
  // v14 — per-bubble trace of which route/path produced (or failed to produce)
  // cards this turn. Shows in the [diag] badge so we can tell from a screenshot
  // whether the bug is Route 0 (backend dropped cards), Route 1 (prose parse
  // empty) or Route 2 (/clarify failed / empty).
  //   route: 'r0' | 'r1' | 'r2-ok' | 'r2-empty' | 'r2-err' | 'none'
  //   extra: any error message / qcount
  const [followUpTrace, setFollowUpTrace] = useState<Record<string, { route: string; extra?: string }>>({});

  // Keep state in sync when props change (e.g. status poll)
  useEffect(() => {
    if (phaseStatus === 'completed') {
      setPhaseCompleted(true);
      setShowApproveCard(true);
    } else if (phaseStatus === 'draft_pending') {
      // User has chatted again after a pipeline run — reset so approve button re-appears
      setShowApproveCard(true);
      setApproveClicked(false);
    } else {
      setShowApproveCard(false);
    }
  }, [phaseStatus]);

  // v21 — sync wizard.scope with the parent-supplied prop. Parent stores
  // scope in localStorage, so on F5 the prop arrives after this component
  // first mounts; keep local wizard state aligned.
  useEffect(() => {
    if (phaseStatus === 'completed') return;
    setWizard(w => {
      if (w.scope === (scope ?? null)) return w;
      return { ...w, scope: scope ?? null };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  // Sync wizard.projectType with the loaded project's type. Matters when
  // the user switches between an RX and a TX project without a reload.
  useEffect(() => {
    if (phaseStatus === 'completed') return;
    const pt = project?.project_type ?? 'receiver';
    setWizard(w => (w.projectType === pt ? w : { ...w, projectType: pt }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.project_type]);

  useEffect(() => {
    if (pipelineStarted) setApproveClicked(true);
  }, [pipelineStarted]);

  const bottomRef = useRef<HTMLDivElement>(null);
  const color = phase.color;

  // Load conversation history from backend on first mount
  const loadHistory = useCallback(async () => {
    if (!project || historyLoaded || messages.length > 0) { setHistoryLoaded(true); return; }
    try {
      const history = await api.getConversationHistory(project.id);
      if (history.length > 0) {
        onMessages(history.map(m => ({
          role: m.role === 'assistant' ? 'ai' : 'user' as 'user' | 'ai',
          text: m.content,
          id: newMsgId(),
        })));
        setWizardStage('done'); // skip the wizard when chat history exists
      }
    } catch { /* silent */ }
    setHistoryLoaded(true);
  }, [project, historyLoaded, messages.length]);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  // ── v21 — per-project wizard state persistence. Keeps the half-finished
  // elicitation alive across F5 and between tab switches. One key per
  // project so two projects don't overwrite each other.
  const wizardKey = project ? `hp-v21-wizard-${project.id}` : null;
  // Load persisted wizard state on project load.
  useEffect(() => {
    if (!wizardKey) return;
    if (phaseStatus === 'completed') { setWizardStage('done'); return; }
    if (messages.length > 0) { setWizardStage('done'); return; }
    try {
      const raw = localStorage.getItem(wizardKey);
      if (!raw) return;
      const parsed = JSON.parse(raw) as { state: WizardState; stage: WizardStage };
      if (parsed?.state) setWizard({ ...emptyWizardState(), ...parsed.state, scope: scope ?? parsed.state.scope ?? null });
      if (parsed?.stage != null) setWizardStage(parsed.stage);
    } catch { /* ignore corrupt persist */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wizardKey]);
  // Save on wizard changes.
  useEffect(() => {
    if (!wizardKey) return;
    if (wizardStage === 'done') return; // no value persisting post-finalize
    try {
      localStorage.setItem(wizardKey, JSON.stringify({ state: wizard, stage: wizardStage }));
    } catch { /* quota — ignore */ }
  }, [wizardKey, wizard, wizardStage]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'auto' });
  }, [messages, showApproveCard, phaseCompleted, streaming]);

  /** Silently finalize Phase 1 — no user bubble, no input echo.
   *  Routes through the backend's async chat path (HTTP 202 + polling)
   *  so dense-RF finalize runs that can take 5–15 min don't time out
   *  on the HTTP connection. The progress label re-words itself based
   *  on elapsed seconds so the user knows it's still working. */
  const finalizePhase = async () => {
    if (!project || loading) return;
    setLoading(true);
    setStreaming('');

    const formatElapsed = (s: number) => {
      const m = Math.floor(s / 60);
      const r = Math.floor(s % 60);
      return m > 0 ? `${m}m ${r}s` : `${r}s`;
    };
    // Phase labels keyed on elapsed seconds. Timings reflect post-circuit-
    // breaker behaviour: component retrieval is now capped at ~19 s/stage
    // so the full finalize (LLM + BOM + audit) lands in 1-4 min for most
    // designs. Labels past 5 min cover glm-5.1 on very dense radar/EW BOMs.
    const progressLabel = (s: number) => {
      const phases = [
        { at: 0,   label: 'Fetching candidate components from distributors' },
        { at: 30,  label: 'Generating BOM — LLM selecting from real parts' },
        { at: 90,  label: 'Computing RF cascade + verifying MPNs' },
        { at: 180, label: 'Running RF audit + fixing any blockers' },
        { at: 300, label: 'Almost there — finalizing for dense RF designs' },
        { at: 420, label: 'Long BOM — still running on glm-5.1 (normal for EW/radar)' },
        { at: 600, label: 'Extended run — verifying every part on the BOM' },
      ];
      let label = phases[0].label;
      for (const p of phases) if (s >= p.at) label = p.label;
      return `${label}… (${formatElapsed(s)} elapsed)`;
    };

    try {
      const { taskId } = await api.chatAsync(project.id, '__FINALIZE__');
      // Local 1-second tick so the displayed elapsed time advances every
      // second — the 5-sec backend poll only refreshes task *status*,
      // never the elapsed counter (which would jump in 5-sec steps and
      // make the UI look frozen between polls). startedAt is captured
      // here so the local clock and the backend clock can drift
      // independently without affecting display continuity.
      const startedAt = Date.now();
      setStreaming(progressLabel(0));
      const tickHandle = window.setInterval(() => {
        const s = (Date.now() - startedAt) / 1000;
        setStreaming(progressLabel(s));
      }, 1000);

      // Poll every 5 s. No client-side hard cap — the backend has its
      // own retry budget; if the LLM truly hangs, the user can refresh
      // and the next finalize click starts a fresh task.
      const POLL_MS = 5000;
      const taskResult = await new Promise<{
        text: string;
        phaseComplete: boolean;
        draftPending: boolean;
      }>((resolve, reject) => {
        const handle = window.setInterval(async () => {
          try {
            const t = await api.getChatTask(project.id, taskId);
            // Note: do NOT call setStreaming(progressLabel(t.elapsedS))
            // here — the local 1-sec tick above owns the displayed
            // elapsed counter. Mixing the two causes 5-sec jolts.
            if (t.status === 'complete' && t.result) {
              clearInterval(handle);
              clearInterval(tickHandle);
              resolve({
                text: (t.result.response as string)
                  || 'Requirements finalized. Reviewing documents…',
                phaseComplete: !!t.result.phase_complete,
                draftPending: !!t.result.draft_pending,
              });
            } else if (t.status === 'failed') {
              clearInterval(handle);
              clearInterval(tickHandle);
              reject(new Error(t.error || 'Finalize task failed on the server'));
            }
          } catch (pollErr) {
            // 404 = task is gone (backend restarted, TTL expired). This is
            // permanent, not a network blip — stop polling and surface to
            // user. Anything else (502, network) is treated as transient.
            const msg = pollErr instanceof Error ? pollErr.message : '';
            if (msg.includes('HTTP 404')) {
              clearInterval(handle);
              clearInterval(tickHandle);
              reject(new Error('Task expired or backend restarted. Please retry.'));
              return;
            }
            console.warn('[finalize] poll error (will retry):', pollErr);
          }
        }, POLL_MS);
      });

      const cleanText = cleanAiText(taskResult.text);
      let idx = 0;
      const interval = setInterval(() => {
        idx = Math.min(idx + 16, cleanText.length);
        setStreaming(cleanText.slice(0, idx));
        if (idx >= cleanText.length) {
          clearInterval(interval);
          onMessages([...messages, { role: 'ai', text: cleanText, id: newMsgId() }]);
          setStreaming('');
          setLoading(false);
          onStatusChange();
          setPhaseCompleted(true);
          setShowApproveCard(true);
        }
      }, 16);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // Background task may still be running on the server even though
      // we couldn't poll it — flag that so the user knows to check.
      onMessages([...messages, {
        role: 'ai',
        text: `⚠️ Finalize couldn't be reached: ${msg}\n\nThe backend may still be working in the background. Wait a minute, then check the **Documents** tab — if files appear, the run succeeded. If not, click **Generate** again.`,
        id: newMsgId(),
      }]);
      setStreaming('');
      setLoading(false);
    }
  };

  /** Spawn + poll a long async chat turn. Mirrors finalizePhase's polling
   *  pattern but returns a ChatResult so callers can re-use the existing
   *  typewriter / card-handling code path. The progress label is fed into
   *  setStreaming so the UI keeps showing what stage the backend is in. */
  const runAsyncChat = async (asyncText: string): Promise<ChatResult> => {
    if (!project) throw new Error('no project');
    const formatElapsed = (s: number) => {
      const m = Math.floor(s / 60);
      const r = Math.floor(s % 60);
      return m > 0 ? `${m}m ${r}s` : `${r}s`;
    };
    // Phase labels keyed on elapsed seconds. Long-tail entries past
    // 8 min so the message keeps changing on dense BOMs (matches the
    // finalizePhase progressLabel above — keep them in sync).
    const progressLabel = (s: number) => {
      const phases = [
        { at: 0,   label: 'Generating BOM + verifying components' },
        { at: 60,  label: 'Computing RF cascade + checking distributors' },
        { at: 180, label: 'Pre-emit gate — re-verifying MPN candidates' },
        { at: 300, label: 'Running RF audit + fixing any blockers' },
        { at: 480, label: 'Finalizing — almost there for dense RF' },
        { at: 720, label: 'Dense BOM — still finalizing on glm-5.1 (this is normal)' },
        { at: 900, label: 'Long run — verifying every part on the BOM' },
      ];
      let label = phases[0].label;
      for (const p of phases) if (s >= p.at) label = p.label;
      return `${label}… (${formatElapsed(s)} elapsed)`;
    };
    const { taskId } = await api.chatAsync(project.id, asyncText);
    // Local 1-sec tick for the displayed elapsed counter; backend poll
    // (every 5 s) only checks task status. Keeps the UI ticking
    // smoothly between polls instead of jumping in 5-sec steps.
    const startedAt = Date.now();
    setStreaming(progressLabel(0));
    const tickHandle = window.setInterval(() => {
      const s = (Date.now() - startedAt) / 1000;
      setStreaming(progressLabel(s));
    }, 1000);
    const POLL_MS = 5000;
    return new Promise<ChatResult>((resolve, reject) => {
      const handle = window.setInterval(async () => {
        try {
          const t = await api.getChatTask(project.id, taskId);
          // Local tick owns the elapsed display — see finalizePhase comment.
          if (t.status === 'complete' && t.result) {
            clearInterval(handle);
            clearInterval(tickHandle);
            resolve({
              text: (t.result.response as string) || 'Done.',
              phaseComplete: !!t.result.phase_complete,
              draftPending: !!t.result.draft_pending,
              clarificationCards: t.result.clarification_cards ?? null,
            });
          } else if (t.status === 'failed') {
            clearInterval(handle);
            clearInterval(tickHandle);
            reject(new Error(t.error || 'Async chat task failed'));
          }
        } catch (pollErr) {
          // 404 = task is gone (backend restarted / TTL expired). Permanent.
          const msg = pollErr instanceof Error ? pollErr.message : '';
          if (msg.includes('HTTP 404')) {
            clearInterval(handle);
            clearInterval(tickHandle);
            reject(new Error('Task expired or backend restarted. Please retry.'));
            return;
          }
          console.warn('[runAsyncChat] poll error (will retry):', pollErr);
        }
      }, POLL_MS);
    });
  };

  const sendMessage = async (text: string) => {
    if (!project || !text.trim() || loading) return;
    setRetryText(''); // clear any previous retry state on fresh send
    const updated: ChatMessage[] = [...messages, { role: 'user', text, id: newMsgId() }];
    onMessages(updated);
    setInput('');
    setLoading(true);
    setStreaming('');
    // Hide both the approve card and "Pipeline is running" card while user is chatting.
    // They re-appear after the AI responds if the phase is still complete/draft_pending.
    if (showApproveCard) setShowApproveCard(false);

    try {
      // v20.1 — inject scope reminder into every outbound turn so the backend
      // LLM cannot forget the active design scope across rounds. The UI still
      // shows the user's raw text (see `updated` above). Skip for the
      // __FINALIZE__ sentinel and already-prefixed messages to avoid doubling
      // up with handleConfirmAnswers (which prefixes the first turn).
      let apiText = text;
      if (
        scope
        && text !== '__FINALIZE__'
        && !text.startsWith('[Design scope:')
      ) {
        apiText = `[Design scope: ${SCOPE_LABELS[scope]}]\n\n${text}`;
      }
      // Long messages (wizard generate-spec payload, big follow-up bundles)
      // hit the dense RF audit loop and routinely exceed the 600s sync
      // deadline. Route them through the backend's async runner + poll
      // loop. 400 chars catches wizard payloads (typically 1-3 KB) while
      // keeping short Q&A on the fast sync path.
      const isLongTurn =
        apiText.length > 400
        || apiText.includes('Please generate the Round-1 requirements')
        || apiText.startsWith('__FINALIZE__');
      const result = isLongTurn
        ? await runAsyncChat(apiText)
        : await api.chat(project.id, apiText);
      // Strip backend boilerplate referencing non-existent UI buttons
      // If the backend returned an empty response, show a helpful fallback
      const rawText = result.text || 'I processed your request. Check the Documents tab to see updated outputs, or try rephrasing your request.';
      const cleanText = cleanAiText(rawText);
      // Typewriter animation — 16ms/16chars (~60fps, ~1000 chars/sec)
      // Streaming div uses plain pre-wrap text (no markdown parsing per tick) for smooth rendering.
      // Full markdown is only rendered once, when the message is committed to messages[].
      let idx = 0;
      const interval = setInterval(() => {
        idx = Math.min(idx + 16, cleanText.length);
        setStreaming(cleanText.slice(0, idx));
        if (idx >= cleanText.length) {
          clearInterval(interval);
          const aiMsg: ChatMessage = { role: 'ai', text: cleanText, id: newMsgId() };
          const nextMessages: ChatMessage[] = [...updated, aiMsg];
          onMessages(nextMessages);
          setStreaming('');
          setLoading(false);
          onStatusChange();

          // Elicitation-shape check. Priority order:
          //  A. If the backend returned structured clarificationCards with at
          //     least one question, that's the authoritative signal — the
          //     agent explicitly called show_clarification_cards.  We must
          //     render chip cards regardless of the prose shape (the prose
          //     is now just a 1-liner intro and won't trip the regex
          //     heuristics in looksLikeFollowUpElicitation).
          //  B. Otherwise, fall back to prose-shape detection for legacy
          //     (pre-structured-cards) responses.
          // v12 — defensive cross-key lookup. The api.ts mapping should convert
          // `clarification_cards` → `clarificationCards`, but if that ever
          // breaks (or the backend ships under a different key), still pick
          // up the cards.  Also check camelCase/ snake_case variants.
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const anyResult = result as any;
          const rawCards = (
            result.clarificationCards
            || anyResult.clarification_cards
            || anyResult.clarificationCard
            || null
          );
          const hasStructuredCards = !!(
            rawCards
            && rawCards.questions
            && Array.isArray(rawCards.questions)
            && rawCards.questions.length > 0
          );
          // v21.2 — `phaseComplete` / `draftPending` are authoritative. Once the
          // backend says P1 is done, the rich summary may contain "?" or words
          // that `looksLikeFollowUpElicitation` would otherwise trip on. Never
          // treat a completion turn as an elicitation — the Approve panel owns
          // that UI, and cards would visually duplicate what's already decided.
          const isElicitation = !!project
            && !result.phaseComplete
            && !result.draftPending
            && (hasStructuredCards || looksLikeFollowUpElicitation(cleanText));
          if (result.phaseComplete && !isElicitation) {
            setPhaseCompleted(true);
            setShowApproveCard(true);
          } else if (result.draftPending && !isElicitation) {
            // Backend says requirements are captured — show approve button again
            setShowApproveCard(true);
            setApproveClicked(false);
          }

          if (isElicitation && project) {
            // Another elicitation round. Strategy, in preference order:
            //   0. If the backend returned structured `clarificationCards` JSON
            //      (agent called show_clarification_cards tool), use that directly.
            //      This is THE source of truth and bypasses any prose parsing.
            //   1. Otherwise, parse the AI's prose response locally (fallback).
            //   2. /clarify endpoint is the last-resort fallback for legacy
            //      responses that neither return structured JSON nor parse.
            setShowApproveCard(false);
            // v13 — key follow-up state by the AI bubble's stable id so it
            // survives any messages-array re-baselining.
            const aiMsgId = aiMsg.id;

            // Route 0 — structured cards from the backend (via defensive
            // cross-key lookup computed above).
            if (rawCards && rawCards.questions?.length) {
              // v20.1 — drop questions the backend asked that don't fit the
              // current Stage-0 scope (e.g. DSP questions on an RF-front-end
              // project). If every question gets filtered out, fall through
              // to the rescue deck which will synthesise scope-appropriate
              // replacement questions.
              const r0Filtered = filterCardsByScope(rawCards, scope);
              const r0Count = r0Filtered?.questions.length ?? 0;
              if (r0Filtered && r0Count > 0) {
                setFollowUpCards(prev => ({
                  ...prev, [aiMsgId]: r0Filtered
                }));
                setFollowUpAnswers(prev => ({ ...prev, [aiMsgId]: {} }));
                setFollowUpTrace(prev => ({
                  ...prev, [aiMsgId]: {
                    route: 'r0',
                    extra: `q=${r0Count}${r0Count !== rawCards.questions.length ? ` (scope-filtered ${rawCards.questions.length - r0Count})` : ''}`,
                  }
                }));
                return;
              }
              // All filtered out — synthesise a scope-appropriate deck.
              const rescue = buildLocalFallbackCards(
                nextMessages.map(m => ({ role: m.role, text: m.text })),
                scope,
              );
              setFollowUpCards(prev => ({ ...prev, [aiMsgId]: rescue }));
              setFollowUpAnswers(prev => ({ ...prev, [aiMsgId]: {} }));
              setFollowUpTrace(prev => ({
                ...prev, [aiMsgId]: {
                  route: 'r3-client',
                  extra: `r0-all-scope-filtered->local q=${rescue.questions.length}`,
                }
              }));
              return;
            }

            const localCardsRaw = parseElicitationText(cleanText);
            const localCards = filterCardsByScope(localCardsRaw, scope);

            if (localCards && localCards.questions.length > 0) {
              // Local parse succeeded — render those cards and stop. The AI
              // text will be trimmed to its intro (questions hidden) so the
              // user sees each question once, as a clickable card.
              setFollowUpCards(prev => ({ ...prev, [aiMsgId]: localCards }));
              setFollowUpAnswers(prev => ({ ...prev, [aiMsgId]: {} }));
              setFollowUpTrace(prev => ({
                ...prev, [aiMsgId]: { route: 'r1', extra: `q=${localCards.questions.length}` }
              }));
            } else {
              // No parseable blocks — fall back to /clarify as a last resort.
              setFollowUpLoadingFor(aiMsgId);
              const history = nextMessages.map(m => ({
                role: m.role === 'ai' ? 'assistant' : 'user',
                content: m.text,
              }));
              // v13 — no more Math.min(5, …) cap; the backend gate is keyed
              // off `prior_user_turns` so arbitrarily deep rounds work, and
              // accurate labels help debugging.
              const roundNum = nextMessages.filter(m => m.role === 'user').length + 1;
              api.clarifyRequirement(
                project.id,
                text,
                project.design_type || 'RF',
                history,
                `round-${roundNum}`
              )
                .then(cards => {
                  // v20.1 — scope filter before we hand the deck to state.
                  const filtered = filterCardsByScope(cards, scope);
                  const qcount = filtered && Array.isArray(filtered.questions) ? filtered.questions.length : 0;
                  const rawCount = cards && Array.isArray(cards.questions) ? cards.questions.length : 0;
                  if (filtered && qcount > 0) {
                    setFollowUpCards(prev => ({ ...prev, [aiMsgId]: filtered }));
                    setFollowUpAnswers(prev => ({ ...prev, [aiMsgId]: {} }));
                    setFollowUpTrace(prev => ({
                      ...prev, [aiMsgId]: {
                        route: 'r2-ok',
                        extra: `q=${qcount}${qcount !== rawCount ? ` (scope-filtered ${rawCount - qcount})` : ''}`,
                      }
                    }));
                  } else {
                    // v19 — backend returned 0 questions. Synthesise a
                    // deterministic card deck in-browser. v19.1 — if the
                    // deck collapses to the single "finalize_confirm"
                    // placeholder AND the Approve panel is going to show,
                    // skip emitting the card (it would be a visual dup).
                    const rescue = buildLocalFallbackCards(
                      nextMessages.map(m => ({ role: m.role, text: m.text })),
                      scope,
                    );
                    const _approvePanelWillShow =
                      !!result.draftPending || !!result.phaseComplete;
                    const _onlyFinalize =
                      rescue.questions.length === 1 &&
                      rescue.questions[0].id === 'finalize_confirm';
                    if (_onlyFinalize && _approvePanelWillShow) {
                      setFollowUpTrace(prev => ({
                        ...prev, [aiMsgId]: {
                          route: 'r3-client',
                          extra: 'finalize-only->suppressed (approve panel covers it)',
                        }
                      }));
                    } else {
                      setFollowUpCards(prev => ({ ...prev, [aiMsgId]: rescue }));
                      setFollowUpAnswers(prev => ({ ...prev, [aiMsgId]: {} }));
                      setFollowUpTrace(prev => ({
                        ...prev, [aiMsgId]: {
                          route: 'r3-client',
                          extra: `r2-empty->local q=${rescue.questions.length}`,
                        }
                      }));
                    }
                    if (result.draftPending) {
                      setShowApproveCard(true);
                      setApproveClicked(false);
                    }
                  }
                })
                .catch(err => {
                  console.error('[clarify-cards] /clarify FAILED →', err);
                  const errMsg = err instanceof Error ? err.message : String(err);
                  // v19 — network / 502 / timeout on /clarify. Rescue the UI
                  // with a client-side deterministic card deck so the chat
                  // never flat-lines on a backend failure. v19.1 — same
                  // finalize-only suppression as the r2-empty branch.
                  const rescue = buildLocalFallbackCards(
                    nextMessages.map(m => ({ role: m.role, text: m.text })),
                    scope,
                  );
                  const _approvePanelWillShow =
                    !!result.draftPending || !!result.phaseComplete;
                  const _onlyFinalize =
                    rescue.questions.length === 1 &&
                    rescue.questions[0].id === 'finalize_confirm';
                  if (_onlyFinalize && _approvePanelWillShow) {
                    setFollowUpTrace(prev => ({
                      ...prev, [aiMsgId]: {
                        route: 'r3-client',
                        extra: `r2-err(${errMsg.slice(0, 40)})->suppressed`,
                      }
                    }));
                  } else {
                    setFollowUpCards(prev => ({ ...prev, [aiMsgId]: rescue }));
                    setFollowUpAnswers(prev => ({ ...prev, [aiMsgId]: {} }));
                    setFollowUpTrace(prev => ({
                      ...prev, [aiMsgId]: {
                        route: 'r3-client',
                        extra: `r2-err(${errMsg.slice(0, 40)})->local q=${rescue.questions.length}`,
                      }
                    }));
                  }
                  if (result.draftPending) {
                    setShowApproveCard(true);
                    setApproveClicked(false);
                  } else if (result.phaseComplete) {
                    setPhaseCompleted(true);
                    setShowApproveCard(true);
                  }
                })
                .finally(() => setFollowUpLoadingFor(null));
            }
          }
        }
      }, 16);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      const isNetworkErr = !msg.startsWith('HTTP ');
      const display = isNetworkErr
        ? '⚠️ Cannot reach the backend.\n\nDouble-click **run.bat** in the project folder and wait for "Application startup complete", then try again.'
        : `⚠️ Server error — ${msg}\n\nCheck the uvicorn terminal window for the full traceback.`;
      onMessages([...updated, { role: 'ai', text: display, id: newMsgId() }]);
      setStreaming('');
      setLoading(false);
      setRetryText(text); // store so user can retry without re-typing
    }
  };

  const handleRetry = () => {
    if (!retryText) return;
    // Remove the last two messages (failed user bubble + error AI bubble) before retrying
    const trimmed = messages.slice(0, -2);
    onMessages(trimmed);
    sendMessage(retryText);
  };

  // ── v21 — Wizard stage handlers ─────────────────────────────────────────
  /** Stage 0 → 1: project type picked. P26 #13: also reset scope/app/arch
   *  if they're no longer compatible with the new type's allowed lists.
   *  Without this, a user who picked 'dsp' scope as a receiver and then
   *  switches to power_supply would carry an invalid scope into Stage 1. */
  const handleWizardType = (typeId: string) => {
    const t = PROJECT_TYPES[typeId];
    if (!t || !t.supported) return;
    setWizard(w => {
      const allowed = scopesForProjectType(typeId);
      const apps = applicationsForProjectType(typeId).map(a => a.id);
      return {
        ...w,
        projectType: typeId,
        scope: w.scope && allowed.includes(w.scope) ? w.scope : null,
        application: w.application && apps.includes(w.application) ? w.application : null,
        // Architecture is keyed off scope + app; a type swap invalidates it.
        architecture: null,
      };
    });
    setWizardStage(1);
  };
  /** Stage 1 → 2: scope picked. Also fires parent onScopeChange for sidebar. */
  const handleWizardScope = (s: DesignScope) => {
    if (onScopeChange) onScopeChange(s);
    setWizard(w => ({ ...w, scope: s }));
    setWizardStage(2);
  };
  /** Stage 2 → 3: application picked. */
  const handleWizardApp = (appId: string) => {
    setWizard(w => ({ ...w, application: appId }));
    setWizardStage(3);
  };
  /** Stage 3 → 4: architecture picked. */
  const handleWizardArch = (archId: string) => {
    setWizard(w => ({ ...w, architecture: archId }));
    setWizardStage(4);
  };
  /** Stage 4 spec pick. Bucket: 'specs' (Tier-1). */
  const handleWizardSpec = (qid: string, value: string) => {
    setWizard(w => ({ ...w, specs: { ...w.specs, [qid]: value } }));
  };
  /** Stage 5 deep-dive answer. Bucket: 'details'. */
  const handleWizardDetail = (qid: string, value: string) => {
    setWizard(w => ({ ...w, details: { ...w.details, [qid]: value } }));
  };
  /** Stage 5 app-addendum answer. Bucket: 'appAnswers'. */
  const handleWizardAppAns = (qid: string, value: string) => {
    setWizard(w => ({ ...w, appAnswers: { ...w.appAnswers, [qid]: value } }));
  };
  /** Build the payload string sent to the backend when the user clicks
   *  "Generate requirements spec" on Stage 6. Matches the shape the agent
   *  expects — [Design scope: X]\n\n [Application: X]\n\n [Architecture: X]\n\n
   *  ... — and is intentionally stringified (not JSON) so no backend change
   *  is needed. Cascade-sanity messages are appended as a final
   *  "Architect notes" block so the LLM can echo/refine them rather than
   *  fabricating its own numbers. (P26.6, 2026-05-05: MDS / Friis logic
   *  removed — front-end section is now front-end-only, no system-level
   *  metrics.) */
  const buildWizardPayload = (): string => {
    const s = wizard;
    const parts: string[] = [];
    if (s.projectType) parts.push(`[Project type: ${PROJECT_TYPES[s.projectType]?.name || s.projectType}]`);
    if (s.scope) parts.push(`[Design scope: ${SCOPE_LABELS[s.scope]}]`);
    const app = applicationsForProjectType(s.projectType).find(a => a.id === s.application);
    if (app) parts.push(`[Application: ${app.name}]`);
    const arch = archById(s.architecture);
    if (arch) parts.push(`[Architecture: ${arch.name}]`);
    parts.push('');
    parts.push('SYSTEM SPECIFICATIONS (Tier-1):');
    if (s.scope) {
      const { shown } = filterSpecsByScope(s.scope, s.mdsLockEnabled, s.projectType);
      shown.forEach(spec => {
        const v = s.specs[spec.id];
        if (v) parts.push(`• ${specLabel(spec, s.scope)} -> ${v}`);
      });
    }
    parts.push('');
    parts.push('SCOPE DEEP-DIVE:');
    const dd = resolveDeepDiveQs(s);
    dd.qs.forEach(q => {
      const v = s.details[q.id];
      if (v) parts.push(`• ${q.q} -> ${v}`);
    });
    const appQs = resolveAppQs(s);
    if (appQs.length > 0) {
      parts.push('');
      parts.push(`${app ? app.name.toUpperCase() : 'APP'} ADDENDUM:`);
      appQs.forEach(q => {
        const v = s.appAnswers[q.id];
        if (v) parts.push(`• ${q.q} -> ${v}`);
      });
    }
    const notes = wizardExtraNotes.trim();
    if (notes) {
      parts.push('');
      parts.push(`ADDITIONAL REQUIREMENTS: ${notes}`);
    }
    // Architect-side notes — deterministic cascade/sanity hints so the LLM
    // echoes them instead of inventing contradictory numbers.
    const cascade = firedCascadeMessages(s);
    if (cascade.length > 0) {
      parts.push('');
      parts.push('ARCHITECT NOTES (deterministic cascade checks):');
      cascade.forEach(c => parts.push(`• [${c.level.toUpperCase()}] ${c.msg}`));
    }
    // P26 #20 (2026-04-26): the architect auto-suggestions shown on the
    // confirm screen (e.g. "interferer_env = Low → IIP3 can be relaxed,
    // pick lowest-NF LNA that meets gain target") used to be display-only.
    // The user asked: "is the suggestion implemented or only for our
    // reference shown?" — answer was display-only. Now we forward them
    // to the LLM so the BOM picker can actually act on them (e.g. choose
    // a lower-IIP3, lower-NF LNA when the interferer environment is low).
    const autoSuggestions = allInlineSuggestions(s);
    if (autoSuggestions.length > 0) {
      parts.push('');
      parts.push('ARCHITECT AUTO-SUGGESTIONS (apply when picking parts — not just advisory):');
      autoSuggestions.forEach(sug => {
        parts.push(`• Trigger ${sug.qid} = ${sug.value} → ${sug.msg}`);
      });
    }
    parts.push('');
    parts.push('Please generate the Round-1 requirements spec + BOM against the above. Do NOT re-ask these questions.');
    return parts.join('\n');
  };

  const handleWizardFinalize = () => {
    const payload = buildWizardPayload();
    setWizardStage('done');
    if (wizardKey) { try { localStorage.removeItem(wizardKey); } catch { /* ignore */ } }
    setInput('');
    sendMessage(payload);
  };

  /** Bundle a follow-up card group's answers and send them as one chat turn.
   *  v13 — keyed by the AI bubble's stable msg.id (was numeric array index),
   *  so card state no longer shifts when the messages array is re-baselined. */
  const handleFollowUpSubmit = (msgId: string) => {
    const cards = followUpCards[msgId];
    if (!cards) return;
    const answers = followUpAnswers[msgId] || {};
    const answered = (cards.questions || []).filter(q => answers[q.id]);
    if (answered.length === 0 && !(followUpExtra[msgId] || '').trim()) return;
    const lines = answered.map(q => `${q.question} -> ${answers[q.id]}`).join('\n');
    const extra = (followUpExtra[msgId] || '').trim()
      ? `\n\nAdditional notes: ${(followUpExtra[msgId] || '').trim()}`
      : '';
    const combined = lines + extra;
    setFollowUpSubmitted(prev => ({ ...prev, [msgId]: true }));
    sendMessage(combined);
  };

  // lastAiText intentionally removed — QuickReplyPanel no longer shown in chat flow.

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', position: 'relative' }}>
      {wizardStage !== 'done' ? (
        /* ── v21 — 6-stage wizard flow ── */
        <>
          <div style={{ flex: 1, overflowY: 'auto', padding: '16px 0 24px' }}>
            <WizardFrame
              stage={wizardStage}
              wizard={wizard}
              color={color}
              onBack={(target: WizardStage) => setWizardStage(target)}
              onType={handleWizardType}
              onScope={handleWizardScope}
              onApp={handleWizardApp}
              onArch={handleWizardArch}
              onSpec={handleWizardSpec}
              onDetail={handleWizardDetail}
              onAppAns={handleWizardAppAns}
              otherActiveId={wizOtherActive}
              otherInput={wizOtherInput}
              onOtherActive={setWizOtherActive}
              onOtherInput={(qid, v) => setWizOtherInput(prev => ({ ...prev, [qid]: v }))}
              wizardExtraNotes={wizardExtraNotes}
              onExtraNotes={setWizardExtraNotes}
              onFinalize={handleWizardFinalize}
              onAdvance={(s) => setWizardStage(s)}
            />
            <div ref={bottomRef} />
          </div>
          <style>{`@keyframes spin { to { transform: rotate(360deg); } } @keyframes blink { 50% { opacity: 0; } } @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }`}</style>
        </>
      ) : (
        /* ── Chat flow ── */
        <>
          {/* Scrollable messages area */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0 80px 0' }}>
            {/* Welcome card — show only when no messages */}
            {messages.length === 0 && !loading && (
              <WelcomeCard color={color} onSuggestion={sendMessage} />
            )}

        {/* Message history — AI messages may have an attached follow-up card group.
            v13 — all follow-up state lookups keyed by the AI bubble's stable
            msg.id, not the numeric array index. This fixes the bug where
            rebuilding the messages array (history restore, optimistic update,
            etc.) would re-shuffle every card's state. */}
        {messages.map((msg, i) => {
          const mid = msg.id;
          const cards = msg.role === 'ai' ? followUpCards[mid] : undefined;
          const loadingForThis = followUpLoadingFor === mid && !cards;
          // When cards are visible for this message, hide the redundant
          // question/option text inside the AI bubble — show only the intro
          // (everything before the first q1./Q1:/1) block).
          const showingCards = !!cards && !followUpSubmitted[mid];
          const overrideText = showingCards && msg.role === 'ai'
            ? stripQuestionBlocks(msg.text)
            : undefined;
          // v13 inline diagnostic — renders directly beneath every AI bubble so
          // we can read the clarify-card state from a screenshot without needing
          // the browser console.  Shows: cards present Y/N, question count,
          // submitted flag, loading flag, elicitation-shape verdict, and the
          // stable msg.id prefix for cross-bubble traceability.
          const diag = false ? (() => {
            const looksElic = looksLikeFollowUpElicitation(msg.text || '');
            const qcount = cards?.questions?.length ?? 0;
            const submitted = !!followUpSubmitted[mid];
            const shortId = mid.slice(-6);
            const trace = followUpTrace[mid];
            const traceStr = trace ? ` route=${trace.route}${trace.extra ? '(' + trace.extra + ')' : ''}` : '';
            return `CARDS:${cards ? 'Y' : 'N'} q=${qcount} submitted=${submitted ? 'Y' : 'N'} loading=${loadingForThis ? 'Y' : 'N'} elicShape=${looksElic ? 'Y' : 'N'} id=${shortId} pos=${i}${traceStr}`;
          })() : null;
          return (
            <div key={mid}>
              <ChatMessageItem msg={msg} color={color} overrideText={overrideText} />
              {diag && (
                <div style={{
                  marginTop: -4, marginBottom: 10, marginLeft: 4,
                  fontSize: 9, color: 'var(--text4)',
                  fontFamily: "'DM Mono',monospace", letterSpacing: '0.05em',
                  opacity: 0.6,
                }}>
                  [diag] {diag}
                </div>
              )}
              {loadingForThis && (
                <div style={{
                  marginTop: -4, marginBottom: 14, marginLeft: 4,
                  display: 'flex', alignItems: 'center', gap: 8,
                  fontSize: 11, color: 'var(--text4)',
                  fontFamily: "'DM Mono',monospace", letterSpacing: '0.06em',
                }}>
                  <div style={{
                    width: 12, height: 12, borderRadius: '50%',
                    border: '1.5px solid transparent', borderTopColor: color,
                    animation: 'spin 0.8s linear infinite',
                  }} />
                  PREPARING QUICK-ANSWER CARDS&hellip;
                </div>
              )}
              {cards && !followUpSubmitted[mid] && (
                <FollowUpCardGroup
                  color={color}
                  cards={cards}
                  answers={followUpAnswers[mid] || {}}
                  extraText={followUpExtra[mid] || ''}
                  otherActiveKey={followUpOtherActive}
                  otherInputs={followUpOtherInput}
                  msgIdx={mid}
                  onAnswer={(qId, val) =>
                    setFollowUpAnswers(prev => ({
                      ...prev,
                      [mid]: { ...(prev[mid] || {}), [qId]: val },
                    }))
                  }
                  onOtherActiveChange={setFollowUpOtherActive}
                  onOtherInputChange={(key, val) =>
                    setFollowUpOtherInput(prev => ({ ...prev, [key]: val }))
                  }
                  onExtraChange={val =>
                    setFollowUpExtra(prev => ({ ...prev, [mid]: val }))
                  }
                  onSubmit={() => handleFollowUpSubmit(mid)}
                  onDismiss={() =>
                    setFollowUpSubmitted(prev => ({ ...prev, [mid]: true }))
                  }
                />
              )}
            </div>
          );
        })}

        {/* QuickReplyPanel removed — clarification is handled by the pre-stage flow
            before the first message, and by FollowUpCardGroup for later rounds. */}

        {/* Streaming / loading indicator */}
        {(loading || streaming) && (
          <div style={{ marginBottom: 16 }}>
            <div style={{
              padding: '16px 20px', borderRadius: '12px 12px 12px 4px',
              background: 'var(--panel2)', border: '1px solid var(--panel3)',
              borderLeft: `2px solid ${color}44`,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                <div style={{ width: 6, height: 6, borderRadius: '50%', background: color, boxShadow: `0 0 6px ${color}66` }} />
                <span style={{ fontSize: 10, color, letterSpacing: '0.1em', fontWeight: 600, fontFamily: "'DM Mono',monospace" }}>AI RESPONSE</span>
              </div>
              {streaming ? (
                <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.65, whiteSpace: 'pre-wrap', fontFamily: "'DM Mono',monospace" }}>
                  {streaming}
                  <span style={{ display: 'inline-block', width: 2, height: 15, background: color, marginLeft: 2, animation: 'blink 0.8s step-end infinite', borderRadius: 1 }} />
                </div>
              ) : (
                <ThinkingIndicator color={color} />
              )}
            </div>
          </div>
        )}

        {/* Approve & Run card */}
        {showApproveCard && !approveClicked && (
          <div style={{
            background: `${color}0d`, border: `1px solid ${color}33`,
            borderRadius: 10, padding: '16px 20px', marginBottom: 16,
          }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 8 }}>
              Requirements captured! Ready to run the full pipeline?
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12 }}>
              This will generate HRS, compliance matrix, netlist, GLR, SRS, SDD, and code review.
            </div>
            <button
              onClick={() => { setApproveClicked(true); onPhaseComplete(); }}
              style={{
                padding: '10px 24px', borderRadius: 6, cursor: 'pointer',
                fontSize: 13, fontFamily: "'DM Mono', monospace", fontWeight: 700,
                background: color, border: 'none', color: '#070b14',
                boxShadow: `0 0 20px ${color}40`, transition: 'all 0.2s',
              }}>
              Approve &amp; Run Pipeline →
            </button>
          </div>
        )}

        {/* Pipeline running card */}
        {showApproveCard && approveClicked && (
          <div style={{
            background: `${color}0d`, border: `1px solid ${color}22`,
            borderRadius: 10, padding: '14px 18px', marginBottom: 16,
            display: 'flex', alignItems: 'center', gap: 10,
          }}>
            <div style={{ width: 10, height: 10, borderRadius: '50%', background: color, animation: 'pulse 1s ease-in-out infinite' }} />
            <span style={{ fontSize: 12, color: 'var(--text2)' }}>
              Pipeline is running — check <strong style={{ color }}>Documents</strong> tab for generated outputs
            </span>
          </div>
        )}

        {/* Retry button — shown when last send failed (network / server error) */}
        {retryText && !loading && (
          <div style={{ textAlign: 'center', marginBottom: 12 }}>
            <button
              onClick={handleRetry}
              style={{
                padding: '9px 22px', borderRadius: 6, cursor: 'pointer',
                fontSize: 12, fontFamily: "'DM Mono', monospace", fontWeight: 700,
                background: `${color}18`, border: `1px solid ${color}66`,
                color, transition: 'all 0.15s',
              }}>
              ↺ Retry last message
            </button>
          </div>
        )}

        {/* Generate Documents button — only before approval AND when no follow-up cards are
            active/loading. We don't want the user to skip unanswered clarification rounds.
            v11: also hide while the most-recent assistant turn LOOKS like an elicitation
            round (question prose / "Please answer the N questions below" / pipe-option
            lines / q1. prefixed blocks). This catches the case where chip cards haven't
            landed in state yet but the AI is clearly still asking — no more premature
            "Generate Documents →" button. */}
        {(() => {
          // v13 — followUpCards / followUpSubmitted are now keyed by the AI
          // bubble's stable msg.id string, so drop the obsolete Number() cast.
          const anyCardsPending = Object.keys(followUpCards).some(
            k => !followUpSubmitted[k]
          );
          const cardsBlocking = followUpLoadingFor !== null || anyCardsPending;
          // Peek at the last assistant bubble — if it quacks like a question
          // round, the pipeline is still in elicitation mode regardless of
          // whether structured cards parsed this turn.
          // v12 FIX: ChatMessage uses {role:'user'|'ai', text}, NOT {role:'assistant', content}.
          // My v11 gate checked the wrong field names so it was always false.
          const lastAssistant = [...messages].reverse().find(m => m.role === 'ai');
          const stillEliciting = !!(lastAssistant && looksLikeFollowUpElicitation(lastAssistant.text));
          if (phaseCompleted || messages.length < 2 || loading || cardsBlocking || stillEliciting) return null;
          return (
            <div style={{ textAlign: 'center', marginBottom: 16 }}>
              <button
                onClick={finalizePhase}
                style={{
                  padding: '10px 24px', borderRadius: 6, cursor: 'pointer',
                  fontSize: 12, fontFamily: "'DM Mono', monospace", fontWeight: 600,
                  background: 'transparent', border: `1px solid ${color}55`,
                  color, transition: 'all 0.15s',
                }}
                onMouseEnter={e => { e.currentTarget.style.background = `${color}18`; }}
                onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}>
                Generate Documents →
              </button>
            </div>
          );
        })()}

        <div ref={bottomRef} />
      </div>

      {/* Sticky input area */}
      <div style={{
        position: 'sticky', bottom: 0, left: 0, right: 0,
        background: 'linear-gradient(transparent, var(--navy) 20%)',
        padding: '16px 0 4px',
      }}>
        <div style={{ display: 'flex', gap: 8 }}>
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(input); } }}
            placeholder="Describe your hardware requirement or ask anything..."
            rows={1}
            style={{
              flex: 1, background: 'var(--panel)', border: `1px solid var(--panel3)`,
              borderRadius: 6, padding: '11px 14px', fontSize: 13,
              color: 'var(--text)', fontFamily: "'DM Mono', monospace",
              resize: 'none', outline: 'none', lineHeight: 1.5,
              transition: 'border-color 0.2s',
            }}
            onFocus={e => { e.target.style.borderColor = color; }}
            onBlur={e => { e.target.style.borderColor = 'var(--panel3)'; }}
          />
          <button
            onClick={() => sendMessage(input)}
            disabled={!input.trim() || loading}
            style={{
              padding: '0 18px', borderRadius: 6, cursor: input.trim() && !loading ? 'pointer' : 'default',
              fontSize: 12, fontFamily: "'DM Mono', monospace", fontWeight: 700,
              background: input.trim() && !loading ? color : 'var(--panel2)',
              border: 'none',
              color: input.trim() && !loading ? '#070b14' : 'var(--text4)',
              transition: 'all 0.15s', whiteSpace: 'nowrap',
            }}>
            Send →
          </button>
        </div>
        {/* Build marker — lets us verify the latest bundle is live.
            Bump the version string whenever a clarify-card fix ships. */}
        {/* P26 #23 (2026-05-04): build banner hidden by user request.
            Build timestamp still useful for diagnostics \u2014 uncomment if
            you need to verify which bundle is running. */}
      </div>

      <style>{`
        @keyframes blink { 50% { opacity: 0; } }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
      `}</style>
        </>
      )}
    </div>
  );
}