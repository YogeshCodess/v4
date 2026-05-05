/**
 * sanitizeMermaid — regression tests for the B6-family of Mermaid parse errors.
 *
 * Each test uses a single known-bad input the LLM actually produced in the
 * wild (or a trivially derived variant) and asserts the sanitiser rewrites
 * it into something Mermaid 10 will accept.
 */
import { describe, expect, it } from 'vitest';
import { sanitizeMermaid } from './mermaidSanitize';

const san = sanitizeMermaid;

describe('frontmatter + comments', () => {
  it('strips %%{ init }%% frontmatter', () => {
    const out = san("%%{ init: { theme: 'dark' } }%%\ngraph TD\nA-->B");
    expect(out).not.toContain('%%{');
    expect(out).not.toContain('init');
  });

  it('strips standalone %% comment lines', () => {
    const out = san('flowchart TD\n%% this is a comment\nA-->B');
    expect(out).not.toContain('this is a comment');
    expect(out).toContain('A-->B');
  });
});

describe('arrow normalisation', () => {
  it('converts ==> to -->', () => {
    const out = san('flowchart TD\nA==>B');
    expect(out).toMatch(/A\s*-->\s*B/);
    expect(out).not.toContain('==>');
  });

  it('converts word -> word to -->', () => {
    const out = san('flowchart TD\nA->B');
    expect(out).toMatch(/A\s*-->\s*B/);
  });

  it('converts em-dash arrow (——>) to -->', () => {
    const out = san('flowchart TD\nA——>B');
    expect(out).toMatch(/A\s*-->\s*B/);
  });
});

describe('diagram type normalisation', () => {
  it('rewrites `graph TD` to `flowchart TD`', () => {
    const out = san('graph TD\nA-->B');
    expect(out).toMatch(/^flowchart TD/);
    expect(out).not.toMatch(/^graph TD/);
  });

  it('merges `flowchart\\nTD` into `flowchart TD`', () => {
    const out = san('flowchart\nTD\nA-->B');
    expect(out).toMatch(/^flowchart TD/);
  });

  it('prepends `flowchart TD` when diagram type is missing', () => {
    const out = san('A-->B');
    expect(out).toMatch(/^flowchart TD/);
  });
});

describe('label sanitisation', () => {
  it('strips angle brackets from node labels', () => {
    const out = san('flowchart TD\nA[Signal <200MHz>]');
    expect(out).not.toContain('<');
    expect(out).not.toContain('>');
  });

  it('strips parentheses from node labels', () => {
    const out = san('flowchart TD\nA[Component (2.4GHz)]');
    expect(out).not.toMatch(/\([^)]*GHz[^)]*\)/);
  });

  it('replaces & with "and" inside labels', () => {
    const out = san('flowchart TD\nA[TX & RX]');
    expect(out).toContain('and');
    expect(out).not.toMatch(/&(?!amp|lt|gt|#)/);
  });

  it('strips double-quotes and single-quotes inside labels', () => {
    const out = san('flowchart TD\nA[\'Mixer\']');
    expect(out).not.toContain("'");
  });

  it('replaces dash sequences inside labels (prevent arrow mis-parse)', () => {
    const out = san('flowchart TD\nA[2----4 GHz]');
    // The ---- must have been collapsed so no >=2-dash run remains inside labels
    expect(out).not.toMatch(/\[[^\]]*-{2,}[^\]]*\]/);
  });
});

