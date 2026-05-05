# Air-Gap Rehearsal Playbook

**Status:** Draft, April 2026. Deliverable for the Great AI Hack-A-Thon 2026
submission under IMPLEMENTATION_PLAN.md D3 (reproducibility + air-gap demo).

**Audience:** Hackathon judges, defence integrators, and programme security
officers who need to see the Silicon to Software (S2S) run end-to-end with *no
outbound network access*.

**Scope:** This document is the pre-flight checklist, the rehearsal script,
and the troubleshooting guide. It does *not* discuss classification handling
or cross-domain data transfer — those remain the responsibility of the
deploying organisation.

---

## 1. Why the air-gap rehearsal matters

Defence primes routinely operate inside enclaves with no internet egress.
Any tool that silently depends on a hosted LLM endpoint or a live datasheet
fetch is useless in that environment. The Silicon to Software (S2S) was designed so
that every phase has two operating modes:

- **Connected mode (default):** primary LLM is the current best model on the
  vendor's hosted API, with live datasheet URL verification and on-demand
  standards web lookups.
- **Air-gapped mode:** a local LLM endpoint (Ollama or equivalent) provides
  the model layer. All deterministic tools (cascade validator, requirements
  lock, clause DB, red-team audit, datasheet cache) continue to work without
  network access.

The rehearsal below proves that every phase finishes successfully with the
network physically disabled.

---

## 2. Pre-flight checklist (T minus one week)

### 2.1 Software prerequisites on the demo laptop

