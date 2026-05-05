/**
 * Dynamic question extraction from AI responses
 * Parses AI's questions, splits compound questions, extracts options
 */

export interface QuestionCard {
  id: string;
  label: string;
  question: string;
  options: string[];
  allowOther?: boolean;
}

const MAX_CARDS = 20;

// ── helpers ────────────────────────────────────────────────────────────────

function stripMd(s: string): string {
  return s.replace(/\*{1,2}([^*]*)\*{1,2}/g, '$1').replace(/\*+/g, '').trim();
}

function cleanChip(raw: string): string {
  let t = raw.trim()
    .replace(/[?!,;:.]+$/, '')
    .replace(/^(or|and|a|an|the|just|only|from|using)\s+/i, '')
    .replace(/\s*\([^)]*\)/g, '')
    .trim();
  if (!t) return '';
  return t.charAt(0).toUpperCase() + t.slice(1);
}

// Words that are filler/meta — never valid as answer chips
const NOISE_WORDS = new Set([
  // filler terminators
  'etc', 'etcetera', 'etc.',
  // availability/identity
  'n/a', 'na', 'tbd', 'tbc', 'tba', 'tbh',
  // vague catch-alls
  'other', 'others', 'none', 'varies', 'various', 'different',
  'custom', 'similar', 'typical', 'standard', 'general', 'specific',
  'based', 'depending', 'optional', 'required', 'relevant', 'applicable',
  // quantity fragments
  'more', 'less', 'any', 'some', 'both', 'either', 'neither', 'all',
  // bare comparatives (meaningless without context)
  'wider', 'narrower', 'higher', 'lower', 'larger', 'smaller',
  'bigger', 'faster', 'slower', 'cheaper', 'better', 'worse',
  // bare structural words
  'above', 'below', 'between', 'within', 'around', 'about', 'yes', 'no',
]);

function isGoodChip(s: string): boolean {
  const c = cleanChip(s);
  if (c.length < 3) return false;           // too short
  if (c.length > 45) return false;          // too long
  if (c.includes('?')) return false;         // question fragment
  if (c.split(/\s+/).length > 5) return false; // too many words
  // reject pure-digit or unit-only tokens like "12V", "48V", "3V" (≤4 chars)
  if (/^\d+\.?\d*\s*[VAWM]?$/.test(c) && c.length <= 4) return false;
  // noise / filler / meta words
  if (NOISE_WORDS.has(c.toLowerCase())) return false;
  // meta-note phrases: "Any size constraints", "Any specific requirement"
  if (/^any\s+/i.test(c)) return false;
  // sentence fragments that are notes not values
  if (/\b(constraint|consideration|specification|parameter|requirement|question|note)\b/i.test(c)) return false;
  // pure comparative/superlative adjectives with -er/-est suffix (wider, highest, etc.)
  if (/^(wid|narrow|high|low|larg|small|fast|slow|cheap|big)(er|est)$/i.test(c)) return false;
  // fragments that start with verbs/connectives — sign of a sentence extraction, not a value
  if (/^(do|does|is|are|has|have|will|would|can|could|should|may|might|please|specify|choose|select|indicate|describe)\b/i.test(c)) return false;
  return true;
}

function dedupe(arr: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of arr) {
    const c = cleanChip(raw);
    if (isGoodChip(c) && !seen.has(c.toLowerCase())) {
      seen.add(c.toLowerCase());
      out.push(c);
    }
  }
  return out.slice(0, 8);
}

// ── is this a Wh-question? (What/Which/How/Where/When/Describe) ─────────────
function isWhQuestion(text: string): boolean {
  return /^\s*(what|which|how|where|when|describe|list|specify|name|tell)\b/i.test(text.trim());
}

// ── main export ────────────────────────────────────────────────────────────

