import { useMemo, useState } from 'react';
import type { ProjectType, DesignScope } from '../types';

interface Props {
  /** Caller receives the picked values. The 5th argument (`design_scope`)
   *  is optional — when omitted the wizard's scope picker still fires
   *  inside ChatView. We pass it explicitly so the user's modal choice
   *  carries straight through to the backend. */
  onConfirm: (
    name: string,
    description: string,
    design_type: string,
    project_type: ProjectType,
    design_scope?: DesignScope,
  ) => void;
  onCancel: () => void;
}

/** Infer RF vs Digital from the project name — no need to ask the user.
 *  Power supplies / switch matrices default to 'rf' too because they
 *  routinely sit in RF instruments and the SRS template treats them
 *  the same way. */
function inferDesignType(name: string, ptype: ProjectType): string {
  if (ptype === 'power_supply') return 'digital'; // PSU is mostly mixed-signal but the agent treats it as digital
  if (ptype === 'switch_matrix') return 'rf';
  const text = name.toLowerCase();
  const rfKeywords = ['rf', 'radio', 'antenna', 'ghz', 'mhz', 'frequency', 'amplifier', 'pa ', 'lna',
    'filter', 'mixer', 'oscillator', 'transmit', 'receiv', 'wireless', 'ism', 'radar', 'microwave',
    'transceiver', 'sdr'];
  if (rfKeywords.some(k => text.includes(k))) return 'rf';
  return 'digital';
}

/**
 * Suggest a project type from the project name. The user can override
 * by clicking a different card. Tested in priority order — most specific
 * first (so "switch matrix" matches before "matrix" gets hijacked).
 *
 *  switch_matrix : "switch matrix", "crossbar", "spdt matrix", "ate matrix"
 *  power_supply  : "psu", "dc-dc", "ldo", "buck", "boost", "flyback",
 *                  "llc", "regulator", "power supply"
 *  transceiver   : "transceiver", "trx", "sdr trx", "tdd", "fdd", "duplex"
 *  transmitter   : "tx", "transmit", "uplink", " pa ", "pa chain",
 *                  "power amp", "driver amp", "upconvert", "exciter"
 *  receiver      : default
 */
function inferProjectType(name: string): ProjectType {
  const t = name.toLowerCase();
  if (['switch matrix','crossbar','spdt matrix','ate matrix','rf matrix',
       'sp4t','sp6t','sp8t','sp16t']
      .some(k => t.includes(k))) return 'switch_matrix';
  if (['psu','dc-dc','dc dc','ldo','buck','boost','flyback','llc',
       'regulator','power supply','smps','pfc','phase-shifted',
       'sepic']
      .some(k => t.includes(k))) return 'power_supply';
  if (['transceiver','trx','sdr trx','tdd','fdd','duplex','duplexer',
       'half-duplex','full-duplex']
      .some(k => t.includes(k))) return 'transceiver';
  const txKeywords = ['tx','transmit','uplink',' pa ','pa chain','power amp',
    'driver amp','driver amplifier','upconvert','exciter'];
  if (txKeywords.some(k => t.includes(k))) return 'transmitter';
  return 'receiver';
}

