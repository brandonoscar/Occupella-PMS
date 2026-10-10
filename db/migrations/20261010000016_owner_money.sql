-- migrate:up
-- The owner money loop (founder decision, 2026-10-10: Buildium/AppFolio parity, owner money
-- first): what a PMC earns from each owner's property and what it pays the owner.
--
--   trust_management_agreements  the terms for one owner's property, in force from a date: a
--                                percent of collected rent, a monthly minimum, a flat monthly
--                                fee, a leasing fee (percent of a new lease's monthly rent) and
--                                a reserve kept back from draws. New terms are a new row with
--                                a later date; an agreement is never edited.
--   trust_management_fees        the management fee taken for a period, with the rent collected
--                                that it was worked from.
--   trust_leasing_fees           the leasing fee taken for a lease.
--   trust_owner_draws            money paid out to the owner, by request key.
--
-- Every fee moves money from the owner's ledger to the PMC's fee ledger (pmc_income) in the
-- same trust bank account; every draw moves it from the owner's ledger out through that bank
-- account's cash. Each is safe to retry: the same period, lease or request key returns the
-- original, and nothing posts twice. The no-negative rule still holds: a fee or draw the owner's
-- balance can't cover is refused.
--
-- Collected rent is what was matched to rent charges (trust_apply_payment) by payments into the
-- owner's ledger dated inside the period, less what bounced back (trust_reverse_payment) inside
-- it. A bounce lowers the month it lands in, never below the minimum fee.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_management_agreements (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    starts_on date NOT NULL,
    fee_percent numeric NOT NULL DEFAULT 0 CHECK (fee_percent BETWEEN 0 AND 100),
    minimum_fee numeric NOT NULL DEFAULT 0 CHECK (minimum_fee >= 0),
    flat_fee numeric NOT NULL DEFAULT 0 CHECK (flat_fee >= 0),
    leasing_fee_percent numeric NOT NULL DEFAULT 0 CHECK (leasing_fee_percent BETWEEN 0 AND 100),
    reserve numeric NOT NULL DEFAULT 0 CHECK (reserve >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ledger_account_id, starts_on)
);

CREATE TABLE trust_management_fees (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    period_start date NOT NULL,
    period_end date NOT NULL,  -- the first day after the period
    agreement_id uuid NOT NULL REFERENCES trust_management_agreements (id),
    collected numeric NOT NULL,
    fee numeric NOT NULL CHECK (fee >= 0),
    transfer_id text REFERENCES pgledger_transfers (id),  -- NULL when the fee is 0.00
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ledger_account_id, period_start),
    CHECK (period_end > period_start),
    CHECK ((fee = 0) = (transfer_id IS NULL))
);
CREATE INDEX trust_management_fees_agreement_id ON trust_management_fees (agreement_id);
CREATE INDEX trust_management_fees_transfer_id ON trust_management_fees (transfer_id);

CREATE TABLE trust_leasing_fees (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid PRIMARY KEY,
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    agreement_id uuid NOT NULL REFERENCES trust_management_agreements (id),
    fee numeric NOT NULL CHECK (fee >= 0),
    transfer_id text REFERENCES pgledger_transfers (id),  -- NULL when the fee is 0.00
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    CHECK ((fee = 0) = (transfer_id IS NULL))
);
CREATE INDEX trust_leasing_fees_ledger_account_id ON trust_leasing_fees (ledger_account_id);
CREATE INDEX trust_leasing_fees_agreement_id ON trust_leasing_fees (agreement_id);
CREATE INDEX trust_leasing_fees_transfer_id ON trust_leasing_fees (transfer_id);

