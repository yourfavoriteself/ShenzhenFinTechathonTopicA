-- The journal only grows. UPDATE, DELETE and TRUNCATE fail for everyone, including the owner,
-- and every new row must continue its hash chain: seq = last + 1 and prev_hash = last hash.

CREATE OR REPLACE FUNCTION journal_no_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'journal is append-only: % is not allowed', TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE OR REPLACE FUNCTION journal_chain() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE last journal%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('guardian.journal:' || NEW.chain));
    SELECT * INTO last FROM journal WHERE chain = NEW.chain ORDER BY seq DESC LIMIT 1;
    IF last.seq IS NULL THEN
        IF NEW.seq <> 0 OR NEW.prev_hash <> repeat('0', 64) THEN
            RAISE EXCEPTION 'journal chain % must start at seq 0 with the genesis hash', NEW.chain;
        END IF;
    ELSIF NEW.seq <> last.seq + 1 OR NEW.prev_hash <> last.hash THEN
        RAISE EXCEPTION 'journal chain broken at seq %: expected seq % and prev_hash %', NEW.seq, last.seq + 1, last.hash;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS journal_no_update ON journal;
CREATE TRIGGER journal_no_update BEFORE UPDATE OR DELETE ON journal
    FOR EACH ROW EXECUTE FUNCTION journal_no_change();
DROP TRIGGER IF EXISTS journal_no_truncate ON journal;
CREATE TRIGGER journal_no_truncate BEFORE TRUNCATE ON journal
    FOR EACH STATEMENT EXECUTE FUNCTION journal_no_change();
DROP TRIGGER IF EXISTS journal_chain ON journal;
CREATE TRIGGER journal_chain BEFORE INSERT ON journal
    FOR EACH ROW EXECUTE FUNCTION journal_chain();
