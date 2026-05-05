/**
 * Mermaid loader — LOCAL-FIRST (no CDN dependency during demo).
 *
 * Load order:
 *   1. http://localhost:8000/static/mermaid.min.js  (served by FastAPI — instant)
 *   2. https://cdn.jsdelivr.net/...                 (fallback if backend not yet running)
 *
 * This eliminates the slow/blocked CDN fetch that was causing render failures
 * on corporate / WebVPN networks.
 *
 * initialize() is called exactly ONCE via the _promise guard.
 */

declare global {
  interface Window {
    mermaid?: {
      initialize: (cfg: object) => void;
      render: (id: string, code: string) => Promise<{ svg: string }>;
      parse: (code: string) => Promise<unknown>;
    };
  }
}

// Local backend first — falls back to CDN only if the backend isn't running
const MERMAID_LOCAL = 'http://localhost:8000/static/mermaid.min.js';
const MERMAID_CDN   = 'https://cdn.jsdelivr.net/npm/mermaid@10.6.1/dist/mermaid.min.js';

const MERMAID_CONFIG = {
  startOnLoad: false,
  securityLevel: 'loose' as const,
  theme: 'base' as const,
  logLevel: 5,
  suppressErrorRendering: true,
  themeVariables: {
    background:          '#0a1628',
    mainBkg:             '#1a2235',
    primaryColor:        '#1e2d42',
    primaryBorderColor:  '#00c6a7',
    primaryTextColor:    '#e2e8f0',
    nodeTextColor:       '#e2e8f0',
    lineColor:           '#00c6a7',
    edgeLabelBackground: '#0f1e33',
    clusterBkg:          '#0d1423',
    clusterBorder:       '#2a3a50',
    secondaryColor:      '#152033',
    tertiaryColor:       '#0d1423',
    tertiaryBorderColor: '#2a3a50',
    noteBkgColor:        '#1e2d42',
    noteTextColor:       '#94a3b8',
    noteBorderColor:     '#2a3a50',
    activationBorderColor: '#00c6a7',
    activationBkgColor:  '#1e2d42',
    fontFamily:          "'DM Mono', monospace",
    fontSize:            '13px',
  },
};

type Callback = () => void;

let _state: 'idle' | 'loading' | 'ready' | 'failed' = 'idle';
let _promise: Promise<void> | null = null;
const _callbacks: Callback[] = [];

function _loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.onload  = () => resolve();
    s.onerror = () => reject(new Error(`Failed to load: ${src}`));
    document.head.appendChild(s);
  });
}

/** Returns a promise that resolves when window.mermaid is ready. */
export function loadMermaid(): Promise<void> {
  if (_state === 'ready' && window.mermaid) return Promise.resolve();
  if (_promise) return _promise;

  // If already loaded externally (HMR), adopt it
  if (window.mermaid) {
    window.mermaid.initialize(MERMAID_CONFIG);
    _state = 'ready';
    return Promise.resolve();
  }

  _state = 'loading';
  _promise = (async () => {
    // Try local backend first (fast, no internet needed)
    try {
      await _loadScript(MERMAID_LOCAL);
    } catch {
      // Backend not running or static/ not mounted — fall back to CDN
      console.warn('[mermaid] local file not available, trying CDN...');
      await _loadScript(MERMAID_CDN);
    }

    if (!window.mermaid) {
      _state = 'failed';
      _promise = null;
      throw new Error('Mermaid loaded but window.mermaid is undefined');
    }

    window.mermaid.initialize(MERMAID_CONFIG);
    _state = 'ready';
    _callbacks.forEach(cb => cb());
    _callbacks.length = 0;
  })();

  _promise.catch(() => {
    _state = 'failed';
    _promise = null;
  });

  return _promise;
}

/**
 * Render a Mermaid diagram string → SVG string.
 * Throws on parse/render error.
 */
export async function renderMermaid(id: string, code: string): Promise<string> {
  await loadMermaid();
  const result = await window.mermaid!.render(id, code);
  return result.svg;
}

/** Callback-style wrapper kept for backward compatibility */
export function ensureMermaid(cb: Callback): void {
  loadMermaid().then(cb).catch(cb);
}

/** Remove any scratch DOM nodes Mermaid inserts into document.body */
export function purgeMermaidScratch(id: string): void {
  [`#${id}`, `#d${id}`, `#dmermaid`, `.mermaid-error`].forEach(sel => {
    try { document.querySelectorAll(sel).forEach(el => el.remove()); } catch { /* ignore */ }
  });
  try {
    document.body.querySelectorAll('[id^="mermaid-"], [id^="dmermaid"], .mermaid').forEach(el => {
      if (el.parentElement === document.body) el.remove();
    });
  } catch { /* ignore */ }
}

let _idCounter = 0;
export function nextMermaidId(): string {
  return `mmd-${++_idCounter}`;
}