CREATE TABLE trust_owner_draws (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    request_key text NOT NULL CHECK (request_key <> ''),
    ledger_account_id text NOT NULL REFERENCES trust_ledger_accounts (ledger_account_id),
    amount numeric NOT NULL CHECK (amount > 0),
    transfer_id text NOT NULL REFERENCES pgledger_transfers (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (pmc_id, request_key)
);
CREATE INDEX trust_owner_draws_ledger_account_id ON trust_owner_draws (ledger_account_id);
CREATE INDEX trust_owner_draws_transfer_id ON trust_owner_draws (transfer_id);

-- Records: kept for good. New terms are a new agreement; a mistaken fee or draw is corrected
-- with a reversing transfer.
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_management_agreements
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_management_fees
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_leasing_fees
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_owner_draws
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

-- An agreement is for one owner's property: an owner_property ledger account of its PMC.
CREATE FUNCTION trust_check_agreement_account() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = NEW.ledger_account_id AND t.pmc_id = NEW.pmc_id
          AND t.kind = 'owner_property'
    ) THEN
        RAISE EXCEPTION 'trust: a management agreement is for an owner''s property account in '
            'its own PMC; % is not one in PMC %', NEW.ledger_account_id, NEW.pmc_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_agreement_account
BEFORE INSERT ON trust_management_agreements
FOR EACH ROW EXECUTE FUNCTION trust_check_agreement_account();

REVOKE ALL ON
    trust_management_agreements, trust_management_fees, trust_leasing_fees, trust_owner_draws
FROM PUBLIC;
-- The app records agreements; fees and draws go through the functions below.
GRANT SELECT, INSERT ON trust_management_agreements TO trust_app;
GRANT SELECT ON trust_management_fees, trust_leasing_fees, trust_owner_draws TO trust_app;
GRANT SELECT ON
    trust_management_agreements, trust_management_fees, trust_leasing_fees, trust_owner_draws
TO trust_ai_agent;

-- The owner_property account p_account of PMC p_pmc_id, locked so fees and draws on it wait
-- for each other; refused unless it is one. The caller then reads balances committed while it
-- waited, which only READ COMMITTED does.
CREATE FUNCTION trust_lock_owner_account(p_pmc_id uuid, p_account text)
RETURNS trust_ledger_accounts
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_account trust_ledger_accounts;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: take fees and draws at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    SELECT t.* INTO v_account
    FROM trust_ledger_accounts AS t
    WHERE t.ledger_account_id = p_account AND t.pmc_id = p_pmc_id AND t.kind = 'owner_property'
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: % is not an owner''s property account in PMC %', p_account, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN v_account;
END;
$$;

REVOKE ALL ON FUNCTION trust_lock_owner_account(uuid, text) FROM PUBLIC;

-- The agreement in force for an account on a day: the latest one starting on or before it.
CREATE FUNCTION trust_agreement_on(p_account text, p_day date)
RETURNS trust_management_agreements
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT a.*
    FROM trust_management_agreements AS a
    WHERE a.ledger_account_id = p_account AND a.starts_on <= p_day
    ORDER BY a.starts_on DESC
    LIMIT 1
$$;

REVOKE ALL ON FUNCTION trust_agreement_on(text, date) FROM PUBLIC;

-- Move p_amount from an owner's account to the PMC's fee account in the same trust bank
-- account; the transfer id.
CREATE FUNCTION trust_post_fee(
    p_account trust_ledger_accounts,
    p_amount numeric,
    p_event_at timestamptz,
    p_memo text
) RETURNS text
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_income text;
    v_transfer_id text;
BEGIN
    SELECT t.ledger_account_id INTO v_income
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = p_account.bank_account_id AND t.kind = 'pmc_income';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: open the PMC''s fee account (pmc_income) in trust bank account % '
            'before taking fees', p_account.bank_account_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT t.id INTO v_transfer_id
    FROM pgledger_create_transfers(
        ARRAY[(p_account.ledger_account_id, v_income, p_amount)::transfer_request],
        p_event_at,
        jsonb_build_object('memo', p_memo)
    ) AS t;
    RETURN v_transfer_id;
END;
$$;

REVOKE ALL ON FUNCTION trust_post_fee(trust_ledger_accounts, numeric, timestamptz, text)
FROM PUBLIC;

