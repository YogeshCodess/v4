interface Props { message: string; }

export default function Toast({ message }: Props) {
  return (
    <div style={{
      position: 'fixed', bottom: 28, left: '50%', transform: 'translateX(-50%)',
      background: 'var(--panel)', border: '1px solid var(--panel3)',
      borderRadius: 8, padding: '10px 22px',
      fontSize: 13, color: 'var(--text)', fontFamily: "'DM Mono', monospace",
      zIndex: 1000, boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
      animation: 'fadeUp 0.2s ease',
    }}>
      {message}
    </div>
  );
}
