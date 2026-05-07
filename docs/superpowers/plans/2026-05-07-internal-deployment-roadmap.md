# Internal Deployment Roadmap — Data Patterns India

> **For agentic workers:** This is a TOP-LEVEL ROADMAP. Each milestone below has its own future sub-plan with bite-sized TDD tasks. When the user says "implement milestone N", run `superpowers:writing-plans` to create the detailed plan for that milestone, then `superpowers:executing-plans` or `superpowers:subagent-driven-development` to execute it. Steps in this roadmap use checkbox (`- [ ]`) syntax for tracking but are higher-granularity than per-milestone implementation plans.

**Goal:** Move the Silicon-to-Software (S2S) tool from "working hackathon-final demo" to "production engineering tool used daily by Data Patterns India design teams" — with multi-user identity, defense/aerospace-grade audit trail, optional air-gap deployment, and integration with the existing PLM + EDA toolchain.

**Architecture decisions (pre-committed):**
- PostgreSQL replaces SQLite for multi-user concurrency. SQLAlchemy already DB-agnostic; migrations port via the existing `migrations/__init__.py` mechanism.
- `services/storage.StorageAdapter` is preserved — add an S3/MinIO/SMB backend behind it without touching agents.
- OIDC for SSO (works with on-prem Keycloak, or with Active Directory via ADFS).
- LLM stays cloud-default (GLM-4.7 → DeepSeek → Anthropic) with `AIRGAP=true` flag that routes to on-prem vLLM/Ollama and disables ALL outbound HTTP.
- DesignManifest SSoT pattern (already shipped) extends to user actions: every state-changing API call is audit-logged with user/project/timestamp/before-hash/after-hash.
- Replace DigiKey/Mouser with an internal-CSV connector first. Per-customer PLM connectors (Teamcenter / Windchill / SAP) are out of scope for V1 internal deployment.

**Tech stack additions:**
- PostgreSQL 16 (deployment), aiosqlite (still works for tests / dev)
- Keycloak 24 (or AD via ADFS) for OIDC
- MinIO (or company NAS via SMB) for object storage
- vLLM (preferred) or Ollama for on-prem inference
- Sentry (optional) for error tracking; OpenTelemetry already plumbed
- WeasyPrint or ReportLab for the AS9100 Design History File PDF generator

---

## Milestone Map

| # | Milestone | Effort | Blocks | Unblocks |
|---|---|---|---|---|
| 1 | Foundation: PostgreSQL + LLM observability + audit log | 2 weeks | All others | Multi-user pilot, cost dashboard |
| 2 | Auth: OIDC SSO + RBAC + project ownership | 2 weeks | M1 | Real team deployment |
| 3 | Air-gap mode + on-prem LLM | 2 weeks | M1 | Defense customer pilot |
| 4 | Quality system hooks: AS9100 Design History File | 1 week | M1 + M2 | Quality system audit |
| 5 | PLM integration stub (internal CSV) | 1.5 weeks | M1 | Real internal BOM use |
| 6 | EDA tool exports (Altium .SchDoc + Vivado .xpr) | 3 weeks | M5 | PCB / FPGA designer handoff |

**Total**: ~12 weeks for full internal deployment. M1 → M2 → M4 unblocks first-team pilot at week 5. M3 unblocks defense/customer demos at week 7.

---

## Milestone 1 — Foundation: PostgreSQL + LLM observability + audit log

**Why first:** Every other milestone needs multi-user concurrency (PostgreSQL), and reproducibility (LLM call trace) is the foundation of the AS9100 design history file. Wire-up cost is small; payoff is large.

**Architecture:**

```
Before                          After
─────────                       ─────
SQLite (one writer)             PostgreSQL 16 (multi-writer, FOR UPDATE locks)
no `users` table                users + project_members + audit_log
llm_calls table sparsely used   ALL LLM calls write a row
no per-action audit             every state-changing API call → audit_log
no cost dashboard               POST-hoc query: tokens × $/M-token per project
```

