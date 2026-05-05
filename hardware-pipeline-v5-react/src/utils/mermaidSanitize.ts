/**
 * Canonical Mermaid sanitiser — shared by ChatView and (eventually)
 * DocumentsView. Previously lived as a duplicated local function in each
 * view, which allowed the two copies to drift. Extract + test in one place.
 *
 * P26 (2026-04-25) — IMPORTANT: the backend already runs an aggressive
 * salvage (`tools/mermaid_salvage.py`) before writing diagrams to disk,
 * so most of the input this function sees is already clean. The job here
 * is to handle:
 *   - Defensive cases where the backend salvage was bypassed.
 *   - LLM-emitted chat draft text (no backend salvage on the chat path).
 *
 * Things this sanitiser used to do but DOES NOT anymore (P26):
 *   - Strip ALL HTML tags (`<[^>]+>`). Mermaid USES `<br>` and `<br/>`
 *     for label line breaks; stripping them concatenates words that
 *     should have been on separate visual lines.
 *   - Convert `&lt;` → `(` and `&gt;` → `)`. HTML entities can appear
 *     inside legitimate labels (`<= 5V` rendered as `&lt;= 5V`); the
 *     replacement corrupts the label.
 *   - Sanitise the inner contents of trapezoid `[\..\]` and parallelogram
 *     `[/.../]` shapes. Those use the same `[..]` outer bracket pair as
 *     a rectangle, but the inner `\` and `/` chars confuse the rect
 *     sanitiser's regex and turn the shape into garbage.
 *   - Auto-insert `-->` between consecutive bare identifiers. Subgraph
 *     bodies look like `\n    NODEA\n    NODEB\n    end` — auto-arrow
 *     insertion would join NODEA and NODEB with `-->`, breaking the
 *     subgraph membership semantics.
 *
 * Fixes still applied:
 *   - %% comments and %%{ init }%% frontmatter
 *   - non-ASCII glyphs (Ohm, °, µ, em-dashes, smart quotes, arrows)
 *   - `graph TD` → `flowchart TD`
 *   - bare `==>` / `->` / unicode arrows → `-->` (only when NOT followed
 *     by `|` so we don't break thick-arrow pipe-form labels)
 *   - quoted edge labels (`A -- "x" --> B` → `A -->|x| B`)
 *   - unclosed `[` brackets at end of line → auto-close
 *   - `end` keyword collapsed onto the same line as content
 */

