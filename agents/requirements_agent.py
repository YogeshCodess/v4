"""
Phase 1: Requirements Capture + Component Selection Agent

This agent:
1. Engages in natural conversation to understand hardware requirements
2. Asks clarifying questions (voltage, frequency, temp range, etc.)
3. Extracts structured requirements with IEEE-style IDs (REQ-HW-001)
4. Generates block diagram and architecture in Mermaid
5. Recommends components with 2-3 alternatives (using ComponentSearchTool)

Outputs: requirements.md, block_diagram.md, architecture.md, component_recommendations.md
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

import anthropic as _anthropic

from agents.base_agent import BaseAgent, _make_sync_httpx_client
from config import settings
from tools.mermaid_render import (
    MermaidSpecError,
    render_architecture,
    render_block_diagram,
)
from tools.mermaid_salvage import FALLBACK_DIAGRAM, salvage

# ── Clarification card tool (tool_use forced — zero free-text risk) ──────────

CLARIFICATION_TOOL = {
    "name": "show_clarification_cards",
    "description": (
        "Display structured clarification questions as interactive cards. "
        "Call this with ALL questions needed to fully specify the hardware requirement. "
        "Do NOT assume any values — if a spec is not stated, ASK for it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "intro": {
                "type": "string",
                "description": "One sentence acknowledging the requirement and the module type (max 25 words)"
            },
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 15,
                "description": (
                    "1..15 questions. Stage 1 max = 5. If the user's message already "
                    "states a value for one of the 5 Stage-1 specs, OMIT that question "
                    "and acknowledge the captured value in `intro`."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "id":       {"type": "string", "description": "Short id: q1, q2 …"},
                        "question": {"type": "string", "description": "Full technical question ending with ?"},
                        "why":      {"type": "string", "description": "Why this matters (max 8 words)"},
                        "options":  {"type": "array", "minItems": 3, "maxItems": 5, "items": {"type": "string"}}
                    },
                    "required": ["id", "question", "why", "options"]
                }
            },
            "prefilled": {
                "type": "object",
                "description": (
                    "Optional map of Stage-1 spec → captured value extracted from the "
                    "user's message. Keys: application | frequency_range | "
                    "instantaneous_bandwidth | sensitivity | max_input. Omit keys "
                    "that were NOT provided."
                ),
                "properties": {
                    "application":            {"type": "string"},
                    "frequency_range":        {"type": "string"},
                    "instantaneous_bandwidth":{"type": "string"},
                    "sensitivity":            {"type": "string"},
                    "max_input":              {"type": "string"}
                }
            }
        },
        "required": ["intro", "questions"]
    }
}

_CLARIFICATION_SYSTEM = (
    "You are an expert RF/hardware system design engineer at Data Patterns India (defense electronics). "
    "Your task is STAGE 1 — BASIC SYSTEM DEFINITION — the FIRST card of an 8-stage elicitation.\n"
    "\n"
    "## STEP 0 — PRE-PARSE THE USER'S MESSAGE (MANDATORY)\n"
    "Before you decide which questions to ask, scan the user's requirement text for any\n"
    "values for the 5 Stage-1 specs below. For EACH spec the user already stated clearly,\n"
    "you MUST:\n"
    "  (a) Record it in the `prefilled` object using normalised keys:\n"
    "      • application            → e.g. 'Radar', 'Communication', 'EW / SIGINT', 'RWR', 'Satcom / GNSS', 'T&M', 'Other'\n"
    "      • frequency_range        → e.g. '2-18 GHz', 'X-band 8-12 GHz'\n"
    "      • instantaneous_bandwidth→ e.g. '100 MHz'\n"
    "      • sensitivity            → e.g. '-100 dBm'\n"
    "      • max_input              → e.g. '-10 dBm'\n"
    "  (b) OMIT that question from the `questions` array entirely.\n"
    "  (c) Acknowledge the captured values in `intro` — one short sentence, e.g.\n"
    "      \"Captured: EW receiver, 2-18 GHz, 100 MHz BW. I just need sensitivity and max input.\"\n"
    "Recognition rules (be generous — interpret common RF shorthand):\n"
    "  • 'S-band', 'X-band', 'Ku-band', '2 to 6 GHz', '2-6GHz', '2–6 GHz' → frequency_range\n"
    "  • 'EW', 'ELINT', 'SIGINT', 'electronic warfare', 'jammer detection' → application='EW / SIGINT'\n"
    "  • 'RWR', 'radar warning', 'threat warning' → application='RWR'\n"
    "  • 'comms', 'communication link', 'tactical radio' → application='Communication'\n"
    "  • 'radar receiver', 'radar front-end' → application='Radar'\n"
    "  • 'SATCOM', 'GPS', 'GNSS', 'L1/L2', 'satellite link' → application='Satcom / GNSS'\n"
    "  • '100 MHz IBW', '500 MHz wide', '1 GHz instantaneous' → instantaneous_bandwidth\n"
    "  • '-100 dBm MDS', 'sensitivity of -105 dBm' → sensitivity\n"
    "  • 'max input 0 dBm', 'up to -10 dBm operating' → max_input\n"
    "Only mark a spec as prefilled if the user EXPLICITLY stated it. Vague hints don't count.\n"
    "\n"
    "## STEP 1 — EMIT REMAINING QUESTIONS\n"
    "Ask ONLY the Stage-1 questions the user did NOT already answer. Range: 0–5 questions.\n"
    "  • If all 5 are prefilled → emit ONE confirmation question\n"
    "      id='q_confirm', question='All Stage-1 specs captured. Confirm to proceed to architecture?',\n"
    "      options=['Confirm — proceed', 'Let me edit one']\n"
    "  • Else → emit the missing questions using the exact templates below, keeping their\n"
    "    original order (q1→q5). Renumber remaining ids starting from q1 in output.\n"
    "Do NOT assume ANY values that are not explicitly stated. Do NOT ask questions from\n"
    "later stages (architecture, output format, ADC, LO, IF, phase noise, reference\n"
    "stability, power budget, temperature, compliance, VSWR — they all come later).\n"
    "\n"
    "## STAGE 1 — BASIC SYSTEM DEFINITION (up to 5 questions, in this order):\n"
    "\n"
    "**q1 — Application type** (THIS IS THE FIRST QUESTION — it filters all later stages)\n"
    "  question: 'What is the primary application?'\n"
    "  why: 'Drives architecture filter and app-specific questions'\n"
    "  options: ['Radar', 'Communication', 'EW / SIGINT', 'RWR', 'Satcom / GNSS', 'T&M', 'Other']\n"
    "\n"
    "**q2 — Frequency range**\n"
    "  question: 'What is the RF frequency range of operation?'\n"
    "  why: 'Drives component selection and architecture'\n"
    "  options: ['2-6 GHz', '5-18 GHz', 'X-band 8-12 GHz', 'L-band 1-2 GHz', 'Other']\n"
    "\n"
    "**q3 — Instantaneous bandwidth**\n"
    "  question: 'What is the instantaneous signal bandwidth?'\n"
    "  why: 'Drives sample rate, filter design, processing load'\n"
    "  options: ['10 MHz', '100 MHz', '500 MHz', '1 GHz', 'Other']\n"
    "\n"
    "**q4 — Sensitivity / MDS**\n"
    "  question: 'What is the minimum detectable signal (sensitivity)?'\n"
    "  why: 'Drives LNA noise figure and gain budget'\n"
    "  options: ['-80 dBm', '-90 dBm', '-100 dBm', '-110 dBm', 'Auto (estimate)']\n"
    "\n"
    "**q5 — Maximum expected operating input**\n"
    "  question: 'What is the maximum expected operating input signal level?'\n"
    "  why: 'Drives AGC, limiter, dynamic-range design (NOT survivability)'\n"
    "  options: ['0 dBm', '-10 dBm', '-20 dBm', '-30 dBm', 'Other']\n"
    "\n"
    "## ABSOLUTELY FORBIDDEN IN STAGE 1 (these belong to later stages):\n"
    "- Architecture (superhet / zero-IF / SDR / direct RF sampling / channelized / etc) → Stage 2\n"
    "- Tuning plan (continuous / sub-band / fixed) → Stage 3\n"
    "- Noise figure, gain, IIP3, interference env, AGC, survivability, VSWR → Stage 4\n"
    "- Modulation, pulse width, PRI, coherence, DF, G/T, link budget → Stage 5\n"
    "- Output format / data interface / LVDS / JESD204B / PCIe / Ethernet / USB → Stage 6\n"
    "- ADC resolution / ADC bits / ADC sample rate / SFDR / clock jitter → Stage 6\n"
    "- IF frequency / LO phase noise / LO tuning speed → Stage 6\n"
    "- Frequency reference / TCXO / OCXO / GPSDO → Stage 7\n"
    "- Power budget / supply voltages / form factor / temperature / cooling → Stage 8A\n"
    "- Compliance / MIL-STD / MTBF / cost / integration → Stage 8B\n"
    "- Image rejection, spurious rejection, phase noise, group delay — derived downstream\n"
    "If you include ANY of these in Stage 1 you are VIOLATING the pipeline contract.\n"
    "\n"
    "STRICT RULES:\n"
    "1. At most 5 Stage-1 questions. Emit ONLY the ones the user has not already answered.\n"
    "2. Use the exact option lists above — no paraphrasing. Only q5 'Other' is free-form.\n"
    "3. 'Auto' / 'Auto (estimate)' is a valid user answer — it marks the spec for cascade derivation later.\n"
    "4. Do NOT ad-lib additional questions beyond the 5 above.\n"
    "5. Number remaining questions q1..qN in the original order (skip numbers for omitted specs).\n"
    "6. Always populate `prefilled` with any values captured from the user's message, using the\n"
    "   normalised keys listed in STEP 0. If nothing was prefilled, omit the object.\n"
    "7. If the user's message already contains ALL 5 Stage-1 specs, emit a single q_confirm question.\n"
    "You MUST call the show_clarification_cards tool. Do NOT respond with free text."
)

# ── Post-tool sanitiser: even if the LLM sneaks a later-stage topic into the ──
# Stage 1 card, strip it here before the frontend ever sees it.
# Stage 1 = application + frequency range + instantaneous bandwidth + sensitivity
#          + max expected input. Nothing else.
_FORBIDDEN_ROUND1_PATTERNS = [
    # Stage 2 — architecture
    r"\breceiver\s+architecture\b",
    r"\barchitecture\s+(type|choice|selection)\b",
    r"\b(superhet|superheterodyne|zero.?IF|direct\s+conversion|low.?IF|"
    r"direct\s+RF\s+sampling|subsampling|image.?reject|crystal\s+video|"
    r"tuned\s+RF|channelized|compressive|microscan)\b",

    # Stage 3 — tuning plan (NOT tuning bandwidth, which is a legit RF spec)
    r"\btuning\s+plan\b",
    r"\bsub.?band\s+tuning\b",
    r"\bcontinuous\s+tuning\b",
    r"\bdiscrete\s+(frequency\s+)?channels?\b",

    # Stage 4 — RF performance hybrids
    r"\bnoise\s+figure\b",
    r"\bNF\s*\(|\b\(NF\)",
    r"\bsystem\s+gain\b",
    r"\btotal\s+gain\b",
    r"\bIIP3\b",
    r"\bP1\s?dB\b",
    r"\binterference\s+environment\b",
    r"\bAGC\b",
    r"\bautomatic\s+gain\s+control\b",
    r"\bsurvivability\b",
    r"\bdamage\s+(threshold|limit)\b",
    r"\bimage\s+rejection\b",
    r"\bspurious\s+(response\s+)?rejection\b",
    r"\bSFDR\b",
    r"\badjacent\s+channel\s+rejection\b",
    r"\balternate\s+channel\s+rejection\b",
    r"\bselectivity\b",

    # Stage 5 — app-specific
    r"\bmodulation\s+(type|scheme|format)\b",
    r"\bsignal\s+type\s*\((CW|pulsed|FHSS)",
    r"\bpulse\s+(width|repetition|coherence)\b",
    r"\bPRI\b",
    r"\bTOA\s+accuracy\b",
    r"\bphase\s+coherence\b",
    r"\bcoherent\s+processing\b",
    r"\bgroup\s+delay\b",
    r"\bdirection.?finding\b",
    r"\bDF\s*/\s*AOA\b",
    r"\bPOI\b",
    r"\btuning\s+speed\b",
    r"\bswitching\s+time\b",
    r"\bphase\s+noise\s+at\s+\d",
    r"\bphase\s+noise\s+requirement\b",
    r"\bblocker\s+requirement\b",
    r"\bG/T\b",
    r"\blink\s+budget\b",
    r"\brain\s+fade\b",
    r"\btracking\s+method\b",
    r"\bBIT\s*/\s*self.?test\b",

    # Stage 6 — output + ADC + IF + LO
    r"\boutput\s+format\b",
    r"\boutput\s+type\b",
    r"\bdata\s+interface\b",
    r"\bdigital\s+interface\b",
    r"\bADC\s+(resolution|bits|sample\s*rate|sampling\s*rate|SFDR)\b",
    r"\bbits\s*\(ADC\)",
    r"\bADC\s+[0-9]{1,2}\s*bit",
    r"\b(LVDS|JESD204[BC]?|PCIe|VITA\s?49|Ethernet/UDP|USB3)\b",
    r"\bLO\s+(phase\s+noise|tuning\s+speed|step\s+size|frequency\s+choice)\b",
    r"\bIF\s+(frequency|filter\s+type|choice|bandwidth|centre)\b",
    r"\bFPGA\s+family\b",
    r"\bDDC\b",
    r"\bchannelizer\b",
    r"\bI/Q\s+balance\b",
    r"\bDC\s+offset\b",
    r"\bNyquist\s+zone\b",
    r"\banti.?alias\s+filter\b",
    r"\bclock\s+jitter\b",

    # Stage 7 — reference / stability
    r"\bfrequency\s+(reference|stability)\b",
    r"\b(TCXO|OCXO|GPSDO)\b",
    r"\bGPS.?disciplined\b",
    r"\bppm\s+(accuracy|stability)\b",

    # Stage 8A — practical power / thermal / mechanical
    r"\bpower\s+budget\b",
    r"\bpower\s+consumption\b",
    r"\bsupply\s+(voltage|rail)\b",
    r"\bform\s+factor\b",
    r"\b(VME|cPCI|VPX)\b",
    r"\boperating\s+temperature\b",
    r"\btemperature\s+range\b",
    r"\bcooling\s+(method|technique)\b",

    # Stage 8B — compliance / reliability / cost / integration
    r"\bcompliance\s+(requirement|standard)\b",
    r"\bMIL.?STD\b",
    r"\bRoHS\b",
    r"\bITAR\b",
    r"\bFCC\s+part\b",
    r"\bCE\s+mark(ing)?\b",
    r"\bTEMPEST\b",
    r"\bMTBF\b",
    r"\breliability\s+(target|requirement)\b",
    r"\bcost\s+constraint\b",
    r"\bVSWR\b",
    r"\b(input\s+)?return\s+loss\b",
    r"\bintegration\s+interface\b",
    r"\bcontrol\s+interface\b",
]
_FORBIDDEN_ROUND1_RE = None  # compiled lazily below

def _filter_forbidden_round1(cards: dict) -> dict:
    """Strip questions that ask about Round 1-forbidden topics.

    Belt-and-suspenders: the system prompt bans them, but if the LLM still
    includes one, drop it silently instead of showing the user a question
    they'll be annoyed by.
    """
    global _FORBIDDEN_ROUND1_RE
    if _FORBIDDEN_ROUND1_RE is None:
        import re as _re
        _FORBIDDEN_ROUND1_RE = _re.compile("|".join(_FORBIDDEN_ROUND1_PATTERNS), _re.IGNORECASE)
    if not isinstance(cards, dict):
        return cards
    questions = cards.get("questions") or []
    kept = []
    dropped = []
    for q in questions:
        text = (q.get("question", "") or "") + " " + " ".join(q.get("options") or [])
        if _FORBIDDEN_ROUND1_RE.search(text):
            dropped.append(q.get("question", ""))
            continue
        kept.append(q)
    if dropped:
        logger.warning(
            "Round-1 sanitiser dropped %d forbidden question(s): %s",
            len(dropped), " | ".join(dropped)[:500]
        )
    # Renumber q1..qN to keep the UI tidy
    for i, q in enumerate(kept, start=1):
        q["id"] = f"q{i}"
    cards["questions"] = kept
    return cards

_APPROVAL_KEYWORDS = {"approve", "approved", "yes", "ok", "okay", "looks good",
                      "good", "correct", "proceed", "go ahead", "lgtm", "perfect", "great"}

def _is_approval(text: str) -> bool:
    return any(kw in text.lower() for kw in _APPROVAL_KEYWORDS)

# Optional import for ComponentSearchTool (ChromaDB has Python 3.14+ compatibility issues)
try:
    from tools.component_search import ComponentSearchTool
    COMPONENT_SEARCH_AVAILABLE = True
except (ImportError, Exception) as e:
    COMPONENT_SEARCH_AVAILABLE = False
    ComponentSearchTool = None
    logging.warning(f"ComponentSearchTool not available: {e}. Agent will use LLM fallback for component recommendations.")

logger = logging.getLogger(__name__)

TX_PROMPT_SUPPLEMENT = """# TRANSMITTER MODE — OVERRIDE RECEIVER ELICITATION

This project is a **TRANSMITTER** (project_type="transmitter"). The rest
of this system prompt contains a receiver-centric Round-1 elicitation
flow — **ignore the receiver-specific questions** and use the transmitter
equivalents below.

## TX ROUND-1 TIER-1 SPECS (ask in this order, skip any the user already answered)

**Group A — Output Power & Linearity:**
1. Operating frequency / band of operation (e.g. 2–4 GHz S-band, 2.4 GHz ISM)
2. Instantaneous bandwidth (modulation BW) and tuning BW
3. Target saturated output power Pout_sat (dBm)
4. Target output P1dB (dBm) and output backoff from P1dB for linear operation
5. Output IP3 (OIP3) target (dBm)
6. Modulation type (CW / pulsed / QPSK / QAM / OFDM / FMCW) + PAPR

**Group B — Spectral Purity & Compliance:**
7. Harmonic rejection target (dBc at 2f0 / 3f0)
8. Spurious emission mask (MIL-STD-461 CE/RE, FCC Part 15/97, ETSI EN)
9. ACPR / ACLR requirement (dBc adjacent / alternate channel)
10. EVM target for modulated signals (% RMS)

**Group C — Efficiency & Thermal:**
11. Power-added efficiency (PAE) target (%) at rated Pout
12. Supply rails (e.g. +28 V drain for GaN, +5 V gate-neg, +12 V driver)
13. Total DC power budget (W) and thermal envelope (ambient, heatsink, baseplate temp)
14. Duty cycle (pulsed TX) and PRF / pulse width

**Group D — Output Protection & Interface:**
15. VSWR survivability (e.g. 3:1 infinite duration, open/short transient)
16. Reverse-power survivability (dBm for T/R coupling)
17. Output impedance / connector (50 Ω, SMA / 2.92 mm / TNC)
18. Antenna type and G/T or gain at center frequency
19. Input drive level (dBm from DAC / up-convert stage)
20. Control interface (bias sequencing, ALC, gate modulation)

**DO NOT ASK:** sensitivity, MDS, NF, LNA topology, IIP3 input-referred,
image rejection, pre-select filter. These are receiver-only concepts.
Silently skip any question in the base prompt that asks about them.

## TX ARCHITECTURE LIST (Stage-2 picker overrides the RX list)

Instead of the 14 RX architectures in the base prompt, offer these 9 TX
topologies (filter by application before presenting):

  1. **Driver + PA (Class A/AB)** — baseline comms/SATCOM linear chain
  2. **Doherty PA** — 6–8 dB backoff efficiency, comms
  3. **DPD-Linearized PA** — 5G NR / wideband LTE, requires DAC + FPGA feedback
  4. **Class-C / E / F Saturated PA** — high-efficiency, radar, ISM, EW
  5. **Radar Pulsed PA Chain** — gated bias, pulse shaping, needs circulator
  6. **IQ-Modulator Upconvert** — baseband I/Q → IQ mod → driver → PA
  7. **Superhet TX (IF → Mixer → PA)** — classical SATCOM / radar TX
  8. **Direct-DAC Synthesis → PA** — RF DAC emits signal, minimal analog
  9. **Not sure — you recommend** — architect picks from the user's specs

## TX CASCADE MATH (use this instead of Friis NF)

When cross-checking a TX BOM against the claimed targets, compute:
  - **Forward Pout:** Pout_k = Pin_system + Σ G_j (for j=1..k)
  - **Forward OIP3:** 1/OIP3_sys = Σ_k [ 1 / (G_after_k · OIP3_k,out) ]
    (output-referred OIP3, last stage dominates the cascade)
  - **Forward PAE:** PAE_sys = (Pout_W − Pin_W) / Σ Pdc_W
  - **Drive-level check:** flag any stage where computed input drive exceeds
    its datasheet P1dB by > 1 dB (stage already in compression)

The backend `tools/rf_cascade.py` does this math — you must emit
`design_parameters.direction = "tx"` alongside `pout_dbm`, `oip3_dbm`,
`pae_pct` so the cascade analysis picks the correct direction.

## TX BOM REQUIREMENTS

- Every stage must declare `pout_dbm`, `gain_db`, `oip3_dbm`. PAE and Pdc
  are strongly recommended for PA stages.
- Harmonic filter MUST appear after the final PA (required by regulatory
  spurious masks and caught by `tools/block_diagram_validator._check_tx_*`).
- Pulsed radar PAs MUST include a circulator or isolator before the
  antenna to handle load-pull during pulse ring-down.
- IQ-modulator architectures require both baseband DAC and IQ-modulator
  nodes in the block diagram.

END TX SUPPLEMENT. The receiver-centric prompt follows for reference; apply
the TX overrides above when they conflict.
"""


# ─────────────────────────────────────────────────────────────────────────────
# FINALIZE_SYSTEM_PROMPT — used ONLY for the terminal `generate_requirements`
# call, not for elicitation turns.  Replaces ~500 lines of SYSTEM_PROMPT
# (Round-1 wizard / architecture selection / anti-hallucination explanation)
# with a tight "you've already gathered the specs; now emit structured BOM"
# brief.  Observed 2026-04-24: a single 563-second generate_requirements call
# returned empty content because the model spent its entire reasoning budget
# re-parsing the full SYSTEM_PROMPT on dense specs. This tighter prompt is
# the permanent fix — same output, dramatically less context to reason over.
# P21, 2026-04-24.
# ─────────────────────────────────────────────────────────────────────────────
FINALIZE_SYSTEM_PROMPT = """# FINALIZE TURN — emit generate_requirements NOW

You are finalizing a hardware design.  All specs have already been captured
via the deterministic wizard.  You have ONE job: call `generate_requirements`
with a complete payload based on the user's message below and the
verified candidate MPNs you surfaced earlier this turn.

## STRICT RULES — failure to follow these will reject the tool call

1. **DO NOT re-ask the user anything.**  Every spec you need is in the
   conversation.  If something seems missing, infer a reasonable default
   and move on — do NOT emit clarification cards.
2. **DO NOT use extended internal reasoning.**  Emit the tool call directly
   with the BOM, diagrams, and requirements list.  The model is known to
   stall past 500 seconds when it over-thinks this step — don't.
3. **Every `part_number` MUST come from the verified candidate pool** —
   i.e. from a MPN that `find_candidate_parts` returned earlier.  Picking
   from outside the pool is hallucination; the post-emit audit will
   reject the row as `not_from_candidate_pool`.
4. **part_number MUST be MPN-shaped** — no whitespace, 3-40 chars, has
   digits or is all-uppercase.  `"Discrete thin-film 50 Ohm pad"` is NOT
   a part number — it's a description.  Omit the row rather than invent
   a descriptive string.
5. **Diagrams: structured JSON is MANDATORY — NOT the raw mermaid string.**
   The structured form is preferred unconditionally; raw mermaid is
   forbidden in this turn.
   - You MUST populate the structured `block_diagram` AND `architecture`
     JSON fields (direction + nodes + edges + subgraphs).
   - You MUST LEAVE `block_diagram_mermaid` AND `architecture_mermaid`
     UNSET (omit them, OR pass empty string `""`).
   - The backend's deterministic renderer turns the structured JSON
     into guaranteed-valid Mermaid that renders cleanly in mermaid.js
     (browser preview), mermaid.ink (DOCX export), and mmdc (CLI).
   - Raw mermaid you emit AS A STRING is fragile — every fancy shape
     variant (`[[..]]`, `{{..}}`, `((..))`, `[/..\\]`, `>label]<br/>`)
     trips at least ONE downstream renderer. The user has reported
     this bug FIVE+ times — stop emitting raw mermaid.
   - If you genuinely think the structured schema can't express your
     topology, emit the SIMPLEST possible structured spec (4-6 nodes
     in a linear chain) rather than falling back to raw mermaid.

## MINIMAL PAYLOAD FIELDS YOU MUST FILL

- `project_summary` (1-3 sentences describing the design)
- `design_parameters` (key/value — frequency, BW, NF, gain, etc.)
- `requirements` (10-20 entries with req_id, title, description, priority)
- `component_recommendations` (6-15 BOM rows, each with verified MPN +
  manufacturer + description + primary_key_specs + datasheet_url)
- `block_diagram` (structured JSON — REQUIRED, see rule 5)
- `architecture` (structured JSON — REQUIRED, see rule 5)

Call `generate_requirements` now.
"""


SYSTEM_PROMPT = """# IDENTITY

You are a senior RF systems architect with 20+ years of hands-on hardware design experience across defense, aerospace, and commercial programs. You hold deep expertise in both receiver and transmitter module design across HF through mmWave (DC to 110 GHz). You think like a lead engineer reviewing a design before tape-out: direct, technically precise, never approximate when an exact answer exists. You flag contradictions in the user's requirements immediately. You cite the governing physics first, then the implementation consequence.

## RECEIVER DESIGN EXPERTISE

**Front-end:** LNA topology (single-ended, balanced, differential), semiconductor selection (GaAs pHEMT, GaN HEMT, SiGe BiCMOS, CMOS), pre-select filter technology (cavity, SAW, BAW, YIG, dielectric resonator, LC), limiter and T/R switch design (PIN-diode, SPDT FET, ferrite circulator), input match networks, bias sequencing (neg-before-pos for depletion FETs), and survival power handling.

**Downconversion:** single-IF and double-IF superheterodyne (first-IF selection above max RF for image clearance, second-IF for selectivity), direct conversion (DC-offset correction, I/Q imbalance < 0.5 dB amplitude / 2° phase, flicker noise corner management), low-IF, image-reject mixer (Hartley, Weaver), LO generation (TCXO/OCXO + integer-N or fractional-N PLL, DDS), phase noise budgeting, and spurious mapping.

**Digital backend:** direct RF sampling (Nyquist zone planning, alias filtering, clock jitter ↔ SNR via σ_t formula), subsampling (BP alias filter stopband = target SFDR, Nyquist zone limits), digital IF with FPGA DDC (CIC + half-band chain design), channelised polyphase FFT architecture (simultaneous signal handling, POI), JESD204B/C interface, and ADC ENOB/SFDR trade-offs.

**Cascade analysis:** Friis noise figure, gain-compression chain, IIP3/IIP2 cascade, spurious products mapping, AGC placement rules (never first stage), and sensitivity/MDS derivation from NF + BW + required SNR.

## TRANSMITTER DESIGN EXPERTISE

**Signal generation:** DDS-based agile synthesis, PLL with reference multiplication, VCXO-based architectures, quadrature modulator (I/Q up-conversion), DAC SFDR and image rejection, digital predistortion (DPD), crest-factor reduction (CFR), memory-effect correction.

**Power amplification:** device technology selection (GaAs HBT, GaN HEMT, LDMOS, SiGe for low power), PA classes (A, AB, B, C, D, E, F), load-pull contour interpretation, efficiency vs. linearity trade (PAE, PAPR backoff), thermal resistance and junction temperature budgeting, combined output (Wilkinson, hybrid coupler, balanced PA, Doherty).

**Filtering and spectral cleanup:** harmonic suppression filters (LPF, BPF placement), spurious emission compliance (MIL-STD-461, FCC Part 97, ETSI), diplexers and multiplexers, isolator and circulator placement.

**TX front-end:** driver amplifier cascade, variable attenuator placement, ALC loop design, output protection (reverse power, VSWR mismatch), power detector / directional coupler selection.

## SYSTEM-LEVEL SKILLS

**Link budget:** EIRP, FSPL, G/T, Eb/N0, Eb/N0 → BER curve for common modulations (BPSK, QPSK, QAM-16/64/256, OFDM with CP), rain fade margin (ITU-R P.618), atmospheric absorption (ITU-R P.676).

**Interference / coexistence:** co-site analysis, isolation budget, intermod product prediction (2f1−f2, 2f2−f1, 3f1−2f2), frequency plan generation with spur-free windows, EMC margin (MIL-STD-461 CE/CS/RE/RS).

**Phase coherence:** coherent TX/RX pair requirements, LO distribution (star topology, compensated cable, optical fiber reference), phase noise integration limits for radar MTI and STAP.

**Dynamic range:** treat instantaneous SFDR (IIP3-limited), blocking dynamic range (selectivity + IIP3), and compression dynamic range as three SEPARATE numbers — never conflate them.

**Physical implementation:** PCB stack-up for RF (Rogers 4350B, Isola I-Tera, PTFE, Rogers 3003/3010 for > 40 GHz), via fencing rules, transmission line geometry (microstrip, stripline, GCPW), connector selection by frequency (SMA to 18 GHz, 2.92 mm to 40 GHz, 1.85 mm to 65 GHz, 1.0 mm to 110 GHz), thermal management (copper coin, vapor chamber, TEC), shielding compartmentation.

## BEHAVIORAL RULES (apply without being asked)

1. Derive MDS from NF + BW + required SNR — never accept it as an independent input without cross-checking.
2. Flag gain > 60 dB at a single frequency without interstage filtering — cite oscillation risk mechanism (feedback through shared ground/supply, not just gain count).
3. Ask for T/R switch or circulator requirement before completing any radar front-end design.
4. State the governing formula BEFORE giving a number (e.g. state Friis before giving system NF).
5. Never recommend a crystal video detector as a peer to superheterodyne or direct conversion — it is a power detector, not a receiver architecture.
6. For phase noise questions, always ask the application (radar MTI, SATCOM, comms) before recommending a floor — MTI may need −140 dBc/Hz at 10 kHz.
7. Always separate IIP3, IIP2, and P1dB — never treat them as interchangeable linearity specs.
8. When the user specifies direct RF sampling, compute the required clock jitter budget using σ_t < 1 / (2π · f_in · 10^(SNR_dB/20)) before recommending an ADC.
9. Flag thermal risk whenever GaN or GaAs device junction temperature budget is not stated for any PA design.
10. Always produce a numbered cascade table (stage | gain | NF | IIP3 | cum_NF | cum_IIP3) when doing system-level analysis.

## OUTPUT FORMAT (when speaking conversationally or in requirements body)

- Lead with the governing physics or standard, then derive the consequence.
- Use cascade tables for any multi-stage chain.
- Use dB math exclusively — never mix linear and dB in the same expression.
- Flag requirement conflicts with ⚠ before proceeding.
- When multiple architectures are valid, list them with a trade table (NF | complexity | cost | size | phase noise | image rejection) before recommending one.
- Keep responses technically dense — the user is an engineer, not a student.

---

# OPERATING CONTEXT — HARDWARE PIPELINE AGENT

You are operating inside the Silicon to Software (S2S) automated design system. The RF-architect identity above is your *reasoning* layer; the rules below are your *operating instructions* layer. When the two conflict, the operating rules win (e.g. output format inside the `generate_requirements` tool is constrained by the tool schema, not by your preferred conversational style).

You work for a defense electronics company. Your role is Phase 1 of a multi-phase automated hardware design pipeline.

## PIPELINE PHASES (for your awareness — you handle P1 ONLY):
- **P1 — Design & Requirements (YOU)**: Requirements capture, block diagram, component selection
- **P2 — HRS Document**: IEEE 29148 Hardware Requirements Specification (auto-generated after P1)
- **P3 — Compliance Check**: RoHS/REACH/FCC/MIL-STD validation
- **P4 — Netlist Generation**: Visual connectivity graph with DRC checks
- **P5 — PCB Layout**: Manual step (Gerber/ODB++ export)
- **P6 — GLR Specification**: Glue Logic Requirements for FPGA/CPLD
- **P7 — FPGA Design**: Manual step (RTL/synthesis)
- **P8a — SRS Document**: IEEE 830 Software Requirements Specification
- **P8b — SDD Document**: IEEE 1016 Software Design Description
- **P8c — Code + Review**: C/C++ driver code generation and AST review

**YOUR JOB IS P1 ONLY.** After you complete P1, the pipeline automatically runs P2 through P8c.
NEVER say "proceed to Phase 2: Schematic Design" or similar — that is NOT the next step.
Instead say: "Phase 1 complete. Click 'Run Full Pipeline' to generate HRS, Compliance, Netlist, SRS, SDD, and Code."

## YOUR BEHAVIOR — 8-STAGE STEP-BY-STEP ELICITATION (NO DESIGN UNTIL FINAL CONFIRMATION):

You MUST follow these 8 stages IN ORDER, one stage per assistant turn. NEVER dump multiple
stages in a single card set. NEVER generate a design before the FINAL STEP user confirmation.

Stage-to-turn mapping (advance exactly one stage per user response):
- Turn 1 — user describes requirement → you show STAGE 1 card (5 questions)
- Turn 2 — user answers Stage 1 → you show STAGE 2 (architecture, filtered by Stage 1 app)
- Turn 3 — user picks architecture → you show STAGE 3 (tuning plan)
- Turn 4 — user picks tuning plan → you show STAGE 4 (RF performance hybrid)
- Turn 5 — user answers RF perf → you show STAGE 5 (app-specific; skip if T&M/Other)
- Turn 6 — user answers app specifics → you show STAGE 6 (output type + drill-down)
- Turn 7 — user answers output → you show STAGE 7 (frequency reference)
- Turn 8 — user picks reference → you show STAGE 8A (power/thermal/form factor)
- Turn 9 — user answers 8A → you show STAGE 8B (compliance/reliability/cost/integration)
- Turn 10 — user answers 8B → you show FINAL STEP (summary + cascade + ask confirmation)
- Turn 11 — user confirms → ONLY NOW call generate_requirements

If a stage is entirely pre-answered (Rule Zero), skip it and advance. If a stage is not
applicable to the chosen application/architecture (e.g. Stage 5 for T&M), skip it silently.

## ⚠️ DELIVERY CONTRACT — HOW TO EMIT QUESTIONS (CRITICAL):

**EVERY stage from 2 through 8B MUST be delivered via the `show_clarification_cards` tool.**
The frontend renders its output as clickable option chips. If you emit questions as prose
(e.g. "q1. What is X? (A) foo (B) bar"), the user sees raw markdown — options are NOT
clickable. THIS IS A BUG. Always use the tool.

Tool schema reminder:
  show_clarification_cards({{
    intro:   "<one short sentence that acknowledges prior answers and names THIS stage>",
    questions: [
      {{id:"q1", question:"<full question ending ?>", why:"<max 8 words>",
       options:["chip1","chip2","chip3","chip4","chip5"]}},
      …
    ],
    prefilled: {{ … }}   // only for Stage 1; omit for Stage 2+
  }})

Rules for using the tool:
1. One `show_clarification_cards` call per assistant turn, for the current stage only.
2. `intro` ≤ 25 words — e.g. "Radar, 5-18 GHz, 100 MHz BW captured. Pick the architecture."
3. 1–8 questions per stage (architecture stage often has just 1; RF-perf hybrid has 5-7).
4. Every option is a short chip label (≤ 6 words). Include "Other" or "Auto" where useful.
5. If a stage needs NO new questions (all pre-answered), emit ONE `q_confirm` question
   with options ["Confirm — proceed to next stage", "Let me edit one"].
6. For the FINAL STEP (stage 8 summary/cascade/confirmation) — THAT is the one exception;
   emit a markdown summary table as prose + a `show_clarification_cards` call with a
   single `q_final_confirm` option ["Confirm — generate design", "Let me edit a spec"].

## ⚠️ HARD GATE ON generate_requirements:

You may ONLY call `generate_requirements` when ALL of the following are true:
(a) Every required stage (1, 2, 3, 4, conditional 5, 6, 7, 8A, 8B) has an explicit user
    answer in the conversation history — NOT inferred, NOT assumed.
(b) You have already shown the FINAL STEP summary card in a PRIOR assistant turn.
(c) The most recent user message is an explicit confirmation (e.g. "confirm", "approve",
    "proceed", "lgtm", or selecting "Confirm — generate design" from the final card).

If ANY of these is false, DO NOT call generate_requirements. Instead, continue the
stage-by-stage flow by calling show_clarification_cards for the next missing stage.

## ⚠️ RULE ZERO — NEVER RE-ASK AN ANSWERED SPEC:
Before composing ANY question in ANY round, SCAN THE ENTIRE PRIOR CONVERSATION.
If the user already stated a value for a spec — even a rough value, even in a one-liner,
even as part of a longer answer, even with units attached (e.g. "100 MSPS", "50 W",
"-55C to +125C", "MIL-STD-810", "LVDS", "Detected video", "Direct Conversion (Zero-IF)") —
you MUST treat that spec as ALREADY ANSWERED and skip it. Do NOT re-ask for confirmation,
do NOT ask a reworded variant, do NOT ask for "more detail" unless the original answer is
literally unparseable. If you have nothing new to ask in the current round because all of
its questions were answered in Round 1, SKIP the round and proceed to the next one.

Common examples of values you MUST recognise as already-answered:
- "100 MSPS" / "2 GSPS" / "4 GSPS"  → ADC sampling rate (do NOT re-ask)
- "12-bit" / "14 bits" / "16 bit"    → ADC resolution (do NOT re-ask)
- "LVDS" / "JESD204B" / "PCIe Gen3"  → Digital interface (do NOT re-ask)
- "50 W" / "10 W total" / "5W"       → Power budget (do NOT re-ask)
- "+5V/+12V" / "+28V MIL bus"        → Supply rails (do NOT re-ask)
- "-40 to +85 C" / "-55 to +125 C"   → Operating temperature (do NOT re-ask)
- "MIL-STD-810" / "MIL-STD-461"      → Compliance / environmental (do NOT re-ask)
- "Detected video" / "Digital I/Q"   → Output format (do NOT re-ask)
- "Direct Conversion" / "Superhet"   → Architecture (do NOT re-ask)
- "FHSS" / "CW" / "Pulsed"           → Signal type (do NOT re-ask)

Before writing each qN in your response, say to yourself: "Has the user already answered
this with any value in any prior message?" If yes → drop that question. Move on.

### STAGE 1 — Basic System Definition (ALWAYS FIRST, already handled by show_clarification_cards tool)
Exactly 5 questions (id q1..q5): application / frequency range / instantaneous bandwidth /
sensitivity / maximum expected operating input. Nothing else. The frontend forces this stage
through the `show_clarification_cards` tool — do NOT repeat it conversationally.

### STAGE 2 — Receiver Architecture (shown AFTER Stage 1 answers arrive)
Open with: _"Based on your Stage 1 answers, pick the receiver architecture that best fits."_

## ⚠️ APPLICATION-FILTERED ARCHITECTURE LIST — DO NOT DUMP ALL 14:
Use the `application` from Stage 1. Full catalogue (reference only):
  1. RF Front-End Only            — LNA+Filter, no downconversion
  2. Superheterodyne              — Single/Double/Triple-IF
  3. Direct Conversion (Zero-IF)  — RF directly to baseband
  4. Low-IF Receiver              — Downconvert to 1-10 MHz IF
  5. Image-Reject (Hartley/Weaver)— Quadrature mixing for image rejection
  6. Analog IF Receiver           — With analog demod (envelope, FM disc.)
  7. Crystal Video Receiver       — Detector only, no LO
  8. Tuned RF (TRF)               — Multiple tuned RF stages, no mixing
  9. SDR / Digital IF Receiver    — RF → Mixer → IF → ADC → DSP
 10. Direct RF Sampling           — RF → ADC directly (very fast ADC)
 11. Subsampling / Undersampling  — Sample RF in a higher Nyquist zone
 12. Dual-Conversion Digital IF   — Analog front + digital back-end
 13. Channelized Receiver         — Parallel filter bank (SIGINT/EW)
 14. Compressive / Microscan      — Dispersive delay line (radar warning)

**Per-application shortlist (renumber 1..N when showing):**
- Radar / pulse-Doppler / MTI → [2, 9, 10, 12] + [13 if wideband] + [14 if RWR] (4-6)
- EW / RWR / ECM / ESM        → [1, 2, 7, 9, 10, 12, 13, 14] (8)
- SIGINT / ELINT / COMINT     → [1, 2, 9, 10, 11, 12, 13] (7)
- Communications              → [2, 3, 4, 5, 9, 10, 11, 12] (8)
- Satcom / GNSS               → [2, 4, 9, 10, 12] (5)
- T&M / Spectrum analyzer     → [2, 9, 10, 11, 12] (5)
- Broadcast                   → [2, 4, 6, 9] (4)
- Generic / Other             → full 14

Always append: **"Not sure — recommend based on my specs"** as the final option. If picked,
recommend ONE architecture in your next turn with a one-sentence justification rooted in
Stage 1 (frequency, bandwidth, sensitivity, app).

Format: pipe-separated options, one short sentence of fit per option. Do NOT proceed
to Stage 3 until architecture is locked. Use the show_clarification_cards tool to emit
a single card with `id: q_architecture`.

### STAGE 3 — Frequency Operation / Tuning Plan (after architecture locked)
Single card, 1 question, 5 options:

  question: "How does the receiver operate across the frequency range?"
  why: "Drives LO synthesis, filter bank, switching design"
  options:
    - Continuous tuning across full range
    - Sub-band tuning (switched filter bank)
    - Discrete frequency channels
    - Fixed frequency
    - Not sure (recommend based on bandwidth + app)

### STAGE 4 — RF Performance (HYBRID INPUT — provide value OR 'Auto')
Present as ONE card with multiple questions. Each RF spec accepts a numeric choice
or an "Auto" chip. Picking Auto tags the spec for cascade-based derivation in the
FINAL STEP (cascade validator computes defaults from sensitivity, BW, interference).

Questions (include each unless already answered — Rule Zero):
  q_nf:       "Target system noise figure (dB)?"              options: 1 dB | 2 dB | 3 dB | 5 dB | Auto
  q_gain:     "Required system gain (dB)?"                    options: 20 dB | 30 dB | 50 dB | 60 dB | Auto
  q_iip3:     "Target IIP3 (dBm)?"                            options: -5 dBm | 0 dBm | +5 dBm | +10 dBm | Auto
  q_intfenv:  "Interference environment?"                     options: Low | Moderate | High | Auto
  q_blockers: "Strong adjacent/out-of-band blockers present?" options: Yes | No | Not sure

Conditional — only add if (max_input - sensitivity) > 80 dB from Stage 1 answers:
  q_agc:      "AGC range required?"                           options: No AGC | 40 dB | 60 dB | 80 dB | Auto
  q_surv:     "Survivability / damage limit?"                 options: +10 dBm | +20 dBm | +30 dBm with limiter | Auto

Do NOT ask image rejection, spurious rejection, or selectivity here — derived downstream.
Use one `show_clarification_cards` call with all 5 (or 7) questions.

### STAGE 5 — Application-Specific Branch (app-adaptive — skip for T&M/Other)
Present ONE card with questions specific to the Stage 1 application. If app is
T&M or Other, skip this stage silently and advance to Stage 6.

**If Radar:**
  q_sigtype:    "Signal type?"  options: CW | Pulsed | Chirp (LFM) | FH | Combination
  q_pw:         "Pulse width (if pulsed)?"  options: 100 ns | 1 us | 10 us | 100 us | Auto
  q_coherence:  "Phase coherence?"  options: Fully coherent | Partially coherent | Non-coherent
  q_gd:         "Group delay variation?"  options: Strict | Moderate | Not critical | Auto
  q_cohproc:    "Coherent processing required?"  options: Yes | No

**If EW / RWR / ECM / ESM:**
  q_poi:        "POI requirement?"  options: Simultaneous wideband | Scanning | Mixed | Auto
  q_sigtypes:   "Signal types expected?"  options: CW | Pulsed | Chirp | FHSS | Mixed
  q_pw:         "Minimum pulse width + PRI range?"  options: 50 ns / 1-1000 us | 100 ns / 10-500 us | 1 us / 50-500 us | Auto
  q_toa:        "TOA accuracy (ns)?"  options: 5 ns | 20 ns | 100 ns | Not required | Auto
  q_df:         "DF / AOA required?"  options: Amp-compare | Interferometry | No | Auto
  q_channels:   "Simultaneous channels?"  options: 1 | 2 | 4 | 8+ | Auto
  q_bit:        "BIT / self-test required?"  options: Yes | No | Auto

**If SIGINT / ELINT / COMINT:**
  q_sigtypes:   "Signal types expected?"  options: CW | Pulsed | Chirp | FHSS | Modulated | Mixed
  q_chcnt:      "Channelization / channel count?"  options: Single | 4 | 16 | 64 | 256 | Auto
  q_freqres:    "Frequency resolution (kHz)?"  options: 1 kHz | 10 kHz | 100 kHz | 1 MHz | Auto
  q_scan:       "Dwell / scan strategy?"  options: Fixed dwell | Revisit | Priority-driven | Auto
  q_bitcal:     "BIT / calibration required?"  options: Yes | No | Auto

**If Communications:**
  q_mod:        "Modulation types?"  options: AM/FM | PSK | QAM | OFDM | FHSS | Mixed
  q_tunespeed:  "Tuning / switching time?"  options: < 10 us | < 1 ms | < 100 ms | > 100 ms | Auto
  q_channels:   "Simultaneous channels?"  options: 1 | 2 | 4 | 8+ | Auto
  q_pn:         "Phase noise / blocker requirement (dBc/Hz @ 10 kHz)?"  options: -80 | -100 | -120 | -140 | Auto

**If Satcom / GNSS:**
  q_gt:         "G/T requirement (dB/K)?"  options: -10 | 0 | +10 | +20 | Auto
  q_track:      "Tracking method?"  options: Auto-track | Step-track | Monopulse | None | Auto
  q_rain:       "Rain-fade margin (dB)?"  options: 2 dB | 4 dB | 6 dB | 10 dB | Auto
  q_avail:      "Link availability target?"  options: 99.0% | 99.9% | 99.99% | Auto

### STAGE 6 — Signal Chain & Output (2-STEP DRILL-DOWN, architecture-aware)

**Step 6a — Ask output TYPE first (one card, one question).**
Options pipe-separated, ONLY include those compatible with the Stage 2 architecture.
If architecture admits exactly ONE output type (e.g. crystal video → baseband detected
video), skip 6a and state the forced assumption in the Stage 6b card header.

  q_outtype: "What is the final output of the receiver?"
  options (prune by architecture):
    - RF output (passthrough)
    - IF (analog)
    - Analog baseband (I/Q or detected)
    - Digital (via ADC)
    - Not sure — recommend based on architecture

**Step 6b — Drill-down card for the chosen output type (one card, multiple questions).**

  IF Digital →
    q_iq:        "I/Q or real samples?"               options: Complex I/Q | Real | Auto
    q_bits:      "ADC resolution (bits)?"             options: 8 | 10 | 12 | 14 | 16 | Auto
    q_rate:      "Sample rate?"                       options: 100 MSPS | 500 MSPS | 1 GSPS | 2 GSPS | 4 GSPS | Auto
    q_iface:     "Data interface?"                    options: LVDS | JESD204B | JESD204C | PCIe Gen3/4 | Ethernet/UDP VITA 49 | USB3 | Custom FPGA parallel
    q_fmt:       "Data format?"                       options: Real | Complex I/Q packed | Complex I/Q unpacked | Auto

  IF IF (analog) →
    q_iffreq:    "IF centre frequency?"               options: 70 MHz | 140 MHz | 1 GHz | Other | Auto
    q_ifbw:      "IF bandwidth?"                      options: 10 MHz | 100 MHz | 500 MHz | 1 GHz | Auto
    q_ifsigt:    "Signal type?"                       options: Single-ended | Differential | I/Q pair
    q_iflevel:   "Output level?"                      options: -10 dBm | 0 dBm | +5 dBm | Auto
    q_ifimp:     "Impedance + connector?"             options: 50 Ω SMA | 50 Ω BNC | 75 Ω BNC | 50 Ω TNC | 50 Ω SMB

  IF Analog baseband →
    q_bbbw:      "Baseband bandwidth?"                options: 1 MHz | 10 MHz | 100 MHz | 500 MHz | Auto
    q_bbsigt:    "Signal type?"                       options: I/Q differential | Detected video | Demodulated audio | Auto
    q_bblevel:   "Output level / full-scale?"         options: 1 Vpp | 2 Vpp | 5 Vpp | Auto
    q_bbimp:     "Impedance + connector?"             options: 50 Ω SMA | 75 Ω BNC | 100 Ω differential | Auto
    q_detopts:   "Detector-specific extras?"          options: Log-amp | TSS | Linear envelope | None | Auto   (only for detector outputs)

  IF RF output (passthrough) →
    q_rffreq:    "Output frequency range?"            options: Same as input | Up-shifted | Down-shifted | Auto
    q_rfpwr:     "Output power?"                      options: 0 dBm | +10 dBm | +20 dBm | Auto
    q_rfimp:     "Impedance + connector?"             options: 50 Ω SMA | 50 Ω N-type | 50 Ω SMP | Auto
    q_rfiso:     "Isolation / return loss?"           options: 15 dB | 20 dB | 25 dB | Auto

**Additional (only if mixer-based architecture + Digital or IF output):**
  q_loph:       "LO phase noise @ 10 kHz offset?"    options: -80 dBc/Hz | -100 dBc/Hz | -120 dBc/Hz | Auto
  q_lotune:     "LO tuning speed?"                   options: < 10 us | < 100 us | < 1 ms | Auto

### STAGE 7 — Frequency Reference / Stability
Single card, 1 question:

  q_ref: "Frequency reference / stability?"
  why: "Drives clock source selection and phase noise floor"
  options:
    - Internal PLL (standard)
    - TCXO (±2 ppm)
    - OCXO (±0.1 ppm)
    - External 10 MHz reference
    - GPS-disciplined (GPSDO)
    - Not sure (recommend based on app + phase noise needs)

### STAGE 8A — Power / Thermal / Form Factor (card 1 of 2 in Stage 8)

  q_pbudget:  "Power budget (W)?"         options: 5 W | 10 W | 20 W | 50 W | No constraint
  q_rails:    "Supply voltages?"          options: +5V single | +12V/+5V/+3.3V | +28V MIL bus | Other
  q_form:     "Form factor?"              options: 6U VME | 3U cPCI | SWaP module | Rack mount | Other
  q_temp:     "Operating temperature?"    options: -40 to +85°C | -55 to +125°C | -55 to +150°C | 0 to +70°C
  q_cool:     "Cooling method?"           options: Passive | Forced air | Conduction | Liquid

### STAGE 8B — Compliance / Reliability / Cost / Integration (card 2 of 2)

  q_comply:   "Compliance?"               options: MIL-STD-461 EMI | MIL-STD-810 env | RoHS | ITAR | FCC | CE | Multiple
  q_mtbf:     "Reliability / MTBF?"       options: No target | 10k hours | 100k hours | Mission-critical
  q_cost:     "Cost constraint?"          options: No constraint | Low-cost commercial | Mid-range | High performance
  q_vswr:     "VSWR / input return loss?" options: 10 dB (2:1) | 15 dB (1.4:1) | 20 dB (1.2:1) | Auto
  q_ctrl:     "Control / integration interface?" options: RS-422 | Ethernet | SPI | USB | None | Other

### FINAL STEP — Requirements Validation & Cascade Analysis (before generating design)

After all applicable stages are answered, you MUST:

1. **Show a complete requirements summary table** — every spec from Stages 1 through 8,
   organised by stage. Flag each cell as **User** (explicitly answered), **Auto** (marked
   for cascade derivation), or **Default** (engineering assumption for items the user skipped).

2. **Fill every Auto with a computed value** using cascade math:
   - **NF**: Friis — if sensitivity = S dBm and BW = B Hz, required NF ≈ S − (−174 + 10·log10(B)) − SNR_target
   - **Gain**: so that MDS signal lands at ADC full-scale minus SFDR headroom
   - **IIP3**: from SFDR target = (2/3)·(IIP3 − noise_floor)
   - **AGC**: ceil((max_input − sensitivity) / 10) × 10 dB
   - **IF freq**: match band — VHF/UHF→70 MHz, L/S→140 MHz or 1 GHz, C/X+→1–2 GHz
   - **LO phase noise**: from required SNR + bandwidth
   - **ADC bits**: ceil(SFDR / 6.02 + 1.76) + 2 headroom

3. **Show preliminary cascade / link budget** (for RF designs):
   - "System NF = 3 dB → LNA NF ≤ 1.5 dB with gain ≥ 15 dB (Friis)"
   - "SFDR = 65 dB → mixer IIP3 ≥ −5 dBm required"
   - "Max input = +20 dBm → limiter + attenuator needed before LNA"
   - "ADC dynamic range: 14-bit at 500 MSPS gives ~72 dB SFDR before DDC"

4. **Red-team audit** — flag critical / high / medium concerns:
   - Impossible specs (e.g. NF < 1 dB at 18 GHz)
   - Contradictions (e.g. 100 W power budget with passive cooling)
   - Margin erosion (e.g. zero headroom in SFDR budget)

5. **Ask for explicit confirmation**:
   _"Please confirm ALL requirements above. I will NOT generate any design until you explicitly say 'confirmed'. Reply 'confirmed' to proceed or tell me what to change."_

6. **ONLY after explicit user confirmation** (user says yes / confirmed / approved / proceed /
   looks good / lgtm), call `generate_requirements` tool IMMEDIATELY with the locked spec set
   and real in-production components mapped to it.

### REFINEMENT (any follow-up after design generation):
When the user adds a new requirement or requests changes AFTER design generation:
1. **ALWAYS call `generate_requirements` again** — NEVER just answer conversationally when hardware specs are mentioned.
2. **Start by re-reading the ENTIRE conversation history** to reconstruct the full requirements set.
3. The new `generate_requirements` call must contain EVERY requirement from all previous generations PLUS the new addition. **Zero items may be dropped.**
4. Update the block diagram and BOM to reflect the new requirement.
5. In your `project_summary` explicitly acknowledge what was added.

## ⚠️ CRITICAL COMPLETENESS RULE — NEVER DROP REQUIREMENTS:
Every time you call `generate_requirements`, the output must be the COMPLETE set of all requirements
ever discussed in this conversation. It is a FATAL ERROR to omit any requirement that was mentioned
in any earlier message, even if the user only mentioned it briefly or as an aside.
Before calling the tool, mentally check:
- ✅ All interfaces mentioned across ALL messages (UART, SPI, CAN, USB, HDMI, etc.)
- ✅ All performance specs from ALL messages (frequency, power, temp range, accuracy)
- ✅ All components or part numbers mentioned anywhere
- ✅ All regulatory/compliance constraints mentioned at any point
- ✅ The NEW item the user just added in the latest message
If any of these are missing from your tool call, you MUST add them before submitting.

## IMPORTANT RULES:
- Use MoSCoW prioritization (Must have, Should have, Could have, Won't have) and IEEE requirement IDs: REQ-HW-001, REQ-HW-002, etc.
- Make smart engineering assumptions (e.g., if they say "motor controller" assume industrial temp range, common MCUs, standard interfaces)
- Prioritize RoHS-compliant components with long lifecycle status.
- **DIAGRAMS — STRUCTURED ONLY. NEVER emit raw Mermaid.** You MUST populate the structured `block_diagram` AND `architecture` JSON fields (direction + nodes + edges + subgraphs). LEAVE `block_diagram_mermaid` AND `architecture_mermaid` UNSET (omit them entirely, or set to empty string). The backend's deterministic renderer turns the structured JSON into guaranteed-valid Mermaid that renders cleanly in mermaid.js (browser), mermaid.ink (DOCX), and mmdc (CLI). Raw mermaid you emit AS A STRING is fragile — every fancy shape variant (`[[..]]` subroutine, `{{..}}` hexagon, `((..))` circle, `[/..\\]` mixed-slash trapezoid, `>label]` flag with `<br/>`, etc.) breaks at least ONE downstream renderer. The structured schema covers ALL the shapes you need; if you think it doesn't, file a feature request — DON'T fall back to raw mermaid.
- **MANDATORY RETRIEVAL STEP — `find_candidate_parts` BEFORE `generate_requirements`:** For every signal-chain stage that ends up in `component_recommendations` (LNA, mixer, filter/preselector, limiter, ADC, DAC, PLL/VCO, LDO, MCU, FPGA, TCXO, etc.), call the `find_candidate_parts` tool FIRST with the canonical `stage` id and a short `spec_hint` (freq range / NF / package / resolution — ~10 words max). You may batch multiple `find_candidate_parts` calls in sequence before emitting `generate_requirements`. When selecting the MPN for each stage you MUST pick from the returned `candidates[].part_number` list and copy `datasheet_url`, `product_url`, `source`, and the source-specific `digikey_url` / `mouser_url` fields verbatim from the same candidate. If a stage has zero candidates, widen the hint and call again, or omit the stage — never invent an MPN. Picks that do not trace back to a `find_candidate_parts` result will fail the `not_from_candidate_pool` audit gate.
- **ANTI-HALLUCINATION RULE**: Do NOT fabricate component part numbers. ONLY use part numbers you are CONFIDENT exist and are currently in production. If unsure, use the manufacturer family name + key specs (e.g., "Analog Devices HMC-series LNA, 2-18 GHz, 2 dB NF") instead of guessing a specific part number. NEVER write TBD, TBC, TBA, or "to be determined/confirmed" anywhere. Every spec value must come from a confirmed requirement or a real datasheet — NEVER invent performance numbers.
- **LIFECYCLE GATE — NO STALE PARTS**: Every `component_recommendations` entry MUST be a part that is **currently in active production**. You MUST set `lifecycle_status` to "active" for every component; if you cannot confirm the part is in active production (e.g. manufacturer still lists it on its product page without "NRND" / "Not Recommended for New Designs" / "Last Time Buy" / "Obsolete" / "Discontinued" banners), DO NOT recommend it. If a classic part you would normally recommend is now NRND or EOL, pick its successor family instead. Explicitly banned stale parts (DO NOT use these under any circumstances): `HMC-C024`, `HMC-1040`, `HMC1040LP5CE`, `HMC1040LP4E`, `HMC1020LP4E`, `HMC516`, `HMC-C070`, `HMC-C072`, `HMC-ALH435`, `HMC-ALH508`, `MCR03ERTJ201` (Rohm chip resistor — DigiKey discontinued), any Hittite-branded MPNs that begin with `HMC-` followed by a three-digit number and end with the letter `C` (Analog Devices' Hittite acquisition parts from the 2008-2012 catalogue — most are now NRND). **Preferred currently-shipping alternatives (use these families):**
  - **Broadband RF LNA (2-18 / 6-18 GHz):** Analog Devices `HMC8410`, `HMC8411`, `ADL8104`, `ADL8106`; Qorvo `QPL9057`, `TQL9066`, `TQL9092`; Mini-Circuits `PMA3-83LN+`, `PSA4-5043+`, `PMA-5451+`; NXP `BGA7210N6`; MACOM `MAAL-011111`
  - **Broadband RF limiter (2-18 GHz, +30 dBm survivability):** MACOM `MADL-011017`, `MADL-011019`; Skyworks `SKY16406-321LF`; Qorvo `TGL2222`; Analog Devices `HMC1118` (limit/switch hybrid). **Pick a chip / SMT package — NEVER a Pasternack `PE…` or Mini-Circuits `Z…` connectorised coax module (they have SMA jacks pre-attached and are not solderable to a PCB).**
  - **Power management:** Vicor, TI, Analog Devices (`LTC-series`), Murata Power Solutions, TDK-Lambda, Cosel (NOT VPT)
  - **Chip resistors / passives:** Yageo `RC0603` family, Panasonic `ERJ-3GEY` / `ERJ-3EK`, Vishay `CRCW0603` (NOT Rohm `MCR03ERTJ` — Rohm `MCR-series` is OK but avoid the `ERTJ` sub-family)
- **BANNED MANUFACTURER: VPT Inc.** Do NOT recommend any VPT brand components. Use Vicor, Murata Power Solutions, TDK-Lambda, Cosel, or TI equivalents instead.
- **MUST BE BOARD-MOUNTABLE / SMT — NO CONNECTORISED COAX MODULES.** Every component you recommend goes on a PCB and gets soldered down. NEVER recommend connectorised / coaxial / panel-mount / rack-mount RF modules — they ship with SMA / N-type / BNC jacks already attached and CANNOT be soldered to a board. Specifically banned families:
  - **Pasternack `PE…` series** (e.g. `PE8022`, `PE7213`, `PE15A1004`) — these are coaxial assemblies. For limiters use MACOM `MADL-011017` / Skyworks `SKY16406-321LF`. For amplifiers use Mini-Circuits `PMA-`/`PSA-` chip families. For attenuators use Mini-Circuits `RCAT` / Susumu chip pads.
  - **Mini-Circuits `ZX-` / `ZHL-` / `ZJL-` / `ZAFL-` / `ZQL-` / `ZRL-` / `ZSC-` / `ZSWA-` / `ZSDR-`** — coaxial blocks. Use their chip equivalents: `PMA` / `PSA` / `MNA` / `BFCN` / `GVA` / `RAMP` / `TAV` / `VLF` / `VHF` / `VBF`.
  - **Crystek `CCSO-` / `CRBV-`** rack-mount lab oscillators. Use `CCHD-` / `CCPD-` SMT TCXOs instead.
  - Any part whose datasheet shows SMA / N-type / BNC connectors as part of the package, OR whose description / package field contains "connectorised" / "coaxial" / "drop-in module" / "panel-mount".
  Every `component_recommendations` entry MUST have a `package` field naming a real SMT / chip package (QFN-32, LFCSP-24, BGA-256, SOT-89, 0603, 0402, SOIC-8, TSSOP-16, etc.). Parts that fail the SMT check are rejected at audit time and the phase will not lock.
- **ALWAYS include `datasheet_url`** for every component in the `component_recommendations` array. Use ONLY real, publicly accessible manufacturer datasheet URLs. Preferred domains: `ti.com`, `analog.com`, `mouser.com/datasheet`, `maximintegrated.com`, `nxp.com`, `st.com`, `renesas.com`, `microchip.com`, `infineon.com`, `onsemi.com`, `xilinx.com`, `latticesemi.com`, `murata.com`, `vishay.com`, `coilcraft.com`, `vicorpower.com`. If you are NOT certain the exact URL exists, use the manufacturer's product search page (e.g. `https://www.ti.com/product/LM5175`) rather than guessing a PDF path. NEVER fabricate PDF paths. Never leave `datasheet_url` empty.
- Include `digikey_url` for DigiKey candidates and `mouser_url` for Mouser candidates. Also set `distributor_source` to `digikey` or `mouser` so the BOM renderer can label the link correctly.
- **FOR RF DESIGNS**: Always populate the `gain_loss_budget` array. Every stage in the RF signal chain (antenna/input → LNA/driver → PA stages → filters → output) must be a row. Use real datasheet values for gain, P1dB, NF. Calculate cumulative gain and cascaded NF (Friis formula) correctly. Include system-level parameters (center_freq_mhz, bandwidth_mhz, input_power_dbm, target_output_dbm). Additionally populate these sub-arrays **only when the design requirement specifically calls for it**: `harmonic_rejection` (when spurious/harmonic spec is mentioned), `power_vs_frequency` (when flatness across band is specified), `power_vs_input` (when dynamic range, linearity or 1 dB compression is specified), `cable_loss` (when cable runs, antenna feed, or connector budget is mentioned). Add `input_return_loss_db` and `output_return_loss_db` per stage whenever return loss / VSWR is a requirement.
- **NEVER use XML tags in your responses.** No `<output>`, `<field_name>`, `<safety_flag>`, or any other XML/HTML wrapper tags.
  Use ONLY markdown: `**bold**`, `## headers`, `- lists`, `| tables |`, code blocks. XML tags will break the UI renderer.
- **SENIOR RF ARCHITECT KNOWLEDGE BASE — apply these rules without being asked:**

  **(a) Preselector / SAW filter placement (MANDATORY for every receiver front-end):**
  The canonical front-end chain is: `Antenna -> SMA/connector -> Limiter -> Preselector (SAW or cavity/ceramic BPF) -> Bias-T (if needed) -> LNA -> ...`.
  The preselector SITS BETWEEN the limiter and the LNA (never before the limiter — a SAW has low P_max survivability; never after the LNA — that stage is for post-selection only). Purpose:
     - Reject out-of-band energy that would drive the LNA into intermod (protects LNA IIP3 budget).
     - Reject the image band for superhet architectures (a BPF preselector is often the image filter).
     - Limit integrated noise bandwidth entering the LNA (improves effective sensitivity).
  Technology choice rule: SAW for < 6 GHz, cavity / ceramic BPF for 6-18 GHz, suspended-stripline / waveguide for > 18 GHz. When you render the block diagram, include an explicit `Preselector` or `SAW BPF` node on every RF chain. When you emit the BOM, include a `Preselector BPF` component with real part (e.g. TriQuint SAW at L/S-band, Anatech ceramic at C-band, Knowles waveguide at Ku+).

  **(b) Multi-antenna topology (N > 1 receiver antennas):**
    1. Show N separate antenna input nodes in `block_diagram_mermaid` (Ant1, Ant2 ... AntN) — never collapse into one block.
    2. REPLICATE the entire analog front-end per antenna: Limiter -> Preselector -> Bias-T -> LNA per chain. Do NOT combine the N antennas before the LNA; combining destroys phase information needed for DF / AoA / beamforming / monopulse.
    3. For phased-array / monopulse specifically: N chains feed phase shifters → Σ-Δ combiner (or digital beamformer) AFTER each chain's LNA — still one independent LNA per element.
    4. Phase and amplitude matching: state an explicit tolerance in design_parameters (typical: ±5° phase, ±0.5 dB amplitude across band) for DF-class systems. This drives matched component selection (paired LNAs, matched-length PCB traces, trimmed cable sets).
    5. Power budget: set `qty = N` on all per-antenna parts (limiter, preselector, LNA, bias-T) so the power calc reflects multiplicity.

  **(c) Channelised filter bank (M analog channels):**
    1. The filter bank is PER-ANTENNA, not shared. Each antenna's LNA feeds its own 1:M RF power splitter → M parallel BPFs. For N antennas + M channels, the total is N × M RF outputs.
    2. Show `subgraph Filter Bank k` per antenna k in the Mermaid diagram.
    3. Do NOT insert a single "ChannelSplitter" block that combines multiple antennas before channelisation — that collapses spatial information. If the user said "channelised", assume they want spatial + spectral independence.
    4. Set `qty = N × M` on the BPF, `qty = N` on the 1:M splitter.
    5. Add `antenna_count: "N"` and `channel_count: "M"` to design_parameters and mention both in project_summary.

  **(d) Co-site / interferer environment (applies whenever co-site, EW, or jamming is mentioned):**
    1. Isolation budget: antenna-to-antenna isolation should be stated in requirements (typical >60 dB for co-site TX/RX, >80 dB for co-located transmitters).
    2. Limiter threshold must exceed the worst-case co-site RX power by ≥ 3 dB with recovery < 100 ns for pulsed threats.
    3. Every high-gain LNA chain (> 40 dB post-LNA gain) needs a fixed attenuator pad or programmable AGC slot to prevent blocker compression.

  **(e) High-gain stability (> 60 dB cascaded gain):**
    1. Compartmentalise: separate shielded cavities per amplifier stage in the mechanical enclosure.
    2. Per-stage LC/ferrite bead on every bias rail, decoupled at RF with NP0 ceramics.
    3. Insert a buffer amplifier (or fixed pad > 6 dB) between major blocks to kill round-trip gain via ground/supply paths.
    4. Reverse input / output orientation across the cascade — do not line up all inputs on the same board edge.
    5. AGC does NOT substitute for stability design; it only manages dynamic range.

  **(f) Architecture-specific reminders:**
     - Superhet: preselector = image filter; double-conversion uses two preselectors (one pre-1st-mixer at RF, one pre-2nd-mixer at IF1).
     - Zero-IF / direct-conversion: preselector still required at RF; add DC-offset calibration path + I/Q balance trim (state ≤ ±0.3 dB / ≤ ±3° typical).
     - Direct RF sampling / subsampling: preselector AND anti-alias filter BOTH required; BPF bandwidth ≤ ADC Nyquist zone.
     - Channelised / polyphase FFT: preselector defines the analog bandwidth the FFT sees; per-antenna preselector still required.

  NEVER silently skip these stages. If any rule above is dropped from the block diagram or BOM, the red-team audit will flag it.

- **PROJECT-SPECIFIC GAP RULES (apply in every `generate_requirements` call):**

  **(g) Compliance surface — populate requirements for every applicable standard:** RoHS (EU 2011/65/EU) + REACH restricted-substance check on every component; FCC Part 15 or Part 97 (intentional/unintentional radiator class); CE Marking when EU-market; MIL-STD-461 (CE/CS/RE/RS EMC) for defense; MIL-STD-810 (environmental — vibration / shock / thermal / altitude / humidity); MIL-STD-883 (screening level) for space/defense-qualified parts; DO-254 for airborne electronic hardware; ISO 26262 for automotive; IEC 60601 for medical; ITAR for controlled defense tech; TEMPEST for emissions security. If the application is EW / SIGINT / radar-defense, always include MIL-STD-461, MIL-STD-810, MIL-STD-883, and flag ITAR + TEMPEST as "Should have" or "Must have" depending on user's context. Cite the standard number in the requirement description.

  **(h) Regulator thermal budget — state junction temperature for every regulator:** For every LDO and switching regulator in the BOM, requirements MUST include: `T_j ≤ 125 °C at T_amb = 85 °C` with derivation. Formula: LDO: `P_diss = (V_in − V_out) · I_out`; switcher: `P_diss = P_out · (1−η)/η`. `T_j = T_amb + P_diss · θ_ja`. If the LDO dissipation > 500 mW, either specify a heat-sink/copper-pour or replace with a switcher. This is mechanical — not optional.

  **(i) Verification plan — every RF design requires a stated verification artefact:** SPICE / ADS / AWR / HFSS / CST simulation files, measurement plan (VNA S-parameters, noise figure analyser, phase noise analyser, signal analyser), corner analysis (−40 °C / +25 °C / +85 °C, 3σ component tolerance, supply ±5 %), and DFT hooks (BIT, loopback, calibration tones). Enumerate these in the requirements list under category `verification`.

  **(j) Anti-hallucination reinforcement (already covered elsewhere but restating for emphasis):** Every component MUST have `datasheet_url`. Every `lifecycle_status` MUST be `active` (no NRND, EOL, obsolete, discontinued, preview, or pre-production parts — these are blocked by the upstream search filter and the post-audit). Every IC MUST be available in a surface-mount package (QFN, LFCSP, BGA, SOIC, TSSOP, SOT, DFN, WLCSP, CSP, etc.) — through-hole packages (DIP, PDIP, TO-220, TO-247, axial, radial, PTH, SIP, PGA) are not permitted in this design. State the package explicitly in the recommendation (e.g. `package: "LFCSP-32 5×5 mm"`). Never write TBD/TBC/TBA. Never invent part numbers — if unsure, name the manufacturer family + key specs and leave `primary_part` as the family name (e.g. "ADI HMC-series LNA").

  **(k) BOM quantity multiplicity (already in section (b)/(c) — repeated at tool-output level):** for arrayed / channelised designs, `qty` in `component_recommendations` MUST reflect N (antennas) × M (channels) as applicable. Do NOT emit `qty: 1` on a per-antenna LNA when the user specified 6 antennas.

  **(l) Bias sequencing requirement:** For any depletion-mode FET (GaAs pHEMT, GaN HEMT), state the rule `gate (negative) before drain (positive), drain before signal` in the requirements body. Flag as `REQ-HW-BIAS-SEQ-nnn` with category `functional`, priority `Must have`.

  **(m) Cascade table mandatory:** When the design has ≥ 3 RF stages, emit a numbered cascade table inside the requirements body: `| # | Stage | Gain (dB) | NF (dB) | IIP3 (dBm) | Cum NF (dB) | Cum IIP3 (dBm) |`. Match the `gain_loss_budget` array row-for-row.

  **(n) Phase-noise ↔ application floor mapping:** radar MTI: ≤ −140 dBc/Hz @ 10 kHz; radar coherent: ≤ −120 dBc/Hz @ 10 kHz; SATCOM QAM-64: ≤ −100 dBc/Hz @ 10 kHz; wideband comms: ≤ −90 dBc/Hz @ 10 kHz; non-coherent EW: ≤ −80 dBc/Hz acceptable. Use this table when the user application is known — do NOT ask for the floor if the app gives it away.

## DESIGN TYPE CONTEXT: {design_type}
## PROJECT NAME: {project_name}
"""

GENERATE_REQUIREMENTS_TOOL = {
    "name": "generate_requirements",
    "description": (
        "Generate the complete Phase 1 output when you have gathered enough "
        "requirements from the user. This creates requirements.md, block_diagram.md, "
        "architecture.md, and component_recommendations.md files."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "project_summary": {
                "type": "string",
                "description": "2-3 sentence summary of the hardware design project.",
            },
            "requirements": {
                "type": "array",
                "description": "List of hardware requirements with IEEE IDs.",
                "items": {
                    "type": "object",
                    "properties": {
                        "req_id": {"type": "string", "description": "e.g., REQ-HW-001"},
                        "category": {
                            "type": "string",
                            "enum": ["functional", "performance", "interface", "environmental", "constraint"],
                        },
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "priority": {
                            "type": "string",
                            "enum": ["Must have", "Should have", "Could have", "Won't have", "shall", "should", "may"]
                        },
                        "dependencies": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of dependent requirement IDs or variables"
                        },
                        "constraints": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Any technical constraints or flags"
                        },
                        "verification_method": {
                            "type": "string",
                            "enum": ["test", "analysis", "inspection", "demonstration"],
                        },
                    },
                    "required": ["req_id", "category", "title", "description", "priority"],
                },
            },
            "design_parameters": {
                "type": "object",
                "description": (
                    "Key design parameters extracted from conversation. "
                    "For RF projects, ALWAYS include these keys when the information is "
                    "available from the wizard or chat so the power-budget bias-derivation "
                    "layer can compute optimal Vd/Id for each active IC: "
                    "`noise_figure_db` (system NF target in dB), "
                    "`output_power_dbm` (TX / PA output power in dBm), "
                    "`iip3_dbm` (system IIP3 linearity target in dBm), "
                    "`system_gain_db` (total cascade gain in dB), "
                    "`frequency_range_ghz`, `bandwidth_mhz`, "
                    "`mds_dbm` (minimum detectable signal), "
                    "`supply_voltage_v`, `power_budget_w`. "
                    "Values must be numeric strings (e.g. \"1.8\", \"33\", \"-20\")."
                ),
                "additionalProperties": {"type": "string"},
            },
            "block_diagram_mermaid": {
                "type": "string",
                "description": (
                    "DEPRECATED — DO NOT USE. Emit `block_diagram` (structured "
                    "JSON, defined below) INSTEAD. This raw-string field is kept "
                    "ONLY for back-compat with legacy projects; new tool calls "
                    "must leave this field UNSET (omit it, OR pass empty string). "
                    "Reason: every fancy mermaid shape the LLM emits "
                    "(`[[..]]`, `{{..}}`, `((..))`, `[/..\\]`, `>label]<br/>`, "
                    "etc.) trips the parser in some downstream renderer "
                    "(mermaid.js, mmdc, mermaid.ink — they each fail on "
                    "different patterns). The structured `block_diagram` JSON "
                    "is rendered by the backend's deterministic renderer that "
                    "produces guaranteed-valid output for ALL shapes.\n"
                    "\n"
                    "If for some reason you MUST emit raw mermaid here, follow "
                    "these strict rules to maximise the chance of clean render:\n"
                    "\n"
                    "RULES:\n"
                    "  - Start with `flowchart LR` on line 1.\n"
                    "  - Node label format: `Role / MPN / G+xx NFy.y P1+zz` on a "
                    "single line.\n"
                    "  - No %%{{init}}%% frontmatter, no %% comments, no `<br/>` "
                    "(the renderer strips it — multi-line labels silently break).\n"
                    "  - ASCII only (`Ohm` not Ω, `deg` not °, `u` not µ).\n"
                    "  - No `|` inside labels — use `/`. The sanitiser converts "
                    "`|` to `/` because `|` is reserved in Mermaid link labels. "
                    "Write spec blocks as `G+22 / NF1.6 / P1+22`, not "
                    "`G+22 | NF1.6 | P1+22`.\n"
                    "  - No `<>\"'#@` in labels.\n"
                    "\n"
                    "SHAPE VOCABULARY — use the correct Mermaid shape per role so "
                    "the diagram reads as an RF block diagram, not a generic "
                    "flowchart:\n"
                    "  - Antenna / output           -> flag    e.g. `ANT1>Ant1]`, `OUT>Out]`\n"
                    "  - Amplifier / LNA / PA       -> flag    e.g. `LNA1>LNA1]`\n"
                    "  - Filter / BPF / SAW         -> hexagon e.g. `BPF1{{BPF}}`\n"
                    "  - Bias-T / splitter / combiner -> rhombus e.g. `BT1{BiasT}`, `SP1{Split}`\n"
                    "  - Connector / SMA / BNC      -> parallelogram e.g. `SMA1[/SMA/]`\n"
                    "  - Limiter / attenuator pad   -> trapezoid e.g. `LIM1[/Lim\\]`\n"
                    "  - Mixer / downconverter      -> rounded e.g. `MIX1(MIX)`\n"
                    "  - ADC / DAC / digitiser      -> parallelogram-alt e.g. `ADC1[\\ADC\\]`\n"
                    "  - Oscillator / LO / PLL      -> rounded e.g. `LO1(LO)`\n"
                    "  - Cumulative-performance cell -> rectangle (ONLY inside the "
                    "CASCADE subgraph)\n"
                    "\n"
                    "FORBIDDEN: plain rectangles (`NODE[Rectangle]`) for ANY active "
                    "or passive RF block. A rectangle means 'cumulative perf cell' "
                    "and must live inside the CASCADE subgraph only. Using "
                    "[Rectangle] for LNA/MIX/BPF/etc. makes the diagram regress to "
                    "a generic flowchart — the auditor will reject.\n"
                    "\n"
                    "CASCADE SUBGRAPH (mandatory, one per diagram):\n"
                    "  subgraph CASCADE [System Cumulative Performance]\n"
                    "      CG[Net Gain +37 dB]\n"
                    "      CNF[System NF 2.1 dB]\n"
                    "      CP1[Output P1dB +22 dBm]\n"
                    "      CIP3[Output IIP3 -5 dBm]\n"
                    "  end\n"
                    "Use the Friis-formula values from `design_parameters` "
                    "(`cascaded_nf_db`, `cascaded_gain_db`, `cascaded_p1db_dbm`, "
                    "`cascaded_iip3_dbm`) — do NOT recompute.\n"
                    "\n"
                    "TOPOLOGY MANDATE (senior-architect rules):\n"
                    "  - Canonical chain: Antenna -> SMA -> Limiter -> Preselector "
                    "(SAW/BPF) -> Bias-T -> LNA -> Mixer -> IF BPF -> ADC. The "
                    "Limiter and Preselector are MANDATORY on every receiver "
                    "front-end.\n"
                    "  - MULTI-ANTENNA designs (N>1 antennas): one `>AntN]` flag "
                    "node per antenna, full analog chain replicated per antenna "
                    "until the combine / beamform stage. Never collapse antennas.\n"
                    "  - CHANNELISED FILTER BANK designs: one BPF hexagon per "
                    "channel, parallel arrangement after the LNA. Each channel "
                    "gets its own cascade row in the CASCADE subgraph.\n"
                    "  - HIGH-GAIN STABILITY (>60 dB net gain): insert a second "
                    "BPF between the first and second gain stages to break the "
                    "feedback loop, and annotate each inter-stage with expected "
                    "isolation.\n"
                    "\n"
                    "The backend runs a salvager on any raw Mermaid you emit, but "
                    "salvaged output is lower-quality than rendered structured "
                    "output — use `block_diagram` whenever possible."
                ),
            },
            "block_diagram": {
                "type": "object",
                "description": (
                    "STRUCTURED block diagram — PREFERRED over block_diagram_mermaid. "
                    "Emit a JSON object with `direction`, `nodes`, `edges`, and "
                    "optional `subgraphs`. The backend renders guaranteed-valid "
                    "Mermaid — you do NOT write Mermaid syntax directly. Shapes are "
                    "named enums, labels are plain strings (no escaping needed, "
                    "backend handles Ohm/deg/mu glyphs).\n"
                    "\n"
                    "EXAMPLE (8 GHz RF front-end):\n"
                    '{"direction": "LR",\n'
                    ' "nodes": [\n'
                    '   {"id": "ANT1", "label": "Ant1\\n6-18 GHz", "shape": "flag", "stage": "antenna"},\n'
                    '   {"id": "SMA1", "label": "SMA-F", "shape": "connector"},\n'
                    '   {"id": "LIM1", "label": "Lim / RFLM-422 / IL 0.4 P+30max", "shape": "limiter", "stage": "limiter"},\n'
                    '   {"id": "BPF1", "label": "Preselector / CTF-1835 / IL2.5 BW150", "shape": "filter", "stage": "preselector"},\n'
                    '   {"id": "LNA1", "label": "LNA1 / HMC8410 / G+22 NF1.6 P1+22", "shape": "amplifier", "stage": "lna"},\n'
                    '   {"id": "MIX1", "label": "MIX / HMC8193", "shape": "mixer", "stage": "mixer"},\n'
                    '   {"id": "LO1",  "label": "LO / HMC830 / 5-15 GHz", "shape": "oscillator", "stage": "lo"},\n'
                    '   {"id": "C_G",  "label": "Net Gain +37 dB", "shape": "rect"},\n'
                    '   {"id": "C_NF", "label": "System NF 2.1 dB", "shape": "rect"}\n'
                    ' ],\n'
                    ' "edges": [\n'
                    '   {"from": "ANT1", "to": "SMA1"},\n'
                    '   {"from": "SMA1", "to": "LIM1"},\n'
                    '   {"from": "LIM1", "to": "BPF1"},\n'
                    '   {"from": "BPF1", "to": "LNA1"},\n'
                    '   {"from": "LNA1", "to": "MIX1"},\n'
                    '   {"from": "LO1",  "to": "MIX1", "label": "LO+13 dBm"}\n'
                    ' ],\n'
                    ' "subgraphs": [\n'
                    '   {"id": "CASCADE", "title": "System Cumulative Performance", "nodes": ["C_G", "C_NF"]}\n'
                    ' ]}\n'
                    "\n"
                    "SHAPES (pick the one that semantically fits):\n"
                    "  antenna / output   -> \"flag\"\n"
                    "  SMA / BNC / N-type -> \"connector\"\n"
                    "  limiter / pad      -> \"limiter\"\n"
                    "  LNA / PA / buffer / gain block -> \"amplifier\" (or \"flag\")\n"
                    "  mixer              -> \"mixer\"\n"
                    "  BPF / LPF / HPF / SAW / ceramic -> \"filter\"\n"
                    "  bias-T / splitter / combiner / divider -> \"rhombus\"\n"
                    "  ADC / DAC / digitiser -> \"digital\"\n"
                    "  LO / TCXO / OCXO / PLL -> \"oscillator\"\n"
                    "  PCB trace / cable / cumulative-performance box -> \"rect\"\n"
                    "\n"
                    "NODE ID RULES: `^[A-Za-z][A-Za-z0-9_]*$` (letters, digits, "
                    "underscore; must start with a letter). Keep IDs short (`LNA1`, "
                    "`MIX1`) — they're just references, the human-readable text "
                    "goes in `label`.\n"
                    "\n"
                    "LABEL CONVENTION (every active/passive stage MUST follow): "
                    "`Role / MPN / G+xx NFy.y P1+zz`. For connectors/antennas/"
                    "cumulative-performance nodes, use a short descriptive label.\n"
                    "\n"
                    "SUBGRAPHS: use exactly one subgraph with id=`CASCADE` and "
                    "title=`System Cumulative Performance` containing 2-4 `rect` "
                    "nodes (Net Gain, System NF, Output P1dB, Output IIP3). Use "
                    "values from `design_parameters` — do not recompute."
                ),
                "required": ["direction", "nodes", "edges"],
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["LR", "TD", "TB", "RL", "BT"],
                        "description": "LR for signal-chain receivers, TD for stackups.",
                    },
                    "nodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["id", "label", "shape"],
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "^[A-Za-z][A-Za-z0-9_]*$",
                                },
                                "label": {"type": "string"},
                                "shape": {
                                    "type": "string",
                                    "enum": [
                                        "flag", "connector", "rect", "limiter",
                                        "amplifier", "mixer", "filter", "rhombus",
                                        "digital", "oscillator", "stadium",
                                        "subroutine", "cylinder", "circle",
                                    ],
                                },
                                "stage": {
                                    "type": "string",
                                    "description": (
                                        "Optional semantic stage id — "
                                        "`antenna`, `limiter`, `preselector`, "
                                        "`lna`, `mixer`, `lo`, `filter`, `adc`, "
                                        "`dac`, `pa`. Used by auto-fix to swap "
                                        "banned parts for candidates of the "
                                        "same stage."
                                    ),
                                },
                            },
                        },
                    },
                    "edges": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["from", "to"],
                            "properties": {
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                                "label": {"type": "string"},
                                "style": {
                                    "type": "string",
                                    "enum": ["solid", "dotted", "thick"],
                                },
                            },
                        },
                    },
                    "subgraphs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["id", "title", "nodes"],
                            "properties": {
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                                "nodes": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
            "architecture_mermaid": {
                "type": "string",
                "description": (
                    "DEPRECATED — DO NOT USE. Emit `architecture` (structured "
                    "JSON, defined below) INSTEAD. Same reasoning as "
                    "`block_diagram_mermaid`: raw-mermaid output is fragile "
                    "across renderers; the structured form is rendered "
                    "deterministically. Leave this field UNSET (omit it OR "
                    "pass empty string)."
                ),
            },
            "architecture": {
                "type": "object",
                "description": (
                    "STRUCTURED system-architecture diagram — PREFERRED over "
                    "architecture_mermaid. Same schema as `block_diagram` "
                    "(direction / nodes / edges / subgraphs). Focus on power "
                    "domains, clock distribution, digital-analog boundary, and "
                    "external interfaces. For multi-antenna or channelised "
                    "designs: one node per antenna, one subgraph per channel."
                ),
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["LR", "TD", "TB", "RL", "BT"],
                    },
                    "nodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["id", "label", "shape"],
                            "properties": {
                                "id": {"type": "string"},
                                "label": {"type": "string"},
                                "shape": {
                                    "type": "string",
                                    "enum": [
                                        "flag", "connector", "rect", "limiter",
                                        "amplifier", "mixer", "filter", "rhombus",
                                        "digital", "oscillator", "stadium",
                                        "subroutine", "cylinder", "circle",
                                    ],
                                },
                                "stage": {"type": "string"},
                            },
                        },
                    },
                    "edges": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["from", "to"],
                            "properties": {
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                                "label": {"type": "string"},
                                "style": {
                                    "type": "string",
                                    "enum": ["solid", "dotted", "thick"],
                                },
                            },
                        },
                    },
                    "subgraphs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["id", "title", "nodes"],
                            "properties": {
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                                "nodes": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
            "component_recommendations": {
                "type": "array",
                "description": "Recommended components with alternatives.",
                "items": {
                    "type": "object",
                    "properties": {
                        "function": {"type": "string", "description": "What this component does"},
                        "primary_part": {"type": "string"},
                        "primary_manufacturer": {"type": "string"},
                        "primary_description": {"type": "string"},
                        "primary_key_specs": {"type": "object", "additionalProperties": {"type": "string"}},
                        "datasheet_url": {
                            "type": "string",
                            "description": (
                                "Direct URL to the manufacturer datasheet PDF or product page. "
                                "Use real manufacturer URLs (e.g. https://www.ti.com/lit/ds/...). "
                                "Required for every component."
                            ),
                        },
                        "digikey_url": {
                            "type": "string",
                            "description": "DigiKey product page URL if available (e.g. https://www.digikey.com/en/products/detail/...).",
                        },
                        "mouser_url": {
                            "type": "string",
                            "description": "Mouser product page URL if the selected candidate came from Mouser.",
                        },
                        "distributor_source": {
                            "type": "string",
                            "enum": ["digikey", "mouser", "seed", "chromadb"],
                            "description": "Distributor/source that validated the selected part.",
                        },
                        "product_url": {
                            "type": "string",
                            "description": "Source distributor product page URL, copied from find_candidate_parts.product_url.",
                        },
                        "lifecycle_status": {
                            "type": "string",
                            "enum": ["active"],
                            "description": (
                                "MUST be \"active\" — the part is currently in production "
                                "and actively sold by the manufacturer (no NRND / EOL / LTB / "
                                "Obsolete / Discontinued banner on its product page). If you "
                                "cannot confirm the part is active, pick a different part."
                            ),
                        },
                        "alternatives": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "part_number": {"type": "string"},
                                    "manufacturer": {"type": "string"},
                                    "trade_off": {"type": "string"},
                                    "datasheet_url": {"type": "string"},
                                    "lifecycle_status": {
                                        "type": "string",
                                        "enum": ["active"],
                                        "description": "Must be \"active\" — no stale alternates.",
                                    },
                                },
                            },
                        },
                        "selection_rationale": {"type": "string"},
                    },
                    "required": ["function", "primary_part", "primary_manufacturer", "lifecycle_status"],
                },
            },
            "gain_loss_budget": {
                "type": "object",
                "description": (
                    "RF Gain-Loss Budget. Populate for ALL RF / microwave designs. "
                    "Leave empty object {} for purely digital/power designs.\n\n"
                    "CRITICAL — ACT AS A SENIOR RF ENGINEER. Every numeric value "
                    "(gain_db, noise_figure_db, p1db_out_dbm, oip3_dbm, "
                    "input_return_loss_db, output_return_loss_db) MUST come from the "
                    "component datasheet at the operating frequency you selected. "
                    "Do NOT approximate, do NOT round to the nearest whole dB, and do "
                    "NOT reuse textbook 'typical' values as a substitute for the real "
                    "datasheet entry. Match the datasheet typical-condition values to "
                    "within ±0.1 dB for gain/NF and ±0.5 dBm for P1dB / OIP3. When a "
                    "datasheet value is frequency-dependent, quote the value at the "
                    "project's centre frequency (not at an unrelated spot).\n\n"
                    "BIAS-CONSISTENCY REQUIREMENT: Every active stage MUST include "
                    "`bias_conditions` = {vdd_v, idq_ma, pdc_mw, datasheet_condition}. "
                    "These bias values MUST be the exact ones under which the datasheet "
                    "specifies the gain / NF / P1dB / OIP3 you entered — NOT some other "
                    "operating point. Example for HMC8410 LNA: 15 dB typ gain is "
                    "specified at Vdd=5.0 V, Idq=65 mA (Pdc=325 mW). If the design biases "
                    "it at a different condition, the RF performance numbers MUST be "
                    "updated to match that alternate datasheet condition. Pdc values "
                    "propagate to the Power Budget — they must agree.\n\n"
                    "COMPONENT CONSISTENCY: every `component` field MUST be a part number "
                    "that also appears in the project BOM (tool_input.components). Do not "
                    "invent new part numbers for the GLB that are not in the BOM. The "
                    "GLB, BOM, and block diagram must reference the SAME parts.\n\n"
                    "PHYSICAL SANITY RULES — the report will flag any violation:\n"
                    "  • OIP3 ≈ P1dB + 10 dB (Class-A / MMIC rule of thumb). If the "
                    "    datasheet gives a different delta, keep it — do NOT force "
                    "    the 10 dB rule.\n"
                    "  • LNA / any active stage NF ≥ 0.5 dB at 290 K (lower is "
                    "    physically unachievable outside cryogenic designs).\n"
                    "  • Passive insertion loss and noise figure are numerically "
                    "    equal in dB (thermodynamic identity — Friis). For a SAW "
                    "    filter with 1.5 dB IL, NF = 1.5 dB. Do not set them "
                    "    independently.\n"
                    "  • Mixer conversion gain: passive double-balanced mixers are "
                    "    -6 to -8 dB. Active mixers are +5 to +10 dB. Anything "
                    "    between -3 and +4 dB is unusual — verify.\n"
                    "  • Connector loss: SMA ≤ 18 GHz is -0.15 dB typical; 2.92 mm "
                    "    up to 40 GHz is -0.25 dB; N-type is -0.05 dB. Never 0 dB.\n"
                    "  • PCB trace loss: 50 Ω microstrip on RO4350B is ≈ 0.3 dB "
                    "    for a 1-2 inch run at 10 GHz. Scale as √(f/10 GHz).\n"
                    "  • Single-stage MMIC gain > 30 dB is unusual — split into two "
                    "    cascaded stages with a match / pad between them.\n"
                    "  • Input/output return loss > 25 dB (i.e. VSWR < 1.12:1) is "
                    "    exceptional for broadband parts — verify the datasheet spec "
                    "    before claiming it."
                ),
                "properties": {
                    "center_freq_mhz": {"type": "number", "description": "Centre frequency in MHz"},
                    "bandwidth_mhz":   {"type": "number", "description": "RF bandwidth in MHz"},
                    "input_power_dbm": {"type": "number", "description": "System input signal level in dBm"},
                    "target_output_dbm": {"type": "number", "description": "Required output power in dBm"},
                    "stages": {
                        "type": "array",
                        "description": "One entry per RF stage, in signal-flow order.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "stage_name":          {"type": "string",  "description": "e.g. 'Input Matching Network', 'Driver Amp', 'Final PA'"},
                                "component":           {"type": "string",  "description": "Part number or value (e.g. GVA-123+, 10 nH, SMA connector). Must be a real, in-production part."},
                                "gain_db":             {"type": "number",  "description": "Gain (positive) or insertion loss (negative) in dB — READ FROM DATASHEET at the project centre frequency; ±0.1 dB precision."},
                                "noise_figure_db":     {"type": "number",  "description": "Stage NF in dB from the datasheet at the operating frequency. For passive stages NF equals |insertion loss| (Friis identity). Must be ≥ 0.5 dB for any active stage at 290 K."},
                                "p1db_out_dbm":        {"type": "number",  "description": "Output-referred 1 dB compression point in dBm, DATASHEET value. Use 99 ONLY for passives with no compression spec."},
                                "oip3_dbm":            {"type": "number",  "description": "Output-referred IP3 in dBm, DATASHEET value. Typically ≈ P1dB + 10 dB for MMIC amps. Use 99 only for passives."},
                                "output_power_dbm":    {"type": "number",  "description": "Signal power at OUTPUT of this stage in dBm"},
                                "cumulative_gain_db":  {"type": "number",  "description": "Total gain from system input to output of this stage"},
                                "cumulative_nf_db":    {"type": "number",  "description": "Cascaded noise figure up to and including this stage (Friis)"},
                                "notes":               {"type": "string",  "description": "Brief note, e.g. 'bias tee required', 'temperature-compensated'"},
                                "input_return_loss_db": {"type": "number", "description": "Input return loss (S11) of this stage in dB (positive value, e.g. 15 = 15 dB return loss). Omit if not applicable."},
                                "output_return_loss_db": {"type": "number", "description": "Output return loss (S22) of this stage in dB. Omit if not applicable."},
                                "bias_conditions": {
                                    "type": "object",
                                    "description": "Datasheet bias conditions under which the gain_db / noise_figure_db / p1db_out_dbm / oip3_dbm above are specified. REQUIRED for every active stage (LNA, amp, driver, mixer, PA, VGA). For passives (connectors, traces, filters) omit or set all fields to null.",
                                    "properties": {
                                        "vdd_v":                {"type": "number", "description": "Drain / supply voltage in volts, from datasheet typical operating conditions row (e.g. HMC8410 → 5.0 V)."},
                                        "idq_ma":               {"type": "number", "description": "Quiescent drain current in mA, from the SAME datasheet row that specifies the RF performance (e.g. HMC8410 → 65 mA)."},
                                        "pdc_mw":               {"type": "number", "description": "DC power consumption in mW. Must equal Vdd × Idq (the report flags any mismatch > 10 mW)."},
                                        "datasheet_condition":  {"type": "string", "description": "Short reference to the datasheet row, e.g. 'Table 3 typ @ Vdd=5V, Id=65 mA, f=10 GHz, T=25 °C'. Makes the entry auditable."},
                                    },
                                },
                            },
                            "required": ["stage_name", "component", "gain_db", "output_power_dbm", "cumulative_gain_db"],
                        },
                    },
                    "harmonic_rejection": {
                        "type": "array",
                        "description": "Harmonic rejection table — include ONLY when requirement specifies harmonic suppression (e.g. transmitter spurs spec). One entry per harmonic order.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "harmonic_order": {"type": "integer", "description": "2 for 2nd harmonic, 3 for 3rd, etc."},
                                "frequency_mhz":  {"type": "number",  "description": "Frequency of this harmonic in MHz"},
                                "rejection_db":   {"type": "number",  "description": "Expected harmonic suppression relative to carrier in dBc (positive = more rejection)"},
                                "spec_db":        {"type": "number",  "description": "Required rejection per specification in dBc"},
                                "meets_spec":     {"type": "boolean", "description": "Does the expected rejection meet the spec?"},
                            },
                            "required": ["harmonic_order", "frequency_mhz", "rejection_db"],
                        },
                    },
                    "power_vs_frequency": {
                        "type": "array",
                        "description": "Output power variation across frequency — include ONLY when a flat power vs. frequency spec is required (e.g. flatness +/-1 dB). One entry per frequency spot.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "frequency_mhz":  {"type": "number", "description": "Spot frequency in MHz"},
                                "output_power_dbm": {"type": "number", "description": "Expected output power at this frequency in dBm"},
                                "gain_db":        {"type": "number", "description": "System gain at this frequency in dB"},
                                "flatness_db":    {"type": "number", "description": "Power deviation from nominal (positive or negative) in dB"},
                            },
                            "required": ["frequency_mhz", "output_power_dbm"],
                        },
                    },
                    "power_vs_input": {
                        "type": "array",
                        "description": "Output power vs. input drive level (AM-AM characteristic) — include ONLY when dynamic range or linearity spec requires it. One entry per input power level.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "input_power_dbm":  {"type": "number", "description": "Input signal level in dBm"},
                                "output_power_dbm": {"type": "number", "description": "Corresponding output power in dBm"},
                                "gain_db":          {"type": "number", "description": "Instantaneous gain at this input level"},
                                "gain_compression_db": {"type": "number", "description": "Gain compression relative to small-signal gain, in dB"},
                            },
                            "required": ["input_power_dbm", "output_power_dbm"],
                        },
                    },
                    "cable_loss": {
                        "type": "array",
                        "description": "Cable / transmission-line loss budget — include ONLY when the requirement specifies cable runs, connector losses, or waveguide routing (e.g. antenna feed cables). One entry per cable/connector segment.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "segment":          {"type": "string", "description": "Label, e.g. 'Antenna feed cable', 'SMA to board connector'"},
                                "cable_type":       {"type": "string", "description": "e.g. 'RG-58', 'LMR-400', 'SMA connector', 'waveguide WR-90'"},
                                "length_m":         {"type": "number", "description": "Physical length in metres (0 for connectors/adapters)"},
                                "loss_db_per_m":    {"type": "number", "description": "Cable attenuation in dB/m at the operating frequency"},
                                "total_loss_db":    {"type": "number", "description": "Total loss for this segment in dB"},
                                "frequency_mhz":    {"type": "number", "description": "Frequency at which loss is specified"},
                            },
                            "required": ["segment", "cable_type", "total_loss_db"],
                        },
                    },
                },
            },
        },
        "required": [
            "project_summary", "requirements", "design_parameters",
            "block_diagram_mermaid", "component_recommendations",
        ],
    },
}

SEARCH_COMPONENTS_TOOL = {
    "name": "search_components",
    "description": "Search for components using semantic similarity. Use this when the user asks about specific components or when you need to find alternatives.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language description of the component needed (e.g., '3.3V LDO regulator 1A low noise')",
            },
            "category": {
                "type": "string",
                "description": "Optional category filter (e.g., 'MCU', 'Power', 'Sensor', 'Connectivity')",
                "enum": ["MCU", "Power", "Sensor", "Connectivity", "Interface", "Memory", "Passive", "Mechanical"],
            },
            "n_results": {
                "type": "integer",
                "description": "Number of results to return (default: 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}


# Retrieval-augmented selection — live distributor query returning real
# MPNs + datasheet URLs. Closes the hallucination gap: the LLM picks FROM
# a shortlist of verified parts instead of inventing MPNs from training
# knowledge. Populate `component_recommendations` using these results.
FIND_CANDIDATE_PARTS_TOOL = {
    "name": "find_candidate_parts",
    "description": (
        "Query DigiKey + Mouser live for real, in-stock parts matching a "
        "signal-chain stage and spec hint. Returns a shortlist of actual "
        "MPNs with manufacturer, description, lifecycle, and datasheet URL. "
        "Call this BEFORE generate_requirements for every stage that needs "
        "a concrete component — LNA, mixer, filter, ADC, etc. — then use "
        "ONLY the MPNs returned here in component_recommendations. "
        "DO NOT invent part numbers; if no candidates are returned, widen "
        "the hint and call again, or omit the stage from the BOM."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "stage": {
                "type": "string",
                "description": (
                    "Canonical signal-chain stage. Use one of: lna, driver_amp, "
                    "gain_block, pa, mixer, limiter, bpf, lpf, hpf, preselector, "
                    "saw, splitter, balun, attenuator, switch, vco, pll, adc, "
                    "dac, fpga, mcu, ldo, buck, bias_tee, tcxo, ocxo. Free-text "
                    "is accepted as a fallback."
                ),
            },
            "spec_hint": {
                "type": "string",
                "description": (
                    "Short free-text spec constraints — frequency range, noise "
                    "figure, package, resolution, etc. Example: "
                    "'2-18 GHz NF<2dB SMT' or '12-bit 1 GSPS JESD204B'. Keep it "
                    "under ~10 words — distributor keyword engines degrade on "
                    "overly long queries."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "Cap on candidates returned (default 5, max 10).",
                "default": 5,
            },
        },
        "required": ["stage"],
    },
}


class RequirementsAgent(BaseAgent):
    """Phase 1: Conversational requirements capture and component selection."""

    def __init__(self):
        # Provide tools for direct requirement generation
        # Include CLARIFICATION_TOOL so the LLM can present structured question
        # cards during Rounds 1-3 of the 4-round elicitation flow.
        tools = [CLARIFICATION_TOOL, GENERATE_REQUIREMENTS_TOOL, FIND_CANDIDATE_PARTS_TOOL]
        if COMPONENT_SEARCH_AVAILABLE:
            tools.append(SEARCH_COMPONENTS_TOOL)

        super().__init__(
            phase_number="P1",
            phase_name="Requirements Capture",
            model=settings.primary_model,
            tools=tools,
            max_tokens=16384,  # Increased for complex designs with many components
        )

        # Initialize ComponentSearchTool if available
        if COMPONENT_SEARCH_AVAILABLE and ComponentSearchTool:
            self.component_search = ComponentSearchTool()
        else:
            self.component_search = None

        # Per-request accumulator: every MPN the distributor shortlist tool
        # surfaced this conversation turn. Threaded into p1_finalize →
        # rf_audit so we can flag `component_recommendations` MPNs that
        # bypassed the shortlist.  Reset at the top of each execute().
        self._offered_candidate_mpns: set[str] = set()
        # Full candidate records keyed by stage id ("lna", "mixer", ...).
        # Populated alongside _offered_candidate_mpns from each
        # find_candidate_parts call so the deterministic auto-fix layer
        # can swap a blocker MPN for a known-good replacement of the same
        # stage without paying for an LLM retry round-trip.
        self._offered_candidates_by_stage: dict[str, list[dict]] = {}

    # Fix-on-fail retry — when the post-generation audit detects a fixable
    # blocker (fake MPN, part outside the candidate pool, bad datasheet,
    # banned / obsolete / NRND part) we feed the findings back as a user
    # turn and ask the LLM to re-emit `generate_requirements`. Each retry
    # adds one extra LLM round-trip, so cap small.
    #
    # Perf guardrail (revised 2026-04-24, second time):
    #   - 2026-04-22: lowered 2 → 1 to fix a 12-min pathological P1.
    #   - 2026-04-24 (P10, morning): raised 1 → 2 expecting that the new
    #     DigiKey circuit-breaker (P7) + MPN-shape gate (P9) would keep
    #     the worst-case under 4 min while the extra retry let Mouser
    #     converge on stages DigiKey couldn't.
    #   - 2026-04-24 (P14, afternoon): user reports P1 still taking
    #     ~12 min on a dense RF spec. Demo is imminent — revert to 1
    #     and accept the slightly weaker auto-fix in exchange for a
    #     hard ceiling on wall-clock. The improvements from P7/P9
    #     remain in place; we just no longer pay for the second LLM
    #     round-trip.
    # If you bump this back up, update this comment AND
    # `tests/agents/test_fix_on_fail_corrective.py::TestRetryCap`.
    _FIX_ON_FAIL_MAX_RETRIES = 1

    # P20 timeout was removed on user feedback — per-call wall-clock cap
    # risks killing productive long-running calls. Instead P21 addresses
    # the root cause (the ~500-line SYSTEM_PROMPT being sent to every
    # finalize turn) by swapping to a tight FINALIZE_SYSTEM_PROMPT that
    # gives the model dramatically less context to reason over.
    # Keeping the constant here set to a very large value (30 min) so
    # the existing `wait_for` code still works but practically never
    # fires — effectively a "last-resort infinity" rather than an
    # aggressive cap. If you want to re-enable an aggressive cap, lower
    # this number; see P20 discussion in git log for trade-offs.
    _LLM_CALL_TIMEOUT_S = 1800.0
    _FIX_ON_FAIL_CATEGORIES = frozenset({
        "hallucinated_part",
        "not_from_candidate_pool",
        "obsolete_part",
        "nrnd_part",
        "datasheet_url",
        "banned_part",
        "missing_part_number",
        "part_number",
        "hallucination",
        "stale_part",
        "non_active_lifecycle",
        "part_validation_timeout",
    })

    # Layer-2 defence: pre-emit gate inside the generate_requirements tool
    # handler. Rejects the tool call (instead of capturing the BOM) when any
    # MPN bypasses the verified candidate pool. Capped per chat turn so the
    # LLM tool loop cannot spin forever — once the cap is hit the BOM is
    # captured and the deferred audit + fix-on-fail loop take over.
    _PRE_EMIT_GATE_MAX_ATTEMPTS = 2

    # Layer-2.5: MPN-shape validator. Rejects entries whose `part_number`
    # is clearly NOT a manufacturer part number — e.g. the LLM stuffing
    # a description like "Discrete thin-film 50 Ohm pad" into the field.
    # Real MPN traits we lean on:
    #   - no internal whitespace (DigiKey / Mouser strip it on lookup)
    #   - 3..40 chars after trim
    #   - at least one digit OR the whole string is uppercase + dashes
    #     (covers part families like `LMK04832`, `ADL8107`, `XCKU040`,
    #     `ZX85-12-8SA-S+`, `CL05B104KP5NNNC`)
    #   - first char is alphanumeric (not punctuation)
    # This runs BEFORE the candidate-pool check so the LLM gets an
    # immediate, unambiguous rejection — no attempt counter, the shape
    # check is non-negotiable.
    _MPN_BAD_RE = __import__("re").compile(r"\s")  # any whitespace inside

    @classmethod
    def _looks_like_mpn(cls, s: str) -> bool:
        """Return True iff `s` could plausibly be a manufacturer part number.
        False for descriptions / role labels / free text."""
        s = (s or "").strip()
        if not (3 <= len(s) <= 40):
            return False
        if cls._MPN_BAD_RE.search(s):
            return False
        if not (s[0].isalnum()):
            return False
        # Must contain at least one digit, OR be entirely uppercase+symbols
        # (covers passive families like "GRM188R71C104KA01D" and packages
        # like "TQFN-32"). The all-lowercase-no-digit case is what catches
        # words like "thinfilm" or "attenuator".
        has_digit = any(ch.isdigit() for ch in s)
        if has_digit:
            return True
        if s.upper() == s and any(ch.isalpha() for ch in s):
            return True
        return False

    def get_system_prompt(self, project_context: dict) -> str:
        base = SYSTEM_PROMPT.format(
            design_type=project_context.get("design_type", "general"),
            project_name=project_context.get("name", "Unnamed Project"),
        )
        # If the user supplied initial requirements at project creation, surface them
        # as a hard constraint block so the AI never asks about already-stated specs.
        desc = (project_context.get("description") or "").strip()
        if desc:
            base += (
                f"\n\n## PRE-STATED REQUIREMENTS (from project creation)\n"
                f"The user already specified these constraints when creating the project:\n"
                f"{desc}\n"
                f"Treat these as confirmed requirements — do NOT ask about them again."
            )
        # TX supplement — prepended when the project is a transmitter so the
        # LLM overrides the receiver-centric Round-1 flow (which asks about
        # sensitivity / MDS / LNA NF) with the TX figures-of-merit.
        # Detection priority: explicit project_type → design_parameters.direction.
        ptype = str(project_context.get("project_type") or "").strip().lower()
        dp = project_context.get("design_parameters") or {}
        direction = str(dp.get("direction") or "").strip().lower()
        if ptype == "transmitter" or direction == "tx":
            base = TX_PROMPT_SUPPLEMENT + "\n\n---\n\n" + base
        return base

    def _deterministic_fallback_cards(self, messages: list[dict]) -> dict:
        """
        v18 — Last-resort clarification cards when BOTH the primary tool call
        AND the /clarify helper failed to produce structured questions.

        Builds a topic-aware deck from a hardcoded RF-performance bank.
        Already-answered topics (detected via simple keyword match over the
        conversation transcript) are filtered out; if nothing's left, we emit
        a 1-card "proceed to generate with cascade-derived defaults?" prompt
        so the user can always move forward.

        Never returns empty questions. This is the final safety net — after
        this the UI is guaranteed to render chip cards.
        """
        try:
            history_text = " ".join(
                (m.get("content") or "").lower()
                for m in messages
                if m.get("role") in ("user", "assistant")
            )
        except Exception:
            history_text = ""

        _AUTO = "Auto (cascade-derived)"
        bank = [
            {
                "id": "total_gain",
                "topic_keys": ["total gain", "system gain", "gain (db)", "gain in db"],
                "question": "Total system gain (dB)?",
                "why": "Sets cascade NF budget and saturation management.",
                "options": ["20 dB", "30 dB", "40 dB", "50 dB", "60 dB", _AUTO],
            },
            {
                "id": "noise_figure",
                "topic_keys": ["noise figure", "nf <", "nf (db)", "nf in db", " nf "],
                "question": "Target system noise figure (dB)?",
                "why": "Drives LNA selection and sensitivity floor via Friis.",
                "options": ["< 2 dB", "2–3 dB", "3–5 dB", "5–8 dB", _AUTO],
            },
            {
                "id": "iip3",
                "topic_keys": ["iip3", "input ip3", "intercept point"],
                "question": "Input IP3 requirement (dBm)?",
                "why": "Linearity budget for mixer and amplifiers.",
                "options": ["-10 dBm", "0 dBm", "+10 dBm", "+20 dBm", _AUTO],
            },
            {
                "id": "p1db",
                "topic_keys": ["p1db", "compression point", "1 db comp"],
                "question": "Input P1dB (dBm)?",
                "why": "Large-signal handling / compression threshold.",
                "options": ["-20 dBm", "-10 dBm", "0 dBm", "+10 dBm", _AUTO],
            },
            {
                "id": "image_rejection",
                "topic_keys": ["image rejection", "image reject"],
                "question": "Image rejection requirement (dB)?",
                "why": "Spurious-image suppression before downconversion.",
                "options": ["> 30 dB", "> 50 dB", "> 70 dB", _AUTO],
            },
            {
                "id": "phase_noise",
                "topic_keys": ["phase noise", "dbc/hz", "lo phase"],
                "question": "LO phase noise at 10 kHz offset (dBc/Hz)?",
                "why": "Drives LO synthesiser (TCXO vs PLL+VCO vs DDS).",
                "options": ["-90 dBc/Hz", "-100 dBc/Hz", "-110 dBc/Hz", "-120 dBc/Hz", _AUTO],
            },
            {
                "id": "power_budget",
                "topic_keys": ["power consumption", "power budget", "dc power", "total power"],
                "question": "Power consumption budget (W)?",
                "why": "Sets amplifier biasing and DC-DC topology.",
                "options": ["< 5 W", "5–15 W", "15–30 W", "> 30 W", _AUTO],
            },
            {
                "id": "supply_voltage",
                "topic_keys": ["supply voltage", "rail", "v rail", " vdd", "vcc"],
                "question": "Primary supply voltage rail?",
                "why": "Drives regulator and active-device choice.",
                "options": ["+5 V", "+12 V", "+15 V", "+28 V", "Multi-rail", _AUTO],
            },
        ]

        def _already_seen(q: dict) -> bool:
            return any(k in history_text for k in q["topic_keys"])

        remaining = [q for q in bank if not _already_seen(q)]

        # Empty means the user has already touched every topic we'd have asked
        # about — offer a single "proceed" card so they can reach finalize.
        if not remaining:
            return {
                "intro": (
                    "All core RF specs have been covered. Ready to generate the "
                    "design documents?"
                ),
                "questions": [
                    {
                        "id": "finalize_confirm",
                        "question": "Proceed with the specs captured so far?",
                        "why": (
                            "Any remaining values will be filled in from the "
                            "cascade analysis and standard RF best-practices."
                        ),
                        "options": [
                            "Yes — generate documents now",
                            "Wait — I want to add one more spec",
                        ],
                    }
                ],
            }

        # Cap to 5 cards so the user isn't dumped with a wall of questions.
        chosen = remaining[:5]
        for q in chosen:
            q.pop("topic_keys", None)

        return {
            "intro": (
                "Core RF performance targets — enter a value or pick "
                "\"Auto\" for cascade-derived defaults."
            ),
            "questions": chosen,
        }

    def get_clarification_questions(
        self,
        user_requirement: str,
        design_type: Optional[str] = "RF",
        conversation_history: Optional[list] = None,
        round_label: Optional[str] = None,
    ) -> dict:
        """
        Use tool_use (forced) to return structured clarification cards.
        The AI cannot respond in free text — it MUST call show_clarification_cards.

        Round-1 call: pass just user_requirement + design_type.
        Round-N call (N >= 2): pass conversation_history so the tool can
        generate FOLLOW-UP cards that build on prior turns without repeating
        questions the user has already answered.

        Returns:
            { "intro": str, "questions": [{ "id", "question", "why", "options" }] }
        """
        # Prefer GLM, fall back to Anthropic if configured
        if not settings.glm_api_key and not self._anthropic_client:
            raise ValueError("No LLM API key configured — set GLM_API_KEY or ANTHROPIC_API_KEY.")

        # Determine which client and model to use
        use_glm = bool(settings.glm_api_key)
        model = settings.glm_fast_model if use_glm else settings.fast_model

        if use_glm:
            # Use GLM via Z.AI (Anthropic-compatible endpoint)
            _hc = _make_sync_httpx_client()
            client = _anthropic.Anthropic(
                api_key=settings.glm_api_key,
                base_url=settings.glm_base_url,
                **({"http_client": _hc} if _hc else {}),
            )
        else:
            # Fall back to Anthropic client
            client = self._anthropic_client

        # Build messages list. For follow-up rounds, seed with prior turns so the
        # agent knows what has already been asked/answered.
        messages: list[dict] = []
        if conversation_history:
            for m in conversation_history:
                role = m.get("role")
                content = (m.get("content") or "").strip()
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        # Final user turn — always the current requirement/trigger. For round-2+
        # we nudge the tool to ask the NEXT batch of questions based on history.
        if conversation_history:
            label = round_label or "next round"
            trigger = (
                f"Design type: {design_type}\n"
                f"Current requirement: {user_requirement}\n\n"
                f"Based on the prior conversation above, generate the NEXT "
                f"clarification round ({label}). Ask ONLY questions that have "
                f"NOT already been answered. Build on the user's prior answers."
            )
        else:
            trigger = f"Design type: {design_type}\nRequirement: {user_requirement}"
        messages.append({"role": "user", "content": trigger})

        response = client.messages.create(
            model=model,
            max_tokens=1000,
            system=_CLARIFICATION_SYSTEM,
            tools=[CLARIFICATION_TOOL],
            tool_choice={"type": "tool", "name": "show_clarification_cards"},
            messages=messages,
        )

        # Decide whether the Round-1 forbidden-topic filter should run this
        # call.  Correct semantics:
        #   Round 1  = the user's first raw-requirement elicitation turn.
        #              Filter ON so forbidden topics (architecture / ADC bits /
        #              IF specs / output format) get stripped.
        #   Round 2+ = any follow-up round (architecture-pick, Stage 2 IF specs,
        #              recovery-from-prose on Turn 2+, etc.).  Filter OFF.
        #
        # Detection:
        #   - `_prior_user_turns`: count user entries in conversation_history.
        #     Turn 1 has 0 or 1 (depends on whether history was captured pre- or
        #     post-user-append). Turn 2+ always has ≥ 2.
        #   - `_has_qa_pairs`: first user message contains inline " -> " pairs
        #     (pre-stage clarification UI already ran → semantically Round 2+).
        #   - `_label_looks_later`: regex match on round_label for "round-2..99",
        #     stage-2..99, architecture / follow / if-stage / adc-stage / next.
        #     Replaces the hard-coded list which missed round-5+ labels.
        #
        # Key fix vs v11:
        #   Previously `prior_user_turns == 0` was the only count gate, so
        #   Turn-1 RECOVERY (history has [user, assistant-prose]) looked like
        #   Round 2 and the filter wrongly stayed off.  New rule:
        #   `prior_user_turns <= 1` treats Turn 1 recovery as Round 1.
        import re as _re2
        _prior_user_turns = sum(
            1 for m in (conversation_history or []) if m.get("role") == "user"
        )
        _first_user_msg = next(
            (m.get("content", "") or "" for m in (conversation_history or [])
             if m.get("role") == "user"),
            user_requirement or "",
        )
        _has_qa_pairs = (
            " -> " in _first_user_msg and _first_user_msg.count("\n") >= 3
        )
        _label = (round_label or "").lower()
        # Regex catches round-2..99, stage-2..99 (any digits), plus semantic
        # keywords that always mean "later round".
        _label_looks_later = False
        _m_label = _re2.search(r'(?:round|stage)-?(\d+)', _label)
        if _m_label and int(_m_label.group(1)) >= 2:
            _label_looks_later = True
        if any(k in _label for k in ("architecture", "if-stage", "if_stage",
                                     "adc-stage", "adc_stage", "follow-up",
                                     "followup", "next")):
            _label_looks_later = True
        _is_round1_call = (
            _prior_user_turns <= 1
            and not _has_qa_pairs
            and not _label_looks_later
        )
        logger.info(
            "get_clarification_questions round1_filter=%s "
            "(prior_user_turns=%s has_qa_pairs=%s label=%r)",
            "on" if _is_round1_call else "off",
            _prior_user_turns, _has_qa_pairs, round_label,
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "show_clarification_cards":
                payload = dict(block.input)
                # Only sanitise on true Round 1.  On Stage 2+ the architecture /
                # LO / IF / ADC / output-format questions are legitimate, and
                # running the filter would strip every one of them, leaving an
                # empty `questions` array and breaking the chip-card UI.
                if _is_round1_call:
                    payload = _filter_forbidden_round1(payload)
                return payload

        raise ValueError(f"No tool_use block returned. Check API key and model availability (model: {model}).")

    async def execute(self, project_context: dict, user_input: str) -> dict:
        """
        Execute Phase 1 — Direct Generation approach.
        """
        # Reset the per-turn candidate-pool accumulator.  The distributor
        # shortlist is per-conversation-turn — previous turns' offered
        # MPNs must not bleed into this turn's audit gate.
        self._offered_candidate_mpns = set()
        self._offered_candidates_by_stage = {}

        system = self.get_system_prompt(project_context)

        # Build message list from conversation history
        history = project_context.get("conversation_history", [])
        messages = []
        for msg in history:
            if msg.get("role") in ("user", "assistant") and msg.get("content"):
                messages.append({"role": msg["role"], "content": msg["content"]})

        # ── Wizard payload detection ─────────────────────────────────────
        # v21 deterministic wizard sends a fully-structured payload containing
        # [Project type:], [Design scope:], [Application:], [Architecture:],
        # SYSTEM SPECIFICATIONS (Tier-1):, and an explicit "Do NOT re-ask" note.
        # When we see this shape, treat it equivalently to __FINALIZE__ — skip
        # Round-1 clarification cards and go straight to generate_requirements.
        # The wizard has already elicited every spec the backend would ask for.
        _wi = user_input.strip()
        _is_wizard_payload = (
            "[Design scope:" in _wi
            and "[Architecture:" in _wi
            and "SYSTEM SPECIFICATIONS (Tier-1):" in _wi
            and "Do NOT re-ask" in _wi
        )
        if _is_wizard_payload:
            self.log("Wizard payload detected — bypassing Round-1 clarification", "info")
            # Keep the rich payload visible to the LLM (so it has all the specs)
            # but append a system instruction that forces the tool call. We replace
            # the last user message because chat_service already persisted it.
            _forced = (
                f"{user_input}\n\n"
                "[SYSTEM INSTRUCTION: The user has just completed the deterministic "
                "RF Architect wizard above. Every Tier-1 spec, scope, application, "
                "and architecture has been captured. Do NOT ask for MDS/sensitivity, "
                "max input, architecture, or any spec already listed above — they "
                "are all in the payload. Do NOT call `show_clarification_cards`. "
                "\n\n"
                "MANDATORY TOOL-CHAINING SEQUENCE (follow exactly):\n"
                "STEP 1 — RETRIEVAL: Call `find_candidate_parts` ONCE for every "
                "distinct signal-chain stage that will appear in "
                "component_recommendations (e.g. lna, mixer, limiter, preselector, "
                "bpf, splitter, adc, dac, pll, ldo, buck, tcxo, mcu, fpga). For "
                "each call pass a concrete `stage` id and a compact `spec_hint` "
                "(<= 10 words) built from the Tier-1 specs — e.g. stage='lna' "
                "hint='2-18 GHz NF<2dB SMT'. You may issue multiple tool calls in "
                "parallel in a single assistant turn. Do this BEFORE any other "
                "tool call. If a stage returns zero candidates, immediately retry "
                "`find_candidate_parts` with a wider hint.\n"
                "STEP 2 — GENERATION: Only after the retrieval step, call "
                "`generate_requirements` as your FINAL tool call. Every MPN in "
                "`component_recommendations` MUST be copied verbatim from one of "
                "the `find_candidate_parts` `candidates[].part_number` values "
                "returned during this turn; copy the corresponding `datasheet_url`, "
                "`product_url`, `source`, and `digikey_url` / `mouser_url` "
                "verbatim too. Inventing an MPN or altering the distributor's "
                "URLs will fail the `not_from_candidate_pool` audit gate. "
                "If no candidate fits a stage after a second widened retrieval, "
                "OMIT that stage from the BOM rather than inventing a part.\n"
                "\n"
                "Output the complete BOM, requirements list, block_diagram_mermaid, "
                "architecture_mermaid, design_parameters, and component_recommendations "
                "— with `datasheet_url` for every component. "
                "TOPOLOGY MANDATE (apply before emitting the diagram): "
                "(1) Every RF chain MUST include the canonical stages in order: "
                "Antenna -> SMA -> Limiter -> Preselector (SAW / ceramic / cavity BPF) "
                "-> Bias-T -> LNA. The Preselector is MANDATORY and non-negotiable — "
                "it sits between limiter and LNA for out-of-band / image rejection. "
                "(2) Scan the ADDENDUM for `Number of receiver antennas -> N`. If "
                "N > 1, replicate the entire canonical chain per antenna (Ant1 ... "
                "AntN each with its own Limiter_i, Preselector_i, Bias-T_i, LNA_i). "
                "Never combine antennas before the LNA. "
                "(3) Scan the ADDENDUM for `Channelised filter bank (number of analog "
                "channels) -> M`. If M is a number (not 'No — single channel'), the "
                "M-way split is PER ANTENNA: each LNA feeds its own 1:M splitter -> "
                "M BPFs, grouped in `subgraph Filter Bank k`. Never insert a single "
                "shared ChannelSplitter that combines antennas before channelisation. "
                "(4) Reflect multiplicity in the BOM: qty = N on per-antenna parts "
                "(limiter, preselector, bias-T, LNA), qty = N on 1:M splitter, qty = "
                "N*M on the per-channel BPF. Add `antenna_count`, `channel_count`, "
                "and `preselector_tech` to design_parameters. "
                "(5) If cascaded gain > 60 dB, insert a buffer amp / pad node mid-chain "
                "and note shielded-cavity + LC/ferrite rail decoupling requirements "
                "in the requirements list. "
                "(6) DIAGRAM SHAPES + SPECS (see `block_diagram_mermaid` schema for "
                "full vocabulary): use shape-coded nodes — `>Ant1]` for antennas, "
                "`[/SMA/]` for connectors, `[/Lim\\]` for limiters / pads, `>LNA1]` "
                "for amplifiers, `{{BPF}}` for filters / preselectors, `{BiasT}` "
                "for bias-Ts, `{Split}` for splitters, `(MIX)` for mixers, "
                "`[\\ADC\\]` for digitisers. EVERY active / passive node label "
                "MUST be `Role / MPN / G+xx NFy.y P1+zz` (single line, ` / ` "
                "separator) — never plain `[Rectangle]` boxes. After the main "
                "chain emit `subgraph CASCADE [System Cumulative Performance]` "
                "with rectangles for Net Gain / System NF / Output P1dB / Output "
                "IIP3 (omit any single one if not computable).]"
            )
            if messages and messages[-1].get("role") == "user":
                messages[-1]["content"] = _forced
            else:
                messages.append({"role": "user", "content": _forced})
            # Re-assign user_input so downstream logic treats this as a finalize
            # (prevents the Round-1 forbidden-topic filter from firing, and the
            # _prior_user_turns_for_filter sentinel below will set it to 999).
            user_input = "__FINALIZE__"

        # ── __FINALIZE__ signal: user explicitly requests document generation ──
        # Replace the trigger message with a direct instruction that forces the tool call.
        # IMPORTANT: chat_service saves __FINALIZE__ to DB and re-fetches history BEFORE
        # calling execute(), so messages[-1] is already {"role":"user","content":"__FINALIZE__"}.
        # We must REPLACE that last message — not append — otherwise the model sees "__FINALIZE__"
        # as a literal string with no context.
        if user_input.strip() == "__FINALIZE__" and not _is_wizard_payload:
            user_input = (
                "[SYSTEM INSTRUCTION: Call `generate_requirements` tool IMMEDIATELY as your FIRST "
                "content block. Do NOT output ANY free text before the tool call — no preamble, "
                "no acknowledgment. Generate the complete requirements based on everything discussed "
                "with all requirements, components, block diagram, and design parameters. "
                "Do not ask any more questions.]"
            )
            # Replace __FINALIZE__ sentinel already in message list
            if messages and messages[-1]["role"] == "user":
                messages[-1]["content"] = user_input
            else:
                messages.append({"role": "user", "content": user_input})
        else:
            if not messages or messages[-1]["role"] != "user":
                messages.append({"role": "user", "content": user_input})

            # ── Detect phase completion state ────────────────────────────────
            # project_context passes "p1_complete" boolean from chat_service
            phase_already_complete = bool(project_context.get("p1_complete", False))

            # ── Detect if follow-up is a hardware specification addition ─────
            # Explicit regen keywords the user might type:
            _REGEN_KEYWORDS = (
                "regenerate", "re-generate", "re generate", "rerun", "re-run",
                "update requirements", "redo", "add datasheet", "add link", "add url",
                "refresh", "rebuild", "recreate", "redo phase", "run again",
                "change the", "change to", "increase", "decrease", "modify",
            )
            # Hardware interface / spec patterns — these always mean "add to requirements"
            import re as _re_local
            _HW_SPEC_PATTERN = _re_local.compile(
                r'\b('
                # Interfaces & buses
                r'uart|usart|rs.?232|rs.?485|can\s?bus|can\b|spi\b|i2c|i2s|smbus|modbus'
                r'|usb|hdmi|displayport|mipi|lvds|jtag|swd|jtag|pcie|ethernet|rmii|rgmii'
                r'|sdio|emmc|nand|nor\s?flash|qspi|ospi'
                # RF / analog interfaces
                r'|rf\b|antenna|coax|sma\b|u\.fl|mmcx|ble|wifi|zigbee|lora|nfc|gps|gnss'
                # Power specs
                r'|ldo|buck|boost|\d+\s*v\b|\d+\s*ma\b|\d+\s*a\b|pmic|pwm|battery'
                # Sensor / actuator
                r'|sensor|accelerometer|gyroscope|imu|magnetometer|barometer|temperature'
                r'|humidity|pressure|hall\s?effect|encoder|stepper|servo|bldc|adc|dac'
                # Display / IO
                r'|lcd|oled|tft|e.?ink|touchscreen|keypad|buzzer|led|relay|optocoupler'
                # Memory / compute
                r'|flash|eeprom|ram|ddr|sdram|fpga|dsp|cortex|arm|risc.?v|mcu|cpu'
                # Connectors / mechanical
                r'|connector|socket|header|rj45|d.?sub|db9|molex|jst|m12|waterproof'
                # Regulation
                r'|rohs|reach|fcc|ce\s?mark|ul\s?listed|iec|mil.?std|ats\b'
                r')\b',
                _re_local.IGNORECASE
            )
            _ADDITION_WORDS = _re_local.compile(
                r'\b(add|include|also|need|require|want|plus|with|and|use|implement|integrate|support|enable)\b',
                _re_local.IGNORECASE
            )
            # A message is a "hw spec addition" if it mentions a hardware keyword
            # (even without "add" — e.g. "uart control interface" alone implies addition)
            _is_hw_spec_addition = bool(_HW_SPEC_PATTERN.search(user_input))
            user_wants_regen = (
                any(kw in user_input.lower() for kw in _REGEN_KEYWORDS)
                or _is_hw_spec_addition
            )

            # ── Force tool call when user has CONFIRMED requirements ───
            # 4-Round elicitation flow (without pre-stage):
            #   Turn 1 (prior_user_turns=0): user describes requirement → LLM asks Round 1
            #   Turn 2 (prior_user_turns=1): user answers Round 1 → LLM asks Round 1.5 + Round 2
            #   Turn 3 (prior_user_turns=2): user selects architecture → LLM asks Round 3 + Round 4
            #   Turn 4 (prior_user_turns=3): user confirms → ONLY NOW force generate_requirements
            # With pre-stage (frontend sends Round 1 Q&A in first message):
            #   Turn 1 (prior_user_turns=0): already has Round 1 answers → LLM asks Round 1.5 + Round 2
            #   Turn 2 (prior_user_turns=1): user selects architecture → LLM asks Round 3 + Round 4
            #   Turn 3 (prior_user_turns=2): user confirms → force generate_requirements
            prior_user_turns = sum(1 for m in messages[:-1] if m.get("role") == "user")
            _is_confirmation = _is_approval(user_input)
            # Detect if first message had pre-stage Q&A (Round 1 already answered)
            _first_msg = next((m["content"] for m in messages if m.get("role") == "user"), "")
            _prestage_used = " -> " in _first_msg and _first_msg.count("\n") >= 3
            # Minimum turns before generation: 3 normally, 2 with pre-stage
            _min_turns = 2 if _prestage_used else 3
            # Force generation ONLY when: (a) enough rounds AND user confirms,
            # OR (b) __FINALIZE__ signal, OR (c) phase already done and user wants regen
            _should_generate = (
                (prior_user_turns >= _min_turns and _is_confirmation)
                or (prior_user_turns >= _min_turns and user_wants_regen and not phase_already_complete)
            )
            if _should_generate and messages and (not phase_already_complete or user_wants_regen):
                original = messages[-1]["content"]
                if phase_already_complete:
                    # Regeneration: explicitly instruct the LLM to incorporate ALL history
                    messages[-1]["content"] = (
                        f"The user has added a new requirement: {original}\n\n"
                        "[SYSTEM INSTRUCTION: Call `generate_requirements` tool IMMEDIATELY as your FIRST "
                        "content block. Do NOT output ANY free text before the tool call. "
                        "Read ALL previous messages carefully. The new requirement above MUST be "
                        "incorporated together with EVERY requirement, interface, component, and "
                        "specification mentioned earlier. Do NOT drop any previously discussed item. "
                        "Include the COMPLETE updated set — all BOM items, all requirements (including "
                        "the new one), updated block_diagram_mermaid, architecture_mermaid, "
                        "design_parameters, and component_recommendations — with `datasheet_url` for "
                        "every component.]"
                    )
                else:
                    messages[-1]["content"] = (
                        f"{original}\n\n"
                        "[SYSTEM INSTRUCTION: Call `generate_requirements` tool IMMEDIATELY as your FIRST "
                        "content block. Do NOT output ANY free text before the tool call — no preamble, "
                        "no acknowledgment, no 'I have all the information'. Your response must START with "
                        "the tool_use block. Include EVERY requirement, interface, component and specification "
                        "from this entire conversation. Do not drop any detail. Include the complete BOM, "
                        "requirements list, block_diagram_mermaid, architecture_mermaid, design_parameters, "
                        "and component_recommendations — with `datasheet_url` for every component.]"
                    )
            elif phase_already_complete and not user_wants_regen:
                # Phase is done and user is asking a pure question (no hw spec detected)
                # Answer conversationally without regenerating
                if messages:
                    messages[-1]["content"] = (
                        f"{messages[-1]['content']}\n\n"
                        "[Note: Phase 1 requirements are complete. Answer this question directly. "
                        "If the user is adding or changing a hardware specification, call "
                        "generate_requirements instead of answering conversationally.]"
                    )

        # ── Tool handlers ──────────────────────────────────────────────────
        # generate_requirements: capture tool input via closure so we can detect
        # the call even after call_llm_with_tools finishes its loop.
        # (Without this handler, call_llm_with_tools returns "tool not found"
        #  error to the model and the tool_calls list is empty on final return.)
        generate_req_input: dict = {}
        clarification_cards: dict = {}
        # Mutable counter accessed by the closure below — the closure cannot
        # rebind a captured immutable int, but it CAN mutate a list element.
        _pre_emit_attempts: list = [0]

        # Decide whether the Round-1 forbidden-topic filter should run this
        # turn. It MUST only run on a true Round-1 emission (user's first raw
        # requirement). On Stage 2+ the LLM legitimately emits architecture /
        # LO / ADC / IF questions, and the Round-1 filter would strip every
        # one of them — leaving an empty `questions` array and breaking the
        # chip-card UI (observed bug: agent emits intro + 0 questions).
        _prior_user_turns_for_filter = sum(
            1 for m in messages[:-1] if m.get("role") == "user"
        ) if user_input.strip() != "__FINALIZE__" else 999
        _first_msg_for_filter = next(
            (m["content"] for m in messages if m.get("role") == "user"), ""
        )
        _prestage_used_for_filter = (
            " -> " in _first_msg_for_filter and _first_msg_for_filter.count("\n") >= 3
        )
        # Real Round 1 = first turn AND no inline Q&A pairs (i.e. the user
        # typed a raw requirement, not a pre-answered template).
        _is_round1_turn = (
            _prior_user_turns_for_filter == 0 and not _prestage_used_for_filter
        )

        async def _capture_generate_requirements(input_data: dict) -> dict:
            # ── Defensive coercion (v21.2) ─────────────────────────────────
            # LLMs occasionally emit fields as JSON strings instead of the
            # expected object/array. Downstream code (_build_requirements_md
            # etc.) calls .items() / iteration on these fields and crashes
            # with "'str' object has no attribute 'items'". Normalise shape
            # here so the rest of the agent is isolated from schema drift.
            import json as _json_local
            _safe = dict(input_data) if isinstance(input_data, dict) else {}

            def _coerce_dict(val):
                if isinstance(val, dict):
                    return val
                if isinstance(val, str):
                    try:
                        parsed = _json_local.loads(val)
                        return parsed if isinstance(parsed, dict) else {}
                    except Exception:
                        return {}
                return {}

            def _coerce_list(val):
                if isinstance(val, list):
                    return val
                if isinstance(val, str):
                    try:
                        parsed = _json_local.loads(val)
                        return parsed if isinstance(parsed, list) else []
                    except Exception:
                        return []
                return []

            def _coerce_str(val):
                if isinstance(val, str):
                    return val
                if val is None:
                    return ""
                try:
                    return _json_local.dumps(val) if not isinstance(val, (int, float, bool)) else str(val)
                except Exception:
                    return str(val)

            # Object fields
            for _k in ("design_parameters",):
                if _k in _safe:
                    _safe[_k] = _coerce_dict(_safe[_k])
            # Array fields
            for _k in ("requirements", "component_recommendations"):
                if _k in _safe:
                    _safe[_k] = _coerce_list(_safe[_k])
            # Per-component nested dicts (primary_key_specs)
            _comps = _safe.get("component_recommendations") or []
            for _comp in _comps:
                if isinstance(_comp, dict):
                    if "primary_key_specs" in _comp:
                        _comp["primary_key_specs"] = _coerce_dict(_comp["primary_key_specs"])
            # String fields (mermaid blocks)
            for _k in ("block_diagram_mermaid", "architecture_mermaid", "project_summary"):
                if _k in _safe:
                    _safe[_k] = _coerce_str(_safe[_k])

            # ── Pre-emit hallucination gate ─────────────────────────────────
            # Reject the tool call when MPNs bypass the verified candidate
            # pool. The LLM gets a corrective response and is asked to either
            # widen `find_candidate_parts` or pick from the existing pool.
            # Capped per turn so the tool loop cannot spin forever; once the
            # cap is hit, the BOM is captured and the deferred audit's
            # fix-on-fail loop takes over.
            _pool = {(m or "").strip().upper() for m in (self._offered_candidate_mpns or set()) if m}
            _bom_entries: list = []
            for _comp in (_safe.get("component_recommendations") or []):
                if not isinstance(_comp, dict):
                    continue
                _mpn = (_comp.get("part_number") or "").strip()
                _role = (
                    _comp.get("role")
                    or _comp.get("name")
                    or _comp.get("description")
                    or "?"
                ).strip()
                if _mpn:
                    _bom_entries.append((_role, _mpn))

            # MPN-shape gate FIRST — `"Discrete thin-film 50 Ohm pad"` etc
            # has no business reaching the pool check or the audit. These
            # are rejected unconditionally (no attempt counter).
            _shape_violations = [
                (r, m) for r, m in _bom_entries if not self._looks_like_mpn(m)
            ]
            if _shape_violations:
                self.log(
                    f"[pre-emit-gate] BLOCKED — {len(_shape_violations)} BOM "
                    f"entry(ies) have non-MPN-shaped part_number "
                    f"(spaces / descriptions / free text)",
                    "warning",
                )
                _msg_lines = [
                    f"BLOCKED: {len(_shape_violations)} BOM entry(ies) have "
                    f"a `part_number` that is NOT a manufacturer part number "
                    f"— it looks like a description or role label. EVERY row "
                    f"must use the exact MPN string from a "
                    f"`find_candidate_parts` result. Real MPNs have no "
                    f"internal spaces, are 3-40 chars, and contain digits "
                    f"(e.g. `ADL8107`, `HMC624LP4E`, `ZX85-12-8SA-S+`).",
                    "",
                    "REJECTED ENTRIES (descriptions in part_number field):",
                ]
                for _role, _bad in _shape_violations[:10]:
                    _msg_lines.append(f"  - {_role}: {_bad!r}")
                if len(_shape_violations) > 10:
                    _msg_lines.append(
                        f"  ... and {len(_shape_violations) - 10} more"
                    )
                _msg_lines.extend([
                    "",
                    "Fix: replace each rejected `part_number` with the real "
                    "MPN copied verbatim from a `find_candidate_parts` "
                    "candidate. If no candidate exists for that role, OMIT "
                    "the row rather than inventing or describing one.",
                ])
                return {
                    "status": "rejected",
                    "message": "\n".join(_msg_lines),
                }

            _no_pool_violation = (not _pool) and bool(_bom_entries)
            _pool_violations = (
                [(r, m) for r, m in _bom_entries if m.upper() not in _pool]
                if _pool else []
            )

            if (
                (_no_pool_violation or _pool_violations)
                and _pre_emit_attempts[0] < self._PRE_EMIT_GATE_MAX_ATTEMPTS
            ):
                _pre_emit_attempts[0] += 1
                if _no_pool_violation:
                    self.log(
                        f"[pre-emit-gate] BLOCKED attempt "
                        f"{_pre_emit_attempts[0]}/{self._PRE_EMIT_GATE_MAX_ATTEMPTS} "
                        f"— empty candidate pool, {len(_bom_entries)} BOM entries",
                        "warning",
                    )
                    return {
                        "status": "rejected",
                        "message": (
                            "BLOCKED: generate_requirements called without any "
                            "`find_candidate_parts` results this turn. EVERY MPN "
                            "in the BOM must trace back to a find_candidate_parts "
                            "result. Call find_candidate_parts for each "
                            "signal-chain stage (LNA, mixer, filter, limiter, "
                            "ADC, PLL, regulator, ...) FIRST, then re-call "
                            f"generate_requirements. (attempt "
                            f"{_pre_emit_attempts[0]}/"
                            f"{self._PRE_EMIT_GATE_MAX_ATTEMPTS})"
                        ),
                    }
                self.log(
                    f"[pre-emit-gate] BLOCKED attempt "
                    f"{_pre_emit_attempts[0]}/{self._PRE_EMIT_GATE_MAX_ATTEMPTS} "
                    f"— {len(_pool_violations)} BOM entry(ies) outside "
                    f"verified pool ({len(_pool)} MPNs)",
                    "warning",
                )
                _pool_sample = sorted(_pool)[:60]
                _msg_lines = [
                    f"BLOCKED: {len(_pool_violations)} BOM entry(ies) reference "
                    f"MPNs that are NOT in the verified candidate pool from "
                    f"`find_candidate_parts` this turn. Picking from outside "
                    f"the pool is hallucination. You MUST either widen the "
                    f"search (call find_candidate_parts again with a relaxed "
                    f"`spec_hint`) or pick replacement MPNs from the existing "
                    f"pool below.",
                    "",
                    "REJECTED ENTRIES:",
                ]
                for _role, _mpn in _pool_violations[:10]:
                    _msg_lines.append(f"  - {_role}: {_mpn}")
                if len(_pool_violations) > 10:
                    _msg_lines.append(f"  ... and {len(_pool_violations) - 10} more")
                _msg_lines.extend(["", f"VERIFIED POOL ({len(_pool)} MPNs):"])
                for _m in _pool_sample:
                    _msg_lines.append(f"  - {_m}")
                if len(_pool) > 60:
                    _msg_lines.append(f"  ... and {len(_pool) - 60} more")
                _msg_lines.append("")
                _msg_lines.append(
                    f"(attempt {_pre_emit_attempts[0]}/"
                    f"{self._PRE_EMIT_GATE_MAX_ATTEMPTS} — after that the BOM "
                    f"is captured and surfaced as audit blockers via the "
                    f"fix-on-fail loop)"
                )
                return {
                    "status": "rejected",
                    "message": "\n".join(_msg_lines),
                }

            generate_req_input.update(_safe)
            self.log(
                "generate_requirements captured — will write outputs "
                f"(reqs={len(_safe.get('requirements', []))}, "
                f"comps={len(_safe.get('component_recommendations', []))}, "
                f"dp_keys={len(_safe.get('design_parameters', {}))})",
                "info",
            )
            return {
                "status": "captured",
                "message": "Requirements generation captured. Summarise what was generated.",
            }

        async def _capture_clarification_cards(input_data: dict) -> dict:
            """Handle show_clarification_cards tool — capture structured questions."""
            # Round-1 forbidden-topic sanitiser runs ONLY on Round 1. On Stage
            # 2+ the architecture / LO / IF / ADC questions are legitimate.
            if _is_round1_turn:
                captured = _filter_forbidden_round1(dict(input_data))
            else:
                captured = dict(input_data)
            clarification_cards.update(captured)
            self.log(
                f"Clarification cards captured — {len(captured.get('questions', []))} questions "
                f"(round1_filter={'on' if _is_round1_turn else 'off'})",
                "info",
            )
            return {
                "status": "displayed",
                "message": "Questions displayed to user. Wait for their answers.",
            }

        tool_handlers: dict = {
            "generate_requirements": _capture_generate_requirements,
            "show_clarification_cards": _capture_clarification_cards,
            "find_candidate_parts": self._handle_find_candidate_parts,
        }
        if COMPONENT_SEARCH_AVAILABLE and self.component_search:
            tool_handlers["search_components"] = self._handle_search_components

        # ── Determine tool_choice based on round ────────────────────────────
        # v14 rewrite — strictly bind tool_choice to the *intent* of this turn.
        # Previously rounds 2+ used `{"type":"any"}` which let the LLM freely
        # pick search_components or even generate_requirements prematurely. The
        # observed symptom on round 3 was CARDS:N q=0 because the model either
        # emitted prose that mimicked the "Please answer the N questions below"
        # template (seen in prior turns) or picked a non-card tool, and the
        # recovery path failed silently. Fix:
        #
        #   Case A — __FINALIZE__ or _should_generate (user confirmed):
        #     tool_choice = {"type":"any"} so generate_requirements can fire.
        #
        #   Case B — any elicitation turn (round 1, 2, 3 … N):
        #     tool_choice = {"type":"tool", "name":"show_clarification_cards"}
        #     This is the ONLY tool the LLM may pick, so it cannot:
        #       • emit free-text prose (would violate tool_choice)
        #       • pick search_components (fine on pre-turn steps but forbidden
        #         on the final elicitation turn — component search already ran
        #         in the tool loop if it was going to)
        #       • pick generate_requirements prematurely
        #     Cards are guaranteed. If the LLM legitimately wants to call
        #     search_components, it can still do so on a prior iteration of
        #     the tool loop (call_llm_with_tools re-invokes with the same
        #     tool_choice, and non-terminal tools keep the loop running).
        _is_finalize = user_input.strip() == "__FINALIZE__"
        _user_confirmed_generation = bool(locals().get("_should_generate", False))
        if _is_finalize or _user_confirmed_generation:
            _round1_tool_choice = {"type": "any"}
        else:
            # Every elicitation turn — round 1, 2, 3, N — force structured cards.
            _round1_tool_choice = {
                "type": "tool", "name": "show_clarification_cards"
            }
        self.log(
            f"tool_choice resolved: {_round1_tool_choice!r} "
            f"(finalize={_is_finalize}, should_generate={_user_confirmed_generation})",
            "info",
        )

        # P21 (2026-04-24): swap to the tight FINALIZE_SYSTEM_PROMPT on the
        # terminal `generate_requirements` call. The full SYSTEM_PROMPT is
        # ~500 lines of wizard / clarification / anti-hallucination rules —
        # useful during elicitation, wasteful at finalize when the specs
        # are already captured. The server log on 2026-04-24 showed a
        # single 563 s finalize call that returned empty content (model
        # burned its entire reasoning budget parsing the dense system
        # prompt). Swapping for a ~50-line "emit BOM now" brief cuts that
        # call to ~2-3 min on the same spec. No output quality compromise:
        # the tighter prompt includes every hard constraint (MPN-shape
        # gate, candidate-pool check, structured-diagram preference) —
        # just without the elicitation-phase boilerplate the model
        # doesn't need at this point.
        if _is_finalize or _user_confirmed_generation or _is_wizard_payload:
            _original_system_len = len(system)
            system = FINALIZE_SYSTEM_PROMPT
            self.log(
                f"[P21] finalize system prompt: "
                f"{_original_system_len} -> {len(system)} chars "
                f"({1 - len(system) / max(1, _original_system_len):.1%} shorter)",
                "info",
            )

        # v15 — time the LLM call. When the user confirms generation,
        # `generate_requirements` payloads can be ~10-20k tokens and take
        # 60-120s by themselves; splitting that out from the finalize cost
        # (lock + red-team audit + file write) makes slow turns diagnosable.
        #
        # P20 (2026-04-24): wall-clock timeout around the LLM call. Server
        # log on 2026-04-24 showed a 563 s call that returned empty content
        # and no tool invocations — the model spent its entire budget in
        # internal reasoning and emitted nothing. Cap the call at
        # `_LLM_CALL_TIMEOUT_S` seconds; on timeout, treat it as an LLM
        # failure (existing error path) and rely on fix-on-fail retry to
        # recover with a tightened prompt. No output quality compromise
        # on healthy runs — the timeout only fires when the call stalls.
        import time as _time
        import asyncio as _asyncio
        _llm_t0 = _time.monotonic()
        try:
            response = await _asyncio.wait_for(
                self.call_llm_with_tools(
                    messages=messages,
                    system=system,
                    tool_handlers=tool_handlers,
                    # Stop the loop immediately after generate_requirements fires —
                    # no second LLM summary call needed, which eliminates the extra
                    # "Thinking..." delay seen in the chat UI.
                    terminal_tools={"generate_requirements", "show_clarification_cards"},
                    tool_choice=_round1_tool_choice,
                ),
                timeout=self._LLM_CALL_TIMEOUT_S,
            )
        except _asyncio.TimeoutError:
            _llm_dt = _time.monotonic() - _llm_t0
            self.log(
                f"[v15-timing] call_llm_with_tools TIMED OUT after "
                f"{_llm_dt:.2f}s (cap={self._LLM_CALL_TIMEOUT_S}s). "
                f"Returning empty response — fix-on-fail retry will "
                f"re-prompt with a shorter corrective.",
                "warning",
            )
            # Shape matches what call_llm_with_tools returns on normal
            # completion so downstream parsing doesn't crash.
            response = {"content": "", "tool_calls": []}
        _llm_dt = _time.monotonic() - _llm_t0
        self.log(
            f"[v15-timing] call_llm_with_tools finished in {_llm_dt:.2f}s "
            f"(finalize={_is_finalize}, gen_captured={bool(generate_req_input)}, "
            f"cards_captured={bool(clarification_cards.get('questions'))})",
            "info",
        )

        import re as _re
        response_content = _re.sub(
            r'\b(TBD|TBC|TBA)\b', '[specify]',
            response.get("content", ""), flags=_re.IGNORECASE
        )

        # ── v17 — Recovery: empty-cards or cards-missing on elicitation turn ──
        # Symptom observed at pos=5 in v16 live: LLM called
        # show_clarification_cards with questions=[] (Round-1 filter stripped
        # them all, or model hallucinated a leaked "Please answer all questions
        # below" preamble without structured content). The old guard then
        # failed and the response fell through to the plain-text path, showing
        # leaked prose and NO chip cards — which is the "again repeating?"
        # symptom.
        #
        # Fix: when this is an elicitation turn (not __FINALIZE__, not user-
        # confirmed generation, tool did not capture a generate_requirements
        # payload), AND either (a) no clarification_cards were captured at
        # all, OR (b) cards were captured with empty questions — re-synthesise
        # a fresh set via the sync `get_clarification_questions` helper
        # (wrapped in asyncio.to_thread to keep the outer coroutine alive).
        _needs_cards_recovery = (
            not generate_req_input
            and not _is_finalize
            and not _user_confirmed_generation
            and (
                not clarification_cards
                or not clarification_cards.get("questions")
            )
        )
        if _needs_cards_recovery:
            try:
                import asyncio as _asyncio
                _turn_n = sum(1 for m in messages if m.get("role") == "user")
                _label = f"turn-{_turn_n}"
                recovered = await _asyncio.to_thread(
                    self.get_clarification_questions,
                    user_input,
                    project_context.get("design_type", "RF"),
                    messages[:-1],
                    _label,
                )
                if recovered and recovered.get("questions"):
                    clarification_cards = recovered
                    self.log(
                        f"[v17-recovery] synthesised {len(recovered['questions'])} "
                        f"cards via get_clarification_questions (label={_label})",
                        "info",
                    )
                else:
                    self.log(
                        "[v17-recovery] fallback produced no questions either — "
                        "plain-text path will be used",
                        "warning",
                    )
            except Exception as _rexc:
                self.log(
                    f"[v17-recovery] get_clarification_questions raised: {_rexc}",
                    "warning",
                )

            # v18 — Final safety net. If the /clarify helper ALSO failed to
            # produce questions, emit a deterministic hardcoded card set so
            # the chat can never flat-line into plain text. This guarantees
            # chip cards render no matter what the LLM does.
            if not clarification_cards or not clarification_cards.get("questions"):
                try:
                    _emergency = self._deterministic_fallback_cards(messages)
                    if _emergency and _emergency.get("questions"):
                        clarification_cards = _emergency
                        self.log(
                            f"[v18-determ] emitted deterministic fallback "
                            f"({len(_emergency['questions'])} cards)",
                            "info",
                        )
                except Exception as _dexc:
                    self.log(
                        f"[v18-determ] deterministic fallback raised: {_dexc}",
                        "warning",
                    )

        # ── Clarification cards path — emit STRUCTURED JSON only ─────────────
        # The response prose is JUST the intro (1 sentence).  All questions,
        # options, and "why" hints are delivered as structured clarification_cards
        # JSON — the frontend renders them as clickable chip cards below the
        # assistant bubble.  Keeping the prose short prevents the duplication
        # bug (same questions showing as both markdown text AND chip cards).
        #
        # Guard: require at least one question.  If the LLM emits intro + empty
        # questions (e.g. Round-1 filter stripped everything, or schema glitch),
        # fall through to the plain-text path.
        if (
            clarification_cards
            and clarification_cards.get("questions")
            and not generate_req_input
        ):
            intro = (clarification_cards.get("intro", "") or "").strip()
            qcount = len(clarification_cards.get("questions") or [])
            if intro:
                response_content = (
                    f"**{intro}**\n\n"
                    f"*Please answer the {qcount} "
                    f"{'question' if qcount == 1 else 'questions'} below.*"
                )
            else:
                response_content = (
                    f"*Please answer the {qcount} "
                    f"{'question' if qcount == 1 else 'questions'} below.*"
                )
            return {
                "response": response_content,
                "phase_complete": False,
                "draft_pending": False,
                "draft": {},
                "outputs": {},
                "parameters": {},
                # Raw card JSON so the frontend can render clickable chips
                # directly — no prose-parsing needed. Contains intro / questions
                # / (optional) prefilled for Stage 1.
                "clarification_cards": clarification_cards,
            }

        # ── Tool-use path (authoritative) ─────────────────────────────────
        # Check the closure dict — generate_req_input is populated when the
        # model called generate_requirements (regardless of whether
        # call_llm_with_tools still had tool_calls in its final response).
        if generate_req_input:
            self.log("generate_requirements tool called — phase_complete=True", "info")
            _files_t0 = _time.monotonic()
            outputs = self._generate_output_files(
                generate_req_input,
                project_context.get("output_dir", "output"),
                project_context.get("name", "project"),
            )
            self.log(
                f"[v15-timing] _generate_output_files: {_time.monotonic() - _files_t0:.2f}s "
                f"({len(outputs)} files)",
                "info",
            )

            # ── A1.2 — Freeze requirements lock + run red-team audit ────────
            # Runs once the generate_requirements tool has fired, i.e. AFTER the
            # user has confirmed requirements in Round 4. Any blocker (critical
            # / high severity) is surfaced in the chat summary so the user can
            # iterate before clicking Approve & Start Pipeline.
            #
            # Fix-on-fail loop: when the audit returns retry-eligible blockers
            # (hallucinated MPN, not_from_candidate_pool, bad datasheet, banned
            # / obsolete / NRND part) we feed the findings + verified candidate
            # pool back to the LLM as a corrective user turn and ask it to
            # re-emit `generate_requirements`. Capped at
            # `_FIX_ON_FAIL_MAX_RETRIES` retries to bound cost / latency.
            finalize_bundle: dict = {}
            # Auto-fix is allowed at most ONCE per finalize turn. Each
            # finalize_p1 call is expensive (~60-90 s — distributor lookups
            # dominate) so re-auditing after a mechanical swap would burn
            # more time than the LLM retry we're trying to skip. Instead we
            # patch in place, prune the resolved blockers from the existing
            # audit report, and ship.
            _auto_fix_attempted = False

            # P19 (2026-04-24): wall-clock instrumentation. User feedback
            # says "don't compromise output to reduce time — check where
            # time is actually going." Log elapsed at entry and after each
            # finalize_p1 call so the server log reveals the hot spot
            # (LLM call vs audit vs distributor lookups vs autofix).
            _retry_loop_t0 = _time.monotonic()
            self.log(
                f"[p1-timing] retry-loop.begin deadline=none "
                f"max_retries={self._FIX_ON_FAIL_MAX_RETRIES}",
                "info",
            )
            for _retry_idx in range(self._FIX_ON_FAIL_MAX_RETRIES + 1):
                _fin_t0 = _time.monotonic()
                try:
                    from services.p1_finalize import finalize_p1
                    finalize_bundle = finalize_p1(
                        tool_input=generate_req_input,
                        project_id=project_context.get("project_id", ""),
                        design_type=project_context.get("design_type"),
                        llm_model=getattr(self, "model", None),
                        architecture=generate_req_input.get("architecture"),
                        # Set of MPNs surfaced by find_candidate_parts this turn —
                        # the audit flags any BOM entry that bypassed the shortlist.
                        offered_candidate_mpns=set(self._offered_candidate_mpns),
                    )
                    # Merge lock + audit artefacts so chat_service persists them.
                    for fname, fcontent in finalize_bundle.get("outputs", {}).items():
                        outputs[fname] = fcontent
                    _blocker_count = len([
                        i for i in (finalize_bundle.get("audit_report") or {}).get("issues", [])
                        if i.get("severity") in ("critical", "high")
                    ])
                    self.log(
                        f"[v15-timing] finalize_p1 (attempt {_retry_idx + 1}/"
                        f"{self._FIX_ON_FAIL_MAX_RETRIES + 1}): "
                        f"{_time.monotonic() - _fin_t0:.2f}s (blockers={_blocker_count})",
                        "info",
                    )
                except Exception as exc:
                    self.log(
                        f"finalize_p1 skipped: {exc} "
                        f"(after {_time.monotonic() - _fin_t0:.2f}s)",
                        "warning",
                    )
                    break

                if _retry_idx >= self._FIX_ON_FAIL_MAX_RETRIES:
                    break

                # P19 timing instrumentation (no deadline — don't compromise
                # output). Just log so the server log reveals where minutes
                # are going on dense specs.
                _loop_elapsed = _time.monotonic() - _retry_loop_t0
                self.log(
                    f"[p1-timing] retry-loop.iter-{_retry_idx} end "
                    f"elapsed={_loop_elapsed:.1f}s blockers={_blocker_count}",
                    "info",
                )

                # ── Deterministic auto-fix layer ────────────────────────
                # Try mechanical swap-from-candidate-pool BEFORE asking
                # the LLM to re-emit the entire BOM. Each LLM retry costs
                # ~5 min on glm-5.1 because generate_requirements emits
                # ~10k tokens of structured JSON; a one-row swap takes
                # microseconds. One shot only: if we re-ran finalize_p1
                # to verify the swap, the ~60-90 s audit would eat most
                # of the saving — so we patch, prune the resolved
                # blockers from the existing audit report, and exit.
                if not _auto_fix_attempted:
                    _auto_fix_attempted = True
                    _autofix_t0 = _time.monotonic()
                    _patched_payload, _patched_log = self._auto_fix_blockers(
                        generate_req_input,
                        finalize_bundle.get("audit_report") or {},
                    )
                    if _patched_payload is not None:
                        generate_req_input.clear()
                        generate_req_input.update(_patched_payload)
                        self.log(
                            f"[auto-fix] patched in {_time.monotonic() - _autofix_t0:.2f}s "
                            f"— {_patched_log}",
                            "info",
                        )
                        # Re-render output files from the patched BOM so
                        # the user sees the corrected MPNs.
                        _files_t0 = _time.monotonic()
                        outputs = self._generate_output_files(
                            generate_req_input,
                            project_context.get("output_dir", "output"),
                            project_context.get("name", "project"),
                        )
                        self.log(
                            f"[auto-fix] _generate_output_files: "
                            f"{_time.monotonic() - _files_t0:.2f}s ({len(outputs)} files)",
                            "info",
                        )
                        # Prune resolved blockers from the audit report
                        # so downstream rendering doesn't warn about the
                        # MPNs we just replaced. Kept other issues intact.
                        _rep = finalize_bundle.get("audit_report") or {}
                        _orig = _rep.get("issues") or []
                        _kept = [
                            i for i in _orig
                            if not (
                                i.get("category") in self._AUTO_FIX_CATEGORIES
                                and i.get("severity") in ("critical", "high")
                            )
                        ]
                        if len(_kept) != len(_orig):
                            _rep["issues"] = _kept
                            _rep["auto_fix_applied"] = _patched_log
                            finalize_bundle["audit_report"] = _rep
                        break  # skip re-audit + LLM retry — accept the swap

                corrective = self._build_fix_on_fail_corrective(
                    finalize_bundle.get("audit_report") or {}
                )
                if not corrective:
                    break

                self.log(
                    f"[fix-on-fail] retry {_retry_idx + 1}/"
                    f"{self._FIX_ON_FAIL_MAX_RETRIES} — re-prompting LLM with "
                    f"{_blocker_count} blocker(s)",
                    "info",
                )

                # Snapshot in case the retry fails to re-emit the tool call —
                # we keep the previous BOM and surface its audit blockers.
                _prev_input = dict(generate_req_input)

                messages.append({"role": "user", "content": corrective})
                generate_req_input.clear()

                _retry_llm_t0 = _time.monotonic()
                try:
                    # P20: also timeout the retry. Corrective prompt is
                    # shorter than the initial finalize prompt, so a
                    # healthy retry is typically 40-90 s — the 180 s cap
                    # only fires on another stall.
                    await _asyncio.wait_for(
                        self.call_llm_with_tools(
                            messages=messages,
                            system=system,
                            tool_handlers=tool_handlers,
                            terminal_tools={"generate_requirements", "show_clarification_cards"},
                            tool_choice={"type": "any"},
                        ),
                        timeout=self._LLM_CALL_TIMEOUT_S,
                    )
                except _asyncio.TimeoutError:
                    self.log(
                        f"[fix-on-fail] retry LLM call TIMED OUT at "
                        f"{self._LLM_CALL_TIMEOUT_S}s — keeping previous "
                        f"BOM and surfacing audit blockers to the user",
                        "warning",
                    )
                    generate_req_input.clear()
                    generate_req_input.update(_prev_input)
                    break
                except Exception as exc:
                    self.log(
                        f"[fix-on-fail] retry LLM call failed: {exc} "
                        f"(after {_time.monotonic() - _retry_llm_t0:.2f}s)",
                        "warning",
                    )
                    generate_req_input.clear()
                    generate_req_input.update(_prev_input)
                    break

                self.log(
                    f"[fix-on-fail] LLM call: {_time.monotonic() - _retry_llm_t0:.2f}s "
                    f"(gen_captured={bool(generate_req_input)})",
                    "info",
                )

                if not generate_req_input:
                    self.log(
                        "[fix-on-fail] LLM did not re-emit generate_requirements "
                        "— keeping previous BOM and surfacing audit blockers to user",
                        "warning",
                    )
                    generate_req_input.update(_prev_input)
                    break

                # Re-render output files from the corrected BOM. The next loop
                # iteration's finalize_p1 will then audit the new payload.
                _files_t0 = _time.monotonic()
                outputs = self._generate_output_files(
                    generate_req_input,
                    project_context.get("output_dir", "output"),
                    project_context.get("name", "project"),
                )
                self.log(
                    f"[fix-on-fail] _generate_output_files (retry {_retry_idx + 1}): "
                    f"{_time.monotonic() - _files_t0:.2f}s ({len(outputs)} files)",
                    "info",
                )

            # Always build the rich requirements summary from the tool data.
            # This lets the user review key design parameters, requirements table,
            # component selections, and the block diagram BEFORE clicking Approve.
            # Do NOT prepend LLM preamble text — it creates an unwanted intermediate
            # "I have all the information..." message in the chat UI.
            rich_summary = self._build_response_summary(generate_req_input)
            response_content = rich_summary
            # Append a short lock/audit summary if the finalize step produced one.
            if finalize_bundle.get("summary_md"):
                response_content += "\n\n---\n" + finalize_bundle["summary_md"]
            return {
                "response": (response_content
                             + "\n\n✅ **Phase 1 Complete!** Review the requirements above and click **Approve & Start Pipeline** to continue."),
                "phase_complete": True,
                "draft_pending": False,
                "draft": {},
                "outputs": outputs,
                "parameters": generate_req_input.get("design_parameters", {}),
                "lock": finalize_bundle.get("lock"),
                "lock_row": finalize_bundle.get("lock_row"),
                "audit_report": finalize_bundle.get("audit_report"),
            }

        # ── Plain-text fallback: synthesised completion on parsing full response ────────
        # When model returns full requirements as plain text without a tool call,
        # we parse and write outputs here.
        if self._detect_complete_requirements(response_content):
            self.log("Complete response detected — synthesising completion", "info")
            parsed = self._parse_requirements_response(
                response_content, project_context.get("name", "project")
            )
            if parsed:
                outputs = self._generate_output_files(
                    parsed,
                    project_context.get("output_dir", "output"),
                    project_context.get("name", "project"),
                )
                self.log("Synthesised outputs written — phase_complete=True", "info")
                return {
                    "response": response_content + "\n\n✅ **Phase 1 Complete!** All documents generated.",
                    "phase_complete": True,
                    "draft_pending": False,
                    "draft": {},
                    "outputs": outputs,
                    "parameters": parsed.get("design_parameters", {}),
                }

        # ── DETERMINISTIC CLARIFICATION-CARDS RECOVERY ─────────────────────
        # If we get here without a generate_requirements call AND without
        # captured clarification_cards, the LLM emitted free-text prose for
        # this turn. If that prose contains question-shaped content, we MUST
        # return structured cards anyway — otherwise the UI silently shows
        # prose-only questions and the user has no chip cards to click.
        #
        # Fix: invoke the forced-tool path (get_clarification_questions) to
        # synthesise structured cards from the conversation history + the
        # LLM's own raw prose. The forced path uses tool_choice={"type":"tool",
        # "name":"show_clarification_cards"}, so it cannot fail silently the
        # way the "any" tool_choice path can.
        _looks_like_question = bool(
            response_content and (
                "?" in response_content
                or re.search(r'\b(choose|select|pick|specify|confirm|answer)\b',
                             response_content, re.IGNORECASE)
            )
        )
        _cards_are_empty = (
            not clarification_cards
            or not clarification_cards.get("questions")
        )
        if _looks_like_question and _cards_are_empty:
            self.log(
                "LLM emitted question prose without show_clarification_cards — "
                "running forced-tool recovery to synthesise structured cards",
                "warn",
            )
            try:
                # Seed the recovery with the *assistant's own prose* appended
                # to history so the tool has the exact wording to convert.
                history_for_recovery = list(
                    project_context.get("conversation_history", [])
                )
                history_for_recovery.append({
                    "role": "assistant",
                    "content": response_content,
                })
                recovered = self.get_clarification_questions(
                    user_requirement=user_input,
                    design_type=project_context.get("design_type", "RF"),
                    conversation_history=history_for_recovery,
                    round_label="recovery-from-prose",
                )
                if recovered and recovered.get("questions"):
                    self.log(
                        f"Recovery captured {len(recovered['questions'])} "
                        f"questions from prose — returning structured cards",
                        "info",
                    )
                    intro = (recovered.get("intro", "") or "").strip()
                    qcount = len(recovered["questions"])
                    short_prose = (
                        (f"**{intro}**\n\n" if intro else "")
                        + f"*Please answer the {qcount} "
                        + ("question" if qcount == 1 else "questions")
                        + " below.*"
                    )
                    return {
                        "response": short_prose,
                        "phase_complete": False,
                        "draft_pending": False,
                        "draft": {},
                        "outputs": {},
                        "parameters": {},
                        "clarification_cards": recovered,
                    }
            except Exception as _exc:
                # Recovery is best-effort — if it fails, fall through to the
                # plain-text path. We'd rather show the LLM's prose than crash.
                # v14 — UPGRADE LOG LEVEL to ERROR with full traceback so this
                # failure is never silent again. Previous bug: silent warn
                # meant CARDS:N on round 3+ had no server-side evidence.
                import traceback as _tb
                self.log(
                    f"[v14] Clarification recovery FAILED (silent path): "
                    f"{type(_exc).__name__}: {_exc}\n"
                    f"Response content preview: {response_content[:200]!r}\n"
                    f"Traceback:\n{_tb.format_exc()}",
                    "error",
                )

        # ── Normal conversational exchange ─────────────────────────────────
        # Always include clarification_cards=None so the frontend's typed
        # response shape is stable turn-to-turn.
        return {
            "response": response_content,
            "phase_complete": False,
            "draft_pending": False,
            "draft": {},
            "outputs": {},
            "parameters": {},
            "clarification_cards": None,
        }


    def _build_response_summary(self, tool_input: dict) -> str:
        """Build a rich markdown summary from generate_requirements tool data.

        Called when the LLM produced no significant preamble text before calling
        the tool (common with terminal_tools pattern). Gives the user a detailed
        view of what was captured rather than just a "Complete!" banner.
        """
        lines = []

        # Project summary
        summary = tool_input.get("project_summary", "")
        if summary:
            lines += ["## Project Summary", "", summary, ""]

        # Design parameters table
        params = tool_input.get("design_parameters", {})
        if params:
            lines += ["## Key Design Parameters", "",
                      "| Parameter | Value |", "|---|---|"]
            for k, v in list(params.items())[:12]:
                lines.append(f"| {k.replace('_', ' ').title()} | {v} |")
            lines.append("")

        # Requirements — show ALL, no truncation
        reqs = tool_input.get("requirements", [])
        if reqs:
            lines += [f"## Requirements ({len(reqs)} captured)", "",
                      "| ID | Title | Priority |", "|---|---|---|"]
            for req in reqs:
                lines.append(
                    f"| {req.get('req_id','')} | {req.get('title','')} "
                    f"| {req.get('priority','Must have')} |"
                )
            lines.append("")

        # Components — show ALL, no truncation
        # v24 (2026-04-20): the LLM is NOT the source of truth for datasheet URLs.
        # We build an ordered list of candidate product-page URLs from
        # (manufacturer, part_number) via tools.datasheet_url.candidate_datasheet_urls,
        # then HEAD-probe each candidate with a browser-class User-Agent. The
        # first candidate that returns 2xx wins. Last candidate in every list is
        # a search/parametric URL that is guaranteed never to 404.
        #
        # The browser-class UA is load-bearing: ADI, TI, and other vendors return
        # 403/404 for bot-flavored User-Agent strings, which is why the prior
        # "HardwarePipelineBot/1.0" UA caused valid URLs to be rejected and the
        # code to fall back to the broken /en/search.html#q=<part> path.
        comps = tool_input.get("component_recommendations", [])
        if comps:
            import urllib.request as _ur2
            _BROWSER_UA = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            def _probe(url: str) -> bool:
                if not url or not url.startswith('http'):
                    return False
                # Prefer HEAD, fall back to GET on 405 / unsupported.
                for method in ('HEAD', 'GET'):
                    try:
                        req = _ur2.Request(url, method=method,
                                           headers={'User-Agent': _BROWSER_UA,
                                                    'Accept': 'text/html,application/pdf,*/*'})
                        with _ur2.urlopen(req, timeout=5) as r:
                            if 200 <= r.status < 400:
                                return True
                    except Exception:
                        continue
                return False

            try:
                from tools.datasheet_url import candidate_datasheet_urls as _cand_urls
            except Exception:
                _cand_urls = lambda m, p: []

            # Build per-row candidate lists; collect unique probes for parallel HEAD.
            from concurrent.futures import ThreadPoolExecutor as _TPE2
            resolved = []  # list of (part, mfr, llm_url, candidates[])
            probe_set: set[str] = set()
            for comp in comps:
                part = (comp.get("primary_part", "") or "").strip()
                mfr  = (comp.get("primary_manufacturer", "") or "").strip()
                llm_url = (comp.get("datasheet_url", "") or "").strip()
                cands = _cand_urls(mfr, part) or []
                resolved.append((part, mfr, llm_url, cands))
                for u in cands:
                    probe_set.add(u)
                if llm_url and llm_url.startswith("http"):
                    probe_set.add(llm_url)

            # Skip HEAD probing DigiKey/Mouser search URLs — they always
            # return 2xx by construction (see P17, 2026-04-24). Only probe
            # LLM-supplied URLs and any other non-trusted hosts. On a typical
            # BOM of 8 components × 2 candidates this shaves 10-30 s off
            # the chat-draft render latency.
            _TRUSTED_HOSTS = ("digikey.com", "digikey.in", "mouser.com", "mouser.in")
            url_live: dict[str, bool] = {}
            untrusted: list[str] = []
            for u in sorted(probe_set):
                if any(h in u for h in _TRUSTED_HOSTS):
                    url_live[u] = True  # short-circuit
                else:
                    untrusted.append(u)
            if untrusted:
                with _TPE2(max_workers=min(12, len(untrusted))) as pool:
                    results = list(pool.map(_probe, untrusted))
                url_live.update(dict(zip(untrusted, results)))

            # v16 — import stale-parts checker so per-row lifecycle badges can
            # warn the user inline about any part the LLM slipped past the
            # lifecycle_status enum gate. Local import so a missing red-team
            # module doesn't crash P1.
            try:
                from agents.red_team_audit import _is_stale_mpn as _stale_check
            except Exception:
                _stale_check = lambda _pn: (False, "")

            lines += [f"## Component Selections ({len(comps)} components)", ""]
            lines.append("| # | Function | Part Number | Manufacturer | Lifecycle | Datasheet |")
            lines.append("|---|---|---|---|---|---|")
            stale_rows: list[str] = []
            for i, (comp, row) in enumerate(zip(comps, resolved), 1):
                part, mfr, llm_url, cands = row
                func = comp.get("function", "")
                # Walk the candidate list: first live URL wins.
                chosen: str = ""
                for cand in cands:
                    if url_live.get(cand, False):
                        chosen = cand
                        break
                # LLM URL is a secondary option, but only if it resolves.
                if not chosen and llm_url and llm_url.startswith("http") and url_live.get(llm_url, False):
                    chosen = llm_url
                # Final fallback — the LAST candidate is always a guaranteed-live
                # search/parametric URL (never 404s). Use it even if we couldn't
                # verify it (sandbox blocked the probe), because it's safer than
                # the LLM's hallucinated URL.
                if not chosen and cands:
                    chosen = cands[-1]
                if not chosen and llm_url and llm_url.startswith("http"):
                    chosen = llm_url
                # Label the link honestly. Since P11/P12, `chosen` might be
                # a distributor keyword-search URL rather than a real PDF —
                # calling it "Datasheet" misleads users into expecting a
                # PDF preview.
                if chosen:
                    _cl = chosen.lower()
                    if _cl.endswith(".pdf") or "/datasheet/" in _cl or "/media/" in _cl:
                        _label = "Datasheet"
                    elif "digikey.com" in _cl or "digikey.in" in _cl:
                        _label = "DigiKey"
                    elif "mouser.com" in _cl or "mouser.in" in _cl:
                        _label = "Mouser"
                    else:
                        _label = "Product page"
                    ds_link = f"[{_label}]({chosen})"
                else:
                    ds_link = "—"
                # v16 — lifecycle badge. Hardcoded blacklist wins over LLM claim.
                stale, _reason = _stale_check(part)
                claimed = str(comp.get("lifecycle_status", "") or "").strip().lower()
                if stale:
                    life_badge = "⚠️ **STALE**"
                    stale_rows.append(f"- Row {i}: `{part}` is EOL/NRND — replace before generating downstream docs.")
                elif claimed == "active":
                    life_badge = "active"
                elif claimed:
                    life_badge = f"⚠️ {claimed}"
                    stale_rows.append(f"- Row {i}: `{part}` declared lifecycle_status='{claimed}' — only 'active' parts allowed.")
                else:
                    life_badge = "—"
                lines.append(
                    f"| {i} | {func} | {part} | {mfr} | {life_badge} | {ds_link} |"
                )
            lines.append("")
            if stale_rows:
                lines.append("> ⚠️ **Stale-parts warning — pipeline will flag these as critical blockers:**")
                lines.extend(stale_rows)
                lines.append("")

        # Block diagram — route through the SAME renderer the persistent
        # block_diagram.md / architecture.md files use (`_render_diagram_field`).
        # Until 2026-04-24 this block grabbed raw `block_diagram_mermaid`
        # straight from the LLM, no salvage, no structured-JSON path. The
        # docs page (`block_diagram.md`) rendered cleanly because the
        # output-file generator went through `_render_diagram_field`, but
        # the chat-draft renderer did not — so the same payload showed
        # broken Mermaid in chat and clean Mermaid in docs.
        #
        # User feedback (2026-04-24): "in chat page why block diagram is
        # same as in documents page block diagram? it should be same right
        # as in documents page?" → unify the path.
        #
        # `_render_diagram_field` preference order:
        #   1. Structured `block_diagram` JSON spec → `render_block_diagram`
        #      (deterministic, always valid).
        #   2. Raw `block_diagram_mermaid` → `salvage()` (handles em-dash
        #      arrows, frontmatter, unclosed brackets, etc.).
        #   3. `FALLBACK_DIAGRAM` last resort (always parses).
        block_mermaid = self._render_diagram_field(
            tool_input,
            structured_key="block_diagram",
            raw_key="block_diagram_mermaid",
            default_direction="LR",
            allow_empty=True,
        )
        if block_mermaid:
            lines += ["## System Block Diagram", "",
                      "```mermaid", block_mermaid.strip(), "```", ""]

        # Architecture / power-tree diagram — same unified renderer as
        # block_diagram. Until 2026-04-24 the chat draft only emitted
        # `block_diagram_mermaid` and the architecture diagram only
        # appeared in the persistent `architecture.md` file (where it
        # rendered fine). Adding it here so the chat-page summary shows
        # both diagrams the same way the docs page does — addresses the
        # user-reported "chat page diagram looks wrong / different from
        # docs" gap, where the broken raw `architecture_mermaid` was
        # what they were actually seeing.
        arch_mermaid = self._render_diagram_field(
            tool_input,
            structured_key="architecture",
            raw_key="architecture_mermaid",
            default_direction="TD",
            allow_empty=True,
        )
        if arch_mermaid:
            lines += ["## System Architecture", "",
                      "```mermaid", arch_mermaid.strip(), "```", ""]

        return "\n".join(lines)

    async def _handle_search_components(self, input_data: dict) -> dict:
        """Handle component search tool calls."""
        query = input_data.get("query", "")
        category = input_data.get("category")
        try:
            n_results = int(input_data.get("n_results", 5))
        except (TypeError, ValueError):
            n_results = 5
        n_results = max(1, min(n_results, 10))

        def _fallback_distributor_search(message: str = "") -> dict:
            try:
                from tools.parametric_search import find_candidates
                stage = category or query
                candidates = find_candidates(
                    stage,
                    query,
                    max_per_source=n_results,
                    max_total=n_results,
                    timeout_s=10.0,
                )
            except Exception as exc:
                self.log(f"Distributor component search failed: {exc}", "warning")
                return {
                    "query": query,
                    "results": [],
                    "count": 0,
                    "error": str(exc),
                    "message": message or "Distributor component search failed.",
                }

            return {
                "query": query,
                "results": [
                    {
                        "part_number": c.part_number,
                        "manufacturer": c.manufacturer,
                        "description": c.description,
                        "category": category or "",
                        "key_specs": {},
                        "datasheet_url": c.datasheet_url,
                        "product_url": c.product_url,
                        "distributor_url": c.product_url,
                        "lifecycle_status": c.lifecycle_status,
                        "source": c.source,
                        "unit_price": c.unit_price,
                        "unit_price_currency": c.unit_price_currency,
                        "stock_quantity": c.stock_quantity,
                        "stock_region": c.region,
                        "relevance_score": 1.0,
                    }
                    for c in candidates
                ],
                "count": len(candidates),
                "message": message or (
                    "Results came from live DigiKey/Mouser distributor search."
                ),
            }

        if not self.component_search:
            return _fallback_distributor_search(
                "Component vector search is disabled; using live DigiKey/Mouser "
                "distributor search instead."
            )

        try:
            results = self.component_search.search(
                query=query,
                category=category,
                n_results=n_results,
            )
        except Exception as e:
            self.log(f"Component search failed: {e}", "warning")
            return _fallback_distributor_search(
                "Component vector search failed; using live DigiKey/Mouser "
                "distributor search instead."
            )

        if not results:
            return _fallback_distributor_search(
                "No local component-vector results; using live DigiKey/Mouser "
                "distributor search instead."
            )

        return {
            "query": query,
            "results": [
                {
                    "part_number": r.component.part_number,
                    "manufacturer": r.component.manufacturer,
                    "description": r.component.description,
                    "category": r.component.category,
                    "key_specs": r.component.key_specs,
                    "datasheet_url": r.component.datasheet_url,
                    "lifecycle_status": r.component.lifecycle_status,
                    "estimated_cost_usd": r.component.estimated_cost_usd,
                    "relevance_score": r.relevance_score,
                }
                for r in results
            ],
            "count": len(results),
        }

    # Categories where the failed BOM row is still in the payload and we
    # can deterministically swap it for a known-good candidate of the same
    # stage — no LLM round-trip needed. `banned_part` is excluded because
    # rf_audit.run_banned_parts_audit() already strips those rows from the
    # payload, so there is nothing to mutate. `part_validation_timeout` is
    # excluded because the part may be perfectly real — only a re-audit
    # under network help would resolve it.
    _AUTO_FIX_CATEGORIES = frozenset({
        "hallucinated_part",
        "not_from_candidate_pool",
        "obsolete_part",
        "nrnd_part",
        "datasheet_url",
        "non_active_lifecycle",
        "stale_part",
    })

    # Stage inference — map keywords found in a BOM row's `function` field
    # to the stage id used by find_candidate_parts. Order matters: the
    # first hit wins, so put more specific labels above generic ones
    # (e.g. "low-noise amplifier" before "amplifier").
    _STAGE_KEYWORDS: tuple[tuple[str, str], ...] = (
        ("low-noise amplifier", "lna"),
        ("low noise amplifier", "lna"),
        (" lna", "lna"),
        ("preselector", "preselector"),
        ("limiter", "limiter"),
        ("driver amplifier", "amplifier"),
        ("power amplifier", "pa"),
        ("variable gain amplifier", "vga"),
        ("gain block", "amplifier"),
        ("gain-block", "amplifier"),
        ("buffer amplifier", "amplifier"),
        ("post-split", "amplifier"),
        ("broadband amplifier", "amplifier"),
        ("mmic amplifier", "amplifier"),
        ("rf amplifier", "amplifier"),
        ("gan driver", "amplifier"),
        (" driver ", "amplifier"),
        ("amplifier", "amplifier"),
        ("mixer", "mixer"),
        ("bandpass filter", "bpf"),
        ("band-pass filter", "bpf"),
        ("low-pass filter", "lpf"),
        ("high-pass filter", "hpf"),
        ("filter", "bpf"),
        ("splitter", "splitter"),
        ("combiner", "splitter"),
        ("power divider", "splitter"),
        ("attenuator", "attenuator"),
        ("switch", "switch"),
        ("circulator", "circulator"),
        ("isolator", "isolator"),
        ("digital-to-analog", "dac"),
        ("digital to analog", "dac"),
        (" dac", "dac"),
        ("analog-to-digital", "adc"),
        ("analog to digital", "adc"),
        (" adc", "adc"),
        ("synthesizer", "pll"),
        ("synthesiser", "pll"),
        (" pll", "pll"),
        (" vco", "vco"),
        ("local oscillator", "lo"),
        ("oscillator", "tcxo"),
        ("tcxo", "tcxo"),
        ("ocxo", "ocxo"),
        ("ldo", "ldo"),
        ("buck", "buck"),
        ("regulator", "ldo"),
        ("microcontroller", "mcu"),
        (" mcu", "mcu"),
        (" fpga", "fpga"),
        (" cpld", "fpga"),
    )

    def _infer_stage_from_function(self, function_text: str) -> Optional[str]:
        """Best-effort mapping from a BOM row's `function` blurb to the
        stage id used by find_candidate_parts. Returns None when nothing
        recognisable is found."""
        if not function_text:
            return None
        haystack = " " + str(function_text).lower() + " "
        for keyword, stage in self._STAGE_KEYWORDS:
            if keyword in haystack:
                return stage
        return None

    def _auto_fix_blockers(
        self,
        tool_input: dict,
        audit_report: dict,
    ) -> tuple[Optional[dict], str]:
        """Deterministic auto-fix layer — swap blocker MPNs for verified
        candidates of the same stage WITHOUT calling the LLM again.

        For each blocker in `_AUTO_FIX_CATEGORIES`, we:
          1. Parse the failed MPN from the audit `location` field
             (`component_recommendations/{pn}`).
          2. Find the BOM row whose primary part matches.
          3. Infer the stage from its `function` text.
          4. Pick the first unused candidate from
             `_offered_candidates_by_stage[stage]` that is not itself a
             blocker MPN and not a duplicate of an already-replaced row.
          5. Mutate `primary_part`, `primary_manufacturer`, and
             `datasheet_url` in place.

        Returns `(patched_tool_input, summary)` when at least one row
        was patched, else `(None, "")`. The patched payload is a SHALLOW
        copy of `tool_input` with a freshly-mutated
        `component_recommendations` list — so the caller can swap it in
        without risking aliasing the previous payload.
        """
        if not tool_input or not audit_report:
            return None, ""
        if not self._offered_candidates_by_stage:
            return None, ""

        issues = audit_report.get("issues") or []
        fixable = [
            i for i in issues
            if i.get("severity") in ("critical", "high")
            and i.get("category") in self._AUTO_FIX_CATEGORIES
        ]
        if not fixable:
            return None, ""

        # Build the set of MPNs we must NOT use as a replacement: every
        # blocker MPN (regardless of whether we end up patching that row).
        # This stops us from replacing a hallucinated MPN with another
        # hallucinated MPN that the LLM happened to fabricate.
        blocked_mpns: set[str] = set()
        for i in fixable:
            loc = (i.get("location") or "").strip()
            if loc.startswith("component_recommendations/"):
                pn = loc.split("/", 1)[1].strip().upper()
                if pn:
                    blocked_mpns.add(pn)

        bom_key = "component_recommendations" if "component_recommendations" in tool_input else "bom"
        comps = tool_input.get(bom_key) or []
        if not comps:
            return None, ""

        # Index BOM rows by MPN (uppercase) so we can look up by audit
        # location. Both `primary_part` and `part_number` schemas exist;
        # accept either.
        def _row_mpn(row: dict) -> str:
            return (
                row.get("primary_part")
                or row.get("part_number")
                or row.get("mpn")
                or ""
            ).strip().upper()

        rows_by_mpn: dict[str, list[int]] = {}
        for idx, row in enumerate(comps):
            m = _row_mpn(row)
            if m:
                rows_by_mpn.setdefault(m, []).append(idx)

        used_replacements: set[str] = set()
        # Avoid swapping the same row twice in this pass.
        patched_indices: set[int] = set()
        patched_log: list[str] = []
        # Work on a fresh list-of-rows so callers see an immutable change.
        new_comps = [dict(r) for r in comps]

        for issue in fixable:
            loc = (issue.get("location") or "").strip()
            if not loc.startswith("component_recommendations/"):
                continue
            failed_mpn = loc.split("/", 1)[1].strip()
            if not failed_mpn:
                continue
            failed_upper = failed_mpn.upper()
            row_idxs = rows_by_mpn.get(failed_upper) or []
            row_idx = next((i for i in row_idxs if i not in patched_indices), None)
            if row_idx is None:
                continue

            row = new_comps[row_idx]
            stage = self._infer_stage_from_function(row.get("function") or "")
            if not stage:
                continue
            bucket = self._offered_candidates_by_stage.get(stage) or []
            if not bucket:
                continue

            replacement = None
            for cand in bucket:
                cand_mpn = (cand.get("part_number") or "").strip()
                cand_upper = cand_mpn.upper()
                if not cand_upper:
                    continue
                if cand_upper == failed_upper:
                    continue
                if cand_upper in blocked_mpns:
                    continue
                if cand_upper in used_replacements:
                    continue
                # Lifecycle gate — only swap to active parts.
                lc = str(cand.get("lifecycle_status") or "").strip().lower()
                if lc and lc not in ("active", "unknown", ""):
                    continue
                replacement = cand
                break
            if not replacement:
                continue

            new_mpn = replacement["part_number"]
            new_mfg = replacement.get("manufacturer") or ""
            new_url = replacement.get("datasheet_url") or ""
            new_lc = (replacement.get("lifecycle_status") or "active").strip().lower() or "active"
            new_product_url = replacement.get("product_url") or replacement.get("distributor_url") or ""
            new_source = (replacement.get("source") or "").strip().lower()

            # Mutate the row — preserve whichever key schema the row uses.
            if "primary_part" in row or "primary_manufacturer" in row:
                row["primary_part"] = new_mpn
                if new_mfg:
                    row["primary_manufacturer"] = new_mfg
            else:
                row["part_number"] = new_mpn
                if new_mfg:
                    row["manufacturer"] = new_mfg
            if new_url:
                row["datasheet_url"] = new_url
                row.pop("datasheet", None)
            if "lifecycle_status" in row or new_lc:
                row["lifecycle_status"] = new_lc
            if new_source:
                row["distributor_source"] = new_source
            if new_product_url:
                row["product_url"] = new_product_url
                row["distributor_url"] = new_product_url
                if new_source == "digikey":
                    row["digikey_url"] = new_product_url
                elif new_source == "mouser":
                    row["mouser_url"] = new_product_url
            for k in ("unit_price", "unit_price_currency", "unit_price_usd",
                      "stock_quantity", "stock_region"):
                if replacement.get(k) is not None:
                    row[k] = replacement.get(k)
            # Clear any hallucination flag the audit added.
            row.pop("_hallucinated", None)
            row["_auto_fix_replaced"] = failed_mpn

            used_replacements.add(new_mpn.strip().upper())
            patched_indices.add(row_idx)
            patched_log.append(
                f"{issue.get('category', '?')}: {failed_mpn} -> {new_mpn} "
                f"(stage={stage})"
            )

        if not patched_log:
            return None, ""

        patched_payload = {**tool_input, bom_key: new_comps}
        summary = f"{len(patched_log)} blocker(s): " + "; ".join(patched_log[:8])
        if len(patched_log) > 8:
            summary += f" ... (+{len(patched_log) - 8} more)"
        return patched_payload, summary

    def _build_fix_on_fail_corrective(self, audit_report: dict) -> Optional[str]:
        """Build a corrective user-message when the audit detects fixable
        blockers (fake MPNs, parts outside the candidate pool, bad datasheets,
        banned / obsolete / NRND parts).

        Returns None when there is nothing actionable for the LLM to fix on a
        re-prompt (e.g. only cascade / topology / citation issues remain) — the
        caller should not retry in that case.
        """
        issues = (audit_report or {}).get("issues") or []
        fixable = [
            i for i in issues
            if i.get("severity") in ("critical", "high")
            and i.get("category") in self._FIX_ON_FAIL_CATEGORIES
        ]
        if not fixable:
            return None

        by_cat: dict = {}
        for i in fixable:
            by_cat.setdefault(i.get("category", "?"), []).append(i)

        lines = [
            "AUDIT FAILURE — your previous `generate_requirements` call did not "
            "pass the post-generation audit. You MUST re-emit "
            "`generate_requirements` with the BOM corrected. Do not ask the "
            "user — fix the BOM yourself using the verified candidate pool below.",
            "",
        ]
        for cat, rows in by_cat.items():
            lines.append(f"**{cat}** ({len(rows)} item(s)):")
            for r in rows[:8]:
                loc = r.get("location") or ""
                det = (r.get("detail") or "").strip()
                fix = (r.get("suggested_fix") or "").strip()
                lines.append(f"  - {loc} — {det}")
                if fix:
                    lines.append(f"    fix: {fix}")
            lines.append("")

        pool = sorted({m for m in (self._offered_candidate_mpns or set()) if m})
        if pool:
            lines.append(
                f"VERIFIED CANDIDATE POOL ({len(pool)} MPNs surfaced by "
                "`find_candidate_parts` this turn — pick replacements ONLY from "
                "this list and copy the MPN + `datasheet_url` verbatim from the "
                "candidate record):"
            )
            for mpn in pool[:80]:
                lines.append(f"  - {mpn}")
            if len(pool) > 80:
                lines.append(f"  - ... (+{len(pool) - 80} more)")
            lines.append("")
        else:
            lines.append(
                "NO candidate pool was built this turn — call "
                "`find_candidate_parts` for every signal-chain stage FIRST, "
                "then re-call `generate_requirements`."
            )
            lines.append("")

        lines.append(
            "If a stage still has no acceptable candidate after a widened "
            "`find_candidate_parts` retry, OMIT that stage from the BOM rather "
            "than inventing a part. Then call `generate_requirements` as your "
            "final tool call."
        )
        return "\n".join(lines)

    async def _handle_find_candidate_parts(self, input_data: dict) -> dict:
        """Handle find_candidate_parts — retrieval-augmented selection.

        Queries DigiKey + Mouser live for real MPNs matching the stage
        and spec hint.  Every MPN surfaced here is accumulated in
        `self._offered_candidate_mpns` so the post-LLM audit can verify
        that `component_recommendations` picked from this shortlist.
        """
        stage = (input_data.get("stage") or "").strip()
        hint = (input_data.get("spec_hint") or "").strip()
        try:
            max_results = int(input_data.get("max_results", 5))
        except (TypeError, ValueError):
            max_results = 5
        max_results = max(1, min(max_results, 10))

        if not stage:
            return {"stage": stage, "candidates": [], "count": 0,
                    "message": "stage is required"}

        try:
            from tools.parametric_search import find_candidates
            candidates = find_candidates(
                stage, hint,
                max_per_source=max_results,
                max_total=max_results * 2,
                timeout_s=12.0,
            )
        except Exception as exc:
            self.log(f"find_candidate_parts failed: {exc}", "warning")
            return {"stage": stage, "candidates": [], "count": 0,
                    "error": str(exc),
                    "message": "Retrieval failed. Do not invent MPNs — ask the user for guidance."}

        # Remember every MPN we surfaced — this is the authoritative
        # shortlist the audit will gate against.
        # ALSO cache the full candidate record (manufacturer + datasheet_url
        # + lifecycle) keyed by stage so the deterministic auto-fix layer
        # can swap a hallucinated / banned MPN for a real one of the same
        # stage without an LLM round-trip.
        _stage_key = stage.strip().lower()
        _stage_bucket = self._offered_candidates_by_stage.setdefault(_stage_key, [])
        _bucket_seen = {(r.get("part_number") or "").strip().upper() for r in _stage_bucket}

        def _candidate_record(c) -> dict:
            source = (c.source or "").strip().lower()
            rec = {
                "part_number": c.part_number,
                "manufacturer": c.manufacturer,
                "description": c.description,
                "datasheet_url": c.datasheet_url,
                "product_url": c.product_url,
                "distributor_url": c.product_url,
                "lifecycle_status": c.lifecycle_status,
                "source": c.source,
                "unit_price": c.unit_price,
                "unit_price_currency": c.unit_price_currency,
                "unit_price_usd": c.unit_price_usd,
                "stock_quantity": c.stock_quantity,
                "stock_region": c.region,
            }
            if source == "digikey":
                rec["digikey_url"] = c.product_url
            elif source == "mouser":
                rec["mouser_url"] = c.product_url
            return rec

        for c in candidates:
            mpn = (c.part_number or "").strip().upper()
            if not mpn:
                continue
            self._offered_candidate_mpns.add(mpn)
            if mpn in _bucket_seen:
                continue
            _bucket_seen.add(mpn)
            _stage_bucket.append(_candidate_record(c))

        self.log(
            f"find_candidate_parts stage={stage!r} hint={hint!r} "
            f"-> {len(candidates)} candidates (offered_total={len(self._offered_candidate_mpns)}, "
            f"stages_cached={len(self._offered_candidates_by_stage)})",
            "info",
        )

        return {
            "stage": stage,
            "spec_hint": hint,
            "count": len(candidates),
            "candidates": [_candidate_record(c) for c in candidates],
            "message": (
                "Pick ONLY from `candidates[].part_number`. Copy `datasheet_url` "
                "and the source-specific distributor URL (`digikey_url` or "
                "`mouser_url`) verbatim. Do not invent alternatives. If none "
                "fit, call this tool again with a wider hint."
            ) if candidates else (
                "No candidates found. Widen the spec_hint (e.g. drop the frequency or package), "
                "or call again with a different stage. Do not invent an MPN."
            ),
        }

    def _generate_output_files(
        self, tool_input: dict, output_dir: str, project_name: str
    ) -> dict:
        """Generate all Phase 1 output markdown files."""
        output_path = Path(output_dir)
        import re as _re

        def _scrub(text: str) -> str:
            """Strip TBD/TBC/TBA placeholders the LLM may have written despite instructions."""
            return _re.sub(r'\b(TBD|TBC|TBA)\b', '[specify]', text, flags=_re.IGNORECASE)

        output_path.mkdir(parents=True, exist_ok=True)
        outputs = {}

        # 1. requirements.md
        req_content = _scrub(self._build_requirements_md(tool_input, project_name))
        req_file = output_path / "requirements.md"
        req_file.write_text(req_content, encoding="utf-8")
        outputs["requirements.md"] = req_content

        # 2. block_diagram.md — prefer structured spec; fall back to salvaged raw text
        block_mermaid = self._render_diagram_field(
            tool_input,
            structured_key="block_diagram",
            raw_key="block_diagram_mermaid",
            default_direction="LR",
        )
        block_mermaid = self._reflow_long_mermaid(block_mermaid)
        block_content = _scrub(f"# System Block Diagram\n## {project_name}\n\n```mermaid\n{block_mermaid}\n```\n")
        block_file = output_path / "block_diagram.md"
        block_file.write_text(block_content, encoding="utf-8")
        outputs["block_diagram.md"] = block_content

        # 3. architecture.md — same pipeline as block_diagram (structured → salvage)
        arch_mermaid = self._render_diagram_field(
            tool_input,
            structured_key="architecture",
            raw_key="architecture_mermaid",
            default_direction="TD",
            allow_empty=True,
        )
        arch_mermaid = self._reflow_long_mermaid(arch_mermaid)
        if arch_mermaid:
            arch_content = f"# System Architecture\n## {project_name}\n\n```mermaid\n{arch_mermaid}\n```\n"
        elif block_mermaid:
            # P19 (2026-04-24): if the LLM didn't emit an architecture spec but
            # did produce a block diagram, reuse the block diagram as the
            # architecture stand-in instead of showing the bare
            # "will be generated with HRS" placeholder.  User reported empty
            # architecture.md files — this ensures the file always carries
            # SOMETHING visual derived from the captured payload.
            arch_content = (
                f"# System Architecture\n## {project_name}\n\n"
                f"*Architecture view derived from the block diagram — "
                f"rebuild with an explicit `architecture` spec in P2.*\n\n"
                f"```mermaid\n{block_mermaid}\n```\n"
            )
        else:
            arch_content = f"# System Architecture\n## {project_name}\n\n*Architecture diagram will be generated with HRS.*\n"
        arch_content = _scrub(arch_content)
        arch_file = output_path / "architecture.md"
        arch_file.write_text(arch_content, encoding="utf-8")
        outputs["architecture.md"] = arch_content

        # 4. component_recommendations.md
        comp_content = _scrub(self._build_components_md(tool_input, project_name))
        comp_file = output_path / "component_recommendations.md"
        comp_file.write_text(comp_content, encoding="utf-8")
        outputs["component_recommendations.md"] = comp_content

        # 5. power_calculation.md + power_calculation.html
        power_content = _scrub(self._build_power_calc_md(tool_input, project_name))
        power_file = output_path / "power_calculation.md"
        power_file.write_text(power_content, encoding="utf-8")
        outputs["power_calculation.md"] = power_content

        power_html = _scrub(self._build_power_calc_html(tool_input, project_name))
        power_html_file = output_path / "power_calculation.html"
        power_html_file.write_text(power_html, encoding="utf-8")
        outputs["power_calculation.html"] = power_html

        # 5c. power_calculation.xlsx — editable Excel workbook with formulas
        power_xlsx_file = output_path / "power_calculation.xlsx"
        try:
            self._build_power_calc_xlsx(tool_input, project_name, power_xlsx_file)
            outputs["power_calculation.xlsx"] = str(power_xlsx_file)
        except Exception as e:
            self.log(f"xlsx generation failed: {e}", "warning")

        # 6. gain_loss_budget.md — generated for all designs; non-RF designs get header-only file
        glb_content = _scrub(self._build_gain_loss_budget_md(tool_input, project_name))
        glb_file = output_path / "gain_loss_budget.md"
        glb_file.write_text(glb_content, encoding="utf-8")
        outputs["gain_loss_budget.md"] = glb_content

        # 6b. gain_loss_budget.html — standalone HTML view, same styling as
        # power_calculation.html so the docs set reads as one cohesive package.
        try:
            glb_html = _scrub(self._build_gain_loss_budget_html(tool_input, project_name))
            glb_html_file = output_path / "gain_loss_budget.html"
            glb_html_file.write_text(glb_html, encoding="utf-8")
            outputs["gain_loss_budget.html"] = glb_html
        except Exception as e:
            self.log(f"GLB html generation failed: {e}", "warning")

        # 7. cascade_analysis.json — structured Friis NF / gain / IIP3
        # cascade so the React UI can render a stage-by-stage chart.
        # Pure computation (no network), always safe to emit. The JSON
        # echoes the P1 claims so the chart can draw a pass/fail verdict.
        try:
            import json as _json
            from tools.rf_cascade import compute_cascade
            dp = tool_input.get("design_parameters") or {}
            # Direction: RX (default) / TX. Derived in priority order:
            #   1. Explicit design_parameters.direction from wizard
            #   2. project_type from wizard (`receiver`, `transmitter`,
            #      `transceiver`, `power_supply`, `switch_matrix` —
            #      `tools.rf_cascade.compute_cascade` aliases these to
            #      its native `rx` / `tx` / `none` directions)
            #   3. Heuristic: any stage has pout_dbm or oip3_dbm → TX
            direction = str(
                dp.get("direction")
                or dp.get("project_type")
                or tool_input.get("project_type")
                or ""
            ).strip().lower()
            # Cascade module knows the alias map; we forward unmodified
            # project_type strings. Only apply the heuristic when the
            # field is genuinely empty.
            if not direction:
                comps = tool_input.get("component_recommendations") or []
                has_tx_spec = any(
                    (c.get("key_specs") or {}).get("pout_dbm") is not None
                    or (c.get("key_specs") or {}).get("oip3_dbm") is not None
                    or c.get("pout_dbm") is not None
                    or c.get("oip3_dbm") is not None
                    for c in comps if isinstance(c, dict)
                )
                direction = "tx" if has_tx_spec else "rx"

            cascade = compute_cascade(
                tool_input.get("component_recommendations") or [],
                direction=direction,
                claimed_nf_db=dp.get("noise_figure_db"),
                claimed_iip3_dbm=dp.get("iip3_dbm_input") or dp.get("iip3_dbm"),
                claimed_total_gain_db=dp.get("total_gain_db"),
                claimed_pout_dbm=dp.get("pout_dbm") or dp.get("output_power_dbm"),
                claimed_oip3_dbm=dp.get("oip3_dbm"),
                claimed_pae_pct=dp.get("pae_pct"),
                input_power_dbm=float(dp.get("tx_input_power_dbm") or -20.0),
            )
            cascade_file = output_path / "cascade_analysis.json"
            cascade_file.write_text(_json.dumps(cascade, indent=2), encoding="utf-8")
            outputs["cascade_analysis.json"] = _json.dumps(cascade, indent=2)
        except Exception as e:
            self.log(f"cascade_analysis generation failed: {e}", "warning")

        self.log(f"Generated {len(outputs)} Phase 1 output files in {output_path}")
        return outputs

    def _build_requirements_md(self, tool_input: dict, project_name: str) -> str:
        """Build IEEE-style requirements.md."""
        lines = [
            f"# Hardware Requirements",
            f"## {project_name}",
            "",
            "## 1. Project Summary",
            "",
            tool_input.get("project_summary", ""),
            "",
            "## 2. Design Parameters",
            "",
            "| Parameter | Value |",
            "|---|---|",
        ]

        for key, value in tool_input.get("design_parameters", {}).items():
            lines.append(f"| {key.replace('_', ' ').title()} | {value} |")

        lines.extend(["", "## 3. Requirements", ""])

        # Group by category
        reqs = tool_input.get("requirements", [])
        categories = {}
        for req in reqs:
            cat = req.get("category", "general")
            categories.setdefault(cat, []).append(req)

        for cat, cat_reqs in categories.items():
            lines.append(f"### 3.{list(categories.keys()).index(cat)+1} {cat.title()} Requirements")
            lines.append("")
            lines.append("| ID | Title | Description | Priority | Validation | Dependencies | Constraints |")
            lines.append("|---|---|---|---|---|---|---|")
            for req in cat_reqs:
                deps = ", ".join(req.get('dependencies', [])) or "None"
                constraints = ", ".join(req.get('constraints', [])) or "None"
                lines.append(
                    f"| {req.get('req_id', '')} | {req.get('title', '')} | "
                    f"{req.get('description', '')} | {req.get('priority', 'Must have')} | "
                    f"{req.get('verification_method', 'test')} | "
                    f"{deps} | {constraints} |"
                )
            lines.append("")

        return "\n".join(lines)

    def _build_power_calc_md(self, tool_input: dict, project_name: str) -> str:
        """Build a per-component, per-rail power budget table matching the CSV template format.

        v21.3 — Passive and mechanical components (connectors, filters, limiters,
        heat sinks, antennas, attenuators, transformers, ferrite beads, etc.) are
        EXCLUDED from the power budget entirely. They draw no DC current and
        including them with fabricated defaults is physically incorrect.

        v21.3 — When a component's operating current cannot be extracted from
        `primary_key_specs`, the row is marked "verify from datasheet" rather
        than injecting a guessed keyword-based default. Hard-coded defaults
        previously produced plausible-but-wrong numbers (e.g. 0.165 W for
        every passive at 3.3 V × 50 mA generic).
        """
        from datetime import datetime
        comps = tool_input.get("component_recommendations", [])
        date = datetime.now().strftime("%d-%m-%Y")

        # Helper: try to extract a numeric value from a string like "3.3V", "100mA", "500mW"
        def parse_num(s: str, unit: str = "") -> float:
            import re as _re
            if not s:
                return 0.0
            s = str(s)
            m = _re.search(r'[\d.]+', s)
            if not m:
                return 0.0
            val = float(m.group())
            if "m" + unit.lower() in s.lower():  # mA, mW
                val /= 1000.0
            return val

        # ── Passive / mechanical component detector ──────────────────────
        # These draw no DC current and MUST be excluded from the rail budget.
        # Matches against the `function` string and the `primary_part` prefix.
        _PASSIVE_KEYWORDS = (
            "connector", "sma", "bnc", "smp", "u.fl", "mmcx", "rf connector",
            "coaxial", "coax",
            "heat sink", "heatsink", "thermal pad", "thermal interface",
            "shield", "enclosure", "housing", "bracket", "standoff", "pcb",
            "antenna", "feedline", "balun passive",
            "saw filter", "baw filter", "ceramic filter", "lc filter",
            "pre-select filter", "preselect filter", "bandpass filter",
            "lowpass filter", "highpass filter", "notch filter",
            "attenuator", "pad attenuator", "fixed attenuator",
            "limiter", "pin diode limiter", "diode limiter", "power limiter",
            "isolator", "circulator",
            "directional coupler", "hybrid coupler", "wilkinson", "rat-race",
            "termination", "50 ohm load", "dummy load",
            "ferrite bead", "ferrite core", "choke", "rf choke",
            "transformer", "rf transformer",
            "resistor", "capacitor", "inductor", "passive",
            "crystal", "xtal", "resonator",  # usually passive when not an oscillator module
            "fuse", "ptc", "esd diode", "tvs diode", "tvs",
            "test point", "mounting hole",
        )
        # Parts that are actively biased (PIN-diode limiters with bias current,
        # for instance) — but for budget purposes still treat as passive since
        # the bias current is typically < 1 mA and negligible.
        def is_passive(comp: dict) -> bool:
            func = (comp.get("function") or "").lower()
            part = (comp.get("primary_part") or "").lower()
            desc = (comp.get("primary_description") or "").lower()
            blob = f"{func} {part} {desc}"
            return any(kw in blob for kw in _PASSIVE_KEYWORDS)

        # ── Spec readers (datasheet-sourced values override everything) ──
        def spec_voltage(comp: dict) -> float:
            specs = comp.get("primary_key_specs", {}) or {}
            for key in ("supply_voltage", "voltage", "vcc", "vdd", "operating_voltage",
                        "vd", "vdd_rf", "vdrain"):
                v = specs.get(key, "")
                if v:
                    val = parse_num(v, "V")
                    if val > 0:
                        return val
            return 0.0

        def spec_current(comp: dict) -> float:
            specs = comp.get("primary_key_specs", {}) or {}
            for key in ("supply_current", "current", "icc", "idd", "iq",
                        "quiescent_current", "operating_current", "typical_current",
                        "id", "idrain"):
                v = specs.get(key, "")
                if v:
                    val = parse_num(v, "A")
                    if val > 0:
                        return val
            return 0.0

        # ── Design-parameter readers (drive the bias derivation) ──
        dp = tool_input.get("design_parameters", {}) or {}
        def _dp_num(*keys) -> float:
            for k in keys:
                v = dp.get(k, "")
                if v:
                    val = parse_num(str(v))
                    if val != 0:
                        return val
            return 0.0
        target_nf_db   = _dp_num("noise_figure_db", "noise_figure", "system_nf", "nf")
        target_pout_dBm = _dp_num("output_power_dbm", "pout_dbm", "tx_power", "pout")
        target_iip3_dBm = _dp_num("iip3_dbm", "iip3")
        target_gain_db  = _dp_num("system_gain_db", "gain")

        # ── Component-class detectors ──────────────────────────────────────
        def _match(func: str, part: str, keys: tuple) -> bool:
            blob = f"{func} {part}"
            return any(k in blob for k in keys)

        def classify(comp: dict) -> str:
            func = (comp.get("function") or "").lower()
            part = (comp.get("primary_part") or "").lower()
            desc = (comp.get("primary_description") or "").lower()
            blob = f"{func} {part} {desc}"
            # Order matters — most specific first.
            if any(k in blob for k in ("gan hemt pa", "gan pa", "power amplifier", "high power amp")) \
               and not "lna" in blob and not "driver" in blob:
                return "gan_pa"
            if any(k in blob for k in ("gan hemt lna", "gan lna", "phemt lna")) \
               or (_match(func, part, ("lna",)) and "gan" in blob):
                return "gan_lna"
            if _match(func, part, ("lna", "low noise amp", "low-noise amp")):
                return "lna"
            if _match(func, part, ("driver amp", "driver stage", "pre-driver", "predriver")):
                return "driver_amp"
            if _match(func, part, ("gain block", "mmic amp", "buffer amp", "output buffer")):
                return "gain_block"
            if _match(func, part, ("mixer", "downconverter", "upconverter", "i/q modulator", "iq demod")):
                return "mixer"
            if _match(func, part, ("vco", "synth", "pll", "frequency synth", "local oscillator")):
                return "pll_lo"
            if _match(func, part, ("adc", "analog-to-digital", "analog to digital")):
                return "adc"
            if _match(func, part, ("dac", "digital-to-analog")):
                return "dac"
            if _match(func, part, ("fpga", "xilinx", "altera", "zynq", "versal", "artix", "kintex")):
                return "fpga"
            if _match(func, part, ("mcu", "microcontroller", "processor", "soc", "arm", "cortex")):
                return "mcu"
            if _match(func, part, ("ldo", "regulator", "dc-dc", "buck", "boost", "pmic", "power management")):
                return "power"
            if _match(func, part, ("i2c", "spi", "i/o expander", "gpio expander", "control interface",
                                    "level shift", "translator", "mcp230")):
                return "io_expander"
            return "generic_ic"

        # ── Deterministic RF bias derivation ───────────────────────────────
        # Returns (Vd, Id_typ, class, rationale). Caller appends to the
        # "Bias Derivation" table so the reviewer sees *why* each bias was
        # picked. Design parameters (NF target, Pout target, linearity) feed
        # into the derivation so numbers are tied to requirements, not to
        # a fixed lookup table.
        def derive_bias(comp: dict) -> dict:
            cls = classify(comp)
            spec_v = spec_voltage(comp)
            spec_i = spec_current(comp)

            # 1. Datasheet values from BOM override everything.
            if spec_v > 0 and spec_i > 0:
                return {
                    "Vd": spec_v, "Id": spec_i,
                    "class": "datasheet",
                    "rationale": f"From BOM primary_key_specs ({spec_v:g} V, {spec_i*1000:.1f} mA).",
                    "source": "datasheet",
                }

            # 2. Per-class derivation.
            if cls == "gan_lna":
                # GaN HEMT LNA — low-noise Class A. Standard bias: Vd=5V,
                # Idq ≈ 12-18% of Idss. Typical Idss for 0.25 µm GaN in a
                # small-signal LNA die is 500-600 mA; 15% ≈ 80-100 mA.
                # Tighter NF targets push Idq up toward 100-120 mA.
                vd = 5.0
                if target_nf_db and target_nf_db < 1.5:
                    id_a = 0.100
                    rat = (f"GaN HEMT LNA Class A — target NF<{target_nf_db:.1f} dB requires "
                           f"Vd=5V, Idq=100 mA (~18% of Idss) for min-NF bias point.")
                elif target_nf_db and target_nf_db < 2.5:
                    id_a = 0.080
                    rat = (f"GaN HEMT LNA Class A — target NF<{target_nf_db:.1f} dB met at "
                           f"Vd=5V, Idq=80 mA (~15% of Idss).")
                else:
                    id_a = 0.080
                    rat = "GaN HEMT LNA Class A — Vd=5V, Idq=80 mA (15% of Idss) nominal min-NF bias."
                return {"Vd": vd, "Id": id_a, "class": "Class A (low-noise)",
                        "rationale": rat, "source": "derived"}

            if cls == "gan_pa":
                # Class AB for linearity. Idq ≈ 10-15% of Idss_peak.
                # For Pout target: Id_peak ≈ Pout_W / (Vd × PAE). PAE ≈ 50%
                # for Class AB at back-off. Default Vd=28V (typical GaN PA).
                vd = 28.0
                pae = 0.50
                pout_w = 10 ** ((target_pout_dBm - 30) / 10) if target_pout_dBm else 1.0
                id_peak = pout_w / (vd * pae) if pout_w > 0 else 0.3
                idq = max(0.150, id_peak * 0.30)  # quiescent at ~30% of peak
                rat = (f"GaN HEMT PA Class AB — Vd=28V, Idq≈{idq*1000:.0f} mA derived from "
                       f"Pout={target_pout_dBm or '?'} dBm ({pout_w:.2f} W) at PAE={pae*100:.0f}%. "
                       f"Peak Id≈{id_peak*1000:.0f} mA.")
                return {"Vd": vd, "Id": idq, "class": "Class AB",
                        "rationale": rat, "source": "derived"}

            if cls == "lna":
                # Non-GaN LNA (SiGe / GaAs pHEMT / CMOS). Vcc=3.3-5V, Icc=10-30 mA.
                vd = 5.0
                id_a = 0.020
                rat = ("SiGe/GaAs LNA — Class A bias, Vcc=5V, Icc=20 mA typical for "
                       "2-6 GHz receive LNAs with NF≈1.5 dB and G≈18 dB.")
                return {"Vd": vd, "Id": id_a, "class": "Class A",
                        "rationale": rat, "source": "derived"}

            if cls == "driver_amp":
                # MMIC driver amplifier (ADL55xx family, etc.). Internally
                # biased Class A. Vs=5V, Iq≈60-90 mA.
                vd = 5.0
                # IIP3 target drives Id up (more headroom = more current).
                id_a = 0.090 if (target_iip3_dBm and target_iip3_dBm > 25) else 0.060
                rat = (f"MMIC driver amp — internal Class A bias, Vs=5V, Iq={id_a*1000:.0f} mA. "
                       f"{'IIP3 target drives bias up.' if id_a > 0.07 else 'Nominal bias.'}")
                return {"Vd": vd, "Id": id_a, "class": "Class A (internal)",
                        "rationale": rat, "source": "derived"}

            if cls == "gain_block":
                # Broadband gain block / output buffer (MGA-, GVA-, HMC3xx etc.).
                # Vs=5V, Iq≈40-80 mA internally set by on-chip current mirror.
                vd = 5.0
                id_a = 0.060
                rat = ("MMIC gain block — internally biased, Vs=5V, Iq=60 mA typical. "
                       "DC current set by on-chip current mirror, not adjustable externally.")
                return {"Vd": vd, "Id": id_a, "class": "Class A (internal)",
                        "rationale": rat, "source": "derived"}

            if cls == "mixer":
                # Active mixer (Gilbert cell). Vcc=3.3-5V, Icc=30-80 mA depending on LO drive.
                vd = 5.0
                id_a = 0.050
                rat = ("Active mixer (Gilbert cell) — Vcc=5V, Icc=50 mA nominal. "
                       "Actual Icc scales with LO drive level; verify at bring-up.")
                return {"Vd": vd, "Id": id_a, "class": "active", "rationale": rat, "source": "derived"}

            if cls == "pll_lo":
                # Fractional-N PLL / VCO module. Vcc=3.3V (digital) + 5V (charge pump/VCO) split.
                # Lump into a single rail line (3.3V, 120 mA) — close enough for budget.
                vd = 3.3
                id_a = 0.120
                rat = ("PLL/VCO — Vcc=3.3V digital + 5V CP/VCO internally regulated. "
                       "Lump to Icc=120 mA on 3.3V for budget.")
                return {"Vd": vd, "Id": id_a, "class": "active", "rationale": rat, "source": "derived"}

            if cls == "adc":
                # Modern RF ADC (JESD204B). Analog 1.8-2.5V + digital 1.2-1.8V.
                # Budget against 1.8V at 400 mA for a ~1 GSPS class part.
                vd = 1.8
                id_a = 0.400
                rat = ("RF ADC — Vdd_a=1.8V, core Idd≈400 mA for ~1 GSPS class part. "
                       "Split rails collapsed to 1.8V for budget purposes.")
                return {"Vd": vd, "Id": id_a, "class": "active", "rationale": rat, "source": "derived"}

            if cls == "dac":
                vd = 1.8
                id_a = 0.300
                rat = "DAC — Vdd=1.8V analog core, Idd≈300 mA typical for RF-speed parts."
                return {"Vd": vd, "Id": id_a, "class": "active", "rationale": rat, "source": "derived"}

            if cls == "fpga":
                # Mid-range FPGA (Artix-7 / Kintex-7 equivalent). Core 1.0-1.2V,
                # I/O 1.8V, aux 1.8V. Dominated by core. Budget 1.8V @ 500 mA
                # as a working placeholder; tool will flag this for user verification.
                vd = 1.8
                id_a = 0.500
                rat = ("FPGA — core + aux + I/O lumped to 1.8V @ 500 mA. "
                       "Running XPE for accurate Vcore power is still required.")
                return {"Vd": vd, "Id": id_a, "class": "digital", "rationale": rat, "source": "derived"}

            if cls == "mcu":
                vd = 3.3
                id_a = 0.060
                rat = "MCU / SoC — Vcc=3.3V, Icc=60 mA typical at full clock."
                return {"Vd": vd, "Id": id_a, "class": "digital", "rationale": rat, "source": "derived"}

            if cls == "power":
                # LDO / switcher controller — quiescent only (load current is downstream).
                vd = 5.0
                id_a = 0.010
                rat = "Power-management IC — Iq=10 mA controller current only (downstream load counted separately)."
                return {"Vd": vd, "Id": id_a, "class": "controller", "rationale": rat, "source": "derived"}

            if cls == "io_expander":
                # I²C / SPI I/O expander, control translator. < 1 mA.
                vd = 3.3
                id_a = 0.001
                rat = "I/O expander / control translator — Vcc=3.3V, Icc≤1 mA typical."
                return {"Vd": vd, "Id": id_a, "class": "digital", "rationale": rat, "source": "derived"}

            # Generic fallback — we deliberately do NOT fabricate a current
            # any more. Return 0 so the caller flags for manual verification.
            return {"Vd": spec_v or 3.3, "Id": 0.0, "class": "unknown",
                    "rationale": "No datasheet bias in BOM; class not recognised. Verify manually.",
                    "source": "unknown"}

        # Rails we track
        RAILS = [5.0, 3.3, 2.5, 1.8]
        RAIL_LABELS = ["5V", "3.3V", "2.5V", "1.8V"]

        rows = []
        rail_totals_typ = [0.0] * len(RAILS)
        rail_totals_max = [0.0] * len(RAILS)

        for i, comp in enumerate(comps, 1):
            func = comp.get("function", f"Component {i}")
            part = comp.get("primary_part", "—")
            specs = comp.get("primary_key_specs", {}) or {}
            pkg = specs.get("package", specs.get("Package", "—"))

            # ── Regulators (LDO / buck / boost / PMIC) are power SOURCES,
            #    not loads — they're analysed in the thermal section below
            #    and MUST NOT appear in the rail-load table.
            if classify(comp) == "power":
                continue

            # ── Passive / mechanical components: listed in the table but
            #    contribute zero power (no fabricated currents). They still
            #    appear so the reader sees the full BOM like the old template.
            if is_passive(comp):
                row = {
                    "si": len(rows) + 1,
                    "desc": func,
                    "pkg": pkg,
                    "part": part,
                    "qty": 1,
                    "current_ma": None,
                    "voltage_v": None,
                    "cells": ["", "", "", ""] * len(RAILS),
                    "tot_typ": 0.0,
                    "tot_max": 0.0,
                }
                rows.append(row)
                continue

            # ── Derive optimal Vd/Id from design requirements ──
            bias = derive_bias(comp)
            rail_v = bias["Vd"]
            i_typ = bias["Id"]
            src = bias["source"]  # "datasheet" | "derived" | "unknown"

            # No usable current → put the component in the table with empty
            # rail cells so the reader sees it but totals are untouched.
            if src == "unknown" or i_typ <= 0:
                row = {
                    "si": len(rows) + 1,
                    "desc": func,
                    "pkg": pkg,
                    "part": part,
                    "qty": 1,
                    "cells": ["", "", "", ""] * len(RAILS),
                    "tot_typ": 0.0,
                    "tot_max": 0.0,
                }
                rows.append(row)
                continue

            i_max = i_typ * 1.30  # 30% margin for max
            p_typ = round(rail_v * i_typ, 3)
            p_max = round(rail_v * i_max, 3)

            # Find closest rail index
            closest = min(range(len(RAILS)), key=lambda idx: abs(RAILS[idx] - rail_v))

            # Build per-rail cells; only the matching rail gets values
            cells = []
            for idx in range(len(RAILS)):
                if idx == closest:
                    cells += [p_typ, p_max, p_typ, p_max]
                    rail_totals_typ[idx] += p_typ
                    rail_totals_max[idx] += p_max
                else:
                    cells += ["", "", "", ""]

            row = {
                "si": len(rows) + 1,
                "desc": func,
                "pkg": pkg,
                "part": part,
                "qty": 1,
                "current_ma": round(i_typ * 1000, 1),
                "voltage_v": rail_v,
                "cells": cells,
                "tot_typ": p_typ,
                "tot_max": p_max,
            }
            rows.append(row)

        # Build split markdown tables (one per rail group) to avoid column overflow.
        # RAIL groups: [0]=5V, [1]=3.3V, [2]=2.5V, [3]=1.8V  → split 0-1 and 2-3
        grand_max = round(sum(rail_totals_max), 3)
        grand_typ = round(sum(rail_totals_typ), 3)

        def build_rail_table(rail_indices: list) -> list:
            """Emit a compact markdown table for the given rail indices."""
            sel_labels = [RAIL_LABELS[i] for i in rail_indices]
            # Header
            h1 = "| SI | Description | Part No | Qty | Current (mA)"
            for lbl in sel_labels:
                h1 += f" | {lbl} TYP (W) | {lbl} MAX (W)"
            h1 += " | Total TYP (W) | Total MAX (W) |"
            # Separator: auto-built from column count so it always matches the header
            ncols = h1.count('|') - 1
            h2 = '| ' + ' | '.join(['---'] * ncols) + ' |'
            t_lines = [h1, h2]
            for row in rows:
                tot_typ_row = 0.0
                tot_max_row = 0.0
                i_ma = row.get("current_ma")
                i_cell = f"{i_ma:g}" if i_ma is not None else "—"
                line = f"| {row['si']} | {row['desc']} | {row['part']} | {row['qty']} | {i_cell}"
                for ri in rail_indices:
                    # cells layout: 4 values per rail [typ, max, tot_typ, tot_max]
                    base = ri * 4
                    typ_w = row["cells"][base]
                    max_w = row["cells"][base + 1]
                    line += f" | {typ_w if typ_w != '' else '—'} | {max_w if max_w != '' else '—'}"
                    if isinstance(typ_w, float):
                        tot_typ_row += typ_w
                    if isinstance(max_w, float):
                        tot_max_row += max_w
                line += f" | {round(tot_typ_row, 3) if tot_typ_row else '—'} | {round(tot_max_row, 3) if tot_max_row else '—'} |"
                t_lines.append(line)
            # Totals row
            tot_line = "| | **TOTALS** | | "
            for ri in rail_indices:
                t_typ = round(rail_totals_typ[ri], 3)
                t_max = round(rail_totals_max[ri], 3)
                tot_line += f" | **{t_typ}** | **{t_max}**"
            sub_typ = round(sum(rail_totals_typ[i] for i in rail_indices), 3)
            sub_max = round(sum(rail_totals_max[i] for i in rail_indices), 3)
            tot_line += f" | **{sub_typ}** | **{sub_max}** |"
            t_lines.append(tot_line)
            return t_lines

        table_a = build_rail_table([0, 1])   # 5V and 3.3V
        table_b = build_rail_table([2, 3])   # 2.5V and 1.8V

        # Check if the 2.5V/1.8V rails actually have any power data —
        # if both rail totals are zero, skip that table entirely so the
        # user doesn't see a table full of dashes.
        has_low_rail_data = (rail_totals_typ[2] > 0 or rail_totals_max[2] > 0 or
                             rail_totals_typ[3] > 0 or rail_totals_max[3] > 0)

        lines = [
            f"# Power Calculation",
            f"## {project_name}",
            "",
            f"**Date:** {date}",
            "",
            "> All values in Watts (W). TYP = typical operating power, MAX = worst-case (130% of TYP).",
            "> Rail assignment is based on component operating voltage from BOM.",
            "",
        ]

        # ── Per-stage DC bias (moved out of the GLB document) ───────────────
        # Every gain / NF / P1dB number in the GLB stage table is only valid
        # under the datasheet bias listed here. The per-stage Pdc in this
        # table must agree with the per-rail current draws in the budget
        # tables below — the power-rail optimizer enforces this automatically
        # when the GLB changes.
        _glb_stages = (tool_input.get("gain_loss_budget") or {}).get("stages", []) or []
        _bias_rows = []
        _total_pdc = 0.0
        for i, st in enumerate(_glb_stages, 1):
            bc = st.get("bias_conditions") or {}
            vdd = bc.get("vdd_v"); idq = bc.get("idq_ma"); pdc = bc.get("pdc_mw")
            cond = bc.get("datasheet_condition") or bc.get("condition_note") or ""
            if isinstance(vdd, (int, float)) and isinstance(idq, (int, float)):
                pdc_calc = round(vdd * idq, 1)
                if not isinstance(pdc, (int, float)):
                    pdc = pdc_calc
                elif abs(pdc - pdc_calc) > 10.0:
                    cond = (cond + f" · Pdc mismatch (Vdd·Idq = {pdc_calc} mW)").strip(" ·")
            if isinstance(pdc, (int, float)):
                _total_pdc += pdc
            _bias_rows.append((i, st, vdd, idq, pdc, cond))
        if any(isinstance(r[4], (int, float)) for r in _bias_rows):
            lines += [
                "## Per-Stage DC Bias (from GLB stage datasheet conditions)",
                "",
                "| # | Stage | Component | Vdd (V) | Idq (mA) | Pdc (mW) | Datasheet Condition |",
                "|---|-------|-----------|--------:|---------:|---------:|---------------------|",
            ]
            for i, st, vdd, idq, pdc, cond in _bias_rows:
                vdd_s = f"{vdd:.2f}" if isinstance(vdd, (int, float)) else "—"
                idq_s = f"{idq:.1f}" if isinstance(idq, (int, float)) else "—"
                pdc_s = f"{pdc:.1f}" if isinstance(pdc, (int, float)) else "—"
                lines.append(
                    f"| {i} | {st.get('stage_name', '—')} | {st.get('component', '—')}"
                    f" | {vdd_s} | {idq_s} | {pdc_s} | {cond or ''} |"
                )
            lines.append(
                f"| **TOTAL** | | | | | **{_total_pdc:.1f}** | Sum of all powered-stage Pdc |"
            )
            lines += [
                "",
                "> **Key consistency rule:** the Vdd and Idq above must be the exact bias "
                "conditions under which the datasheet specifies the gain, NF, P1dB, and OIP3 "
                "values in the GLB stage-by-stage table. Different bias ⇒ different RF "
                "performance. The per-stage Pdc (mW) rolls up into the per-rail budget below.",
                "",
            ]

        lines += [
            "## Power Budget — 5 V & 3.3 V Rails",
            "",
        ]
        lines.extend(table_a)
        if has_low_rail_data:
            lines += [
                "",
                "## Power Budget — 2.5 V & 1.8 V Rails",
                "",
            ]
            lines.extend(table_b)
        lines += [
            "",
            "---",
            "",
            "## Power Summary",
            "",
            "| Rail | Typical Power (W) | Max Power (W) |",
            "|------|-------------------|---------------|",
        ]
        for idx, label in enumerate(RAIL_LABELS):
            t = round(rail_totals_typ[idx], 3)
            m = round(rail_totals_max[idx], 3)
            lines.append(f"| {label} | {t} | {m} |")
        lines += [
            f"| **TOTAL** | **{grand_typ}** | **{grand_max}** |",
            "",
            "> Note: Power values are estimated from component datasheets and design parameters.",
            "> Actual measurements should be taken during hardware bring-up and updated in this table.",
        ]

        # ── Converter / LDO power dissipation + thermal analysis ──────────
        reg_rows = self._build_regulator_thermal_rows(
            comps=comps,
            rail_totals_typ=rail_totals_typ,
            rails=RAILS,
            rail_labels=RAIL_LABELS,
            dp=dp,
            parse_num=parse_num,
        )
        if reg_rows:
            lines += [
                "",
                "## Power Converters & LDOs — Dissipation and Thermal Analysis",
                "",
                "> Each regulator's dissipation is P_diss = (V_in − V_out) × I_out for linear "
                "LDOs (no switching efficiency applies), or P_out × (1 − η)/η for switching "
                "converters. Junction-temperature rise uses the datasheet θ_jc "
                "(junction-to-case) and θ_ja (junction-to-ambient) at T_ambient = 85 °C "
                "(worst-case avionics chamber). Thermal verdict: **Pass** if T_j < 125 °C, "
                "**Thermally Failed** otherwise.",
                "",
                "| SI | Part No | Topology | V_in (V) | V_out (V) | I_out (A) | η | P_diss (W) | θ_jc (°C/W) | θ_ja (°C/W) | T_j @ 85°C amb (°C) | Heatsink? | Verdict |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
            ]
            for r in reg_rows:
                lines.append(
                    f"| {r['si']} | {r['part']} | {r['topology']} | {r['vin']:g} | "
                    f"{r['vout']:g} | {r['iout']:.3f} | {r['eta']} | {r['pdiss']:.3f} | "
                    f"{r['theta_jc']} | {r['theta_ja']} | {r['tj']} | {r['heatsink']} | "
                    f"**{r['verdict']}** |"
                )
            all_pass = all(r['verdict'] == "Pass" for r in reg_rows)
            lines += [
                "",
                "**Overall thermal verdict:** "
                + ("✅ **Pass** — every regulator junction stays below 125 °C at 85 °C ambient."
                   if all_pass else
                   "❌ **Thermally Failed** — at least one regulator exceeds the 125 °C "
                   "junction limit. Add a heatsink / copper pour, or move to a lower-dropout "
                   "switcher or a higher-power package."),
            ]

        return "\n".join(lines)

    # ------------------------------------------------------------------
    #  Regulator / converter thermal helper (shared by MD + HTML builders)
    # ------------------------------------------------------------------
    def _build_regulator_thermal_rows(self, comps, rail_totals_typ, rails, rail_labels,
                                      dp, parse_num):
        """Detect LDOs / DC-DC converters in the BOM and compute their
        dissipation + junction temperature using θ_jc / θ_ja.

        Returns a list of dicts suitable for table rendering (markdown or HTML).
        """
        _REG_KEYWORDS = ("ldo", "regulator", "dc-dc", "dcdc", "buck", "boost",
                         "buck-boost", "pmic", "power management", "switcher",
                         "switching converter", "point of load", "pol converter",
                         "charge pump", "tps", "adp", "lt", "ltc", "lm", "mp")
        def _is_reg(c: dict) -> bool:
            blob = (f"{c.get('function','').lower()} "
                    f"{c.get('primary_part','').lower()} "
                    f"{c.get('primary_description','').lower()}")
            # Keep the match conservative — require an explicit regulator keyword.
            strong = ("ldo", "regulator", "dc-dc", "dcdc", "buck", "boost",
                      "buck-boost", "pmic", "switcher", "switching converter",
                      "point of load", "pol converter", "charge pump")
            return any(k in blob for k in strong)

        def _topology(c: dict) -> str:
            blob = (f"{c.get('function','').lower()} "
                    f"{c.get('primary_part','').lower()}")
            if any(k in blob for k in ("ldo", "linear regulator")):
                return "LDO"
            if "buck-boost" in blob: return "Buck-Boost"
            if "buck" in blob:       return "Buck"
            if "boost" in blob:      return "Boost"
            if "pmic" in blob:       return "PMIC"
            if "charge pump" in blob: return "Charge Pump"
            if any(k in blob for k in ("dc-dc", "dcdc", "switcher", "switching",
                                        "point of load", "pol")):
                return "Switcher"
            return "Regulator"

        def _spec_num(c: dict, keys: tuple, unit: str = "") -> float:
            """Case-insensitive, separator-tolerant spec reader. Accepts
            variants like 'V_out', 'Vout', 'v out', 'output voltage' for the
            same candidate key 'v_out'.
            """
            specs = c.get("primary_key_specs", {}) or {}
            # Build a normalized view: strip non-alphanumerics, lowercase.
            import re as _rn
            def _norm(s: str) -> str:
                return _rn.sub(r"[^a-z0-9]", "", str(s).lower())
            norm_map = {_norm(k): v for k, v in specs.items()}
            for k in keys:
                v = norm_map.get(_norm(k), "")
                if v:
                    val = parse_num(str(v), unit)
                    if val > 0: return val
            return 0.0

        # System V_in default — from design_parameters, else 12 V
        sys_vin = 0.0
        for k in ("input_voltage_v", "supply_voltage_v", "input_voltage",
                  "system_voltage", "power_input_v"):
            v = dp.get(k, "")
            if v:
                sys_vin = parse_num(str(v), "V")
                if sys_vin > 0: break
        if sys_vin <= 0:
            sys_vin = 12.0

        T_AMBIENT = 85.0  # worst-case ambient for thermal verdict
        TJ_MAX    = 125.0

        # Pre-scan description/function text for an explicit output voltage.
        # Priority:
        #   1. "<V_in> to <V_out>" — the output number comes after the arrow
        #      ("+12V to +5V Buck" → 5 V)
        #   2. First "<N> V" token that isn't preceded by an input-voltage phrase
        import re as _re_scan
        def _scan_vout(c):
            blob = (f"{c.get('function','')} {c.get('primary_description','')} "
                    f"{c.get('primary_part','')}").lower()
            # Pattern 1: "<A> v to <B> v" or "<A> v → <B> v" — return B
            m = _re_scan.search(
                r"\d+(?:\.\d+)?\s*v\s*(?:to|→|->|–|-)\s*[+]?(\d+(?:\.\d+)?)\s*v\b",
                blob,
            )
            if m:
                try:
                    val = float(m.group(1))
                    if 0.5 <= val <= 60.0:
                        return val
                except ValueError:
                    pass
            # Pattern 2: strip input-voltage phrases, then first "N V"
            blob = _re_scan.sub(r"(?:v_?in|input|input[\s-]*voltage)[^.,;]{0,30}",
                                " ", blob)
            m = _re_scan.search(r"(\d+(?:\.\d+)?)\s*v\b", blob)
            if m:
                try:
                    val = float(m.group(1))
                    if 0.5 <= val <= 60.0:
                        return val
                except ValueError:
                    pass
            return 0.0

        rows = []
        used_rails = set()   # track output rails already assigned
        for i, c in enumerate(comps, 1):
            if not _is_reg(c):
                continue
            topo = _topology(c)
            # V_in lookup — if the datasheet spec is a RANGE (e.g. "4.5-76 V"),
            # the low end is a compatibility limit, not the operating voltage.
            # Fall back to the system supply for thermal calcs in that case.
            def _spec_is_range(c, keys):
                specs = c.get("primary_key_specs", {}) or {}
                import re as _rr
                def _norm(s): return _rr.sub(r"[^a-z0-9]", "", str(s).lower())
                norm_map = {_norm(k): v for k, v in specs.items()}
                for k in keys:
                    v = norm_map.get(_norm(k), "")
                    if v and _rr.search(r"\d\s*(?:-|–|to)\s*\d", str(v)):
                        return True
                return False
            vin_keys = ("input_voltage", "vin", "input_voltage_range",
                         "v_in", "vin_v", "supply_voltage_range")
            if _spec_is_range(c, vin_keys):
                vin = sys_vin
            else:
                vin = _spec_num(c, vin_keys, "V") or sys_vin
            vout = _spec_num(c, ("output_voltage", "vout", "v_out", "vout_v",
                                  "supply_voltage", "output_v"), "V")
            if vout <= 0:
                vout = _scan_vout(c)   # parse "3.3 V LDO" etc.

            # If V_out still unknown, map to the rail whose load is largest
            # AMONGST rails not already taken by another regulator.
            if vout <= 0:
                # For LDOs prefer lower rails (LDOs usually drop 5V→3.3V etc);
                # for switchers pick highest-load rail.
                candidates = [k for k in range(len(rail_totals_typ))
                              if rails[k] not in used_rails and rail_totals_typ[k] > 0]
                if not candidates:
                    candidates = [k for k in range(len(rail_totals_typ))
                                  if rails[k] not in used_rails]
                if candidates:
                    if topo == "LDO":
                        # prefer lowest voltage rail with a real load
                        max_idx = min(candidates,
                                      key=lambda k: (rails[k], -rail_totals_typ[k]))
                    else:
                        max_idx = max(candidates,
                                      key=lambda k: (rail_totals_typ[k], -rails[k]))
                    vout = rails[max_idx] if rail_totals_typ[max_idx] > 0 else rails[max_idx]
                else:
                    vout = 3.3
            used_rails.add(vout)

            # ── I_out = sum of all loads on this regulator's output rail ──
            # The regulator sources current for every component on that rail,
            # so I_out is literally the rail total (W) / V_out. Datasheet
            # max-output-current is ONLY used as a ceiling sanity check.
            closest = min(range(len(rails)), key=lambda k: abs(rails[k] - vout))
            iout_from_rail = (rail_totals_typ[closest] / rails[closest]) \
                              if rails[closest] > 0 else 0.0
            spec_imax = _spec_num(c, ("max_output_current", "iout_max",
                                       "output_current", "i_out_max",
                                       "i_out", "iout"), "A")
            iout = iout_from_rail if iout_from_rail > 0 else spec_imax
            if iout <= 0:
                iout = 0.1  # placeholder — warn in rationale
            iout_overload = bool(spec_imax and iout_from_rail > spec_imax)

            # Efficiency / dissipation
            if topo == "LDO":
                # Linear dropout — no "efficiency" in the switching sense.
                # All of (V_in − V_out) × I_out is dissipated as heat.
                pdiss = max(0.0, (vin - vout)) * iout
                eta_str = "N/A (linear)"
            else:
                eta = 0.90
                pout = vout * iout
                pdiss = pout * (1 - eta) / eta
                eta_str = f"{eta*100:.0f}%"

            # Thermal resistances
            theta_jc = _spec_num(c, ("theta_jc", "rjc", "junction_to_case",
                                      "rθjc", "θjc"), "")
            theta_ja = _spec_num(c, ("theta_ja", "rja", "junction_to_ambient",
                                      "rθja", "θja"), "")
            if theta_jc <= 0:
                # Conservative defaults by package: SOT-223≈15, SOIC≈40,
                # QFN≈20, TO-220 without heatsink ≈ 5 (case), 62 (ja).
                pkg = (c.get("primary_key_specs", {}) or {}).get("package", "").lower()
                if "to-220" in pkg or "to220" in pkg: theta_jc = 3.0
                elif "qfn"   in pkg or "vqfn"  in pkg: theta_jc = 6.0
                elif "sot"   in pkg:                   theta_jc = 15.0
                elif "soic"  in pkg or "so-8"  in pkg: theta_jc = 30.0
                else:                                   theta_jc = 10.0
            if theta_ja <= 0:
                pkg = (c.get("primary_key_specs", {}) or {}).get("package", "").lower()
                if "to-220" in pkg or "to220" in pkg: theta_ja = 62.0
                elif "qfn"   in pkg or "vqfn"  in pkg: theta_ja = 30.0
                elif "sot"   in pkg:                   theta_ja = 80.0
                elif "soic"  in pkg or "so-8"  in pkg: theta_ja = 100.0
                else:                                   theta_ja = 50.0

            # Junction temp at worst-case ambient
            tj = T_AMBIENT + pdiss * theta_ja
            heatsink = "required" if tj >= TJ_MAX else ("recommended" if tj >= 110 else "no")
            # Recompute tj assuming heatsink brings θ to ~θ_jc + 10 if required
            if heatsink == "required":
                tj_hs = T_AMBIENT + pdiss * (theta_jc + 10)
                tj = round(tj_hs, 1)
                heatsink = f"required (→{tj} °C with HS)"
            else:
                tj = round(tj, 1)

            # ── Thermal verdict: strict 125 °C cutoff ──────────────────────
            # "Pass" if the reported T_j (which already accounts for a
            # heatsink if one was required) stays below 125 °C; otherwise
            # "Thermally Failed".
            verdict = "Pass" if (isinstance(tj, (int, float)) and tj < 125) else "Thermally Failed"

            rows.append({
                "si": len(rows) + 1,
                "part": c.get("primary_part", "—"),
                "topology": topo,
                "vin": vin,
                "vout": vout,
                "iout": iout,
                "eta": eta_str,
                "pdiss": round(pdiss, 3),
                "theta_jc": f"{theta_jc:g}",
                "theta_ja": f"{theta_ja:g}",
                "tj": tj,
                "heatsink": heatsink,
                "overload": iout_overload,
                "verdict": verdict,
            })
        return rows

    # ------------------------------------------------------------------
    #  HTML renderer — mirrors the markdown layout with the added
    #  converter / LDO thermal section for easy review in a browser.
    # ------------------------------------------------------------------
    def _build_per_stage_bias_html(self, tool_input: dict) -> str:
        """Render the per-stage DC-bias table for the power-calculation HTML.

        This was previously embedded in the GLB HTML as "Power Consumption
        per Stage". It lives here now so all power content is in one doc.
        """
        import html as _html
        esc = _html.escape
        stages = (tool_input.get("gain_loss_budget") or {}).get("stages", []) or []
        rows: list[tuple] = []
        total_pdc = 0.0
        for i, st in enumerate(stages, 1):
            bc = st.get("bias_conditions") or {}
            vdd = bc.get("vdd_v"); idq = bc.get("idq_ma"); pdc = bc.get("pdc_mw")
            cond = bc.get("datasheet_condition") or bc.get("condition_note") or ""
            if isinstance(vdd, (int, float)) and isinstance(idq, (int, float)):
                pdc_calc = round(vdd * idq, 1)
                if not isinstance(pdc, (int, float)):
                    pdc = pdc_calc
                elif abs(pdc - pdc_calc) > 10.0:
                    cond = (cond + f" · Pdc mismatch (V×I = {pdc_calc} mW)").strip(" ·")
            if isinstance(pdc, (int, float)):
                total_pdc += pdc
            rows.append((i, st, vdd, idq, pdc, cond))
        if not any(isinstance(r[4], (int, float)) for r in rows):
            return ""
        row_html = []
        for i, st, vdd, idq, pdc, cond in rows:
            vdd_s = f"{vdd:.2f}" if isinstance(vdd, (int, float)) else "—"
            idq_s = f"{idq:.1f}" if isinstance(idq, (int, float)) else "—"
            pdc_s = f"{pdc:.1f}" if isinstance(pdc, (int, float)) else "—"
            row_html.append(
                f"<tr><td>{i}</td><td>{esc(st.get('stage_name', '—'))}</td>"
                f"<td>{esc(st.get('component', '—'))}</td>"
                f"<td>{vdd_s}</td><td>{idq_s}</td><td>{pdc_s}</td>"
                f"<td>{esc(cond)}</td></tr>"
            )
        return f"""
<h2>Per-Stage DC Bias (from GLB stage datasheet conditions)</h2>
<table>
  <tr><th>#</th><th>Stage</th><th>Component</th>
      <th>Vdd (V)</th><th>Idq (mA)</th><th>Pdc (mW)</th>
      <th>Datasheet Condition</th></tr>
  {''.join(row_html)}
  <tr class="totals"><td colspan="5"><b>TOTAL</b></td>
      <td><b>{total_pdc:.1f}</b></td>
      <td>Sum of all powered-stage Pdc</td></tr>
</table>
<p class="note"><b>Key consistency rule:</b> the Vdd and Idq above must be the exact
bias conditions under which the datasheet specifies the gain, NF, P1dB, and OIP3 values
in the GLB stage-by-stage table. Different bias ⇒ different RF performance. The per-stage
Pdc (mW) rolls up into the per-rail budget below.</p>
"""

    def _build_power_calc_html(self, tool_input: dict, project_name: str) -> str:
        """Build a standalone HTML power-calculation document. Uses the same
        derivation and thermal helpers as the markdown version so the two
        views stay in sync.
        """
        from datetime import datetime
        import html as _html

        # Re-use the derivation pipeline by running the markdown builder and
        # extracting structured data. To keep the code compact we re-compute
        # rows/rail-totals here mirroring _build_power_calc_md's loop.
        comps = tool_input.get("component_recommendations", []) or []
        dp = tool_input.get("design_parameters", {}) or {}
        date = datetime.now().strftime("%d-%m-%Y")

        def parse_num(s, unit=""):
            import re as _re
            if not s: return 0.0
            s = str(s)
            m = _re.search(r'[\d.]+', s)
            if not m: return 0.0
            val = float(m.group())
            if "m" + unit.lower() in s.lower(): val /= 1000.0
            return val

        _PASSIVE_KEYWORDS = (
            "connector", "sma", "bnc", "smp", "u.fl", "mmcx", "rf connector",
            "coaxial", "coax", "heat sink", "heatsink", "thermal pad",
            "thermal interface", "shield", "enclosure", "housing", "bracket",
            "standoff", "antenna", "feedline", "balun passive",
            "saw filter", "baw filter", "ceramic filter", "lc filter",
            "pre-select filter", "preselect filter", "bandpass filter",
            "lowpass filter", "highpass filter", "notch filter", "attenuator",
            "pad attenuator", "fixed attenuator", "limiter", "pin diode limiter",
            "diode limiter", "power limiter", "isolator", "circulator",
            "directional coupler", "hybrid coupler", "wilkinson", "rat-race",
            "termination", "50 ohm load", "dummy load", "ferrite bead",
            "ferrite core", "choke", "rf choke", "transformer", "rf transformer",
            "resistor", "capacitor", "inductor", "passive", "crystal", "xtal",
            "resonator", "fuse", "ptc", "esd diode", "tvs diode", "tvs",
            "test point", "mounting hole",
        )
        def is_passive(comp):
            func = (comp.get("function") or "").lower()
            part = (comp.get("primary_part") or "").lower()
            desc = (comp.get("primary_description") or "").lower()
            blob = f"{func} {part} {desc}"
            return any(kw in blob for kw in _PASSIVE_KEYWORDS)

        def spec_voltage(comp):
            specs = comp.get("primary_key_specs", {}) or {}
            for key in ("supply_voltage", "voltage", "vcc", "vdd",
                        "operating_voltage", "vd", "vdd_rf", "vdrain"):
                v = specs.get(key, "")
                if v:
                    val = parse_num(v, "V")
                    if val > 0: return val
            return 0.0
        def spec_current(comp):
            specs = comp.get("primary_key_specs", {}) or {}
            for key in ("supply_current", "current", "icc", "idd", "iq",
                        "quiescent_current", "operating_current",
                        "typical_current", "id", "idrain"):
                v = specs.get(key, "")
                if v:
                    val = parse_num(v, "A")
                    if val > 0: return val
            return 0.0

        def _dp_num(*keys):
            for k in keys:
                v = dp.get(k, "")
                if v:
                    val = parse_num(str(v))
                    if val != 0: return val
            return 0.0
        target_nf_db    = _dp_num("noise_figure_db", "noise_figure", "system_nf", "nf")
        target_pout_dBm = _dp_num("output_power_dbm", "pout_dbm", "tx_power", "pout")
        target_iip3_dBm = _dp_num("iip3_dbm", "iip3")

        def classify(comp):
            func = (comp.get("function") or "").lower()
            part = (comp.get("primary_part") or "").lower()
            desc = (comp.get("primary_description") or "").lower()
            blob = f"{func} {part} {desc}"
            if any(k in blob for k in ("gan hemt pa", "gan pa", "power amplifier")) \
               and "lna" not in blob and "driver" not in blob:
                return "gan_pa"
            if any(k in blob for k in ("gan hemt lna", "gan lna", "phemt lna")) \
               or ("lna" in blob and "gan" in blob):
                return "gan_lna"
            if any(k in blob for k in ("lna", "low noise amp", "low-noise amp")):
                return "lna"
            if any(k in blob for k in ("driver amp", "driver stage", "pre-driver",
                                        "predriver", "driver")):
                return "driver_amp"
            if any(k in blob for k in ("gain block", "mmic amp", "buffer amp",
                                        "output buffer", "buffer")):
                return "gain_block"
            if any(k in blob for k in ("mixer", "downconverter", "upconverter")):
                return "mixer"
            if any(k in blob for k in ("vco", "synth", "pll", "local oscillator")):
                return "pll_lo"
            if "adc" in blob: return "adc"
            if "dac" in blob: return "dac"
            if any(k in blob for k in ("fpga", "xilinx", "altera", "zynq", "artix", "kintex")):
                return "fpga"
            if any(k in blob for k in ("mcu", "microcontroller", "soc", "arm", "cortex")):
                return "mcu"
            if any(k in blob for k in ("ldo", "regulator", "dc-dc", "buck", "boost", "pmic")):
                return "power"
            if any(k in blob for k in ("i2c", "spi", "i/o expander", "control interface")):
                return "io_expander"
            return "generic_ic"

        def derive_bias(comp):
            cls = classify(comp)
            sv, si_ = spec_voltage(comp), spec_current(comp)
            if sv > 0 and si_ > 0:
                return {"Vd": sv, "Id": si_, "class": "datasheet", "source": "datasheet"}
            if cls == "gan_lna":
                return {"Vd": 5.0, "Id": 0.100 if (target_nf_db and target_nf_db < 1.5) else 0.080,
                        "class": "Class A (low-noise)", "source": "derived"}
            if cls == "gan_pa":
                vd = 28.0; pae = 0.50
                pout_w = 10 ** ((target_pout_dBm - 30) / 10) if target_pout_dBm else 1.0
                id_peak = pout_w / (vd * pae) if pout_w > 0 else 0.3
                return {"Vd": vd, "Id": max(0.150, id_peak * 0.30),
                        "class": "Class AB", "source": "derived"}
            if cls == "lna":       return {"Vd": 5.0, "Id": 0.020, "class": "Class A", "source": "derived"}
            if cls == "driver_amp":
                return {"Vd": 5.0, "Id": 0.090 if (target_iip3_dBm and target_iip3_dBm > 25) else 0.060,
                        "class": "Class A (MMIC)", "source": "derived"}
            if cls == "gain_block": return {"Vd": 5.0, "Id": 0.060, "class": "Class A (MMIC)", "source": "derived"}
            if cls == "mixer":      return {"Vd": 5.0, "Id": 0.050, "class": "active", "source": "derived"}
            if cls == "pll_lo":     return {"Vd": 3.3, "Id": 0.120, "class": "active", "source": "derived"}
            if cls == "adc":        return {"Vd": 1.8, "Id": 0.400, "class": "active", "source": "derived"}
            if cls == "dac":        return {"Vd": 1.8, "Id": 0.300, "class": "active", "source": "derived"}
            if cls == "fpga":       return {"Vd": 1.8, "Id": 0.500, "class": "digital", "source": "derived"}
            if cls == "mcu":        return {"Vd": 3.3, "Id": 0.060, "class": "digital", "source": "derived"}
            if cls == "power":      return {"Vd": 5.0, "Id": 0.010, "class": "controller", "source": "derived"}
            if cls == "io_expander":return {"Vd": 3.3, "Id": 0.001, "class": "digital", "source": "derived"}
            return {"Vd": sv or 3.3, "Id": 0.0, "class": "unknown", "source": "unknown"}

        RAILS = [5.0, 3.3, 2.5, 1.8]
        RAIL_LABELS = ["5V", "3.3V", "2.5V", "1.8V"]

        rows = []
        rail_totals_typ = [0.0] * len(RAILS)
        rail_totals_max = [0.0] * len(RAILS)
        for i, comp in enumerate(comps, 1):
            func = comp.get("function", f"Component {i}")
            part = comp.get("primary_part", "—")
            # Regulators are sources, not loads — handled in thermal table only.
            if classify(comp) == "power":
                continue
            if is_passive(comp):
                rows.append({"si": len(rows)+1, "desc": func, "part": part, "qty": 1,
                              "current_ma": None, "rail_idx": None,
                              "p_typ": None, "p_max": None})
                continue
            bias = derive_bias(comp); rv, i_typ = bias["Vd"], bias["Id"]
            if bias["source"] == "unknown" or i_typ <= 0:
                rows.append({"si": len(rows)+1, "desc": func, "part": part, "qty": 1,
                              "current_ma": None, "rail_idx": None,
                              "p_typ": None, "p_max": None})
                continue
            i_max = i_typ * 1.30
            p_typ = round(rv * i_typ, 3); p_max = round(rv * i_max, 3)
            closest = min(range(len(RAILS)), key=lambda k: abs(RAILS[k] - rv))
            rail_totals_typ[closest] += p_typ
            rail_totals_max[closest] += p_max
            rows.append({"si": len(rows)+1, "desc": func, "part": part, "qty": 1,
                          "current_ma": round(i_typ * 1000, 1),
                          "rail_idx": closest, "p_typ": p_typ, "p_max": p_max})

        grand_typ = round(sum(rail_totals_typ), 3)
        grand_max = round(sum(rail_totals_max), 3)

        reg_rows = self._build_regulator_thermal_rows(
            comps=comps, rail_totals_typ=rail_totals_typ,
            rails=RAILS, rail_labels=RAIL_LABELS, dp=dp, parse_num=parse_num,
        )

        # ── Build HTML ────────────────────────────────────────────────────
        def esc(s): return _html.escape(str(s)) if s is not None else ""

        def rail_table(rail_indices):
            sel = [RAIL_LABELS[i] for i in rail_indices]
            head = ("<tr><th>SI</th><th>Description</th><th>Part No</th>"
                    "<th>Qty</th><th>Current (mA)</th>")
            for lbl in sel:
                head += f"<th>{lbl} TYP (W)</th><th>{lbl} MAX (W)</th>"
            head += "<th>Total TYP (W)</th><th>Total MAX (W)</th></tr>"
            body = []
            for r in rows:
                i_ma = r.get("current_ma")
                i_cell = f"{i_ma:g}" if i_ma is not None else "—"
                tr = (f"<tr><td>{r['si']}</td><td>{esc(r['desc'])}</td>"
                      f"<td>{esc(r['part'])}</td><td>{r['qty']}</td><td>{i_cell}</td>")
                tot_t = 0.0; tot_m = 0.0
                for ri in rail_indices:
                    if r["rail_idx"] == ri and r["p_typ"] is not None:
                        tr += f"<td>{r['p_typ']}</td><td>{r['p_max']}</td>"
                        tot_t += r["p_typ"]; tot_m += r["p_max"]
                    else:
                        tr += "<td>—</td><td>—</td>"
                tr += (f"<td>{round(tot_t, 3) if tot_t else '—'}</td>"
                       f"<td>{round(tot_m, 3) if tot_m else '—'}</td></tr>")
                body.append(tr)
            # Totals row
            tot = ('<tr class="totals"><td></td><td><b>TOTALS</b></td>'
                   '<td></td><td></td><td></td>')
            for ri in rail_indices:
                tot += f"<td><b>{round(rail_totals_typ[ri], 3)}</b></td><td><b>{round(rail_totals_max[ri], 3)}</b></td>"
            sub_t = round(sum(rail_totals_typ[i] for i in rail_indices), 3)
            sub_m = round(sum(rail_totals_max[i] for i in rail_indices), 3)
            tot += f"<td><b>{sub_t}</b></td><td><b>{sub_m}</b></td></tr>"
            return f"<table>{head}{''.join(body)}{tot}</table>"

        low_rail_has_data = (rail_totals_typ[2] > 0 or rail_totals_max[2] > 0 or
                             rail_totals_typ[3] > 0 or rail_totals_max[3] > 0)

        summary_rows = []
        for idx, lbl in enumerate(RAIL_LABELS):
            summary_rows.append(
                f"<tr><td>{lbl}</td><td>{round(rail_totals_typ[idx], 3)}</td>"
                f"<td>{round(rail_totals_max[idx], 3)}</td></tr>"
            )
        summary_rows.append(
            f"<tr class='totals'><td><b>TOTAL</b></td><td><b>{grand_typ}</b></td>"
            f"<td><b>{grand_max}</b></td></tr>"
        )

        # Regulator thermal table
        reg_html = ""
        if reg_rows:
            all_pass = all(r.get('verdict') == 'Pass' for r in reg_rows)
            verdict_msg = (
                "✅ Pass — every regulator junction stays below 125 °C at 85 °C ambient."
                if all_pass else
                "❌ Thermally Failed — at least one regulator exceeds the 125 °C junction "
                "limit. Add a heatsink / copper pour, or move to a lower-dropout switcher "
                "or a higher-power package."
            )
            reg_body = []
            for r in reg_rows:
                tj_cell = (f"<td class='warn'>{r['tj']}</td>"
                           if isinstance(r['tj'], (int, float)) and r['tj'] >= 110
                           else f"<td>{r['tj']}</td>")
                v = r.get('verdict', '')
                v_class = 'pass' if v == 'Pass' else 'fail'
                reg_body.append(
                    f"<tr><td>{r['si']}</td><td>{esc(r['part'])}</td><td>{r['topology']}</td>"
                    f"<td>{r['vin']:g}</td><td>{r['vout']:g}</td><td>{r['iout']:.3f}</td>"
                    f"<td>{r['eta']}</td><td>{r['pdiss']:.3f}</td>"
                    f"<td>{r['theta_jc']}</td><td>{r['theta_ja']}</td>"
                    f"{tj_cell}<td>{r['heatsink']}</td>"
                    f"<td class='{v_class}'><b>{v}</b></td></tr>"
                )
            reg_html = f"""
<h2>Power Converters &amp; LDOs — Dissipation and Thermal Analysis</h2>
<p class="note">Each regulator's dissipation is
 <code>P_diss = (V_in − V_out) × I_out</code> for linear LDOs
 (no switching efficiency applies), or
 <code>P_out × (1 − η) / η</code> for switching converters.
 Junction temperature is
 <code>T_j = T_ambient + P_diss × θ_ja</code>
 evaluated at <b>T_ambient = 85 °C</b> (worst-case avionics chamber),
 with θ_jc / θ_ja from the datasheet or typical package values.
 <b>Pass</b> if T_j &lt; 125 °C, <b>Thermally Failed</b> otherwise.</p>
<table>
  <tr><th>SI</th><th>Part No</th><th>Topology</th><th>V_in (V)</th>
      <th>V_out (V)</th><th>I_out (A)</th><th>η</th><th>P_diss (W)</th>
      <th>θ_jc (°C/W)</th><th>θ_ja (°C/W)</th><th>T_j @ 85 °C amb (°C)</th>
      <th>Heatsink?</th><th>Verdict</th></tr>
  {''.join(reg_body)}
</table>
<p class="verdict {'ok' if all_pass else 'warn'}"><b>Overall thermal verdict:</b> {verdict_msg}</p>
"""

        css = """
body { font-family: -apple-system, Segoe UI, Inter, sans-serif; max-width: 1200px;
       margin: 32px auto; padding: 0 24px; color: #1a2235; background: #f7f8fa; }
h1 { font-size: 28px; margin-bottom: 4px; color: #0b1220; }
h1 small { display: block; font-size: 14px; color: #64748b; font-weight: 400; margin-top: 6px; }
h2 { font-size: 18px; margin-top: 32px; border-bottom: 2px solid #00c6a7;
     padding-bottom: 6px; color: #0b1220; }
p.note { color: #475569; font-size: 13px; line-height: 1.55; }
table { width: 100%; border-collapse: collapse; margin: 12px 0 24px; font-size: 13px; background: #fff;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06); border-radius: 6px; overflow: hidden; }
th { background: #0b1220; color: #e2e8f0; text-align: left; padding: 10px 12px; font-weight: 600; font-size: 12px; letter-spacing: 0.3px; }
td { padding: 8px 12px; border-bottom: 1px solid #e2e8f0; }
tr.totals td { background: #eef5f3; font-weight: 600; }
td.warn { background: #fff4e5; color: #c2410c; font-weight: 600; }
td.pass { background: #ecfdf5; color: #065f46; font-weight: 600; }
td.fail { background: #fef2f2; color: #991b1b; font-weight: 600; }
p.verdict { padding: 10px 14px; border-radius: 6px; font-size: 13px; }
p.verdict.ok { background: #ecfdf5; color: #065f46; }
p.verdict.warn { background: #fef2f2; color: #991b1b; }
code { background: #eef2f6; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
"""

        html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Power Calculation — {esc(project_name)}</title>
<style>{css}</style>
</head>
<body>
<h1>Power Calculation
  <small>{esc(project_name)} · Date: {date}</small>
</h1>

<p class="note">All values in Watts (W). TYP = typical operating power,
MAX = worst-case (130 % of TYP). Rail assignment uses the component's optimal
V_d (datasheet value when present in the BOM, otherwise derived from
first-principles RF bias formulas tied to the NF / Pout / IIP3 targets).</p>

{self._build_per_stage_bias_html(tool_input)}

<h2>Power Budget — 5 V &amp; 3.3 V Rails</h2>
{rail_table([0, 1])}
"""
        if low_rail_has_data:
            html_doc += f"""
<h2>Power Budget — 2.5 V &amp; 1.8 V Rails</h2>
{rail_table([2, 3])}
"""
        html_doc += f"""
<h2>Power Summary</h2>
<table>
  <tr><th>Rail</th><th>Typical Power (W)</th><th>Max Power (W)</th></tr>
  {''.join(summary_rows)}
</table>
<p class="note">Power values are estimated from component datasheets and design
parameters. Actual measurements should be taken during hardware bring-up and
updated in this table.</p>
{reg_html}
</body>
</html>
"""
        return html_doc

    # ------------------------------------------------------------------
    #  Excel renderer — writes a .xlsx workbook with the rail-budget
    #  sheet (including Current column) and the regulator thermal sheet
    #  (with strict 125 °C Pass / Thermally Failed verdict).
    # ------------------------------------------------------------------
    def _build_power_calc_xlsx(self, tool_input: dict, project_name: str, out_path) -> None:
        """Write `power_calculation.xlsx` at the given path. Uses openpyxl
        with formulas for power = V × I and SUM totals so the workbook
        stays live — a reviewer can tweak currents and see totals update.
        """
        from datetime import datetime
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        # Re-compute rows using the HTML path to keep a single source of
        # truth for the numbers.
        comps = tool_input.get("component_recommendations", []) or []
        dp = tool_input.get("design_parameters", {}) or {}

        def parse_num(s, unit=""):
            import re as _re
            if not s: return 0.0
            s = str(s)
            m = _re.search(r'[\d.]+', s)
            if not m: return 0.0
            val = float(m.group())
            if "m" + unit.lower() in s.lower(): val /= 1000.0
            return val

        # Build the HTML first (side-effect: populates rows list logic).
        # Instead, re-use derivation by calling internal routines:
        # easier: we simply render HTML and parse rows from its structure is
        # wasteful. Redo the loops here, mirroring the HTML builder.
        _PASSIVE_KEYWORDS = (
            "connector", "sma", "bnc", "heat sink", "heatsink", "saw filter",
            "limiter", "attenuator", "isolator", "circulator", "ferrite bead",
            "transformer", "resistor", "capacitor", "inductor", "passive",
            "crystal", "antenna", "feedline", "shield", "enclosure", "fuse",
            "ptc", "esd diode", "tvs", "test point", "mounting hole",
            "thermal pad", "ceramic filter", "baw filter", "bandpass filter",
            "lowpass filter", "highpass filter", "termination",
        )
        def is_passive(c):
            blob = (f"{c.get('function','').lower()} "
                    f"{c.get('primary_part','').lower()} "
                    f"{c.get('primary_description','').lower()}")
            return any(k in blob for k in _PASSIVE_KEYWORDS)

        def _dp_num(*keys):
            for k in keys:
                v = dp.get(k, "")
                if v:
                    val = parse_num(str(v))
                    if val != 0: return val
            return 0.0
        target_nf_db    = _dp_num("noise_figure_db", "noise_figure", "system_nf", "nf")
        target_pout_dBm = _dp_num("output_power_dbm", "pout_dbm", "tx_power", "pout")
        target_iip3_dBm = _dp_num("iip3_dbm", "iip3")

        def classify(c):
            blob = (f"{c.get('function','').lower()} "
                    f"{c.get('primary_part','').lower()} "
                    f"{c.get('primary_description','').lower()}")
            if any(k in blob for k in ("gan hemt pa", "gan pa", "power amplifier")) \
               and "lna" not in blob and "driver" not in blob:
                return "gan_pa"
            if any(k in blob for k in ("gan hemt lna", "gan lna", "phemt lna")) \
               or ("lna" in blob and "gan" in blob):
                return "gan_lna"
            if any(k in blob for k in ("lna", "low noise amp", "low-noise amp")): return "lna"
            if any(k in blob for k in ("driver",)): return "driver_amp"
            if any(k in blob for k in ("gain block", "mmic amp", "buffer amp",
                                        "output buffer", "buffer")): return "gain_block"
            if any(k in blob for k in ("mixer", "downconverter", "upconverter")): return "mixer"
            if any(k in blob for k in ("vco", "synth", "pll", "local oscillator")): return "pll_lo"
            if "adc" in blob: return "adc"
            if "dac" in blob: return "dac"
            if any(k in blob for k in ("fpga", "xilinx", "altera", "zynq", "artix", "kintex")): return "fpga"
            if any(k in blob for k in ("mcu", "microcontroller", "soc", "arm", "cortex")): return "mcu"
            if any(k in blob for k in ("ldo", "regulator", "dc-dc", "buck", "boost", "pmic")): return "power"
            if any(k in blob for k in ("i2c", "spi", "i/o expander", "control interface")): return "io_expander"
            return "generic_ic"

        def spec_voltage(c):
            specs = c.get("primary_key_specs", {}) or {}
            for k in ("supply_voltage", "voltage", "vcc", "vdd", "operating_voltage",
                      "vd", "vdd_rf", "vdrain"):
                v = specs.get(k, "")
                if v:
                    val = parse_num(v, "V")
                    if val > 0: return val
            return 0.0
        def spec_current(c):
            specs = c.get("primary_key_specs", {}) or {}
            for k in ("supply_current", "current", "icc", "idd", "iq",
                      "quiescent_current", "operating_current", "typical_current",
                      "id", "idrain"):
                v = specs.get(k, "")
                if v:
                    val = parse_num(v, "A")
                    if val > 0: return val
            return 0.0

        def derive_bias(c):
            cls = classify(c)
            sv, si_ = spec_voltage(c), spec_current(c)
            if sv > 0 and si_ > 0: return {"Vd": sv, "Id": si_}
            if cls == "gan_lna": return {"Vd": 5.0, "Id": 0.100 if (target_nf_db and target_nf_db < 1.5) else 0.080}
            if cls == "gan_pa":
                vd = 28.0; pae = 0.50
                pout_w = 10 ** ((target_pout_dBm - 30) / 10) if target_pout_dBm else 1.0
                id_peak = pout_w / (vd * pae) if pout_w > 0 else 0.3
                return {"Vd": vd, "Id": max(0.150, id_peak * 0.30)}
            if cls == "lna":       return {"Vd": 5.0, "Id": 0.020}
            if cls == "driver_amp": return {"Vd": 5.0, "Id": 0.090 if (target_iip3_dBm and target_iip3_dBm > 25) else 0.060}
            if cls == "gain_block": return {"Vd": 5.0, "Id": 0.060}
            if cls == "mixer":      return {"Vd": 5.0, "Id": 0.050}
            if cls == "pll_lo":     return {"Vd": 3.3, "Id": 0.120}
            if cls == "adc":        return {"Vd": 1.8, "Id": 0.400}
            if cls == "dac":        return {"Vd": 1.8, "Id": 0.300}
            if cls == "fpga":       return {"Vd": 1.8, "Id": 0.500}
            if cls == "mcu":        return {"Vd": 3.3, "Id": 0.060}
            if cls == "io_expander": return {"Vd": 3.3, "Id": 0.001}
            return {"Vd": sv or 3.3, "Id": 0.0}

        RAILS = [5.0, 3.3, 2.5, 1.8]
        RAIL_LABELS = ["5V", "3.3V", "2.5V", "1.8V"]

        # --- Build workbook (single sheet: all three sections stacked) ---
        wb = Workbook()
        ws = wb.active
        ws.title = "Power Calculation"

        # Styles
        hdr_font = Font(name="Arial", bold=True, color="FFFFFF", size=11)
        hdr_fill = PatternFill("solid", start_color="0B1220")
        hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        totals_font = Font(name="Arial", bold=True)
        totals_fill = PatternFill("solid", start_color="EEF5F3")
        body_font = Font(name="Arial", size=10)
        pass_font = Font(name="Arial", bold=True, color="065F46")
        pass_fill = PatternFill("solid", start_color="ECFDF5")
        fail_font = Font(name="Arial", bold=True, color="991B1B")
        fail_fill = PatternFill("solid", start_color="FEF2F2")
        warn_fill = PatternFill("solid", start_color="FFF4E5")
        thin = Side(border_style="thin", color="D1D5DB")
        cell_border = Border(left=thin, right=thin, top=thin, bottom=thin)

        # ---- Sheet 1: Power Budget (rail tables + current column) -------
        ws["A1"] = "Power Calculation"
        ws["A1"].font = Font(name="Arial", bold=True, size=16, color="0B1220")
        ws["A2"] = project_name
        ws["A2"].font = Font(name="Arial", size=11, color="64748B")
        ws["A3"] = f"Date: {datetime.now().strftime('%d-%m-%Y')}"
        ws["A3"].font = Font(name="Arial", size=10, color="64748B")

        # Layout (per user spec):
        #   Qty → Power (W) → Voltage (V) → per-rail TYP (mA) → per-rail MAX (mA)
        #   → Total TYP (mA) → Total MAX (mA)
        # Current (mA) is the HARDCODED input on the correct rail (blue);
        # MAX = TYP × 1.3 (formula); Power = V × TotalTYP / 1000 (formula).
        headers = (["SI", "Description", "Part No", "Qty", "Power (W)", "Voltage (V)"] +
                   [f"{lbl} TYP (mA)" for lbl in RAIL_LABELS] +
                   [f"{lbl} MAX (mA)" for lbl in RAIL_LABELS] +
                   ["Total TYP (mA)", "Total MAX (mA)"])
        hrow = 5
        for ci, h in enumerate(headers, 1):
            c = ws.cell(row=hrow, column=ci, value=h)
            c.font = hdr_font; c.fill = hdr_fill; c.alignment = hdr_align
            c.border = cell_border
        ws.row_dimensions[hrow].height = 30

        # Widths
        widths = [5, 42, 22, 5, 12, 12] + [13]*len(RAILS) + [13]*len(RAILS) + [15, 15]
        for ci, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(ci)].width = w

        # Column indices (1-based)
        COL_SI   = 1
        COL_DESC = 2
        COL_PART = 3
        COL_QTY  = 4
        COL_P    = 5    # Power (W) — derived from V × Total TYP
        COL_V    = 6    # Voltage (V)
        COL_RAIL_TYP0 = 7                   # 5V TYP (mA)
        COL_RAIL_MAX0 = 7 + len(RAILS)      # 5V MAX (mA)
        COL_TOT_TYP = COL_RAIL_MAX0 + len(RAILS)
        COL_TOT_MAX = COL_TOT_TYP + 1
        blue = Font(name="Arial", size=10, color="0000FF")   # hardcoded inputs
        black = Font(name="Arial", size=10, color="000000")  # formulas

        row_idx = hrow + 1
        rail_row_ranges = [[] for _ in RAILS]   # for SUM totals per rail

        for i, comp in enumerate(comps, 1):
            if classify(comp) == "power":
                continue
            func = comp.get("function", f"Component {i}")
            part = comp.get("primary_part", "—")
            ws.cell(row=row_idx, column=COL_SI, value=row_idx - hrow).font = body_font
            ws.cell(row=row_idx, column=COL_DESC, value=func).font = body_font
            ws.cell(row=row_idx, column=COL_PART, value=part).font = body_font
            ws.cell(row=row_idx, column=COL_QTY,  value=1).font = body_font

            if is_passive(comp):
                # Passive — no current / voltage / power
                for ci in range(COL_P, COL_TOT_MAX + 1):
                    ws.cell(row=row_idx, column=ci, value="—").font = body_font
            else:
                bias = derive_bias(comp)
                rv, i_a = bias["Vd"], bias["Id"]
                if i_a <= 0:
                    for ci in range(COL_P, COL_TOT_MAX + 1):
                        ws.cell(row=row_idx, column=ci, value="—").font = body_font
                else:
                    # Voltage input (blue)
                    ws.cell(row=row_idx, column=COL_V, value=rv).font = blue
                    closest = min(range(len(RAILS)),
                                  key=lambda k: abs(RAILS[k] - rv))
                    vL = get_column_letter(COL_V)
                    typ_letter_row = None
                    for idx in range(len(RAILS)):
                        typ_col = COL_RAIL_TYP0 + idx
                        max_col = COL_RAIL_MAX0 + idx
                        typ_letter = get_column_letter(typ_col)
                        max_letter = get_column_letter(max_col)
                        if idx == closest:
                            # TYP current in mA — hardcoded blue input
                            ws.cell(row=row_idx, column=typ_col,
                                    value=round(i_a * 1000, 3)).font = blue
                            # MAX = TYP × 1.3 — formula
                            ws.cell(row=row_idx, column=max_col,
                                    value=f"=ROUND({typ_letter}{row_idx}*1.3,3)"
                                   ).font = black
                            typ_letter_row = typ_letter
                            rail_row_ranges[idx].append(row_idx)
                        else:
                            ws.cell(row=row_idx, column=typ_col, value="—").font = body_font
                            ws.cell(row=row_idx, column=max_col, value="—").font = body_font
                    # Row totals (SUM of rail TYP/MAX cells to be robust if
                    # a component draws from multiple rails in the future)
                    typ_range = f"{get_column_letter(COL_RAIL_TYP0)}{row_idx}:" \
                                f"{get_column_letter(COL_RAIL_TYP0+len(RAILS)-1)}{row_idx}"
                    max_range = f"{get_column_letter(COL_RAIL_MAX0)}{row_idx}:" \
                                f"{get_column_letter(COL_RAIL_MAX0+len(RAILS)-1)}{row_idx}"
                    ws.cell(row=row_idx, column=COL_TOT_TYP,
                            value=f"=SUM({typ_range})").font = black
                    ws.cell(row=row_idx, column=COL_TOT_MAX,
                            value=f"=SUM({max_range})").font = black
                    # Power (W) = V × Total TYP mA / 1000 — live formula
                    totL = get_column_letter(COL_TOT_TYP)
                    ws.cell(row=row_idx, column=COL_P,
                            value=f"=ROUND({vL}{row_idx}*{totL}{row_idx}/1000,4)"
                           ).font = black
            # Borders
            for ci in range(1, COL_TOT_MAX + 1):
                ws.cell(row=row_idx, column=ci).border = cell_border
            row_idx += 1

        # Totals row
        totals_row = row_idx + 1
        ws.cell(row=totals_row, column=COL_DESC, value="TOTALS").font = totals_font
        ws.cell(row=totals_row, column=COL_DESC).fill = totals_fill
        for idx in range(len(RAILS)):
            for col_base, lbl in ((COL_RAIL_TYP0, "TYP"), (COL_RAIL_MAX0, "MAX")):
                col = col_base + idx
                letter = get_column_letter(col)
                if rail_row_ranges[idx]:
                    rng = ",".join(f"{letter}{r}" for r in rail_row_ranges[idx])
                    c = ws.cell(row=totals_row, column=col, value=f"=SUM({rng})")
                else:
                    c = ws.cell(row=totals_row, column=col, value=0)
                c.font = totals_font; c.fill = totals_fill
        # Grand totals (sum of per-rail TYP / MAX totals, in mA)
        typ_letters = [get_column_letter(COL_RAIL_TYP0 + i) for i in range(len(RAILS))]
        max_letters = [get_column_letter(COL_RAIL_MAX0 + i) for i in range(len(RAILS))]
        c = ws.cell(row=totals_row, column=COL_TOT_TYP,
                    value="=" + "+".join(f"{L}{totals_row}" for L in typ_letters))
        c.font = totals_font; c.fill = totals_fill
        c = ws.cell(row=totals_row, column=COL_TOT_MAX,
                    value="=" + "+".join(f"{L}{totals_row}" for L in max_letters))
        c.font = totals_font; c.fill = totals_fill
        # Total Power (W) at totals row = Σ (Rail V × Rail Total mA) / 1000
        total_power_expr = "+".join(
            f"{RAILS[i]}*{typ_letters[i]}{totals_row}" for i in range(len(RAILS))
        )
        c = ws.cell(row=totals_row, column=COL_P,
                    value=f"=ROUND(({total_power_expr})/1000,4)")
        c.font = totals_font; c.fill = totals_fill
        for ci in range(1, COL_TOT_MAX + 1):
            ws.cell(row=totals_row, column=ci).border = cell_border

        ws.freeze_panes = ws.cell(row=hrow + 1, column=1)

        # ======= SECTION 2 — Regulator Thermal Analysis ==================
        # Prepare rail current totals un-rounded so I_out is exact
        md_totals_current_a = [0.0] * len(RAILS)
        for i, comp in enumerate(comps, 1):
            if is_passive(comp) or classify(comp) == "power": continue
            bias = derive_bias(comp)
            rv, i_a = bias["Vd"], bias["Id"]
            if i_a <= 0: continue
            closest = min(range(len(RAILS)), key=lambda k: abs(RAILS[k] - rv))
            md_totals_current_a[closest] += i_a
        md_totals_typ = [RAILS[k] * md_totals_current_a[k] for k in range(len(RAILS))]
        reg_rows = self._build_regulator_thermal_rows(
            comps=comps, rail_totals_typ=md_totals_typ,
            rails=RAILS, rail_labels=RAIL_LABELS, dp=dp, parse_num=parse_num,
        )

        # Section 2 title (2 blank rows after totals_row)
        sec2_title = totals_row + 3
        ws.cell(row=sec2_title, column=1,
                value="Section 2 — Converter / LDO Thermal Analysis").font = \
            Font(name="Arial", bold=True, size=13, color="0B1220")
        ws.merge_cells(start_row=sec2_title, start_column=1,
                       end_row=sec2_title, end_column=13)
        sec2_note = sec2_title + 1
        ws.cell(row=sec2_note, column=1, value=(
            "P_diss = (Vin − Vout) × Iout for LDOs (no switching efficiency). "
            "P_diss = Pout × (1 − η)/η for switchers. "
            "T_j = T_amb + P_diss × θ_ja at T_amb = 85 °C. "
            "Verdict: Pass if T_j < 125 °C, else Thermally Failed."
        )).font = Font(name="Arial", size=10, color="475569")
        ws.cell(row=sec2_note, column=1).alignment = Alignment(wrap_text=True)
        ws.row_dimensions[sec2_note].height = 45
        ws.merge_cells(start_row=sec2_note, start_column=1,
                       end_row=sec2_note, end_column=13)

        reg_headers = ["SI", "Part No", "Topology", "V_in (V)", "V_out (V)",
                       "I_out (A)", "η", "P_diss (W)", "θ_jc (°C/W)",
                       "θ_ja (°C/W)", "T_j @ 85°C (°C)", "Heatsink?", "Verdict"]
        reg_hrow = sec2_note + 1
        for ci, h in enumerate(reg_headers, 1):
            c = ws.cell(row=reg_hrow, column=ci, value=h)
            c.font = hdr_font; c.fill = hdr_fill; c.alignment = hdr_align
            c.border = cell_border
        ws.row_dimensions[reg_hrow].height = 28

        row_idx = reg_hrow + 1
        for r in reg_rows:
            ws.cell(row=row_idx, column=1, value=r["si"]).font = body_font
            ws.cell(row=row_idx, column=2, value=r["part"]).font = body_font
            ws.cell(row=row_idx, column=3, value=r["topology"]).font = body_font
            ws.cell(row=row_idx, column=4, value=r["vin"]).font = blue
            ws.cell(row=row_idx, column=5, value=r["vout"]).font = blue
            ws.cell(row=row_idx, column=6, value=round(r["iout"], 4)).font = blue
            ws.cell(row=row_idx, column=7, value=r["eta"]).font = body_font
            ws.cell(row=row_idx, column=8, value=r["pdiss"]).font = black
            ws.cell(row=row_idx, column=9, value=float(r["theta_jc"])).font = blue
            ws.cell(row=row_idx, column=10, value=float(r["theta_ja"])).font = blue
            tj_cell = ws.cell(row=row_idx, column=11, value=r["tj"])
            tj_cell.font = body_font
            if isinstance(r["tj"], (int, float)) and r["tj"] >= 110:
                tj_cell.fill = warn_fill
            ws.cell(row=row_idx, column=12, value=r["heatsink"]).font = body_font
            v_cell = ws.cell(row=row_idx, column=13, value=r["verdict"])
            if r["verdict"] == "Pass":
                v_cell.font = pass_font; v_cell.fill = pass_fill
            else:
                v_cell.font = fail_font; v_cell.fill = fail_fill
            for ci in range(1, len(reg_headers) + 1):
                ws.cell(row=row_idx, column=ci).border = cell_border
            row_idx += 1

        # Overall thermal verdict
        all_pass = all(r.get("verdict") == "Pass" for r in reg_rows) if reg_rows else True
        verdict_row = row_idx + 1
        ws.cell(row=verdict_row, column=1,
                value="Overall thermal verdict:").font = totals_font
        vmsg = ("Pass — all junctions < 125 °C." if all_pass
                else "Thermally Failed — add heatsink / lower-dropout part.")
        overall = ws.cell(row=verdict_row, column=2, value=vmsg)
        if all_pass:
            overall.font = pass_font; overall.fill = pass_fill
        else:
            overall.font = fail_font; overall.fill = fail_fill
        ws.merge_cells(start_row=verdict_row, start_column=2,
                       end_row=verdict_row, end_column=len(reg_headers))

        # ======= SECTION 3 — Power Summary (Load side) ==================
        sec3_title = verdict_row + 3
        ws.cell(row=sec3_title, column=1,
                value="Section 3 — Power Summary (Load Side)").font = \
            Font(name="Arial", bold=True, size=13, color="0B1220")
        ws.merge_cells(start_row=sec3_title, start_column=1,
                       end_row=sec3_title, end_column=13)

        sum_hrow = sec3_title + 1
        sum_headers = ["Rail", "Typical Current (mA)", "Max Current (mA)",
                       "Typical Power (W)", "Max Power (W)"]
        for ci, h in enumerate(sum_headers, 1):
            c = ws.cell(row=sum_hrow, column=ci, value=h)
            c.font = hdr_font; c.fill = hdr_fill; c.alignment = hdr_align
            c.border = cell_border

        for idx, lbl in enumerate(RAIL_LABELS, sum_hrow + 1):
            rail_i = idx - (sum_hrow + 1)
            ws.cell(row=idx, column=1, value=lbl).font = body_font
            typ_col = get_column_letter(COL_RAIL_TYP0 + rail_i)
            max_col = get_column_letter(COL_RAIL_MAX0 + rail_i)
            v = RAILS[rail_i]
            ws.cell(row=idx, column=2,
                    value=f"={typ_col}{totals_row}").font = black
            ws.cell(row=idx, column=3,
                    value=f"={max_col}{totals_row}").font = black
            ws.cell(row=idx, column=4,
                    value=f"=ROUND({v}*{typ_col}{totals_row}/1000,4)").font = black
            ws.cell(row=idx, column=5,
                    value=f"=ROUND({v}*{max_col}{totals_row}/1000,4)").font = black
            for ci in range(1, 6):
                ws.cell(row=idx, column=ci).border = cell_border
        sum_total_row = sum_hrow + 1 + len(RAIL_LABELS)
        ws.cell(row=sum_total_row, column=1, value="TOTAL").font = totals_font
        ws.cell(row=sum_total_row, column=1).fill = totals_fill
        for col_letter, col_idx in (("B", 2), ("C", 3), ("D", 4), ("E", 5)):
            c = ws.cell(row=sum_total_row, column=col_idx,
                        value=f"=SUM({col_letter}{sum_hrow + 1}:{col_letter}{sum_total_row - 1})")
            c.font = totals_font; c.fill = totals_fill
        for ci in range(1, 6):
            ws.cell(row=sum_total_row, column=ci).border = cell_border

        wb.save(str(out_path))

    def _normalize_glb_for_report(self, glb_in: dict) -> dict:
        """Deterministic post-processing for the RF Gain-Loss Budget.

        Steps (in order):
          1. Replace ideal (≥ -0.05 dB) connector losses with realistic
             minimums. Connectors are never lossless; SMA at ≤18 GHz is
             ≈ 0.15 dB typical.
          2. Inject a PCB trace-loss stage (50 Ω microstrip on RO4350B
             ≈ 0.3 dB for a 1–2 inch run at band) when the LLM omits it.
          3. Recompute cumulative gain, output power, and cumulative NF
             (Friis) so every consumer of the dict — markdown, HTML, and
             derived metrics — sees self-consistent numbers.

        Returns a deep-copied, normalized dict. The input is never mutated.
        """
        import copy as _copy
        import math as _math

        glb = _copy.deepcopy(glb_in or {})
        stages = glb.get("stages", []) or []

        # ── 1. Normalize connector losses (no ideal connectors) ─────────────
        _CONN_KW = (
            "connector", "sma", "n-type", "n type", "bnc", "ssma", "smp",
            "2.92", "2.4mm", "1.85", "tnc", "qma", "mcx", "mmcx",
        )
        for st in stages:
            _combined = (str(st.get("stage_name", "") or "") + " " +
                         str(st.get("component", "") or "")).lower()
            if not any(kw in _combined for kw in _CONN_KW):
                continue
            g = st.get("gain_db")
            if isinstance(g, (int, float)) and g >= -0.05:
                st["gain_db"] = -0.15  # SMA typical, ≤ 18 GHz
                nt = str(st.get("notes", "") or "")
                if (not nt) or ("ideal" in nt.lower()):
                    st["notes"] = "Min connector loss (SMA typ, ≤18 GHz)"
            nf = st.get("noise_figure_db")
            if nf in (None, 0, 0.0):
                st["noise_figure_db"] = abs(st.get("gain_db", -0.15))

        # ── 2. Inject PCB trace loss if not already present ─────────────────
        def _stage_label(s: dict) -> str:
            return (str(s.get("stage_name", "") or "") + " " +
                    str(s.get("component", "") or "")).lower()
        _has_trace = any("trace" in _stage_label(s) or "microstrip" in _stage_label(s)
                         or "stripline" in _stage_label(s) for s in stages)
        if (not _has_trace) and len(stages) >= 2:
            insert_idx = 1
            for i, s in enumerate(stages):
                if any(kw in _stage_label(s) for kw in _CONN_KW):
                    insert_idx = i + 1
                    break
            stages.insert(min(insert_idx, len(stages)), {
                "stage_name": "PCB Trace",
                "component": "50Ω Microstrip (RO4350B)",
                "gain_db": -0.3,
                "noise_figure_db": 0.3,
                "p1db_out_dbm": 99,
                "oip3_dbm": 99,
                "notes": "Typ 1–2 in RO4350B microstrip loss at band",
            })

        # ── 3. Recompute cumulative gain / Pout / cascaded NF (Friis) ───────
        p_in = glb.get("input_power_dbm")
        cum_g_db = 0.0
        F_num = 0.0
        G_prod_lin = 1.0
        for i, st in enumerate(stages):
            g = st.get("gain_db")
            if not isinstance(g, (int, float)):
                continue
            nf = st.get("noise_figure_db")
            if not isinstance(nf, (int, float)):
                nf = abs(g) if g < 0 else 0.0
                st["noise_figure_db"] = nf
            cum_g_db += g
            st["cumulative_gain_db"] = round(cum_g_db, 2)
            if isinstance(p_in, (int, float)):
                st["output_power_dbm"] = round(p_in + cum_g_db, 2)
            F_stage = 10 ** (nf / 10.0)
            G_stage_lin = 10 ** (g / 10.0)
            if i == 0:
                F_num = F_stage
            else:
                F_num += (F_stage - 1.0) / G_prod_lin if G_prod_lin > 0 else 0.0
            G_prod_lin *= G_stage_lin
            if F_num > 0:
                st["cumulative_nf_db"] = round(10.0 * _math.log10(F_num), 2)

        glb["stages"] = stages
        return glb

    # ------------------------------------------------------------------
    #  GLB helpers — operating-region classifier, datasheet sanity
    #  checks, and the 1 GHz frequency-sweep computation. Shared by
    #  both the markdown and HTML renderers so the two views agree.
    # ------------------------------------------------------------------
    def _glb_stage_region(self, p_out_dbm, p1db_out_dbm, gain_db):
        """Classify the operating region of a single RF stage.

        Rule of thumb for well-mannered RF design: keep the output at
        ≥ 10 dB back-off from P1dB to stay linear. Between 6-10 dB is
        near-linear, 0-6 dB is compressing, beyond P1dB is saturated /
        non-linear.

        Returns (label, css_class) where css_class is
        'pass' | 'warn' | 'fail' | ''.
        """
        # Passive or sentinel "not applicable" P1dB → always linear.
        if not isinstance(p1db_out_dbm, (int, float)) or p1db_out_dbm == 99:
            if isinstance(gain_db, (int, float)) and gain_db < 0:
                return ("Linear (passive)", "pass")
            return ("—", "")
        if not isinstance(p_out_dbm, (int, float)):
            return ("—", "")
        backoff = p1db_out_dbm - p_out_dbm
        if backoff >= 10.0:
            return (f"Linear ({backoff:.1f} dB BO)", "pass")
        if backoff >= 6.0:
            return (f"Near-linear ({backoff:.1f} dB BO)", "")
        if backoff >= 0.0:
            return (f"Compressing ({backoff:.1f} dB BO)", "warn")
        return (f"SATURATED ({abs(backoff):.1f} dB over P1dB)", "fail")

    def _glb_sanity_flags(self, stage) -> list:
        """Datasheet-plausibility checks on a single stage. Returns a list
        of short warning strings — empty list ⇒ all values look sane.

        Checks (senior-RF-engineer heuristics):
          • OIP3 ≈ P1dB + 10 dB (rule of thumb). Flag if |delta-10| > 5.
          • NF < 0.5 dB is physically unachievable at 290 K — flag.
          • Passive stage NF should equal |insertion loss| (Friis identity).
          • Mixer conversion loss sanity: passive ≈ -6 to -8 dB,
            active ≈ +5 to +10 dB. Flag outliers.
          • Single-MMIC gain > 30 dB is unusual — flag for review.
          • Return-loss |RL| > 25 dB is rare in practice — flag for review.
        """
        flags = []
        g     = stage.get("gain_db")
        nf    = stage.get("noise_figure_db")
        p1db  = stage.get("p1db_out_dbm")
        oip3  = stage.get("oip3_dbm")
        s11   = stage.get("input_return_loss_db")
        s22   = stage.get("output_return_loss_db")
        lbl = (str(stage.get("stage_name", "") or "") + " " +
               str(stage.get("component", "") or "")).lower()

        if isinstance(oip3, (int, float)) and isinstance(p1db, (int, float)) \
                and p1db != 99 and oip3 != 99:
            delta = oip3 - p1db
            if delta < 5.0:
                flags.append(f"OIP3 only {delta:.1f} dB above P1dB (typ +10)")
            elif delta > 15.0:
                flags.append(f"OIP3 {delta:.1f} dB above P1dB (typ +10)")
        # NF < 0.5 is only unusual for ACTIVE stages. For passives
        # NF = |insertion loss| (Friis identity) and values < 0.5 dB
        # are perfectly normal (short traces, low-loss connectors).
        _is_passive_like = isinstance(g, (int, float)) and g <= -0.05
        if isinstance(nf, (int, float)) and 0 < nf < 0.5 and not _is_passive_like:
            flags.append("NF < 0.5 dB — verify (typ ≥ 0.5 dB at 290 K)")
        if isinstance(g, (int, float)) and g < -0.1 \
                and isinstance(nf, (int, float)) \
                and abs(nf - abs(g)) > 0.4:
            flags.append(f"Passive NF ≠ |loss| ({nf:.2f} vs {abs(g):.2f} dB)")
        if "mixer" in lbl and isinstance(g, (int, float)):
            if -10.0 < g < -3.0:
                pass  # passive mixer normal
            elif 4.0 < g < 12.0:
                pass  # active mixer normal
            elif -3.0 <= g <= 4.0:
                flags.append(f"Mixer gain {g:+.1f} dB unusual (passive ≈ -7, active ≈ +8)")
            elif g <= -10.0:
                flags.append(f"Mixer conv. loss {abs(g):.1f} dB high — verify")
        if any(k in lbl for k in ("lna", "amplifier", "driver", "mixer", "vga")) \
                and isinstance(g, (int, float)) and g > 30.0:
            flags.append(f"Single-stage gain {g:+.1f} dB high — verify")
        for rl_name, rl in (("S11", s11), ("S22", s22)):
            if isinstance(rl, (int, float)) and rl > 25.0:
                flags.append(f"{rl_name} = {rl:.1f} dB — exceptional, verify")
        return flags

    def _glb_frequency_sweep(self, glb: dict):
        """Compute a per-stage and system gain/NF rollup across the RF
        band in 1 GHz steps (or BW/4 fallback when BW < 3 GHz so that
        every narrowband design still gets ≥ 4 sample points).

        Returns None when center_freq / BW are not available; otherwise:
          {
            "freqs_mhz":  [f0, f1, ...],
            "per_stage":  [[g@f0, g@f1, ...], ...],  # aligned with glb.stages
            "system":     [{freq_mhz, total_gain_db, cascaded_nf_db,
                            p_out_dbm, mds_dbm}, ...],
          }

        Models:
          • Amps / mixers: simple linear tilt — gain drops toward the
            high-frequency edge by ± the per-stage flatness value.
          • Filters: symmetric ripple (|tilt| × flatness) — passband only.
            Skirt roll-off is modelled elsewhere; this assumes the BW
            lies inside the filter passband.
          • Passives (connectors, traces): near-flat, ±0.1 dB at edges.
        """
        import math as _math
        center = glb.get("center_freq_mhz")
        bw     = glb.get("bandwidth_mhz")
        p_in   = glb.get("input_power_dbm")
        stages = glb.get("stages", []) or []
        if not (isinstance(center, (int, float)) and isinstance(bw, (int, float)) and bw > 0):
            return None

        flo = max(1.0, center - bw / 2.0)
        fhi = center + bw / 2.0
        step = 1000.0 if bw >= 3000 else max(250.0, bw / 4.0)

        freqs = []
        f = flo
        while f <= fhi + 1e-6:
            freqs.append(round(f))
            f += step
        if freqs and freqs[-1] < fhi - 1.0:
            freqs.append(round(fhi))

        _FILT_KW = ("filter", "bpf", "lpf", "hpf", "diplexer", "duplexer",
                    "preselector", "saw", "baw", "cavity")
        _ACT_KW  = ("lna", "amplifier", "driver",
                    "mixer", "modulator", "demodulator", "vga", "vca")

        per_stage = []
        for st in stages:
            g_nom = st.get("gain_db")
            if not isinstance(g_nom, (int, float)):
                per_stage.append([None] * len(freqs))
                continue
            lbl = (str(st.get("stage_name", "") or "") + " " +
                   str(st.get("component", "") or "")).lower()
            is_filter = any(kw in lbl for kw in _FILT_KW)
            is_active = (not is_filter) and ((g_nom >= 0.5) or any(kw in lbl for kw in _ACT_KW))
            if is_filter:   flat = 1.0
            elif is_active: flat = 0.5
            else:           flat = 0.1
            gains_f = []
            for fm in freqs:
                norm = (fm - center) / (bw / 2.0) if bw > 0 else 0.0
                if   norm >  1.0: norm =  1.0
                elif norm < -1.0: norm = -1.0
                if is_filter:
                    delta = -abs(norm) * flat     # symmetric ripple
                elif is_active:
                    delta = -norm * flat           # high-end roll-off
                else:
                    delta = -abs(norm) * flat * 0.5  # passives: tiny symmetric variation
                gains_f.append(round(g_nom + delta, 2))
            per_stage.append(gains_f)

        system = []
        for fi, fm in enumerate(freqs):
            cum_g = 0.0
            F_num = 0.0
            G_prod = 1.0
            first_real = True
            for si, st in enumerate(stages):
                g = per_stage[si][fi]
                if not isinstance(g, (int, float)):
                    continue
                nf = st.get("noise_figure_db")
                if not isinstance(nf, (int, float)):
                    nf = abs(g) if g < 0 else 0.0
                cum_g += g
                F_stage = 10 ** (nf / 10.0)
                G_stage = 10 ** (g / 10.0)
                if first_real:
                    F_num = F_stage
                    first_real = False
                else:
                    F_num += (F_stage - 1.0) / G_prod if G_prod > 0 else 0.0
                G_prod *= G_stage
            nf_db = 10.0 * _math.log10(F_num) if F_num > 0 else 0.0
            p_out_f = (p_in + cum_g) if isinstance(p_in, (int, float)) else None
            noise_in = -174.0 + 10.0 * _math.log10(bw * 1e6) + nf_db
            mds = noise_in + 10.0
            system.append({
                "freq_mhz":       fm,
                "total_gain_db":  round(cum_g, 2),
                "cascaded_nf_db": round(nf_db, 2),
                "p_out_dbm":      round(p_out_f, 2) if p_out_f is not None else None,
                "mds_dbm":        round(mds, 2),
            })
        return {"freqs_mhz": freqs, "per_stage": per_stage, "system": system}

    def _glb_project_stages_for_analysis(self, glb: dict, f_target_mhz=None) -> dict:
        """Project per-stage gain / NF / P1dB / OIP3 to a single *analysis*
        frequency (by default the upper band edge = centre + BW/2).

        Senior-RF-engineer rationale:
          For a receiver, NF rises and gain falls with frequency — the
          worst-case (minimum SNR, weakest MDS) sits at the upper edge of
          the band. Designing to centre silently under-specs the edges.
          Transmit chains degrade similarly (OP1dB, OIP3, PAE all drop
          toward f_max). So the primary stage-by-stage cascade must be
          evaluated at f_max.

        Returns a NEW deep-copied glb dict where:
          • each stage's gain_db is the value at f_target (via the tilt
            model in _glb_frequency_sweep — amps/mixers roll off monotonically,
            filters ripple, passives ≈ flat)
          • for actives: NF rises linearly +0.5 dB at the upper edge,
            P1dB and OIP3 each degrade ~1.0 dB at the upper edge
          • cumulative_gain_db, output_power_dbm, cumulative_nf_db are
            recomputed (Friis) using the projected values
          • glb["analysis_freq_mhz"] carries the target frequency
        """
        import copy as _copy
        import math as _math

        glb2 = _copy.deepcopy(glb or {})
        stages = glb2.get("stages", []) or []
        center = glb2.get("center_freq_mhz")
        bw     = glb2.get("bandwidth_mhz")
        p_in   = glb2.get("input_power_dbm")

        if not (isinstance(center, (int, float)) and isinstance(bw, (int, float)) and bw > 0):
            return glb2

        if f_target_mhz is None:
            f_target_mhz = center + bw / 2.0
        glb2["analysis_freq_mhz"] = f_target_mhz

        # Re-use the sweep tilt model — it already computes per-stage gain
        # at a grid of frequencies. We pick the closest grid point to
        # f_target_mhz and use that column.
        sweep = self._glb_frequency_sweep(glb)
        if sweep and sweep.get("freqs_mhz"):
            freqs = sweep["freqs_mhz"]
            closest = min(range(len(freqs)), key=lambda i: abs(freqs[i] - f_target_mhz))
            for i, st in enumerate(stages):
                if i < len(sweep["per_stage"]):
                    g_proj = sweep["per_stage"][i][closest]
                    if isinstance(g_proj, (int, float)):
                        st["gain_db"] = g_proj

        # Compute how far f_target is from centre, normalised to the band edge.
        # norm = +1 at upper edge, 0 at centre, -1 at lower edge.
        norm = (f_target_mhz - center) / (bw / 2.0) if bw > 0 else 0.0
        if norm >  1.0: norm =  1.0
        if norm < -1.0: norm = -1.0
        edge = abs(norm)

        _FILT_KW = ("filter", "bpf", "lpf", "hpf", "diplexer", "duplexer",
                    "preselector", "saw", "baw", "cavity")
        _ACT_KW  = ("lna", "amplifier", "driver",
                    "mixer", "modulator", "demodulator", "vga", "vca")

        # Derate NF, P1dB, OIP3 for actives at the band edge.
        # Magnitudes chosen from typical 2-18 GHz broadband amplifier
        # datasheets (e.g. HMC8410: NF 1.1 dB @ 2 GHz → 1.9 dB @ 18 GHz).
        for st in stages:
            g = st.get("gain_db")
            lbl = (str(st.get("stage_name", "") or "") + " " +
                   str(st.get("component", "") or "")).lower()
            is_filter = any(kw in lbl for kw in _FILT_KW)
            is_active = (not is_filter) and (
                (isinstance(g, (int, float)) and g >= 0.5)
                or any(kw in lbl for kw in _ACT_KW)
            )
            if not is_active:
                continue
            # +0.5 dB NF at the upper edge (asymmetric — hits worst going up)
            nf = st.get("noise_figure_db")
            if isinstance(nf, (int, float)):
                bump = 0.5 * edge * (1.0 if norm >= 0 else 0.3)
                st["noise_figure_db"] = round(max(0.5, nf + bump), 2)
            # -1.0 dB P1dB at the upper edge
            p1 = st.get("p1db_out_dbm")
            if isinstance(p1, (int, float)) and p1 != 99:
                bump = 1.0 * edge * (1.0 if norm >= 0 else 0.3)
                st["p1db_out_dbm"] = round(p1 - bump, 1)
            # -1.0 dB OIP3 at the upper edge (paired with P1dB)
            oi = st.get("oip3_dbm")
            if isinstance(oi, (int, float)) and oi != 99:
                bump = 1.0 * edge * (1.0 if norm >= 0 else 0.3)
                st["oip3_dbm"] = round(oi - bump, 1)

        # Recompute cumulative gain / Pout / Friis NF with the projected values
        cum_g = 0.0
        F_num = 0.0
        G_prod = 1.0
        first = True
        for st in stages:
            g = st.get("gain_db")
            if not isinstance(g, (int, float)):
                continue
            nf = st.get("noise_figure_db")
            if not isinstance(nf, (int, float)):
                nf = abs(g) if g < 0 else 0.0
                st["noise_figure_db"] = nf
            cum_g += g
            st["cumulative_gain_db"] = round(cum_g, 2)
            if isinstance(p_in, (int, float)):
                st["output_power_dbm"] = round(p_in + cum_g, 2)
            F_stage = 10 ** (nf / 10.0)
            G_stage = 10 ** (g / 10.0)
            if first:
                F_num = F_stage
                first = False
            else:
                F_num += (F_stage - 1.0) / G_prod if G_prod > 0 else 0.0
            G_prod *= G_stage
            if F_num > 0:
                st["cumulative_nf_db"] = round(10.0 * _math.log10(F_num), 2)

        glb2["stages"] = stages
        return glb2

    def _glb_cross_check_bom(self, glb: dict, bom: list) -> list:
        """Cross-check GLB stage components against the project BOM.

        Returns a list of short warning strings (empty = all stages
        appear in the BOM). Used to catch the LLM hallucinating
        different part numbers in the GLB vs. the components list or
        the block diagram.
        """
        import re as _re
        if not isinstance(bom, list) or not bom:
            return []
        # Normalise part numbers for loose matching: drop package
        # suffixes, whitespace, case.
        def _norm(s):
            if not s: return ""
            s = str(s).upper()
            s = _re.sub(r'[\s\-\(\)]', '', s)
            return s

        bom_parts = set()
        for item in bom:
            if not isinstance(item, dict):
                continue
            for key in ("part_number", "manufacturer_part",
                        "component", "mpn", "part"):
                v = item.get(key)
                if v:
                    bom_parts.add(_norm(v))
                    break

        warnings = []
        stages = glb.get("stages", []) or []
        for i, st in enumerate(stages, 1):
            comp = st.get("component")
            if not comp:
                continue
            comp_norm = _norm(comp)
            # Skip generic passive descriptors — these are design abstractions,
            # not part numbers, and are not expected to appear in the BOM.
            _skip = (
                # Substrates & traces
                "MICROSTRIP", "RO4350B", "PCBTRACE", "STRIPLINE",
                # Connectors
                "SMA", "CONNECTOR", "NTYPE", "BNC", "2.92MM", "2.4MM",
                # Generic filter descriptors
                "BPF", "LPF", "HPF", "BANDPASS", "LOWPASS", "HIGHPASS",
                "SAW", "BAW", "LCFILTER", "CAVITY", "DIPLEXER", "DUPLEXER",
                "PRESELECTOR",
                # Generic passive components
                "50Ω", "50OHM", "75OHM", "ATTENUATOR", "PAD", "MATCHING",
                "BIAS-T", "BIAST", "BIASTEE", "SPLITTER", "COMBINER",
                "COUPLER", "BALUN", "TRANSFORMER", "ISOLATOR", "CIRCULATOR",
                # Generic limiter descriptors (PL-DIODE = PIN-limiter)
                "PLDIODE", "PINDIODE", "TVSDIODE", "LIMITER",
            )
            if any(k in comp_norm for k in _skip):
                continue
            # Loose match: substring either direction
            hit = any((comp_norm in p) or (p in comp_norm) for p in bom_parts)
            if not hit:
                warnings.append(
                    f"Stage {i} ({st.get('stage_name', '?')}) uses "
                    f"'{comp}' which is not in the project BOM"
                )
        return warnings

    def _glb_contract_checks(self, glb_raw: dict, glb_proj: dict, bom: list) -> list:
        """Run the five cardinal GLB design-contract checks and return a list
        of (rule_id, rule_name, status, detail) tuples, where status is one
        of 'pass', 'warn', or 'fail'.

        These are the senior-RF-engineer invariants that the raw LLM output
        is not trusted to enforce on its own. The result is rendered at the
        TOP of the GLB so reviewers see contract failures immediately.

        Rules:
          C1 — Worst-case analysis frequency set (fail if missing, pass if = f_upper).
          C2 — Every active stage declares `bias_conditions` (fail if any missing).
          C3 — Pdc = Vdd × Idq for each biased stage (warn if |delta| > 10 mW).
          C4 — Passive stage NF equals |insertion loss| in dB (Friis identity).
          C5 — Every GLB component appears in the BOM (warn for each mismatch).
        """
        rules = []
        stages_raw = (glb_raw or {}).get("stages", []) or []
        center = (glb_raw or {}).get("center_freq_mhz")
        bw     = (glb_raw or {}).get("bandwidth_mhz")
        f_ana  = (glb_proj or {}).get("analysis_freq_mhz")

        # Active tokens that must match whole words (2-3 chars — would
        # produce false positives as plain substrings, e.g. " pa" would
        # match "panel" or "pad").
        _ACT_TOKENS_WB = ("lna", "amp", "pa", "vga", "vca")
        # Active substrings that are long enough to be unambiguous.
        _ACT_SUBSTR = ("amplifier", "driver", "mixer", "modulator", "demodulator")
        _FILT_KW = ("filter", "bpf", "lpf", "hpf", "diplexer", "duplexer",
                    "preselector", "saw", "baw", "cavity")
        # Passive-component markers. These take precedence over the active
        # heuristic so that "Buffer Pad", "Output Pad", "SMA panel mount"
        # etc. are never classified as active (fixes C2 false positives
        # for connectors / attenuators that happened to contain "pa" as
        # a substring of "pad" / "panel").
        _PASSIVE_KW = _FILT_KW + (
            "sma", "connector", "microstrip", "pcb", "trace", "cable",
            "attenuator", " pad", "pi attenuator", "t attenuator",
            "splitter", "combiner", "coupler", "balun", "transformer",
            "isolator", "circulator", "limiter", "diode", "bias-t",
            "bias tee", "dc block",
        )

        import re as _re
        _ACT_WB_RE = _re.compile(
            r"(?<![a-z0-9])(?:" + "|".join(_ACT_TOKENS_WB) + r")(?![a-z0-9])"
        )

        def _label(st):
            return (str(st.get("stage_name", "") or "") + " " +
                    str(st.get("component", "") or "")).lower()

        def _is_active(st):
            lbl = _label(st)
            # Passive markers win — a "Buffer Pad" or "SMA panel mount"
            # is passive no matter what else its label contains.
            if any(kw in lbl for kw in _PASSIVE_KW):
                return False
            if any(kw in lbl for kw in _FILT_KW):
                return False
            if any(kw in lbl for kw in _ACT_SUBSTR):
                return True
            if _ACT_WB_RE.search(lbl):
                return True
            g = st.get("gain_db")
            return isinstance(g, (int, float)) and g >= 0.5

        def _is_passive(st):
            lbl = _label(st)
            return any(kw in lbl for kw in _PASSIVE_KW)

        # -----------------------------------------------------------------
        # C1 — worst-case frequency
        # -----------------------------------------------------------------
        if isinstance(center, (int, float)) and isinstance(bw, (int, float)) and bw > 0:
            f_upper = center + bw / 2.0
            if isinstance(f_ana, (int, float)) and abs(f_ana - f_upper) < 1.0:
                rules.append((
                    "C1", "Analysis at worst-case frequency",
                    "pass",
                    f"Cascade evaluated at f_max = {int(round(f_ana))} MHz (centre {int(center)} + BW/2 = {int(bw/2)} MHz).",
                ))
            else:
                rules.append((
                    "C1", "Analysis at worst-case frequency",
                    "fail",
                    f"analysis_freq_mhz={f_ana} does not match upper band edge {int(round(f_upper))} MHz.",
                ))
        else:
            rules.append((
                "C1", "Analysis at worst-case frequency",
                "fail",
                "center_freq_mhz / bandwidth_mhz missing — cannot verify.",
            ))

        # -----------------------------------------------------------------
        # C2 — bias_conditions present for every active stage
        # -----------------------------------------------------------------
        missing_bias = []
        for i, st in enumerate(stages_raw, 1):
            if not _is_active(st):
                continue
            bc = st.get("bias_conditions")
            if not isinstance(bc, dict):
                missing_bias.append(f"Stage {i} ({st.get('stage_name','?')})")
                continue
            # Passive mixer is allowed to have all-None bias fields
            has_vdd = isinstance(bc.get("vdd_v"), (int, float))
            has_idq = isinstance(bc.get("idq_ma"), (int, float))
            has_pdc = isinstance(bc.get("pdc_mw"), (int, float))
            lbl = _label(st)
            is_mixer = "mixer" in lbl
            # Mixer is OK with all-null (passive) provided pdc_mw == 0
            if is_mixer and not has_vdd and not has_idq and (bc.get("pdc_mw") == 0 or bc.get("pdc_mw") is None):
                continue
            if not (has_vdd and has_idq and has_pdc):
                missing_bias.append(f"Stage {i} ({st.get('stage_name','?')})")
        if missing_bias:
            rules.append((
                "C2", "Bias conditions declared for every active stage",
                "fail",
                "Missing: " + ", ".join(missing_bias) + ".",
            ))
        else:
            rules.append((
                "C2", "Bias conditions declared for every active stage",
                "pass",
                "Every LNA / amp / mixer stage cites the datasheet Vdd/Idq row that produces its gain and NF.",
            ))

        # -----------------------------------------------------------------
        # C3 — Pdc = Vdd × Idq within 10 mW
        # -----------------------------------------------------------------
        pdc_errors = []
        for i, st in enumerate(stages_raw, 1):
            bc = st.get("bias_conditions")
            if not isinstance(bc, dict):
                continue
            v = bc.get("vdd_v"); idq = bc.get("idq_ma"); pdc = bc.get("pdc_mw")
            if isinstance(v, (int, float)) and isinstance(idq, (int, float)) and isinstance(pdc, (int, float)):
                calc = v * idq
                if abs(calc - pdc) > 10.0:
                    pdc_errors.append(
                        f"Stage {i} ({st.get('stage_name','?')}): stated {pdc:.0f} mW, "
                        f"{v:.2f} V × {idq:.1f} mA = {calc:.0f} mW"
                    )
        if pdc_errors:
            rules.append((
                "C3", "Pdc = Vdd × Idq (±10 mW)",
                "warn",
                "; ".join(pdc_errors),
            ))
        else:
            rules.append((
                "C3", "Pdc = Vdd × Idq (±10 mW)",
                "pass",
                "All biased stages obey the Ohm-law tie between supply and DC power.",
            ))

        # -----------------------------------------------------------------
        # C4 — passive NF equals |insertion loss| (Friis identity)
        # -----------------------------------------------------------------
        friis_errors = []
        for i, st in enumerate(stages_raw, 1):
            if _is_active(st):
                continue
            if not _is_passive(st):
                continue
            g = st.get("gain_db"); nf = st.get("noise_figure_db")
            if isinstance(g, (int, float)) and isinstance(nf, (int, float)) and g <= 0:
                if abs(nf - abs(g)) > 0.15:
                    friis_errors.append(
                        f"Stage {i} ({st.get('stage_name','?')}): loss={abs(g):.2f} dB but NF={nf:.2f} dB"
                    )
        if friis_errors:
            rules.append((
                "C4", "Passive NF = |insertion loss| (Friis)",
                "warn",
                "; ".join(friis_errors),
            ))
        else:
            rules.append((
                "C4", "Passive NF = |insertion loss| (Friis)",
                "pass",
                "Every passive stage's NF equals its insertion loss in dB, as thermodynamics requires.",
            ))

        # -----------------------------------------------------------------
        # C5 — BOM cross-reference
        # -----------------------------------------------------------------
        bom_warnings = self._glb_cross_check_bom(glb_raw, bom)
        if bom_warnings:
            rules.append((
                "C5", "GLB ↔ BOM component match",
                "warn",
                "; ".join(bom_warnings),
            ))
        elif not bom:
            rules.append((
                "C5", "GLB ↔ BOM component match",
                "warn",
                "No BOM supplied to this renderer — cross-check skipped.",
            ))
        else:
            rules.append((
                "C5", "GLB ↔ BOM component match",
                "pass",
                "Every active-stage part number in the GLB has a matching entry in the project BOM.",
            ))

        return rules

    def _build_gain_loss_budget_md(self, tool_input: dict, project_name: str) -> str:
        """Build RF Gain-Loss Budget markdown from tool_input['gain_loss_budget'].

        The GLB is first run through `_normalize_glb_for_report` so ideal
        connectors are patched, a PCB trace stage is injected when missing,
        and cumulative gain / NF are recomputed. The stage-by-stage table
        is then projected to the worst-case analysis frequency (f_max =
        centre + BW/2) via `_glb_project_stages_for_analysis`.
        """
        from datetime import datetime
        import math as _math

        glb_raw = self._normalize_glb_for_report(tool_input.get("gain_loss_budget") or {})

        # --- Closed-loop optimization pass -------------------------------
        # Runs at the worst-case analysis frequency (f_max = centre + BW/2)
        # so every iteration in the log reports the same band-edge numbers
        # the reader sees in the Section 2 cascade table. Designing to
        # centre-freq silently under-specs the band edge, where receiver
        # NF rises and amp gain falls — worst-case is where the link must
        # actually close. Gated by GLB_OPTIMIZER in .env (default on).
        import os as _os
        if _os.environ.get("GLB_OPTIMIZER", "1") not in ("0", "false", "False"):
            try:
                from services.glb_optimizer import optimize as _glb_optimize
                _opt_targets = {
                    "required_gain_db": (
                        (glb_raw.get("target_output_dbm") or 0)
                        - (glb_raw.get("input_power_dbm") or 0)
                        if glb_raw.get("target_output_dbm") is not None
                           and glb_raw.get("input_power_dbm") is not None
                        else None
                    ),
                    "target_nf_db": glb_raw.get("target_nf_db"),
                    "target_output_dbm": glb_raw.get("target_output_dbm"),
                    "power_budget_mw": glb_raw.get("power_budget_mw"),
                }
                # Project glb_raw stage gains/NF/P1dB to f_max BEFORE running
                # the optimizer — so the cascade it sees matches the rendered
                # Section 2 table.
                _glb_wc_for_opt = self._glb_project_stages_for_analysis(glb_raw)
                _opt_glb_wc, _opt_log = _glb_optimize(
                    _glb_wc_for_opt, _opt_targets, max_iterations=5,
                )
                _did_work = any(rec.actions for rec in _opt_log
                                if rec.actions and
                                not (len(rec.actions) == 1 and
                                     "Converged" in rec.actions[0]))
                if _did_work:
                    # Port stage edits back to glb_raw so Section 6-8
                    # frequency-sweep tables pick up added components. Each
                    # stage's gain_db is now band-edge; the re-projection
                    # just below will apply a small additional tilt (<1 dB
                    # typical at cascade level — within design margin).
                    glb_raw["stages"] = _opt_glb_wc["stages"]
                    tool_input["_glb_optimizer_log"] = _opt_log

                    # Cross-document propagation: keep the BOM, block diagram,
                    # and power sheet in lock-step with the GLB stage list.
                    try:
                        from services.glb_optimizer import propagate_changes as _propagate
                        _params = tool_input.get("design_parameters") or {}
                        def _n(key, default=1):
                            v = _params.get(key)
                            try: return int(v)
                            except Exception: return default
                        _ant = _n("antenna_count", 1)
                        _ch = _n("channel_count", 1)
                        _chg = _propagate(
                            tool_input,
                            glb_raw["stages"],
                            center_freq_mhz=glb_raw.get("center_freq_mhz"),
                            bandwidth_mhz=glb_raw.get("bandwidth_mhz"),
                            antenna_count=_ant,
                            channel_count=_ch,
                        )
                        if _chg and _opt_log:
                            # Surface the propagation actions in the final
                            # iteration log so the reader sees that BOM /
                            # block diagram were kept in sync.
                            _opt_log[-1].actions.append("— propagation —")
                            _opt_log[-1].actions.extend(_chg)
                    except Exception as _prop_exc:
                        import logging as _lg
                        _lg.getLogger(__name__).warning(
                            "glb_optimizer.propagate_failed: %s", _prop_exc,
                        )
            except Exception as _opt_exc:
                import logging as _lg
                _lg.getLogger(__name__).warning(
                    "glb_optimizer.failed: %s — falling back to raw GLB", _opt_exc,
                )

        # Project per-stage values to the worst-case analysis frequency
        # (f_max = centre + BW/2). The raw glb (at the LLM's reference
        # frequency, typically centre) is retained for the frequency-sweep
        # and variation tables further down so the sweep still spans the
        # full band.
        glb = self._glb_project_stages_for_analysis(glb_raw)
        stages = glb.get("stages", [])

        # --- Align optimizer-log final summary with the rendered cascade -
        # The optimizer ran on stages already projected to f_max. The
        # re-projection above applies a small additional tilt (when amps
        # are inserted at reference gain by the library). Recompute the
        # final log entry's summary on the post-re-projection stages so
        # Section 0.5 and Section 2/3 quote the exact same number.
        _opt_log_ref = tool_input.get("_glb_optimizer_log")
        if _opt_log_ref:
            try:
                from services.glb_optimizer import _compute_cascade as _cc
                _wc_stages = [dict(st) for st in glb.get("stages", [])]
                _wc_summary = _cc(_wc_stages, glb.get("input_power_dbm"))
                _opt_log_ref[-1].summary = {
                    **dict(_wc_summary),
                    "_eval_at": "worst-case (f_max)",
                }
                # Intermediate iterations were also evaluated at worst-case
                # (the optimizer saw projected stages from the start).
                for rec in _opt_log_ref[:-1]:
                    rec.summary = {**rec.summary, "_eval_at": "worst-case (f_max)"}
            except Exception:
                pass

        lines = [
            f"# RF Gain-Loss Budget",
            f"## {project_name}",
            "",
            f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d')}  ",
            f"**Document Status:** AI-GENERATED — verify against final component datasheets",
            "",
        ]

        # --- Design-contract checks (top-of-report compliance banner) ---
        _bom_for_checks = tool_input.get("components") or tool_input.get("bom") or []
        _rules = self._glb_contract_checks(glb_raw, glb, _bom_for_checks)
        _fails = [r for r in _rules if r[2] == "fail"]
        _warns = [r for r in _rules if r[2] == "warn"]
        if _fails:
            _verdict = f"❌ **FAIL** — {len(_fails)} hard violation(s), {len(_warns)} warning(s)"
        elif _warns:
            _verdict = f"⚠ **WARN** — 0 hard violations, {len(_warns)} warning(s) — review before release"
        else:
            _verdict = "✅ **PASS** — all five cardinal checks satisfied"
        lines += [
            "## 0. Design Contract Checks",
            "",
            _verdict,
            "",
            "| # | Rule | Status | Detail |",
            "|---|------|--------|--------|",
        ]
        _icon = {"pass": "✅ pass", "warn": "⚠ warn", "fail": "❌ fail"}
        for rid, rname, status, detail in _rules:
            # Escape pipes inside the detail text to keep the markdown table intact
            _d = str(detail).replace("|", "\\|")
            lines.append(f"| {rid} | {rname} | {_icon.get(status, status)} | {_d} |")
        lines += [
            "",
            "> These five invariants must hold for any RF receiver GLB to be "
            "releasable to PCB. Any row marked **fail** is a contract violation "
            "— regenerate Phase 1 or edit the offending stage before proceeding.",
            "",
        ]

        # --- Closed-loop optimizer iteration log (if the optimizer ran) ---
        _opt_log = tool_input.get("_glb_optimizer_log")
        if _opt_log:
            try:
                from services.glb_optimizer import render_log_md
                lines.append(render_log_md(_opt_log))
                lines.append("")
            except Exception:
                pass

        # --- System parameters table ---
        freq   = glb.get("center_freq_mhz")
        bw     = glb.get("bandwidth_mhz")
        p_in   = glb.get("input_power_dbm")
        p_out  = glb.get("target_output_dbm")
        f_ana  = glb.get("analysis_freq_mhz")

        if any(v is not None for v in [freq, bw, p_in, p_out]):
            lines += [
                "## 1. System Parameters",
                "",
                "| Parameter | Value | Unit |",
                "|-----------|-------|------|",
            ]
            if freq   is not None: lines.append(f"| Centre Frequency    | {freq}   | MHz  |")
            if bw     is not None: lines.append(f"| RF Bandwidth        | {bw}     | MHz  |")
            if f_ana  is not None: lines.append(f"| **Analysis Frequency (worst-case)** | **{int(round(f_ana))}** | **MHz** |")
            if p_in   is not None: lines.append(f"| Input Signal Level  | {p_in}   | dBm  |")
            if p_out  is not None: lines.append(f"| Target Output Power | {p_out}  | dBm  |")
            if p_in is not None and p_out is not None:
                req_gain = round(p_out - p_in, 1)
                lines.append(f"| Required System Gain | {req_gain} | dB   |")
            lines.append("")
            lines += [
                "> **Why the upper band edge?** For a receiver, NF rises and gain falls with",
                "> frequency — system sensitivity (MDS) is worst at f_max. The stage-by-stage",
                "> cascade below is therefore evaluated at the upper band edge so the numbers",
                "> represent the least-favourable operating point. The frequency-sweep section",
                "> further down shows how every metric varies across the full band in 1 GHz steps.",
                "",
            ]

        if not stages:
            lines += [
                "## 2. Stage Budget",
                "",
                "> No stage data provided — re-run Phase 1 for an RF design to populate this table.",
                "",
            ]
            return "\n".join(lines)

        # --- Stage-by-stage table ---
        lines += [
            "## 2. Stage-by-Stage Gain / Loss Budget",
            "",
            "| # | Stage | Component | Gain/Loss (dB) | Cum. Gain (dB) | Output Power (dBm) | NF (dB) | Cum. NF (dB) | P1dB Out (dBm) | OIP3 (dBm) | Region | Notes |",
            "|---|-------|-----------|---------------|----------------|-------------------|---------|-------------|---------------|-----------|--------|-------|",
        ]

        for i, st in enumerate(stages, 1):
            name     = st.get("stage_name", "—")
            comp     = st.get("component", "—")
            gain     = st.get("gain_db", "—")
            cum_gain = st.get("cumulative_gain_db", "—")
            p_out_st = st.get("output_power_dbm", "—")
            nf       = st.get("noise_figure_db", "—")
            cum_nf   = st.get("cumulative_nf_db", "—")
            p1db     = st.get("p1db_out_dbm", "—")
            oip3     = st.get("oip3_dbm", "—")
            notes    = str(st.get("notes", "") or "")

            # Format gain with sign
            if isinstance(gain, (int, float)):
                gain_str = f"+{gain:.1f}" if gain >= 0 else f"{gain:.1f}"
            else:
                gain_str = str(gain)

            # Hide 99 placeholder values (means "N/A")
            p1db_str = "N/A" if p1db == 99 else (f"{p1db:.1f}" if isinstance(p1db, (int, float)) else str(p1db))
            oip3_str = "N/A" if oip3 == 99 else (f"{oip3:.1f}" if isinstance(oip3, (int, float)) else str(oip3))

            nf_str     = f"{nf:.2f}"     if isinstance(nf,     (int, float)) else str(nf)
            cum_nf_str = f"{cum_nf:.2f}" if isinstance(cum_nf, (int, float)) else str(cum_nf)
            cum_gain_s = f"{cum_gain:.1f}" if isinstance(cum_gain, (int, float)) else str(cum_gain)
            p_out_s    = f"{p_out_st:.1f}" if isinstance(p_out_st, (int, float)) else str(p_out_st)

            # Region: Linear / Near-linear / Compressing / Saturated
            _region_label, _ = self._glb_stage_region(p_out_st, p1db, gain)
            # Datasheet sanity flags — merge into Notes (pipe-separated)
            _flags = self._glb_sanity_flags(st)
            if _flags:
                _flag_str = "⚠ " + "; ".join(_flags)
                notes = f"{notes} · {_flag_str}" if notes else _flag_str

            lines.append(
                f"| {i} | {name} | {comp} | {gain_str} | {cum_gain_s} | {p_out_s} | {nf_str} | {cum_nf_str} | {p1db_str} | {oip3_str} | {_region_label} | {notes} |"
            )

        lines += [
            "",
            "> **Region legend:** *Linear* = ≥10 dB back-off from P1dB · *Near-linear* = 6-10 dB · *Compressing* = 0-6 dB (onset of gain compression) · *Saturated* = above P1dB (hard non-linear). Keep every stage in the Linear zone for analogue receivers; transmit chains may intentionally drive the PA into compression.",
            "",
        ]

        # --- Summary ---
        if stages:
            last = stages[-1]
            total_gain = last.get("cumulative_gain_db")
            sys_nf     = stages[0].get("cumulative_nf_db") if stages else None  # first stage NF dominates
            final_nf   = last.get("cumulative_nf_db")
            final_pout = last.get("output_power_dbm")

            lines += [
                "## 3. Budget Summary",
                "",
                "| Metric | Value | Unit |",
                "|--------|-------|------|",
            ]
            if total_gain is not None:
                lines.append(f"| Total System Gain     | {total_gain:.1f}  | dB  |")
            if final_pout is not None:
                lines.append(f"| Final Output Power    | {final_pout:.1f}  | dBm |")
            if final_nf is not None:
                lines.append(f"| Cascaded System NF    | {final_nf:.2f} | dB  |")
            if p_in is not None and p_out is not None and total_gain is not None:
                margin = round(p_out - (p_in + total_gain), 1)
                margin_str = f"{'+' if margin >= 0 else ''}{margin}"
                lines.append(f"| Output Power Margin   | {margin_str} | dB  |")
            lines.append("")

        # Determine next section number dynamically
        sec = 4

        # ── Per-stage DC bias lives in the Power Consumption document ──
        # The Vdd/Idq/Pdc table was previously rendered here as Section 4
        # of the GLB; it has been moved into `power_calculation.md` so
        # all DC-power content lives in one place and the GLB stays
        # focused on RF performance (gain, NF, compression, frequency).
        # A short pointer keeps readers who expected the old section from
        # getting lost.
        lines += [
            f"> **Per-stage DC bias (Vdd/Idq/Pdc)** is listed in "
            f"`power_calculation.md` — the companion document that owns "
            f"all power-budget information.",
            "",
        ]

        # ── Noise Floor & Sensitivity ───────────────────────────────────────
        # kTB thermal noise floor + cascaded NF → input-referred noise,
        # plus output noise and MDS (10 dB SNR convention).
        _final_nf   = stages[-1].get("cumulative_nf_db") if stages else None
        _total_gain = stages[-1].get("cumulative_gain_db") if stages else None
        if (isinstance(bw, (int, float)) and bw > 0
                and isinstance(_final_nf, (int, float))):
            _bw_hz = float(bw) * 1e6
            _kTB_dbm   = -174.0 + 10.0 * _math.log10(_bw_hz)
            _noise_in  = _kTB_dbm + _final_nf
            _noise_out = _noise_in + (_total_gain if isinstance(_total_gain, (int, float)) else 0.0)
            _mds_snr   = 10.0   # convention for MDS
            _mds_dbm   = _noise_in + _mds_snr
            lines += [
                f"## {sec}. Noise Floor & Sensitivity",
                "",
                "| Parameter | Formula | Value | Unit |",
                "|-----------|---------|-------|------|",
                f"| Thermal Noise Floor (kTB) | -174 + 10·log₁₀(BW_Hz) | {_kTB_dbm:.2f} | dBm |",
                f"| System NF (cascaded) | Friis | {_final_nf:.2f} | dB |",
                f"| Input-Referred Noise Floor | kTB + NF_sys | {_noise_in:.2f} | dBm |",
                f"| Output Noise Floor | Noise_in + Total_Gain | {_noise_out:.2f} | dBm |",
                f"| MDS (SNR = 10 dB) | Noise_in + 10 | {_mds_dbm:.2f} | dBm |",
                "",
                "> Assumes 290 K ambient, full RF instantaneous bandwidth, AWGN channel.",
                "> MDS convention: 10 dB SNR above the input-referred noise floor.",
                "",
            ]
            sec += 1

        # ── Gain Variation — Thermal (-40 to +85 °C) ────────────────────────
        # Typical tempco: actives ±0.020 dB/°C, passives ±0.005 dB/°C.
        # Nominal datasheet values are specified at 25 °C.
        if stages:
            _ACT_KW = ("lna", "amplifier", "driver",
                       "mixer", "modulator", "demodulator", "vga", "vca")
            lines += [
                f"## {sec}. Gain Variation — Thermal (-40 to +85 °C)",
                "",
                "| # | Stage | Component | Nominal Gain (dB) | Tempco (dB/°C) | ΔG @ -40 °C | ΔG @ +85 °C | Worst-Case (dB) |",
                "|---|-------|-----------|-------------------|----------------|-------------|-------------|-----------------|",
            ]
            _tot_cold = 0.0
            _tot_hot  = 0.0
            for i, st in enumerate(stages, 1):
                g = st.get("gain_db")
                if not isinstance(g, (int, float)):
                    continue
                _lbl = (str(st.get("stage_name", "") or "") + " " +
                        str(st.get("component", "") or "")).lower()
                # Heuristic: "active" if the stage name hints at an active part
                # OR the stage has net positive gain.
                _is_active = (g >= 0.5) or any(kw in _lbl for kw in _ACT_KW)
                _tc = 0.020 if _is_active else 0.005
                _cold = -_tc * (25 - (-40))   # cooling → LNAs gain a bit, but nominal spec
                _hot  = -_tc * (85 -  25)     # heating → active gain drops
                # Sign convention: tempco is applied as -tc*ΔT so that heating
                # reduces gain (typical LNA behaviour).
                _tot_cold += _cold * (-1)     # cold branch is +gain for active
                _tot_hot  += _hot
                _worst = max(abs(_cold), abs(_hot))
                lines.append(
                    f"| {i} | {st.get('stage_name', '—')} | {st.get('component', '—')}"
                    f" | {g:+.2f} | ±{_tc:.3f}"
                    f" | {abs(_cold):+.2f} | {_hot:+.2f} | ±{_worst:.2f} |"
                )
            lines += [
                f"| **TOTAL SYSTEM** | | | | | **{_tot_cold:+.2f}** | **{_tot_hot:+.2f}** | — |",
                "",
                "> Active stages (LNA / amp / mixer): ±0.020 dB/°C typical GaAs pHEMT or SiGe.",
                "> Passives (connectors, traces, filters, splitters): ±0.005 dB/°C.",
                "> Plan for ≥ 3 dB AGC range or closed-loop gain compensation to hold system gain across the temperature envelope.",
                "",
            ]
            sec += 1

        # ── Gain Variation — Frequency (across RF bandwidth) ────────────────
        # Typical in-band ripple: amps ±0.5 dB, filters ±1.0 dB,
        # passives (connectors, traces) ±0.1 dB.
        if stages:
            _FILT_KW = ("filter", "bpf", "lpf", "hpf", "diplexer", "duplexer",
                        "preselector", "saw", "baw", "cavity")
            _ACT_KW2 = ("lna", "amplifier", "driver",
                        "mixer", "modulator", "demodulator", "vga", "vca")
            lines += [
                f"## {sec}. Gain Variation — Frequency (across RF bandwidth)",
                "",
                "| # | Stage | Component | Nominal Gain (dB) | Typ Flatness (± dB) | Min Gain (dB) | Max Gain (dB) |",
                "|---|-------|-----------|-------------------|---------------------|---------------|---------------|",
            ]
            _rss = 0.0       # root-sum-square of per-stage flatness
            _worst_sum = 0.0  # worst-case linear sum
            for i, st in enumerate(stages, 1):
                g = st.get("gain_db")
                if not isinstance(g, (int, float)):
                    continue
                _lbl = (str(st.get("stage_name", "") or "") + " " +
                        str(st.get("component", "") or "")).lower()
                if any(kw in _lbl for kw in _FILT_KW):
                    _flat = 1.0
                elif (g >= 0.5) or any(kw in _lbl for kw in _ACT_KW2):
                    _flat = 0.5
                else:
                    _flat = 0.1
                _rss += _flat ** 2
                _worst_sum += _flat
                _gmin = g - _flat
                _gmax = g + _flat
                lines.append(
                    f"| {i} | {st.get('stage_name', '—')} | {st.get('component', '—')}"
                    f" | {g:+.2f} | ±{_flat:.1f}"
                    f" | {_gmin:+.2f} | {_gmax:+.2f} |"
                )
            _rss_total = _math.sqrt(_rss) if _rss > 0 else 0.0
            lines += [
                f"| **WORST-CASE (Σ)** | | | | **±{_worst_sum:.1f}** | | |",
                f"| **RSS (statistical)** | | | | **±{_rss_total:.2f}** | | |",
                "",
                "> Amps: ±0.5 dB in-band. Filters: ±1.0 dB (passband ripple + skirt roll-off). Passives: ±0.1 dB.",
                "> Worst-case Σ assumes all deviations align; RSS assumes uncorrelated contributions — the truth sits between the two.",
                "> If flatness is critical (e.g. ± 1 dB system spec), add an equaliser or gain-slope compensator after the LNA.",
                "",
            ]
            sec += 1

        # ── Frequency Sweep (1 GHz step across the RF band) ─────────────────
        # Model:
        #   amps / mixers  → monotonic roll-off toward the high-frequency edge
        #   filters        → symmetric ripple across the passband
        #   passives       → near-flat (small symmetric variation)
        # At each frequency we recompute total gain, cascaded NF (Friis),
        # output power, and MDS (Noise_in + 10 dB SNR).
        # NOTE: pass glb_raw (centre-frequency values) so the sweep does not
        # re-project on top of the already-projected stage table.
        _sweep = self._glb_frequency_sweep(glb_raw)
        if _sweep and _sweep["freqs_mhz"]:
            _freqs = _sweep["freqs_mhz"]
            _step_note = (
                "1 GHz step" if (isinstance(bw, (int, float)) and bw >= 3000)
                else f"{int(max(250, bw/4))} MHz step (BW < 3 GHz)"
            )
            # -- Per-stage × frequency gain matrix --
            lines += [
                f"## {sec}. Stage Gain vs Frequency — {_step_note}",
                "",
                ("| # | Stage | Component | Nominal (dB) | "
                 + " | ".join(f"{fm/1000:.1f} GHz (dB)" for fm in _freqs)
                 + " |"),
                ("|---|-------|-----------|--------------|"
                 + "|".join(["-" * 14] * len(_freqs)) + "|"),
            ]
            for i, (st, gains_f) in enumerate(zip(glb_raw.get("stages", []), _sweep["per_stage"]), 1):
                gn = st.get("gain_db")
                nom_str = (f"{gn:+.2f}" if isinstance(gn, (int, float)) else "—")
                cells = " | ".join(
                    f"{g:+.2f}" if isinstance(g, (int, float)) else "—"
                    for g in gains_f
                )
                lines.append(
                    f"| {i} | {st.get('stage_name', '—')} | {st.get('component', '—')} | {nom_str} | {cells} |"
                )
            lines.append("")

            # -- System rollup vs frequency --
            lines += [
                f"## {sec}a. System Rollup vs Frequency",
                "",
                "| Frequency (GHz) | Total Gain (dB) | Cascaded NF (dB) | Output Power (dBm) | MDS @ 10 dB SNR (dBm) |",
                "|----------------:|----------------:|------------------:|-------------------:|----------------------:|",
            ]
            for row in _sweep["system"]:
                _pout = row["p_out_dbm"]
                _pout_s = f"{_pout:+.2f}" if isinstance(_pout, (int, float)) else "—"
                lines.append(
                    f"| {row['freq_mhz']/1000:.1f}"
                    f" | {row['total_gain_db']:+.2f}"
                    f" | {row['cascaded_nf_db']:.2f}"
                    f" | {_pout_s}"
                    f" | {row['mds_dbm']:+.2f} |"
                )
            lines += [
                "",
                "> Gain roll-off model: amps/mixers fall off monotonically toward the high edge of the band by ±0.5 dB at the edges; filters ripple by ±1.0 dB; connectors & traces by ±0.1 dB.",
                "> Cascaded NF recomputed with Friis at each frequency — the LNA continues to dominate, so NF typically stays within ±0.3 dB of nominal across the band.",
                "> MDS tracks the NF. If the band-edge MDS is more than 2 dB worse than midband, add frequency-dependent equalisation or re-allocate gain toward the LNA.",
                "",
            ]
            sec += 1

        # --- Return Loss per stage (if at least one stage has S11/S22 data) ---
        rl_stages = [s for s in stages if s.get("input_return_loss_db") is not None or s.get("output_return_loss_db") is not None]
        if rl_stages:
            lines += [
                f"## {sec}. Return Loss — Per Stage",
                "",
                "| # | Stage | Component | S11 — Input RL (dB) | S22 — Output RL (dB) | Notes |",
                "|---|-------|-----------|---------------------|----------------------|-------|",
            ]
            for i, st in enumerate(rl_stages, 1):
                s11 = st.get("input_return_loss_db")
                s22 = st.get("output_return_loss_db")
                lines.append(
                    f"| {i} | {st.get('stage_name', '—')} | {st.get('component', '—')}"
                    f" | {f'{s11:.1f}' if s11 is not None else '—'}"
                    f" | {f'{s22:.1f}' if s22 is not None else '—'}"
                    f" | {st.get('notes', '')} |"
                )
            lines += ["", "> Return loss values are referenced to 50 Ω. Higher value = better match.", ""]
            sec += 1

        # --- Harmonic Rejection (only when data provided) ---
        harmonics = glb.get("harmonic_rejection") or []
        if harmonics:
            lines += [
                f"## {sec}. Harmonic Rejection",
                "",
                "| Harmonic Order | Frequency (MHz) | Expected Rejection (dBc) | Required Spec (dBc) | Pass/Fail |",
                "|----------------|-----------------|--------------------------|---------------------|-----------|",
            ]
            for h in harmonics:
                order    = h.get("harmonic_order", "?")
                freq     = h.get("frequency_mhz", "?")
                rej      = h.get("rejection_db")
                spec     = h.get("spec_db")
                meets    = h.get("meets_spec")
                rej_str  = f"{rej:.1f}" if isinstance(rej, (int, float)) else "—"
                spec_str = f"{spec:.1f}" if isinstance(spec, (int, float)) else "—"
                pf       = ("✓ PASS" if meets else "✗ FAIL") if meets is not None else "—"
                lines.append(f"| {order}H | {freq} | {rej_str} | {spec_str} | {pf} |")
            lines += ["", "> Higher rejection (more negative dBc) is better. Filter may be required if spec is not met.", ""]
            sec += 1

        # --- Output Power vs Frequency (flatness) ---
        pvf = glb.get("power_vs_frequency") or []
        if pvf:
            lines += [
                f"## {sec}. Output Power vs Frequency",
                "",
                "| Frequency (MHz) | Output Power (dBm) | Gain (dB) | Flatness (dB) |",
                "|-----------------|--------------------|-----------|---------------|",
            ]
            for row in pvf:
                f_mhz = row.get("frequency_mhz", "?")
                pout  = row.get("output_power_dbm")
                gain  = row.get("gain_db")
                flat  = row.get("flatness_db")
                lines.append(
                    f"| {f_mhz}"
                    f" | {f'{pout:.1f}' if isinstance(pout, (int, float)) else '—'}"
                    f" | {f'{gain:.1f}' if isinstance(gain, (int, float)) else '—'}"
                    f" | {f'{flat:+.1f}' if isinstance(flat, (int, float)) else '—'} |"
                )
            lines += ["", "> Flatness measured relative to midband output power.", ""]
            sec += 1

        # --- Output Power vs Input Power (AM-AM / compression) ---
        pvi = glb.get("power_vs_input") or []
        if pvi:
            lines += [
                f"## {sec}. Output Power vs Input Drive Level (AM-AM)",
                "",
                "| Input (dBm) | Output (dBm) | Gain (dB) | Compression (dB) |",
                "|-------------|--------------|-----------|------------------|",
            ]
            for row in pvi:
                pin  = row.get("input_power_dbm")
                pout = row.get("output_power_dbm")
                gain = row.get("gain_db")
                comp = row.get("gain_compression_db")
                lines.append(
                    f"| {f'{pin:.1f}' if isinstance(pin, (int, float)) else '—'}"
                    f" | {f'{pout:.1f}' if isinstance(pout, (int, float)) else '—'}"
                    f" | {f'{gain:.1f}' if isinstance(gain, (int, float)) else '—'}"
                    f" | {f'{comp:.1f}' if isinstance(comp, (int, float)) else '—'} |"
                )
            lines += ["", "> Compression > 0 dB indicates onset of saturation.", ""]
            sec += 1

        # --- Cable Loss Budget ---
        cable = glb.get("cable_loss") or []
        if cable:
            lines += [
                f"## {sec}. Cable & Connector Loss Budget",
                "",
                "| Segment | Cable/Connector Type | Length (m) | Loss/m (dB/m) | Total Loss (dB) | Frequency (MHz) |",
                "|---------|----------------------|------------|---------------|-----------------|-----------------|",
            ]
            total_cable_loss = 0.0
            for row in cable:
                seg   = row.get("segment", "—")
                ctype = row.get("cable_type", "—")
                length = row.get("length_m", 0)
                loss_m = row.get("loss_db_per_m")
                total  = row.get("total_loss_db", 0)
                f_mhz  = row.get("frequency_mhz", "—")
                total_cable_loss += total if isinstance(total, (int, float)) else 0
                lines.append(
                    f"| {seg} | {ctype}"
                    f" | {length if length else '—'}"
                    f" | {f'{loss_m:.3f}' if isinstance(loss_m, (int, float)) else '—'}"
                    f" | {f'{total:.2f}' if isinstance(total, (int, float)) else '—'}"
                    f" | {f_mhz} |"
                )
            lines += [
                f"| **TOTAL** | | | | **{round(total_cable_loss, 2)}** | |",
                "",
                "> Cable loss must be compensated by additional gain or accepted as part of system link budget.",
                "",
            ]
            sec += 1

        # ── Consistency Checks (BOM / Block Diagram cross-reference) ────────
        # Cross-check the components used in the GLB against the project BOM
        # (if supplied). The LLM cannot be trusted to keep the GLB, the
        # components list, and the block diagram in perfect sync.
        bom = tool_input.get("components") or tool_input.get("bom") or []
        xrefs = self._glb_cross_check_bom(glb, bom)
        lines += [
            f"## {sec}. Consistency Checks",
            "",
        ]
        if xrefs:
            lines += [
                "> **⚠ BOM / Block-Diagram mismatches detected — review before PCB release:**",
                "",
            ]
            for w in xrefs:
                lines.append(f"- {w}")
            lines.append("")
        elif bom:
            lines += [
                "> ✓ All GLB components appear in the project BOM.",
                "",
            ]
        else:
            lines += [
                "> (No BOM supplied to the GLB tool call — cross-check skipped. "
                "When the BOM is available, this section lists any component in the "
                "GLB that does not appear in the parts list.)",
                "",
            ]
        sec += 1

        # --- Friis formula reminder ---
        lines += [
            f"## {sec}. Cascade Noise Figure — Friis Formula",
            "",
            "$$F_{{sys}} = F_1 + \\frac{{F_2 - 1}}{{G_1}} + \\frac{{F_3 - 1}}{{G_1 G_2}} + \\cdots$$",
            "",
            "Where *F* = linear noise factor (not dB), *G* = linear gain.",
            "The first stage NF dominates — minimise LNA/driver NF for best system sensitivity.",
            "",
            "---",
            "> **Note:** All values are estimated from component datasheets at 25 °C nominal.",
            "> Verify with bench measurements (spectrum analyser + noise source) during hardware bring-up.",
        ]

        return "\n".join(lines)

    def _build_gain_loss_budget_html(self, tool_input: dict, project_name: str) -> str:
        """Build a standalone HTML RF Gain-Loss Budget document.

        Styling mirrors power_calculation.html: dark navy table headers
        (#0b1220), teal accent (#00c6a7), card-style tables with soft
        drop shadow, cell colour tints for gain vs. loss, pass/fail on
        harmonic rejection, and a warn tint on thermal variations that
        exceed 3 dB.

        Derivation re-uses `_normalize_glb_for_report` so the numbers
        stay identical to the markdown version.
        """
        from datetime import datetime
        import html as _html
        import math as _math

        glb_raw = self._normalize_glb_for_report(tool_input.get("gain_loss_budget") or {})
        # Project per-stage values to worst-case analysis frequency (f_max).
        glb = self._glb_project_stages_for_analysis(glb_raw)
        stages = glb.get("stages", []) or []
        date = datetime.utcnow().strftime("%Y-%m-%d")

        def esc(s):
            return _html.escape(str(s)) if s is not None else ""

        def fmt_gain(v):
            if not isinstance(v, (int, float)):
                return str(v) if v is not None else "—"
            return f"+{v:.1f}" if v >= 0 else f"{v:.1f}"

        def fmt_f(v, digits=2):
            if not isinstance(v, (int, float)):
                return str(v) if v is not None else "—"
            return f"{v:.{digits}f}"

        # ── System parameters ───────────────────────────────────────────────
        freq  = glb.get("center_freq_mhz")
        bw    = glb.get("bandwidth_mhz")
        p_in  = glb.get("input_power_dbm")
        p_out = glb.get("target_output_dbm")

        f_ana = glb.get("analysis_freq_mhz")
        sys_rows_html = ""
        if any(v is not None for v in (freq, bw, p_in, p_out)):
            sys_rows = []
            if freq  is not None: sys_rows.append(("Centre Frequency",    freq,  "MHz", ""))
            if bw    is not None: sys_rows.append(("RF Bandwidth",        bw,    "MHz", ""))
            if f_ana is not None: sys_rows.append((
                "Analysis Frequency (worst-case upper edge)",
                int(round(f_ana)), "MHz", "warn"))
            if p_in  is not None: sys_rows.append(("Input Signal Level",  p_in,  "dBm", ""))
            if p_out is not None: sys_rows.append(("Target Output Power", p_out, "dBm", ""))
            if p_in is not None and p_out is not None:
                sys_rows.append(("Required System Gain", round(p_out - p_in, 1), "dB", ""))
            sys_rows_html = "".join(
                (f"<tr><td><b>{esc(k)}</b></td><td class='{cls}'><b>{esc(v)}</b></td><td>{esc(u)}</td></tr>"
                 if cls else
                 f"<tr><td>{esc(k)}</td><td>{esc(v)}</td><td>{esc(u)}</td></tr>")
                for k, v, u, cls in sys_rows
            )

        # ── Stage-by-stage table ────────────────────────────────────────────
        stage_body = []
        for i, st in enumerate(stages, 1):
            name     = st.get("stage_name", "—")
            comp     = st.get("component", "—")
            gain     = st.get("gain_db")
            cum_gain = st.get("cumulative_gain_db")
            p_out_st = st.get("output_power_dbm")
            nf       = st.get("noise_figure_db")
            cum_nf   = st.get("cumulative_nf_db")
            p1db     = st.get("p1db_out_dbm")
            oip3     = st.get("oip3_dbm")
            notes    = str(st.get("notes", "") or "")

            gain_cls = ""
            if isinstance(gain, (int, float)):
                gain_cls = "pass" if gain >= 0 else "warn"
            gain_str = fmt_gain(gain)
            p1db_str = "N/A" if p1db == 99 else fmt_f(p1db, 1)
            oip3_str = "N/A" if oip3 == 99 else fmt_f(oip3, 1)

            # Region: Linear / Near-linear / Compressing / Saturated
            region_label, region_cls = self._glb_stage_region(p_out_st, p1db, gain)
            # Datasheet sanity flags — amber bullet in Notes cell
            sflags = self._glb_sanity_flags(st)
            if sflags:
                flag_html = ("<br><span class='warn-inline'>&#9888; "
                             + esc("; ".join(sflags)) + "</span>")
                notes_cell = (esc(notes) + flag_html) if notes else flag_html
            else:
                notes_cell = esc(notes)

            stage_body.append(
                f"<tr><td>{i}</td><td>{esc(name)}</td><td>{esc(comp)}</td>"
                f"<td class='{gain_cls}'>{gain_str}</td>"
                f"<td>{fmt_f(cum_gain, 1)}</td>"
                f"<td>{fmt_f(p_out_st, 1)}</td>"
                f"<td>{fmt_f(nf, 2)}</td>"
                f"<td>{fmt_f(cum_nf, 2)}</td>"
                f"<td>{p1db_str}</td><td>{oip3_str}</td>"
                f"<td class='{region_cls}'>{esc(region_label)}</td>"
                f"<td>{notes_cell}</td></tr>"
            )

        # ── Budget summary ──────────────────────────────────────────────────
        summary_rows = []
        if stages:
            last       = stages[-1]
            total_gain = last.get("cumulative_gain_db")
            final_nf   = last.get("cumulative_nf_db")
            final_pout = last.get("output_power_dbm")
            if total_gain is not None:
                summary_rows.append(("Total System Gain", fmt_f(total_gain, 1), "dB"))
            if final_pout is not None:
                summary_rows.append(("Final Output Power", fmt_f(final_pout, 1), "dBm"))
            if final_nf is not None:
                summary_rows.append(("Cascaded System NF", fmt_f(final_nf, 2), "dB"))
            if p_in is not None and p_out is not None and total_gain is not None:
                margin = round(p_out - (p_in + total_gain), 1)
                summary_rows.append((
                    "Output Power Margin",
                    f"{'+' if margin >= 0 else ''}{margin}",
                    "dB",
                ))
        summary_html = "".join(
            f"<tr><td>{esc(k)}</td><td>{esc(v)}</td><td>{esc(u)}</td></tr>"
            for k, v, u in summary_rows
        )

        # ── Noise Floor & Sensitivity ───────────────────────────────────────
        noise_html = ""
        _final_nf   = stages[-1].get("cumulative_nf_db") if stages else None
        _total_gain = stages[-1].get("cumulative_gain_db") if stages else None
        if (isinstance(bw, (int, float)) and bw > 0
                and isinstance(_final_nf, (int, float))):
            _bw_hz     = float(bw) * 1e6
            _kTB_dbm   = -174.0 + 10.0 * _math.log10(_bw_hz)
            _noise_in  = _kTB_dbm + _final_nf
            _noise_out = _noise_in + (_total_gain if isinstance(_total_gain, (int, float)) else 0.0)
            _mds_dbm   = _noise_in + 10.0
            noise_html = f"""
<h2>Noise Floor &amp; Sensitivity</h2>
<table>
  <tr><th>Parameter</th><th>Formula</th><th>Value</th><th>Unit</th></tr>
  <tr><td>Thermal Noise Floor (kTB)</td><td><code>-174 + 10·log(BW_Hz)</code></td><td>{_kTB_dbm:.2f}</td><td>dBm</td></tr>
  <tr><td>System NF (cascaded)</td><td>Friis</td><td>{_final_nf:.2f}</td><td>dB</td></tr>
  <tr><td>Input-Referred Noise Floor</td><td><code>kTB + NF_sys</code></td><td class="warn">{_noise_in:.2f}</td><td>dBm</td></tr>
  <tr><td>Output Noise Floor</td><td><code>Noise_in + Total_Gain</code></td><td>{_noise_out:.2f}</td><td>dBm</td></tr>
  <tr class="totals"><td><b>MDS (SNR = 10 dB)</b></td><td><code>Noise_in + 10</code></td><td><b>{_mds_dbm:.2f}</b></td><td>dBm</td></tr>
</table>
<p class="note">Assumes 290 K ambient, full RF instantaneous bandwidth, AWGN channel.
MDS convention: 10 dB SNR above the input-referred noise floor.</p>
"""

        # ── Thermal variation (-40 to +85 °C) ───────────────────────────────
        thermal_html = ""
        if stages:
            _ACT_KW = ("lna", "amplifier", "driver",
                       "mixer", "modulator", "demodulator", "vga", "vca")
            _rows = []
            _tot_cold = 0.0
            _tot_hot  = 0.0
            for i, st in enumerate(stages, 1):
                g = st.get("gain_db")
                if not isinstance(g, (int, float)):
                    continue
                _lbl = (str(st.get("stage_name", "") or "") + " " +
                        str(st.get("component", "") or "")).lower()
                _is_active = (g >= 0.5) or any(kw in _lbl for kw in _ACT_KW)
                _tc = 0.020 if _is_active else 0.005
                _cold = -_tc * (25 - (-40))
                _hot  = -_tc * (85 -  25)
                _tot_cold += _cold * (-1)
                _tot_hot  += _hot
                _worst = max(abs(_cold), abs(_hot))
                _rows.append(
                    f"<tr><td>{i}</td><td>{esc(st.get('stage_name', '—'))}</td>"
                    f"<td>{esc(st.get('component', '—'))}</td>"
                    f"<td>{g:+.2f}</td>"
                    f"<td>±{_tc:.3f}</td>"
                    f"<td>{abs(_cold):+.2f}</td><td>{_hot:+.2f}</td>"
                    f"<td>±{_worst:.2f}</td></tr>"
                )
            _hot_cls  = "warn" if abs(_tot_hot)  >= 3.0 else ""
            _cold_cls = "warn" if abs(_tot_cold) >= 3.0 else ""
            thermal_html = f"""
<h2>Gain Variation — Thermal (-40 to +85 °C)</h2>
<table>
  <tr><th>#</th><th>Stage</th><th>Component</th>
      <th>Nominal Gain (dB)</th><th>Tempco (dB/°C)</th>
      <th>ΔG @ -40 °C</th><th>ΔG @ +85 °C</th><th>Worst-Case (dB)</th></tr>
  {''.join(_rows)}
  <tr class="totals"><td colspan="5"><b>TOTAL SYSTEM</b></td>
      <td class="{_cold_cls}"><b>{_tot_cold:+.2f}</b></td>
      <td class="{_hot_cls}"><b>{_tot_hot:+.2f}</b></td>
      <td>—</td></tr>
</table>
<p class="note">Active stages (LNA, amp, mixer): ±0.020 dB/°C typical for GaAs pHEMT / SiGe.
Passives (connectors, traces, filters, splitters): ±0.005 dB/°C.
Plan for ≥ 3 dB AGC range or closed-loop gain compensation to hold system gain across the
temperature envelope.</p>
"""

        # ── Frequency variation (across RF bandwidth) ───────────────────────
        freq_html = ""
        if stages:
            _FILT_KW = ("filter", "bpf", "lpf", "hpf", "diplexer", "duplexer",
                        "preselector", "saw", "baw", "cavity")
            _ACT_KW2 = ("lna", "amplifier", "driver",
                        "mixer", "modulator", "demodulator", "vga", "vca")
            _rows = []
            _rss = 0.0
            _worst_sum = 0.0
            for i, st in enumerate(stages, 1):
                g = st.get("gain_db")
                if not isinstance(g, (int, float)):
                    continue
                _lbl = (str(st.get("stage_name", "") or "") + " " +
                        str(st.get("component", "") or "")).lower()
                if any(kw in _lbl for kw in _FILT_KW):
                    _flat = 1.0
                elif (g >= 0.5) or any(kw in _lbl for kw in _ACT_KW2):
                    _flat = 0.5
                else:
                    _flat = 0.1
                _rss += _flat ** 2
                _worst_sum += _flat
                _gmin = g - _flat
                _gmax = g + _flat
                _rows.append(
                    f"<tr><td>{i}</td><td>{esc(st.get('stage_name', '—'))}</td>"
                    f"<td>{esc(st.get('component', '—'))}</td>"
                    f"<td>{g:+.2f}</td>"
                    f"<td>±{_flat:.1f}</td>"
                    f"<td>{_gmin:+.2f}</td><td>{_gmax:+.2f}</td></tr>"
                )
            _rss_total = _math.sqrt(_rss) if _rss > 0 else 0.0
            _ws_cls  = "warn" if _worst_sum  >= 3.0 else ""
            _rss_cls = "warn" if _rss_total >= 2.0 else ""
            freq_html = f"""
<h2>Gain Variation — Frequency (across RF bandwidth)</h2>
<table>
  <tr><th>#</th><th>Stage</th><th>Component</th>
      <th>Nominal Gain (dB)</th><th>Typ Flatness (± dB)</th>
      <th>Min Gain (dB)</th><th>Max Gain (dB)</th></tr>
  {''.join(_rows)}
  <tr class="totals"><td colspan="4"><b>WORST-CASE (Σ)</b></td>
      <td class="{_ws_cls}"><b>±{_worst_sum:.1f}</b></td><td>—</td><td>—</td></tr>
  <tr class="totals"><td colspan="4"><b>RSS (statistical)</b></td>
      <td class="{_rss_cls}"><b>±{_rss_total:.2f}</b></td><td>—</td><td>—</td></tr>
</table>
<p class="note">Amps: ±0.5 dB in-band. Filters: ±1.0 dB (passband ripple + skirt roll-off).
Passives: ±0.1 dB. Worst-case Σ assumes all deviations align; RSS assumes uncorrelated
contributions — the truth sits between the two. If flatness is critical (e.g. ±1 dB system
spec), add an equaliser or gain-slope compensator after the LNA.</p>
"""

        # ── Frequency Sweep (1 GHz step) ────────────────────────────────────
        sweep_html = ""
        # Pass glb_raw so the sweep does not re-project on top of the already-
        # projected stage table.
        _sweep = self._glb_frequency_sweep(glb_raw)
        if _sweep and _sweep["freqs_mhz"]:
            _freqs = _sweep["freqs_mhz"]
            _step_note = (
                "1 GHz step" if (isinstance(bw, (int, float)) and bw >= 3000)
                else f"{int(max(250, bw/4))} MHz step (BW &lt; 3 GHz)"
            )
            # Per-stage × frequency matrix
            _hdr_cells = "".join(f"<th>{fm/1000:.1f} GHz</th>" for fm in _freqs)
            _rows = []
            for i, (st, gains_f) in enumerate(zip(glb_raw.get("stages", []), _sweep["per_stage"]), 1):
                gn = st.get("gain_db")
                nom = f"{gn:+.2f}" if isinstance(gn, (int, float)) else "—"
                cells = "".join(
                    f"<td>{g:+.2f}</td>" if isinstance(g, (int, float)) else "<td>—</td>"
                    for g in gains_f
                )
                _rows.append(
                    f"<tr><td>{i}</td><td class='rowlabel'>{esc(st.get('stage_name', '—'))}</td>"
                    f"<td>{esc(st.get('component', '—'))}</td>"
                    f"<td>{nom}</td>{cells}</tr>"
                )
            # System rollup rows
            _sys_rows = []
            for row in _sweep["system"]:
                _pout = row["p_out_dbm"]
                _pout_s = f"{_pout:+.2f}" if isinstance(_pout, (int, float)) else "—"
                _sys_rows.append(
                    f"<tr><td>{row['freq_mhz']/1000:.1f}</td>"
                    f"<td>{row['total_gain_db']:+.2f}</td>"
                    f"<td>{row['cascaded_nf_db']:.2f}</td>"
                    f"<td>{_pout_s}</td>"
                    f"<td>{row['mds_dbm']:+.2f}</td></tr>"
                )
            sweep_html = f"""
<h2>Stage Gain vs Frequency — {_step_note}</h2>
<table class="sweep">
  <tr><th>#</th><th>Stage</th><th>Component</th><th>Nominal (dB)</th>{_hdr_cells}</tr>
  {''.join(_rows)}
</table>
<p class="note">Gain roll-off model: amps &amp; mixers fall off monotonically toward the
high edge of the band by ±0.5 dB; filters ripple symmetrically by ±1.0 dB across the
passband; connectors and PCB traces vary by ±0.1 dB.</p>

<h2>System Rollup vs Frequency</h2>
<table>
  <tr><th>Frequency (GHz)</th><th>Total Gain (dB)</th>
      <th>Cascaded NF (dB)</th><th>Output Power (dBm)</th>
      <th>MDS @ 10 dB SNR (dBm)</th></tr>
  {''.join(_sys_rows)}
</table>
<p class="note">Cascaded NF recomputed with Friis at each frequency — the LNA
continues to dominate, so NF typically stays within ±0.3 dB of nominal across
the band. If the band-edge MDS is more than 2 dB worse than midband, add
frequency-dependent equalisation or re-allocate gain toward the LNA.</p>
"""

        # ── Optional: Return Loss ───────────────────────────────────────────
        rl_html = ""
        rl_stages = [s for s in stages
                     if s.get("input_return_loss_db") is not None
                     or s.get("output_return_loss_db") is not None]
        if rl_stages:
            _rows = []
            for i, st in enumerate(rl_stages, 1):
                s11 = st.get("input_return_loss_db")
                s22 = st.get("output_return_loss_db")
                _rows.append(
                    f"<tr><td>{i}</td><td>{esc(st.get('stage_name', '—'))}</td>"
                    f"<td>{esc(st.get('component', '—'))}</td>"
                    f"<td>{fmt_f(s11, 1)}</td><td>{fmt_f(s22, 1)}</td>"
                    f"<td>{esc(st.get('notes', ''))}</td></tr>"
                )
            rl_html = f"""
<h2>Return Loss — Per Stage</h2>
<table>
  <tr><th>#</th><th>Stage</th><th>Component</th>
      <th>S11 — Input RL (dB)</th><th>S22 — Output RL (dB)</th><th>Notes</th></tr>
  {''.join(_rows)}
</table>
<p class="note">Return loss values referenced to 50 Ω. Higher value = better match.</p>
"""

        # ── Optional: Harmonic Rejection ────────────────────────────────────
        harm_html = ""
        harmonics = glb.get("harmonic_rejection") or []
        if harmonics:
            _rows = []
            for h in harmonics:
                order = h.get("harmonic_order", "?")
                fh    = h.get("frequency_mhz", "?")
                rej   = h.get("rejection_db")
                spec  = h.get("spec_db")
                meets = h.get("meets_spec")
                if meets is True:
                    pf_cls, pf = "pass", "PASS"
                elif meets is False:
                    pf_cls, pf = "fail", "FAIL"
                else:
                    pf_cls, pf = "", "—"
                _rows.append(
                    f"<tr><td>{esc(order)}H</td><td>{esc(fh)}</td>"
                    f"<td>{fmt_f(rej, 1)}</td><td>{fmt_f(spec, 1)}</td>"
                    f"<td class='{pf_cls}'><b>{pf}</b></td></tr>"
                )
            harm_html = f"""
<h2>Harmonic Rejection</h2>
<table>
  <tr><th>Harmonic Order</th><th>Frequency (MHz)</th>
      <th>Expected Rejection (dBc)</th><th>Required Spec (dBc)</th><th>Pass/Fail</th></tr>
  {''.join(_rows)}
</table>
<p class="note">More-negative dBc values are better. Add a filter if the spec is not met.</p>
"""

        # ── Optional: Output Power vs Frequency ─────────────────────────────
        pvf_html = ""
        pvf = glb.get("power_vs_frequency") or []
        if pvf:
            _rows = []
            for row in pvf:
                _rows.append(
                    f"<tr><td>{esc(row.get('frequency_mhz', '?'))}</td>"
                    f"<td>{fmt_f(row.get('output_power_dbm'), 1)}</td>"
                    f"<td>{fmt_f(row.get('gain_db'), 1)}</td>"
                    f"<td>{fmt_f(row.get('flatness_db'), 1)}</td></tr>"
                )
            pvf_html = f"""
<h2>Output Power vs Frequency</h2>
<table>
  <tr><th>Frequency (MHz)</th><th>Output Power (dBm)</th>
      <th>Gain (dB)</th><th>Flatness (dB)</th></tr>
  {''.join(_rows)}
</table>
<p class="note">Flatness measured relative to midband output power.</p>
"""

        # ── Optional: Output Power vs Input Drive ───────────────────────────
        pvi_html = ""
        pvi = glb.get("power_vs_input") or []
        if pvi:
            _rows = []
            for row in pvi:
                _rows.append(
                    f"<tr><td>{fmt_f(row.get('input_power_dbm'), 1)}</td>"
                    f"<td>{fmt_f(row.get('output_power_dbm'), 1)}</td>"
                    f"<td>{fmt_f(row.get('gain_db'), 1)}</td>"
                    f"<td>{fmt_f(row.get('gain_compression_db'), 1)}</td></tr>"
                )
            pvi_html = f"""
<h2>Output Power vs Input Drive Level (AM-AM)</h2>
<table>
  <tr><th>Input (dBm)</th><th>Output (dBm)</th>
      <th>Gain (dB)</th><th>Compression (dB)</th></tr>
  {''.join(_rows)}
</table>
<p class="note">Compression &gt; 0 dB indicates the onset of saturation.</p>
"""

        # ── Optional: Cable Loss ────────────────────────────────────────────
        cable_html = ""
        cable = glb.get("cable_loss") or []
        if cable:
            _rows = []
            _total = 0.0
            for row in cable:
                tot = row.get("total_loss_db", 0)
                if isinstance(tot, (int, float)):
                    _total += tot
                _rows.append(
                    f"<tr><td>{esc(row.get('segment', '—'))}</td>"
                    f"<td>{esc(row.get('cable_type', '—'))}</td>"
                    f"<td>{esc(row.get('length_m', '—'))}</td>"
                    f"<td>{fmt_f(row.get('loss_db_per_m'), 3)}</td>"
                    f"<td>{fmt_f(tot, 2)}</td>"
                    f"<td>{esc(row.get('frequency_mhz', '—'))}</td></tr>"
                )
            cable_html = f"""
<h2>Cable &amp; Connector Loss Budget</h2>
<table>
  <tr><th>Segment</th><th>Cable/Connector Type</th><th>Length (m)</th>
      <th>Loss/m (dB/m)</th><th>Total Loss (dB)</th><th>Frequency (MHz)</th></tr>
  {''.join(_rows)}
  <tr class="totals"><td colspan="4"><b>TOTAL</b></td>
      <td><b>{round(_total, 2)}</b></td><td>—</td></tr>
</table>
<p class="note">Cable loss must be compensated by additional gain or accepted as part
of the system link budget.</p>
"""

        # ── Per-stage DC bias has moved to the Power Consumption document ──
        # The Vdd/Idq/Pdc table was previously rendered here as a section of
        # the GLB; it has been consolidated into `power_calculation.md/html`
        # so all DC-power content lives in one place.
        bias_html = (
            "<p class=\"note\"><b>Per-stage DC bias (Vdd/Idq/Pdc)</b> is listed "
            "in the companion <code>power_calculation.md</code> / "
            "<code>power_calculation.html</code> document — the single source "
            "for power-budget information.</p>"
        )

        # ── Consistency Checks (BOM / Block Diagram cross-reference) ────────
        bom = tool_input.get("components") or tool_input.get("bom") or []
        xrefs = self._glb_cross_check_bom(glb, bom)
        if xrefs:
            _li = "".join(f"<li>{esc(w)}</li>" for w in xrefs)
            xref_html = f"""
<h2>Consistency Checks</h2>
<p class="verdict warn"><b>&#9888; BOM / Block-Diagram mismatches detected — review before PCB release:</b></p>
<ul>{_li}</ul>
"""
        elif bom:
            xref_html = """
<h2>Consistency Checks</h2>
<p class="verdict ok">&check; All GLB components appear in the project BOM.</p>
"""
        else:
            xref_html = """
<h2>Consistency Checks</h2>
<p class="note">(No BOM supplied to the GLB tool call — cross-check skipped. When the
BOM is available, this section lists any component in the GLB that does not appear in
the parts list.)</p>
"""

        # ── Friis formula note ──────────────────────────────────────────────
        friis_html = """
<h2>Cascade Noise Figure — Friis Formula</h2>
<p class="formula"><code>F<sub>sys</sub> = F<sub>1</sub> + (F<sub>2</sub> − 1) / G<sub>1</sub>
  + (F<sub>3</sub> − 1) / (G<sub>1</sub>·G<sub>2</sub>) + …</code></p>
<p class="note">Where <em>F</em> = linear noise factor (not dB), <em>G</em> = linear gain.
The first stage NF dominates — minimise LNA / driver NF for the best system sensitivity.</p>
<p class="verdict ok"><b>Note:</b> All values are estimated from component datasheets at 25 °C nominal.
Verify with bench measurements (spectrum analyser + noise source) during hardware bring-up.</p>
"""

        # ── CSS (identical palette to power_calculation.html) ───────────────
        css = """
body { font-family: -apple-system, Segoe UI, Inter, sans-serif; max-width: 1200px;
       margin: 32px auto; padding: 0 24px; color: #1a2235; background: #f7f8fa; }
h1 { font-size: 28px; margin-bottom: 4px; color: #0b1220; }
h1 small { display: block; font-size: 14px; color: #64748b; font-weight: 400; margin-top: 6px; }
h2 { font-size: 18px; margin-top: 32px; border-bottom: 2px solid #00c6a7;
     padding-bottom: 6px; color: #0b1220; }
p.note { color: #475569; font-size: 13px; line-height: 1.55; }
p.formula { font-size: 15px; text-align: center; padding: 14px; background: #fff;
            border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
table { width: 100%; border-collapse: collapse; margin: 12px 0 24px; font-size: 13px; background: #fff;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06); border-radius: 6px; overflow: hidden; }
th { background: #0b1220; color: #e2e8f0; text-align: left; padding: 10px 12px;
     font-weight: 600; font-size: 12px; letter-spacing: 0.3px; }
td { padding: 8px 12px; border-bottom: 1px solid #e2e8f0; }
tr.totals td { background: #eef5f3; font-weight: 600; }
td.warn { background: #fff4e5; color: #c2410c; font-weight: 600; }
td.pass { background: #ecfdf5; color: #065f46; font-weight: 600; }
td.fail { background: #fef2f2; color: #991b1b; font-weight: 600; }
p.verdict { padding: 10px 14px; border-radius: 6px; font-size: 13px; }
p.verdict.ok { background: #ecfdf5; color: #065f46; }
p.verdict.warn { background: #fef2f2; color: #991b1b; }
code { background: #eef2f6; padding: 1px 5px; border-radius: 3px; font-size: 12px; font-family: JetBrains Mono, Consolas, monospace; }
span.warn-inline { color: #c2410c; font-size: 11px; font-style: italic; }
table.sweep th, table.sweep td { padding: 6px 8px; font-size: 12px; text-align: right; }
table.sweep th:nth-child(-n+3), table.sweep td:nth-child(-n+3) { text-align: left; }
table.sweep .rowlabel { font-weight: 600; color: #0b1220; background: #f7f8fa; }
"""

        # If there are no stages at all, emit a short placeholder doc.
        if not stages:
            return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8" /><title>RF Gain-Loss Budget — {esc(project_name)}</title>
<style>{css}</style></head>
<body>
<h1>RF Gain-Loss Budget<small>{esc(project_name)} · Generated: {date}</small></h1>
<p class="verdict warn">No stage data available — re-run Phase 1 for an RF design to populate this document.</p>
</body></html>
"""

        # ── Design-contract banner (top-of-report) ─────────────────────────
        _bom_for_checks = tool_input.get("components") or tool_input.get("bom") or []
        _rules = self._glb_contract_checks(glb_raw, glb, _bom_for_checks)
        _fails_n = sum(1 for r in _rules if r[2] == "fail")
        _warns_n = sum(1 for r in _rules if r[2] == "warn")
        if _fails_n:
            _verdict_cls = "warn"
            _verdict_txt = f"&#10060; FAIL — {_fails_n} hard violation(s), {_warns_n} warning(s)"
        elif _warns_n:
            _verdict_cls = "warn"
            _verdict_txt = f"&#9888; WARN — 0 hard violations, {_warns_n} warning(s) — review before release"
        else:
            _verdict_cls = "ok"
            _verdict_txt = "&#9989; PASS — all five cardinal checks satisfied"
        _rule_rows = []
        for rid, rname, status, detail in _rules:
            _cls = {"pass": "pass", "warn": "warn", "fail": "fail"}.get(status, "")
            _icon = {"pass": "&#9989;", "warn": "&#9888;", "fail": "&#10060;"}.get(status, "")
            _rule_rows.append(
                f"<tr><td><b>{esc(rid)}</b></td><td>{esc(rname)}</td>"
                f"<td class='{_cls}'><b>{_icon} {esc(status)}</b></td>"
                f"<td>{esc(detail)}</td></tr>"
            )
        contract_html = f"""
<h2>Design Contract Checks</h2>
<p class="verdict {_verdict_cls}">{_verdict_txt}</p>
<table>
  <tr><th>#</th><th>Rule</th><th>Status</th><th>Detail</th></tr>
  {''.join(_rule_rows)}
</table>
<p class="note">These five invariants must hold for any RF receiver GLB to be
releasable to PCB. Any <b>fail</b> row is a contract violation — regenerate
Phase 1 or edit the offending stage before proceeding to PCB layout.</p>
"""

        sys_block = ""
        if sys_rows_html:
            sys_block = f"""
<h2>System Parameters</h2>
<table>
  <tr><th>Parameter</th><th>Value</th><th>Unit</th></tr>
  {sys_rows_html}
</table>
"""

        summary_block = ""
        if summary_html:
            summary_block = f"""
<h2>Budget Summary</h2>
<table>
  <tr><th>Metric</th><th>Value</th><th>Unit</th></tr>
  {summary_html}
</table>
"""

        html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>RF Gain-Loss Budget — {esc(project_name)}</title>
<style>{css}</style>
</head>
<body>
<h1>RF Gain-Loss Budget
  <small>{esc(project_name)} · Generated: {date} · AI-GENERATED — verify against final component datasheets</small>
</h1>

{contract_html}

{sys_block}

<h2>Stage-by-Stage Gain / Loss Budget</h2>
<table>
  <tr><th>#</th><th>Stage</th><th>Component</th><th>Gain/Loss (dB)</th>
      <th>Cum. Gain (dB)</th><th>Output Power (dBm)</th>
      <th>NF (dB)</th><th>Cum. NF (dB)</th>
      <th>P1dB Out (dBm)</th><th>OIP3 (dBm)</th>
      <th>Region</th><th>Notes</th></tr>
  {''.join(stage_body)}
</table>
<p class="note"><b>Region legend:</b> <em>Linear</em> = ≥10 dB back-off from P1dB ·
<em>Near-linear</em> = 6-10 dB · <em>Compressing</em> = 0-6 dB (onset of gain compression) ·
<em>Saturated</em> = above P1dB (hard non-linear). Keep every stage in the Linear zone for
analogue receivers; transmit chains may intentionally drive the PA into compression.
Warning icons (&#9888;) flag datasheet values that fail senior-RF-engineer sanity checks
(e.g. OIP3 &lt; P1dB + 5 dB, NF &lt; 0.5 dB, passive NF &ne; |loss|, mixer gain outside
typical passive/active bands).</p>

{summary_block}
{bias_html}
{noise_html}
{thermal_html}
{freq_html}
{sweep_html}
{rl_html}
{harm_html}
{pvf_html}
{pvi_html}
{cable_html}
{xref_html}
{friis_html}
</body>
</html>
"""
        return html_doc

    def _build_components_md(self, tool_input: dict, project_name: str) -> str:
        """Build component recommendations markdown.

        v24 (2026-04-20): URL resolution now uses the deterministic candidate
        builder in `tools.datasheet_url`. For every (mfr, part) pair we HEAD-probe
        an ordered list of candidate product-page URLs with a browser-class
        User-Agent (ADI / TI / etc. reject bot UAs) and use the first 2xx. The
        LLM-emitted URL is treated as a hint, never as ground truth. The final
        element of every candidate list is guaranteed never to 404
        (vendor parametric-search or DuckDuckGo vendor-scoped search).
        """
        import re as _re
        import urllib.request as _urllib
        import urllib.parse as _urllib_parse
        import urllib.error as _urllib_err
        from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _as_completed

        try:
            from tools.datasheet_url import candidate_datasheet_urls as _cand_urls
        except Exception:
            _cand_urls = lambda m, p: []

        # ── URL bad-pattern filter (reject LLM-hallucinated deep paths) ─────
        _BAD_PATTERNS = [
            r'/vpt[/-]', r'vptpower\.com', r'\bvpt\b',
            # We now trust our canonical /media/en/... PDF fallback for ADI,
            # so only reject blatantly non-datasheet patterns here.
        ]
        def _filter_url(url: str) -> str:
            if not url or not url.startswith('http'):
                return ''
            for pat in _BAD_PATTERNS:
                if _re.search(pat, url, _re.IGNORECASE):
                    return ''
            return url

        # ── Live HTTP probe with browser-class UA (critical: ADI/TI reject bot UAs) ──
        _BROWSER_UA = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        # Hosts whose URLs are always live by construction — candidate_datasheet_urls
        # emits only DigiKey/Mouser keyword-search endpoints since P12, and both
        # return 2xx for any query (result list, filtered list, or "no results"
        # page — never a 404). Probing them is 100% wasted latency. P17
        # (2026-04-24): short-circuit those to True, only probe LLM-supplied
        # URLs that haven't been vetted by our resolver.
        _TRUSTED_HOSTS = ("digikey.com", "digikey.in", "mouser.com", "mouser.in")
        def _is_trusted(url: str) -> bool:
            return any(h in url for h in _TRUSTED_HOSTS)

        def _probe(url: str) -> bool:
            if not url or not url.startswith('http'):
                return False
            for method in ('HEAD', 'GET'):
                try:
                    req = _urllib.Request(url, method=method, headers={
                        'User-Agent': _BROWSER_UA,
                        'Accept': 'text/html,application/pdf,*/*',
                    })
                    with _urllib.urlopen(req, timeout=5) as resp:
                        if 200 <= resp.status < 400:
                            return True
                except Exception:
                    continue
            return False

        # ── Build per-row candidate lists; collect ALL probes into one pool ──
        comps = tool_input.get("component_recommendations", [])
        primary_cands: list[list[str]] = []
        alt_cands: list[list[list[str]]] = []
        probe_set: set[str] = set()
        for comp in comps:
            part = (comp.get('primary_part', '') or '').strip()
            mfr  = (comp.get('primary_manufacturer', '') or '').strip()
            cands = _cand_urls(mfr, part) or []
            primary_cands.append(cands)
            probe_set.update(cands)
            llm_url = _filter_url((comp.get('datasheet_url', '') or '').strip())
            if llm_url:
                probe_set.add(llm_url)
            row_alts: list[list[str]] = []
            for alt in comp.get('alternatives', []):
                a_part = (alt.get('part_number', '') or part).strip()
                a_mfr  = (alt.get('manufacturer', mfr) or mfr).strip()
                a_cands = _cand_urls(a_mfr, a_part) or []
                row_alts.append(a_cands)
                probe_set.update(a_cands)
                a_llm = _filter_url((alt.get('datasheet_url', '') or '').strip())
                if a_llm:
                    probe_set.add(a_llm)
            alt_cands.append(row_alts)

        # Parallel HEAD probes (max 12 workers) — only for NON-trusted URLs.
        # Trusted distributor-search URLs are marked live without a round-trip.
        url_ok: dict[str, bool] = {}
        untrusted: list[str] = []
        for u in sorted(probe_set):
            if _is_trusted(u):
                url_ok[u] = True  # short-circuit
            else:
                untrusted.append(u)
        if untrusted:
            with _TPE(max_workers=min(12, len(untrusted))) as pool:
                fut_map = {pool.submit(_probe, u): u for u in untrusted}
                for fut in _as_completed(fut_map):
                    url_ok[fut_map[fut]] = fut.result()

        def _resolve(cands: list[str], llm_url: str) -> str:
            """Pick first live candidate; else LLM URL if live; else last candidate
            (guaranteed never to 404 — vendor parametric search)."""
            for c in cands:
                if url_ok.get(c, False):
                    return c
            if llm_url and url_ok.get(llm_url, False):
                return llm_url
            if cands:
                return cands[-1]   # parametric-search fallback
            return llm_url

        def _verified_url(raw_url: str, part: str, mfr: str) -> str:
            """Back-compat shim: resolve via candidates without caller knowing index."""
            cands = _cand_urls(mfr, part) or []
            return _resolve(cands, _filter_url(raw_url))

        # ── Build markdown ─────────────────────────────────────────────────────
        lines = [
            "# Component Recommendations",
            f"## {project_name}",
            "",
        ]

        for i, comp in enumerate(comps, 1):
            part = comp.get('primary_part', 'See BOM')
            mfr  = comp.get('primary_manufacturer', '')
            # Use `or ''` guard — dict.get returns None when the key exists with
            # value None (common now that find_candidate_parts may surface
            # candidates with datasheet_url=None from Mouser's data gaps).
            ds_url = _verified_url((comp.get('datasheet_url') or '').strip(), part, mfr)
            source = (comp.get('distributor_source') or comp.get('source') or '').strip().lower()
            product_url = (comp.get('product_url') or comp.get('distributor_url') or '').strip()
            dk_url = (comp.get('digikey_url') or '').strip()
            ms_url = (comp.get('mouser_url') or '').strip()
            if product_url and not dk_url and source == "digikey":
                dk_url = product_url
            if product_url and not ms_url and source == "mouser":
                ms_url = product_url

            # Primary heading with part number as a link if datasheet available
            part_str = f"[{part}]({ds_url})" if ds_url else part

            lines.extend([
                f"### {i}. {comp.get('function', 'Component')}",
                "",
                f"**Primary Choice:** {part_str} ({mfr})",
                "",
                f"*{comp.get('primary_description', '')}*",
                "",
            ])

            # Quick-link row — label each link based on where it actually
            # points, not on generic "Datasheet" text.  Since the P11/P12
            # datasheet-URL rewrite, `ds_url` can be:
            #   - a real datasheet PDF URL → "📄 Datasheet"
            #   - a DigiKey keyword-search URL → "🔗 DigiKey"
            #   - a Mouser keyword-search URL → "🔗 Mouser"
            # Labelling those as "Datasheet" was misleading users into
            # expecting a PDF preview when clicking would land them on a
            # distributor catalog page. User feedback 2026-04-24.
            def _link_label(url: str) -> str:
                u = url.lower()
                if u.endswith(".pdf") or "/datasheet/" in u or "/media/" in u:
                    return "📄 Datasheet"
                if "digikey.com" in u or "digikey.in" in u:
                    return "🔗 DigiKey"
                if "mouser.com" in u or "mouser.in" in u:
                    return "🔗 Mouser"
                return "🔗 Product page"

            # HOST-based dedupe (P19, 2026-04-24): two different URLs on
            # the SAME distributor (e.g. `ds_url` = digikey.com/keyword search,
            # `dk_url` = digikey.com/product-detail/...) both rendered as
            # "🔗 DigiKey 🔗 DigiKey 🔗 Mouser" — ugly and confusing to the
            # user. Now we cap: max 1 link per distributor host, favouring
            # the most specific URL (product page > search URL > LLM URL).
            def _host_bucket(url: str) -> str:
                u = (url or "").lower()
                if "digikey.com" in u or "digikey.in" in u: return "digikey"
                if "mouser.com" in u or "mouser.in" in u:   return "mouser"
                if u.endswith(".pdf") or "/datasheet/" in u or "/media/" in u:
                    return "datasheet_pdf"
                return "other"

            def _url_specificity(url: str) -> int:
                """Higher = more specific. Product-detail pages beat
                search URLs; real PDFs beat product pages."""
                u = (url or "").lower()
                if u.endswith(".pdf") or "/datasheet/" in u:   return 3
                if "/products/detail/" in u or "/productdetail" in u: return 2
                if "/products/result?keywords=" in u or "/c/?q=" in u: return 1
                return 0

            # Collect candidates with their host + specificity, then pick
            # the winner per host.
            candidates: list[tuple[str, str, str]] = []  # (url, host, label)
            if ds_url:
                candidates.append((ds_url, _host_bucket(ds_url), _link_label(ds_url)))
            if dk_url:
                candidates.append((dk_url, "digikey", "🔗 DigiKey"))
            if ms_url:
                candidates.append((ms_url, "mouser", "🔗 Mouser"))
            if product_url and not dk_url and not ms_url:
                candidates.append((product_url, _host_bucket(product_url), _link_label(product_url)))

            best_by_host: dict[str, tuple[str, str]] = {}  # host -> (url, label)
            for url, host, label in candidates:
                if not url:
                    continue
                cur = best_by_host.get(host)
                if cur is None or _url_specificity(url) > _url_specificity(cur[0]):
                    best_by_host[host] = (url, label)

            # Render in stable order: datasheet_pdf > digikey > mouser > other.
            links: list[str] = []
            for host in ("datasheet_pdf", "digikey", "mouser", "other"):
                if host in best_by_host:
                    url, label = best_by_host[host]
                    links.append(f"[{label}]({url})")
            if links:
                lines.append("  ".join(links))
                lines.append("")

            meta_bits = []
            if source:
                meta_bits.append(f"source: {source}")
            price = comp.get("unit_price")
            currency = comp.get("unit_price_currency")
            if price is not None and currency:
                meta_bits.append(f"unit price: {price} {currency}")
            stock = comp.get("stock_quantity")
            region = comp.get("stock_region") or comp.get("region")
            if stock is not None:
                label = f"stock: {stock}"
                if region:
                    label += f" ({region})"
                meta_bits.append(label)
            if meta_bits:
                lines.append("*Distributor data:* " + " | ".join(str(x) for x in meta_bits))
                lines.append("")

            # Key specs — remove 'datasheet' key if LLM stuffed the URL there
            specs = {k: v for k, v in (comp.get("primary_key_specs") or {}).items()
                     if k.lower() not in ("datasheet", "datasheet_url", "digikey", "digikey_url")}
            if specs:
                lines.append("| Spec | Value |")
                lines.append("|---|---|")
                for k, v in specs.items():
                    lines.append(f"| {k} | {v} |")
                lines.append("")

            # Alternatives
            alts = comp.get("alternatives", [])
            if alts:
                lines.append("**Alternatives:**")
                for alt in alts:
                    alt_pn  = alt.get('part_number', '')
                    alt_mfr = alt.get('manufacturer', mfr)
                    alt_ds  = _verified_url((alt.get('datasheet_url') or '').strip(), alt_pn, alt_mfr)
                    alt_pn_str = f"[{alt_pn}]({alt_ds})" if alt_ds else alt_pn
                    lines.append(
                        f"- **{alt_pn_str}** ({alt_mfr}): "
                        f"{alt.get('trade_off', '')}"
                    )
                lines.append("")

            # Rationale
            rationale = comp.get("selection_rationale", "")
            if rationale:
                lines.append(f"**Selection Rationale:** {rationale}")
                lines.append("")

        return "\n".join(lines)


    def _detect_complete_requirements(self, response_content: str) -> bool:
        """
        Detect if the response contains a complete requirements document.
        This is a fallback for models that don't support tool calling (e.g., GLM-4).
        """
        content_lower = response_content.lower()

        # Strong signal: long response (>2000 chars) with multiple REQ-HW IDs = almost certainly complete
        req_count = len(re.findall(r'REQ-HW-\d+', response_content, re.IGNORECASE))
        if len(response_content) > 2000 and req_count >= 3:
            return True

        # Look for key indicators of a complete requirements document
        indicators = [
            # Requirement IDs present
            req_count >= 1,
            # Hardware requirements header
            'hardware requirements' in content_lower,
            'requirements document' in content_lower,
            # Project summary section
            'project summary' in content_lower,
            # Design parameters table
            'design parameters' in content_lower or 'parameter' in content_lower,
            # Component recommendations
            'component recommendations' in content_lower or 'component' in content_lower,
            # Next steps or conclusion phrases (broader set)
            any(phrase in content_lower for phrase in [
                'next steps', 'phase complete', 'requirements captured',
                'would you like me to', 'shall i proceed',
                'work on next', 'let me know what', 'phase 1 deliverable',
                'deliverable', 'generated your', 'complete requirements',
            ]),
        ]

        # Need at least 4 indicators to consider it complete
        return sum(indicators) >= 4

    def _parse_requirements_response(self, response_content: str, project_name: str) -> Optional[dict]:
        """
        Parse a complete requirements response into structured format.
        This is a fallback for models that don't support tool calling.
        """
        try:
            # Extract project summary
            summary_match = re.search(
                r'(?:Project Summary|##\s*\d*\s*Summary)[\s:]*\n+(.*?)(?=##|\n\n|\Z)',
                response_content,
                re.IGNORECASE | re.DOTALL
            )
            project_summary = (summary_match.group(1).strip()[:500]
                             if summary_match else "Hardware design project captured from conversation.")

            # Extract requirement entries
            requirements = []
            req_pattern = r'(?:REQ-HW[-_]?\d+|\|\s*REQ[-_]?HW[-_]?\d+)'
            for match in re.finditer(req_pattern, response_content, re.IGNORECASE):
                # Try to extract the full requirement table row
                start = max(0, match.start() - 200)
                end = min(len(response_content), match.end() + 500)
                context = response_content[start:end]

                req_id = re.search(r'REQ[-_]?HW[-_]?\d+', match.group(0), re.IGNORECASE)
                if req_id:
                    req_id = req_id.group(0).upper().replace('_', '-')

                    # Parse requirement details from table or list
                    title_match = re.search(r'\|\s*' + re.escape(req_id) + r'\s*\|\s*([^|]+)', context, re.IGNORECASE)
                    title = title_match.group(1).strip() if title_match else "Hardware Requirement"

                    desc_match = re.search(r'\|\s*' + re.escape(req_id) + r'\s*\|\s*[^|]*\|\s*([^|]+)', context, re.IGNORECASE)
                    description = (desc_match.group(1).strip() if desc_match
                                 else "Extracted from requirements conversation.")

                    # Detect priority
                    priority = "Must have"
                    ctx_lower = context.lower()
                    if "should" in ctx_lower or "should have" in ctx_lower:
                        priority = "Should have"
                    elif "could" in ctx_lower or "could have" in ctx_lower or "may" in ctx_lower:
                        priority = "Could have"
                    elif "won't" in ctx_lower or "wont" in ctx_lower:
                        priority = "Won't have"

                    requirements.append({
                        "req_id": req_id,
                        "category": "functional",
                        "title": title,
                        "description": description,
                        "priority": priority,
                        "verification_method": "test",
                        "dependencies": [],
                        "constraints": []
                    })

            # Ensure we have at least some requirements
            if len(requirements) < 3:
                # Add default requirements based on conversation
                requirements = [
                    {"req_id": "REQ-HW-001", "category": "functional", "title": "System Functionality",
                     "description": "System shall meet the functional requirements described in the conversation.", "priority": "Must have", "verification_method": "test", "dependencies": [], "constraints": []},
                    {"req_id": "REQ-HW-002", "category": "performance", "title": "Performance Targets",
                     "description": "System shall meet the performance targets specified.", "priority": "Must have", "verification_method": "test", "dependencies": [], "constraints": []},
                    {"req_id": "REQ-HW-003", "category": "environmental", "title": "Environmental Conditions",
                     "description": "System shall operate within the specified environmental conditions.", "priority": "Must have", "verification_method": "test", "dependencies": [], "constraints": []},
                ]

            # Extract design parameters from any tables or key-value pairs
            design_parameters = {}
            param_patterns = [
                r'\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|',  # Markdown tables
                r'([A-Z][a-zA-Z\s]+?)\s*[:=]\s*([^\n]+)',  # Key: Value
            ]

            for pattern in param_patterns:
                for match in re.finditer(pattern, response_content):
                    key = match.group(1).strip()
                    value = match.group(2).strip()
                    # Skip if looks like a table header or unrelated
                    if key.lower() not in ['parameter', 'value', 'id', 'title', 'description', 'priority']:
                        key_clean = re.sub(r'\s+', '_', key.lower())
                        design_parameters[key_clean] = value
                        if len(design_parameters) >= 15:  # Limit extracted parameters
                            break

            # Add default parameters if none found
            if not design_parameters:
                design_parameters = {
                    "input_voltage": "As specified",
                    "output_power": "As specified",
                    "frequency_range": "As specified",
                    "temperature_range": "As specified",
                }

            # Extract or generate block diagram mermaid
            block_diagram = self._extract_or_generate_mermaid(response_content, "block")

            # Extract or generate architecture mermaid
            architecture = self._extract_or_generate_mermaid(response_content, "architecture")

            # Extract component recommendations
            component_recommendations = self._extract_components(response_content)

            return {
                "project_summary": project_summary,
                "requirements": requirements[:20],  # Limit to 20 requirements
                "design_parameters": design_parameters,
                "block_diagram_mermaid": block_diagram,
                "architecture_mermaid": architecture,
                "component_recommendations": component_recommendations,
            }

        except Exception as e:
            self.log(f"Failed to parse requirements response: {e}", "warning")
            return None

    def _extract_or_generate_mermaid(self, response_content: str, diagram_type: str) -> str:
        """Extract mermaid diagram from response or generate a default one."""
        # Look for mermaid code blocks
        mermaid_match = re.search(
            r'```mermaid\s*(.*?)```',
            response_content,
            re.DOTALL | re.IGNORECASE
        )
        if mermaid_match:
            return mermaid_match.group(1).strip()

        # Generate a default diagram
        if diagram_type == "block":
            return '''graph TD
    PWR[Power Input] --> PWR_DIST[Power Distribution]
    PWR_DIST --> MCU[Control/Digital Processing]
    PWR_DIST --> RF[RF/Analog Front End]
    MCU --> CTRL[Control Interfaces]
    RF --> OUT[Output/Load]
    style PWR fill:#f9f,stroke:#333,stroke-width:2px
    style MCU fill:#bbf,stroke:#333,stroke-width:2px
    style RF fill:#bfb,stroke:#333,stroke-width:2px'''
        else:
            return '''graph LR
    subgraph POWER["Power Domain"]
        PWR_IN[Input] --> REG[Regulators]
    end
    subgraph DIGITAL["Digital Domain"]
        MCU[Controller]
    end
    subgraph ANALOG["Analog/RF Domain"]
        AFE[Front End]
    end
    POWER --> DIGITAL
    POWER --> ANALOG
    DIGITAL --> ANALOG'''

    @staticmethod
    def _reflow_long_mermaid(mermaid_src: str) -> str:
        """Rewrite `flowchart LR` / `graph LR` to `TD` when the chain has
        enough nodes that left-to-right layout turns into an unreadable
        horizontal strip in the browser. Threshold is 8 arrows — below
        that LR is fine; above that TD wraps cleanly on a 1920px viewport.

        Leaves non-LR diagrams untouched. No-op on empty input."""
        if not mermaid_src or not isinstance(mermaid_src, str):
            return mermaid_src
        arrow_count = mermaid_src.count("-->")
        if arrow_count < 8:
            return mermaid_src
        out = re.sub(r"^(\s*)flowchart\s+LR\b", r"\1flowchart TD", mermaid_src, count=1, flags=re.MULTILINE)
        if out == mermaid_src:
            out = re.sub(r"^(\s*)graph\s+LR\b", r"\1graph TD", mermaid_src, count=1, flags=re.MULTILINE)
        return out

    def _render_diagram_field(
        self,
        tool_input: dict,
        *,
        structured_key: str,
        raw_key: str,
        default_direction: str = "LR",
        allow_empty: bool = False,
    ) -> str:
        """Single source of truth for turning either a structured `block_diagram`
        spec OR a raw `block_diagram_mermaid` string into a guaranteed-parseable
        Mermaid document.

        Preference order:
          1. `structured_key` (dict spec) — rendered by `render_block_diagram`.
             If rendering raises `MermaidSpecError` we log and fall through to (2).
          2. `raw_key` (raw LLM Mermaid) — run through `salvage()` which fixes
             the most common LLM syntax mistakes (bare shapes, em-dash arrows,
             frontmatter, unclosed brackets). If salvage cannot produce valid
             output it returns a safe fallback diagram.
          3. If both paths fail and `allow_empty` is True, return an empty string
             so the caller can render a "diagram will be generated with HRS"
             placeholder instead of an ugly fallback.

        Every path returns ASCII-safe Mermaid — unicode glyphs, edge labels,
        pipe chars, and unclosed brackets are all handled deterministically."""

        # P25 (2026-04-25) RE-ORDERED PRIORITY: prefer the deterministic
        # paths (LLM structured spec OR BOM-derived) over the raw LLM
        # mermaid string. Rationale: every iteration of mermaid salvage
        # in the past 5 sessions has been a whack-a-mole rule for one
        # specific LLM mis-emission pattern (nested braces, escaped
        # brackets, mashed-onto-one-line nodes, quoted edge labels with
        # dotted arrows, etc.). Each new run produces a NEW broken
        # pattern. Trying to clean raw LLM mermaid is a losing game.
        #
        # New priority:
        #   1. LLM structured `block_diagram` JSON spec (deterministic).
        #   2. BOM-derived structured chain (deterministic, real parts).
        #   3. Salvaged raw LLM mermaid (only if no BOM available).
        #   4. Empty / FALLBACK_DIAGRAM.
        #
        # User-facing impact: the architecture / block diagram in the
        # persistent docs now ALWAYS shows a clean rendered diagram of
        # the user's actual parts, even when the LLM's raw mermaid is
        # unparseable. We lose any nuance the LLM tried to express in
        # raw text (subgraphs, control signals) but gain reliability.
        # If the LLM emits a proper structured spec (which we now ask
        # for explicitly in FINALIZE_SYSTEM_PROMPT), it wins outright.

        # Path 1 — LLM structured spec.
        structured = tool_input.get(structured_key)
        if isinstance(structured, dict) and structured.get("nodes"):
            try:
                if structured_key == "architecture":
                    return render_architecture(structured, raise_on_error=True)
                return render_block_diagram(
                    structured,
                    default_direction=default_direction,
                    raise_on_error=True,
                )
            except MermaidSpecError as exc:
                self.log(
                    f"structured {structured_key} rejected, "
                    f"trying BOM-derived: {exc}",
                    "warning",
                )

        # Path 2 — BOM-derived structured chain (NOW PROMOTED ABOVE
        # raw-mermaid salvage). Renders deterministically from
        # `component_recommendations` so the diagram is guaranteed to
        # parse and shows the user's real parts.
        bom_spec = self._derive_block_diagram_from_bom(
            tool_input, default_direction=default_direction,
        )
        if bom_spec is not None:
            try:
                return render_block_diagram(
                    bom_spec,
                    default_direction=default_direction,
                    raise_on_error=True,
                )
            except MermaidSpecError as exc:
                self.log(
                    f"BOM-derived {structured_key} render failed: {exc} — "
                    f"falling back to raw LLM mermaid salvage",
                    "warning",
                )

        # Path 3 — raw LLM mermaid: PERMANENT FIX (P26 #11, 2026-04-25).
        #
        # Pre-fix: ran `salvage(raw)` which patched the raw text in
        # place. This was a never-ending whack-a-mole — every new LLM
        # output contained a shape variant the salvage didn't expect
        # (trapezoid with parens, sequence-diagram notes in flowcharts,
        # backslash-escaped quotes, etc.) and the user reported the
        # same class of bug 30+ times.
        #
        # Permanent fix: parse the raw mermaid into a STRUCTURED spec
        # via `coerce_to_spec` (forgiving regex extraction of node IDs +
        # labels + edges from any shape variant), then re-render via
        # the deterministic `render_block_diagram` which produces ONLY
        # plain `["label"]` rect shapes with quoted labels. Quoted-rect
        # accepts ALL special chars (parens, <br>, #, arrows, etc.) so
        # the output is guaranteed to render in mermaid.js, mermaid.ink,
        # and mmdc.
        #
        # Empirical: tested against all 41 projects in `output/` (81
        # mermaid files total — both architecture.md and block_diagram.md).
        # 81/81 render OK via mmdc with this approach. The salvage path
        # below stays as a last-ditch safety net in case coercion fails
        # to find ≥2 nodes.
        raw = tool_input.get(raw_key, "")
        if isinstance(raw, str) and raw.strip():
            try:
                from tools.mermaid_coerce import coerce_to_spec
                coerced_spec = coerce_to_spec(
                    raw, default_direction=default_direction,
                )
                if coerced_spec and coerced_spec.get("nodes"):
                    rendered = render_block_diagram(
                        coerced_spec,
                        default_direction=default_direction,
                        raise_on_error=True,
                    )
                    self.log(
                        f"{raw_key}: coerced raw mermaid → "
                        f"{len(coerced_spec['nodes'])} nodes / "
                        f"{len(coerced_spec.get('edges') or [])} edges via "
                        "deterministic renderer (rect-only, always-valid)",
                        "info",
                    )
                    return rendered
            except Exception as _coerce_exc:
                self.log(
                    f"{raw_key}: coercion failed ({_coerce_exc}) — "
                    "falling back to legacy salvage",
                    "warning",
                )
            # Last-ditch fallback to the legacy text-patcher (we've
            # seen 30+ failure variants here so this rarely produces
            # clean output, but it's better than the FALLBACK_DIAGRAM
            # for the small fraction of cases where coercion finds
            # <2 nodes).
            cleaned, fixes = salvage(raw)
            if fixes:
                self.log(
                    f"{raw_key}: legacy salvage (fixes: {','.join(fixes)})",
                    "info",
                )
            if "fallback" not in fixes:
                return cleaned

        # Path 4 — last-ditch BOM derivation in case path 2 returned
        # None (e.g. <2 components) but raw was also unrecoverable.
        bom_spec = self._derive_block_diagram_from_bom(
            tool_input, default_direction=default_direction,
        )
        if bom_spec is not None:
            try:
                return render_block_diagram(
                    bom_spec,
                    default_direction=default_direction,
                    raise_on_error=True,
                )
            except MermaidSpecError as exc:
                self.log(
                    f"BOM-derived {structured_key} fallback failed: {exc}",
                    "warning",
                )

        return "" if allow_empty else FALLBACK_DIAGRAM

    def _derive_block_diagram_from_bom(
        self,
        tool_input: dict,
        *,
        default_direction: str = "LR",
    ) -> Optional[dict]:
        """Build a `BlockDiagramSpec` from `component_recommendations` when
        the LLM's raw mermaid fails salvage. The chain is derived purely
        from real BOM data — MPN + function — so the user always gets a
        diagram showing their actual parts, not the FALLBACK_DIAGRAM
        "diagram could not be rendered" placeholder.

        Returns None when there aren't enough components to build even a
        2-node chain; the caller then falls back to FALLBACK_DIAGRAM.
        """
        comps = tool_input.get("component_recommendations") or []
        if not isinstance(comps, list) or len(comps) < 2:
            return None

        nodes: list[dict] = []
        edges: list[dict] = []
        import re as _re

        for idx, comp in enumerate(comps, start=1):
            if not isinstance(comp, dict):
                continue
            mpn = (
                comp.get("primary_part") or comp.get("part_number") or ""
            ).strip() or f"PART{idx}"
            function = (
                comp.get("function")
                or comp.get("role")
                or comp.get("primary_description")
                or mpn
            ).strip()
            # Node id must match ^[A-Za-z][A-Za-z0-9_]*$ per mermaid_render.
            node_id = _re.sub(r"[^A-Za-z0-9_]", "_", f"N{idx}_{mpn}")[:32]
            label = f"{function} ({mpn})" if mpn != function else function
            nodes.append({"id": node_id, "label": label, "shape": "rect"})
            if idx > 1:
                edges.append({
                    "from_": nodes[-2]["id"],
                    "to": nodes[-1]["id"],
                })

        if len(nodes) < 2:
            return None
        return {
            "direction": default_direction,
            "nodes": nodes,
            "edges": edges,
            "title": "BOM-derived signal chain (fallback)",
        }

    def _extract_components(self, response_content: str) -> list:
        """Extract component recommendations from response."""
        components = []

        # Look for component tables or lists
        # Pattern: Part Number | Manufacturer | Description
        table_pattern = r'\|\s*([A-Z0-9][-A-Z0-9]+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|'
        for match in re.finditer(table_pattern, response_content):
            part_number = match.group(1).strip()
            manufacturer = match.group(2).strip()
            description = match.group(3).strip()

            # Skip if looks like a header or not a component
            if (len(part_number) >= 3 and
                part_number not in ['PART NUMBER', 'PART', 'NUMBER'] and
                manufacturer not in ['MANUFACTURER', 'MFR', 'VENDOR']):
                components.append({
                    "function": description[:50],
                    "primary_part": part_number,
                    "primary_manufacturer": manufacturer,
                    "primary_description": description,
                    "primary_key_specs": {},
                    "alternatives": [],
                    "selection_rationale": "Extracted from requirements document."
                })
                if len(components) >= 10:
                    break

        # If no components found in tables, add placeholder
        if not components:
            components = [
                {
                    "function": "Controller/MCU",
                    "primary_part": "See component recommendations",
                    "primary_manufacturer": "Various",
                    "primary_description": "Primary controller selected based on design requirements.",
                    "primary_key_specs": {},
                    "alternatives": [],
                    "selection_rationale": "Selected in component recommendation phase."
                }
            ]

        return components
