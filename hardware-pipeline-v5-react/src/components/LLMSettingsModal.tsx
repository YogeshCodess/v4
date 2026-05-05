import { useState, useEffect } from 'react';

interface ConfigSettings {
  glm_api_key?: string;
  deepseek_api_key?: string;
  anthropic_api_key?: string;
  glm_base_url?: string;
  deepseek_base_url?: string;
  primary_model?: string;
  fast_model?: string;
  github_token?: string;
  github_repo?: string;
  digikey_client_id?: string;
  digikey_client_secret?: string;
  mouser_api_key?: string;
  chroma_persist_dir?: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  onSave: (settings: ConfigSettings) => Promise<void>;
}

const DEFAULT_SETTINGS: ConfigSettings = {
  glm_api_key: '',
  deepseek_api_key: '',
  anthropic_api_key: '',
  glm_base_url: 'https://api.z.ai/api/anthropic',
  deepseek_base_url: 'https://api.deepseek.com',
  primary_model: '',
  fast_model: '',
  github_token: '',
  github_repo: '',
  digikey_client_id: '',
  digikey_client_secret: '',
  mouser_api_key: '',
  chroma_persist_dir: './chroma_data',
};

type Tab = 'llm' | 'git' | 'components';

export default function LLMSettingsModal({ open, onClose, onSave }: Props) {
  const [settings, setSettings] = useState<ConfigSettings>(DEFAULT_SETTINGS);
  const [activeTab, setActiveTab] = useState<Tab>('llm');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [showKeys, setShowKeys] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const [gitEnabled, setGitEnabled] = useState(false);

  useEffect(() => {
    if (open) {
      loadSettings();
      setActiveTab('llm');
    }
  }, [open]);

  const loadSettings = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch('/api/v1/settings/llm');
      if (!res.ok) throw new Error('Failed to load settings');
      const data = await res.json();
      setSettings({
        glm_api_key: data.glm_api_key || '',
        deepseek_api_key: data.deepseek_api_key || '',
        anthropic_api_key: data.anthropic_api_key || '',
        glm_base_url: data.glm_base_url || 'https://api.z.ai/api/anthropic',
        deepseek_base_url: data.deepseek_base_url || 'https://api.deepseek.com',
        primary_model: data.primary_model || '',
        fast_model: data.fast_model || '',
        github_token: data.github_token || '',
        github_repo: data.github_repo || '',
        digikey_client_id: data.digikey_client_id || '',
        digikey_client_secret: data.digikey_client_secret || '',
        mouser_api_key: data.mouser_api_key || '',
        chroma_persist_dir: data.chroma_persist_dir || './chroma_data',
      });
      setGitEnabled(data.git_enabled || false);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError('');
    setSuccess(false);
    try {
      await onSave(settings);
      setSuccess(true);
      setGitEnabled(Boolean(settings.github_token?.replace(/•/g, '')));
      setTimeout(() => { onClose(); setSuccess(false); }, 1500);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const monoFont = '"DM Mono", monospace';
  const syneFont = '"Syne", sans-serif';

  const inputStyle: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box', padding: '9px 12px', borderRadius: 6,
    background: 'var(--panel2)', border: '1px solid var(--border2)',
    color: 'var(--text)', fontSize: 12, fontFamily: monoFont, outline: 'none',
  };

  const labelStyle: React.CSSProperties = {
    display: 'block', fontSize: 12, color: 'var(--text2)', marginBottom: 5, fontWeight: 500,
  };

  const sectionHeaderStyle: React.CSSProperties = {
    fontSize: 10, color: 'var(--text4)', letterSpacing: '0.12em',
    marginBottom: 12, fontFamily: monoFont, textTransform: 'uppercase' as const,
  };

  const fieldWrap: React.CSSProperties = { marginBottom: 14 };
  const hintStyle: React.CSSProperties = { fontSize: 10, color: 'var(--text4)', marginTop: 4 };

  if (!open) return null;

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.65)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 1000,
    }}>
      <div style={{
        background: 'var(--panel)', border: '1px solid var(--border2)',
        borderRadius: 10, width: '90%', maxWidth: 600,
        maxHeight: '88vh', overflow: 'hidden', display: 'flex', flexDirection: 'column',
        boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
      }}>

        {/* Header */}
        <div style={{
          padding: '18px 22px', borderBottom: '1px solid var(--border2)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          flexShrink: 0,
        }}>
          <div>
            <div style={{ fontFamily: syneFont, fontSize: 18, fontWeight: 700, color: 'var(--text)' }}>
              Configuration
            </div>
            <div style={{ fontSize: 11, color: 'var(--text4)', marginTop: 3, fontFamily: monoFont }}>
              LLM models · GitHub integration
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              width: 30, height: 30, borderRadius: 6, border: '1px solid var(--border2)',
              background: 'transparent', color: 'var(--text3)', cursor: 'pointer',
              fontSize: 18, display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
            onMouseEnter={e => { e.currentTarget.style.background = 'var(--panel2)'; }}
            onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
          >
            ×
          </button>
        </div>

        {/* Tabs */}
        <div style={{
          display: 'flex', borderBottom: '1px solid var(--border2)',
          background: 'var(--panel)', flexShrink: 0,
        }}>
          {([['llm', '⚡ LLM Models'], ['git', '⎇ GitHub Integration'], ['components', '◈ Components']] as [Tab, string][]).map(([id, label]) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              style={{
                padding: '10px 20px', fontSize: 12, fontFamily: monoFont,
                border: 'none', background: 'transparent', cursor: 'pointer',
                color: activeTab === id ? 'var(--teal)' : 'var(--text3)',
                borderBottom: activeTab === id ? '2px solid var(--teal)' : '2px solid transparent',
                fontWeight: activeTab === id ? 600 : 400,
                transition: 'all 0.15s',
              }}
            >
              {label}
              {id === 'git' && (
                <span style={{
                  marginLeft: 6, fontSize: 9, padding: '1px 5px', borderRadius: 3,
                  background: gitEnabled ? 'rgba(0,198,167,0.15)' : 'rgba(100,116,139,0.2)',
                  color: gitEnabled ? 'var(--teal)' : 'var(--text4)',
                  fontWeight: 700,
                }}>
                  {gitEnabled ? 'ON' : 'OFF'}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Content */}
        <div style={{ padding: '20px 22px', overflowY: 'auto', flex: 1 }}>
          {loading ? (
            <div style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
              Loading settings…
            </div>
          ) : (
            <>
              {error && (
                <div style={{
                  padding: '10px 14px', borderRadius: 6, background: 'rgba(220,38,38,0.12)',
                  border: '1px solid rgba(220,38,38,0.35)', color: '#dc2626',
                  fontSize: 12, marginBottom: 16, fontFamily: monoFont,
                }}>⚠ {error}</div>
              )}
              {success && (
                <div style={{
                  padding: '10px 14px', borderRadius: 6, background: 'rgba(0,198,167,0.12)',
                  border: '1px solid rgba(0,198,167,0.35)', color: 'var(--teal)',
                  fontSize: 12, marginBottom: 16, fontFamily: monoFont,
                }}>✓ Settings saved and written to .env</div>
              )}

              {/* ── LLM Tab ── */}
              {activeTab === 'llm' && (
                <>
                  <div style={sectionHeaderStyle}>API Keys</div>

                  {/* GLM */}
                  <div style={fieldWrap}>
                    <label style={labelStyle}>GLM API Key <span style={{ color: 'var(--teal)', fontSize: 10 }}>(Primary)</span></label>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <input
                        type={showKeys ? 'text' : 'password'}
                        value={settings.glm_api_key}
                        onChange={e => setSettings({ ...settings, glm_api_key: e.target.value })}
                        placeholder="Enter GLM API key"
                        style={{ ...inputStyle, flex: 1 }}
                      />
                      <button
                        onClick={() => setShowKeys(!showKeys)}
                        style={{
                          padding: '8px 12px', borderRadius: 6, border: '1px solid var(--border2)',
                          background: 'var(--panel2)', color: 'var(--text3)', cursor: 'pointer',
                          fontSize: 11, fontFamily: monoFont, whiteSpace: 'nowrap' as const,
                        }}
                      >{showKeys ? '🙈 Hide' : '👁 Show'}</button>
                    </div>
                    <div style={hintStyle}>
                      Get key at{' '}
                      <a href="https://open.bigmodel.cn" target="_blank" rel="noopener" style={{ color: 'var(--teal)' }}>open.bigmodel.cn</a>
                      {' '}or{' '}
                      <a href="https://api.z.ai" target="_blank" rel="noopener" style={{ color: 'var(--teal)' }}>api.z.ai</a>
                    </div>
                  </div>

                  {/* DeepSeek */}
                  <div style={fieldWrap}>
                    <label style={labelStyle}>DeepSeek API Key <span style={{ color: 'var(--text4)', fontSize: 10 }}>(Fallback)</span></label>
                    <input
                      type={showKeys ? 'text' : 'password'}
                      value={settings.deepseek_api_key}
                      onChange={e => setSettings({ ...settings, deepseek_api_key: e.target.value })}
                      placeholder="Enter DeepSeek API key"
                      style={inputStyle}
                    />
                    <div style={hintStyle}>
                      <a href="https://platform.deepseek.com" target="_blank" rel="noopener" style={{ color: 'var(--teal)' }}>platform.deepseek.com</a>
                    </div>
                  </div>

                  {/* Anthropic */}
                  <div style={fieldWrap}>
                    <label style={labelStyle}>Anthropic API Key <span style={{ color: 'var(--text4)', fontSize: 10 }}>(Optional)</span></label>
                    <input
                      type={showKeys ? 'text' : 'password'}
                      value={settings.anthropic_api_key}
                      onChange={e => setSettings({ ...settings, anthropic_api_key: e.target.value })}
                      placeholder="Enter Anthropic API key"
                      style={inputStyle}
                    />
                  </div>

                  <div style={{ marginTop: 20, marginBottom: 12, ...sectionHeaderStyle }}>Advanced</div>

                  {/* GLM Base URL */}
                  <div style={fieldWrap}>
                    <label style={labelStyle}>GLM Base URL</label>
                    <input
                      type="text"
                      value={settings.glm_base_url}
                      onChange={e => setSettings({ ...settings, glm_base_url: e.target.value })}
                      style={inputStyle}
                    />
                  </div>

                  {/* Primary Model Override */}
                  <div style={fieldWrap}>
                    <label style={labelStyle}>Primary Model Override</label>
                    <input
                      type="text"
                      value={settings.primary_model}
                      onChange={e => setSettings({ ...settings, primary_model: e.target.value })}
                      placeholder="e.g. glm-4.7  (blank = auto)"
                      style={inputStyle}
                    />
                  </div>

                  {/* Fast Model Override */}
                  <div style={fieldWrap}>
                    <label style={labelStyle}>Fast Model Override</label>
                    <input
                      type="text"
                      value={settings.fast_model}
                      onChange={e => setSettings({ ...settings, fast_model: e.target.value })}
                      placeholder="e.g. glm-4.5-air  (blank = auto)"
                      style={inputStyle}
                    />
                  </div>

                  {/* Priority info */}
                  <div style={{
                    padding: 12, borderRadius: 6, background: 'rgba(0,198,167,0.06)',
                    border: '1px solid rgba(0,198,167,0.2)', fontSize: 11,
                    color: 'var(--text2)', lineHeight: 1.7,
                  }}>
                    <div style={{ fontWeight: 600, marginBottom: 4, color: 'var(--teal)' }}>ℹ Auto-Selection Order</div>
                    GLM (Z.AI) → DeepSeek → Anthropic → Ollama (local)<br/>
                    Override with explicit model names above if needed.
                  </div>
                </>
              )}

              {/* ── Components Tab ── */}
              {activeTab === 'components' && (
                <>
                  <div style={sectionHeaderStyle}>DigiKey API</div>

                  <div style={fieldWrap}>
                    <label style={labelStyle}>Client ID</label>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <input
                        type={showKeys ? 'text' : 'password'}
                        value={settings.digikey_client_id}
                        onChange={e => setSettings({ ...settings, digikey_client_id: e.target.value })}
                        placeholder="DigiKey Client ID"
                        style={{ ...inputStyle, flex: 1 }}
                      />
                      <button
                        onClick={() => setShowKeys(!showKeys)}
                        style={{
                          padding: '8px 12px', borderRadius: 6, border: '1px solid var(--border2)',
                          background: 'var(--panel2)', color: 'var(--text3)', cursor: 'pointer',
                          fontSize: 11, fontFamily: monoFont, whiteSpace: 'nowrap' as const,
                        }}
                      >{showKeys ? '🙈 Hide' : '👁 Show'}</button>
                    </div>
                  </div>

                  <div style={fieldWrap}>
                    <label style={labelStyle}>Client Secret</label>
                    <input
                      type={showKeys ? 'text' : 'password'}
                      value={settings.digikey_client_secret}
                      onChange={e => setSettings({ ...settings, digikey_client_secret: e.target.value })}
                      placeholder="DigiKey Client Secret"
                      style={inputStyle}
                    />
                    <div style={hintStyle}>
                      Register at{' '}
                      <a href="https://developer.digikey.com" target="_blank" rel="noopener" style={{ color: 'var(--teal)' }}>developer.digikey.com</a>
                      {' '}— enables real-time BOM pricing &amp; availability
                    </div>
                  </div>

                  <div style={{ marginTop: 20, marginBottom: 12, ...sectionHeaderStyle }}>Mouser API</div>

                  <div style={fieldWrap}>
                    <label style={labelStyle}>API Key</label>
                    <input
                      type={showKeys ? 'text' : 'password'}
                      value={settings.mouser_api_key}
                      onChange={e => setSettings({ ...settings, mouser_api_key: e.target.value })}
                      placeholder="Mouser API Key"
                      style={inputStyle}
                    />
                    <div style={hintStyle}>
                      Register at{' '}
                      <a href="https://www.mouser.com/api-hub" target="_blank" rel="noopener" style={{ color: 'var(--teal)' }}>mouser.com/api-hub</a>
                    </div>
                  </div>

                  <div style={{ marginTop: 20, marginBottom: 12, ...sectionHeaderStyle }}>ChromaDB Vector Search</div>

                  <div style={fieldWrap}>
                    <label style={labelStyle}>Persist Directory</label>
                    <input
                      type="text"
                      value={settings.chroma_persist_dir}
                      onChange={e => setSettings({ ...settings, chroma_persist_dir: e.target.value })}
                      placeholder="./chroma_data"
                      style={inputStyle}
                    />
                    <div style={hintStyle}>
                      Local path where component datasheet embeddings are stored
                    </div>
                  </div>

                  <div style={{
                    padding: 12, borderRadius: 6, background: 'rgba(245,158,11,0.06)',
                    border: '1px solid rgba(245,158,11,0.2)', fontSize: 11,
                    color: 'var(--text2)', lineHeight: 1.7, marginTop: 8,
                  }}>
                    <div style={{ fontWeight: 600, marginBottom: 4, color: '#f59e0b' }}>ℹ Component Search</div>
                    DigiKey + Mouser APIs provide live pricing and stock data during BOM generation.
                    ChromaDB stores datasheet embeddings for semantic component search.
                    All are optional — the LLM will use its training knowledge as fallback.
                  </div>
                </>
              )}

              {/* ── Git Tab ── */}
              {activeTab === 'git' && (
                <>
                  {/* Status banner */}
                  <div style={{
                    padding: '10px 14px', borderRadius: 6, marginBottom: 20,
                    background: gitEnabled ? 'rgba(0,198,167,0.08)' : 'rgba(100,116,139,0.08)',
                    border: `1px solid ${gitEnabled ? 'rgba(0,198,167,0.3)' : 'rgba(100,116,139,0.3)'}`,
                    color: gitEnabled ? 'var(--teal)' : 'var(--text3)',
                    fontSize: 12, fontFamily: monoFont,
                  }}>
                    {gitEnabled
                      ? '✓ Git integration active — P8c will auto-commit and open a GitHub PR'
                      : '○ Git integration inactive — set your GitHub token to enable'}
                  </div>

                  <div style={sectionHeaderStyle}>GitHub Credentials</div>

                  {/* GitHub Token */}
                  <div style={fieldWrap}>
                    <label style={labelStyle}>GitHub Personal Access Token</label>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <input
                        type={showKeys ? 'text' : 'password'}
                        value={settings.github_token}
                        onChange={e => setSettings({ ...settings, github_token: e.target.value })}
                        placeholder="ghp_xxxxxxxxxxxxxxxxxxxx"
                        style={{ ...inputStyle, flex: 1 }}
                      />
                      <button
                        onClick={() => setShowKeys(!showKeys)}
                        style={{
                          padding: '8px 12px', borderRadius: 6, border: '1px solid var(--border2)',
                          background: 'var(--panel2)', color: 'var(--text3)', cursor: 'pointer',
                          fontSize: 11, fontFamily: monoFont, whiteSpace: 'nowrap' as const,
                        }}
                      >{showKeys ? '🙈 Hide' : '👁 Show'}</button>
                    </div>
                    <div style={hintStyle}>
                      Create at{' '}
                      <a href="https://github.com/settings/tokens/new" target="_blank" rel="noopener" style={{ color: 'var(--teal)' }}>
                        github.com/settings/tokens
                      </a>
                      {' '}— needs <strong style={{ color: 'var(--text2)' }}>Contents (write)</strong> + <strong style={{ color: 'var(--text2)' }}>Pull requests (write)</strong>
                    </div>
                  </div>

                  {/* GitHub Repo */}
                  <div style={fieldWrap}>
                    <label style={labelStyle}>Repository <span style={{ color: 'var(--text4)', fontWeight: 400 }}>(owner/repo)</span></label>
                    <input
                      type="text"
                      value={settings.github_repo}
                      onChange={e => setSettings({ ...settings, github_repo: e.target.value })}
                      placeholder="e.g. bala9066/AI_S2S_Code"
                      style={inputStyle}
                    />
                    <div style={hintStyle}>Generated artefacts are committed here after P8c completes</div>
                  </div>

                  {/* How it works */}
                  <div style={{
                    padding: 14, borderRadius: 6, background: 'rgba(59,130,246,0.06)',
                    border: '1px solid rgba(59,130,246,0.2)', fontSize: 11,
                    color: 'var(--text2)', lineHeight: 1.8, marginTop: 8,
                  }}>
                    <div style={{ fontWeight: 600, marginBottom: 6, color: '#3b82f6' }}>⎇ How it works</div>
                    After P8c (Code Review) completes, the pipeline automatically:<br/>
                    1. Creates a feature branch <span style={{ fontFamily: monoFont, color: 'var(--text)', background: 'var(--panel2)', padding: '1px 5px', borderRadius: 3 }}>ai/&lt;project&gt;-p8c-&lt;date&gt;</span><br/>
                    2. Commits all generated artefacts (requirements, HRS, netlist, code, review)<br/>
                    3. Opens a GitHub PR with the full review summary<br/>
                    No manual git steps needed.
                  </div>
                </>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: '14px 22px', borderTop: '1px solid var(--border2)',
          display: 'flex', gap: 10, justifyContent: 'flex-end', flexShrink: 0,
        }}>
          <button
            onClick={onClose}
            disabled={saving}
            style={{
              padding: '9px 20px', borderRadius: 6, border: '1px solid var(--border2)',
              background: 'transparent', color: 'var(--text2)',
              cursor: saving ? 'not-allowed' : 'pointer',
              fontSize: 12, fontFamily: monoFont, fontWeight: 500,
            }}
            onMouseEnter={e => { if (!saving) e.currentTarget.style.background = 'var(--panel2)'; }}
            onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            style={{
              padding: '9px 20px', borderRadius: 6, border: 'none',
              background: saving ? 'var(--panel3)' : 'var(--teal)',
              color: saving ? 'var(--text4)' : '#070b14',
              cursor: saving ? 'not-allowed' : 'pointer',
              fontSize: 12, fontFamily: monoFont, fontWeight: 600,
              opacity: saving ? 0.6 : 1, transition: 'all 0.15s',
            }}
          >
            {saving ? 'Saving…' : '✓ Save to .env'}
          </button>
        </div>
      </div>
    </div>
  );
}
