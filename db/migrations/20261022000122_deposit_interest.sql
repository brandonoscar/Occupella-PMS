-- migrate:up
-- Interest on security deposits (the founder's review q041, approved 2026-10-22; docs/law.md):
-- some states have the landlord pay interest on what a tenant's deposit held, at a rate the
-- state sets, each year or when the deposit goes back. The PMS builds in no state's rate or rule:
-- a property's terms say the rate, and staff pay it when its law says to.
--
--   trust_deposit_interest_terms     a property's yearly rate from a day (0: none), as late fee
--                                    terms are; the latest from a day on or before holds.
--   trust_deposit_interest_payments  interest paid on a lease's deposits for days in a row, each
--                                    payment from the day after the last one's through day (or
--                                    the lease's first day), once per request key: as a credit
--                                    against the lease's rent (a lease in effect), or added to
--                                    the deposits (any lease whose deposit hasn't gone back).
--   trust_deposit_interest_lines     each deposit's part, and when added to it, the transfer.
--
-- Interest is simple: each day, what each tenant's deposit held at its end (UTC) times the
-- day's rate over the days in its year, summed over the days and rounded to the cent per
-- deposit. Added to a deposit, it comes from the property's owner's account (its owners' by
-- their shares) with its cash, from the owner's trust bank account to the deposits'. It then
-- earns interest itself and goes back with the deposit. Paid as a credit, it moves no money:
-- the lease owes less. All append-only and audited; the AI role reads them, and the report.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_deposit_interest_terms (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid NOT NULL,
    starts_on date NOT NULL,
    annual_percent numeric NOT NULL
        CHECK (annual_percent >= 0 AND annual_percent <= 25
               AND annual_percent = round(annual_percent, 4)),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (property_id, starts_on),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_deposit_interest_terms_pmc
ON trust_deposit_interest_terms (pmc_id, property_id);
CREATE INDEX trust_deposit_interest_terms_recorded_by
ON trust_deposit_interest_terms (pmc_id, recorded_by);

CREATE TABLE trust_deposit_interest_payments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    from_on date NOT NULL,
    through_on date NOT NULL,
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    paid_as text NOT NULL CHECK (paid_as IN ('rent_credit', 'deposit')),
    charge_id uuid,
    request_key text NOT NULL CHECK (request_key <> ''),
    paid_at timestamptz NOT NULL,
    paid_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (through_on >= from_on),
    -- A rent credit is the credit charge it made; interest added to a deposit made none.
    CHECK ((paid_as = 'rent_credit') = (charge_id IS NOT NULL)),
    UNIQUE (lease_id, from_on),
    UNIQUE (pmc_id, request_key),
    UNIQUE (charge_id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, charge_id) REFERENCES trust_charges (pmc_id, id),
    FOREIGN KEY (pmc_id, paid_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_deposit_interest_payments_pmc
ON trust_deposit_interest_payments (pmc_id, lease_id);
CREATE INDEX trust_deposit_interest_payments_paid_by
ON trust_deposit_interest_payments (pmc_id, paid_by);

CREATE TABLE trust_deposit_interest_lines (
    payment_id uuid NOT NULL REFERENCES trust_deposit_interest_payments (id),
    line_no bigint NOT NULL CHECK (line_no > 0),
    pmc_id uuid NOT NULL,
    deposit_account text NOT NULL,
    owner_account text,
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    transfer_id text REFERENCES pgledger_transfers (id),
    PRIMARY KEY (payment_id, line_no),
    -- Added to a deposit, a line names the owner's account it came from and its transfer.
    CHECK ((owner_account IS NULL) = (transfer_id IS NULL)),
    FOREIGN KEY (pmc_id, deposit_account)
        REFERENCES trust_ledger_accounts (pmc_id, ledger_account_id),
    FOREIGN KEY (pmc_id, owner_account)
        REFERENCES trust_ledger_accounts (pmc_id, ledger_account_id)
);
CREATE INDEX trust_deposit_interest_lines_pmc
ON trust_deposit_interest_lines (pmc_id, deposit_account);
CREATE INDEX trust_deposit_interest_lines_owner
ON trust_deposit_interest_lines (pmc_id, owner_account);
CREATE INDEX trust_deposit_interest_lines_transfer
ON trust_deposit_interest_lines (transfer_id);

-- The yearly rate on a property's deposits on a day: its latest terms from then or before.
CREATE FUNCTION trust_deposit_interest_rate(p_property_id uuid, p_day date) RETURNS numeric
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce((
        SELECT t.annual_percent FROM trust_deposit_interest_terms AS t
        WHERE t.property_id = p_property_id AND t.starts_on <= p_day
        ORDER BY t.starts_on DESC
        LIMIT 1
    ), 0)
$$;

-- A deposit's interest over days, not rounded: what it held at each day's end (UTC) times the
-- property's rate that day, over the days in that day's year.
CREATE FUNCTION trust_deposit_interest_on(
    p_account text, p_property_id uuid, p_from date, p_through date
) RETURNS numeric
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    WITH moves AS (
        SELECT (tr.event_at AT TIME ZONE 'UTC')::date AS day, sum(e.amount) AS amount
        FROM pgledger_entries AS e
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE e.account_id = p_account
          AND tr.event_at < (p_through + 1)::timestamp AT TIME ZONE 'UTC'
        GROUP BY 1
    ),
    days AS (
        SELECT d::date AS day
        FROM generate_series(p_from::timestamp, p_through::timestamp, interval '1 day') AS d
    ),
    held AS (
        SELECT d.day,
               (SELECT coalesce(sum(m.amount), 0) FROM moves AS m WHERE m.day < p_from)
               + coalesce(sum(m.amount) OVER (ORDER BY d.day), 0) AS balance
        FROM days AS d
        LEFT JOIN moves AS m ON m.day = d.day
    ),
    terms AS (
        SELECT t.starts_on, lead(t.starts_on) OVER (ORDER BY t.starts_on) AS next_on,
               t.annual_percent
        FROM trust_deposit_interest_terms AS t
        WHERE t.property_id = p_property_id
    )
    SELECT coalesce(sum(
        h.balance * t.annual_percent / 100
        / ((date_trunc('year', h.day) + interval '1 year')::date - date_trunc('year', h.day)::date)
    ), 0)
    FROM held AS h
    JOIN terms AS t ON t.starts_on <= h.day AND (t.next_on IS NULL OR h.day < t.next_on)
$$;

-- The first day a lease's next interest payment is for: the day after the last one's.
CREATE FUNCTION trust_deposit_interest_from(p_lease_id uuid) RETURNS date
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce(
        (SELECT max(p.through_on) + 1 FROM trust_deposit_interest_payments AS p
         WHERE p.lease_id = p_lease_id),
        (SELECT l.starts_on FROM trust_leases AS l WHERE l.id = p_lease_id)
    )
$$;

-- Each deposit held for a lease's tenants, with its interest from a day through another, to
-- the cent: the deposits a return would empty, in the order it empties them.
CREATE FUNCTION trust_deposit_interest_due(p_lease_id uuid, p_from date, p_through date)
RETURNS TABLE (deposit_account text, tenant text, held numeric, interest numeric)
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT t.ledger_account_id, tn.display_name || CASE t.deposit_kind
                                                     WHEN 'pet' THEN ' (pet deposit)'
                                                     WHEN 'key' THEN ' (key deposit)'
                                                     ELSE '' END,
           a.balance,
           round(trust_deposit_interest_on(t.ledger_account_id, u.property_id, p_from, p_through), 2)
    FROM trust_leases AS l
    JOIN trust_units AS u ON u.id = l.unit_id
    JOIN trust_lease_tenants AS lt ON lt.lease_id = l.id
    JOIN trust_ledger_accounts AS t ON t.tenant_id = lt.tenant_id AND t.kind = 'tenant_deposit'
    JOIN trust_tenants AS tn ON tn.id = t.tenant_id
    JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
    WHERE l.id = p_lease_id
    ORDER BY tn.display_name COLLATE "C", t.tenant_id,
             CASE t.deposit_kind WHEN 'key' THEN 1 WHEN 'pet' THEN 2 ELSE 0 END,
             t.ledger_account_id
$$;

-- Pay a lease's deposit interest from the day after its last payment through a day gone by: as
-- a credit against its rent, or added to its deposits from its owner's account. Once per request
-- key: the same key again returns what it paid then.
CREATE FUNCTION trust_pay_deposit_interest(
    p_pmc_id uuid,
    p_lease_id uuid,
    p_through date,
    p_paid_as text,
    p_owner_account text,
    p_request_key text,
    p_event_at timestamptz
) RETURNS numeric
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_lease trust_leases;
    v_done trust_deposit_interest_payments;
    v_day date := (p_event_at AT TIME ZONE 'UTC')::date;
    v_from date;
    v_property uuid;
    v_split boolean;
    v_owner text;
    v_owners bigint;
    v_total numeric;
    v_charge uuid;
    v_payment uuid;
    v_line bigint := 0::bigint;
    v_deposit record;
    v_pay record;
    v_cash text;
    v_owner_cash text;
    v_transfer text;
    v_moved uuid;
BEGIN
    -- What a deposit held must count every payment into it that committed while this waited.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: pay deposit interest at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF coalesce(p_request_key, '') = '' OR p_event_at IS NULL OR p_through IS NULL THEN
        RAISE EXCEPTION 'trust: deposit interest needs a request key, the day it is paid through'
            ' and the time it was paid'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_paid_as IS NULL OR p_paid_as NOT IN ('rent_credit', 'deposit') THEN
        RAISE EXCEPTION 'trust: deposit interest is paid as a rent credit or added to the deposit,'
            ' not %', coalesce(p_paid_as, 'nothing')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- The lease's rent work and its deposit's return wait for this, and this for them.
    PERFORM pg_advisory_xact_lock(hashtextextended('rent of lease ' || p_lease_id, 0));
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT p.* INTO v_done FROM trust_deposit_interest_payments AS p
    WHERE p.pmc_id = p_pmc_id AND p.request_key = p_request_key;
    IF FOUND THEN
        IF v_done.lease_id <> p_lease_id OR v_done.through_on <> p_through
           OR v_done.paid_as <> p_paid_as THEN
            RAISE EXCEPTION 'trust: request key % paid other deposit interest', p_request_key
                USING ERRCODE = 'unique_violation',
                      HINT = 'Resend a request key only to retry.';
        END IF;
        RETURN v_done.amount;  -- a retry: what it paid then
    END IF;

    IF EXISTS (SELECT 1 FROM trust_deposit_returns AS r WHERE r.lease_id = p_lease_id) THEN
        RAISE EXCEPTION 'trust: the deposit of lease % was returned already', p_lease_id
            USING ERRCODE = 'check_violation',
                  HINT = 'Pay its interest before the deposit goes back.';
    END IF;
    SELECT t.to_lease_id INTO v_moved FROM trust_lease_transfers AS t
    WHERE t.from_lease_id = p_lease_id;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: lease %''s tenants moved to lease %, and their deposits with them',
            p_lease_id, v_moved
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_paid_as = 'rent_credit' AND (v_lease.ends_on < v_day OR v_lease.starts_on > v_day) THEN
        RAISE EXCEPTION 'trust: lease % is not in effect on %; add its interest to the deposit',
            p_lease_id, v_day
            USING ERRCODE = 'check_violation';
    END IF;

    v_from := trust_deposit_interest_from(p_lease_id);
    IF p_through < v_from THEN
        RAISE EXCEPTION 'trust: lease %''s deposit interest is paid through %; pay from % on',
            p_lease_id, v_from - 1, v_from
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_through > v_day THEN
        RAISE EXCEPTION 'trust: deposit interest is paid for days gone by, and % is after %',
            p_through, v_day
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT coalesce(sum(d.interest), 0) INTO v_total
    FROM trust_deposit_interest_due(p_lease_id, v_from, p_through) AS d;
    IF v_total <= 0 THEN
        RAISE EXCEPTION 'trust: no interest is due on lease %''s deposits from % through %',
            p_lease_id, v_from, p_through
            USING ERRCODE = 'check_violation';
    END IF;

    IF p_paid_as = 'rent_credit' THEN
        INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
        VALUES (
            p_pmc_id, p_lease_id, v_day, 'credit', v_total,
            'Security deposit interest, ' || to_char(v_from, 'MM/DD/YYYY') || ' to '
            || to_char(p_through, 'MM/DD/YYYY')
        )
        RETURNING id INTO v_charge;
    ELSE
        -- A property its owners own in shares on the day pays by their shares; one without, from
        -- its one owner's account, or the one named.
        SELECT u.property_id INTO v_property FROM trust_units AS u WHERE u.id = v_lease.unit_id;
        v_split := EXISTS (SELECT 1 FROM trust_ownership_on(v_property, v_day));
        IF NOT v_split THEN
            SELECT count(*), min(t.ledger_account_id) INTO v_owners, v_owner
            FROM trust_ledger_accounts AS t
            WHERE t.property_id = v_property AND t.pmc_id = p_pmc_id AND t.kind = 'owner_property'
              AND (p_owner_account IS NULL OR t.ledger_account_id = p_owner_account);
            IF v_owners <> 1 THEN
                RAISE EXCEPTION 'trust: % is not the one owner''s account of the lease''s property',
                    coalesce(p_owner_account, 'no account given')
                    USING ERRCODE = 'invalid_parameter_value',
                          HINT = 'Name the owner''s account when the property has more than one.';
            END IF;
        END IF;
    END IF;

    INSERT INTO trust_deposit_interest_payments (
        pmc_id, lease_id, from_on, through_on, amount, paid_as, charge_id, request_key, paid_at,
        paid_by
    ) VALUES (
        p_pmc_id, p_lease_id, v_from, p_through, v_total, p_paid_as, v_charge, p_request_key,
        p_event_at, nullif(current_setting('trust.staff_id', true), '')::uuid
    )
    RETURNING id INTO v_payment;

    FOR v_deposit IN
        SELECT d.deposit_account, d.interest, t.bank_account_id
        FROM trust_deposit_interest_due(p_lease_id, v_from, p_through) AS d
        JOIN trust_ledger_accounts AS t ON t.ledger_account_id = d.deposit_account
        WHERE d.interest > 0
    LOOP
        IF p_paid_as = 'rent_credit' THEN
            v_line := v_line + 1;
            INSERT INTO trust_deposit_interest_lines (
                payment_id, line_no, pmc_id, deposit_account, amount
            ) VALUES (v_payment, v_line, p_pmc_id, v_deposit.deposit_account, v_deposit.interest);
            CONTINUE;
        END IF;
        SELECT t.ledger_account_id INTO v_cash FROM trust_ledger_accounts AS t
        WHERE t.bank_account_id = v_deposit.bank_account_id AND t.kind = 'bank_cash';
        FOR v_pay IN
            SELECT s.ledger_account_id COLLATE "C" AS account, s.amount
            FROM trust_split_by_ownership(v_property, v_day, v_deposit.interest) AS s
            WHERE v_split
            UNION ALL
            SELECT v_owner COLLATE "C", v_deposit.interest
            WHERE NOT v_split
            ORDER BY account
        LOOP
            CONTINUE WHEN v_pay.amount <= 0;
            SELECT c.ledger_account_id INTO v_owner_cash
            FROM trust_ledger_accounts AS o
            JOIN trust_ledger_accounts AS c
              ON c.bank_account_id = o.bank_account_id AND c.kind = 'bank_cash'
            WHERE o.ledger_account_id = v_pay.account;
            -- The owner's money into the deposit, and its cash from the owner's trust bank
            -- account to the deposits' (an owner short of it is refused: no owner goes below 0).
            SELECT t.id INTO v_transfer
            FROM pgledger_create_transfers(
                ARRAY[
                    (v_pay.account, v_deposit.deposit_account, v_pay.amount)::transfer_request,
                    (v_cash, v_owner_cash, v_pay.amount)::transfer_request
                ],
                p_event_at,
                jsonb_build_object('memo', 'Security deposit interest')
            ) AS t
            WHERE t.from_account_id = v_pay.account;
            v_line := v_line + 1;
            INSERT INTO trust_deposit_interest_lines (
                payment_id, line_no, pmc_id, deposit_account, owner_account, amount, transfer_id
            ) VALUES (
                v_payment, v_line, p_pmc_id, v_deposit.deposit_account, v_pay.account,
                v_pay.amount, v_transfer
            );
        END LOOP;
    END LOOP;
    RETURN v_total;
END;
$$;

-- Each lease whose tenants' deposits are held on a day, at a property with deposit interest
-- terms by then: what is held, the rate that day, the day its next payment would be from and
-- what it would pay through the day. It runs as the owner, so the AI role gets what it comes to
-- without calling the functions that work it out, which serve the app's payments.
CREATE FUNCTION trust_report_deposit_interest(p_pmc_id uuid, p_as_of date)
RETURNS TABLE (
    line integer,
    lease_id uuid,
    property text,
    unit text,
    tenants text,
    held numeric,
    annual_percent numeric,
    interest_from date,
    due numeric
)
LANGUAGE plpgsql STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: deposit interest is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    WITH leases AS (
        SELECT l.id, pr.display_name AS property, u.display_name AS unit, u.property_id,
               (SELECT string_agg(t.display_name, ', ' ORDER BY t.display_name COLLATE "C", t.id)
                FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id
                WHERE lt.lease_id = l.id) AS tenants,
               trust_deposit_interest_from(l.id) AS interest_from
        FROM trust_leases AS l
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        WHERE l.pmc_id = p_pmc_id AND l.starts_on <= p_as_of
          AND NOT EXISTS (SELECT 1 FROM trust_deposit_returns AS r WHERE r.lease_id = l.id)
          AND NOT EXISTS (SELECT 1 FROM trust_lease_transfers AS m WHERE m.from_lease_id = l.id)
          AND EXISTS (
              SELECT 1 FROM trust_deposit_interest_terms AS t
              WHERE t.property_id = u.property_id AND t.starts_on <= p_as_of
          )
    ),
    deposits AS (
        -- Each deposit's interest from the lease's next payment's first day, and what it held
        -- at the day's end.
        SELECT x.id, d.interest,
               (SELECT coalesce(sum(e.amount), 0)
                FROM pgledger_entries AS e
                JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                WHERE e.account_id = d.deposit_account
                  AND tr.event_at < (p_as_of + 1)::timestamp AT TIME ZONE 'UTC') AS held
        FROM leases AS x
        CROSS JOIN LATERAL trust_deposit_interest_due(x.id, x.interest_from, p_as_of) AS d
    ),
    sums AS (
        SELECT x.*,
               coalesce((SELECT sum(d.held) FROM deposits AS d WHERE d.id = x.id), 0) AS held,
               coalesce((SELECT sum(d.interest) FROM deposits AS d WHERE d.id = x.id), 0) AS due
        FROM leases AS x
    )
    SELECT (row_number() OVER (
                ORDER BY s.property COLLATE "C", s.unit COLLATE "C", s.id
            ))::integer,
           s.id, s.property, s.unit, s.tenants, s.held,
           trust_deposit_interest_rate(s.property_id, p_as_of), s.interest_from, s.due
    FROM sums AS s
    WHERE s.held > 0 OR s.due > 0
    ORDER BY 1;
END;
$$;

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_deposit_interest_terms
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_disburse BEFORE INSERT ON trust_deposit_interest_payments
FOR EACH ROW WHEN (new.paid_as = 'deposit')
EXECUTE FUNCTION trust_check_staff_role('disburse', 'pay out money');
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_deposit_interest_terms
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_deposit_interest_payments
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_deposit_interest_lines
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_deposit_interest_terms
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_deposit_interest_payments
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_deposit_interest_lines
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_deposit_interest_terms FROM PUBLIC;
REVOKE ALL ON trust_deposit_interest_payments FROM PUBLIC;
REVOKE ALL ON trust_deposit_interest_lines FROM PUBLIC;
GRANT SELECT, INSERT ON trust_deposit_interest_terms TO trust_app;
GRANT SELECT ON trust_deposit_interest_payments, trust_deposit_interest_lines TO trust_app;
GRANT SELECT ON trust_deposit_interest_terms, trust_deposit_interest_payments,
trust_deposit_interest_lines TO trust_ai_agent;

REVOKE ALL ON FUNCTION trust_deposit_interest_rate(uuid, date) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_deposit_interest_on(text, uuid, date, date) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_deposit_interest_from(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_deposit_interest_due(uuid, date, date) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_pay_deposit_interest(
    uuid, uuid, date, text, text, text, timestamptz
) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_report_deposit_interest(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_deposit_interest_rate(uuid, date),
trust_deposit_interest_on(text, uuid, date, date), trust_deposit_interest_from(uuid),
trust_deposit_interest_due(uuid, date, date) TO trust_app;
GRANT EXECUTE ON FUNCTION trust_pay_deposit_interest(
    uuid, uuid, date, text, text, text, timestamptz
) TO trust_app;
GRANT EXECUTE ON FUNCTION trust_report_deposit_interest(uuid, date) TO trust_app, trust_ai_agent;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- Interest paid on a deposit moved money (added to it) or made a credit; undoing the tables
-- would leave those without their record.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_deposit_interest_payments) THEN
        RAISE EXCEPTION 'trust: deposit interest was paid; this migration is not rolled back'
            USING ERRCODE = 'object_in_use';
    END IF;
END;
$$;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_deposit_interest(uuid, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_pay_deposit_interest(uuid, uuid, date, text, text, text, timestamptz);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_deposit_interest_due(uuid, date, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_deposit_interest_from(uuid);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_deposit_interest_on(text, uuid, date, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_deposit_interest_rate(uuid, date);
-- squawk-ignore ban-drop-table
DROP TABLE trust_deposit_interest_lines;
-- squawk-ignore ban-drop-table
DROP TABLE trust_deposit_interest_payments;
-- squawk-ignore ban-drop-table
DROP TABLE trust_deposit_interest_terms;
