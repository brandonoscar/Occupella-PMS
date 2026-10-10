-- migrate:up
-- Cutover: a PMC moving its books from another system opens each trust bank account with the
-- balances it held there.
--
-- trust_post_opening_balances takes, for one trust bank account, the first day this system
-- keeps (the cutover date) and what each account in it held at the start of that day: each
-- owner's property account, each tenant's deposit and rent paid ahead, each vendor's money set
-- aside, and the PMC's earned fees. It posts them as the account's first transfers, from its
-- cash, dated the start of that day (UTC), so book cash is their total; the caller also gives
-- that total (the old system's book cash for the account), and a line left out or mistyped
-- makes them disagree. It runs as the owner.
--
-- The opening comes first, and nothing is dated before it:
--   - a trust bank account opens once, before any posting or approved reconciliation in it;
--   - from then on, a transfer dated before the cutover that touches it is refused, whoever
--     posts it (history before the cutover stays in the old system);
--   - its first reconciliation starts at the cutover, and the next ones follow on as before.
-- Sending the same opening again returns the original transfers (safe to retry); a different
-- one is refused. A mistake in an opening is fixed like any other: with a new transfer.
--
-- trust_bank_accounts.opened_at holds the cutover's first instant, NULL for an account that
-- didn't open with balances. A trigger refuses any other value, from any role. The opening sets
-- it and a posting reads it under that row's lock, as with closed_through: a posting waits for
-- an opening in progress and then sees the new date, and an opening waits for postings in
-- flight and then sees them.
--
-- What tenants owe at the cutover stays out of the ledger, as always: their unpaid charges are
-- entered with their own due dates, so aging and late fees read them like any other.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_opening_balances (
    bank_account_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    opened_on date NOT NULL,
    -- {ledger account id: amount}, as posted.
    balances jsonb NOT NULL,
    book_cash numeric NOT NULL CHECK (book_cash > 0),
    entered_by text NOT NULL CHECK (entered_by <> ''),
    transfer_ids text [] NOT NULL CHECK (cardinality(transfer_ids) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id)
);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_opening_balances
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

REVOKE ALL ON trust_opening_balances FROM PUBLIC;
GRANT SELECT ON trust_opening_balances TO trust_app, trust_ai_agent;

-- Every existing row is NULL: no account has opened with balances yet.
ALTER TABLE trust_bank_accounts ADD COLUMN opened_at timestamptz;

-- opened_at is always the start of the account's cutover date (NULL without one), so no role
-- moves it, or sets one, by writing it directly.
CREATE FUNCTION trust_refuse_moving_opened_at() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NEW.opened_at IS DISTINCT FROM (
        SELECT o.opened_on::timestamp AT TIME ZONE 'UTC' FROM trust_opening_balances AS o
        WHERE o.bank_account_id = NEW.id
    ) THEN
        RAISE EXCEPTION 'trust: trust bank account % opened at the start of its cutover date, '
            'not %', NEW.id, NEW.opened_at
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Only trust_post_opening_balances sets it.';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_opened_at
BEFORE INSERT OR UPDATE OF opened_at ON trust_bank_accounts
FOR EACH ROW EXECUTE FUNCTION trust_refuse_moving_opened_at();

-- Open a trust bank account with the balances carried over from another system. Runs as the
-- owner: the app can't call pgledger's posting functions or write trust_opening_balances.
CREATE FUNCTION trust_post_opening_balances(
    p_pmc_id uuid,
    p_bank_account_id uuid,
    p_opened_on date,
    p_balances jsonb,
    p_book_cash numeric,
    p_entered_by text
) RETURNS text []
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank trust_bank_accounts;
    v_cash text;
    v_accounts text[];
    v_amounts numeric[];
    v_balances jsonb;
    v_line text;
    v_opening trust_opening_balances;
    v_transfer_ids text[];
BEGIN
    -- The check that nothing was posted first must see every posting that committed while this
    -- waited for the lock below. At REPEATABLE READ or SERIALIZABLE it would read a snapshot
    -- taken before them.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: post opening balances at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_opened_on IS NULL OR p_book_cash IS NULL OR coalesce(p_entered_by, '') = '' THEN
        RAISE EXCEPTION 'trust: opening balances need the cutover date, the book cash and who '
            'entered them'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- A cutover date can't be undone, and nothing may be dated before it: one typed years
    -- ahead would hold every posting off until then.
    IF p_opened_on > (now() AT TIME ZONE 'UTC')::date THEN
        RAISE EXCEPTION 'trust: the cutover date % has not come yet', p_opened_on
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Post opening balances on or after the cutover date.';
    END IF;
    IF jsonb_typeof(p_balances) IS DISTINCT FROM 'object' OR p_balances = '{}' THEN
        RAISE EXCEPTION 'trust: opening balances are a JSON object of ledger account id to '
            'amount, with at least one account'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Waits for postings in flight on this account (they hold the row FOR SHARE), and holds new
    -- ones off until this commits.
    SELECT b.* INTO v_bank
    FROM trust_bank_accounts AS b
    WHERE b.id = p_bank_account_id AND b.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no trust bank account % in PMC %', p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.ledger_account_id INTO v_cash
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = p_bank_account_id AND t.kind = 'bank_cash';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: open the cash account (bank_cash) of trust bank account % first',
            p_bank_account_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT l.key INTO v_line
    FROM jsonb_each(p_balances) AS l
    WHERE NOT EXISTS (
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = l.key AND t.bank_account_id = p_bank_account_id
          AND t.kind <> 'bank_cash'
    )
    ORDER BY l.key COLLATE "C"
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: % is not an account held in trust bank account %',
            v_line, p_bank_account_id
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Its cash is the opening balances'' total; list what each account holds.';
    END IF;

    SELECT array_agg(l.key ORDER BY l.key COLLATE "C"),
           array_agg((l.value #>> '{}')::numeric ORDER BY l.key COLLATE "C")
    INTO v_accounts, v_amounts
    FROM jsonb_each(p_balances) AS l;

    SELECT l.account INTO v_line
    FROM unnest(v_accounts, v_amounts) AS l (account, amount)
    WHERE l.amount IS NULL OR l.amount <= 0 OR l.amount <> round(l.amount, 2)
    ORDER BY l.account COLLATE "C"
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: the opening balance of % is %; it must be positive and in cents',
            v_line, p_balances ->> v_line
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Leave out an account that held nothing.';
    END IF;

    IF (SELECT sum(a) FROM unnest(v_amounts) AS a) <> p_book_cash THEN
        RAISE EXCEPTION 'trust: the opening balances add up to %, not the book cash of %',
            (SELECT sum(a) FROM unnest(v_amounts) AS a), p_book_cash
            USING ERRCODE = 'check_violation',
                  HINT = 'An account may be missing or mistyped.';
    END IF;

    SELECT jsonb_object_agg(l.account, l.amount) INTO v_balances
    FROM unnest(v_accounts, v_amounts) AS l (account, amount);

    SELECT o.* INTO v_opening
    FROM trust_opening_balances AS o WHERE o.bank_account_id = p_bank_account_id;
    IF FOUND THEN
        IF (v_opening.opened_on, v_opening.balances, v_opening.entered_by)
            IS DISTINCT FROM (p_opened_on, v_balances, p_entered_by) THEN
            RAISE EXCEPTION 'trust: trust bank account % already opened on % with other '
                'balances', p_bank_account_id, v_opening.opened_on
                USING ERRCODE = 'unique_violation',
                      HINT = 'Correct an opening balance with a new transfer.';
        END IF;
        RETURN v_opening.transfer_ids;  -- a retry: the original transfers
    END IF;

    IF v_bank.closed_through IS NOT NULL OR EXISTS (
        SELECT e.id
        FROM pgledger_entries AS e
        JOIN trust_ledger_accounts AS t ON t.ledger_account_id = e.account_id
        WHERE t.bank_account_id = p_bank_account_id
    ) THEN
        RAISE EXCEPTION 'trust: opening balances come first, and trust bank account % already '
            'has postings or an approved reconciliation', p_bank_account_id
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT array_agg(t.id ORDER BY t.n) INTO v_transfer_ids
    FROM pgledger_create_transfers(
        ARRAY(
            SELECT (v_cash, l.account, l.amount)::transfer_request
            FROM unnest(v_accounts, v_amounts) WITH ORDINALITY AS l (account, amount, n)
            ORDER BY l.n
        ),
        p_opened_on::timestamp AT TIME ZONE 'UTC',
        jsonb_build_object('memo', 'Opening balance')
    ) WITH ORDINALITY AS t (id, n);

    INSERT INTO trust_opening_balances (
        bank_account_id, pmc_id, opened_on, balances, book_cash, entered_by, transfer_ids
    ) VALUES (
        p_bank_account_id, p_pmc_id, p_opened_on, v_balances, p_book_cash, p_entered_by,
        v_transfer_ids
    );
    UPDATE trust_bank_accounts SET opened_at = p_opened_on::timestamp AT TIME ZONE 'UTC'
    WHERE id = p_bank_account_id;

    RETURN v_transfer_ids;
END;
$$;

REVOKE ALL ON FUNCTION trust_post_opening_balances(uuid, uuid, date, jsonb, numeric, text)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_post_opening_balances(uuid, uuid, date, jsonb, numeric, text)
TO trust_app;

-- Refuse a transfer dated inside a closed period of either side's trust bank account, or before
-- its cutover. Was migration 20261009000006's; CREATE OR REPLACE keeps its trigger and grants.
CREATE OR REPLACE FUNCTION trust_refuse_posting_into_closed_period() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank uuid;
    v_closed_through timestamptz;
    v_opened_at timestamptz;
BEGIN
    -- FOR SHARE waits for an approval or an opening in progress on either account and then
    -- reads the date it set. At REPEATABLE READ or SERIALIZABLE, one that committed after this
    -- transaction's snapshot makes the lock fail with a serialization error instead.
    FOR v_bank, v_closed_through, v_opened_at IN
        SELECT b.id, b.closed_through, b.opened_at
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
        IF NEW.event_at < v_opened_at THEN
            RAISE EXCEPTION 'trust: trust bank account % opened at % with balances carried '
                'over; a transfer dated % can''t be posted', v_bank, v_opened_at, NEW.event_at
                USING ERRCODE = 'check_violation',
                      HINT = 'Its history before the cutover stays in the old system.';
        END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

-- Approve a reconciliation and close its period: the first one starts at the cutover, if the
-- account had one. Was migration 20261009000006's; CREATE OR REPLACE keeps its grants.
CREATE OR REPLACE FUNCTION trust_approve_reconciliation(
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
    v_next_start timestamptz;
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
    SELECT coalesce(b.closed_through, b.opened_at) INTO v_next_start
    FROM trust_bank_accounts AS b
    WHERE b.id = p_bank_account_id AND b.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no trust bank account % in PMC %', p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF p_period_start <> v_next_start THEN
        RAISE EXCEPTION 'trust: the next period of trust bank account % starts at %',
            p_bank_account_id, v_next_start
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Periods follow one another, from the cutover: no gap, no overlap.';
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

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'cutover cannot be rolled back: opening balances are ledger history';
END;
$$;