**Files to create:**
- `migrations/009_users_and_audit.sql` — DDL for `users`, `project_members`, `audit_log`
- `migrations/__init__.py` — add `_apply_009`
- `database/models.py` — `UserDB`, `ProjectMemberDB`, `AuditLogDB`, `LlmCallDB` (already exists, will populate)
- `services/auth_local.py` — temporary local-account auth (username + bcrypt). REPLACED by OIDC in M2 but useful for dev.
- `services/audit_log.py` — `audit(user_id, action, project_id, before, after, request_id)` helper
- `services/llm_logger.py` — already exists; needs to be CALLED from `agents/base_agent.py::call_llm`
- `compose/postgres.yml` — docker-compose with Postgres 16 + pgvector (for future component embedding migration off Chroma)
- `tests/services/test_audit_log.py`, `tests/services/test_llm_logger_integration.py`, `tests/services/test_postgres_migration.py`

**Files to modify:**
- `agents/base_agent.py:call_llm()` — wire `services.llm_logger.log_call()` around every LLM call (start_span, after response, on exception)
- `database/models.py` — add Postgres async URL handling (already handles `postgresql+asyncpg://`)
- `main.py` — add audit-logging middleware that catches every state-changing route and writes to audit_log
- `config.py` — `DATABASE_URL` env reads Postgres or SQLite; `LLM_LOG_ENABLED=true` default
- `pyproject.toml` — add `asyncpg`, `psycopg2-binary` (sync), `bcrypt`

**Verification (success criteria):**
- `docker-compose -f compose/postgres.yml up -d && pytest tests/` runs the full suite against Postgres
- After running a P1 → P8 pipeline, `SELECT count(*) FROM llm_calls WHERE pipeline_run_id IS NOT NULL` returns ≥ all the LLM calls
- A complete project produces `SELECT count(*) FROM audit_log WHERE project_id = X` ≥ 50 rows (one per state-changing API call)
- `pgcli` query: `SELECT model, sum(tokens_in + tokens_out) AS total, sum(tokens_in + tokens_out) * 0.0000015 AS cost_usd FROM llm_calls WHERE pipeline_run_id IN (SELECT id FROM pipeline_runs WHERE project_id = X) GROUP BY model` produces a per-model cost breakdown

**Key tasks (high-level — TDD detail in sub-plan):**

- [ ] **1.1** docker-compose for Postgres 16 + pgvector. Migrations 001-008 run idempotently against Postgres (test in CI).
- [ ] **1.2** Migration 009: `users(id, username, email, hashed_password, role, created_at, last_login_at)`, `project_members(project_id, user_id, role, added_at)`, `audit_log(id, user_id, action, project_id, before_hash, after_hash, request_id, created_at, payload_json)`.
- [ ] **1.3** `UserDB`/`ProjectMemberDB`/`AuditLogDB` SQLAlchemy models. Tests for relationship cascades.
- [ ] **1.4** `services/auth_local.py` — bcrypt-hashed local accounts. `POST /auth/login` returns a session cookie. Replaced in M2; intentional throwaway scope.
- [ ] **1.5** `services/audit_log.py::audit(user_id, action, project_id, before, after, request_id)` — async helper writing one row per state change. Hash `before` / `after` with the same canonical-JSON SHA256 the manifest uses.
- [ ] **1.6** Wire `services/llm_logger.py` into `agents/base_agent.py::call_llm`. Every call writes one `llm_calls` row with `pipeline_run_id` (read from contextvars or `project_context`), `model`, `temperature`, `prompt_sha256`, `response_sha256`, `tokens_in`, `tokens_out`, `latency_ms`, `tool_calls_json`. Skip raw payload to avoid leaking IP per ITAR.
- [ ] **1.7** FastAPI middleware `audit_middleware` that wraps every state-changing route (POST/PATCH/DELETE) and emits an audit_log row with the request user (from session cookie).
- [ ] **1.8** Cost dashboard SQL queries documented in `docs/operator/cost-dashboard.sql` (or `metabase` queries if MB is in use).
- [ ] **1.9** Object-storage backend for `services/storage.StorageAdapter`. Add `S3StorageBackend` (works with MinIO via S3-compatible API) and `SmbStorageBackend` (for company NAS via `pysmb`). `STORAGE_BACKEND=local|s3|smb` env var picks one. Tests round-trip bytes through each backend.
- [ ] **1.10** Migration from local-disk to S3/MinIO: `scripts/migrate_outputs_to_s3.py` reads existing `output/<project>/` and uploads with the same key layout. Idempotent — safe to re-run.

