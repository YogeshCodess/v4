-- Migration 004 — add project_type to projects table.
-- Distinguishes receiver (default) vs transmitter so the P1 wizard can
-- show the right architecture catalogue and the agent prompt includes
-- the TX supplement when appropriate.
ALTER TABLE projects
  ADD COLUMN project_type TEXT NOT NULL DEFAULT 'receiver';
