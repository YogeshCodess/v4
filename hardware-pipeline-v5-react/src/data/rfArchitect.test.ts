/**
 * rfArchitect.ts — deterministic wizard data + helpers
 *
 * Covers the v21 wizard intelligence that powers the P1 chat:
 *   - scope-filtered specs and architectures
 *   - Friis-derived MDS
 *   - cascade sanity rules
 *   - architect rationale lookups
 */
import { describe, expect, it } from 'vitest';
import {
  ALL_ARCHITECTURES,
  ALL_SPECS,
  APPLICATIONS,
  PROJECT_TYPES,
  SCOPE_DESC,
  archById,
  archRationale,
  derivedMDS,
  emptyWizardState,
  filterArchByScopeAndApp,
  filterSpecsByScope,
  firedCascadeMessages,
  resolveAppQs,
  resolveDeepDiveQs,
  specLabel,
  type WizardState,
} from './rfArchitect';
import type { DesignScope } from '../types';

const ALL_SCOPES: DesignScope[] = ['full', 'front-end', 'downconversion', 'dsp'];

// ---------------------------------------------------------------------------
// Table shape / invariants
// ---------------------------------------------------------------------------

describe('table shape', () => {
  it('every architecture declares at least one scope', () => {
    for (const a of ALL_ARCHITECTURES) {
      expect(a.scopes.length).toBeGreaterThan(0);
    }
  });

  it('every spec declares at least one scope', () => {
    for (const s of ALL_SPECS) {
      expect(s.scopes.length).toBeGreaterThan(0);
    }
  });

  it('SCOPE_DESC covers every DesignScope', () => {
    for (const scope of ALL_SCOPES) {
      expect(SCOPE_DESC[scope]).toBeTruthy();
      expect(SCOPE_DESC[scope].desc.length).toBeGreaterThan(0);
    }
  });

  it('PROJECT_TYPES has a receiver entry which is supported', () => {
    expect(PROJECT_TYPES.receiver.supported).toBe(true);
  });

  it('APPLICATIONS strong_for ids all exist in ALL_ARCHITECTURES', () => {
    const validIds = new Set(ALL_ARCHITECTURES.map(a => a.id));
    for (const app of APPLICATIONS) {
      for (const archId of app.strong_for) {
        expect(validIds.has(archId)).toBe(true);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// archById
// ---------------------------------------------------------------------------

describe('archById', () => {
  it('returns the matching arch', () => {
    expect(archById('std_lna_filter')?.name).toContain('Standard LNA');
  });

  it('returns undefined for unknown id', () => {
    expect(archById('does_not_exist')).toBeUndefined();
  });

  it('returns undefined for null', () => {
    expect(archById(null)).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// specLabel + q_override
// ---------------------------------------------------------------------------

describe('specLabel', () => {
  it('returns override label when scope matches', () => {
    const gain = ALL_SPECS.find(s => s.id === 'gain')!;
    expect(specLabel(gain, 'front-end')).toBe('LNA chain gain (dB)?');
    expect(specLabel(gain, 'downconversion')).toBe('RF + IF gain (dB)?');
  });

  it('falls back to default q when scope has no override', () => {
    const gain = ALL_SPECS.find(s => s.id === 'gain')!;
    expect(specLabel(gain, 'full')).toBe('Total system gain (dB)?');
  });

  it('returns default q when scope is null', () => {
    const freq = ALL_SPECS.find(s => s.id === 'freq_range')!;
    expect(specLabel(freq, null)).toBe(freq.q);
  });
});

// ---------------------------------------------------------------------------
// filterSpecsByScope
// ---------------------------------------------------------------------------

describe('filterSpecsByScope', () => {
  it('hides dsp-scope specs when scope is front-end', () => {
    const { shown } = filterSpecsByScope('front-end', false);
    // selectivity is downconversion-only → not shown for front-end
    expect(shown.map(s => s.id)).not.toContain('selectivity');
    // freq_range is front-end applicable → shown
    expect(shown.map(s => s.id)).toContain('freq_range');
  });

  // P26.6 (2026-05-05): mds_lock spec + the MDS-lock toggle were
  // removed — the front-end section is now front-end-only. Tests that
  // previously asserted toggle behavior are no longer applicable.

  it('hidden contains specs NOT applicable to the scope', () => {
    const { hidden } = filterSpecsByScope('dsp', false);
    // `noise_figure` is front-end/downconversion/full only
    expect(hidden.map(s => s.id)).toContain('noise_figure');
  });
});

// ---------------------------------------------------------------------------
// filterArchByScopeAndApp
// ---------------------------------------------------------------------------

describe('filterArchByScopeAndApp', () => {
  it('only returns architectures whose scopes include the query scope', () => {
    const { linear, detector, hidden } = filterArchByScopeAndApp('dsp', 'radar');
    linear.forEach(a => expect(a.scopes).toContain('dsp'));
    detector.forEach(a => expect(a.scopes).toContain('dsp'));
    hidden.forEach(a => expect(a.scopes).not.toContain('dsp'));
  });

  it('ranks strong-for architectures first', () => {
    const { linear } = filterArchByScopeAndApp('full', 'radar');
    // For radar, 'superhet_double' is the first entry in strong_for
    expect(linear[0].id).toBe('superhet_double');
  });

  it('detectors are gated by apps_required', () => {
    const { detector: forComms } = filterArchByScopeAndApp('full', 'comms');
    expect(forComms.find(a => a.id === 'crystal_video')).toBeUndefined();

    const { detector: forEw } = filterArchByScopeAndApp('full', 'ew');
    expect(forEw.find(a => a.id === 'crystal_video')).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// derivedMDS (Friis noise floor)
// ---------------------------------------------------------------------------

describe('derivedMDS', () => {
  const withSpecs = (nf: string, ibw: string): WizardState => ({
    ...emptyWizardState(),
    specs: { noise_figure: nf, ibw },
  });

  it('returns null when NF is missing', () => {
    expect(derivedMDS(withSpecs('', '10-100 MHz'))).toBeNull();
  });

  it('returns null when IBW is missing', () => {
    expect(derivedMDS(withSpecs('< 2 dB', ''))).toBeNull();
  });

  it('returns null for Other values not in the chip map', () => {
    expect(derivedMDS(withSpecs('Other', '10-100 MHz'))).toBeNull();
  });

  it('computes -174 + 10·log10(BW) + NF for NF=1.5 dB, BW=50 MHz', () => {
    // Expected: -174 + 76.99 + 1.5 ≈ -95.5 dBm
    const out = derivedMDS(withSpecs('< 2 dB', '10-100 MHz'));
    expect(out).not.toBeNull();
    expect(parseFloat(out!)).toBeCloseTo(-95.5, 1);
  });

  it('computes correctly for a wideband EW case (NF=5 dB, BW=2 GHz)', () => {
    // Expected: -174 + 93.01 + 5 ≈ -76.0 dBm
    const out = derivedMDS(withSpecs('4-6 dB', '> 1 GHz'));
    expect(parseFloat(out!)).toBeCloseTo(-76.0, 1);
  });
});

// ---------------------------------------------------------------------------
// firedCascadeMessages + DEEP_DIVES
// ---------------------------------------------------------------------------

describe('cascade rules', () => {
  it('no messages fire for an empty wizard', () => {
    expect(firedCascadeMessages(emptyWizardState())).toEqual([]);
  });
});

describe('resolveDeepDiveQs', () => {
  it('returns empty when scope is null', () => {
    const { qs } = resolveDeepDiveQs(emptyWizardState());
    expect(qs).toEqual([]);
  });

  it('returns non-empty list for front-end scope', () => {
    const s: WizardState = { ...emptyWizardState(), scope: 'front-end' };
    const { qs } = resolveDeepDiveQs(s);
    expect(qs.length).toBeGreaterThan(0);
    expect(qs.map(q => q.id)).toContain('interferer_env');
  });

  it('radar T/R-switch question only appears for radar application', () => {
    const s: WizardState = {
      ...emptyWizardState(),
      scope: 'front-end',
      application: 'comms',
    };
    const { qs } = resolveDeepDiveQs(s);
    expect(qs.map(q => q.id)).not.toContain('tr_switch');
  });
});

describe('resolveAppQs', () => {
  it('returns empty when application is missing', () => {
    expect(resolveAppQs(emptyWizardState())).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// archRationale
// ---------------------------------------------------------------------------

describe('archRationale', () => {
  it('returns application-specific rationale when present', () => {
    const out = archRationale('superhet_double', 'radar');
    expect(out).toContain('pulse');
  });

  it('falls back to default rationale for unknown app', () => {
    const out = archRationale('superhet_double', 'nothing-there');
    expect(out.length).toBeGreaterThan(0);
  });

  it('returns generic fallback for unknown arch', () => {
    const out = archRationale('nonexistent_arch', 'radar');
    expect(out).toContain('it matches your scope');
  });
});