**Commit cadence:** ~1 commit per task above. Total ~10 commits.

---

## Milestone 2 — Auth: OIDC SSO + RBAC + project ownership

**Why second:** Local accounts from M1 don't fit into Data Patterns' existing identity infra. OIDC works with on-prem Keycloak (which can also bridge to Active Directory via LDAP federation).

**Architecture:**

```
                         ┌───────────────┐
        Browser ────────▶│ FastAPI       │
        (with PKCE)      │   /auth/oidc  │──── Keycloak (or AD via ADFS)
                         │   middleware  │     (issues JWT)
                         └───────────────┘
                                 │
                                 ▼
                         users.id resolved
                                 │
                                 ▼
                         RBAC check on
                         project_members
```

**RBAC roles:**
- `viewer` — read project + outputs, can't trigger phases or modify
- `editor` — can run phases, edit P1 chat, approve & run pipeline
- `owner` — editor + can add/remove members + delete project
- `admin` (system-wide) — can see all projects, intended for tool ops, recorded specially in audit log

**Files to create:**
- `services/auth_oidc.py` — discovery, PKCE, token exchange, JWT verify, user provisioning on first login
- `services/rbac.py` — `require_role(project_id, role)` dependency injection helper
- `tests/services/test_auth_oidc.py` — mock OIDC server (`pytest-httpx`)
- `tests/services/test_rbac.py` — table-driven role × action × outcome matrix
- `hardware-pipeline-v5-react/src/auth/AuthContext.tsx` — login redirect, token refresh, current-user state
- `hardware-pipeline-v5-react/src/auth/LoginPage.tsx` — landing page when unauthenticated

**Files to modify:**
- `main.py` — replace `APP_PASSWORD` middleware with OIDC middleware. Add `/auth/oidc/login`, `/auth/oidc/callback`, `/auth/logout`, `/auth/me`.
- Every router function that takes `project_id` — add `Depends(require_role(project_id, "viewer"|"editor"|"owner"))`.
- `services/project_service.py::create()` — add `owner_user_id` parameter; insert `project_members(project_id, owner_user_id, "owner")` row.
- `services/audit_log.py::audit()` — record real `user_id` from request context (no more `system` placeholder).
- `hardware-pipeline-v5-react/src/App.tsx` — wrap in `<AuthProvider>`; redirect to LoginPage when no session.

**Verification:**
- Unauthenticated request to `GET /api/v1/projects` returns 401, with a `Location` header pointing to OIDC login.
- After login, `/auth/me` returns `{user_id, username, email, system_role, project_memberships: [...]}`.
- Viewer trying `POST /phases/P2/execute` gets 403, audit log has the rejected attempt.
- Editor → owner promotion flips RBAC table; downgraded user immediately loses delete access on next call.
- All existing 500+ services + agents tests still pass with mocked auth context.

**Key tasks:**