-- Take the management fee for one owner's property for [p_period_start, p_period_end), dated
-- p_event_at: the agreement's percent of rent collected in the period (UTC days), or its
-- minimum if that is more, plus its flat fee. Safe to retry: the same period returns the fee
-- taken; a period overlapping one already taken is refused. Runs as the owner.
CREATE FUNCTION trust_post_management_fee(
    p_pmc_id uuid,
    p_account text,
    p_period_start date,
    p_period_end date,
    p_event_at timestamptz
) RETURNS numeric
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_account trust_ledger_accounts := trust_lock_owner_account(p_pmc_id, p_account);
    v_start timestamptz := p_period_start::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := p_period_end::timestamp AT TIME ZONE 'UTC';
    v_taken trust_management_fees;
    v_agreement trust_management_agreements;
    v_collected numeric;
    v_fee numeric;
BEGIN
    IF p_period_start IS NULL OR p_period_end IS NULL OR p_period_end <= p_period_start THEN
        RAISE EXCEPTION 'trust: a fee period must end after it starts (got % to %)',
            p_period_start, p_period_end
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT f.* INTO v_taken
    FROM trust_management_fees AS f
    WHERE f.ledger_account_id = p_account
      AND f.period_start < p_period_end AND p_period_start < f.period_end
    LIMIT 1;
    IF FOUND THEN
        IF (v_taken.period_start, v_taken.period_end) = (p_period_start, p_period_end) THEN
            RETURN v_taken.fee;  -- a retry: the fee already taken
        END IF;
        RAISE EXCEPTION 'trust: the fee for % to % overlaps the one taken for % to %',
            p_period_start, p_period_end, v_taken.period_start, v_taken.period_end
            USING ERRCODE = 'exclusion_violation';
    END IF;

    v_agreement := trust_agreement_on(p_account, p_period_start);
    IF v_agreement.id IS NULL THEN
        RAISE EXCEPTION 'trust: no management agreement for % in force on %',
            p_account, p_period_start
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT coalesce(sum(cp.amount), 0) INTO v_collected
    FROM trust_charge_payments AS cp
    JOIN trust_charges AS c ON c.id = cp.charge_id AND c.kind = 'rent'
    JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
    WHERE t.to_account_id = p_account AND t.event_at >= v_start AND t.event_at < v_end;
    SELECT v_collected - coalesce(sum(r.amount), 0) INTO v_collected
    FROM trust_payment_reversals AS r
    JOIN trust_charges AS c ON c.id = r.charge_id AND c.kind = 'rent'
    JOIN pgledger_transfers AS t ON t.id = r.reversal_id
    WHERE t.from_account_id = p_account AND t.event_at >= v_start AND t.event_at < v_end;

    v_fee := greatest(round(v_agreement.fee_percent / 100 * v_collected, 2),
                      v_agreement.minimum_fee)
             + v_agreement.flat_fee;

    INSERT INTO trust_management_fees (
        pmc_id, ledger_account_id, period_start, period_end, agreement_id, collected, fee,
        transfer_id
    ) VALUES (
        p_pmc_id, p_account, p_period_start, p_period_end, v_agreement.id, v_collected, v_fee,
        CASE WHEN v_fee > 0 THEN trust_post_fee(
            v_account, v_fee, p_event_at,
            format('Management fee %s to %s', p_period_start, p_period_end - 1)
        ) END
    );
    RETURN v_fee;
END;
$$;

REVOKE ALL ON FUNCTION trust_post_management_fee(uuid, text, date, date, timestamptz)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_post_management_fee(uuid, text, date, date, timestamptz)
TO trust_app;

-- Take the leasing fee for a new lease from the owner's account for its property: the
-- agreement's leasing percent (in force on the lease's first day) of its monthly rent. Once
-- per lease; a retry returns the fee taken. Runs as the owner.
CREATE FUNCTION trust_post_leasing_fee(
    p_pmc_id uuid,
    p_lease_id uuid,
    p_account text,
    p_event_at timestamptz
) RETURNS numeric
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_account trust_ledger_accounts := trust_lock_owner_account(p_pmc_id, p_account);
    v_lease trust_leases;
    v_property_id uuid;
    v_taken trust_leasing_fees;
    v_agreement trust_management_agreements;
    v_fee numeric;