describe('HTML entity decoding', () => {
  it('decodes &lt; &gt; &amp; &nbsp;', () => {
    const out = san('flowchart TD\nA[a&amp;b]');
    expect(out).toContain('and');
  });

  it('strips raw HTML tags BUT preserves <br> and <br/>', () => {
    // P26 (2026-04-25) — `<br>` and `<br/>` are mermaid's accepted
    // line-break tokens inside quoted labels. Earlier this test asserted
    // they were stripped, but stripping them mangles multi-line labels
    // (e.g. `LODIST("LO Chain<br/>(OCXO-PLL-Splitter)")` lost the visible
    // line break and ran the two halves together). Other HTML tags
    // (script, style, span, etc.) are still stripped.
    const out = san('flowchart TD\nA[<br/>label]');
    // <br> survives:
    expect(out).toContain('<br>');
    // But other tags don't:
    const out2 = san('flowchart TD\nA[<script>x</script>label]');
    expect(out2).not.toContain('<script>');
    expect(out2).not.toContain('</script>');
  });

  it('preserves <br> inside trapezoid shape (P26 regression)', () => {
    // Real failing input: `ADCBLOCK[\\"ADC / AD9648BCPZ-125<br/>Dual 14b 125Msps"\\]`.
    // The trapezoid was being mangled by the rect `[..]` sanitiser
    // running on the inner content, AND the <br> was being stripped.
    // Both fixes verified together.
    const raw = 'flowchart TD\n    ADCBLOCK[\\"ADC<br/>Dual 14b 125Msps"\\]\n    ADCBLOCK --> X[Done]';
    const out = san(raw);
    // The trapezoid shape delimiters survived:
    expect(out).toMatch(/ADCBLOCK\[\\.*ADC.*Dual 14b 125Msps.*\\\]/);
    // The <br> survived (as <br>, possibly normalised from <br/>):
    expect(out).toMatch(/<br\s*\/?>/);
    // The edge line is intact:
    expect(out).toMatch(/ADCBLOCK\s*-->\s*X/);
  });

  it('strips redundant inner quotes from trapezoid [\\..\\] (P26 #2)', () => {
    // Real failing input from project djd (2026-04-25, second occurrence):
    //   `DIG_SEC[\\"Digitisation<br/>2x ISLA212P25 ADCs<br/>500Msps 12-bit"\\]`
    // Mermaid parser: "Parse error on line 48: ... THERMAL end ..."
    // — the trapezoid's inner `"` confused the parser into looking for a
    // quoted-label close that didn't match the shape's `\\]`, and the
    // diagram failed deep inside an unrelated subgraph because parsing
    // got out of sync.
    const raw = 'flowchart TB\n    DIG_SEC[\\"Digitisation<br/>500Msps 12-bit"\\]\n    DIG_SEC --> X[Done]';
    const out = san(raw);
    // No more inner `\"...\"\` artefacts.
    expect(out).not.toMatch(/\[\\".*"\\\]/);
    // Trapezoid shape preserved with bare label between `[\` and `\]`.
    expect(out).toMatch(/DIG_SEC\[\\Digitisation<br>500Msps 12-bit\\\]/);
  });

  it('strips redundant inner quotes from parallelogram [/.../] (P26 #2)', () => {
    const raw = 'flowchart TB\n    LVDS_OUT[/"LVDS Output<br/>To Signal Processor"/]';
    const out = san(raw);
    expect(out).not.toMatch(/\[\/".*"\//);
    expect(out).toMatch(/LVDS_OUT\[\/LVDS Output<br>To Signal Processor\/\]/);
  });

  it('subroutine [[..]] preserved (not mangled into [..]]) — P26 #3', () => {
    // Real failing input from project fyfu (architecture.md line 3):
    //   BUCK[["Buck / LT1107CS8-5#PBF / 12V to 5V"]]
    // The single-bracket rect regex used to capture `["Buck...V` (the
    // unbalanced opening `[` was captured INTO the inner), then strip
    // the `[` and `"`, producing:
    //   BUCK[Buck / LT1107CS8-5 PBF / 12V to 5V]]
    // — single open, double close. Mermaid: "Parse error on line 3:
    // ...12V to 5V]] LDO_5..."
    const raw = (
      'flowchart TB\n' +
      '    BUCK[["Buck / LT1107CS8-5#PBF / 12V to 5V"]]\n' +
      '    LDO_5[["LDO 5V / SPX3819M5"]]\n' +
      '    BUCK --> LDO_5\n'
    );
    const out = san(raw);
    // Subroutine brackets must remain matched [[..]].
    expect(out).toMatch(/BUCK\[\[Buck \/ LT1107CS8-5 PBF \/ 12V to 5V\]\]/);
    expect(out).toMatch(/LDO_5\[\[LDO 5V \/ SPX3819M5\]\]/);
    // No mangled single-open/double-close patterns.
    expect(out).not.toMatch(/BUCK\[[^[][^\]]*\]\]/);
    expect(out).not.toMatch(/LDO_5\[[^[][^\]]*\]\]/);
  });

  it('hexagon {{..}} and circle ((..)) preserved — P26 #3', () => {
    const raw = (
      'flowchart TD\n' +
      '    BPF{{"Custom Cavity / IL1.5"}}\n' +
      '    OSC(("10 MHz Reference"))\n'
    );
    const out = san(raw);
    expect(out).toMatch(/BPF\{\{Custom Cavity \/ IL1\.5\}\}/);
    expect(out).toMatch(/OSC\(\(10 MHz Reference\)\)/);
  });

  it('mixed-slash trapezoid [/..\\] preserved — P26 #3', () => {
    // Real failing input from project fyfu (block_diagram.md):
    //   LIM1[/"Lim / CLA4602-000 / IL0.2 P+33max"\]
    const raw = (
      'flowchart TD\n' +
      '    LIM1[/"Lim / CLA4602-000 / IL0.2 P+33max"\\]\n' +
      '    LIM1 --> X[Done]\n'
    );
    const out = san(raw);
    // Trapezoid delimiters intact (forward open, backward close).
    expect(out).toMatch(/LIM1\[\/Lim \/ CLA4602-000 \/ IL0\.2 P\+33max\\\]/);
  });

  it('literal < inside label does NOT collapse multiple lines — P26 #3', () => {
    // Real failing input from project fyfu: `ANT1>"Ant1<br/>< 2 GHz"]`.
    // The literal `<` (less-than sign for "< 2 GHz") combined with the
    // `>` of the next-many-lines-down `>...]` flag node would cause
    // the strip-HTML regex `<[^>]+>` to match across newlines and
    // collapse all the intermediate node defs into a single mangled
    // line. Fix: regex now uses `[^>\\n]+` so it can't cross newlines.
    const raw = (
      'flowchart TD\n' +
      '    ANT1>"Ant1<br/>< 2 GHz"]\n' +
      '    ANT2>"Ant2<br/>< 2 GHz"]\n' +
      '    SMA1[/"SMA-F"/]\n' +
      '    LNA1>"LNA1 amp"]\n' +
      '    ANT1 --> SMA1\n'
    );
    const out = san(raw);
    // Each node def survives on its own line.
    const lines = out.split('\n').map(l => l.trim()).filter(Boolean);
    expect(lines.some(l => l.startsWith('ANT1>'))).toBe(true);
    expect(lines.some(l => l.startsWith('ANT2>'))).toBe(true);
    expect(lines.some(l => l.startsWith('SMA1'))).toBe(true);
    expect(lines.some(l => l.startsWith('LNA1>'))).toBe(true);
    // No line should contain BOTH ANT1 and LNA1 (would mean they got merged).
    expect(lines.every(l => !(l.includes('ANT1') && l.includes('LNA1')))).toBe(true);
  });

  it('does not insert --> between bare identifiers in subgraph body (P26)', () => {
    // Subgraph membership lists must NOT be auto-arrowed:
    //     subgraph POWER["Power Distribution"]
    //         PWR12V
    //         BUCK5V       <-- "include in subgraph", NOT "PWR12V --> BUCK5V"
    //         LDO33
    //     end
    const raw = (
      'flowchart TD\n' +
      '    PWR12V[12V Input]\n' +
      '    BUCK5V[5V Buck]\n' +
      '    LDO33[3.3V LDO]\n' +
      '    PWR12V --> BUCK5V\n' +
      '    BUCK5V --> LDO33\n' +
      '    subgraph POWER["Power Distribution"]\n' +
      '        PWR12V\n' +
      '        BUCK5V\n' +
      '        LDO33\n' +
      '    end\n'
    );
    const out = san(raw);
    // The KEY invariant: subgraph body's bare identifiers must NOT have
    // been joined by `-->` (which would change subgraph membership into
    // node connectivity). Title quotes may or may not survive — that's
    // not the property under test here.
    expect(out).toMatch(/subgraph POWER\[[^\]]+\]\s*\n\s+PWR12V\s*\n\s+BUCK5V\s*\n\s+LDO33\s*\n\s+end/);
    // No `-->` arrows between subgraph members:
    expect(out).not.toMatch(/PWR12V\s*-->\s*BUCK5V\s*\n\s*BUCK5V\s*-->\s*LDO33\s*\n\s*end/);
  });
});