- [ ] **2.1** Keycloak compose service alongside Postgres. Test realm + test client + 2 test users.
- [ ] **2.2** `services/auth_oidc.py`: PKCE flow, ID token verify (RS256), refresh token, user provisioning.
- [ ] **2.3** `services/rbac.py`: `require_role(project_id, min_role)` FastAPI dependency. Read membership from `project_members`. System admins always pass.
- [ ] **2.4** Add Depends to every state-changing route in `main.py`. Read-only routes get `viewer` minimum.
- [ ] **2.5** `services/project_service.py::create()` writes ownership row. Migrate existing data: backfill `system` user as owner of all pre-M2 projects.
- [ ] **2.6** Frontend `<AuthProvider>` + login redirect + token refresh + logout button.
- [ ] **2.7** Documentation: `docs/operator/oidc-setup.md` — Keycloak realm setup, client config, AD federation steps.
- [ ] **2.8** Project classification field. Even on internal-only deployment, defense projects carry sensitivity tags. Add `projects.classification` column (one of `unclass | internal | itar | secret`, default `internal`). RBAC enforces: `itar` and `secret` projects only visible to users with the matching `users.clearance` level. Migration `010_classification.sql`. UI shows a coloured badge in the topbar + project list. Audit log entries on `itar`/`secret` projects are flagged for compliance review.

---

## Milestone 3 — Air-gap mode + on-prem LLM

**Why third:** Required for any defense / aerospace customer site or any project flagged ITAR. Cloud LLM calls leak design IP.

**Architecture:**

```
.env
  AIRGAP=true
  ON_PREM_LLM_URL=http://vllm-internal:8000/v1
  ON_PREM_LLM_MODEL=Qwen3-32B-Instruct
  ON_PREM_LLM_API_KEY=internal-token

  Disables (when AIRGAP=true):
    - DigiKey API
    - Mouser API
    - mermaid.ink (uses local mmdc instead)
    - datasheet HEAD probes (already controlled by SKIP_DATASHEET_VERIFY)
    - All cloud LLM (GLM, DeepSeek, Anthropic)

  Enables:
    - vLLM/Ollama at ON_PREM_LLM_URL (uses OpenAI-compatible /v1/chat/completions)
    - Local mermaid CLI for diagram rendering
    - Audit-log entry for every blocked outbound HTTP attempt
```

**Files to create:**
- `services/airgap.py` — single `IS_AIRGAPPED` constant + `block_egress(url, reason)` helper
- `agents/llm_clients/vllm_client.py` — OpenAI-compatible client targeting on-prem vLLM
- `tests/services/test_airgap.py` — verify every cloud SDK call is blocked when `AIRGAP=true`
- `docs/operator/airgap-deployment.md` — operator runbook for airgap site

**Files to modify:**
- `agents/base_agent.py` — fallback chain rebuilds: when `AIRGAP=true`, the chain is `[vllm_internal]` only. No cloud fallback.
- `tools/digikey_api.py`, `tools/mouser_api.py`, `tools/datasheet_verify.py`, `tools/datasheet_url.py` — guard every outbound `httpx`/`requests` call with `if airgap.IS_AIRGAPPED: raise BlockedEgressError(url)`. The error is caught by the calling tool which falls back to local data.
- `tools/mermaid_render.py` — already supports local `mmdc`; mark `mermaid.ink` path airgap-blocked.
- `services/component_cache.py` — already has Chroma (local). Confirm pgvector migration path so airgap deployments don't depend on Chroma's process model.
- `requirements.txt` / `pyproject.toml` — pin `openai` SDK version that works with vLLM `/v1` endpoint.

**Verification:**
- `AIRGAP=true python -m pytest tests/integration/test_no_egress.py` — runs a full P1-P8 pipeline against vLLM with `AIRGAP=true`. Test fixture wraps `httpx.Client` and asserts NO outbound request leaves the local network.
- `tcpdump -i eth0 -n 'host !internal-net'` during a pipeline run — zero packets out.
- LLM call quality benchmark: rerun the 30 golden eval scenarios on the on-prem model. Document quality delta vs GLM-4.7 in `docs/operator/airgap-llm-quality.md`. Quality SHOULD drop ~20% (Qwen3-32B vs GLM-4.7) — operators need to know.
- Datasheet verify falls back to the `data/component_specs/` curated library cleanly (no exceptions).

