import { useState, useEffect } from 'react';
import type { Project } from '../types';
import { api } from '../api';

interface Props {
  onSelect: (p: Project) => void;
  onCancel: () => void;
}

export default function LoadProjectModal({ onSelect, onCancel }: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [connErr, setConnErr] = useState(false);

  useEffect(() => {
    api.listProjects()
      .then(ps => { setProjects(ps); setLoading(false); })
      .catch(() => { setLoading(false); setConnErr(true); });
  }, []);

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(7,11,20,0.88)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
    }}>
      <div style={{
        background: 'var(--panel)', border: '1px solid var(--panel2)',
        borderRadius: 10, padding: 30, width: 500, maxHeight: '78vh',
        display: 'flex', flexDirection: 'column',
        boxShadow: '0 24px 60px rgba(0,0,0,0.7)',
      }}>
        <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 17, fontWeight: 800, marginBottom: 5 }}>
          Load Project
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 18 }}>
          Continue from last completed phase
        </div>

        <div style={{ flex: 1, overflowY: 'auto' }}>
          {loading && (
            <div style={{ fontSize: 13, color: 'var(--text4)', textAlign: 'center', padding: 28 }}>Loading...</div>
          )}
          {!loading && connErr && (
            <div style={{ fontSize: 13, color: 'var(--danger)', textAlign: 'center', padding: 24, lineHeight: 1.6 }}>
              ⚠️ <strong>Cannot reach the backend server.</strong><br />
              Double-click <code>run.bat</code> (or <code>INSTALL.bat</code> on first use)<br />
              in the project folder, then try again.
            </div>
          )}
          {!loading && !connErr && !projects.length && (
            <div style={{ fontSize: 13, color: 'var(--text4)', textAlign: 'center', padding: 28 }}>
              No saved projects. Create one first.
            </div>
          )}
          {projects.map(p => (
            <div key={p.id} onClick={() => onSelect(p)} style={{
              background: 'var(--panel2)', border: '1px solid var(--panel3)',
              borderRadius: 8, padding: '13px 14px', marginBottom: 9,
              cursor: 'pointer', transition: 'border-color 0.15s',
            }}
              onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--teal)')}
              onMouseLeave={e => (e.currentTarget.style.borderColor = 'var(--panel2)')}
            >
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 3 }}>{p.name}</div>
              {p.description && (
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 4 }}>{p.description}</div>
              )}
              <div style={{ fontSize: 11, color: 'var(--text4)' }}>
                {p.design_type?.toUpperCase()} &middot; {p.created_at ? new Date(p.created_at).toLocaleDateString() : ''}
              </div>
            </div>
          ))}
        </div>

        <button onClick={onCancel} style={{
          marginTop: 16, padding: '10px 0', borderRadius: 5, cursor: 'pointer',
          fontSize: 12, fontFamily: "'DM Mono', monospace",
          background: 'transparent', border: '1px solid var(--panel3)',
          color: 'var(--text3)',
        }}>
          Cancel
        </button>
      </div>
    </div>
  );
}
