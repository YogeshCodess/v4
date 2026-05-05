# ADR-003 — Requirements Lock Semantics

**Status:** Accepted, 2026-04-18.
**Authors:** Silicon to Software (S2S) team.
**Related:** ADR-001 (model selection), ADR-002 (elicitation order),
`services/requirements_lock.py`, `migrations/001_requirements_lock.sql`,
`agents/red_team_audit.py`.

---

## Context

Once Phase 1 has produced a confirmed requirements set, every downstream
phase (P2 HRS, P3 Compliance, P4 Netlist, P6 GLR, P8a SRS, P8b SDD,
P8c Code Review) consumes that set as authoritative input. If anything
in the requirements changes after those phases ran — even one
parameter — the downstream artefacts may no longer be coherent.

We need an explicit, hashable, machine-checkable notion of "the
requirements were X at the time phase Y ran" so that:

- A stale downstream phase can be detected automatically.
- A judge or auditor can verify the exact inputs a given artefact was
  produced from.
- A user can re-run only the phases whose inputs have drifted.

---

## Decision

We introduce a SHA-256-addressable **requirements lock**. The lock is
managed by `services.requirements_lock.RequirementsLock` and has the
following semantics.

### 1. What the lock contains

The lock's canonical JSON — call it `content` — contains everything a
downstream phase might need to know about the requirements. Concretely,
for P1 that is a dict with keys (at minimum):

- `round1_answers` — Tier-1 and Tier-2 confirmed answers.
- `round2_architecture` — the architecture choice.
- `round3_followups` — architecture-adaptive answers.
- `round4_confirmed` — boolean, must be True to permit freeze.
- `domain` — radar / ew / satcom / communication.
- `cited_standards` — list of (standard, clause) pairs the user
  approved during compliance review.
- `bom_stage_sketch` — the list of stage dicts shown to the user at
  Round 4 (names, targets, tolerances — NOT part numbers).

Derived fields (cascade NF, total gain, cost estimate) are **not**
in the lock — they are recomputed on demand. Storing derived values
would create two sources of truth.

Part-number selection is **not** part of the lock. Parts change over
the product lifecycle (obsolescence, qualification); the lock only
pins the functional requirements. A part-number diff does not
invalidate the lock.

### 2. How the hash is computed

`RequirementsLock.compute_hash()` canonicalises the `content` dict
(`json.dumps(..., sort_keys=True, separators=(",", ":"))`) and returns
the SHA-256 hex digest. The hash is stable across Python processes and
across machines as long as `content` is semantically equal. Content
ordering does not matter; set-valued fields (e.g. `cited_standards`) are
sorted by their canonical `(standard, clause)` tuple before hashing.

### 3. What invalidates a lock

A lock is considered invalid (its `verify()` returns False) when any of
the following is true:

- `RequirementsLock.compute_hash()` on the stored `content` does not
  equal the stored `hash`. This catches tampering or schema drift.
- The stored lock has `frozen_at is None`. Lock objects that have not
  been frozen cannot be persisted (see 4).

### 4. When a lock can be created

`RequirementsLock.freeze()` raises if:

- `content["round4_confirmed"] is not True`.
- Any of `round1_answers`, `round2_architecture`, `round3_followups` is
  missing or empty (we do not accept "mostly confirmed" locks).

Once frozen, `frozen_at` is stamped (UTC seconds since epoch) and
`hash` is populated. `save_to_row(project_row)` then writes the three
columns `requirements_hash`, `requirements_frozen_at`,
`requirements_locked_json` on the `projects` table.

### 5. What counts as "stale"

A phase's output is **stale** if the current project lock hash differs
from the lock hash recorded on the phase's most recent `pipeline_runs`
row (`pipeline_runs.requirements_hash_at_run`). That comparison is
exact: no partial-match semantics, no field-level diffing.

Staleness is phase-specific. Re-locking the requirements does NOT
automatically re-run anything; it only flips the downstream phases from
`completed` to `stale`. The user must explicitly kick off re-runs (see
`scripts/run_baseline_eval.py` for the batch flavour;
`services/stale_phases.py` exposes the helper used by FastAPI).

### 6. What a re-lock means

A re-lock is NOT an edit in place. `freeze()` on a project that already
has a lock produces a new `(hash, frozen_at)` pair. The OLD lock is
overwritten on the `projects` row, but every previous `pipeline_runs`
entry still carries the old hash value, preserving the provenance of
each artefact.

### 7. Who is authorised to re-lock

The P1 agent is the only entity that writes the lock. The red-team
audit agent can recommend a re-lock (e.g. when it detects that the
cascade does not meet the claimed sensitivity), but the user must drive
the P1 flow forward before `freeze()` is permitted.

### 8. What the lock does NOT do

- It does not encrypt or sign the content. Integrity is by hash
  equality; confidentiality is the enclosing application's job.
- It does not enforce backwards-compatible schema evolution. If the
  `content` schema changes, every previously frozen lock becomes stale
  until re-frozen. This is intentional: we want schema drift to be
  visible, not silent.
- It does not manage *version history* of the lock. Snapshotting
  previous lock states is a Tier-3 roadmap item
  ("Requirement version history" in CLAUDE.md).

---

## Alternatives considered

### A. Store a structured diff between runs instead of a hash

**Pros:** friendlier UX — "this field changed, this is why P2 is stale".
**Cons:** complex to implement correctly for every field type; easy to
miss cases (e.g. nested list reordering). Hash-based staleness is
trivial to reason about: equal hash, no change.

### B. Hash only the derived values (NF, gain, sensitivity targets)

**Pros:** smaller surface; downstream phases only care about derived
outputs.
**Cons:** derived values can collide across semantically different
requirement sets (e.g. two different modulation choices can both
require 1.5 dB NF). A content-based hash is unambiguous.

### C. Git-style commit per lock change

**Pros:** lock history for free; diffs are first-class.
**Cons:** requires a second storage backend alongside SQLite; out of
scope for the hackathon deliverable. Acceptable future direction.

---

## Consequences

- **Positive:** downstream phases can detect staleness from a single
  column comparison. The red-team audit can reject phases whose lock
  hash no longer matches. Judges can reproduce a run by reading the
  `requirements_locked_json` blob out of the DB and replaying the
  deterministic half via `scripts/reproduce_run.py`.
- **Negative:** schema evolution of the lock content requires a
  migration and a re-lock pass. We accept this cost.
- **Testing implication:** `tests/test_requirements_lock.py` exercises
  all six failure modes (hash stability, tampering, unfrozen rejection,
  roundtrip save/load, staleness detection, content equality).

---

## Revisit criteria

This ADR should be revisited if any of the following become true:

- We need to store multiple historical locks per project (version
  history); a structured table would replace the single-row columns.
- Downstream phases need field-level staleness (e.g. re-run P3 only if
  compliance citations changed). Today's all-or-nothing rule is coarse.
- The lock blob exceeds ~64 KB on real projects. If so we may move it
  to an external blob store and keep only the hash on the projects row.

Until one of those triggers, the single-column lock stands.
