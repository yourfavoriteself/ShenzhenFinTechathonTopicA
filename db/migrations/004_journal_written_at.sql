-- Wall-clock time when a row reached the database. The kernel's own clock (ts) can repeat across
-- restarts in the demo, so the order of chains comes from this column.
ALTER TABLE journal ADD COLUMN IF NOT EXISTS written_at timestamptz NOT NULL DEFAULT clock_timestamp();
