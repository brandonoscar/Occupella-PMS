-- migrate:up
-- A housing authority's payments, the rest of the founder's review q034 (migration 0095 built
-- its part of the rent; docs/law.md):
--
--   trust_authority_late_fees           an authority's late payment fee, as its HAP contract
--                                       provides (24 CFR 982.451(b)(4)): grace days and a flat
--                                       fee, from a day; 0 charges none from then. Append-only,
--                                       naming who recorded it.
--   trust_assess_authority_late_fees    charges an authority's part of a rent still open at the
--                                       end of its last grace day (UTC) one late fee, under the
--                                       terms in force on its due date: a charge on the authority
--                                       (source 'late fee', late_for_id the rent's part), never
--                                       the tenant, once per part. The nightly run and an
--                                       admin's Run rent now call it.
--   trust_assistance_payment_reversals  an authority's payment the bank returned: what moved
--   trust_reverse_assistance_payment    back to cash, matched to what the payment paid of each
--                                       charge (as trust_reverse_payment is for a tenant's), so
--                                       the charge is open again. Append-only.
--   trust_apply_assistance_payment      now refuses an authority's payment dated in a payment
--                                       hold on the lease (an eviction filed, migration 0025),
--                                       unless the transfer was allowed on that hold, as a
--                                       tenant's is.
--
-- What an authority owes, its owner's balance sheet, the owner's year-end package, the
-- management fee and both income statements count what bounced back; a late fee paid by the
-- authority is a late fee on the income statements and not rent for the management fee, as a
-- tenant's late fee is.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- --- late payment fees on an authority --------------------------------------------------------

CREATE TABLE trust_authority_late_fees (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    housing_authority_id uuid NOT NULL,
    starts_on date NOT NULL,
    -- squawk-ignore prefer-bigint-over-int
    grace_days integer NOT NULL CHECK (grace_days BETWEEN 0 AND 60),
    -- 0 charges none from the day.
    flat_fee numeric NOT NULL CHECK (flat_fee >= 0 AND flat_fee = round(flat_fee, 2)),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (housing_authority_id, starts_on),
    FOREIGN KEY (pmc_id, housing_authority_id) REFERENCES trust_housing_authorities (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_authority_late_fees_pmc
ON trust_authority_late_fees (pmc_id, housing_authority_id);
CREATE INDEX trust_authority_late_fees_recorded_by
ON trust_authority_late_fees (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_authority_late_fees
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_authority_late_fees
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_authority_late_fees
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_authority_late_fees FROM PUBLIC;
GRANT SELECT, INSERT ON trust_authority_late_fees TO trust_app;
GRANT SELECT ON trust_authority_late_fees TO trust_ai_agent;

-- A late fee is a charge on the authority, of one rent's part, once.
ALTER TABLE trust_assistance_charges ADD COLUMN late_for_id uuid;
-- Replaced at once by a check that allows one more source; no guarantee is lost.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_assistance_charges DROP CONSTRAINT trust_assistance_charges_source_check;
ALTER TABLE trust_assistance_charges ADD CONSTRAINT trust_assistance_charges_source_check
CHECK (source IN ('rent', 'lease end', 'staff', 'late fee')) NOT VALID;
ALTER TABLE trust_assistance_charges ADD CONSTRAINT trust_assistance_charges_late_fee_check
CHECK (
    (source = 'late fee') = (late_for_id IS NOT NULL)
    AND (source <> 'late fee' OR (kind = 'charge' AND adjusts_id IS NULL))
) NOT VALID;
-- squawk-ignore adding-foreign-key-constraint
ALTER TABLE trust_assistance_charges ADD CONSTRAINT trust_assistance_charges_late_for_fkey
FOREIGN KEY (pmc_id, late_for_id) REFERENCES trust_assistance_charges (pmc_id, id) NOT VALID;
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX trust_assistance_charges_one_late_fee
ON trust_assistance_charges (late_for_id) WHERE source = 'late fee';

-- --- an authority's payment that bounced -------------------------------------------------------

CREATE TABLE trust_assistance_payment_reversals (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    assistance_charge_id uuid NOT NULL,
    transfer_id text NOT NULL,  -- the payment that bounced
    reversal_id text NOT NULL REFERENCES pgledger_transfers (id),  -- the transfer undoing it
    amount numeric NOT NULL CHECK (amount > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (assistance_charge_id, transfer_id, reversal_id),
    FOREIGN KEY (pmc_id, assistance_charge_id) REFERENCES trust_assistance_charges (pmc_id, id),
    FOREIGN KEY (transfer_id, assistance_charge_id)
        REFERENCES trust_assistance_payments (transfer_id, assistance_charge_id)
);
CREATE INDEX trust_assistance_payment_reversals_reversal
ON trust_assistance_payment_reversals (reversal_id);
CREATE INDEX trust_assistance_payment_reversals_pmc
ON trust_assistance_payment_reversals (pmc_id, assistance_charge_id);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_assistance_payment_reversals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_assistance_payment_reversals
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_assistance_payment_reversals FROM PUBLIC;
GRANT SELECT ON trust_assistance_payment_reversals TO trust_app, trust_ai_agent;

CREATE FUNCTION trust_reverse_assistance_payment(
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

    -- Waits for matches and reversals in progress on the charge (trust_apply_assistance_payment
    -- takes the same lock), then for those on the reversing transfer.
    PERFORM c.id FROM trust_assistance_charges AS c
    WHERE c.id = p_charge_id AND c.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no housing authority charge % in PMC %', p_charge_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT p.amount INTO v_matched
    FROM trust_assistance_payments AS p
    WHERE p.assistance_charge_id = p_charge_id AND p.transfer_id = p_transfer_id;
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
    FROM trust_assistance_payment_reversals AS r
    WHERE r.assistance_charge_id = p_charge_id AND r.transfer_id = p_transfer_id
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
    FROM trust_assistance_payment_reversals AS r
    WHERE r.assistance_charge_id = p_charge_id AND r.transfer_id = p_transfer_id;
    IF v_used + p_amount > v_matched THEN
        RAISE EXCEPTION 'trust: transfer % pays % of charge % and % of it is reversed already; '
            '% more is too much', p_transfer_id, v_matched, p_charge_id, v_used, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT coalesce(sum(r.amount), 0) INTO v_used
    FROM trust_assistance_payment_reversals AS r WHERE r.reversal_id = p_reversal_id;
    IF v_used + p_amount > v_reversal.amount THEN
        RAISE EXCEPTION 'trust: transfer % moved % and already reverses %; % more is too much',
            p_reversal_id, v_reversal.amount, v_used, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_assistance_payment_reversals (
        pmc_id, assistance_charge_id, transfer_id, reversal_id, amount
    ) VALUES (p_pmc_id, p_charge_id, p_transfer_id, p_reversal_id, p_amount);
    RETURN p_amount;
END;
$$;
REVOKE ALL ON FUNCTION trust_reverse_assistance_payment(uuid, uuid, text, text, numeric)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_reverse_assistance_payment(uuid, uuid, text, text, numeric)
TO trust_app;

-- What is open of an authority's charge: its amount and adjustments, less what was paid of it,
-- plus what of that bounced back.
CREATE OR REPLACE FUNCTION trust_assistance_open(p_charge_id uuid) RETURNS numeric
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT c.amount
           + coalesce((SELECT sum(CASE a.kind WHEN 'credit' THEN -a.amount ELSE a.amount END)
                       FROM trust_assistance_charges AS a WHERE a.adjusts_id = c.id), 0)
           - coalesce((SELECT sum(p.amount) FROM trust_assistance_payments AS p
                       WHERE p.assistance_charge_id = c.id), 0)
           + coalesce((SELECT sum(r.amount) FROM trust_assistance_payment_reversals AS r
                       WHERE r.assistance_charge_id = c.id), 0)
    FROM trust_assistance_charges AS c
    WHERE c.id = p_charge_id
$$;

-- What was open of an authority's charge at a moment: its amount and the adjustments due by
-- then, less what transfers dated before it paid, plus what of that bounced back before it.
CREATE FUNCTION trust_assistance_open_at(p_charge_id uuid, p_at timestamptz) RETURNS numeric
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT c.amount
           + coalesce((SELECT sum(CASE a.kind WHEN 'credit' THEN -a.amount ELSE a.amount END)
                       FROM trust_assistance_charges AS a
                       WHERE a.adjusts_id = c.id
                         AND a.due_on < (p_at AT TIME ZONE 'UTC')::date), 0)
           - coalesce((SELECT sum(p.amount) FROM trust_assistance_payments AS p
                       JOIN pgledger_transfers AS t ON t.id = p.transfer_id
                       WHERE p.assistance_charge_id = c.id AND t.event_at < p_at), 0)
           + coalesce((SELECT sum(r.amount) FROM trust_assistance_payment_reversals AS r
                       JOIN pgledger_transfers AS t ON t.id = r.reversal_id
                       WHERE r.assistance_charge_id = c.id AND t.event_at < p_at), 0)
    FROM trust_assistance_charges AS c
    WHERE c.id = p_charge_id
$$;
REVOKE ALL ON FUNCTION trust_assistance_open_at(uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_assistance_open_at(uuid, timestamptz) TO trust_app;

-- Each authority's part of a rent still open at the end of its last grace day (UTC), on or
-- before the day, charged its late fee once, under the terms in force on its due date. A part
-- the authority paid by then, or one whose terms charge none, gets none. Returns how many.
CREATE FUNCTION trust_assess_authority_late_fees(p_pmc_id uuid, p_as_of date) RETURNS integer
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_made integer;
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: late fees are assessed as of a day'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    WITH due AS (
        SELECT c.id, c.lease_id, c.housing_authority_id, c.due_on, t.grace_days, t.flat_fee
        FROM trust_assistance_charges AS c
        CROSS JOIN LATERAL (
            SELECT f.grace_days, f.flat_fee FROM trust_authority_late_fees AS f
            WHERE f.housing_authority_id = c.housing_authority_id AND f.starts_on <= c.due_on
            ORDER BY f.starts_on DESC
            LIMIT 1
        ) AS t
        WHERE c.pmc_id = p_pmc_id AND c.source = 'rent' AND t.flat_fee > 0
          -- The last grace day has ended by the end of the day.
          AND c.due_on + t.grace_days < p_as_of
          AND NOT EXISTS (
              SELECT 1 FROM trust_assistance_charges AS l
              WHERE l.late_for_id = c.id AND l.source = 'late fee'
          )
          AND trust_assistance_open_at(
              c.id, (c.due_on + t.grace_days + 1)::timestamp AT TIME ZONE 'UTC'
          ) > 0
    )
    INSERT INTO trust_assistance_charges (
        pmc_id, lease_id, housing_authority_id, due_on, kind, amount, memo, source, late_for_id
    )
    SELECT p_pmc_id, d.lease_id, d.housing_authority_id, d.due_on + d.grace_days + 1, 'charge',
           d.flat_fee, 'Late payment fee, rent due ' || to_char(d.due_on, 'YYYY-MM-DD'),
           'late fee', d.id
    FROM due AS d
    ORDER BY d.due_on, d.id
    ON CONFLICT (late_for_id) WHERE source = 'late fee' DO NOTHING;
    GET DIAGNOSTICS v_made = ROW_COUNT;
    RETURN v_made;
END;
$$;
REVOKE ALL ON FUNCTION trust_assess_authority_late_fees(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_assess_authority_late_fees(uuid, date) TO trust_app;

-- --- the functions that count it ---------------------------------------------------------------
-- Each as it stands, with what this migration adds.

CREATE OR REPLACE FUNCTION public.trust_check_assistance_charge()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
    v_target trust_assistance_charges;
BEGIN
    -- A late fee is on an authority's part of a rent, of the same lease and authority.
    IF NEW.late_for_id IS NOT NULL THEN
        SELECT c.* INTO v_target FROM trust_assistance_charges AS c
        WHERE c.id = NEW.late_for_id AND c.pmc_id = NEW.pmc_id;
        IF FOUND AND (v_target.source <> 'rent' OR v_target.lease_id <> NEW.lease_id
                      OR v_target.housing_authority_id <> NEW.housing_authority_id) THEN
            RAISE EXCEPTION 'trust: a late fee is on an authority''s part of a rent of the same '
                'lease and authority'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.adjusts_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT c.* INTO v_target FROM trust_assistance_charges AS c
    WHERE c.id = NEW.adjusts_id AND c.pmc_id = NEW.pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RETURN NEW;  -- the foreign key refuses it
    END IF;
    IF v_target.adjusts_id IS NOT NULL OR v_target.lease_id <> NEW.lease_id
       OR v_target.housing_authority_id <> NEW.housing_authority_id THEN
        RAISE EXCEPTION 'trust: an adjustment changes a charge of the same lease and authority, '
            'not another adjustment'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.source = 'staff' AND NEW.amount > trust_assistance_open(v_target.id) THEN
        RAISE EXCEPTION 'trust: % is more than is open of the authority''s charge, %',
            NEW.amount, trust_assistance_open(v_target.id)
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.trust_apply_assistance_payment(p_pmc_id uuid, p_charge_id uuid, p_transfer_id text, p_amount numeric)
 RETURNS numeric
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
DECLARE
    v_charge trust_assistance_charges;
    v_transfer pgledger_transfers;
    v_property_id uuid;
    v_existing numeric;
    v_open numeric;
    v_used numeric;
    v_hold trust_payment_holds;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: apply a payment at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a payment applies a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT c.* INTO v_charge FROM trust_assistance_charges AS c
    WHERE c.id = p_charge_id AND c.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no housing authority charge % in PMC %', p_charge_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_charge.adjusts_id IS NOT NULL THEN
        RAISE EXCEPTION 'trust: an adjustment is not paid; pay the charge it adjusts'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.* INTO v_transfer FROM pgledger_transfers AS t
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
          AND f.kind = 'bank_cash'
    ) THEN
        RAISE EXCEPTION 'trust: transfer % did not pay into an owner''s account of this charge''s '
            'property from cash', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_charge_payments AS p WHERE p.transfer_id = p_transfer_id)
       OR EXISTS (SELECT 1 FROM trust_owner_contributions AS c WHERE c.transfer_id = p_transfer_id)
    THEN
        RAISE EXCEPTION 'trust: transfer % is a tenant''s payment or an owner''s contribution, '
            'not a housing authority''s', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT p.amount INTO v_existing FROM trust_assistance_payments AS p
    WHERE p.assistance_charge_id = p_charge_id AND p.transfer_id = p_transfer_id;
    IF FOUND THEN
        IF v_existing <> p_amount THEN
            RAISE EXCEPTION 'trust: transfer % already pays % of charge %, not %',
                p_transfer_id, v_existing, p_charge_id, p_amount
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_existing;  -- a retry: the original match
    END IF;

    -- A payment hold on the lease (an eviction filed, migration 0025) in force on the transfer's
    -- day (UTC) refuses an authority's payment as it does a tenant's, unless the transfer was
    -- allowed for that hold (q034). The lease's row lock orders this against a hold being placed.
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

    v_open := trust_assistance_open(p_charge_id);
    IF p_amount > v_open THEN
        RAISE EXCEPTION 'trust: % is more than is open of the authority''s charge, %',
            p_amount, v_open
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT coalesce(sum(p.amount), 0) INTO v_used FROM trust_assistance_payments AS p
    WHERE p.transfer_id = p_transfer_id;
    IF v_used + p_amount > v_transfer.amount THEN
        RAISE EXCEPTION 'trust: transfer % moved %, and % of it pays charges already',
            p_transfer_id, v_transfer.amount, v_used
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_assistance_payments (transfer_id, assistance_charge_id, pmc_id, amount)
    VALUES (p_transfer_id, p_charge_id, p_pmc_id, p_amount);
    RETURN p_amount;
END;
$function$;

CREATE OR REPLACE FUNCTION public.trust_management_fee_collected(p_account text, p_start timestamp with time zone, p_end timestamp with time zone)
 RETURNS numeric
 LANGUAGE sql
 STABLE
 SET search_path TO 'public', 'pg_temp'
AS $function$
    SELECT coalesce((
        SELECT sum(cp.amount)
        FROM trust_charge_payments AS cp
        -- Rent, or a fee the rent run made from a recurring charge. (A recurring credit is
        -- never paid, so it has no payments here.)
        JOIN trust_charges AS c ON c.id = cp.charge_id
            AND (c.kind = 'rent' OR c.recurring_charge_id IS NOT NULL)
        JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
        WHERE t.to_account_id = p_account AND t.event_at >= p_start AND t.event_at < p_end
    ), 0)
    -- A housing authority's part of the rent it paid (migration 0095).
    + coalesce((
        SELECT sum(ap.amount)
        FROM trust_assistance_payments AS ap
        JOIN trust_assistance_charges AS ac ON ac.id = ap.assistance_charge_id
            AND ac.source <> 'late fee'
        JOIN pgledger_transfers AS t ON t.id = ap.transfer_id
        WHERE t.to_account_id = p_account AND t.event_at >= p_start AND t.event_at < p_end
    ), 0)
    -- ...less what of it bounced back (migration 0116); an authority's late fee is not rent.
    - coalesce((
        SELECT sum(ar.amount)
        FROM trust_assistance_payment_reversals AS ar
        JOIN trust_assistance_charges AS ac ON ac.id = ar.assistance_charge_id
            AND ac.source <> 'late fee'
        JOIN pgledger_transfers AS t ON t.id = ar.reversal_id
        WHERE t.from_account_id = p_account AND t.event_at >= p_start AND t.event_at < p_end
    ), 0)
    - coalesce((
        SELECT sum(r.amount)
        FROM trust_payment_reversals AS r
        JOIN trust_charges AS c ON c.id = r.charge_id
            AND (c.kind = 'rent' OR c.recurring_charge_id IS NOT NULL)
        JOIN pgledger_transfers AS t ON t.id = r.reversal_id
        WHERE t.from_account_id = p_account AND t.event_at >= p_start AND t.event_at < p_end
    ), 0)
$function$;

CREATE OR REPLACE FUNCTION public.trust_report_income_statement(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date)
 RETURNS TABLE(line integer, property text, item text, schedule_e integer, amount numeric)
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'public', 'pg_temp'
AS $function$
#variable_conflict use_column
DECLARE
    v_start timestamptz;
    v_end timestamptz;
BEGIN
    IF p_from IS NULL OR p_through IS NULL OR p_through < p_from THEN
        RAISE EXCEPTION 'trust: an income statement runs from a day through a day no earlier, not % to %',
            p_from, p_through
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- No owner: every owner of the PMC (q108).
    IF p_owner_id IS NOT NULL AND NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_start := p_from::timestamp AT TIME ZONE 'UTC';
    v_end := (p_through + 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH schedule_e (n, label) AS (
        VALUES (3, 'Rents received'), (5, 'Advertising'), (6, 'Auto and travel'),
               (7, 'Cleaning and maintenance'), (8, 'Commissions'), (9, 'Insurance'),
               (10, 'Legal and other professional fees'), (11, 'Management fees'),
               (12, 'Mortgage interest paid to banks, etc.'), (13, 'Other interest'),
               (14, 'Repairs'), (15, 'Supplies'), (16, 'Taxes'), (17, 'Utilities'),
               (18, 'Depreciation expense or depletion'), (19, 'Other')
    ),
    incomes (part, label) AS (
        VALUES (1, 'income: rent'), (2, 'income: recurring charges'), (3, 'income: late fees'),
               (4, 'income: NSF fees'), (5, 'income: other charges')
    ),
    accounts AS (
        -- An owner's property accounts by their property; every owner's, each named by its
        -- property and owner, when no owner is given.
        SELECT t.ledger_account_id AS account,
               CASE WHEN p_owner_id IS NULL THEN pr.display_name || ', ' || o.display_name
                    ELSE pr.display_name END AS name
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        JOIN trust_owners AS o ON o.id = t.owner_id
        WHERE t.pmc_id = p_pmc_id AND (p_owner_id IS NULL OR t.owner_id = p_owner_id)
          AND t.kind = 'owner_property'
    ),
    -- What each charge's money is: rent, a recurring charge (pet rent, parking), a late fee, an
    -- NSF fee, or another charge, and the company's own type of it if it has one (its own, or
    -- its recurring charge's). A lease end's adjustment of a rent or recurring charge
    -- (migration 0051) is what it adjusts.
    classes AS (
        SELECT c.id,
               coalesce(o.charge_type_id, c.charge_type_id, rc.charge_type_id) AS charge_type,
               CASE
                   WHEN coalesce(o.kind, c.kind) = 'rent' THEN 1
                   WHEN coalesce(o.recurring_charge_id, c.recurring_charge_id) IS NOT NULL THEN 2
                   WHEN lf.fee_charge_id IS NOT NULL THEN 3
                   WHEN nf.fee_charge_id IS NOT NULL THEN 4
                   ELSE 5
               END AS part
        FROM trust_charges AS c
        LEFT JOIN trust_lease_end_adjustments AS adj ON adj.adjustment_charge_id = c.id
        LEFT JOIN trust_charges AS o ON o.id = adj.charge_id
        LEFT JOIN trust_late_fees AS lf ON lf.fee_charge_id = c.id
        LEFT JOIN trust_nsf_fees AS nf ON nf.fee_charge_id = c.id
        LEFT JOIN trust_recurring_charges AS rc
            ON rc.id = coalesce(o.recurring_charge_id, c.recurring_charge_id)
        WHERE c.pmc_id = p_pmc_id
    ),
    -- Collected from tenants in the days: payments matched to charges, less what bounced back,
    -- as the year-end package counts them (migration 0039).
    collected AS (
        SELECT tr.to_account_id AS account, k.part, k.charge_type, cp.amount
        FROM trust_charge_payments AS cp
        JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
        JOIN classes AS k ON k.id = cp.charge_id
        WHERE cp.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT tr.from_account_id, k.part, k.charge_type, -r.amount
        FROM trust_payment_reversals AS r
        JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
        JOIN classes AS k ON k.id = r.charge_id
        WHERE r.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A housing authority's part of the rent, as it paid it (migration 0095): rent; its late
        -- fee a late fee; less what bounced back (migration 0116).
        SELECT tr.to_account_id, CASE ac.source WHEN 'late fee' THEN 3 ELSE 1 END, NULL::uuid,
               ap.amount
        FROM trust_assistance_payments AS ap
        JOIN trust_assistance_charges AS ac ON ac.id = ap.assistance_charge_id
        JOIN pgledger_transfers AS tr ON tr.id = ap.transfer_id
        WHERE ap.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT tr.from_account_id, CASE ac.source WHEN 'late fee' THEN 3 ELSE 1 END, NULL::uuid,
               -ar.amount
        FROM trust_assistance_payment_reversals AS ar
        JOIN trust_assistance_charges AS ac ON ac.id = ar.assistance_charge_id
        JOIN pgledger_transfers AS tr ON tr.id = ar.reversal_id
        WHERE ar.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    fees AS (
        SELECT f.ledger_account_id AS account, 0 AS ord, tr.amount
        FROM trust_management_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT f.ledger_account_id, 1, tr.amount
        FROM trust_leasing_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A fee staff took from the owner (migration 0082): a lease-up fee with the leasing
        -- fees, an inspection or other fee with the management fees.
        SELECT f.ledger_account_id, CASE f.kind WHEN 'lease_up' THEN 1 ELSE 0 END, tr.amount
        FROM trust_owner_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A bill's markup (migration 0083), with the management fees.
        SELECT tr.from_account_id, 0, tr.amount
        FROM trust_bill_markup_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    -- Each bill's expense account: its latest row.
    categorized AS (
        SELECT DISTINCT ON (x.bill_id) x.id, x.bill_id, x.expense_account_id
        FROM trust_bill_expense_accounts AS x
        WHERE x.pmc_id = p_pmc_id
        ORDER BY x.bill_id, x.id DESC
    ),
    -- Bills, when their money left the owner's account (set aside to pay them): a split bill
    -- (migration 0069) by its lines, each in its expense account, which come to the bill.
    spent AS (
        SELECT tr.from_account_id AS account,
               coalesce(sp.expense_account_id, cz.expense_account_id) AS expense,
               coalesce(sp.amount, tr.amount) AS amount
        FROM trust_bill_payments AS b
        JOIN pgledger_transfers AS tr ON tr.id = b.transfer_id
        LEFT JOIN categorized AS cz ON cz.bill_id = b.bill_id
        LEFT JOIN trust_bill_splits AS sp ON sp.categorized_id = cz.id
        WHERE b.pmc_id = p_pmc_id AND b.step = 'set_aside'
          AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    -- Each account's lines: (group, order within it, item, Schedule E line, amount). Groups:
    -- 1 income, 3 expenses. Income and the fees show even when nothing moved; each of the
    -- company's charge types has a line of its own after them once money was collected on it,
    -- and isn't counted again in the line it would be in without one.
    items AS (
        SELECT a.account, 1 AS grp, i.part AS ord, i.label AS item, 3 AS sched,
               coalesce((SELECT sum(c.amount) FROM collected AS c
                         WHERE c.account = a.account AND c.part = i.part
                           AND c.charge_type IS NULL), 0) AS total
        FROM accounts AS a
        CROSS JOIN incomes AS i
        UNION ALL
        SELECT c.account, 1, 6, 'income: ' || ct.name, 3, sum(c.amount)
        FROM collected AS c
        JOIN accounts AS a ON a.account = c.account
        JOIN trust_charge_types AS ct ON ct.id = c.charge_type
        GROUP BY c.account, ct.id, ct.name
        UNION ALL
        SELECT a.account, 3, f.ord,
               CASE f.ord WHEN 0 THEN 'expense: management fees' ELSE 'expense: leasing fees' END,
               CASE f.ord WHEN 0 THEN 11 ELSE 8 END,
               coalesce((SELECT sum(x.amount) FROM fees AS x
                         WHERE x.account = a.account AND x.ord = f.ord), 0)
        FROM accounts AS a
        CROSS JOIN (VALUES (0), (1)) AS f (ord)
        UNION ALL
        SELECT s.account, 3, coalesce(ea.schedule_e_line, 99),
               CASE WHEN ea.id IS NULL THEN 'expense: bills not in an expense account'
                    ELSE 'expense: ' || ea.name END,
               ea.schedule_e_line,
               sum(s.amount)
        FROM spent AS s
        JOIN accounts AS a ON a.account = s.account
        LEFT JOIN trust_expense_accounts AS ea ON ea.id = s.expense
        GROUP BY s.account, ea.id, ea.name, ea.schedule_e_line
    ),
    sums AS (
        SELECT i.account,
               coalesce(sum(i.total) FILTER (WHERE i.grp = 1), 0) AS income,
               coalesce(sum(i.total) FILTER (WHERE i.grp = 3), 0) AS expenses
        FROM items AS i
        GROUP BY i.account
    ),
    -- The same by Schedule E line (group 6): rents received, and each expense line with money
    -- on it; bills in no expense account are on no line until they are given one.
    schedule AS (
        SELECT i.account, 6 AS grp, i.sched AS ord,
               'Schedule E line ' || i.sched || ': ' || e.label AS item, i.sched,
               sum(i.total) AS total
        FROM items AS i
        JOIN schedule_e AS e ON e.n = i.sched
        GROUP BY i.account, i.sched, e.label
        HAVING i.sched = 3 OR sum(i.total) <> 0
        UNION ALL
        SELECT i.account, 6, 99, 'Schedule E: on no line, bills not in an expense account',
               NULL::integer, sum(i.total)
        FROM items AS i
        WHERE i.grp = 3 AND i.sched IS NULL
        GROUP BY i.account
    ),
    rows AS (
        SELECT i.account, i.grp, i.ord, i.item, i.sched, i.total FROM items AS i
        UNION ALL
        SELECT s.account, 2, 0, 'total income', NULL::integer, s.income FROM sums AS s
        UNION ALL
        SELECT s.account, 4, 0, 'total expenses', NULL::integer, s.expenses FROM sums AS s
        UNION ALL
        SELECT s.account, 5, 0, 'net income', NULL::integer, s.income - s.expenses
        FROM sums AS s
        UNION ALL
        SELECT h.account, h.grp, h.ord, h.item, h.sched, h.total FROM schedule AS h
    ),
    lines AS (
        -- (section, property, account, group, order, item) orders the statement; names sort
        -- by byte (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS name, NULL::text AS account, 0 AS grp, 1 AS ord,
               'PMC' AS item, pmc.display_name AS label, NULL::integer AS sched,
               NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, 2, 'owner', coalesce(o.display_name, 'all owners'), NULL, NULL
        FROM (SELECT 1) AS one
        LEFT JOIN trust_owners AS o ON o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, 3, 'period',
               p_from::text || ' to ' || p_through::text || ' UTC, cash basis', NULL, NULL
        UNION ALL
        SELECT 1, a.name, r.account, r.grp, r.ord, r.item, a.name, r.sched, r.total
        FROM rows AS r
        JOIN accounts AS a ON a.account = r.account
        UNION ALL
        SELECT 2, NULL, NULL, r.grp, r.ord, 'all properties: ' || r.item, NULL, r.sched,
               sum(r.total)
        FROM rows AS r
        GROUP BY r.grp, r.ord, r.item, r.sched
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.account COLLATE "C", l.grp, l.ord,
                        l.item COLLATE "C"
           )::integer,
           -- The PMC's, owner's and period's names sit in the property column of the first
           -- lines.
           l.label,
           l.item,
           l.sched,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$function$;

CREATE OR REPLACE FUNCTION public.trust_report_income_statement_accrual(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date)
 RETURNS TABLE(line integer, property text, item text, schedule_e integer, amount numeric)
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'public', 'pg_temp'
AS $function$
#variable_conflict use_column
DECLARE
    v_start timestamptz;
    v_end timestamptz;
BEGIN
    IF p_from IS NULL OR p_through IS NULL OR p_through < p_from THEN
        RAISE EXCEPTION 'trust: an income statement runs from a day through a day no earlier, not % to %',
            p_from, p_through
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_start := p_from::timestamp AT TIME ZONE 'UTC';
    v_end := (p_through + 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH schedule_e (n, label) AS (
        VALUES (3, 'Rents received'), (5, 'Advertising'), (6, 'Auto and travel'),
               (7, 'Cleaning and maintenance'), (8, 'Commissions'), (9, 'Insurance'),
               (10, 'Legal and other professional fees'), (11, 'Management fees'),
               (12, 'Mortgage interest paid to banks, etc.'), (13, 'Other interest'),
               (14, 'Repairs'), (15, 'Supplies'), (16, 'Taxes'), (17, 'Utilities'),
               (18, 'Depreciation expense or depletion'), (19, 'Other')
    ),
    incomes (part, label) AS (
        VALUES (1, 'income: rent'), (2, 'income: recurring charges'), (3, 'income: late fees'),
               (4, 'income: NSF fees'), (5, 'income: other charges')
    ),
    accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS name, t.property_id
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- What each charge is: rent, a recurring charge (pet rent, parking), a late fee, an NSF
    -- fee, or another charge, and the company's own type of it if it has one (its own, or its
    -- recurring charge's), as the cash statement has it. A lease end's adjustment of a rent or
    -- recurring charge (migration 0051), and a waiver (migration 0079), are what they change. A
    -- credit given on its own comes off rent, as credits come off a lease's rent first
    -- (migration 0093).
    classes AS (
        SELECT c.id,
               coalesce(o.charge_type_id, c.charge_type_id, rc.charge_type_id) AS charge_type,
               CASE
                   WHEN coalesce(o.kind, c.kind) = 'rent' THEN 1
                   WHEN coalesce(o.recurring_charge_id, c.recurring_charge_id) IS NOT NULL THEN 2
                   WHEN lf.fee_charge_id IS NOT NULL THEN 3
                   WHEN nf.fee_charge_id IS NOT NULL THEN 4
                   WHEN coalesce(o.kind, c.kind) = 'credit' THEN 1
                   ELSE 5
               END AS part
        FROM trust_charges AS c
        LEFT JOIN trust_lease_end_adjustments AS adj ON adj.adjustment_charge_id = c.id
        LEFT JOIN trust_charge_waivers AS w ON w.credit_charge_id = c.id
        LEFT JOIN trust_charges AS o ON o.id = coalesce(adj.charge_id, w.waived_charge_id)
        LEFT JOIN trust_late_fees AS lf ON lf.fee_charge_id = coalesce(o.id, c.id)
        LEFT JOIN trust_nsf_fees AS nf ON nf.fee_charge_id = coalesce(o.id, c.id)
        LEFT JOIN trust_recurring_charges AS rc
            ON rc.id = coalesce(o.recurring_charge_id, c.recurring_charge_id)
        WHERE c.pmc_id = p_pmc_id
    ),
    -- Each account's share of its property on a day: the account's percent of the ownership in
    -- force (migration 0090), all of it when the property has none.
    -- Charged in the days (accrual basis): each charge due in them of a lease at the account's
    -- property (a credit as less), and the housing authority's part of the rent and its
    -- adjustments (migration 0095) as rent, each at the account's share on its due day, to the
    -- cent.
    owed AS (
        SELECT l.id AS lease, u.property_id, c.id AS charge, NULL::integer AS part, c.due_on,
               CASE c.kind WHEN 'credit' THEN -c.amount ELSE c.amount END AS amount
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE c.pmc_id = p_pmc_id AND c.due_on BETWEEN p_from AND p_through
        UNION ALL
        -- An authority's late fee (migration 0116) is a late fee.
        SELECT l.id, u.property_id, NULL, CASE ac.source WHEN 'late fee' THEN 3 ELSE 1 END,
               ac.due_on, CASE ac.kind WHEN 'credit' THEN -ac.amount ELSE ac.amount END
        FROM trust_assistance_charges AS ac
        JOIN trust_leases AS l ON l.id = ac.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE ac.pmc_id = p_pmc_id AND ac.due_on BETWEEN p_from AND p_through
    ),
    charged AS (
        SELECT a.account, coalesce(x.part, k.part) AS part, k.charge_type,
               round(x.amount * CASE
                   WHEN EXISTS (SELECT 1 FROM trust_ownership_shares AS sh
                                WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on)
                   THEN coalesce((
                       SELECT sl.percent FROM trust_ownership_share_lines AS sl
                       WHERE sl.ledger_account_id = a.account AND sl.share_id = (
                           SELECT sh.id FROM trust_ownership_shares AS sh
                           WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on
                           ORDER BY sh.starts_on DESC, sh.created_at DESC, sh.id DESC LIMIT 1
                       )
                   ), 0) / 100
                   ELSE 1
               END, 2) AS amount
        FROM owed AS x
        JOIN accounts AS a ON a.property_id = x.property_id
        LEFT JOIN classes AS k ON k.id = x.charge
    ),
    -- The owner's bills dated in the days, but those the owner declined by their end
    -- (migration 0041): one is never paid.
    entered AS (
        SELECT b.id, b.ledger_account_id AS account, b.amount
        FROM trust_bills AS b
        JOIN accounts AS a ON a.account = b.ledger_account_id
        WHERE b.pmc_id = p_pmc_id AND b.bill_date BETWEEN p_from AND p_through
          AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d
                          WHERE d.bill_id = b.id AND d.declined_on <= p_through)
    ),
    fees AS (
        SELECT f.ledger_account_id AS account, 0 AS ord, tr.amount
        FROM trust_management_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT f.ledger_account_id, 1, tr.amount
        FROM trust_leasing_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A fee staff took from the owner (migration 0082): a lease-up fee with the leasing
        -- fees, an inspection or other fee with the management fees.
        SELECT f.ledger_account_id, CASE f.kind WHEN 'lease_up' THEN 1 ELSE 0 END, tr.amount
        FROM trust_owner_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A bill's markup (migration 0083), with the management fees, with its bill.
        SELECT b.account, 0, m.amount
        FROM trust_bill_markups AS m
        JOIN entered AS b ON b.id = m.bill_id
    ),
    -- Each bill's expense account: its latest row.
    categorized AS (
        SELECT DISTINCT ON (x.bill_id) x.id, x.bill_id, x.expense_account_id
        FROM trust_bill_expense_accounts AS x
        WHERE x.pmc_id = p_pmc_id
        ORDER BY x.bill_id, x.id DESC
    ),
    -- Bills dated in the days (accrual basis): a split bill (migration 0069) by its lines,
    -- each in its expense account, which come to the bill.
    spent AS (
        SELECT b.account,
               coalesce(sp.expense_account_id, cz.expense_account_id) AS expense,
               coalesce(sp.amount, b.amount) AS amount
        FROM entered AS b
        LEFT JOIN categorized AS cz ON cz.bill_id = b.id
        LEFT JOIN trust_bill_splits AS sp ON sp.categorized_id = cz.id
    ),
    -- Each account's lines: (group, order within it, item, Schedule E line, amount). Groups:
    -- 1 income, 3 expenses. Income and the fees show even when nothing moved; each of the
    -- company's charge types has a line of its own after them once money was collected on it,
    -- and isn't counted again in the line it would be in without one.
    items AS (
        SELECT a.account, 1 AS grp, i.part AS ord, i.label AS item, 3 AS sched,
               coalesce((SELECT sum(c.amount) FROM charged AS c
                         WHERE c.account = a.account AND c.part = i.part
                           AND c.charge_type IS NULL), 0) AS total
        FROM accounts AS a
        CROSS JOIN incomes AS i
        UNION ALL
        SELECT c.account, 1, 6, 'income: ' || ct.name, 3, sum(c.amount)
        FROM charged AS c
        JOIN accounts AS a ON a.account = c.account
        JOIN trust_charge_types AS ct ON ct.id = c.charge_type
        GROUP BY c.account, ct.id, ct.name
        UNION ALL
        SELECT a.account, 3, f.ord,
               CASE f.ord WHEN 0 THEN 'expense: management fees' ELSE 'expense: leasing fees' END,
               CASE f.ord WHEN 0 THEN 11 ELSE 8 END,
               coalesce((SELECT sum(x.amount) FROM fees AS x
                         WHERE x.account = a.account AND x.ord = f.ord), 0)
        FROM accounts AS a
        CROSS JOIN (VALUES (0), (1)) AS f (ord)
        UNION ALL
        SELECT s.account, 3, coalesce(ea.schedule_e_line, 99),
               CASE WHEN ea.id IS NULL THEN 'expense: bills not in an expense account'
                    ELSE 'expense: ' || ea.name END,
               ea.schedule_e_line,
               sum(s.amount)
        FROM spent AS s
        JOIN accounts AS a ON a.account = s.account
        LEFT JOIN trust_expense_accounts AS ea ON ea.id = s.expense
        GROUP BY s.account, ea.id, ea.name, ea.schedule_e_line
    ),
    sums AS (
        SELECT i.account,
               coalesce(sum(i.total) FILTER (WHERE i.grp = 1), 0) AS income,
               coalesce(sum(i.total) FILTER (WHERE i.grp = 3), 0) AS expenses
        FROM items AS i
        GROUP BY i.account
    ),
    -- The same by Schedule E line (group 6): rents received, and each expense line with money
    -- on it; bills in no expense account are on no line until they are given one.
    schedule AS (
        SELECT i.account, 6 AS grp, i.sched AS ord,
               'Schedule E line ' || i.sched || ': ' || e.label AS item, i.sched,
               sum(i.total) AS total
        FROM items AS i
        JOIN schedule_e AS e ON e.n = i.sched
        GROUP BY i.account, i.sched, e.label
        HAVING i.sched = 3 OR sum(i.total) <> 0
        UNION ALL
        SELECT i.account, 6, 99, 'Schedule E: on no line, bills not in an expense account',
               NULL::integer, sum(i.total)
        FROM items AS i
        WHERE i.grp = 3 AND i.sched IS NULL
        GROUP BY i.account
    ),
    rows AS (
        SELECT i.account, i.grp, i.ord, i.item, i.sched, i.total FROM items AS i
        UNION ALL
        SELECT s.account, 2, 0, 'total income', NULL::integer, s.income FROM sums AS s
        UNION ALL
        SELECT s.account, 4, 0, 'total expenses', NULL::integer, s.expenses FROM sums AS s
        UNION ALL
        SELECT s.account, 5, 0, 'net income', NULL::integer, s.income - s.expenses
        FROM sums AS s
        UNION ALL
        SELECT h.account, h.grp, h.ord, h.item, h.sched, h.total FROM schedule AS h
    ),
    lines AS (
        -- (section, property, account, group, order, item) orders the statement; names sort
        -- by byte (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS name, NULL::text AS account, 0 AS grp, 1 AS ord,
               'PMC' AS item, pmc.display_name AS label, NULL::integer AS sched,
               NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, 2, 'owner', o.display_name, NULL, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, 3, 'period',
               p_from::text || ' to ' || p_through::text || ' UTC, accrual basis', NULL, NULL
        UNION ALL
        SELECT 1, a.name, r.account, r.grp, r.ord, r.item, a.name, r.sched, r.total
        FROM rows AS r
        JOIN accounts AS a ON a.account = r.account
        UNION ALL
        SELECT 2, NULL, NULL, r.grp, r.ord, 'all properties: ' || r.item, NULL, r.sched,
               sum(r.total)
        FROM rows AS r
        GROUP BY r.grp, r.ord, r.item, r.sched
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.account COLLATE "C", l.grp, l.ord,
                        l.item COLLATE "C"
           )::integer,
           -- The PMC's, owner's and period's names sit in the property column of the first
           -- lines.
           l.label,
           l.item,
           l.sched,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$function$;

CREATE OR REPLACE FUNCTION public.trust_report_owner_balance_sheet(p_pmc_id uuid, p_owner_id uuid, p_as_of date)
 RETURNS TABLE(line integer, property text, item text, amount numeric)
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'public', 'pg_temp'
AS $function$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz;
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: an owner''s balance sheet is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_cutoff := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS name, t.property_id
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- What each account holds in trust at the day's end.
    cash AS (
        SELECT a.account,
               coalesce((SELECT sum(e.amount)
                         FROM pgledger_entries AS e
                         JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                         WHERE e.account_id = a.account AND tr.event_at < v_cutoff), 0) AS amount
        FROM accounts AS a
    ),
    -- Owed at the day's end of what was due by it: by tenants, what is still owed of each charge
    -- as the delinquency report counts it (written out as it is there, since the AI role calls
    -- no function but the reports), and by housing authorities, what is open of their part of
    -- the rent and its adjustments (migration 0095).
    paid AS (
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
        -- Each charge's place, as trust_pay_rank gives it (written out here: the AI role runs
        -- this report and calls no function but the reports).
        SELECT c.id, c.lease_id, c.due_on, c.kind, c.created_at,
               CASE
                   WHEN c.kind = 'rent' THEN 0
                   WHEN c.kind = 'fee' AND (
                       c.recurring_charge_id IS NOT NULL
                       OR EXISTS (
                           SELECT 1 FROM trust_lease_end_adjustments AS x
                           JOIN trust_charges AS o ON o.id = x.charge_id
                           WHERE x.adjustment_charge_id = c.id
                             AND (o.kind = 'rent' OR o.recurring_charge_id IS NOT NULL)
                       )
                   ) THEN 1
                   ELSE 2
               END AS pay_rank,
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
        -- Each charge due by the day with what is left of it, rent first (migration 0093).
        SELECT s.lease_id, s.due_on, s.left_owing,
               sum(s.left_owing) OVER (
                   PARTITION BY s.lease_id
                   ORDER BY s.pay_rank = 2, s.due_on, s.pay_rank, s.created_at, s.id
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
        SELECT o.lease_id, o.due_on,
               o.left_owing - least(
                   o.left_owing,
                   greatest(coalesce(f.amount, 0) - coalesce(o.owing_before, 0), 0)
               ) AS owed
        FROM open_charges AS o
        LEFT JOIN offsets AS f ON f.lease_id = o.lease_id
    ),

    owed AS (
        SELECT u.property_id, g.due_on, g.owed AS amount, 1 AS ord
        FROM aged AS g
        JOIN trust_leases AS l ON l.id = g.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE u.property_id IN (SELECT a.property_id FROM accounts AS a)
        UNION ALL
        SELECT u.property_id, ac.due_on,
               ac.amount
               + coalesce((SELECT sum(CASE x.kind WHEN 'credit' THEN -x.amount ELSE x.amount END)
                           FROM trust_assistance_charges AS x
                           WHERE x.adjusts_id = ac.id AND x.due_on <= p_as_of), 0)
               - coalesce((SELECT sum(p.amount)
                           FROM trust_assistance_payments AS p
                           JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
                           WHERE p.assistance_charge_id = ac.id AND tr.event_at < v_cutoff), 0)
               -- What of it bounced back is owed again (migration 0116).
               + coalesce((SELECT sum(r.amount)
                           FROM trust_assistance_payment_reversals AS r
                           JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
                           WHERE r.assistance_charge_id = ac.id AND tr.event_at < v_cutoff), 0),
               2
        FROM trust_assistance_charges AS ac
        JOIN trust_leases AS l ON l.id = ac.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE ac.pmc_id = p_pmc_id AND ac.kind = 'charge' AND ac.adjusts_id IS NULL
          AND ac.due_on <= p_as_of
          AND u.property_id IN (SELECT a.property_id FROM accounts AS a)
    ),
    -- Each account's part of what is owed: its share of its property on the due day
    -- (migration 0090; all of it when the property has no shares), to the cent.
    receivable AS (
        SELECT a.account, x.ord,
               round(x.amount * CASE
                   WHEN EXISTS (SELECT 1 FROM trust_ownership_shares AS sh
                                WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on)
                   THEN coalesce((
                       SELECT sl.percent FROM trust_ownership_share_lines AS sl
                       WHERE sl.ledger_account_id = a.account AND sl.share_id = (
                           SELECT sh.id FROM trust_ownership_shares AS sh
                           WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on
                           ORDER BY sh.starts_on DESC, sh.created_at DESC, sh.id DESC LIMIT 1
                       )
                   ), 0) / 100
                   ELSE 1
               END, 2) AS amount
        FROM owed AS x
        JOIN accounts AS a ON a.property_id = x.property_id
        WHERE x.amount > 0
    ),
    -- The account's bills dated by the day and not yet set aside by its end, with any markup
    -- added to them (migration 0083), but those the owner declined by then (migration 0041).
    payable AS (
        SELECT b.ledger_account_id AS account,
               b.amount + coalesce((SELECT m.amount FROM trust_bill_markups AS m
                                    WHERE m.bill_id = b.id), 0) AS amount
        FROM trust_bills AS b
        JOIN accounts AS a ON a.account = b.ledger_account_id
        WHERE b.pmc_id = p_pmc_id AND b.bill_date <= p_as_of
          AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d
                          WHERE d.bill_id = b.id AND d.declined_on <= p_as_of)
          AND NOT EXISTS (SELECT 1 FROM trust_bill_payments AS bp
                          JOIN pgledger_transfers AS tr ON tr.id = bp.transfer_id
                          WHERE bp.bill_id = b.id AND bp.step = 'set_aside'
                            AND tr.event_at < v_cutoff)
    ),
    -- Each account's lines, in order: what it holds, what is owed to it, what it owes, and
    -- what is the owner's.
    items AS (
        SELECT c.account, 1 AS ord, 'cash held in trust' AS item, c.amount AS total FROM cash AS c
        UNION ALL
        SELECT a.account, 1 + k.ord, k.item,
               coalesce((SELECT sum(r.amount) FROM receivable AS r
                         WHERE r.account = a.account AND r.ord = k.ord), 0)
        FROM accounts AS a
        CROSS JOIN (VALUES (1, 'owed by tenants'), (2, 'owed by housing authorities'))
            AS k (ord, item)
        UNION ALL
        SELECT a.account, 4, 'bills not yet set aside',
               -coalesce((SELECT sum(p.amount) FROM payable AS p WHERE p.account = a.account), 0)
        FROM accounts AS a
    ),
    rows AS (
        SELECT i.account, i.ord, i.item, i.total FROM items AS i
        UNION ALL
        SELECT i.account, 5, 'owner''s equity', sum(i.total) FROM items AS i GROUP BY i.account
    ),
    lines AS (
        -- (section, property, account, order) orders the sheet; names sort by byte
        -- (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS name, NULL::text AS account, 1 AS ord, 'PMC' AS item,
               pmc.display_name AS label, NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 2, 'owner', o.display_name, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 3, 'as of',
               p_as_of::text || ', end of day UTC, accrual basis', NULL
        UNION ALL
        SELECT 1, a.name, r.account, r.ord, r.item, a.name, r.total
        FROM rows AS r
        JOIN accounts AS a ON a.account = r.account
        UNION ALL
        SELECT 2, NULL, NULL, r.ord, 'all properties: ' || r.item, NULL, sum(r.total)
        FROM rows AS r
        GROUP BY r.ord, r.item
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.account COLLATE "C", l.ord
           )::integer,
           -- The PMC's, owner's and day's names sit in the property column of the first lines.
           l.label,
           l.item,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$function$;

CREATE OR REPLACE FUNCTION public.trust_report_owner_year_end(p_pmc_id uuid, p_owner_id uuid, p_year integer)
 RETURNS TABLE(line integer, property text, item text, amount numeric)
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'public', 'pg_temp'
AS $function$
#variable_conflict use_column
DECLARE
    v_start timestamptz;
    v_end timestamptz;
BEGIN
    IF p_year IS NULL OR p_year NOT BETWEEN 1900 AND 9998 THEN
        RAISE EXCEPTION 'trust: a year-end package is for a calendar year, not %', p_year
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_start := make_date(p_year, 1, 1)::timestamp AT TIME ZONE 'UTC';
    v_end := make_date(p_year + 1, 1, 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH labels AS (
        SELECT v.part, v.label
        FROM (VALUES
            (0, 'opening balance'),
            (1, 'collected from tenants'),
            (2, 'management fees'),
            (3, 'leasing fees'),
            (4, 'bills paid'),
            (5, 'owner draws'),
            (6, 'owner contributions'),
            (7, 'other'),
            (9, 'closing balance')
        ) AS v (part, label)
    ),
    accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS name
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- What moved each account in the year, by part, signed as it moved the account.
    moved AS (
        -- 1: collected from tenants: payments matched to the leases' charges...
        SELECT tr.to_account_id AS account, 1 AS part, cp.amount AS change
        FROM trust_charge_payments AS cp
        JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
        WHERE cp.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- ...less what of them bounced back...
        SELECT tr.from_account_id, 1, -r.amount
        FROM trust_payment_reversals AS r
        JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
        WHERE r.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- ...and a housing authority's part of the rent, as it paid it (migration 0095).
        SELECT tr.to_account_id, 1, ap.amount
        FROM trust_assistance_payments AS ap
        JOIN pgledger_transfers AS tr ON tr.id = ap.transfer_id
        WHERE ap.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- ...less what of it bounced back (migration 0116).
        SELECT tr.from_account_id, 1, -ar.amount
        FROM trust_assistance_payment_reversals AS ar
        JOIN pgledger_transfers AS tr ON tr.id = ar.reversal_id
        WHERE ar.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT f.ledger_account_id, 2, -tr.amount
        FROM trust_management_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT f.ledger_account_id, 3, -tr.amount
        FROM trust_leasing_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A fee staff took from the owner (migration 0082): a lease-up fee with the leasing
        -- fees, an inspection or other fee with the management fees.
        SELECT f.ledger_account_id, CASE f.kind WHEN 'lease_up' THEN 3 ELSE 2 END, -tr.amount
        FROM trust_owner_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A bill's markup (migration 0083), with the management fees.
        SELECT tr.from_account_id, 2, -tr.amount
        FROM trust_bill_markup_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- 4: bills, when their money left the owner's account (set aside to pay them).
        SELECT tr.from_account_id, 4, -tr.amount
        FROM trust_bill_payments AS b
        JOIN pgledger_transfers AS tr ON tr.id = b.transfer_id
        WHERE b.pmc_id = p_pmc_id AND b.step = 'set_aside'
          AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT d.ledger_account_id, 5, -tr.amount
        FROM trust_owner_draws AS d
        JOIN pgledger_transfers AS tr ON tr.id = d.transfer_id
        WHERE d.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT c.ledger_account_id, 6, tr.amount
        FROM trust_owner_contributions AS c
        JOIN pgledger_transfers AS tr ON tr.id = c.transfer_id
        WHERE c.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    balances AS (
        SELECT a.account, a.name,
               coalesce(sum(e.amount) FILTER (WHERE tr.event_at < v_start), 0) AS opening,
               coalesce(sum(e.amount) FILTER (WHERE tr.event_at < v_end), 0) AS closing
        FROM accounts AS a
        LEFT JOIN pgledger_entries AS e ON e.account_id = a.account
        LEFT JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
        GROUP BY a.account, a.name
    ),
    parts AS (
        SELECT b.account, b.name, p.part, p.label,
               CASE p.part
                   WHEN 0 THEN b.opening
                   WHEN 9 THEN b.closing
                   WHEN 7 THEN b.closing - b.opening - coalesce(
                       (SELECT sum(m.change) FROM moved AS m WHERE m.account = b.account), 0)
                   ELSE coalesce(
                       (SELECT sum(m.change) FROM moved AS m
                        WHERE m.account = b.account AND m.part = p.part), 0)
               END AS total
        FROM balances AS b
        CROSS JOIN labels AS p
    ),
    lines AS (
        -- (section, property, account, part) orders the package; names sort by byte
        -- (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS name, NULL::text AS account, 1 AS part,
               'PMC' AS item, pmc.display_name AS label, NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 2, 'owner', o.display_name, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 3, 'year',
               p_year::text || ', January 1 to December 31 UTC', NULL
        UNION ALL
        SELECT 1, p.name, p.account, p.part, p.label, p.name, p.total
        FROM parts AS p
        UNION ALL
        SELECT 2, NULL, NULL, x.part, 'all properties: ' || x.label, NULL,
               coalesce((SELECT sum(p.total) FROM parts AS p WHERE p.part = x.part), 0)
        FROM labels AS x
        UNION ALL
        SELECT 3, NULL, NULL, 0, '1099-MISC rents (box 1)', NULL,
               coalesce((SELECT sum(p.total) FROM parts AS p WHERE p.part = 1), 0)
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.account COLLATE "C", l.part
           )::integer,
           -- The PMC's, owner's and year's names sit in the property column of the first lines.
           l.label,
           l.item,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$function$;


-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'authority_payments cannot be rolled back: late fees and returned payments '
        'are records';
END;
$$;
