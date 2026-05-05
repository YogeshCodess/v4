# ADR-002 — Why Four Rounds of Elicitation, Not One

**Status:** Accepted, 2026-04-18.
**Authors:** Silicon to Software (S2S) team.
**Related:** IMPLEMENTATION_PLAN.md (B3.1, A1.1), ADR-001 (model selection),
`agents/requirements_agent.py`, `agents/red_team_audit.py`,
`tools/cascade_validator.py`, `services/requirements_lock.py`.

---

## Context

The Silicon to Software (S2S)'s Phase 1 must convert a free-form user description
into a frozen, hashable requirements baseline that subsequent phases
(HRS, compliance, netlist, GLR, SRS, SDD, code review) can consume
without ambiguity. A naive one-shot "give the LLM the whole problem and
ask for a BOM" approach was tried during the earlier hackathon round and
produced three recurring failure modes:

1. **Hallucinated parts and citations.** With no explicit spec in hand,
   the model invents part numbers that look plausible and cites clauses
   that do not exist in the referenced standard.
2. **Missing or inconsistent numeric specs.** Noise figure, gain, IIP3,
   sensitivity, SFDR — any one of which, if unspecified, is silently
   assumed by the model and usually contradicts the others.
3. **No cascade sanity check.** The recommended BOM cannot physically
   meet the implied system NF or SFDR and no one notices until a PCB
   engineer tries to simulate it.

The existing project CLAUDE.md codifies these failure modes under the
"P1 Anti-Hallucination Design" section and the current agent design
splits elicitation into **four** rounds. This ADR records *why*.

---

## Decision

We will keep a strict four-round elicitation flow for P1:

1. **Round 1 — Mandatory Tier-1 questions** (16 questions for RF domains).
   Frequency range, noise figure, gain, sensitivity, SFDR, linearity,
   power, application, environmental, compliance. No architecture
   selection yet.
2. **Round 1.5 — Application-adaptive Tier-2 questions** (3 to 6).
   Conditional on application = {radar, EW, satcom, communication, other}.
   Military-specific follow-ups (TEMPEST, BIT, POI, G/T) live here.
3. **Round 2 — Architecture selection.** User picks among 15 receiver
   architectures or lets the agent recommend. Round 3's question set
   depends on this choice.
4. **Round 3 — Architecture-adaptive follow-ups** (3 to 5 questions).
   IF frequency, LO phase noise, ADC ENOB, and so on, each triggered by
   the architecture from Round 2.
5. **Round 4 — Validation and cascade preview.** Before any BOM is
   produced, the agent prints a summary of every spec gathered so far,
   runs `tools.cascade_validator.validate_cascade` on a placeholder BOM,
   flags impossible combinations (e.g. NF < 1 dB at 18 GHz with 8 dB
   insertion loss front-end), and demands explicit user confirmation.

Only after Round 4 confirmation does the requirements content become
eligible for a `services.requirements_lock.RequirementsLock.freeze()`.
The red-team audit (`agents.red_team_audit`) runs as a post-lock
verification pass; it cannot unlock, only flag.

---

## Alternatives considered

### A. One-shot "here's everything, produce the BOM"

**Pros:** fastest user experience, minimum chat turns.
**Cons:** the failure modes above dominate. No deterministic place to
gate on spec completeness. The cascade validator has no confirmed input.
Frozen requirements would encode whatever the LLM invented.

### B. Two rounds (basic + clarifying)

**Pros:** lighter chat load than four rounds.
**Cons:** architecture choice changes which follow-up questions are
relevant. Collapsing architecture and follow-ups into one round either
asks every follow-up regardless (noise, slow) or skips follow-ups that
matter (e.g. LO phase noise for superhet, ENOB for direct RF sampling).

### C. Three rounds (Tier-1 + architecture + follow-ups, no validation)

**Pros:** matches the SDR-engineer mental model reasonably well.
**Cons:** no gating on cascade feasibility before BOM generation. The
cascade validator still catches the errors, but after the user has been
shown a bad BOM; users routinely anchor on the first output.

### D. Five+ rounds

**Pros:** each topic gets its own round; maximally structured.
**Cons:** chat fatigue. Empirically, users abandon the P1 chat after
~45 turns. Five rounds puts us near that ceiling with no cascade
validation yet.

---

## Consequences

- **Positive:** every frozen requirement is reached through a
  deterministic path that the red-team audit can reason about. The
  cascade validator's inputs are confirmed, not inferred. The frozen
  hash is meaningful because the content was confirmed. The staleness
  propagation in ADR-003 works because the round-level confirmation
  flags become part of the canonical JSON.
- **Negative:** a four-round flow is longer than a one-shot prompt.
  Median P1 duration moves from ~2 min (one-shot) to ~4 min (four rounds).
  This is accepted: 4 min vs. a 2-week PCB redo is an easy trade.
- **Testing implication:** the elicitation state machine is
  test-addressable — each round has an entry gate (`round_n_confirmed`
  flag) and the four-round confirmation sequence is reproducible from
  logs. Golden scenarios in `tests/golden/` fix Round 1 answers so the
  harness can replay the state machine deterministically.
- **Frontend implication:** the React UI renders one round at a time
  with a visible progress bar (see `ChatView.tsx`). The "ANY SPECIFIC
  REQUIREMENTS?" optional card (bug B10 in CLAUDE.md) lands inside
  Round 1 as an explicit append step.

---

## Revisit criteria

This ADR should be revisited if any of the following become true:

- The red-team audit's `overall_pass=True` rate on new scenarios drops
  below 80 % — could indicate elicitation is *not* gathering enough
  information.
- Median P1 wall-clock duration exceeds 6 minutes — may indicate we
  need to collapse Rounds 1 and 1.5.
- We add a fifth domain (e.g. avionics flight-control) whose question
  set does not fit the current round structure.

Until one of those triggers, the four-round flow stands.
