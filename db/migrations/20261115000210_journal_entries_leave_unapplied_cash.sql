-- migrate:up
-- A journal entry never moves money into or out of unapplied cash (docs/edge-cases.md M-154).
--
-- Unapplied cash is held receipt by receipt (migration 20261106000240): what is left of each is
-- what came in less what was applied to a lease or refunded (trust_unapplied_left), and the
-- account's balance is what is left of them all. A journal entry out of it left every receipt
-- reading as held, so refunding the one it meant paid out another receipt's money, which then
-- waited for good; one into it held money no receipt could apply or refund, and month-end refused
-- the trust bank account while it was there. Now a journal entry's line naming an unapplied cash
-- account is refused when proposed, and an entry proposed before this with such a line is refused
-- when posted: unapplied cash leaves only by being applied to a lease or refunded.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE FUNCTION trust_refuse_unapplied_journal_line() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_found boolean;
BEGIN
    IF TG_TABLE_NAME = 'trust_journal_entry_lines' THEN
        SELECT EXISTS (
            SELECT 1 FROM trust_ledger_accounts AS a
            WHERE a.ledger_account_id IN (NEW.from_account, NEW.to_account)
              AND a.kind = 'unapplied_cash'
        ) INTO v_found;
    ELSIF NEW.decision = 'posted' THEN
        -- An entry proposed before this migration, posted now.
        SELECT EXISTS (
            SELECT 1
            FROM trust_journal_entry_lines AS l
            JOIN trust_ledger_accounts AS a
              ON a.ledger_account_id IN (l.from_account, l.to_account)
            WHERE l.entry_id = NEW.entry_id AND a.kind = 'unapplied_cash'
        ) INTO v_found;
    ELSE
        v_found := false;  -- declining moves nothing
    END IF;
    IF v_found THEN
        RAISE EXCEPTION 'trust: a journal entry moves no money into or out of unapplied cash'
            USING ERRCODE = 'check_violation',
                  HINT = 'Apply unapplied cash to a lease or refund it from Banking.';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_refuse_unapplied_journal_line() FROM PUBLIC;

CREATE TRIGGER trust_no_unapplied_cash BEFORE INSERT ON trust_journal_entry_lines
    FOR EACH ROW EXECUTE FUNCTION trust_refuse_unapplied_journal_line();
CREATE TRIGGER trust_no_unapplied_cash BEFORE INSERT ON trust_journal_entry_decisions
    FOR EACH ROW EXECUTE FUNCTION trust_refuse_unapplied_journal_line();

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DROP TRIGGER trust_no_unapplied_cash ON trust_journal_entry_decisions;
DROP TRIGGER trust_no_unapplied_cash ON trust_journal_entry_lines;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_refuse_unapplied_journal_line();
