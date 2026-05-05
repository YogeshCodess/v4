/**
 * Scoped error boundary for Mermaid diagrams — degrades gracefully to a
 * source-code panel when Mermaid's internals throw.
 *
 * Why we need it beyond the per-render try/catch already in `MermaidBlock`
 * components:
 *   - Mermaid 10.x can throw synchronously during the React render commit
 *     phase (e.g. when the svg string it returns has escape artefacts that
 *     React then refuses to reconcile). Those errors bypass the async
 *     `try { renderMermaid() } catch` around the render call and crash the
 *     whole component tree above the diagram if not caught here.
 *   - `xBt[a.shape] is not a function` — a specific Mermaid-10.6.1 crash
 *     seen when the input contains a shape the library's internal shape
 *     table doesn't know — shows up *after* our catch block, during DOM
 *     commit. Wrapping every `<MermaidBlock>` in this boundary turns what
 *     used to be a "white screen of death" into a readable source dump.
 *
 * This boundary is intentionally LOCAL (one per diagram). The top-level
 * `ErrorBoundary` is for full-app crashes and would hide the rest of the
 * document — we want the user to still see the text parts of the page.
 */
import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

interface Props {
  children: ReactNode;
  /** Raw Mermaid source to show if rendering blew up. */
  source: string;
  /** Phase accent colour. */
  color?: string;
  /** Short label for the fallback header (e.g. "BLOCK DIAGRAM"). */
  label?: string;
}

interface State { err: Error | null; }

export default class MermaidErrorBoundary extends Component<Props, State> {
  state: State = { err: null };

  static getDerivedStateFromError(err: Error): State {
    return { err };
  }

  componentDidCatch(err: Error, info: ErrorInfo) {
    // Log once so ops can grep for regression patterns. Don't propagate.
    console.warn('[MermaidErrorBoundary] caught:', err.message, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    // When the source changes (user navigated to a different phase or a
    // salvaged version landed), reset so we try to render again.
    if (prev.source !== this.props.source && this.state.err) {
      this.setState({ err: null });
    }
  }

  render() {
    const { err } = this.state;
    if (!err) return this.props.children;

    const color = this.props.color || '#f59e0b';
    const label = this.props.label || 'DIAGRAM';
    const msg = (err.message || String(err)).slice(0, 160);

    return (
      <div style={{ margin: '10px 0' }}>
        <div style={{
          fontSize: 10, color, letterSpacing: '0.08em',
          background: `${color}0d`, padding: '4px 12px',
          borderRadius: '6px 6px 0 0', border: `1px solid ${color}40`,
          borderBottom: 'none', fontFamily: "'DM Mono', monospace",
        }}>
          &#9888; {label} SOURCE &mdash; render crashed ({msg})
        </div>
        <pre style={{
          background: 'var(--panel2)',
          border: `1px solid ${color}40`,
          borderRadius: '0 0 6px 6px',
          padding: '12px 14px',
          margin: 0,
          fontSize: 11,
          color: 'var(--text3)',
          fontFamily: "'JetBrains Mono', monospace",
          overflowX: 'auto',
          lineHeight: 1.65,
          whiteSpace: 'pre-wrap',
        }}>
          {this.props.source}
        </pre>
      </div>
    );
  }
}