- Python 3.11 or later (project standard).
- Node 20 or later (only needed if rebuilding the React frontend).
- SQLite 3.35 or later (bundled with Python on most platforms).
- Optional but strongly recommended: `ollama` (https://ollama.com) with a
  locally pulled model such as `llama3.1:8b-instruct` or
  `mistral:7b-instruct`. Pull the model **before** you cut the network.

### 2.2 Pre-seeded repository state

From the connected rehearsal machine, one week before the demo, run:

```
make golden                          # 4 scenarios, deterministic pass
python scripts/verify_datasheets.py  # refreshes datasheet_verified flags
python scripts/run_baseline_eval.py  # writes eval_results/baseline_<ts>.json
python scripts/expand_component_db.py
python scripts/expand_clause_db.py
```

Commit the updated `domains/*/components.json`, `domains/standards.json`,
and `eval_results/baseline_*.json` into a branch that will be copied over to
the air-gapped laptop. These files are the "last known good" artefact set
for the rehearsal.

### 2.3 Cached model weights

If using Ollama as the local LLM backend:

```
ollama pull llama3.1:8b-instruct
ollama list
```

Confirm the model is present on the filesystem. Test once with:

```
ollama run llama3.1:8b-instruct "Respond with the exact token OK"
```

This should print `OK`. Stop Ollama when the test is done.

### 2.4 Database migrations

Run the idempotent migration script so the air-gapped copy has the same
schema as the connected machine:

```
python -c "from migrations import apply_all; print(apply_all('hardware_pipeline.db'))"
```

Expected output (abbreviated): `{"001_requirements_lock": True,
"002_pipeline_runs_llm_calls": True}`. Running it a second time should print
`False` for both (idempotent).

---

## 3. Cutting the network

Immediately before the rehearsal, physically disable network access:

- Disable Wi-Fi and unplug any Ethernet cable.
- On Linux: `nmcli networking off` for good measure.
- On macOS: toggle Wi-Fi off in the menu bar; unplug Ethernet.
- Confirm with `curl -m 3 https://example.com || echo 'no network — good'`.
  Expect the fallback message.

Do **not** connect to a mobile hotspot, conference Wi-Fi, or any other
network. The rehearsal is meant to prove independence from egress.

---

## 4. Rehearsal script

### 4.1 Open the pipeline UI

```
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/app` in a local browser. Confirm the landing
page renders and the "New Project" button works.

### 4.2 Phase-by-phase demo

1. **Create project** with Design Type = RF. No description field; the chat
   will elicit requirements in P1.
2. **P1 — Design & Requirements.** Drive the 4-round elicitation to
   completion. Confirm on the final step that the UI shows the freeze
   button and the SHA-256 hash is displayed. Lock the requirements.
3. **P2 — HRS.** Run. When complete, open the Documents tab and confirm
   the HRS markdown renders with tables of power, interfaces, and
   standards.
4. **P3 — Compliance.** Run. Open the compliance matrix and confirm every
   cited clause appears in `domains/standards.json` (use
   `validate_citations` if in doubt).
5. **P4 — Netlist.** Run. Download the `.net` file and grep for TBD/TBC/TBA
   — there should be none.
6. **P5 — PCB Layout (manual).** The UI should display the "external tool"
   note; no AI run is expected.
7. **P6 — GLR.** Run. Confirm the GLR document renders with RF spec tables
   (return loss, harmonic rejection, etc.).
8. **P7 — FPGA (manual).** Manual phase note appears; no AI run.
9. **P8a — SRS.** Run. Confirm sections and traceability matrix.
10. **P8b — SDD.** Run. Confirm architecture diagrams render.
11. **P8c — Code Review.** Run. Confirm MISRA-C and Clang-Tidy sections
    appear with severity classifications.

### 4.3 Determinism demo

From a separate terminal, run:

```
python scripts/reproduce_run.py --latest
```

Expected output (abbreviated): `lock OK:` followed by the same hash shown
in the P1 freeze, then `deterministic replay passed.`

Then re-run the baseline eval:

```
python scripts/run_baseline_eval.py
```

All 4 golden scenarios must report PASS with identical numeric values to
the pre-flight run.

### 4.4 Red-team surfacing demo

Intentionally inject a bad citation into the P1 output (e.g. `MIL-STD-461G
CS999`) and re-run the red-team audit. The UI should flag the
`unresolved_citation` issue as `high` severity and the overall_pass status
should flip to False. Revert the injection to clear the issue.

Then insert a fabricated part number (e.g. `FAKE-XYZ-9000`) with no
datasheet URL into the BOM and re-run the audit. The
`hallucinated_part` issue should appear at `critical` severity.

Finally, supply a cosite emitter pair whose IMD3 product lands in-band
(e.g. 150 MHz and 75 MHz for a 225-400 MHz receiver) and demonstrate that
`check_cosite_imd` surfaces the expected blocker.

---

## 5. Acceptance criteria

The rehearsal is considered **passed** if all of the following are true
while the network is disabled:

1. The FastAPI backend starts cleanly on `localhost:8000`.
2. The React frontend renders at `/app` without network errors in the browser
   console.
3. Every AI phase (P1, P2, P3, P4, P6, P8a, P8b, P8c) runs end-to-end using
   only the local LLM endpoint.
4. `python -m pytest` from the repository root reports zero failures.
5. `python scripts/run_baseline_eval.py` reports 4/4 PASS.
6. `python scripts/reproduce_run.py --latest` completes with zero diffs.
7. The red-team audit flags all three injected failure modes correctly.

---

## 6. Troubleshooting

- **UI shows "Failed to fetch".** The backend is not running, or the React
  bundle is pointing at a different port. Restart uvicorn on 8000 and
  reload.
- **P1 chat returns HTTP 500.** Check `logs/` for the traceback. The most
  common cause after an LLM swap is a prompt `format(**)` KeyError; see
  bug B9 in CLAUDE.md for the `%%{{init}}%%` escaping pattern.
- **Local LLM timeouts.** Start Ollama manually with `ollama serve` and
  confirm the model responds before driving the pipeline.
- **DOCX download fails.** The `cairosvg` dependency is required for PDF /
  DOCX emission with Mermaid embedding. Pre-install into the demo venv.
- **Baseline eval reports FAIL.** Check `eval_results/baseline_*.json` for
  the first failing check. If it is a cascade mismatch, regenerate the
  golden scenario's `expected_cascade` from the validator output and commit
  the update (never loosen tolerance to force a pass).

---

## 7. Post-rehearsal

After the rehearsal, before reconnecting:

- Export `hardware_pipeline.db` to a timestamped copy in `db_snapshots/`
  (created if missing). This preserves the rehearsal state for the judges'
  retrospective.
- Capture the latest `eval_results/baseline_*.json` and the `progress.md`
  file — both together are the auditable evidence that the air-gap run
  completed successfully.
- Reconnect only after the rehearsal transcripts have been reviewed.

---

## 8. References

- IMPLEMENTATION_PLAN.md — workstreams A / B / C / D.
- docs/adr/ADR-001-model-selection.md — local LLM fallback chain.
- docs/competitive_landscape.md — section 5 "Can this run air-gapped?".
- docs/eval_rubric.md — dimensions scored during the rehearsal.
- scripts/run_baseline_eval.py, scripts/reproduce_run.py.
- agents/red_team_audit.py — `check_cosite_imd` and friends.
- services/requirements_lock.py.
- migrations/ — idempotent schema install.
