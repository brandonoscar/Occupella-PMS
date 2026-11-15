-- migrate:up
-- A payment dated within a rent's grace that reaches the books after the night charged its late
-- fee takes the fee off, by itself (docs/edge-cases.md D-078, D-079).
--
-- A check received on the last day of grace and recorded the next morning dated back, or a
-- lockbox file's line dated within grace brought in after the night: the rent was paid in time,
-- but the late fee the night charged stayed until staff waived it by hand. Now, as each payment
-- is matched to a rent that has its late fee (trust_charge_payments, from every way a payment
-- comes: Receive payment, a payment file, online, autopay, unapplied cash), a payment that counts
-- by the end of its grace works the fee out again as the night would have with it in the books
-- (trust_open_charges at the end of the grace, the fee's own terms), and the part of the fee that
-- would not have been charged is waived: a credit of the company's day linked to the fee, with
-- why, as staff's waiver is (migration 0079). Nothing is deleted. Never more than is left of the
-- fee once its payments and earlier waivers count; a fee already paid keeps what was paid.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE FUNCTION trust_waive_late_fee_paid_in_grace() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_late trust_late_fees;
    v_rent trust_charges;
    v_fee trust_charges;
    v_terms trust_late_fee_policies;
    v_transfer pgledger_transfers;
    v_ends date;
    v_cutoff timestamptz;
    v_unpaid numeric;
    v_owed numeric;
    v_paid numeric;
    v_waived numeric;
    v_waive numeric;
    v_credit uuid;
BEGIN
    SELECT f.* INTO v_late FROM trust_late_fees AS f WHERE f.rent_charge_id = NEW.charge_id;
    IF NOT FOUND THEN
        RETURN NULL;  -- not a rent with a late fee: nearly every payment
    END IF;
    SELECT c.* INTO v_rent FROM trust_charges AS c WHERE c.id = v_late.rent_charge_id;
    SELECT t.* INTO v_terms FROM trust_late_fee_policies AS t WHERE t.id = v_late.policy_id;
    v_ends := trust_late_fee_grace_ends(v_rent.id, v_rent.due_on, v_terms.grace_days);
    v_cutoff := (v_ends + 1)::timestamp AT TIME ZONE 'UTC';
    SELECT t.* INTO v_transfer FROM pgledger_transfers AS t WHERE t.id = NEW.transfer_id;
    -- Only a payment that counts by the end of the grace: dated by then, or online and started
    -- by then (migration 20261113000100).
    IF NOT (v_transfer.event_at < v_cutoff OR trust_online_started_by(v_transfer, v_ends)) THEN
        RETURN NULL;
    END IF;

    -- What was open of the rent at the end of its grace, counting this payment, as
    -- trust_assess_late_fees counts it...
    SELECT o.open INTO v_unpaid
    FROM trust_open_charges(v_rent.pmc_id, v_rent.lease_id, v_cutoff) AS o
    WHERE o.charge_id = v_rent.id;
    v_unpaid := v_unpaid - coalesce((
        SELECT sum(cp.amount)
        FROM trust_charge_payments AS cp
        JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
        WHERE cp.charge_id = v_rent.id AND t.event_at >= v_cutoff
          AND trust_online_started_by(t, v_ends)
    ), 0);
    -- ...and the fee it would have been charged then.
    v_owed := CASE
        WHEN v_unpaid <= 0 THEN 0
        ELSE greatest(
            least(round(v_terms.flat_fee + v_terms.percent / 100 * v_unpaid, 2), v_terms.maximum),
            0
        )
    END;

    -- The fee, locked as a waiver locks it, with what was paid and waived of it.
    SELECT c.* INTO v_fee FROM trust_charges AS c WHERE c.id = v_late.fee_charge_id
    FOR NO KEY UPDATE;
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.charge_id = v_fee.id;
    SELECT v_paid - coalesce(sum(r.amount), 0) INTO v_paid
    FROM trust_payment_reversals AS r WHERE r.charge_id = v_fee.id;
    SELECT coalesce(sum(k.amount), 0) INTO v_waived
    FROM trust_charge_waivers AS w JOIN trust_charges AS k ON k.id = w.credit_charge_id
    WHERE w.waived_charge_id = v_fee.id;
    v_waive := least(v_fee.amount - v_owed - v_waived, v_fee.amount - v_paid - v_waived);
    IF v_waive <= 0 THEN
        RETURN NULL;
    END IF;

    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    VALUES (
        v_fee.pmc_id, v_fee.lease_id, trust_pmc_day(v_fee.pmc_id), 'credit', v_waive,
        'Waived: ' || coalesce(v_fee.memo, 'Late fee') || ', due '
            || to_char(v_fee.due_on, 'MM/DD/YYYY')
    )
    RETURNING id INTO v_credit;
    INSERT INTO trust_charge_waivers (credit_charge_id, pmc_id, waived_charge_id, reason)
    VALUES (
        v_credit, v_fee.pmc_id, v_fee.id,
        'Paid within its grace: a payment dated ' || to_char(v_transfer.event_at AT TIME ZONE 'UTC',
        'MM/DD/YYYY') || ' came into the books after the late fee was charged'
    );
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION trust_waive_late_fee_paid_in_grace() FROM PUBLIC;

CREATE TRIGGER trust_waive_late_fee_paid_in_grace
AFTER INSERT ON trust_charge_payments
FOR EACH ROW EXECUTE FUNCTION trust_waive_late_fee_paid_in_grace();

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DROP TRIGGER trust_waive_late_fee_paid_in_grace ON trust_charge_payments;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_waive_late_fee_paid_in_grace();
