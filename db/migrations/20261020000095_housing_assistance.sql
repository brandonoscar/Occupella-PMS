-- migrate:up
-- A housing authority's part of a lease's rent (the founder's review, q034: "Can a housing
-- authority (Section 8) payment be recorded apart from the tenant's share?"; docs/law.md).
--
--   trust_housing_authorities    a PMC's housing authorities, each name once whatever its case,
--                                naming who added it.
--   trust_rent_assistance        a lease's housing assistance from the first of a month: the
--                                authority and what it pays of each month's rent (0 ends it),
--                                maybe its contract's number. Append-only, the latest from a month
--                                or before holding, naming who recorded it. Like a rent due day it
--                                starts in a month of the lease after the last month whose rent is
--                                charged, and it pays no more than the rent then.
--   trust_assistance_charges     what an authority owes for a lease: each month's part of the rent
--                                (split from it as it is charged), a lease end's adjustment of it,
--                                and what staff charge it or credit it by hand.
--   trust_assistance_payments    an authority's payment matched to its charges
--                                (trust_apply_assistance_payment): never past what is open of one,
--                                nor past the payment.
--
-- As rent is charged for a month the lease's assistance is in force (the rent run, a renewal), the
-- authority's part of it, its share of the month's rent to the cent and prorated as the rent is,
-- becomes the authority's charge, and the tenant is charged the rest (nothing when the authority
-- pays all of it). So everything of the tenant's (their charges, payments, late fees, the deposit
-- kept at move-out, their balance and the delinquency report) is the tenant's part alone: the
-- family is not responsible for the authority's payment (24 CFR 982.310(b)), and no tenant money
-- pays it. A lease's end moves both parts to the days it is in effect (trust_end_lease), and a
-- renewal keeps the assistance in force on its first day (trust_complete_lease_offer). An
-- authority's payment goes to the property's owners like a tenant's, and counts as rent collected
-- in the management fee, the income statement and the year-end package. No money moves here.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_housing_authorities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    name text NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 200),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE UNIQUE INDEX trust_housing_authorities_name
ON trust_housing_authorities (pmc_id, lower(name));
CREATE INDEX trust_housing_authorities_recorded_by
ON trust_housing_authorities (pmc_id, recorded_by);

