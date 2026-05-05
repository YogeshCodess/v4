import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

interface Props { children: ReactNode; }
interface State { error: Error | null; info: string; }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: '' };

  static getDerivedStateFromError(error: Error): State {
    return { error, info: '' };
  }

  componentDidCatch(_error: Error, info: ErrorInfo) {
    this.setState({ info: info.componentStack || '' });
    console.error('[ErrorBoundary] Render error caught:', _error, info);
  }

  render() {
    const { error, info } = this.state;
    if (!error) return this.props.children;

    return (
      <div style={{
        minHeight: '100vh', background: '#070b14',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: "'DM Mono', monospace", padding: 32,
      }}>
        <div style={{
          maxWidth: 640, width: '100%',
          background: '#1a2235', border: '1px solid rgba(220,38,38,0.4)',
          borderRadius: 12, padding: '32px 36px',
        }}>
          {/* Header */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
            <div style={{
              width: 36, height: 36, borderRadius: 8,
              background: 'rgba(220,38,38,0.12)', border: '1px solid rgba(220,38,38,0.3)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 18,
            }}>⚠</div>
            <div>
              <div style={{ fontSize: 15, fontWeight: 700, color: '#e2e8f0', fontFamily: "'Syne', sans-serif" }}>
                Something went wrong
              </div>
              <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
                A render error was caught — your project data is safe
              </div>
            </div>
          </div>

          {/* Error message */}
          <div style={{
            background: 'rgba(220,38,38,0.07)', border: '1px solid rgba(220,38,38,0.2)',
            borderRadius: 6, padding: '10px 14px', marginBottom: 16,
            fontSize: 12, color: '#fca5a5', wordBreak: 'break-word',
          }}>
            {error.message || String(error)}
          </div>

          {/* Stack hint */}
          {info && (
            <details style={{ marginBottom: 20 }}>
              <summary style={{ fontSize: 11, color: '#475569', cursor: 'pointer', marginBottom: 6 }}>
                Component stack
              </summary>
              <pre style={{
                fontSize: 10, color: '#475569', lineHeight: 1.6,
                background: '#0d1524', borderRadius: 4, padding: '8px 12px',
                overflow: 'auto', maxHeight: 160, whiteSpace: 'pre-wrap',
              }}>
                {info.trim()}
              </pre>
            </details>
          )}

          {/* Actions */}
          <div style={{ display: 'flex', gap: 10 }}>
            <button
              onClick={() => this.setState({ error: null, info: '' })}
              style={{
                padding: '8px 18px', borderRadius: 6, cursor: 'pointer',
                background: '#00c6a7', border: 'none', color: '#070b14',
                fontSize: 12, fontWeight: 700, fontFamily: "'DM Mono', monospace",
              }}
            >
              ↺ Retry
            </button>
            <button
              onClick={() => window.location.reload()}
              style={{
                padding: '8px 18px', borderRadius: 6, cursor: 'pointer',
                background: 'transparent', border: '1px solid #2a3a50',
                color: '#94a3b8', fontSize: 12, fontFamily: "'DM Mono', monospace",
              }}
            >
              ⟳ Reload page
            </button>
          </div>
        </div>
      </div>
    );
  }
}