describe('unclosed bracket repair', () => {
  it('joins multi-line labels where [ is unclosed', () => {
    const out = san('flowchart TD\nA[Long\nLabel]\nB-->A');
    // After join, the label still ends with ]
    expect(out).toMatch(/A\[[^\]]*Long[^\]]*Label[^\]]*\]/);
  });

  it('auto-closes a dangling [ on a line (LLM forgot ])', () => {
    const out = san('flowchart TD\nA[Dangling\nB-->A');
    expect(out).toContain(']');
  });
});

describe('missing arrow repair', () => {
  it('inserts --> between two bracket-delimited nodes on one line', () => {
    const out = san('flowchart TD\nA[X] B[Y]');
    expect(out).toMatch(/A\[X\]\s*-->\s*B\[Y\]/);
  });

  it('inserts arrows between 3+ bare identifiers on one line', () => {
    const out = san('flowchart TD\nRF IF DSP');
    expect(out).toMatch(/RF\s*-->\s*IF\s*-->\s*DSP/);
  });
});

describe('end-keyword line separation', () => {
  it('puts trailing `end` on its own line', () => {
    const out = san('flowchart TD\nsubgraph S\nA-->B end');
    expect(out).toMatch(/B\n\s*end/);
  });

  it('puts leading `end` + content on separate lines', () => {
    const out = san('flowchart TD\nsubgraph S\nA-->B\nend C-->D');
    expect(out).toMatch(/end\n\s*C/);
  });
});