**Key tasks:**

- [ ] **3.1** `services/airgap.py` with `IS_AIRGAPPED` boolean + `block_egress` helper that raises `BlockedEgressError` and writes to audit_log.
- [ ] **3.2** Wrap every outbound HTTP in tools/ with the egress guard. Each tool catches the exception and falls back to local data.
- [ ] **3.3** `agents/llm_clients/vllm_client.py` — OpenAI-compatible client. Tested against a docker-compose vLLM service.
- [ ] **3.4** `agents/base_agent.py` fallback-chain rebuilder reads `AIRGAP` and constructs vLLM-only chain.
- [ ] **3.5** Quality benchmark harness: run golden evals against vLLM, write report.
- [ ] **3.6** Operator runbook `docs/operator/airgap-deployment.md` with network diagram, vLLM sizing recommendations, model-quality caveats.

---

## Milestone 4 — Quality system hooks: AS9100 Design History File

**Why fourth:** AS9100 (and ISO 9001) require a Design History File (DHF) per project showing every design decision, who made it, why, and how it was verified. The audit_log + manifest hashes from M1 are the data; M4 builds the report.

**Architecture:**

```
GET /api/v1/projects/{id}/dhf.pdf
   → services/dhf.py::generate_dhf(project_id)
      → reads:
          projects (identity + frozen_at)
          design_manifest_json (the spec)
          audit_log (chronological actions)
          pipeline_runs (phase executions)
          llm_calls (LLM-assisted decisions, hash-only)
      → emits a 30-50 page PDF with:
          1. Project Identity + Approval signatures
          2. Frozen DesignManifest + audit verdict
          3. Requirement traceability matrix (REQ-HW → REQ-SW → verification)
          4. Component selection journal (every BOM change, who approved, when)
          5. Phase execution log (timestamps, durations, model-versions)
          6. Audit findings + resolutions
          7. AS9100 conformance checklist
```

**Files to create:**
- `services/dhf.py` — `generate_dhf(project_id) -> bytes` (PDF). Pure function of database state — reproducible.
- `services/dhf_sections/` — one renderer per DHF section (identity, manifest, traceability, components, executions, audits, checklist)
- `tests/services/test_dhf.py` — golden test: known project state → byte-identical PDF (modulo timestamp + page numbers)
- `templates/dhf/cover_page.html` (WeasyPrint)
- `templates/dhf/checklist.csv` (AS9100 clauses 7.3.x)

**Files to modify:**
- `main.py` — `GET /api/v1/projects/{id}/dhf.pdf` endpoint, RBAC: viewer minimum
- `hardware-pipeline-v5-react/src/views/DocumentsView.tsx` — add "Download DHF" button visible only to project members

**Verification:**
- For a complete project, `GET /dhf.pdf` returns a valid PDF (libqpdf parses without error)
- The PDF references the locked manifest_hash on its cover; that hash matches `projects.manifest_hash`
- Audit log entries appear chronologically with user names (not user IDs)
- AS9100 checklist references real clauses and links to evidence in the document

**Key tasks:**

- [ ] **4.1** WeasyPrint vs ReportLab decision (WeasyPrint preferred — HTML+CSS templates are easier for engineers to edit). Pin version.
- [ ] **4.2** `services/dhf.py::generate_dhf` skeleton + per-section renderers + master template. Each section is a separate function, tested independently.
- [ ] **4.3** AS9100 conformance checklist hard-coded in `templates/dhf/checklist.csv` — clause id → check description → evidence query. The checklist evolves; it's data-driven.
- [ ] **4.4** Golden test: a fixture project state → SHA256(PDF text content) is stable across runs.
- [ ] **4.5** Frontend "Download DHF" button + loading state.