BEGIN
    SELECT l.* INTO v_lease FROM trust_leases AS l WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT u.property_id INTO v_property_id FROM trust_units AS u WHERE u.id = v_lease.unit_id;
    IF v_account.property_id <> v_property_id THEN
        RAISE EXCEPTION 'trust: % is not an owner''s account for the lease''s property', p_account
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT f.* INTO v_taken FROM trust_leasing_fees AS f WHERE f.lease_id = p_lease_id;
    IF FOUND THEN
        IF v_taken.ledger_account_id <> p_account THEN
            RAISE EXCEPTION 'trust: the leasing fee for lease % was taken from %, not %',
                p_lease_id, v_taken.ledger_account_id, p_account
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_taken.fee;  -- a retry: the fee already taken
    END IF;

    v_agreement := trust_agreement_on(p_account, v_lease.starts_on);
    IF v_agreement.id IS NULL THEN
        RAISE EXCEPTION 'trust: no management agreement for % in force on %',
            p_account, v_lease.starts_on
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_fee := round(v_agreement.leasing_fee_percent / 100 * v_lease.monthly_rent, 2);

    INSERT INTO trust_leasing_fees (
        pmc_id, lease_id, ledger_account_id, agreement_id, fee, transfer_id
    ) VALUES (
        p_pmc_id, p_lease_id, p_account, v_agreement.id, v_fee,
        CASE WHEN v_fee > 0 THEN trust_post_fee(
            v_account, v_fee, p_event_at, format('Leasing fee, lease from %s', v_lease.starts_on)
        ) END
    );
    RETURN v_fee;
END;
$$;

