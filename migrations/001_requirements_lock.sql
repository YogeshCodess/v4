-- 001_requirements_lock.sql
-- Adds requirements-lock columns to the projects table.
-- Idempotent application is handled by migrations/__init__.py:_apply_001.
-- Kept as a .sql file for humans to read / grep / apply manually if needed.

ALTER TABLE projects ADD COLUMN requirements_hash TEXT;
ALTER TABLE projects ADD COLUMN requirements_frozen_at DATETIME;
ALTER TABLE projects ADD COLUMN requirements_locked_json TEXT;