// Derive a short label from a plain question sentence
function labelFromQuestion(q: string): string {
  const stripped = stripMd(q).trim();
  // Try to grab first noun-phrase: up to 3 meaningful words before "?" or verb
  const keywordMatch = stripped.match(
    /\b(supply\s+voltage|operating\s+voltage|input\s+voltage|voltage|frequency|temp(?:erature)?|interface|protocol|power|current|data\s+rate|bandwidth|accuracy|resolution|form\s+factor|environment|regulation|topology|package|quantity|range)\b/i
  );
  if (keywordMatch) {
    const kw = keywordMatch[0].trim();
    return kw.charAt(0).toUpperCase() + kw.slice(1).toLowerCase()
      .replace(/\b\w/g, c => c.toUpperCase());
  }
  // Fall back: first 3–4 words
  const words = stripped.replace(/[?!.,;:].*$/, '').split(/\s+/).slice(0, 4);
  return words.join(' ');
}

export function parseQuestionsFromAI(aiText: string): QuestionCard[] {
  const allCards: QuestionCard[] = [];
  const lines = aiText.split('\n');
  let i = 0;

  while (i < lines.length && allCards.length < MAX_CARDS) {
    const line = lines[i];

    // ── FORMAT A: "1. **Label**: body text" ──────────────────────────────
    // Handles both bold ("**Label**:") and plain ("Label:") labels
    const numberedMatch = line.match(/^(\d+)\.\s+\*{0,2}([^*\n:]+?)\*{0,2}\s*:\s+(.*)$/);
    if (numberedMatch) {
      const label = stripMd(numberedMatch[2].trim());
      const body  = numberedMatch[3].trim();

      if (body.length > 0) {
        allCards.push(createCard(allCards.length + 1, label, body));
        i++;
        continue;
      }

      // No inline body — look ahead for bullet sub-items
      const bullets: string[] = [];
      let j = i + 1;
      while (j < lines.length && j < i + 20) {
        const bl = lines[j].trim();
        if (/^[\-\*\u2022]\s+.+/.test(bl)) {
          bullets.push(bl.replace(/^[\-\*\u2022]\s+/, ''));
          j++;
        } else if (bl.length === 0) {
          j++;
        } else {
          break;
        }
      }

      if (bullets.length > 0) {
        for (const bullet of bullets) {
          allCards.push(createCard(allCards.length + 1, label, bullet));
        }
        i = j;
      } else {
        i++;
      }
      continue;
    }

    // ── FORMAT B: "1. Plain question text?" ──────────────────────────────
    // Handles plain numbered questions with no "Label:" prefix
    const plainMatch = line.match(/^(\d+)\.\s+(.{10,})$/);
    if (plainMatch) {
      const fullText = plainMatch[2].trim();
      // Skip lines that are obviously not questions (too short, purely descriptive)
      const looksLikeQuestion = fullText.includes('?') ||
        /\b(what|which|how|is|are|do|does|will|would|should|can|please|specify|choose|select|indicate|describe|list)\b/i.test(fullText);
      if (looksLikeQuestion) {
        // Collect multi-line: check if next lines continue this point (indented or bullets)
        const bullets: string[] = [];
        let j = i + 1;
        while (j < lines.length && j < i + 10) {
          const bl = lines[j].trim();
          if (/^[\-\*\u2022]\s+.+/.test(bl)) {
            bullets.push(bl.replace(/^[\-\*\u2022]\s+/, ''));
            j++;
          } else if (bl.length === 0 && j < i + 3) {
            j++;
          } else {
            break;
          }
        }

        const label = labelFromQuestion(fullText);
        if (bullets.length > 0) {
          // Each bullet becomes a separate card under the same label
          for (const b of bullets) {
            allCards.push(createCard(allCards.length + 1, label, b));
          }
          i = j;
        } else {
          allCards.push(createCard(allCards.length + 1, label, fullText));
          i++;
        }
        continue;
      }
    }

    i++;
  }

  return allCards.slice(0, MAX_CARDS);
}

// ── card creation ──────────────────────────────────────────────────────────