---

## Milestone 5 — PLM integration stub (internal CSV)

**Why fifth:** Replaces DigiKey/Mouser with a Data-Patterns-approved-parts CSV. Most defense / aerospace shops have a "qualified parts list" (QPL) — non-QPL parts can't ship in a flight system. The current pipeline has no concept of QPL.

**Architecture:**

```
data/internal_qpl.csv          (Data Patterns approved-parts list)
columns: mpn, manufacturer, role, package, freq_min_ghz, freq_max_ghz,
         nf_db, gain_db, iip3_dbm, p1db_dbm, supply_v, current_ma,
         lifecycle, qpl_status (qualified|alt|forbidden), datasheet_path

services/internal_qpl.py       new
  load_qpl() -> dict[mpn, row]
  search(role, freq_min, freq_max, ...) -> list[row]
  is_qualified(mpn) -> bool

tools/parametric_search.py     modified
  When AIRGAP=true OR USE_INTERNAL_QPL=true:
    find_candidates() reads from internal_qpl instead of DigiKey/Mouser.
    The fallback chain becomes: internal_qpl → curated specs → return empty.

services/rf_audit.py           modified
  Add rule 13: qpl_compliance.
  Every BOM part: is_qualified(mpn) ?
    - qualified  → silent
    - alt        → medium advisory ("alternate part — confirm with project lead")
    - not in QPL → critical (block)
    - forbidden  → critical (block)
```

**Files to create:**
- `data/internal_qpl.csv` — sample 50-row QPL covering common roles (LNA, mixer, ADC, PLL, regulator, MCU). Real one is customer-supplied.
- `services/internal_qpl.py` — load + search + is_qualified API
- `services/qpl_audit.py` — `run_qpl_audit(bom, design_parameters) -> list[AuditIssue]`
- `tests/services/test_internal_qpl.py` — load + search + qualification tests
- `tests/services/test_qpl_audit.py` — audit rule tests
- `docs/operator/qpl-format.md` — CSV schema for customer-supplied QPLs

**Files to modify:**
- `tools/parametric_search.py::find_candidates()` — add `USE_INTERNAL_QPL` branch
- `services/rf_audit.py::run_all()` — add rule 13 (`run_qpl_audit`)
- `agents/requirements_agent.py::FIND_CANDIDATE_PARTS_TOOL` — schema description mentions QPL when active
- `services/component_spec_resolver.py` — internal_qpl rows take priority over LLM extraction (similar to curated specs)

**Verification:**
- `USE_INTERNAL_QPL=true python -m pytest tests/services/test_qpl_audit.py` passes
- A project that picks a non-QPL MPN gets a critical audit issue: "Part X is not on the qualified parts list. Replace with one of the QPL alternatives: [..]"
- The audit suggestion lists 3 alternatives from the QPL with the same role
- Pipeline fails P1 lock with `audit_pass=false` when any non-QPL part is in the BOM

**Key tasks:**

- [ ] **5.1** CSV schema design + sample data + format documentation
- [ ] **5.2** `services/internal_qpl.py` with cached load (LRU 1)
- [ ] **5.3** `services/qpl_audit.py::run_qpl_audit` + tests
- [ ] **5.4** Wire into `find_candidates` and `rf_audit.run_all`
- [ ] **5.5** Customer-data import script `scripts/import_qpl.py` that reads a customer's QPL Excel sheet and writes the canonical CSV

---

## Milestone 6 — EDA tool exports (Altium .SchDoc + Vivado .xpr)

**Why sixth:** Final-mile productivity. The current pipeline produces deterministic netlists + GLR + register maps, but the PCB designer still has to recreate them in Altium and the FPGA designer recreates them in Vivado. Direct export saves 1-2 days per project.

**Architecture:**

