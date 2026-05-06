-- Migration 008 — DesignManifest persistence (Step 1 of the SSoT refactor).
--
-- Adds two columns to the `projects` table:
--   - design_manifest_json : full frozen DesignManifest as canonical JSON
--   - manifest_hash        : top-level SHA256 mirrored for cheap stale-detection
--                            without parsing the JSON blob
--
-- Both nullable: legacy projects predating the manifest continue to work; the
-- manifest is built the next time P1 finalises.
--
-- This SQL is informational — the live migration runs from
-- migrations/__init__.py::_apply_008 (idempotent column-exists check) so it
-- can be safely re-applied on every FastAPI startup.

ALTER TABLE projects ADD COLUMN design_manifest_json TEXT;
ALTER TABLE projects ADD COLUMN manifest_hash TEXT;
