-- Each request runs as the application role with the client set for the transaction:
--   SELECT set_config('guardian.client_id', $1, true);
-- Rows of other clients are invisible and cannot be written; with no client set, nothing is visible.

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'guardian_app') THEN
        CREATE ROLE guardian_app NOLOGIN;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO guardian_app;
GRANT SELECT, INSERT ON journal TO guardian_app;
GRANT SELECT, INSERT, UPDATE ON mandates, holds TO guardian_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON vault_entries TO guardian_app;
GRANT SELECT, INSERT ON cases TO guardian_app;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['mandates', 'vault_entries', 'holds', 'cases'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS own_rows ON %I', t);
        EXECUTE format($p$CREATE POLICY own_rows ON %I
                         USING (client_id = current_setting('guardian.client_id', true))
                         WITH CHECK (client_id = current_setting('guardian.client_id', true))$p$, t);
    END LOOP;
END $$;