CREATE TABLE trust_rent_assistance (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    housing_authority_id uuid NOT NULL,
    starts_on date NOT NULL CHECK (extract(day FROM starts_on) = 1),
    -- What the authority pays of a month's rent; 0 ends the lease's assistance.
    amount numeric NOT NULL CHECK (amount >= 0 AND amount = round(amount, 2)),
    contract text CHECK (btrim(contract) <> '' AND length(contract) <= 100),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (lease_id, starts_on),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, housing_authority_id) REFERENCES trust_housing_authorities (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_rent_assistance_pmc ON trust_rent_assistance (pmc_id, lease_id);
CREATE INDEX trust_rent_assistance_authority
ON trust_rent_assistance (pmc_id, housing_authority_id);
CREATE INDEX trust_rent_assistance_recorded_by ON trust_rent_assistance (pmc_id, recorded_by);

CREATE TABLE trust_assistance_charges (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    housing_authority_id uuid NOT NULL,
    due_on date NOT NULL,
    kind text NOT NULL CHECK (kind IN ('charge', 'credit')),
    amount numeric NOT NULL CHECK (amount > 0 AND amount = round(amount, 2)),
    memo text NOT NULL CHECK (btrim(memo) <> '' AND length(memo) <= 200),
    -- 'rent': a month's part of the rent, split from it as it was charged; 'lease end': a lease
    -- end's adjustment of one; 'staff': a charge or credit staff entered.
    source text NOT NULL CHECK (source IN ('rent', 'lease end', 'staff')),
    -- The month's rent and assistance a part of the rent was worked out from.
    monthly_rent numeric CHECK (monthly_rent > 0),
    monthly_assistance numeric CHECK (monthly_assistance > 0),
    -- The charge an adjustment or a credit changes.
    adjusts_id uuid,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    CHECK ((source = 'rent') = (monthly_rent IS NOT NULL AND monthly_assistance IS NOT NULL)),
    CHECK (source <> 'rent' OR (kind = 'charge' AND adjusts_id IS NULL)),
    CHECK (source <> 'lease end' OR adjusts_id IS NOT NULL),
    CHECK (kind = 'charge' OR adjusts_id IS NOT NULL),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, housing_authority_id) REFERENCES trust_housing_authorities (pmc_id, id),
    FOREIGN KEY (pmc_id, adjusts_id) REFERENCES trust_assistance_charges (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
-- A month's part of the rent once per lease and month, whatever its due day.
CREATE UNIQUE INDEX trust_assistance_charges_one_rent_per_month
ON trust_assistance_charges (lease_id, (date_trunc('month', due_on::timestamp)))
WHERE source = 'rent';
CREATE INDEX trust_assistance_charges_pmc ON trust_assistance_charges (pmc_id, lease_id, due_on);
CREATE INDEX trust_assistance_charges_authority
ON trust_assistance_charges (pmc_id, housing_authority_id);
CREATE INDEX trust_assistance_charges_adjusts ON trust_assistance_charges (pmc_id, adjusts_id);
CREATE INDEX trust_assistance_charges_recorded_by
ON trust_assistance_charges (pmc_id, recorded_by);

CREATE TABLE trust_assistance_payments (
    transfer_id text NOT NULL REFERENCES pgledger_transfers (id),
    assistance_charge_id uuid NOT NULL,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    amount numeric NOT NULL CHECK (amount > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (transfer_id, assistance_charge_id),
    FOREIGN KEY (pmc_id, assistance_charge_id) REFERENCES trust_assistance_charges (pmc_id, id)
);
CREATE INDEX trust_assistance_payments_charge
ON trust_assistance_payments (pmc_id, assistance_charge_id);

-- --- the assistance in force ------------------------------------------------------------------

-- A lease's assistance on a day: the latest from a month on or before it, else none.
CREATE FUNCTION trust_assistance_on(p_lease_id uuid, p_day date)
RETURNS SETOF trust_rent_assistance
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT a.* FROM trust_rent_assistance AS a
    WHERE a.lease_id = p_lease_id AND a.starts_on <= p_day
    ORDER BY a.starts_on DESC, a.created_at DESC, a.id DESC
    LIMIT 1
$$;
REVOKE ALL ON FUNCTION trust_assistance_on(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_assistance_on(uuid, date) TO trust_app;

-- The authority's part of an amount of rent: its share of the month's rent, to the cent, never
-- more than the amount.
CREATE FUNCTION trust_assistance_part(p_amount numeric, p_monthly_rent numeric, p_assistance numeric)
RETURNS numeric
LANGUAGE sql IMMUTABLE
SET search_path = public, pg_temp
AS $$
    SELECT CASE WHEN p_amount <= 0 THEN 0::numeric
                ELSE least(p_amount, round(p_amount * p_assistance / p_monthly_rent, 2)) END
$$;
REVOKE ALL ON FUNCTION trust_assistance_part(numeric, numeric, numeric) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_assistance_part(numeric, numeric, numeric) TO trust_app;

-- What is open of an authority's charge: it, with what adjusted or credited it, less what the
-- authority paid of it. Below 0 when the authority paid more than it now owes.
CREATE FUNCTION trust_assistance_open(p_charge_id uuid) RETURNS numeric
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT c.amount
           + coalesce((SELECT sum(CASE a.kind WHEN 'credit' THEN -a.amount ELSE a.amount END)
                       FROM trust_assistance_charges AS a WHERE a.adjusts_id = c.id), 0)
           - coalesce((SELECT sum(p.amount) FROM trust_assistance_payments AS p
                       WHERE p.assistance_charge_id = c.id), 0)
    FROM trust_assistance_charges AS c
    WHERE c.id = p_charge_id
$$;
REVOKE ALL ON FUNCTION trust_assistance_open(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_assistance_open(uuid) TO trust_app;

-- Assistance starts in a month of the lease, after the last month whose rent is charged (the
-- tenant's part or the authority's), and pays no more than the rent then. Rent runs and new
-- assistance of the PMC wait for each other, as runs and due days do.
CREATE FUNCTION trust_check_rent_assistance() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_lease trust_leases;
    v_charged date;
    v_rent numeric;
BEGIN
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.id = NEW.lease_id AND l.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- no such lease of the PMC: the foreign key refuses it
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('lease days of ' || NEW.pmc_id, 0));
    IF NEW.starts_on < date_trunc('month', v_lease.starts_on::timestamp)::date
       OR NEW.starts_on > v_lease.ends_on THEN
        RAISE EXCEPTION 'trust: housing assistance starts in a month of the lease, from % to %',
            v_lease.starts_on, coalesce(v_lease.ends_on::text, 'month to month')
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT max(d.due_on) INTO v_charged
    FROM (
        SELECT c.due_on FROM trust_charges AS c
        WHERE c.lease_id = NEW.lease_id AND c.kind = 'rent' AND c.due_on >= NEW.starts_on
        UNION ALL
        SELECT a.due_on FROM trust_assistance_charges AS a
        WHERE a.lease_id = NEW.lease_id AND a.source = 'rent' AND a.due_on >= NEW.starts_on
    ) AS d;
    IF v_charged IS NOT NULL THEN
        RAISE EXCEPTION 'trust: rent due % is charged already; housing assistance starts after '
            'its month', v_charged
            USING ERRCODE = 'check_violation';
    END IF;
    v_rent := trust_rent_on(NEW.lease_id, greatest(NEW.starts_on, v_lease.starts_on));
    IF NEW.amount > v_rent THEN
        RAISE EXCEPTION 'trust: a housing authority pays no more than the rent, %, not %',
            v_rent, NEW.amount
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_rent_assistance() FROM PUBLIC;

CREATE TRIGGER trust_rent_assistance_in_time BEFORE INSERT ON trust_rent_assistance
FOR EACH ROW EXECUTE FUNCTION trust_check_rent_assistance();

-- --- splitting the rent -----------------------------------------------------------------------

-- A month's rent charged while the lease's assistance is in force: the authority's part becomes
-- its charge (once a month), and the tenant is charged the rest, or nothing when the authority
-- pays it all. Rent is charged only by the rent run and a renewal; neither names an authority.
CREATE FUNCTION trust_split_rent_assistance() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_aid trust_rent_assistance;
    v_rent numeric;
    v_part numeric;
BEGIN
    SELECT a.* INTO v_aid FROM trust_assistance_on(NEW.lease_id, NEW.due_on) AS a;
    IF NOT FOUND OR v_aid.amount = 0 THEN
        RETURN NEW;
    END IF;
    -- The lease's rent that day: always above 0 (its lease and rent changes check it).
    v_rent := trust_rent_on(NEW.lease_id, NEW.due_on);
    v_part := trust_assistance_part(NEW.amount, v_rent, v_aid.amount);
    IF v_part <= 0 THEN
        RETURN NEW;
    END IF;
    INSERT INTO trust_assistance_charges (
        pmc_id, lease_id, housing_authority_id, due_on, kind, amount, memo, source,
        monthly_rent, monthly_assistance
    ) VALUES (
        NEW.pmc_id, NEW.lease_id, v_aid.housing_authority_id, NEW.due_on, 'charge', v_part,
        coalesce(NEW.memo, 'Rent due ' || to_char(NEW.due_on, 'YYYY-MM-DD')), 'rent',
        v_rent, v_aid.amount
    )
    ON CONFLICT (lease_id, (date_trunc('month', due_on::timestamp))) WHERE source = 'rent'
    DO NOTHING;
    NEW.amount := NEW.amount - v_part;
    IF NEW.amount <= 0 THEN
        RETURN NULL;  -- the authority pays all of it
    END IF;
    NEW.memo := coalesce(NEW.memo, 'Rent due ' || to_char(NEW.due_on, 'YYYY-MM-DD'))
                || ', the tenant''s part';
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_split_rent_assistance() FROM PUBLIC;

CREATE TRIGGER trust_rent_assistance_split BEFORE INSERT ON trust_charges
FOR EACH ROW WHEN (NEW.kind = 'rent') EXECUTE FUNCTION trust_split_rent_assistance();

-- An adjustment or credit changes a charge of the authority's for the same lease; staff credit
-- no more than is open of it. Payments to the charge and credits wait for each other: the
-- charge's row lock, which the app may not take, so this runs as the owner.
CREATE FUNCTION trust_check_assistance_charge() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_target trust_assistance_charges;
BEGIN
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
$$;
REVOKE ALL ON FUNCTION trust_check_assistance_charge() FROM PUBLIC;

CREATE TRIGGER trust_assistance_charge_adjusts BEFORE INSERT ON trust_assistance_charges
FOR EACH ROW EXECUTE FUNCTION trust_check_assistance_charge();

-- --- the authority's payments -----------------------------------------------------------------

-- Matches an authority's payment (a transfer from a trust bank account's cash into an owner's
-- account of the lease's property) to one of its charges: never past what is open of the charge
-- nor past the transfer, and once (the same match again returns it). A transfer pays a tenant's
-- charges or an authority's, never both. READ COMMITTED, like a tenant's payment.
CREATE FUNCTION trust_apply_assistance_payment(
    p_pmc_id uuid, p_charge_id uuid, p_transfer_id text, p_amount numeric
) RETURNS numeric
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_charge trust_assistance_charges;
    v_transfer pgledger_transfers;
    v_property_id uuid;
    v_existing numeric;
    v_open numeric;
    v_used numeric;
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
$$;
REVOKE ALL ON FUNCTION trust_apply_assistance_payment(uuid, uuid, text, numeric) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_apply_assistance_payment(uuid, uuid, text, numeric) TO trust_app;

-- A tenant's payment never pays an authority's charge: a transfer matched to one is refused.
CREATE FUNCTION trust_check_tenant_payment_transfer() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM trust_assistance_payments AS p WHERE p.transfer_id = NEW.transfer_id
    ) THEN
        RAISE EXCEPTION 'trust: transfer % is a housing authority''s payment, not a tenant''s',
            NEW.transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_tenant_payment_transfer() FROM PUBLIC;

CREATE TRIGGER trust_tenant_payment_not_assistance BEFORE INSERT ON trust_charge_payments
FOR EACH ROW EXECUTE FUNCTION trust_check_tenant_payment_transfer();

-- --- append-only, audited, naming who ---------------------------------------------------------

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_housing_authorities
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_rent_assistance
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_assistance_charges
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_housing_authorities
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_rent_assistance
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_assistance_charges
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_assistance_payments
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_housing_authorities
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_rent_assistance
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_assistance_charges
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_assistance_payments
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_housing_authorities FROM PUBLIC;
REVOKE ALL ON trust_rent_assistance FROM PUBLIC;
REVOKE ALL ON trust_assistance_charges FROM PUBLIC;
REVOKE ALL ON trust_assistance_payments FROM PUBLIC;
GRANT SELECT, INSERT ON trust_housing_authorities TO trust_app;
GRANT SELECT, INSERT ON trust_rent_assistance TO trust_app;
GRANT SELECT, INSERT ON trust_assistance_charges TO trust_app;
GRANT SELECT ON trust_assistance_payments TO trust_app;
GRANT SELECT ON trust_housing_authorities TO trust_ai_agent;
GRANT SELECT ON trust_rent_assistance TO trust_ai_agent;
GRANT SELECT ON trust_assistance_charges TO trust_ai_agent;
GRANT SELECT ON trust_assistance_payments TO trust_ai_agent;

-- --- what changes elsewhere --------------------------------------------------------------------

-- A rent due day starts after the last month charged, the authority's part of it included.
CREATE OR REPLACE FUNCTION trust_check_rent_due_day() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_lease trust_leases;
    v_charged date;
BEGIN
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.id = NEW.lease_id AND l.pmc_id = NEW.pmc_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- no such lease of the PMC: the foreign key refuses it
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('lease days of ' || NEW.pmc_id, 0));
    IF NEW.starts_on < date_trunc('month', v_lease.starts_on::timestamp)::date
       OR NEW.starts_on > v_lease.ends_on THEN
        RAISE EXCEPTION 'trust: a rent due day starts in a month of the lease, from % to %',
            v_lease.starts_on, coalesce(v_lease.ends_on::text, 'month to month')
            USING ERRCODE = 'check_violation';
    END IF;
    -- A month the authority pays all of the rent has only its charge (migration 0095).
    SELECT max(d.due_on) INTO v_charged
    FROM (
        SELECT c.due_on FROM trust_charges AS c
        WHERE c.lease_id = NEW.lease_id AND c.kind = 'rent' AND c.due_on >= NEW.starts_on
        UNION ALL
        SELECT a.due_on FROM trust_assistance_charges AS a
        WHERE a.lease_id = NEW.lease_id AND a.source = 'rent' AND a.due_on >= NEW.starts_on
    ) AS d;
    IF v_charged IS NOT NULL THEN
        RAISE EXCEPTION 'trust: rent due % is charged already; a new due day starts after its month',
            v_charged
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

-- A lease's end set, moved or cleared after its month was charged: as before (migrations 0051 and
-- 0061), each rent and recurring charge that stands at its month's days under the old end is added
-- to or taken off so it stands at them under the new end. A month the lease's housing authority
-- pays part of (migration 0095) has two parts: the tenant's rent charge stands at the month's days
-- less the authority's part of them, and the authority's charge at its part, each moved to the new
-- end on its own.
CREATE OR REPLACE FUNCTION trust_end_lease(p_pmc_id uuid, p_lease_id uuid, p_ends_on date)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_old trust_leases;
    v_new trust_leases;
    v_charge record;
    v_adjustment uuid;
BEGIN
    -- Wait for a rent run of the PMC under way, so the charges it makes are seen below, and keep
    -- the next one from charging until this commits.
    PERFORM pg_advisory_xact_lock(hashtextextended('lease days of ' || p_pmc_id, 0));
    SELECT l.* INTO v_old FROM trust_leases AS l WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id;
    UPDATE trust_leases SET ends_on = p_ends_on WHERE id = p_lease_id AND pmc_id = p_pmc_id
    RETURNING * INTO v_new;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- The tenant's charges the rent run makes (rent, and a recurring charge's).
    FOR v_charge IN
        SELECT k.id, k.due_on, k.label, k.was, k.now
        FROM (
            SELECT c.id, c.due_on,
                   coalesce(rc.memo, 'Rent') AS label,
                   (CASE WHEN c.kind = 'credit' THEN -c.amount ELSE c.amount END)
                   + coalesce((
                       SELECT sum(CASE WHEN a.kind = 'credit' THEN -a.amount ELSE a.amount END)
                       FROM trust_lease_end_adjustments AS x
                       JOIN trust_charges AS a ON a.id = x.adjustment_charge_id
                       WHERE x.charge_id = c.id
                   ), 0) AS net,
                   (CASE WHEN c.kind = 'credit' THEN -1 ELSE 1 END) * (
                       w.amount - CASE WHEN aid.id IS NULL THEN 0 ELSE
                           trust_assistance_part(w.amount, aid.monthly_rent, aid.monthly_assistance)
                       END
                   ) AS was,
                   (CASE WHEN c.kind = 'credit' THEN -1 ELSE 1 END) * (
                       n.amount - CASE WHEN aid.id IS NULL THEN 0 ELSE
                           trust_assistance_part(n.amount, aid.monthly_rent, aid.monthly_assistance)
                       END
                   ) AS now
            FROM trust_charges AS c
            LEFT JOIN trust_recurring_charges AS rc ON rc.id = c.recurring_charge_id
            -- The authority's part of the same month's rent, if it pays part of it.
            LEFT JOIN trust_assistance_charges AS aid
                ON c.kind = 'rent' AND aid.lease_id = c.lease_id AND aid.source = 'rent'
                AND date_trunc('month', aid.due_on::timestamp)
                    = date_trunc('month', c.due_on::timestamp)
            CROSS JOIN LATERAL (
                SELECT greatest((
                    SELECT r.amount FROM trust_rent_for_month(
                        v_old.starts_on, v_old.ends_on,
                        coalesce(rc.amount, trust_rent_on(p_lease_id, c.due_on)), c.due_on
                    ) AS r
                ), 0) AS amount
            ) AS w
            CROSS JOIN LATERAL (
                SELECT greatest((
                    SELECT r.amount FROM trust_rent_for_month(
                        v_new.starts_on, v_new.ends_on,
                        coalesce(rc.amount, trust_rent_on(p_lease_id, c.due_on)), c.due_on
                    ) AS r
                ), 0) AS amount
            ) AS n
            WHERE c.lease_id = p_lease_id
              AND (c.kind = 'rent' OR c.recurring_charge_id IS NOT NULL)
        ) AS k
        WHERE k.net = k.was AND k.now <> k.was
        ORDER BY k.due_on, k.id
    LOOP
        INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
        VALUES (
            p_pmc_id, p_lease_id, v_charge.due_on,
            CASE WHEN v_charge.now < v_charge.was THEN 'credit' ELSE 'fee' END,
            abs(v_charge.now - v_charge.was),
            v_charge.label || ' due ' || to_char(v_charge.due_on, 'YYYY-MM-DD') || CASE
                WHEN abs(v_charge.now) < abs(v_charge.was)
                    THEN ': the days after the lease ends ' || to_char(p_ends_on, 'YYYY-MM-DD')
                WHEN p_ends_on IS NULL THEN ': the days back, the lease no longer ends'
                ELSE ': the days back, the lease now ends ' || to_char(p_ends_on, 'YYYY-MM-DD')
            END
        )
        RETURNING id INTO v_adjustment;
        INSERT INTO trust_lease_end_adjustments (adjustment_charge_id, pmc_id, charge_id, ends_on)
        VALUES (v_adjustment, p_pmc_id, v_charge.id, p_ends_on);
    END LOOP;

    -- The authority's part of each month's rent, the same way: one that, with the lease ends'
    -- adjustments of it, stands at its part of the month's days under the old end.
    FOR v_charge IN
        SELECT k.id, k.due_on, k.housing_authority_id, k.was, k.now
        FROM (
            SELECT a.id, a.due_on, a.housing_authority_id,
                   a.amount + coalesce((
                       SELECT sum(CASE x.kind WHEN 'credit' THEN -x.amount ELSE x.amount END)
                       FROM trust_assistance_charges AS x
                       WHERE x.adjusts_id = a.id AND x.source = 'lease end'
                   ), 0) AS net,
                   trust_assistance_part(greatest((
                       SELECT r.amount FROM trust_rent_for_month(
                           v_old.starts_on, v_old.ends_on, trust_rent_on(p_lease_id, a.due_on),
                           a.due_on
                       ) AS r
                   ), 0), a.monthly_rent, a.monthly_assistance) AS was,
                   trust_assistance_part(greatest((
                       SELECT r.amount FROM trust_rent_for_month(
                           v_new.starts_on, v_new.ends_on, trust_rent_on(p_lease_id, a.due_on),
                           a.due_on
                       ) AS r
                   ), 0), a.monthly_rent, a.monthly_assistance) AS now
            FROM trust_assistance_charges AS a
            WHERE a.lease_id = p_lease_id AND a.source = 'rent'
        ) AS k
        WHERE k.net = k.was AND k.now <> k.was
        ORDER BY k.due_on, k.id
    LOOP
        INSERT INTO trust_assistance_charges (
            pmc_id, lease_id, housing_authority_id, due_on, kind, amount, memo, source, adjusts_id
        ) VALUES (
            p_pmc_id, p_lease_id, v_charge.housing_authority_id, v_charge.due_on,
            CASE WHEN v_charge.now < v_charge.was THEN 'credit' ELSE 'charge' END,
            abs(v_charge.now - v_charge.was),
            'Rent due ' || to_char(v_charge.due_on, 'YYYY-MM-DD') || CASE
                WHEN v_charge.now < v_charge.was
                    THEN ': the days after the lease ends ' || to_char(p_ends_on, 'YYYY-MM-DD')
                WHEN p_ends_on IS NULL THEN ': the days back, the lease no longer ends'
                ELSE ': the days back, the lease now ends ' || to_char(p_ends_on, 'YYYY-MM-DD')
            END,
            'lease end', v_charge.id
        );
    END LOOP;