describe('Unicode glyphs', () => {
  it('replaces Ω with Ohm', () => {
    const out = san('flowchart TD\nA[50Ω]');
    expect(out).toContain('Ohm');
    expect(out).not.toContain('Ω');
  });

  it('replaces µ with u and ° with deg', () => {
    const out = san('flowchart TD\nA[10µs 90°]');
    expect(out).toContain('u');
    expect(out).toContain('deg');
    expect(out).not.toContain('µ');
    expect(out).not.toContain('°');
  });

  it('replaces smart quotes with straight quotes', () => {
    const out = san('flowchart TD\nA[\u201CMixer\u201D]');
    expect(out).not.toContain('\u201C');
    expect(out).not.toContain('\u201D');
  });
});

describe('happy path — a known-good diagram passes through intact', () => {
  it('preserves a well-formed flowchart', () => {
    const input = 'flowchart TD\n    A[LNA] --> B[Mixer]\n    B --> C[ADC]';
    const out = san(input);
    expect(out).toContain('flowchart TD');
    expect(out).toContain('A[LNA]');
    expect(out).toContain('B[Mixer]');
    expect(out).toContain('C[ADC]');
  });
});

describe('idempotence', () => {
  it('running the sanitiser twice produces the same output', () => {
    const input = 'graph TD\nA[x] B[y]\n%%comment\nZ-->A';
    const once = san(input);
    const twice = san(once);
    expect(twice).toEqual(once);
  });
});

