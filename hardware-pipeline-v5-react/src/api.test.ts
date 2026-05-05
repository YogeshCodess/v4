/**
 * api.ts — unit tests for the HTTP client mapping + status flattening.
 *
 * Uses a stubbed global fetch so we don't need a running backend.
 * Focused on the two non-trivial transform functions:
 *   1. getStatus → flattens { status: 'completed', updated_at } → 'completed'
 *   2. chat     → maps response/message/content with precedence + `??` defaults
 * Plus a minimal sanity check on 4xx error translation in `req`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Must mock window.location.port for BASE detection BEFORE importing api.
// jsdom defaults to port '' which triggers same-origin (BASE='').
import { api } from './api';

type FetchArgs = [RequestInfo | URL, RequestInit?];

function mockFetchOnce(response: Partial<Response> & { jsonBody?: unknown; textBody?: string }) {
  const fetchMock = vi.fn(async (..._args: FetchArgs): Promise<Response> => ({
    ok: response.ok ?? true,
    status: response.status ?? 200,
    statusText: response.statusText ?? 'OK',
    json: async () => response.jsonBody,
    text: async () => response.textBody ?? '',
  }) as unknown as Response);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// req — 4xx translation
// ---------------------------------------------------------------------------

describe('request error handling', () => {
  it('throws with status + detail on 4xx', async () => {
    mockFetchOnce({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      jsonBody: { detail: "Phase P6 not applicable" },
    });
    await expect(api.getProject(1)).rejects.toThrow(/HTTP 409/);
    await expect(api.getProject(1).catch(e => e.message))
      .resolves.toMatch(/Conflict/);
  });

  it('throws even when error body is not JSON', async () => {
    mockFetchOnce({
      ok: false,
      status: 500,
      statusText: 'Internal',
      // json() will reject by omission — stub as throwing
    });
    // Override json to throw, simulating non-JSON error body
    const fetchMock = vi.fn(async () => ({
      ok: false, status: 500, statusText: 'Internal',
      json: async () => { throw new Error('not json'); },
    }) as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);
    await expect(api.getProject(1)).rejects.toThrow(/HTTP 500/);
  });
});

// ---------------------------------------------------------------------------
// getStatus — flattens nested `{ status }` to a plain string
// ---------------------------------------------------------------------------

describe('getStatus', () => {
  it('flattens {status, updated_at} objects to plain strings', async () => {
    mockFetchOnce({
      jsonBody: {
        phase_statuses: {
          P1: { status: 'completed', updated_at: '2026-01-01T00:00:00Z' },
          P2: { status: 'in_progress' },
          P3: { status: 'pending' },
        },
      },
    });
    const flat = await api.getStatus(1);
    expect(flat.P1).toBe('completed');
    expect(flat.P2).toBe('in_progress');
    expect(flat.P3).toBe('pending');
  });

  it('accepts plain-string entries (older DB rows)', async () => {
    mockFetchOnce({
      jsonBody: {
        phase_statuses: { P1: 'completed', P2: 'pending' },
      },
    });
    const flat = await api.getStatus(1);
    expect(flat.P1).toBe('completed');
    expect(flat.P2).toBe('pending');
  });

  it('defaults unknown shape to "pending"', async () => {
    mockFetchOnce({
      jsonBody: { phase_statuses: { P1: { weird: 'thing' } } },
    });
    const flat = await api.getStatus(1);
    expect(flat.P1).toBe('pending');
  });

  it('returns {} when phase_statuses is missing', async () => {
    mockFetchOnce({ jsonBody: {} });
    const flat = await api.getStatus(1);
    expect(flat).toEqual({});
  });
});

// ---------------------------------------------------------------------------
// chat — field precedence mapping
// ---------------------------------------------------------------------------

describe('chat response mapping', () => {
  it('prefers `response` over `message`/`content`', async () => {
    mockFetchOnce({
      jsonBody: {
        response: 'primary text',
        message: 'should lose',
        content: 'should also lose',
        phase_complete: true,
        draft_pending: false,
      },
    });
    const r = await api.chat(1, 'hi');
    expect(r.text).toBe('primary text');
    expect(r.phaseComplete).toBe(true);
    expect(r.draftPending).toBe(false);
  });

  it('falls back to `message` when `response` is null', async () => {
    mockFetchOnce({
      jsonBody: { response: null, message: 'via message' },
    });
    const r = await api.chat(1, 'hi');
    expect(r.text).toBe('via message');
  });

  it('falls back to `content` when response/message are absent', async () => {
    mockFetchOnce({ jsonBody: { content: 'via content' } });
    const r = await api.chat(1, 'hi');
    expect(r.text).toBe('via content');
  });

  it('preserves an empty-string `response` (not falls through to fallback)', async () => {
    mockFetchOnce({
      jsonBody: {
        response: '', message: 'should not be used',
      },
    });
    const r = await api.chat(1, 'hi');
    expect(r.text).toBe('');
  });

  it('defaults clarificationCards to null when absent', async () => {
    mockFetchOnce({
      jsonBody: { response: 'hi' },
    });
    const r = await api.chat(1, 'x');
    expect(r.clarificationCards).toBeNull();
  });

  it('forwards clarification_cards when present', async () => {
    const cards = {
      intro: 'Quick qs',
      questions: [{ id: 'f', question: 'freq?', why: '', options: ['X', 'Y'] }],
    };
    mockFetchOnce({
      jsonBody: { response: '', clarification_cards: cards },
    });
    const r = await api.chat(1, 'x');
    expect(r.clarificationCards).toEqual(cards);
  });
});

// ---------------------------------------------------------------------------
// setDesignScope + getFullStatus — body/method sanity
// ---------------------------------------------------------------------------

describe('setDesignScope', () => {
  it('PATCHes /design-scope with the scope body', async () => {
    const mock = mockFetchOnce({
      jsonBody: { id: 1, design_scope: 'front-end' },
    });
    await api.setDesignScope(1, 'front-end');
    const [url, opts] = mock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/v1\/projects\/1\/design-scope$/);
    expect(opts?.method).toBe('PATCH');
    expect(opts?.body).toBe(JSON.stringify({ design_scope: 'front-end' }));
  });
});

describe('getFullStatus', () => {
  it('returns design_scope + applicable_phase_ids from the backend payload', async () => {
    mockFetchOnce({
      jsonBody: {
        project_id: 1,
        current_phase: 'P2',
        design_scope: 'dsp',
        applicable_phase_ids: ['P1', 'P8c'],
        phase_statuses: {},
        requirements_hash: null,
        requirements_frozen_at: null,
        stale_phase_ids: [],
      },
    });
    const r = await api.getFullStatus(1);
    expect(r.design_scope).toBe('dsp');
    expect(r.applicable_phase_ids).toEqual(['P1', 'P8c']);
  });
});