Altium export:
```
schematic.json (post-M1, deterministic) ──┐
                                          ▼
                          tools/altium_exporter.py
                                          │
                                          ▼
                          schematic.SchDoc + .OutJob
                              (Altium binary format
                               or .NetList in tab format)
```

Vivado export:
```
glr_specification.md + register_map + pinout ──┐
                                                ▼
                            tools/vivado_exporter.py
                                                │
                                                ▼
                            project.tcl + project.xpr
                            (sources block, IP cores,
                             constraint .xdc, top-level .v)
```

**Files to create:**
- `tools/altium_exporter.py` — `export_schdoc(schematic_json) -> bytes`. Two output options:
  - `.SchDoc` (binary, requires reverse-engineered format library — risky)
  - `.NetList` (tab-separated text — Altium can import this and auto-place; simpler, more reliable)
- `tools/vivado_exporter.py` — `export_vivado(glr, register_map, pinout) -> dict[str, bytes]`. Outputs `project.tcl`, `project.xdc`, top-level `.v`.
- `tests/tools/test_altium_exporter.py` — round-trip: schematic.json → .NetList → re-parse → original
- `tests/tools/test_vivado_exporter.py` — TCL syntax validation + xdc constraint format

**Files to modify:**
- `agents/netlist_agent.py` — after schematic.json synthesis, also write `netlist.altium.NetList`
- `agents/fpga_agent.py` — after generating Verilog + XDC, write `project.vivado.tcl` + `project.vivado.xpr` placeholder
- `hardware-pipeline-v5-react/src/views/DocumentsView.tsx` — Altium / Vivado export buttons in P4 / P7 docs panels

**Verification:**
- `tools/altium_exporter.py` output imports cleanly into Altium 23 (manual verification + screenshot in PR)
- `vivado -mode batch -source project.tcl -tclargs --create_project` succeeds in a Vivado 2023.2 docker container
- All BOM MPNs from `manifest.bom` appear in the Altium export (leak gate runs against the export bytes)

**Key tasks:**

- [ ] **6.1** Pick Altium output format: `.NetList` (tab-format, simpler) over `.SchDoc` (binary, reverse-engineered). Decision documented in `docs/eda/altium-format-choice.md`.
- [ ] **6.2** `tools/altium_exporter.py` v1: schematic.json → .NetList. 90% of refdes types covered.
- [ ] **6.3** `tools/vivado_exporter.py` v1: GLR + register map → project.tcl + .xdc + top.v. Uses Vivado's TCL automation API.
- [ ] **6.4** Frontend export buttons + download flow.
- [ ] **6.5** Documented manual-verification checklist for each new BOM shape until automated import-test stabilises.

---

## Cross-cutting concerns

### Backups + DR
- **Backup**: `pg_dump` nightly to MinIO bucket. Tested restore quarterly. Document in `docs/operator/backup-restore.md`.
- **DR**: PostgreSQL replication is overkill for V1. Daily backup + on-call runbook is enough. Revisit when usage justifies.

### Migration from current SQLite
- Provide `scripts/sqlite_to_postgres.py` that reads existing SQLite DB and emits SQL inserts compatible with the new schema. ~200 LoC, ~half day.

### Versioning
- API: `/api/v1/...` already namespaced. Stay on v1; breaking changes go to v2 in a future milestone, not within these 6.
- DesignManifest: `schema_version` field already exists ("2.0"). Increment when the BOM hash inputs change.
- Database: keep the migrations approach (idempotent column-exists checks).

### Documentation deliverables
- `docs/operator/` — deployment, OIDC, airgap, backup-restore, qpl-format, cost-dashboard.sql
- `docs/architect/` — DesignManifest pattern, RBAC model, audit-log schema
- `docs/user/` — pipeline workflow, P1 chat tips, troubleshooting
- README updated with the new deployment options