describe('quoted edge labels (regression for 2026-04-24 chat page errors)', () => {
  it('PWR1 -- "5V" --> LDO_ADC → canonical pipe form', () => {
    // Exact pattern from user-reported parse error:
    //   "Parse error on line 24: ...> OUT1 PWR1 -- "5V" --> LDO_ADC
    //    ----------------------^ Expecting 'LINK', 'UNICODE_TEX'"
    const input = 'flowchart TD\n    PWR1 -- "5V" --> LDO_ADC';
    const out = san(input);
    expect(out).toMatch(/PWR1\s+-->\|5V\|\s+LDO_ADC/);
    expect(out).not.toContain('-- "5V" -->');
  });

  it('dotted arrow with quoted label — CLK1 -. "170 MHz" .-> ADC1', () => {
    const input = 'flowchart LR\n    CLK1 -. "170 MHz LVPECL" .-> ADC1';
    const out = san(input);
    // Dotted arrow → `-.->|label|` form.
    expect(out).toMatch(/CLK1\s+-\.->\|170 MHz LVPECL\|\s+ADC1/);
    expect(out).not.toContain('-. "170 MHz');
  });

  it('thick arrow with quoted label — FPG1 == "JESD204C 4+ lanes" ==> OUT1', () => {
    const input = 'flowchart LR\n    FPG1 == "JESD204C 4+ lanes" ==> OUT1';
    const out = san(input);
    expect(out).toMatch(/FPG1\s+==>\|JESD204C 4\+ lanes\|\s+OUT1/);
    expect(out).not.toContain('== "JESD204C');
  });

  it('full channelised-FE diagram from user screenshot', () => {
    // Condensed version of the actual source in screenshot 1:
    const input = [
      'flowchart LR',
      '    ANT1 -- "RF per channel" --> SMA1',
      '    SMA1 -- "Analog RF" --> ADC1',
      '    REF1 -- "25 MHz LVCMOS" --> CLK1',
      '    CLK1 -. "170 MHz LVPECL" .-> ADC1',
      '    CLK1 -. "Ref Clk + SYSREF" .-> FPG1',
      '    ADC1 -- "14-bit parallel LVDS" --> FPG1',
      '    FPG1 == "JESD204C 4+ lanes" ==> OUT1',
      '    PWR1 -- "5V" --> LDO_ADC',
      '    PWR1 -- "5V" --> LDO_33',
    ].join('\n');
    const out = san(input);
    // Every quoted-label edge converts.
    expect(out).not.toContain('-- "');
    expect(out).not.toContain('== "');
    expect(out).not.toContain('-. "');
    // Spot-check a few.
    expect(out).toMatch(/ANT1\s+-->\|RF per channel\|\s+SMA1/);
    expect(out).toMatch(/CLK1\s+-\.->\|170 MHz LVPECL\|\s+ADC1/);
    expect(out).toMatch(/FPG1\s+==>\|JESD204C 4\+ lanes\|\s+OUT1/);
    expect(out).toMatch(/PWR1\s+-->\|5V\|\s+LDO_ADC/);
  });
});

describe('round-bracket nodes with nested parens in quoted labels', () => {
  it('S11("VGA (AGC)<br/>HMC624LP4E") renders as a single node', () => {
    // Regression for the 12 GHz receiver diagram that fell back to the
    // "BLOCK DIAGRAM (source)" view because the round-bracket label
    // sanitiser captured only up to the first inner `)`.
    const input = 'flowchart LR\n    S11("VGA (AGC)<br/>HMC624LP4E")';
    const out = san(input);
    // Must produce a single well-formed square-bracket node.
    expect(out).toMatch(/S11\[[^\]]*VGA[^\]]*AGC[^\]]*HMC624LP4E[^\]]*\]/);
    // No leftover floating text after the node (the failure mode was
    // `S11( VGA  AGC) HMC624LP4E")`).
    expect(out).not.toMatch(/S11[^[]*HMC624LP4E"/);
    expect(out).not.toContain('")');
  });

  it('preserves rounded shape when the label has no inner parens', () => {
    // S4("LNA Stage 1<br/>HMC618ALP3E") has no `()` in the label, so we
    // keep the round-edge visual.
    const input = 'flowchart LR\n    S4("LNA Stage 1<br/>HMC618ALP3E")';
    const out = san(input);
    expect(out).toMatch(/S4\([^)]*LNA Stage 1[^)]*HMC618ALP3E[^)]*\)/);
  });

  it('handles the full receiver front-end block diagram', () => {
    // End-to-end: the exact shape the pipeline emits for a 12 GHz Rx.
    const input = [
      'flowchart LR',
      '    %% 12.00 GHz +- 50 MHz',
      '    ANT((Antenna)) --> S1',
      '    S1["N-type Input Connector<br/>N-type IP67 50 ohm"]',
      '    S2["PCB Trace<br/>50Ohm Microstrip (RO4350B)"]',
      '    S11("VGA (AGC)<br/>HMC624LP4E")',
      '    S1 --> S2',
      '    S2 --> S11',
    ].join('\n');
    const out = san(input);
    // Must still start with a valid diagram type.
    expect(out.split('\n')[0].trim()).toBe('flowchart LR');
    // S2 (square brackets) keeps its RO4350B content.
    expect(out).toMatch(/S2\[[^\]]*RO4350B[^\]]*\]/);
    // S11 converts to square brackets and keeps HMC624LP4E.
    expect(out).toMatch(/S11\[[^\]]*HMC624LP4E[^\]]*\]/);
    // Edges preserved.
    expect(out).toMatch(/S1\s*-->\s*S2/);
    expect(out).toMatch(/S2\s*-->\s*S11/);
  });
});
