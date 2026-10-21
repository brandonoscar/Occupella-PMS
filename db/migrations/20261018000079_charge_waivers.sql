-- migrate:up
-- Waiving a charge (the founder's review, q019): staff take a late fee, an NSF fee or any charge
-- off a lease, all of it or part, with why. A waiver is a credit (a trust_charges row, so every
-- balance, ledger and statement already counts it) linked to the charge it waives
-- (`trust_charge_waivers`, append-only, naming who recorded it). Where other credits come off the
-- oldest open charges first, a waiver comes off its own charge:
--
--   trust_open_charges, trust_report_delinquency
--       a charge's waivers take what they can off it first; anything a waiver could not take
--       (the charge was paid again after) joins the other credits, oldest charges first.
--   trust_apply_payment
--       never matches a charge past its amount less what was paid and waived of it.
--   trust_return_deposit
--       keeps nothing of a deposit for what was waived.
--
-- A waiver is checked when written (its trigger takes the charge's lock, as a match does): a
-- credit of the same lease and PMC, of no more than is left of the charge once its payments
-- (net of bounces) and earlier waivers are counted; never a credit waived. Nothing here moves
-- money: what tenants owe stays out of the trust ledger.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_charge_waivers (
    -- The credit that waives: one waiver each.
    credit_charge_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    waived_charge_id uuid NOT NULL,
    reason text NOT NULL CHECK (reason ~ '\S' AND char_length(reason) <= 500),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, credit_charge_id) REFERENCES trust_charges (pmc_id, id),
    FOREIGN KEY (pmc_id, waived_charge_id) REFERENCES trust_charges (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_charge_waivers_waived ON trust_charge_waivers (pmc_id, waived_charge_id);
CREATE INDEX trust_charge_waivers_recorded_by ON trust_charge_waivers (pmc_id, recorded_by);

-- A waiver is a credit of the waived charge's lease, of no more than is left of the charge.
-- Runs as the owner to take the charge's lock, which the app can't, as trust_apply_payment does:
-- a match and a waiver of one charge wait for each other, and each then counts the other.
CREATE FUNCTION trust_check_charge_waiver() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_credit trust_charges;
    v_charge trust_charges;
    v_paid numeric;
    v_waived numeric;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: waive a charge at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    SELECT c.* INTO v_credit FROM trust_charges AS c
    WHERE c.id = NEW.credit_charge_id AND c.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- no such charge of the PMC: the foreign key refuses it
    END IF;
    IF v_credit.kind <> 'credit' THEN
        RAISE EXCEPTION 'trust: a waiver is a credit, not a %', v_credit.kind
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT c.* INTO v_charge FROM trust_charges AS c
    WHERE c.id = NEW.waived_charge_id AND c.pmc_id = NEW.pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;
    IF v_charge.kind = 'credit' THEN
        RAISE EXCEPTION 'trust: a credit is not waived; it is owed to no one'
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_charge.lease_id <> v_credit.lease_id THEN
        RAISE EXCEPTION 'trust: a waiver is a credit of the waived charge''s own lease'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.charge_id = v_charge.id;
    SELECT v_paid - coalesce(sum(r.amount), 0) INTO v_paid
    FROM trust_payment_reversals AS r WHERE r.charge_id = v_charge.id;
    SELECT coalesce(sum(k.amount), 0) INTO v_waived
    FROM trust_charge_waivers AS w JOIN trust_charges AS k ON k.id = w.credit_charge_id
    WHERE w.waived_charge_id = v_charge.id;
    IF v_paid + v_waived + v_credit.amount > v_charge.amount THEN
        RAISE EXCEPTION 'trust: % is left of charge % (% paid, % waived); % can''t be waived',
            v_charge.amount - v_paid - v_waived, v_charge.id, v_paid, v_waived, v_credit.amount
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_charge_waiver() FROM PUBLIC;

CREATE TRIGGER trust_charge_waiver_fits BEFORE INSERT ON trust_charge_waivers
FOR EACH ROW EXECUTE FUNCTION trust_check_charge_waiver();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_charge_waivers
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_charge_waivers
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_charge_waivers
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_charge_waivers FROM PUBLIC;
GRANT SELECT, INSERT ON trust_charge_waivers TO trust_app;
-- The AI role runs the delinquency report, which reads waivers.
GRANT SELECT ON trust_charge_waivers TO trust_ai_agent;

CREATE OR REPLACE FUNCTION trust_apply_payment(p_pmc_id uuid, p_charge_id uuid, p_transfer_id text, p_amount numeric) RETURNS numeric
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_charge trust_charges;
    v_property_id uuid;
    v_transfer pgledger_transfers;
    v_existing numeric;
    v_paid numeric;
    v_hold trust_payment_holds;
BEGIN
    -- The sums below must count every match that committed while this waited for the locks.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: apply a payment at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a payment applies a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Matches to one charge, and then to one transfer, wait for each other.
    SELECT c.* INTO v_charge
    FROM trust_charges AS c
    WHERE c.id = p_charge_id AND c.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no charge % in PMC %', p_charge_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_charge.kind = 'credit' THEN
        RAISE EXCEPTION 'trust: a credit is not paid; it takes its amount off what is owed'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.* INTO v_transfer
    FROM pgledger_transfers AS t
    WHERE t.id = p_transfer_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no transfer %', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF EXISTS (
        SELECT c.transfer_id FROM trust_owner_contributions AS c
        WHERE c.transfer_id = p_transfer_id
    ) THEN
        RAISE EXCEPTION 'trust: transfer % is an owner''s contribution, not a tenant''s payment',
            p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT u.property_id INTO v_property_id
    FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
    WHERE l.id = v_charge.lease_id;
    IF NOT EXISTS (
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = v_transfer.to_account_id AND t.pmc_id = p_pmc_id
          AND t.kind = 'owner_property' AND t.property_id = v_property_id
    ) OR NOT EXISTS (
        SELECT f.ledger_account_id FROM trust_ledger_accounts AS f
        WHERE f.ledger_account_id = v_transfer.from_account_id AND f.pmc_id = p_pmc_id
          AND (
              f.kind = 'bank_cash'
              OR (f.kind IN ('prepaid_rent', 'tenant_deposit') AND f.tenant_id IN (
                  SELECT lt.tenant_id FROM trust_lease_tenants AS lt
                  WHERE lt.lease_id = v_charge.lease_id
              ))
          )
    ) THEN
        RAISE EXCEPTION 'trust: transfer % did not pay into the owner''s account for this '
            'charge''s property, from cash or money held for a tenant on its lease', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT p.amount INTO v_existing
    FROM trust_charge_payments AS p
    WHERE p.charge_id = p_charge_id AND p.transfer_id = p_transfer_id;
    IF FOUND THEN
        IF v_existing <> p_amount THEN
            RAISE EXCEPTION 'trust: transfer % already pays % of charge %, not %',
                p_transfer_id, v_existing, p_charge_id, p_amount
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_existing;  -- a retry: the original match
    END IF;

    -- A payment hold on the lease (an eviction, say) in force on the transfer's day (UTC)
    -- refuses it, unless the transfer was allowed for that hold. The lease's row lock orders
    -- this against a hold being placed: one waits for the other, then sees it.
    PERFORM l.id FROM trust_leases AS l WHERE l.id = v_charge.lease_id FOR SHARE;
    SELECT h.* INTO v_hold
    FROM trust_payment_holds AS h
    LEFT JOIN trust_payment_hold_releases AS r ON r.hold_id = h.id
    WHERE h.lease_id = v_charge.lease_id
      AND h.starts_on <= (v_transfer.event_at AT TIME ZONE 'UTC')::date
      AND (r.ends_on IS NULL OR (v_transfer.event_at AT TIME ZONE 'UTC')::date < r.ends_on)
      AND NOT EXISTS (
          SELECT a.hold_id FROM trust_payment_hold_allowances AS a
          WHERE a.hold_id = h.id AND a.transfer_id = p_transfer_id
      )
    ORDER BY h.starts_on, h.id
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: lease % is on a payment hold from % (%); transfer % is not '
            'allowed for it', v_charge.lease_id, v_hold.starts_on, v_hold.reason, p_transfer_id
            USING ERRCODE = 'check_violation',
                  HINT = 'Record an allowance for this transfer (trust_payment_hold_allowances) '
                         'to accept it.';
    END IF;

    -- Paid so far: matched, less what bounced back (trust_reverse_payment).
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.charge_id = p_charge_id;
    SELECT v_paid - coalesce(sum(r.amount), 0) INTO v_paid
    FROM trust_payment_reversals AS r WHERE r.charge_id = p_charge_id;
    -- What waivers took off it (credits linked to it, migration 0079) is settled too.
    SELECT v_paid + coalesce(sum(k.amount), 0) INTO v_paid
    FROM trust_charge_waivers AS w
    JOIN trust_charges AS k ON k.id = w.credit_charge_id
    WHERE w.waived_charge_id = p_charge_id;
    IF v_paid + p_amount > v_charge.amount THEN
        RAISE EXCEPTION 'trust: charge % is % and already paid or waived %; % more is too much',
            p_charge_id, v_charge.amount, v_paid, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    -- A transfer pays only once, even after a reversal: its money left the owner's account.
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.transfer_id = p_transfer_id;
    IF v_paid + p_amount > v_transfer.amount THEN
        RAISE EXCEPTION 'trust: transfer % moved % and already pays %; % more is too much',
            p_transfer_id, v_transfer.amount, v_paid, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_charge_payments (pmc_id, charge_id, transfer_id, amount)
    VALUES (p_pmc_id, p_charge_id, p_transfer_id, p_amount);
    RETURN p_amount;
END;
$$;

CREATE OR REPLACE FUNCTION trust_open_charges(p_pmc_id uuid, p_lease_id uuid, p_at timestamp with time zone) RETURNS TABLE(lease_id uuid, charge_id uuid, open numeric)
    LANGUAGE sql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
    WITH charged AS (
        SELECT c.id, c.lease_id, c.kind, c.amount, c.due_on,
               c.amount
               - coalesce((
                   SELECT sum(cp.amount)
                   FROM trust_charge_payments AS cp
                   JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
                   WHERE cp.charge_id = c.id AND t.event_at < p_at
               ), 0)
               + coalesce((
                   SELECT sum(r.amount)
                   FROM trust_payment_reversals AS r
                   JOIN pgledger_transfers AS t ON t.id = r.reversal_id
                   WHERE r.charge_id = c.id AND t.event_at < p_at
               ), 0) AS left_owing,
               EXISTS (SELECT 1 FROM trust_charge_waivers AS w
                       WHERE w.credit_charge_id = c.id) AS waiver
        FROM trust_charges AS c
        WHERE c.pmc_id = p_pmc_id AND (p_lease_id IS NULL OR c.lease_id = p_lease_id)
    ),
    -- What waivers due before the moment's day take off the charge each one names.
    waived AS (
        SELECT w.waived_charge_id AS id, sum(k.amount) AS amount
        FROM charged AS k
        JOIN trust_charge_waivers AS w ON w.credit_charge_id = k.id
        WHERE k.due_on < (p_at AT TIME ZONE 'UTC')::date
        GROUP BY w.waived_charge_id
    ),
    -- Each charge after its own waivers, and what of them it could not take.
    settled AS (
        SELECT k.lease_id, k.id, k.due_on,
               k.left_owing - least(greatest(k.left_owing, 0), coalesce(w.amount, 0)) AS left_owing,
               coalesce(w.amount, 0) - least(greatest(k.left_owing, 0), coalesce(w.amount, 0))
                   AS spare
        FROM charged AS k
        LEFT JOIN waived AS w ON w.id = k.id
        WHERE k.kind <> 'credit'
    ),
    -- The rest come off the oldest open charges: credits that waive nothing, and what a waiver
    -- could not take off its own charge.
    credits AS (
        SELECT x.lease_id, sum(x.amount) AS amount
        FROM (
            SELECT k.lease_id, k.amount FROM charged AS k
            WHERE k.kind = 'credit' AND NOT k.waiver
              AND k.due_on < (p_at AT TIME ZONE 'UTC')::date
            UNION ALL
            SELECT s.lease_id, s.spare FROM settled AS s WHERE s.spare > 0
        ) AS x
        GROUP BY x.lease_id
    ),
    owing AS (
        SELECT s.lease_id, s.id, s.left_owing,
               coalesce(sum(s.left_owing) OVER (
                   PARTITION BY s.lease_id ORDER BY s.due_on, s.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ), 0) AS owing_before
        FROM settled AS s
    )
    SELECT o.lease_id, o.id,
           o.left_owing
           - least(o.left_owing, greatest(coalesce(cr.amount, 0) - o.owing_before, 0))
    FROM owing AS o
    LEFT JOIN credits AS cr ON cr.lease_id = o.lease_id
$$;

CREATE OR REPLACE FUNCTION trust_report_delinquency(p_pmc_id uuid, p_as_of date) RETURNS TABLE(line integer, item text, property text, unit text, tenants text, current_due numeric, days_1_30 numeric, days_31_60 numeric, days_61_90 numeric, over_90 numeric, total numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: delinquency is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH paid AS (
        -- What of each charge was paid by the end of the day, net of bounces by then.
        SELECT c.id,
               coalesce((
                   SELECT sum(cp.amount)
                   FROM trust_charge_payments AS cp
                   JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
                   WHERE cp.charge_id = c.id AND tr.event_at < v_cutoff
               ), 0) - coalesce((
                   SELECT sum(r.amount)
                   FROM trust_payment_reversals AS r
                   JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
                   WHERE r.charge_id = c.id AND tr.event_at < v_cutoff
               ), 0) AS amount
        FROM trust_charges AS c
        WHERE c.pmc_id = p_pmc_id
    ),
    waived AS (
        -- What waivers due by the day take off the charge each one names (migration 0079).
        SELECT w.waived_charge_id AS id, sum(k.amount) AS amount
        FROM trust_charge_waivers AS w
        JOIN trust_charges AS k ON k.id = w.credit_charge_id
        WHERE w.pmc_id = p_pmc_id AND k.due_on <= p_as_of
        GROUP BY w.waived_charge_id
    ),
    settled AS (
        -- Each charge less what was paid of it and what its own waivers take (up to what was
        -- left), and what of its waivers it could not take.
        SELECT c.id, c.lease_id, c.due_on, c.kind,
               c.amount - p.amount
               - least(greatest(c.amount - p.amount, 0), coalesce(w.amount, 0)) AS left_owing,
               coalesce(w.amount, 0)
               - least(greatest(c.amount - p.amount, 0), coalesce(w.amount, 0)) AS spare
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        LEFT JOIN waived AS w ON w.id = c.id
        WHERE c.pmc_id = p_pmc_id AND c.kind <> 'credit'
    ),
    open_charges AS (
        -- Each charge due by the day with what is left of it, oldest first.
        SELECT s.lease_id, s.due_on, s.left_owing,
               sum(s.left_owing) OVER (
                   PARTITION BY s.lease_id ORDER BY s.due_on, s.id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ) AS owing_before
        FROM settled AS s
        WHERE s.due_on <= p_as_of
    ),
    offsets AS (
        -- Per lease, what comes off the oldest charges: credits due by the day that waive
        -- nothing, what a waiver could not take off its own charge, and payments by then toward
        -- charges not yet due.
        SELECT c.lease_id,
               sum(CASE WHEN c.kind = 'credit' AND c.due_on <= p_as_of
                         AND NOT EXISTS (SELECT 1 FROM trust_charge_waivers AS w
                                         WHERE w.credit_charge_id = c.id)
                        THEN c.amount ELSE 0 END)
               + sum(CASE WHEN c.kind <> 'credit' AND c.due_on > p_as_of THEN p.amount ELSE 0 END)
               + coalesce(sum(s.spare), 0)
                   AS amount
        FROM trust_charges AS c
        JOIN paid AS p ON p.id = c.id
        LEFT JOIN settled AS s ON s.id = c.id
        WHERE c.pmc_id = p_pmc_id
        GROUP BY c.lease_id
    ),
    aged AS (
        -- What is still owed of each charge once the offsets are used up on the ones before it.
        SELECT o.lease_id, p_as_of - o.due_on AS late,
               o.left_owing - least(
                   o.left_owing,
                   greatest(coalesce(f.amount, 0) - coalesce(o.owing_before, 0), 0)
               ) AS owed
        FROM open_charges AS o
        LEFT JOIN offsets AS f ON f.lease_id = o.lease_id
    ),
    by_lease AS (
        SELECT a.lease_id,
               sum(a.owed) FILTER (WHERE a.late = 0) AS b0,
               sum(a.owed) FILTER (WHERE a.late BETWEEN 1 AND 30) AS b1,
               sum(a.owed) FILTER (WHERE a.late BETWEEN 31 AND 60) AS b2,
               sum(a.owed) FILTER (WHERE a.late BETWEEN 61 AND 90) AS b3,
               sum(a.owed) FILTER (WHERE a.late > 90) AS b4,
               sum(a.owed) AS owed
        FROM aged AS a
        GROUP BY a.lease_id
        HAVING sum(a.owed) > 0
    ),
    lines AS (
        SELECT 0 AS section, NULL::text AS property_name, NULL::text AS unit_name,
               NULL::date AS starts_on, 1 AS step, 'PMC' AS item, pmc.display_name AS names,
               NULL::numeric AS b0, NULL::numeric AS b1, NULL::numeric AS b2,
               NULL::numeric AS b3, NULL::numeric AS b4, NULL::numeric AS owed
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 2, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC',
               NULL, NULL, NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, pr.display_name, u.display_name, l.starts_on, 0, 'lease',
               (SELECT string_agg(tn.display_name, ', ' ORDER BY tn.display_name COLLATE "C",
                                  tn.id)
                FROM trust_lease_tenants AS lt
                JOIN trust_tenants AS tn ON tn.id = lt.tenant_id
                WHERE lt.lease_id = l.id),
               coalesce(b.b0, 0), coalesce(b.b1, 0), coalesce(b.b2, 0), coalesce(b.b3, 0),
               coalesce(b.b4, 0), b.owed
        FROM by_lease AS b
        JOIN trust_leases AS l ON l.id = b.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        JOIN trust_properties AS pr ON pr.id = u.property_id
        UNION ALL
        SELECT 2, NULL, NULL, NULL, 0, 'total', NULL,
               coalesce(sum(b.b0), 0), coalesce(sum(b.b1), 0), coalesce(sum(b.b2), 0),
               coalesce(sum(b.b3), 0), coalesce(sum(b.b4), 0), coalesce(sum(b.owed), 0)
        FROM by_lease AS b
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.property_name COLLATE "C", l.unit_name COLLATE "C",
                        l.starts_on, l.step
           )::integer,
           l.item,
           l.property_name,
           l.unit_name,
           -- The PMC's name and the day sit in the tenants column of the first two lines.
           l.names,
           round(l.b0, 2), round(l.b1, 2), round(l.b2, 2), round(l.b3, 2), round(l.b4, 2),
           round(l.owed, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE OR REPLACE FUNCTION trust_return_deposit(p_pmc_id uuid, p_lease_id uuid, p_owner_account text, p_request_key text, p_event_at timestamp with time zone) RETURNS numeric
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_lease trust_leases;
    v_done trust_deposit_returns;
    v_day date := (p_event_at AT TIME ZONE 'UTC')::date;
    v_owner trust_ledger_accounts;
    v_owner_cash text;
    v_still record;
    v_held numeric;
    v_kept numeric;
    v_keep numeric;
    v_owners bigint;
    v_line bigint := 0::bigint;
    v_deposit record;
    v_charge record;
    v_take numeric;
    v_left numeric;
    v_part numeric;
    v_transfer text;
BEGIN
    -- What the lease owes must count every payment that committed while this waited.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: return a deposit at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF coalesce(p_request_key, '') = '' OR p_event_at IS NULL THEN
        RAISE EXCEPTION 'trust: a deposit''s return needs a request key and the time it was paid'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- The lease's rent work (src/pms/api/rent.py) waits for this, and this for it. The lease's
    -- row, as trust_apply_payment takes it, holds off a change of its days or a new hold.
    PERFORM pg_advisory_xact_lock(hashtextextended('rent of lease ' || p_lease_id, 0));
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT r.* INTO v_done FROM trust_deposit_returns AS r WHERE r.lease_id = p_lease_id;
    IF FOUND THEN
        IF v_done.request_key <> p_request_key THEN
            RAISE EXCEPTION 'trust: the deposit of lease % was returned already', p_lease_id
                USING ERRCODE = 'unique_violation',
                      HINT = 'Resend a request key only to retry.';
        END IF;
        RETURN v_done.returned;  -- a retry: what went back then
    END IF;

    IF v_lease.ends_on IS NULL OR v_lease.ends_on >= v_day THEN
        RAISE EXCEPTION 'trust: lease % has not ended by %; end it first', p_lease_id, v_day
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT t.display_name AS tenant, l.id AS lease INTO v_still
    FROM trust_lease_tenants AS mine
    JOIN trust_lease_tenants AS other ON other.tenant_id = mine.tenant_id
    JOIN trust_leases AS l ON l.id = other.lease_id
    JOIN trust_tenants AS t ON t.id = mine.tenant_id
    WHERE mine.lease_id = p_lease_id AND l.id <> p_lease_id
      AND (l.ends_on IS NULL OR l.ends_on >= v_day)
    ORDER BY t.display_name COLLATE "C", l.id
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: % is on lease % still, and their deposit stays with it',
            v_still.tenant, v_still.lease
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT count(*), min(t.ledger_account_id) INTO v_owners, v_owner.ledger_account_id
    FROM trust_ledger_accounts AS t
    JOIN trust_units AS u ON u.property_id = t.property_id
    WHERE u.id = v_lease.unit_id AND t.pmc_id = p_pmc_id AND t.kind = 'owner_property'
      AND (p_owner_account IS NULL OR t.ledger_account_id = p_owner_account);
    IF v_owners <> 1 THEN
        RAISE EXCEPTION 'trust: % is not the one owner''s account of the lease''s property',
            coalesce(p_owner_account, 'no account given')
            USING ERRCODE = 'invalid_parameter_value',
                  HINT = 'Name the owner''s account when the property has more than one.';
    END IF;
    SELECT t.* INTO v_owner FROM trust_ledger_accounts AS t
    WHERE t.ledger_account_id = v_owner.ledger_account_id;
    SELECT t.ledger_account_id INTO v_owner_cash FROM trust_ledger_accounts AS t
    WHERE t.bank_account_id = v_owner.bank_account_id AND t.kind = 'bank_cash';

    -- The deposits held for the lease's tenants. One paid in meanwhile stays held (the
    -- transfers below take each account's lock); one taken out meanwhile, by the return of
    -- another lease of the same tenant, makes this refuse, as no deposit goes below zero.
    SELECT coalesce(sum(a.balance), 0) INTO v_held FROM pgledger_accounts AS a
    JOIN trust_ledger_accounts AS t ON t.ledger_account_id = a.id
    JOIN trust_lease_tenants AS lt ON lt.tenant_id = t.tenant_id
    WHERE lt.lease_id = p_lease_id AND t.kind = 'tenant_deposit';
    IF v_held <= 0 THEN
        RAISE EXCEPTION 'trust: no security deposit is held for lease %', p_lease_id
            USING ERRCODE = 'check_violation';
    END IF;
    v_kept := least(trust_lease_owed(p_lease_id), v_held);
    v_keep := v_kept;  -- what is left to keep, deposit by deposit

    INSERT INTO trust_deposit_returns (
        lease_id, pmc_id, request_key, returned_at, held, kept, returned, returned_by
    ) VALUES (
        p_lease_id, p_pmc_id, p_request_key, p_event_at, v_held, v_kept, v_held - v_kept,
        nullif(current_setting('trust.staff_id', true), '')::uuid
    );

    FOR v_deposit IN
        SELECT t.ledger_account_id AS account, t.tenant_id, a.balance,
               (SELECT c.ledger_account_id FROM trust_ledger_accounts AS c
                WHERE c.bank_account_id = t.bank_account_id AND c.kind = 'bank_cash') AS cash
        FROM trust_ledger_accounts AS t
        JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
        JOIN trust_lease_tenants AS lt ON lt.tenant_id = t.tenant_id
        JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        WHERE lt.lease_id = p_lease_id AND t.kind = 'tenant_deposit'
        ORDER BY tn.display_name COLLATE "C", t.tenant_id, t.ledger_account_id
    LOOP
        v_take := least(v_keep, v_deposit.balance);
        IF v_take > 0 THEN
            -- Kept: into the owner's account, its cash moving to the owner's trust bank account.
            SELECT t.id INTO v_transfer
            FROM pgledger_create_transfers(
                ARRAY[(v_deposit.account, v_owner.ledger_account_id, v_take)::transfer_request,
                      (v_owner_cash, v_deposit.cash, v_take)::transfer_request],
                p_event_at,
                jsonb_build_object('memo', 'Security deposit kept')
            ) AS t
            WHERE t.from_account_id = v_deposit.account;
            v_left := v_take;
            FOR v_charge IN
                SELECT c.id, c.amount
                       - coalesce((SELECT sum(p.amount) FROM trust_charge_payments AS p
                                   WHERE p.charge_id = c.id), 0)
                       + coalesce((SELECT sum(r.amount) FROM trust_payment_reversals AS r
                                   WHERE r.charge_id = c.id), 0)
                       -- What a waiver took off it is not kept from the deposit (0079).
                       - coalesce((SELECT sum(k.amount) FROM trust_charge_waivers AS w
                                   JOIN trust_charges AS k ON k.id = w.credit_charge_id
                                   WHERE w.waived_charge_id = c.id), 0) AS open
                FROM trust_charges AS c
                WHERE c.lease_id = p_lease_id AND c.kind <> 'credit'
                ORDER BY c.due_on, c.created_at, c.id
            LOOP
                EXIT WHEN v_left <= 0;
                CONTINUE WHEN v_charge.open <= 0;
                v_part := least(v_left, v_charge.open);
                PERFORM trust_apply_payment(p_pmc_id, v_charge.id, v_transfer, v_part);
                v_line := v_line + 1;
                INSERT INTO trust_deposit_return_lines (
                    lease_id, line_no, pmc_id, tenant_id, charge_id, amount, transfer_id
                ) VALUES (
                    p_lease_id, v_line, p_pmc_id, v_deposit.tenant_id, v_charge.id, v_part,
                    v_transfer
                );
                v_left := v_left - v_part;
            END LOOP;
            v_keep := v_keep - v_take;
        END IF;
        IF v_deposit.balance > v_take THEN
            -- Returned: out of the security-deposit trust bank account's cash.
            SELECT t.id INTO v_transfer
            FROM pgledger_create_transfers(
                ARRAY[(v_deposit.account, v_deposit.cash, v_deposit.balance - v_take)
                      ::transfer_request],
                p_event_at,
                jsonb_build_object('memo', 'Security deposit returned')
            ) AS t;
            v_line := v_line + 1;
            INSERT INTO trust_deposit_return_lines (
                lease_id, line_no, pmc_id, tenant_id, charge_id, amount, transfer_id
            ) VALUES (
                p_lease_id, v_line, p_pmc_id, v_deposit.tenant_id, NULL,
                v_deposit.balance - v_take, v_transfer
            );
        END IF;
    END LOOP;
    RETURN v_held - v_kept;
END;
$$;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'charge_waivers cannot be rolled back: waivers are records';
END;
$$;