export default function CreateProjectModal({ onConfirm, onCancel }: Props) {
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(false);

  // Class + scope are inferred from the project name; the wizard refines
  // both inside ChatView. Keeping inference here so the backend still
  // gets a sensible project_type on creation.
  const effectiveType: ProjectType = useMemo(
    () => (name.trim() ? inferProjectType(name) : 'receiver'),
    [name],
  );

  const handleSubmit = async () => {
    if (!name.trim() || loading) return;
    setLoading(true);
    try {
      const dtype = inferDesignType(name, effectiveType);
      // Scope defaults to 'full'; user narrows it in the wizard.
      const scope: DesignScope = 'full';
      await onConfirm(name.trim(), '', dtype, effectiveType, scope);
    } finally {
      setLoading(false);
    }
  };

  const inputStyle = {
    width: '100%', background: 'var(--panel2)', border: '1px solid var(--panel3)',
    borderRadius: 5, padding: '10px 13px', fontSize: 13,
    color: 'var(--text)', fontFamily: "'DM Mono', monospace",
    transition: 'border-color 0.2s', outline: 'none', boxSizing: 'border-box' as const,
  };
  const labelStyle = {
    fontSize: 10, color: 'var(--text3)', letterSpacing: '0.12em', marginBottom: 6, display: 'block',
  };

  // Colors used to tint each project type card (matches the iris
  // palette so the modal feels consistent with the dashboard).
  const TYPE_TINT: Record<ProjectType, string> = {
    receiver:      '#00c6a7',   // teal — matches existing P1 brand
    transmitter:   '#3b82f6',   // blue
    transceiver:   '#b388ff',   // iris-a
    power_supply:  '#ffc65c',   // iris-d (amber)
    switch_matrix: '#ff5ca8',   // iris-b (pink)
  };

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(7,11,20,0.88)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999,
      overflow: 'auto', padding: '40px 20px',
    }}>
      <div style={{
        background: 'var(--panel)', border: '1px solid var(--panel2)',
        borderRadius: 12, padding: 30, width: 720, maxWidth: '100%',
        boxShadow: '0 24px 60px rgba(0,0,0,0.7)',
      }}>
        <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 19, fontWeight: 800, marginBottom: 6, color: 'var(--text)' }}>
          New Project
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 22 }}>
          Give your project a name — class and scope are inferred from the name and refined inside the wizard.
        </div>

        {/* ── NAME (sole input — class/scope inferred + refined in wizard) ── */}
        <div style={{ marginBottom: 22 }}>
          <label style={labelStyle}>PROJECT NAME <span style={{ color: 'var(--teal)' }}>*</span></label>
          <input
            style={inputStyle}
            placeholder={
              effectiveType === 'switch_matrix' ? 'e.g. 4×8 SPDT ATE matrix · 16×16 non-blocking crossbar'
              : effectiveType === 'power_supply' ? 'e.g. 12V → 3.3V 10A buck · ±15V dual LDO · 48V LLC brick'
              : effectiveType === 'transceiver' ? 'e.g. SDR TRX 70 MHz - 6 GHz · TDD half-duplex link'
              : effectiveType === 'transmitter' ? 'e.g. 2.4 GHz 10 W PA chain · S-band radar TX'
              : 'e.g. 6-18 GHz wideband receiver · X-band radar RX · Ku-band SATCOM'
            }
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) handleSubmit(); }}
            autoFocus
          />
          <div style={{ fontSize: 10, color: 'var(--text4)', marginTop: 6, fontFamily: "'DM Mono', monospace" }}>
            Naming hint: include band / topology / power so the agent can pick the right components.
          </div>
        </div>

        {/* ── Actions ──────────────────────────────────────────────── */}
        <div style={{ display: 'flex', gap: 10 }}>
          <button onClick={onCancel} style={{
            flex: 1, padding: '11px 0', borderRadius: 6, cursor: 'pointer',
            fontSize: 12, fontFamily: "'DM Mono', monospace",
            background: 'transparent', border: '1px solid var(--panel3)',
            color: 'var(--text3)', transition: 'all 0.15s',
          }}>
            Cancel
          </button>
          <button onClick={handleSubmit} disabled={!name.trim() || loading} style={{
            flex: 2, padding: '11px 0', borderRadius: 6,
            cursor: name.trim() && !loading ? 'pointer' : 'default',
            fontSize: 12, fontFamily: "'DM Mono', monospace", fontWeight: 600,
            background: name.trim() && !loading
              ? `linear-gradient(92deg, ${TYPE_TINT[effectiveType]}, ${TYPE_TINT[effectiveType]}cc)`
              : 'var(--panel2)',
            border: 'none',
            color: name.trim() && !loading ? '#0a0216' : 'var(--text4)',
            transition: 'all 0.15s', letterSpacing: '0.04em',
          }}>
            {loading ? 'Creating…' : 'CREATE PROJECT →'}
          </button>
        </div>
      </div>
    </div>
  );
}