END;
$$;

-- The management fee counts the rent a housing authority paid as rent collected.
CREATE OR REPLACE FUNCTION trust_post_management_fee(
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
    -- Rent, or a fee the rent run made from a recurring charge. (A recurring credit is never
    -- paid, so it has no payments here.)
    JOIN trust_charges AS c ON c.id = cp.charge_id
        AND (c.kind = 'rent' OR c.recurring_charge_id IS NOT NULL)
    JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
    WHERE t.to_account_id = p_account AND t.event_at >= v_start AND t.event_at < v_end;
    -- A housing authority's part of the rent it paid (migration 0095).
    SELECT v_collected + coalesce(sum(ap.amount), 0) INTO v_collected
    FROM trust_assistance_payments AS ap
    JOIN pgledger_transfers AS t ON t.id = ap.transfer_id
    WHERE t.to_account_id = p_account AND t.event_at >= v_start AND t.event_at < v_end;
    SELECT v_collected - coalesce(sum(r.amount), 0) INTO v_collected
    FROM trust_payment_reversals AS r
    JOIN trust_charges AS c ON c.id = r.charge_id
        AND (c.kind = 'rent' OR c.recurring_charge_id IS NOT NULL)
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


-- The income statement counts the rent a housing authority paid as rent collected.
CREATE OR REPLACE FUNCTION trust_report_income_statement(
    p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date
) RETURNS TABLE (line integer, property text, item text, schedule_e integer, amount numeric)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
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
        SELECT t.ledger_account_id AS account, pr.display_name AS name
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- What each charge's money is: rent, a recurring charge (pet rent, parking), a late fee, an
    -- NSF fee, or another charge. A lease end's adjustment of a rent or recurring charge
    -- (migration 0051) is what it adjusts.
    classes AS (
        SELECT c.id,
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
        WHERE c.pmc_id = p_pmc_id
    ),
    -- Collected from tenants in the days: payments matched to charges, less what bounced back,
    -- as the year-end package counts them (migration 0039).
    collected AS (
        SELECT tr.to_account_id AS account, k.part, cp.amount
        FROM trust_charge_payments AS cp
        JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
        JOIN classes AS k ON k.id = cp.charge_id
        WHERE cp.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT tr.from_account_id, k.part, -r.amount
        FROM trust_payment_reversals AS r
        JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
        JOIN classes AS k ON k.id = r.charge_id
        WHERE r.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A housing authority's part of the rent, as it paid it (migration 0095): rent.
        SELECT tr.to_account_id, 1, ap.amount
        FROM trust_assistance_payments AS ap
        JOIN pgledger_transfers AS tr ON tr.id = ap.transfer_id
        WHERE ap.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
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
    -- 1 income, 3 expenses. Income and the fees show even when nothing moved.
    items AS (
        SELECT a.account, 1 AS grp, i.part AS ord, i.label AS item, 3 AS sched,
               coalesce((SELECT sum(c.amount) FROM collected AS c
                         WHERE c.account = a.account AND c.part = i.part), 0) AS total
        FROM accounts AS a
        CROSS JOIN incomes AS i
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
$$;


-- The year-end package counts the rent a housing authority paid as collected (and in 1099 rents).
CREATE OR REPLACE FUNCTION trust_report_owner_year_end(
    p_pmc_id uuid,
    p_owner_id uuid,
    p_year integer
) RETURNS TABLE (
    line integer,
    property text,
    item text,
    amount numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
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
$$;


-- A renewal keeps the housing assistance of the lease it renews (migration 0095); otherwise as
-- in migration 0080.
CREATE OR REPLACE FUNCTION trust_complete_lease_offer(
    p_pmc_id uuid,
    p_offer_id uuid,
    p_completed_by uuid
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_offer trust_lease_offers;
    v_lease uuid;
    v_old trust_leases;
    v_tenants uuid[];
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: complete lease offers at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    SELECT o.* INTO v_offer FROM trust_lease_offers AS o
    WHERE o.id = p_offer_id AND o.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease offer % in PMC %', p_offer_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Offers for one application or one lease wait for each other.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'lease offer of ' || coalesce(v_offer.application_id, v_offer.renews_lease_id), 0
    ));

    SELECT c.lease_id INTO v_lease FROM trust_lease_offer_completions AS c
    WHERE c.offer_id = p_offer_id;
    IF FOUND THEN
        RETURN v_lease;
    END IF;
    IF EXISTS (
        SELECT s.position FROM trust_lease_offer_signers AS s
        WHERE s.offer_id = p_offer_id AND NOT EXISTS (
            SELECT g.position FROM trust_signatures AS g
            WHERE g.offer_id = s.offer_id AND g.position = s.position
        )
    ) OR NOT EXISTS (
        SELECT s.position FROM trust_lease_offer_signers AS s WHERE s.offer_id = p_offer_id
    ) THEN
        RAISE EXCEPTION 'trust: lease offer % is not signed by everyone yet', p_offer_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT o.id FROM trust_lease_offers AS o
        JOIN trust_lease_offer_completions AS c ON c.offer_id = o.id
        WHERE o.application_id = v_offer.application_id
           OR o.renews_lease_id = v_offer.renews_lease_id
    ) THEN
        RAISE EXCEPTION 'trust: another offer for this % became its lease already',
            CASE WHEN v_offer.application_id IS NULL THEN 'lease' ELSE 'application' END
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_offer.application_id IS NOT NULL THEN
        WITH made AS (
            INSERT INTO trust_tenants (pmc_id, property_id, display_name)
            SELECT p_pmc_id, u.property_id, s.display_name
            FROM trust_lease_offer_signers AS s
            JOIN trust_units AS u ON u.id = v_offer.unit_id
            WHERE s.offer_id = p_offer_id AND s.role = 'tenant'
            ORDER BY s.position
            RETURNING id
        )
        SELECT array_agg(m.id) INTO v_tenants FROM made AS m;
    ELSE
        SELECT l.* INTO v_old FROM trust_leases AS l WHERE l.id = v_offer.renews_lease_id;
        SELECT array_agg(t.tenant_id) INTO v_tenants
        FROM trust_lease_tenants AS t
        WHERE t.lease_id = v_old.id AND NOT EXISTS (
            SELECT d.tenant_id FROM trust_lease_departures AS d
            WHERE d.lease_id = t.lease_id AND d.tenant_id = t.tenant_id
        );
        IF v_old.ends_on IS NULL OR v_old.ends_on >= v_offer.starts_on THEN
            PERFORM trust_end_lease(p_pmc_id, v_old.id, v_offer.starts_on - 1);
        END IF;
    END IF;
    IF coalesce(cardinality(v_tenants), 0) = 0 THEN
        RAISE EXCEPTION 'trust: lease offer % has no tenant to make its lease with', p_offer_id
            USING ERRCODE = 'check_violation';
    END IF;

    v_lease := trust_open_lease(
        p_pmc_id, v_offer.unit_id, v_offer.starts_on, v_offer.ends_on, v_offer.monthly_rent,
        v_tenants
    );
    -- A renewal keeps the housing assistance in force on its first day (migration 0095), from
    -- its first month and never past its rent, so its rent is split as the old lease's was,
    -- the part month charged below included.
    IF v_offer.application_id IS NULL THEN
        INSERT INTO trust_rent_assistance (
            pmc_id, lease_id, housing_authority_id, starts_on, amount, contract
        )
        SELECT p_pmc_id, v_lease, a.housing_authority_id,
               date_trunc('month', v_offer.starts_on::timestamp)::date,
               least(a.amount, v_offer.monthly_rent), a.contract
        FROM trust_assistance_on(v_old.id, v_offer.starts_on) AS a
        WHERE a.amount > 0;
    END IF;
    INSERT INTO trust_lease_guarantors (pmc_id, lease_id, display_name, email, offer_id)
    SELECT p_pmc_id, v_lease, s.display_name, s.email, p_offer_id
    FROM trust_lease_offer_signers AS s
    WHERE s.offer_id = p_offer_id AND s.role = 'guarantor'
    ORDER BY s.position;
    -- A renewal starting in a month whose rent the old lease was charged: the old lease's days
    -- after its end come off (trust_end_lease above), and the new lease is charged its days of
    -- that month, due on its first day, as the rent run would.
    IF v_offer.application_id IS NULL THEN
        INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
        SELECT p_pmc_id, v_lease, v_offer.starts_on, 'rent', r.amount,
               'Rent due ' || to_char(v_offer.starts_on, 'YYYY-MM-DD') || r.days
        FROM trust_rent_for_month(
            v_offer.starts_on, v_offer.ends_on, v_offer.monthly_rent, v_offer.starts_on
        ) AS r
        WHERE r.amount > 0 AND EXISTS (
            SELECT c.id FROM trust_charges AS c
            WHERE c.lease_id = v_old.id AND c.kind = 'rent'
              AND date_trunc('month', c.due_on::timestamp)
                  = date_trunc('month', v_offer.starts_on::timestamp)
        );
    END IF;
    INSERT INTO trust_lease_offer_completions (pmc_id, offer_id, lease_id, completed_by)
    VALUES (p_pmc_id, p_offer_id, v_lease, p_completed_by);
    RETURN v_lease;
END;
$$;


-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'housing_assistance cannot be rolled back: rent split and paid by it stands';
END;
$$;