/** Sanitise AI-generated Mermaid code. */
export function sanitizeMermaid(raw: string): string {
  let code = raw.trim().replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  // Strip %%{ init }%% frontmatter and %% comments
  code = code.replace(/^%%\{[\s\S]*?\}%%\s*/m, '');
  code = code.replace(/%%[^\n]*/g, '');
  // Replace non-ASCII symbols that break Mermaid parser
  const uMap: Record<string, string> = {
    '\u03A9': 'Ohm', '\u2126': 'Ohm', '\u00B0': 'deg', '\u00B5': 'u',
    '\u2013': '-', '\u2014': '-', '\u2018': "'", '\u2019': "'",
    '\u201C': '"', '\u201D': '"', '\u2264': '<=', '\u2265': '>=',
    '\u00B1': '+-', '\u2192': '-->', '\u2190': '<--',
  };
  code = code.replace(/[^\x00-\x7F]/g, ch => uMap[ch] || '');
  // P26 (2026-04-25): REMOVED the legacy `((label))` → `(label)` collapse.
  // Mermaid's `((..))` is a VALID circle shape — collapsing it to a round
  // `(..)` lost the visual distinction. The downstream double-paren
  // sanitiser (later in this function) now handles `((..))` correctly.
  // Round-bracket nodes with quoted labels containing nested parens break
  // the paren-label sanitiser below — its regex `/\(([^)]*)\)/g` captures up
  // to the *first* `)`, so `S11("VGA (AGC)…")` is parsed as a node whose
  // label ends at `(AGC)`, leaving the rest of the original label as
  // syntactically-garbage tokens on the line and failing the whole diagram.
  // Normalise `ID("…(…)…")` → `ID["…(…)…"]` so the bracket regex (which
  // uses `[^\]]*` and can span any inner `()`) finds the right boundary.
  // The rounded-edge visual is lost for these nodes, but the diagram
  // actually renders — strictly better than dumping the raw source.
  code = code.replace(/([\w-]+)\("([^"]*[()][^"]*)"\)/g, '$1["$2"]');
  // Quoted edge labels — mirror of `tools/mermaid_salvage._step_fix_quoted_edge_labels`.
  // Mermaid edge labels live INSIDE pipes (`-->|label|`), not between arrow
  // tokens (`-- "label" -->`). The LLM keeps emitting the wrong shape across
  // three arrow styles with THREE DIFFERENT tail tokens:
  //   BUCK -- "+5 V"    --> LDO1          (normal:  left `--`,  tail `-->`)
  //   A    == "thick"  ==> B              (thick:   left `==`,  tail `==>`)
  //   CLK1 -. "170 MHz" .-> ADC1          (dotted:  left `-.`,  tail `.->`)
  // All three are parse errors. Convert each to the canonical pipe form:
  //   BUCK -->|+5 V| LDO1
  //   A    ==>|thick| B
  //   CLK1 -.->|170 MHz| ADC1
  // Regression for the 2026-04-24 power-tree + channelised-FE diagrams.
  // Note: the dotted tail is `.->` (not `-.->`) — the leading `.` matches
  // the trailing `-.` of the left arrow token. Previous fix had the wrong
  // tail which is why dotted edges still tripped the parser.
  code = code.replace(
    /(\b[\w][\w-]*\b)\s*(==|--|-\.)\s*"([^"]+)"\s*(==>|-->|\.->)\s*(\b[\w][\w-]*\b)/g,
    (_m, src, style, label, tail, dst) => {
      const thick  = String(style).includes('==') || String(tail).includes('==');
      const dotted = String(style).includes('-.') || String(tail).startsWith('.');
      const arrow = thick ? '==>' : dotted ? '-.->' : '-->';
      const cleanLabel = String(label).trim().replace(/['"`]/g, '');
      return `${src} ${arrow}|${cleanLabel}| ${dst}`;
    },
  );
  // Arrow normalisations — DO NOT collapse `==>` or `-.->` that are
  // immediately followed by `|label|` (pipe-form), since we just emitted
  // those as thick / dotted arrows with labels. Only normalise bare forms.
  code = code.replace(/\u2014\u2014>/g, '-->').replace(/\u2014>/g, '-->');
  code = code.replace(/——>/g, '-->').replace(/—>/g, '-->');
  // `==>` → `-->` only when NOT followed by `|` (bare thick arrow → normal).
  code = code.replace(/==>(?!\|)/g, '-->');
  code = code.replace(/(\w)\s*->\s*(\w)/g, '$1 --> $2');
  // Normalise graph → flowchart
  code = code.replace(/^graph\s+(TD|LR|TB|RL|BT)/im, 'flowchart $1');
  code = code.replace(/^(flowchart)\n(TD|LR|TB|RL|BT)\b/m, '$1 $2');
  // Join lines where [ is opened but not closed (multi-line node labels)
  {
    const joinedLines: string[] = [];
    for (const line of code.split('\n')) {
      if (joinedLines.length > 0) {
        const last = joinedLines[joinedLines.length - 1];
        const opens = (last.match(/\[/g) || []).length;
        const closes = (last.match(/\]/g) || []).length;
        if (opens > closes) {
          joinedLines[joinedLines.length - 1] = last.trimEnd() + ' ' + line.trimStart();
          continue;
        }
      }
      joinedLines.push(line);
    }
    code = joinedLines.join('\n');
    // Auto-close any still-unclosed [ on a single line (LLM forgot closing bracket)
    code = code.split('\n').map(line => {
      const opens = (line.match(/\[/g) || []).length;
      const closes = (line.match(/\]/g) || []).length;
      return opens > closes ? line + ']'.repeat(opens - closes) : line;
    }).join('\n');
  }
  // Ensure known diagram type on line 1
  const first = code.split('\n')[0].trim().toLowerCase();
  const known = ['flowchart', 'sequencediagram', 'classdiagram', 'statediagram',
    'erdiagram', 'gantt', 'pie', 'gitgraph', 'mindmap', 'timeline'];
  if (!known.some(t => first.startsWith(t))) code = 'flowchart TD\n' + code;
  // P26 (2026-04-25): preserve `<br>` and `<br/>` (mermaid's accepted
  // line-break tokens inside quoted labels). The previous code stripped
  // ALL HTML tags via `<[^>]+>/gi` which corrupted multi-line labels:
  //   `LODIST("LO Chain<br/>(OCXO-PLL-Splitter)")`
  //     →  `LODIST("LO Chain (OCXO-PLL-Splitter)")` — visible line break GONE.
  // Worse, when combined with the whitespace-collapse step below, words
  // from different visual lines got jammed together and the resulting
  // label could exceed mermaid's per-shape character limit and be
  // mis-attributed to the next statement.
  //
  // Also dropped the `&lt;` → `(` and `&gt;` → `)` replacements:
  //   - HTML entities legitimately appear inside labels ("<= 5V" rendered
  //     as `&lt;= 5V`).
  //   - Replacing them with parens turned `&lt;= 5V` into `(= 5V` —
  //     unbalanced paren that confused the round-shape sanitiser.
  code = code.replace(/\\n/g, ' ');
  code = code.replace(/&amp;/g, 'and').replace(/&nbsp;/g, ' ');
  // Normalise self-closing `<br/>` → `<br>` so the rest of the pipeline
  // can rely on a single canonical form.
  code = code.replace(/<br\s*\/>/gi, '<br>');
  // Strip HTML tags EXCEPT <br> (mermaid uses it for line breaks).
  // P26 (2026-04-25, fyfu fix): the `[^>]+` body MUST exclude newlines.
  // Without `\n` in the negation, a literal `<` inside a label (e.g.
  // `ANT1>"Ant1<br/>< 2 GHz"]` — the `< 2` is just less-than-sign +
  // digit, NOT an HTML tag) would be paired with a `>` MANY LINES
  // LATER (e.g. the `>` of the next `>...]` flag node), and the strip
  // would replace dozens of lines with a single space — collapsing
  // the diagram into one mangled line. The literal `<` survives this
  // step unchanged; sanitizeLabel below maps it to a space inside the
  // label sanitisation pass.
  code = code.replace(/<(?!br\b)[^>\n]+>/gi, ' ');

  // P26 (2026-04-25, second pass): trapezoid `[\..\]` and parallelogram
  // `[/.../]` shapes — strip REDUNDANT internal quotes the LLM emits
  // when it JSON-escapes its tool input. The trapezoid/parallelogram
  // family is the ONLY shape group mermaid genuinely REJECTS quoted
  // labels on; all other shapes (`[[..]]`, `{{..}}`, `((..))`,
  // `([..])`, `[(..)]`, `[label]`, `(label)`, `{label}`) accept
  // quoted labels and we leave those alone.
  //
  // P26 #4 (fyfu DOCX fix): an earlier "third pass" was stripping
  // quotes off ALL shape variants — that turned the well-formed
  // stadium `RF_CH1(["RF Chain 1 (Ant1 to ADC1)"])` into the broken
  // `RF_CH1([RF Chain 1 (Ant1 to ADC1)])`, which mermaid rejected
  // because the unquoted inner `(Ant1 to ADC1)` parens trip the
  // stadium parser. Mirror of backend `_step_normalise_shape_quotes`.
  code = code.replace(/\[\\\s*"([^"]*)"\s*\\\]/g, (_m, inner: string) => `[\\${inner.trim()}\\]`);
  code = code.replace(/\[\/\s*"([^"]*)"\s*\/\]/g, (_m, inner: string) => `[/${inner.trim()}/]`);
  // Ensure `end` (subgraph close) is always on its own line — trailing case
  code = code.split('\n').map(line => {
    if (/\bend\s*$/.test(line) && !/^\s*end\b/.test(line)) {
      const before = line.replace(/\s+end\s*$/, '').trimEnd();
      return (before ? before + '\n' : '') + 'end';
    }
    return line;
  }).join('\n');
  // Ensure `end` is always on its own line — leading case ("end NODE ...")
  code = code.split('\n').map(line => {
    const m = line.match(/^(\s*)end\s+(\S.*)$/);
    if (m) return `${m[1]}end\n${m[1]}${m[2]}`;
    return line;
  }).join('\n');
  // Fix "NODE |label|" (no following node) → "NODE[label]" — orphan pipe-label
  code = code.split('\n').map(line =>
    line.replace(/(\w)\s+\|([^|]+)\|(?!\s*[\w\[])/g,
      (_m, pre, inner) => `${pre}[${inner.trim()}]`)
  ).join('\n');
  // Fix "NODEA |label| NODEB" (pipe label but NO arrow) → "NODEA -->|label| NODEB"
  code = code.split('\n').map(line => {
    if (/^\s*(subgraph|end|%%)/.test(line)) return line;
    return line.replace(
      /^(\s*)([\w][\w\-]*)\s+(\|[^|]+\|)\s*([\w])/,
      (_m, indent, n1, label, n2start) => `${indent}${n1} -->${label} ${n2start}`
    );
  }).join('\n');
  // Fix two+ word-tokens on same line with NO arrow — handles both
  // "NODEA NODEB[" and "A B C" (3+ bare IDs).
  //
  // P26 (2026-04-25): track subgraph nesting so we DON'T insert `-->`
  // arrows between bare identifiers that are members of a subgraph body.
  // Subgraph syntax is:
  //     subgraph Name["Title"]
  //         NODE1
  //         NODE2     <-- bare identifier; means "include in subgraph",
  //         NODE3         NOT "draw an arrow from NODE2 to NODE3"
  //     end
  // The previous code joined NODE2 and NODE3 with `-->` whenever they
  // appeared on the same VISUAL line (e.g. after the `<br>`-stripping
  // step concatenated them), turning the subgraph into garbage.
  {
    let subgraphDepth = 0;
    code = code.split('\n').map(line => {
      const stripped = line.trim();
      if (/^subgraph\b/i.test(stripped)) { subgraphDepth++; return line; }
      if (/^end\b/i.test(stripped)) {
        if (subgraphDepth > 0) subgraphDepth--;
        return line;
      }
      // Inside a subgraph: only the membership list (no arrow auto-insert).
      if (subgraphDepth > 0) return line;
      if (!stripped || /^\s*(subgraph|end|%%)/.test(line)) return line;
      line = line.replace(
        /^(\s*)([\w][\w\-]*)(\s+)([\w][\w\-]*[\[\(])/,
        (_m, indent, n1, _sp, n2) => `${indent}${n1} --> ${n2}`
      );
      if (/-->|---/.test(line)) return line;
      const indent = line.match(/^(\s*)/)?.[1] || '';
      const tokens = stripped.split(/\s+/);
      const seqKeywords = /^(participant|actor|activate|deactivate|Note|loop|alt|else|opt|par|rect|end|autonumber|title|as)\b/i;
      if (tokens.length >= 3 && tokens.every(t => /^[\w][\w\-]*$/.test(t)) && !seqKeywords.test(stripped)) {
        return indent + tokens.join(' --> ');
      }
      return line;
    }).join('\n');
  }
  // Fix "NODEA[label] NODEB[label]" — bracket-delimited nodes without
  // arrow between. P26 (2026-04-25): also skip lines inside subgraphs
  // (their bodies are bare identifiers, not connectivity).
  {
    let subgraphDepth2 = 0;
    code = code.split('\n').map(line => {
      const stripped = line.trim();
      if (/^subgraph\b/i.test(stripped)) { subgraphDepth2++; return line; }
      if (/^end\b/i.test(stripped)) {
        if (subgraphDepth2 > 0) subgraphDepth2--;
        return line;
      }
      if (subgraphDepth2 > 0) return line;
      if (/^\s*(subgraph|end|%%)/.test(line)) return line;
      return line.replace(
        /([\]\)\}])(\s+)([\w][\w\-]*)(\s*[\[\(\{])/g,
        (_m, closer, _sp, n2, opener) => `${closer} --> ${n2}${opener}`
      );
    }).join('\n');
  }
  // Fix "NODEA] NODEID |label| NODEB" — pipe-label after a bare identifier that follows a closed node.
  code = code.split('\n').map(line => {
    if (/^\s*(subgraph|end|%%)/.test(line)) return line;
    return line.replace(
      /([\]\)\}])\s+([\w][\w\-]*)\s+(\|[^|]+\|)/g,
      (_m, closer, node, label) => `${closer} --> ${node} -->${label}`
    );
  }).join('\n');
  // Fix "NODE [label]" → "NODE[label]"
  code = code.split('\n').map(line => {
    if (/^\s*subgraph\b/.test(line)) return line.replace(/^(\s*subgraph\s+[\w-]+)\s+\[/, '$1[');
    return line.replace(/(\w)\s+\[/g, '$1[');
  }).join('\n');
  // Sanitize node labels.
  //
  // P26 (2026-04-25): the label-sanitiser strips `<` and `>` (among
  // other chars). PRESERVE `<br>` / `<br/>` by replacing them with a
  // unique placeholder before sanitising and restoring after. Without
  // this, every `<br>` inside a label was turned into a single space
  // by the `<` and `>` strippers, eating multi-line labels.
  const _BR_PLACEHOLDER = '\u0001BR\u0001';
  const sanitizeLabel = (inner: string) => {
    const protectedBr = inner
      .replace(/<br\s*\/?>/gi, _BR_PLACEHOLDER);
    const cleaned = protectedBr
      .replace(/-->/g, ' ').replace(/->/g, ' ')
      .replace(/</g, ' ').replace(/>/g, ' ')
      .replace(/\(/g, ' ').replace(/\)/g, ' ')
      .replace(/_/g, '-')
      .replace(/&(?!amp;|lt;|gt;|#)/g, 'and')
      .replace(/"/g, ' ').replace(/'/g, ' ')
      .replace(/#/g, ' ')
      .replace(/\|/g, '/')
      .replace(/@/g, ' ')
      .replace(/-{2,}/g, ' ')
      .replace(/^[-—=]+|[—=-]+$/g, ' ')
      .replace(/[\[\]]/g, ' ')
      .replace(/\s{2,}/g, ' ')
      .trim();
    // Restore the <br> tags after sanitisation.
    return cleaned.replace(new RegExp(_BR_PLACEHOLDER, 'g'), '<br>');
  };
  // P26 (2026-04-25, third pass) — sanitise the DOUBLE-BRACKET shapes
  // FIRST so the single-bracket regex below doesn't mistake their inner
  // bracket for its own match. Real failing input from project fyfu:
  //     BUCK[["Buck / LT1107CS8-5#PBF / 12V to 5V"]]
  // The single-bracket rect regex used to match the FIRST `[` and the
  // FIRST `]`, capturing `["Buck...V` (note the unbalanced opening `[`
  // captured INTO the inner). After sanitizeLabel stripped the `[` and
  // `"`, the line became `BUCK[Buck / LT1107CS8-5 PBF / 12V to 5V]]`
  // — single open bracket, double close bracket. Mermaid parser:
  //     "Parse error on line 3: ...S8-5 PBF / 12V to 5V]] LDO_5..."
  //
  // Shape inventory the LLM emits (each must be handled as ONE unit):
  //   [["..."]]    subroutine
  //   {{"..."}}    hexagon
  //   (("..."))    circle
  //   (["..."])    stadium
  //   [("...")]    cylinder
  //   [/"..."/]    parallelogram          (already done above)
  //   [\"..."\]    parallelogram_alt      (already done above)
  //   [/"..."\]    trapezoid              NEW
  //   [\"..."/]    trapezoid_alt          NEW
  //   ["..."]      rect                   (single-bracket pass below)
  //   ("...")      round                  (single-bracket pass below)
  //   {"..."}      rhombus                (single-bracket pass below)
  //   >"..."]      flag                   (single-bracket pass below)
  //
  // For each double-bracket shape, sanitise the INNER label as one unit,
  // preserving the outer delimiters.
  code = code.replace(/\[\[([^\[\]]*)\]\]/g, (_m, inner: string) => `[[${sanitizeLabel(inner)}]]`);
  code = code.replace(/\{\{([^{}]*)\}\}/g, (_m, inner: string) => `{{${sanitizeLabel(inner)}}}`);
  code = code.replace(/\(\(([^()]*)\)\)/g, (_m, inner: string) => `((${sanitizeLabel(inner)}))`);
  code = code.replace(/\(\[([^\[\]]*)\]\)/g, (_m, inner: string) => `([${sanitizeLabel(inner)}])`);
  code = code.replace(/\[\(([^()]*)\)\]/g, (_m, inner: string) => `[(${sanitizeLabel(inner)})]`);
  // Mixed-slash trapezoids — strip inner quotes (mermaid doesn't accept
  // quotes inside `[/.../]` family).
  code = code.replace(/\[\/\s*"([^"]*)"\s*\\\]/g, (_m, inner: string) => `[/${inner.trim()}\\]`);
  code = code.replace(/\[\\\s*"([^"]*)"\s*\/\]/g, (_m, inner: string) => `[\\${inner.trim()}/]`);

  // P26 (2026-05-03) — parallelogram `[/.../]`, trapezoid `[\..\]`, mixed-
  // slash variants, AND flag `>...]` shapes: mermaid REJECTS quoted labels
  // on this whole family (the only shape group that does), so unlike the
  // rect / round / rhombus passes below we cannot rescue parser-hostile
  // chars by quoting. Parens / brackets / braces inside the unquoted label
  // re-trigger the round-shape / rect-shape / rhombus-shape parsers and
  // produce errors like:
  //   Parse error on line 15: ...10 MHz Ref<br>(SMA Input)/]
  //   ...^ Expecting 'SQE', 'DOUBLECIRC'
  // Run sanitizeLabel on the inner content so `(`, `)`, `[`, `]`, `{`, `}`
  // (and the rest of the parser-hostile set) get stripped. `<br>` survives
  // via the BR_PLACEHOLDER protection inside sanitizeLabel.
  code = code.replace(/\[\/([^/\]]*)\/\]/g, (_m, inner: string) => `[/${sanitizeLabel(inner)}/]`);
  code = code.replace(/\[\\([^\\\]]*)\\\]/g, (_m, inner: string) => `[\\${sanitizeLabel(inner)}\\]`);
  code = code.replace(/\[\/([^\\\]]*)\\\]/g, (_m, inner: string) => `[/${sanitizeLabel(inner)}\\]`);
  code = code.replace(/\[\\([^/\]]*)\/\]/g, (_m, inner: string) => `[\\${sanitizeLabel(inner)}/]`);
  // Flag `>label]` — only when the `>` is at start-of-token (not part of
  // an arrow `-->` / `==>` and not the `>` closing an HTML `<br>`).
  code = code.replace(/(?<![-=<])(?<!<br)>([^>\]\n]+)\]/g, (_m, inner: string) => `>${sanitizeLabel(inner)}]`);

  // Now the SINGLE-bracket shapes. Each regex now has BOTH a lookbehind
  // (open `[` not preceded by `[`) AND a lookahead (close `]` not
  // followed by `]`) so we don't slice into the inside of an already-
  // sanitised double-bracket shape.
  //
  // Rect `[..]` — but NOT trapezoid `[\..\]`, parallelogram `[/.../]`,
  // or subroutine `[[..]]`.
  code = code.replace(/(?<![\[(])\[(?![\\/[(])([^\]]*)(?<![\\/])\](?![\])])/g, (_m, inner: string) => `[${sanitizeLabel(inner)}]`);
  // Round `(..)` — but NOT circle `((..))` or stadium `(["..."])`.
  code = code.replace(/(?<![(\[])\((?![(\[])([^)]*)(?<![\]])\)(?![)\]])/g, (_m, inner: string) => `(${sanitizeLabel(inner)})`);
  // Rhombus `{..}` — but NOT hexagon `{{..}}`.
  code = code.replace(/(?<!\{)\{(?!\{)([^}]*)\}(?!\})/g, (_m, inner: string) => `{${sanitizeLabel(inner)}}`);
  // Bare quoted strings (e.g. inside subgraph titles, edge labels).
  code = code.replace(/"([^"]+)"/g, (_m, inner: string) => `"${sanitizeLabel(inner)}"`);
  // Edge labels: --> |label| node
  code = code.replace(/\|([^|]+)\|/g, (_m, inner: string) => `|${sanitizeLabel(inner)}|`);
  // State diagram colon-label sanitization — use spaces NOT parentheses
  if (first.startsWith('statediagram')) {
    code = code.split('\n').map(line => {
      const m = line.match(/^(\s*.*?-->\s*\S+\s*:)(.*)$/);
      if (m) {
        const label = m[2].replace(/>/g, ' ').replace(/</g, ' ').replace(/:/g, ',');
        return m[1] + label;
      }
      return line;
    }).join('\n');
  }
  return code;
}
