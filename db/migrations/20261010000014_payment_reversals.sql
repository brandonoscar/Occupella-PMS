-- migrate:up
-- A payment that bounces (issue #10's follow-up): undo its match without rewriting history.
--
-- Matches are append-only, so a payment that comes back (a returned check, a failed transfer)
-- needs a record of its own. The PMC posts the reversing transfer in the ledger as usual (from
-- the owner's account back where the money came from) and records it against the match with
-- trust_reverse_payment. From the reversal's date on:
--   - the charge counts as paid that much less, so it can be paid again;
--   - the rent roll shows it owed again;
--   - the original transfer still can't pay anything else: its money left the owner.
-- A reversal undoes no more than the match, the reversing transfer is spent no more than its
-- amount, and the same reversal retried returns the original.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_payment_reversals (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    charge_id uuid NOT NULL,
    transfer_id text NOT NULL,  -- the payment that bounced
    reversal_id text NOT NULL REFERENCES pgledger_transfers (id),  -- the transfer undoing it
    amount numeric NOT NULL CHECK (amount > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (charge_id, transfer_id, reversal_id),
    FOREIGN KEY (pmc_id, charge_id) REFERENCES trust_charges (pmc_id, id),
    FOREIGN KEY (charge_id, transfer_id) REFERENCES trust_charge_payments (charge_id, transfer_id)
);
CREATE INDEX trust_payment_reversals_reversal_id ON trust_payment_reversals (reversal_id);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_payment_reversals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

REVOKE ALL ON trust_payment_reversals FROM PUBLIC;
GRANT SELECT ON trust_payment_reversals TO trust_app;  -- written only by trust_reverse_payment

-- Record that a ledger transfer undoes part or all of a payment's match to a charge. The
-- reversing transfer must move money the opposite way of the payment (from the owner's account
-- back to where it came from), dated no earlier. Runs as the owner.
CREATE FUNCTION trust_reverse_payment(
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

REVOKE ALL ON FUNCTION trust_reverse_payment(uuid, uuid, text, text, numeric) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_reverse_payment(uuid, uuid, text, text, numeric) TO trust_app;

-- A charge can be paid again once a payment of it is reversed. CREATE OR REPLACE keeps the
-- grants of migration 20261010000012.
CREATE OR REPLACE FUNCTION trust_apply_payment(
    p_pmc_id uuid,
    p_charge_id uuid,
    p_transfer_id text,
    p_amount numeric
) RETURNS numeric
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_charge trust_charges;
    v_property_id uuid;
    v_transfer pgledger_transfers;
    v_existing numeric;
    v_paid numeric;
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

    -- Paid so far: matched, less what bounced back (trust_reverse_payment).
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.charge_id = p_charge_id;
    SELECT v_paid - coalesce(sum(r.amount), 0) INTO v_paid
    FROM trust_payment_reversals AS r WHERE r.charge_id = p_charge_id;
    IF v_paid + p_amount > v_charge.amount THEN
        RAISE EXCEPTION 'trust: charge % is % and already paid %; % more is too much',
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

-- The rent roll counts a reversed payment as unpaid from the reversal's date. CREATE OR REPLACE
-- keeps the grants of migration 20261010000013.
CREATE OR REPLACE FUNCTION trust_report_rent_roll(
    p_pmc_id uuid,
    p_as_of date
) RETURNS TABLE (
    line integer,
    item text,
    property text,
    unit text,
    tenants text,
    detail text,
    monthly_rent numeric,
    deposits numeric,
    prepaid numeric,
    charged numeric,
    paid numeric,
    balance_due numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    -- The end of the day, UTC: the first moment that is not on the roll.
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a rent roll is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH held AS (
        -- Each tenant's deposits and prepaid rent at the end of the day.
        SELECT t.tenant_id,
               coalesce(sum(e.amount) FILTER (WHERE t.kind = 'tenant_deposit'), 0.00) AS deposits,
               coalesce(sum(e.amount) FILTER (WHERE t.kind = 'prepaid_rent'), 0.00) AS prepaid
        FROM trust_ledger_accounts AS t
        JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
        JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        WHERE t.pmc_id = p_pmc_id AND t.kind IN ('tenant_deposit', 'prepaid_rent')
          AND tr.event_at < v_cutoff
        GROUP BY t.tenant_id
    ),
    leases AS (
        SELECT l.id, l.unit_id, l.starts_on, l.ends_on, l.monthly_rent,
               l.starts_on <= p_as_of AND p_as_of <= coalesce(l.ends_on, 'infinity'::date)
                   AS is_current,
               coalesce((
                   SELECT sum(CASE c.kind WHEN 'credit' THEN -c.amount ELSE c.amount END)
                   FROM trust_charges AS c
                   WHERE c.lease_id = l.id AND c.due_on <= p_as_of
               ), 0.00) AS charged,
               coalesce((
                   SELECT sum(cp.amount)
                   FROM trust_charges AS c
                   JOIN trust_charge_payments AS cp ON cp.charge_id = c.id
                   JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
                   WHERE c.lease_id = l.id AND tr.event_at < v_cutoff
               ), 0.00) - coalesce((
                   SELECT sum(r.amount)
                   FROM trust_charges AS c
                   JOIN trust_payment_reversals AS r ON r.charge_id = c.id
                   JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
                   WHERE c.lease_id = l.id AND tr.event_at < v_cutoff
               ), 0.00) AS paid
        FROM trust_leases AS l
        WHERE l.pmc_id = p_pmc_id
    ),
    on_lease AS (
        SELECT lt.lease_id,
               string_agg(tn.display_name, ', ' ORDER BY tn.display_name COLLATE "C", tn.id)
                   AS names,
               sum(coalesce(h.deposits, 0.00)) AS deposits,
               sum(coalesce(h.prepaid, 0.00)) AS prepaid
        FROM trust_lease_tenants AS lt
        JOIN trust_tenants AS tn ON tn.id = lt.tenant_id
        LEFT JOIN held AS h ON h.tenant_id = lt.tenant_id
        WHERE lt.pmc_id = p_pmc_id
        GROUP BY lt.lease_id
    ),
    current_tenants AS (
        SELECT DISTINCT lt.tenant_id
        FROM trust_lease_tenants AS lt
        JOIN leases AS l ON l.id = lt.lease_id
        WHERE l.is_current
    ),
    units AS (
        SELECT u.id, pr.display_name AS property_name, u.display_name AS unit_name,
               l.id AS lease_id
        FROM trust_units AS u
        JOIN trust_properties AS pr ON pr.id = u.property_id
        LEFT JOIN leases AS l ON l.unit_id = u.id AND l.is_current
        WHERE u.pmc_id = p_pmc_id
    ),
    lines AS (
        -- (section, property, unit, unit id, step) orders the roll.
        SELECT 0 AS section, NULL::text AS property_name, NULL::text AS unit_name,
               NULL::uuid AS unit_id, 1 AS step, 'PMC' AS item, NULL::text AS names,
               pmc.display_name AS detail, NULL::numeric AS rent, NULL::numeric AS deposits,
               NULL::numeric AS prepaid, NULL::numeric AS charged, NULL::numeric AS paid
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 2, 'as of', NULL,
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, u.property_name, u.unit_name, u.id, 0, 'unit', o.names,
               CASE
                   WHEN l.id IS NULL THEN 'vacant'
                   WHEN l.ends_on IS NULL
                       THEN to_char(l.starts_on, 'YYYY-MM-DD') || ', month to month'
                   ELSE to_char(l.starts_on, 'YYYY-MM-DD') || ' to '
                        || to_char(l.ends_on, 'YYYY-MM-DD')
               END,
               l.monthly_rent, o.deposits, o.prepaid, l.charged, l.paid
        FROM units AS u
        LEFT JOIN leases AS l ON l.id = u.lease_id
        LEFT JOIN on_lease AS o ON o.lease_id = u.lease_id
        UNION ALL
        -- g.on_current: true for current leases, false for the rest, NULL for all of them.
        SELECT 2, NULL, NULL, NULL, g.step, g.item, NULL,
               CASE WHEN g.on_current THEN (
                   SELECT count(u.lease_id) || ' of ' || count(*) || ' units leased'
                   FROM units AS u
               ) END,
               CASE WHEN g.on_current THEN coalesce((
                   SELECT sum(l.monthly_rent) FROM leases AS l WHERE l.is_current
               ), 0.00) END,
               coalesce((
                   SELECT sum(h.deposits) FROM held AS h
                   WHERE g.on_current IS NULL OR g.on_current = EXISTS (
                       SELECT ct.tenant_id FROM current_tenants AS ct
                       WHERE ct.tenant_id = h.tenant_id
                   )
               ), 0.00),
               coalesce((
                   SELECT sum(h.prepaid) FROM held AS h
                   WHERE g.on_current IS NULL OR g.on_current = EXISTS (
                       SELECT ct.tenant_id FROM current_tenants AS ct
                       WHERE ct.tenant_id = h.tenant_id
                   )
               ), 0.00),
               coalesce((
                   SELECT sum(l.charged) FROM leases AS l
                   WHERE g.on_current IS NULL OR g.on_current = l.is_current
               ), 0.00),
               coalesce((
                   SELECT sum(l.paid) FROM leases AS l
                   WHERE g.on_current IS NULL OR g.on_current = l.is_current
               ), 0.00)
        FROM (VALUES
            (0, 'total: current leases', true),
            (1, 'not on a current lease', false),
            (2, 'total: all tenants', NULL)
        ) AS g (step, item, on_current)
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.property_name COLLATE "C", l.unit_name COLLATE "C",
                        l.unit_id, l.step
           )::integer,
           l.item,
           l.property_name,
           l.unit_name,
           l.names,
           l.detail,
           l.rent,
           l.deposits,
           l.prepaid,
           l.charged,
           l.paid,
           l.charged - l.paid
    FROM lines AS l
    ORDER BY 1;
END;
$$;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'payment_reversals cannot be rolled back: reversals are records';
END;
$$;