function createCard(index: number, label: string, questionText: string): QuestionCard {
  const options = extractOptions(questionText);

  const displayQuestion = stripMd(questionText)
    .replace(/\s*\([^)]*\)/g, '')
    .replace(/[?!]\s*$/, '')
    .trim();

  return {
    id: `q-${index}`,
    label,
    question: displayQuestion,
    options: options.length >= 2 ? options : defaultChips(questionText),
    allowOther: true,
  };
}

// ── option extraction ──────────────────────────────────────────────────────

function extractOptions(text: string): string[] {
  // Strip explanatory side-notes in parens
  const clean = text.replace(
    /\(\s*(?:affects|impacts|determines|influences|requires|means|indicates|note:|i\.e\.)[^)]*\)/gi, ''
  );

  // ── P1: parenthetical e.g. list "(e.g., A, B, C)" ────────────────────────
  const parenRe = /\(\s*(?:e\.g\.[,.]?\s*)?([^)]{4,200})\)/gi;
  let pm: RegExpExecArray | null;
  while ((pm = parenRe.exec(clean)) !== null) {
    const inner = pm[1].replace(/^(?:e\.g\.|i\.e\.)[,.]?\s*/i, '');
    const parts = inner
      .split(/\s*,\s*/)           // split on commas only — NOT on "/"
      .map(cleanChip)
      .filter(isGoodChip);
    if (parts.length >= 2) return parts.slice(0, 8);
  }

  // ── P2: "A, B, or C?" list ending at "?" ANYWHERE in sentence ────────────
  // Handles "from a battery system, industrial DC bus, or another source?"
  // The list ends at the first "?" after the "or X" segment.
  const orBeforeQ = /\b([\w][\w\s\-]{2,30}?)(?:,\s*[\w][\w\s\-]{2,30}?)+(?:,?\s*or\s+[\w][\w\s\-]{2,30}?)\s*\?/i;
  const orBQm = clean.match(orBeforeQ);
  if (orBQm) {
    const raw = orBQm[0].replace(/\?\s*$/, '').trim();
    const parts = raw
      .split(/,\s*(?:or\s+)?|\s+or\s+/)
      .map(cleanChip)
      .filter(isGoodChip);
    if (parts.length >= 2) return parts.slice(0, 8);
  }

  // ── P3: "A, B, or C" at end of string (no trailing ?) ────────────────────
  const bodyClean = clean.replace(/\s*\([^)]*\)/g, '').replace(/\s+/g, ' ').trim();
  const orAtEnd = /\b([\w][\w\s\-]{2,30}?)(?:,\s*[\w][\w\s\-]{2,30}?)+(?:,?\s*or\s+[\w][\w\s\-]{2,30}?)\s*[.!]?\s*$/i;
  const orEndM = bodyClean.match(orAtEnd);
  if (orEndM) {
    const raw = orEndM[0].replace(/[.!]\s*$/, '').trim();
    const parts = raw
      .split(/,\s*(?:or\s+)?|\s+or\s+/)
      .map(cleanChip)
      .filter(isGoodChip);
    if (parts.length >= 2) return parts.slice(0, 8);
  }

  // ── P4: simple "X or Y?" near sentence end ───────────────────────────────
  const simpleOr = bodyClean.match(/\b([\w][\w\s\-]{2,25}?)\s+or\s+([\w][\w\s\-]{2,25}?)\s*[?!]?\s*$/i);
  if (simpleOr) {
    const a = cleanChip(simpleOr[1]);
    const b = cleanChip(simpleOr[2]);
    if (isGoodChip(a) && isGoodChip(b) && a.toLowerCase() !== b.toLowerCase()) return [a, b];
  }

  // ── P5: slash-separated tokens "A/B/C" ───────────────────────────────────
  // Use [\w\-]+ so "Wi-Fi" stays whole; require each token ≥ 3 chars
  // Exclude patterns that look like version/voltage (e.g. "3.3V/5V")
  const slashRe = /[\w][\w\-]*(?:\/[\w][\w\-]*){1,}/g;
  let sm: RegExpExecArray | null;
  const slashCandidates: string[] = [];
  while ((sm = slashRe.exec(clean)) !== null) {
    const match = sm[0];
    // Skip voltage/unit patterns like "3.3V/5V", "12V/5V", "48V/28V"
    if (/^\d/.test(match)) continue;
    const parts = match.split('/').map(cleanChip).filter(isGoodChip);
    if (parts.length >= 2) slashCandidates.push(...parts);
  }
  if (slashCandidates.length >= 2) return dedupe(slashCandidates).slice(0, 8);

  // ── P6: "whether X or Y" ─────────────────────────────────────────────────
  const whetherM = clean.match(/whether\s+(.{3,35}?)\s+or\s+(.{3,35}?)(?:\?|$|\s*\()/i);
  if (whetherM) {
    const a = cleanChip(whetherM[1]);
    const b = cleanChip(whetherM[2]);
    if (isGoodChip(a) && isGoodChip(b)) return [a, b];
  }

  return [];
}