REVOKE ALL ON FUNCTION trust_post_leasing_fee(uuid, uuid, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_post_leasing_fee(uuid, uuid, text, timestamptz) TO trust_app;

-- Pay an owner from one property's account: p_amount, or with NULL everything available, which
-- is the balance less the reserve of the agreement in force on the draw's day (UTC). The money
-- leaves through the trust bank account's cash. Safe to retry: the same request key returns
-- the original draw, and a different draw under it is refused. Runs as the owner.
CREATE FUNCTION trust_draw_owner(
    p_pmc_id uuid,
    p_account text,
    p_request_key text,
    p_amount numeric,
    p_event_at timestamptz
) RETURNS numeric
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_account trust_ledger_accounts := trust_lock_owner_account(p_pmc_id, p_account);
    v_taken trust_owner_draws;
    v_reserve numeric;
    v_available numeric;
    v_cash text;
    v_transfer_id text;
BEGIN
    IF coalesce(p_request_key, '') = '' THEN
        RAISE EXCEPTION 'trust: an owner draw needs a request key, so a retry can''t pay twice'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a draw pays a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT d.* INTO v_taken
    FROM trust_owner_draws AS d
    WHERE d.pmc_id = p_pmc_id AND d.request_key = p_request_key;
    IF FOUND THEN
        IF v_taken.ledger_account_id <> p_account
            OR v_taken.amount <> coalesce(p_amount, v_taken.amount) THEN
            RAISE EXCEPTION 'trust: request key % already paid % from %',
                p_request_key, v_taken.amount, v_taken.ledger_account_id
                USING ERRCODE = 'unique_violation',
                      HINT = 'Use a new key for a new draw; resend a key only to retry.';
        END IF;
        RETURN v_taken.amount;  -- a retry: the original draw
    END IF;

    -- pgledger dates a transfer with no date now, so the reserve is today's.
    v_reserve := coalesce((
        trust_agreement_on(p_account, (coalesce(p_event_at, now()) AT TIME ZONE 'UTC')::date)
    ).reserve, 0);
    SELECT a.balance - v_reserve INTO v_available
    FROM pgledger_accounts AS a WHERE a.id = p_account;
    IF coalesce(p_amount, v_available) > v_available OR v_available <= 0 THEN
        RAISE EXCEPTION 'trust: % is available to draw from % (its balance less a reserve of %); '
            '% is too much', greatest(v_available, 0), p_account, v_reserve,
            coalesce(p_amount, v_available)
            USING ERRCODE = 'check_violation';
    END IF;

    -- Money reaches an owner's account only through its trust bank account's cash, so a
    -- balance to draw means that cash account exists.
    SELECT t.ledger_account_id INTO v_cash
    FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = v_account.bank_account_id AND t.kind = 'bank_cash';
    SELECT t.id INTO v_transfer_id
    FROM pgledger_create_transfers(
        ARRAY[(p_account, v_cash, coalesce(p_amount, v_available))::transfer_request],
        p_event_at,
        jsonb_build_object('memo', 'Owner draw')
    ) AS t;

    INSERT INTO trust_owner_draws (pmc_id, request_key, ledger_account_id, amount, transfer_id)
    VALUES (p_pmc_id, p_request_key, p_account, coalesce(p_amount, v_available), v_transfer_id);
    RETURN coalesce(p_amount, v_available);
END;
$$;

REVOKE ALL ON FUNCTION trust_draw_owner(uuid, text, text, numeric, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_draw_owner(uuid, text, text, numeric, timestamptz)
TO trust_app;

-- An owner draw is not a bounced payment coming back, though both move money from the owner's
-- account to cash. CREATE OR REPLACE keeps the grants of migration 20261010000014.
CREATE OR REPLACE FUNCTION trust_reverse_payment(
    p_pmc_id uuid,
    p_charge_id uuid,
    p_transfer_id text,
    p_reversal_id text,
    p_amount numeric
) RETURNS numeric
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_matched numeric;
    v_payment pgledger_transfers;
    v_reversal pgledger_transfers;
    v_existing numeric;
    v_used numeric;
BEGIN
    -- The sums below must count every reversal that committed while this waited for the locks.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: reverse a payment at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a reversal undoes a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Waits for matches and reversals in progress on the charge (trust_apply_payment takes the
    -- same lock), then for those on the reversing transfer.
    PERFORM c.id FROM trust_charges AS c
    WHERE c.id = p_charge_id AND c.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no charge % in PMC %', p_charge_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT p.amount INTO v_matched
    FROM trust_charge_payments AS p
    WHERE p.charge_id = p_charge_id AND p.transfer_id = p_transfer_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: transfer % pays nothing of charge %', p_transfer_id, p_charge_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT t.* INTO v_payment FROM pgledger_transfers AS t WHERE t.id = p_transfer_id;

    SELECT t.* INTO v_reversal
    FROM pgledger_transfers AS t
    WHERE t.id = p_reversal_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no transfer %', p_reversal_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- A draw also moves money from the owner's account to cash, but it paid the owner.
    IF EXISTS (
        SELECT d.transfer_id FROM trust_owner_draws AS d WHERE d.transfer_id = p_reversal_id
    ) THEN
        RAISE EXCEPTION 'trust: transfer % paid the owner (an owner draw); it reverses no payment',
            p_reversal_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_reversal.from_account_id <> v_payment.to_account_id
        OR v_reversal.to_account_id <> v_payment.from_account_id THEN
        RAISE EXCEPTION 'trust: transfer % does not move money back the way transfer % came',
            p_reversal_id, p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_reversal.event_at < v_payment.event_at THEN
        RAISE EXCEPTION 'trust: transfer % is dated before the payment it reverses', p_reversal_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT r.amount INTO v_existing
    FROM trust_payment_reversals AS r
    WHERE r.charge_id = p_charge_id AND r.transfer_id = p_transfer_id
      AND r.reversal_id = p_reversal_id;
    IF FOUND THEN
        IF v_existing <> p_amount THEN
            RAISE EXCEPTION 'trust: transfer % already reverses % of that payment, not %',
                p_reversal_id, v_existing, p_amount
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_existing;  -- a retry: the original reversal
    END IF;

    SELECT coalesce(sum(r.amount), 0) INTO v_used
    FROM trust_payment_reversals AS r
    WHERE r.charge_id = p_charge_id AND r.transfer_id = p_transfer_id;
    IF v_used + p_amount > v_matched THEN
        RAISE EXCEPTION 'trust: transfer % pays % of charge % and % of it is reversed already; '
            '% more is too much', p_transfer_id, v_matched, p_charge_id, v_used, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT coalesce(sum(r.amount), 0) INTO v_used
    FROM trust_payment_reversals AS r WHERE r.reversal_id = p_reversal_id;
    IF v_used + p_amount > v_reversal.amount THEN
        RAISE EXCEPTION 'trust: transfer % moved % and already reverses %; % more is too much',
            p_reversal_id, v_reversal.amount, v_used, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_payment_reversals (pmc_id, charge_id, transfer_id, reversal_id, amount)
    VALUES (p_pmc_id, p_charge_id, p_transfer_id, p_reversal_id, p_amount);
    RETURN p_amount;
END;
$$;

-- Report: what each owner's property holds at the end of a day (UTC), the reserve its agreement
-- keeps back on that day, and what is available to pay the owner; then the totals. Owners and
-- properties sort by byte (COLLATE "C"). An unknown PMC or a missing date is refused.
CREATE FUNCTION trust_report_owner_balances(
    p_pmc_id uuid,
    p_as_of date
) RETURNS TABLE (
    line integer,
    item text,
    owner text,
    property text,
    detail text,
    balance numeric,
    reserve numeric,
    available numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: owner balances are as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH accounts AS (
        SELECT t.ledger_account_id AS account, o.display_name AS owner_name,
               pr.display_name AS property_name,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0.00) AS held,
               ag.id, ag.fee_percent, ag.minimum_fee, ag.flat_fee, ag.reserve
        FROM trust_ledger_accounts AS t
        JOIN trust_owners AS o ON o.id = t.owner_id
        JOIN trust_properties AS pr ON pr.id = t.property_id
        -- The agreement in force that day: the latest one starting on or before it.
        LEFT JOIN LATERAL (
            SELECT g.id, g.fee_percent, g.minimum_fee, g.flat_fee, g.reserve
            FROM trust_management_agreements AS g
            WHERE g.ledger_account_id = t.ledger_account_id AND g.starts_on <= p_as_of
            ORDER BY g.starts_on DESC
            LIMIT 1
        ) AS ag ON true
        WHERE t.pmc_id = p_pmc_id AND t.kind = 'owner_property'
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS owner_name, NULL::text AS property_name,
               NULL::text AS account, 1 AS step, 'PMC' AS item, pmc.display_name AS detail,
               NULL::numeric AS held, NULL::numeric AS kept
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 2, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL
        UNION ALL
        SELECT 1, a.owner_name, a.property_name, a.account, 0, 'owner property',
               CASE
                   WHEN a.id IS NULL THEN 'no management agreement'
                   ELSE trim_scale(a.fee_percent)::text || '% of collected rent'
                        || ', minimum ' || to_char(a.minimum_fee, 'FM999999990.00')
                        || ', flat ' || to_char(a.flat_fee, 'FM999999990.00')
               END,
               a.held, coalesce(a.reserve, 0.00)
        FROM accounts AS a
        UNION ALL
        SELECT 2, NULL, NULL, NULL, 0, 'total', NULL,
               coalesce((SELECT sum(a.held) FROM accounts AS a), 0.00),
               coalesce((SELECT sum(coalesce(a.reserve, 0.00)) FROM accounts AS a), 0.00)
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.owner_name COLLATE "C", l.property_name COLLATE "C",
                        l.account, l.step
           )::integer,
           l.item,
           l.owner_name,
           l.property_name,
           l.detail,
           round(l.held, 2),
           round(l.kept, 2),
           round(CASE
               WHEN l.section = 1 THEN greatest(l.held - l.kept, 0.00)
               WHEN l.section = 2 THEN coalesce((
                   SELECT sum(greatest(a.held - coalesce(a.reserve, 0.00), 0.00))
                   FROM accounts AS a
               ), 0.00)
           END, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_owner_balances(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_owner_balances(uuid, date) TO trust_app, trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'owner_money cannot be rolled back: fees and draws are records';
END;
$$;
