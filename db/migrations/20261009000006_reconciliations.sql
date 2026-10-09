-- migrate:up
-- Reconciliations: once a trust bank account's reconciliation is approved, its period is closed.
--
-- A reconciliation records, for one trust bank account and one period: the bank statement's
-- ending balance (entered by hand; bank files are out of Phase 0), the book balance the ledger
-- shows at the period's end (computed at approval), who prepared it and who approved it. There
-- is no login in Phase 0, so preparer and approver are recorded names, not checked users.
--
-- Approved means locked:
--   - the record can't change (append-only, like the ledger);
--   - the trust bank account is closed through the period's end: any transfer dated (event_at)
--     before it that touches an account in that trust bank account is refused, whoever posts
--     it. A late correction is dated in the next open period.
-- Periods follow one another with no gap and no overlap: each starts where the last approved
-- one ended. A period is approved only once it has ended, so today's postings are never locked.
--
-- trust_bank_accounts.closed_through holds where the account is closed through: the end of its
-- newest approved reconciliation, NULL before the first. A trigger refuses any other value, from
-- any role, so a closed period can't be reopened by editing it. An approval moves it forward and
-- a posting reads it, under that row's lock: a posting waits for an approval in progress and
-- then sees the new date, and an approval waits for postings in flight and then counts them in
-- its book balance.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_bank_accounts ADD COLUMN closed_through timestamptz;

CREATE TABLE trust_reconciliations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bank_account_id uuid NOT NULL,
    period_start timestamptz NOT NULL,
    period_end timestamptz NOT NULL,  -- exclusive: the account is closed through this instant
    statement_balance numeric NOT NULL,
    book_balance numeric NOT NULL,
    prepared_by text NOT NULL CHECK (prepared_by <> ''),
    approved_by text NOT NULL CHECK (approved_by <> ''),
    approved_at timestamptz NOT NULL DEFAULT now(),
    CHECK (period_end > period_start),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id)
);

CREATE INDEX trust_reconciliations_bank_account_id
ON trust_reconciliations (bank_account_id, period_end);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_reconciliations
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

REVOKE ALL ON trust_reconciliations FROM PUBLIC;
GRANT SELECT ON trust_reconciliations TO trust_app;

-- Approve a reconciliation and close its period. Runs as the owner: the app has no write grant
-- on trust_reconciliations, and only this function moves trust_bank_accounts.closed_through.
CREATE FUNCTION trust_approve_reconciliation(
    p_pmc_id uuid,
    p_bank_account_id uuid,
    p_period_start timestamptz,
    p_period_end timestamptz,
    p_statement_balance numeric,
    p_prepared_by text,
    p_approved_by text
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_closed_through timestamptz;
    v_book_balance numeric;
    v_id uuid;
BEGIN
    -- The book balance must count every posting that committed while this waited for the lock
    -- below. At REPEATABLE READ or SERIALIZABLE it would read a snapshot taken before them.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: approve a reconciliation at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;

    IF p_period_end > now() THEN
        RAISE EXCEPTION 'trust: the period ends at %, which has not come yet', p_period_end
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Approve a reconciliation once its period has ended.';
    END IF;

    -- Waits for postings in flight on this account (they hold the row FOR SHARE), and holds new
    -- ones off until this commits.
    SELECT b.closed_through INTO v_closed_through
    FROM trust_bank_accounts AS b
    WHERE b.id = p_bank_account_id AND b.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no trust bank account % in PMC %', p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF p_period_start <> v_closed_through THEN
        RAISE EXCEPTION 'trust: the next period of trust bank account % starts at %',
            p_bank_account_id, v_closed_through
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Periods follow one another: no gap, no overlap.';
    END IF;

    -- The cash the books say the bank held at the period's end: -balance of its bank_cash.
    SELECT coalesce(-sum(e.amount), 0) INTO v_book_balance
    FROM trust_ledger_accounts AS t
    JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
    JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
    WHERE t.bank_account_id = p_bank_account_id AND t.kind = 'bank_cash'
      AND tr.event_at < p_period_end;

    INSERT INTO trust_reconciliations (
        pmc_id, bank_account_id, period_start, period_end, statement_balance, book_balance,
        prepared_by, approved_by
    ) VALUES (
        p_pmc_id, p_bank_account_id, p_period_start, p_period_end, p_statement_balance,
        v_book_balance, p_prepared_by, p_approved_by
    ) RETURNING id INTO v_id;

    UPDATE trust_bank_accounts SET closed_through = p_period_end WHERE id = p_bank_account_id;

    RETURN v_id;
END;
$$;

REVOKE ALL ON FUNCTION trust_approve_reconciliation(
    uuid, uuid, timestamptz, timestamptz, numeric, text, text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_approve_reconciliation(
    uuid, uuid, timestamptz, timestamptz, numeric, text, text
) TO trust_app;

-- closed_through is always the end of the newest approved reconciliation (NULL before the
-- first), so no role reopens a period, or closes one, by writing it directly.
CREATE FUNCTION trust_refuse_moving_closed_through() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NEW.closed_through IS DISTINCT FROM (
        SELECT max(r.period_end) FROM trust_reconciliations AS r
        WHERE r.bank_account_id = NEW.id
    ) THEN
        RAISE EXCEPTION 'trust: trust bank account % is closed through the end of its newest '
            'approved reconciliation, not %', NEW.id, NEW.closed_through
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Only trust_approve_reconciliation moves it.';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_closed_through
BEFORE INSERT OR UPDATE OF closed_through ON trust_bank_accounts
FOR EACH ROW EXECUTE FUNCTION trust_refuse_moving_closed_through();

-- Refuse a transfer dated inside a closed period of either side's trust bank account.
CREATE FUNCTION trust_refuse_posting_into_closed_period() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank uuid;
    v_closed_through timestamptz;
BEGIN
    -- FOR SHARE waits for an approval in progress on either account and then reads the date
    -- it set. At REPEATABLE READ or SERIALIZABLE, an approval that committed after this
    -- transaction's snapshot makes the lock fail with a serialization error instead.
    FOR v_bank, v_closed_through IN
        SELECT b.id, b.closed_through
        FROM trust_bank_accounts AS b
        JOIN trust_ledger_accounts AS t ON t.bank_account_id = b.id
        WHERE t.ledger_account_id IN (NEW.from_account_id, NEW.to_account_id)
        ORDER BY b.id
        FOR SHARE OF b
    LOOP
        IF NEW.event_at < v_closed_through THEN
            RAISE EXCEPTION 'trust: trust bank account % is reconciled and closed through %; '
                'a transfer dated % can''t be posted', v_bank, v_closed_through, NEW.event_at
                USING ERRCODE = 'check_violation',
                      HINT = 'Date the correction in the next open period.';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_closed_period
BEFORE INSERT ON pgledger_transfers
FOR EACH ROW EXECUTE FUNCTION trust_refuse_posting_into_closed_period();

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'reconciliations cannot be rolled back: approved reconciliations are records';
END;
$$;