// ── domain-specific chip defaults ──────────────────────────────────────────

function defaultChips(text: string): string[] {
  const t = text.toLowerCase();
  const isWh = isWhQuestion(text);

  // Wh-questions that ask for specifics — never Yes/No
  if (isWh && /\b(current|ampere|amp)\b/.test(t))
    return ['<1A per rail', '1-5A per rail', '5-15A per rail', '>15A per rail'];
  if (isWh && /\b(voltage|volt)\b/.test(t))
    return ['3.3V', '5V', '12V', '24V', '48V'];
  if (isWh && /\b(power|watt)\b/.test(t))
    return ['<10W', '10-50W', '50-200W', '>200W'];
  if (isWh && /\b(frequency|freq)\b/.test(t))
    return ['<1 GHz', '1-6 GHz', '6-18 GHz', '>18 GHz'];
  if (isWh && /\b(bandwidth|bw)\b/.test(t))
    return ['Narrowband <1 MHz', '1-50 MHz', 'Wideband >50 MHz'];
  if (isWh && /\b(data.?rate|throughput|baud|bit.?rate)\b/.test(t))
    return ['<1 Mbps', '1-100 Mbps', '100 Mbps-1 Gbps', '>1 Gbps'];
  if (isWh && /\b(temperature|temp|thermal|range)\b/.test(t))
    return ['Commercial (0-70°C)', 'Industrial (-40-85°C)', 'Automotive (-40-105°C)', 'MIL-SPEC'];
  if (isWh && /\b(size|dimension|form.?factor)\b/.test(t))
    return ['1U rack-mount', 'Desktop', 'Handheld', 'PCB-only'];
  if (isWh && /\b(input.?power|drive.?level|input.?signal|pin)\b/.test(t))
    return ['0 dBm', '-10 to 0 dBm', '+10 dBm', 'Variable gain'];

  // Generic Wh-question — don't give Yes/No, give a prompt instead
  if (isWh) return ['Specify value', 'Refer to datasheet', 'Not sure yet'];

  // Domain shortcuts for non-Wh questions
  if (/\b(battery|telecom.?batt|dc.?bus|ac.?mains|solar|industrial.?dc)\b/.test(t))
    return ['Battery system', 'Industrial DC bus', 'AC mains', 'Solar/renewable'];
  if (/\b(bandwidth|modulation\s*bw|channel\s*bw)\b/.test(t))
    return ['Narrowband <1 MHz', '1-50 MHz', 'Wideband 50-500 MHz', '>500 MHz'];
  if (/\b(data\s*rate|bit\s*rate|throughput|baud)\b/.test(t))
    return ['<1 Mbps', '1-100 Mbps', '100 Mbps-1 Gbps', '>1 Gbps'];
  if (/\b(freq|frequency)\b/.test(t) && /\b(center|carrier|operating|lo)\b/.test(t))
    return ['<1 GHz', '1-6 GHz', '6-18 GHz', '>18 GHz'];
  if (/\b(input|supply|bus|main)\s*voltage\b/.test(t))
    return ['5V', '12V', '24V', '48V'];
  if (/\b(max|output|load)\s*current\b/.test(t))
    return ['<1A', '1-10A', '10-50A', '>50A'];
  if (/\b(output|transmit|pa)\s*power\b/.test(t))
    return ['<1W', '1-10W', '10-50W', '>50W'];
  if (/\b(efficiency|pae)\b/.test(t))
    return ['<20%', '20-40%', '40-60%', '>60%'];
  if (/\b(cross.?regulat|derived.?from|buck.?linear|separate.?dc)\b/.test(t))
    return ['Tight cross-regulation', 'Derived from main rail', 'Separate DC-DC stages'];
  if (/\b(control|interface|protocol)\b/.test(t) && /\b(digital|fpga|baseband|spi|i2c)\b/.test(t))
    return ['SPI', 'I2C', 'UART', 'JESD204B', 'Parallel'];
  if (/\b(enable|ttl|analog.?gain|rf.in|rf.out)\b/.test(t))
    return ['Enable/TTL', 'Analog gain', 'SPI/I2C', 'RF in/out only'];
  if (/\b(compliance|certification|regulatory|fcc|ce\b)/.test(t))
    return ['FCC/CE required', 'MIL-STD', 'Lab/R&D only'];
  if (/\b(form.?factor|enclosure|size|rack)\b/.test(t))
    return ['1U rack-mount', 'Desktop', 'Handheld', 'PCB-only'];
  if (/\b(gain|amplif)/.test(t))
    return ['Low (<10 dB)', 'Medium (10-30 dB)', 'High (>30 dB)'];
  if (/\b(isolated|non.?isolated)\b/.test(t))
    return ['Isolated', 'Non-isolated'];
  if (/\b(temperature|thermal|temp\s*range|grade)\b/.test(t))
    return ['Commercial (0-70°C)', 'Industrial (-40-85°C)', 'Automotive (-40-105°C)', 'MIL-SPEC'];
  if (/\b(clock|reference|oscillator|pll|synthesizer)\b/.test(t))
    return ['On-board PLL', 'External reference', 'Crystal oscillator'];
  if (/\b(duty\s*cycle|pulsed|continuous\s*wave)\b/.test(t))
    return ['CW (100%)', 'Pulsed', 'Modulated'];
  if (/\b(input\s*power|drive\s*level|input\s*signal|pin)\b/.test(t))
    return ['0 dBm', '-10 to 0 dBm', '+10 dBm', 'Variable gain'];
  if (/\b(topology|flyback|forward|buck.?boost|boost|buck)\b/.test(t))
    return ['Buck', 'Boost', 'Buck-boost', 'Flyback', 'Forward'];
  if (/\b(cooling|convect|heatsink|forced.?air|conduction)\b/.test(t))
    return ['Forced air (fan)', 'Natural convection', 'Conduction-cooled'];
  if (/\b(protection|overcurrent|overvoltage|reverse)\b/.test(t))
    return ['Overcurrent', 'Overvoltage', 'Reverse polarity', 'All'];

  // Yes/No only for binary Do-you/Is-there questions (never for Wh-questions)
  if (!isWh && /\b(do you|is there|are there|will|should|does|can you|is it|would you|is the|is this|is your)\b/i.test(t))
    return ['Yes', 'No'];

  return ['Yes', 'No'];
}

// ── backwards-compat exports ───────────────────────────────────────────────

export function hasQuestions(aiMessage: string): boolean {
  return /^\d+\.\s+\*/m.test(aiMessage) ||
         /^\d+\.\s+/m.test(aiMessage) ||
         /\b(clarif|need to know|please (specify|confirm|indicate|select))\b/i.test(aiMessage);
}

export function getQuestionsForDesign(desc: string): QuestionCard[] {
  return parseQuestionsFromAI(desc);
}

export function shouldShowQuestions(aiMessage: string, _: string): boolean {
  return hasQuestions(aiMessage);
}