### Performance budgets to validate (in M1 / M3)
- Postgres can handle 10 concurrent users running pipelines (each pipeline ≈ 10 min, ≈ 50 LLM calls). Each LLM call writes to llm_calls. Each phase writes ≈ 5 audit_log rows. Concurrent-write throughput must stay above 100 inserts/sec.
- vLLM throughput: ≥ 30 tokens/sec per request, ≥ 4 concurrent requests on a single A100 80GB. Below that, bench against a smaller model.

---

## Risks

| Risk | Mitigation |
|---|---|
| OIDC integration with Active Directory takes longer than 1 week | Build against Keycloak first (controlled env); AD bridge happens in parallel |
| On-prem LLM quality drop is bigger than 20% | Run quality benchmark in week 1 of M3, decide whether to spec a larger on-prem model (70B) or accept |
| Customer QPL formats vary widely | Define our canonical CSV schema; force conversion at import; don't try to support every customer's format natively |
| Altium binary format proves too brittle | Ship `.NetList` (tab-format) as primary; binary `.SchDoc` is best-effort |
| Postgres migration from SQLite reveals data quirks | Run on a test database first; keep SQLite read-only as backup for 30 days |
| LLM call logging adds significant overhead | `llm_calls` table is hash-only (no raw payload); inserts batched in a background task |

---

## How sub-plans get created

When the user says "implement Milestone N":

1. Create `docs/superpowers/plans/2026-XX-XX-milestone-N-<name>.md` with bite-sized TDD tasks (the writing-plans skill produces this from this roadmap).
2. Create a worktree for the milestone: `git worktree add ../<repo>-milestone-N -b milestone-N main`.
3. Execute via `superpowers:subagent-driven-development` (preferred — fresh subagent per task) or `superpowers:executing-plans` (inline batches).
4. Each milestone ends with a PR + integration test + tag.

---

## Sequencing decision tree

```
"We need to start somewhere — what unblocks the most?"
                       │
                       ▼
            ─── Always Milestone 1 ───
                       │
                       ▼
        "Do we have a defense / aerospace customer in pilot?"
                       │
              YES ─────┴───── NO
               │               │
               ▼               ▼
            Milestone 3      Milestone 2
            (air-gap)        (OIDC SSO)
               │               │
               ▼               ▼
        Milestone 2     Milestones 3 + 4
            │           (parallel)
            ▼
        Milestone 4
            │
            ▼
        Milestones 5 + 6 (parallel — different teams)
```

---

## Effort summary

| Milestone | Calendar weeks | Engineer-weeks |
|---|---|---|
| 1 | 2 | 4 (1 backend + 0.5 ops + 0.5 frontend) × 2 weeks |
| 2 | 2 | 3 (1 backend + 1 frontend + 0.5 ops) × 2 weeks |
| 3 | 2 | 4 (1 backend + 1 ML + 0.5 ops + 0.5 docs) × 2 weeks |
| 4 | 1 | 1.5 (1 backend + 0.5 frontend) × 1 week |
| 5 | 1.5 | 2 (1 backend + 0.5 backend) × 1.5 weeks |
| 6 | 3 | 4 (2 backend with EDA experience) × 3 weeks |
| **Total** | **11.5 calendar weeks** | **~19 engineer-weeks** |

With a 2-engineer team running M1→M2→M3+4→M5+6 sequentially, **calendar duration ≈ 12 weeks for full deployment**. With 4 engineers running M3+M4+M5+M6 in parallel after M2 lands, **calendar duration ≈ 8 weeks**.

---

## Status

**Plan written:** 2026-05-07
**Author:** Claude (writing-plans skill)
**Owner:** TBD (Data Patterns India lead)
**Approval:** Awaiting decision on which milestone to start.

When ready: tell me "implement Milestone N" and I'll:
1. Create the detailed sub-plan in `docs/superpowers/plans/2026-XX-XX-milestone-N-<name>.md` (writing-plans skill, bite-sized TDD tasks)
2. Create a worktree
3. Execute via subagent-driven-development (fresh subagent per task) or inline executing-plans (batched)
