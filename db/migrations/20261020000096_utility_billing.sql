-- migrate:up
-- Utility billing: a property's utility bill shared among its leases by RUBS or submeters (the
-- founder's review, q021: "Can a utility bill be split among tenants (RUBS or meter
-- readings)?"; docs/law.md).
--
--   trust_unit_areas         a unit's square feet, the latest row holding, naming who recorded it.
--   trust_utility_plans      how a property bills a utility back from a day: by unit, by square
--                            feet, by occupants or by submeter, and the owner's share (the common
--                            areas'), in percent; 'none' stops it. The latest from a day holds.
--   trust_lease_utilities    whether a lease is billed for a utility from a day: the lease must
--                            say so; the latest from a day holds.
--   trust_utility_bills      a utility bill of a property for a period, billed back once
--                            (trust_bill_back_utility): no two of a property's bills for a utility
--                            cover the same day.
--   trust_utility_shares     each lease's share of it, with what it was worked out from, and the
--                            fee it charged the lease.
--
-- A bill is shared by the plan in force on its first day. The owner's share comes off first; the
-- rest is the tenants' pool, and each lease in effect in the period weighs:
--   by unit         its days in effect, of every unit of the property for every day;
--   by square feet  its unit's square feet for its days in effect, of every unit's;
--   by occupants    the people on it (tenants who have not left and others living there) for its
--                   days in effect, of every lease's in the period;
--   by submeter     its unit's reading for the period, of the master meter's.
-- Each lease's share is its weight's part of the pool, rounded down to the cent. So a vacant
-- unit's days (by unit and square feet), a lease that isn't billed for the utility, the common
-- areas and every part of a cent stay with the owner: the tenants together are never charged more
-- than the bill, nor what another lease or the common areas used. All append-only; no money moves
-- here: a share is a fee charged to the lease, paid like any other.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_unit_areas (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    unit_id uuid NOT NULL,
    square_feet bigint NOT NULL CHECK (square_feet > 0 AND square_feet <= 1000000),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, unit_id) REFERENCES trust_units (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_unit_areas_unit ON trust_unit_areas (pmc_id, unit_id, created_at);
CREATE INDEX trust_unit_areas_recorded_by ON trust_unit_areas (pmc_id, recorded_by);

CREATE TABLE trust_utility_plans (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid NOT NULL,
    utility text NOT NULL
        CHECK (utility IN ('water', 'sewer', 'trash', 'gas', 'electric', 'other')),
    starts_on date NOT NULL,
    method text NOT NULL
        CHECK (method IN ('units', 'square_feet', 'occupants', 'submeter', 'none')),
    -- The common areas' share, which the owner keeps: no tenant is charged it.
    owner_percent numeric NOT NULL DEFAULT 0
        CHECK (owner_percent >= 0 AND owner_percent <= 100
               AND owner_percent = round(owner_percent, 2)),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (property_id, utility, starts_on),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_utility_plans_pmc ON trust_utility_plans (pmc_id, property_id);
CREATE INDEX trust_utility_plans_recorded_by ON trust_utility_plans (pmc_id, recorded_by);

CREATE TABLE trust_lease_utilities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    utility text NOT NULL
        CHECK (utility IN ('water', 'sewer', 'trash', 'gas', 'electric', 'other')),
    starts_on date NOT NULL,
    billed boolean NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (lease_id, utility, starts_on),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_lease_utilities_pmc ON trust_lease_utilities (pmc_id, lease_id);
CREATE INDEX trust_lease_utilities_recorded_by ON trust_lease_utilities (pmc_id, recorded_by);

CREATE TABLE trust_utility_bills (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid NOT NULL,
    utility text NOT NULL
        CHECK (utility IN ('water', 'sewer', 'trash', 'gas', 'electric', 'other')),
    period_start date NOT NULL,
    period_end date NOT NULL,
    amount numeric NOT NULL
        CHECK (amount > 0 AND amount = round(amount, 2) AND amount <= 10000000),
    -- The plan it was shared by, as it stood on the period's first day.
    method text NOT NULL CHECK (method IN ('units', 'square_feet', 'occupants', 'submeter')),
    owner_percent numeric NOT NULL CHECK (owner_percent >= 0 AND owner_percent <= 100),
    -- What the master meter read for the period, for a submetered utility.
    master_usage numeric CHECK (master_usage > 0),
    due_on date NOT NULL,
    memo text CHECK (btrim(memo) <> '' AND length(memo) <= 100),
    -- The vendor's bill for it, when it was entered as one.
    bill_id uuid UNIQUE,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    CHECK (period_end >= period_start AND period_end < period_start + 366),
    CHECK ((method = 'submeter') = (master_usage IS NOT NULL)),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, bill_id) REFERENCES trust_bills (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_utility_bills_property
ON trust_utility_bills (pmc_id, property_id, utility, period_start);
CREATE INDEX trust_utility_bills_bill ON trust_utility_bills (pmc_id, bill_id);
CREATE INDEX trust_utility_bills_recorded_by ON trust_utility_bills (pmc_id, recorded_by);

CREATE TABLE trust_utility_shares (
    utility_bill_id uuid NOT NULL,
    lease_id uuid NOT NULL,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    billed boolean NOT NULL,
    weight numeric NOT NULL CHECK (weight >= 0),
    usage numeric CHECK (usage >= 0),
    amount numeric NOT NULL CHECK (amount >= 0 AND amount = round(amount, 2)),
    charge_id uuid UNIQUE,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (utility_bill_id, lease_id),
    CHECK ((charge_id IS NULL) = (amount = 0)),
    CHECK (billed OR amount = 0),
    FOREIGN KEY (pmc_id, utility_bill_id) REFERENCES trust_utility_bills (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, charge_id) REFERENCES trust_charges (pmc_id, id)
);
CREATE INDEX trust_utility_shares_pmc ON trust_utility_shares (pmc_id, utility_bill_id);
CREATE INDEX trust_utility_shares_lease ON trust_utility_shares (pmc_id, lease_id);
CREATE INDEX trust_utility_shares_charge ON trust_utility_shares (pmc_id, charge_id);

-- --- what holds on a day --------------------------------------------------------------------

-- A unit's square feet: the latest recorded.
CREATE FUNCTION trust_unit_area(p_unit_id uuid) RETURNS bigint
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT a.square_feet FROM trust_unit_areas AS a
    WHERE a.unit_id = p_unit_id
    ORDER BY a.created_at DESC, a.id DESC
    LIMIT 1
$$;
REVOKE ALL ON FUNCTION trust_unit_area(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_unit_area(uuid) TO trust_app;

-- A property's plan for a utility on a day: the latest from that day or before.
CREATE FUNCTION trust_utility_plan_on(p_property_id uuid, p_utility text, p_day date)
RETURNS SETOF trust_utility_plans
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT p.* FROM trust_utility_plans AS p
    WHERE p.property_id = p_property_id AND p.utility = p_utility AND p.starts_on <= p_day
    ORDER BY p.starts_on DESC, p.created_at DESC, p.id DESC
    LIMIT 1
$$;
REVOKE ALL ON FUNCTION trust_utility_plan_on(uuid, text, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_utility_plan_on(uuid, text, date) TO trust_app;

-- Whether a lease is billed for a utility on a day: the latest from that day or before; never,
-- without one.
CREATE FUNCTION trust_lease_billed(p_lease_id uuid, p_utility text, p_day date) RETURNS boolean
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce((
        SELECT u.billed FROM trust_lease_utilities AS u
        WHERE u.lease_id = p_lease_id AND u.utility = p_utility AND u.starts_on <= p_day
        ORDER BY u.starts_on DESC, u.created_at DESC, u.id DESC
        LIMIT 1
    ), false)
$$;
REVOKE ALL ON FUNCTION trust_lease_billed(uuid, text, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_lease_billed(uuid, text, date) TO trust_app;

-- The people living under a lease on a day: its tenants who have not left by then (migration
-- 0080), and the others living there who have not been taken off it.
CREATE FUNCTION trust_lease_people(p_lease_id uuid, p_day date) RETURNS integer
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT (
        SELECT count(*) FROM trust_lease_tenants AS t
        WHERE t.lease_id = p_lease_id AND NOT EXISTS (
            SELECT 1 FROM trust_lease_departures AS d
            WHERE d.lease_id = t.lease_id AND d.tenant_id = t.tenant_id AND d.left_on <= p_day
        )
    )::integer + (
        SELECT count(*) FROM trust_lease_occupants AS o
        WHERE o.lease_id = p_lease_id AND NOT EXISTS (
            SELECT 1 FROM trust_lease_household_removals AS r WHERE r.occupant_id = o.id
        )
    )::integer
$$;
REVOKE ALL ON FUNCTION trust_lease_people(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_lease_people(uuid, date) TO trust_app;

-- --- sharing a bill -------------------------------------------------------------------------

-- Each lease of a property in effect in a period, whether it is billed for the utility, and its
-- weight by a method (see the top of this file); by submeter, its reading from `p_usage`, which
-- maps a lease's id to its unit's reading for the period.
CREATE FUNCTION trust_utility_weights(
    p_pmc_id uuid,
    p_property_id uuid,
    p_utility text,
    p_period_start date,
    p_period_end date,
    p_method text,
    p_usage jsonb
) RETURNS TABLE (lease_id uuid, unit_id uuid, billed boolean, weight numeric, usage numeric)
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT x.id, x.unit_id, trust_lease_billed(x.id, p_utility, x.last_day),
           CASE p_method
               WHEN 'units' THEN x.days::numeric
               WHEN 'square_feet' THEN trust_unit_area(x.unit_id)::numeric * x.days
               WHEN 'occupants' THEN trust_lease_people(x.id, x.last_day)::numeric * x.days
               ELSE coalesce((p_usage ->> x.id::text)::numeric, 0)
           END,
           CASE WHEN p_method = 'submeter'
                THEN coalesce((p_usage ->> x.id::text)::numeric, 0) END
    FROM (
        SELECT l.id, l.unit_id,
               least(p_period_end, coalesce(l.ends_on, p_period_end)) AS last_day,
               least(p_period_end, coalesce(l.ends_on, p_period_end))
               - greatest(p_period_start, l.starts_on) + 1 AS days
        FROM trust_leases AS l
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE l.pmc_id = p_pmc_id AND u.property_id = p_property_id
          AND l.starts_on <= p_period_end
          AND (l.ends_on IS NULL OR l.ends_on >= p_period_start)
    ) AS x
$$;
REVOKE ALL ON FUNCTION trust_utility_weights(uuid, uuid, text, date, date, text, jsonb)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_utility_weights(uuid, uuid, text, date, date, text, jsonb)
TO trust_app;

-- How a utility bill of a property for a period is shared among the leases in effect in it, by
-- the plan in force on its first day (see the top of this file): each lease, whether it is
-- billed for the utility, its weight, its reading (by submeter) and its share. Nothing is
-- written.
CREATE FUNCTION trust_utility_allocation(
    p_pmc_id uuid,
    p_property_id uuid,
    p_utility text,
    p_period_start date,
    p_period_end date,
    p_amount numeric,
    p_master_usage numeric,
    p_usage jsonb
) RETURNS TABLE (lease_id uuid, unit_id uuid, billed boolean, weight numeric, usage numeric,
                 amount numeric)
LANGUAGE plpgsql STABLE
SET search_path = public, pg_temp
AS $$
DECLARE
    v_plan trust_utility_plans;
    v_pool numeric;
    v_total numeric;
    v_least numeric;
    v_unknown text;
BEGIN
    IF p_period_end < p_period_start OR p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a utility bill is for a period of days and an amount above 0'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM trust_properties AS p WHERE p.id = p_property_id AND p.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no property % in PMC %', p_property_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT p.* INTO v_plan FROM trust_utility_plan_on(p_property_id, p_utility, p_period_start) AS p;
    IF NOT FOUND OR v_plan.method = 'none' THEN
        RAISE EXCEPTION 'trust: the property bills no % back on %', p_utility, p_period_start
            USING ERRCODE = 'check_violation';
    END IF;
    IF (v_plan.method = 'submeter') <> (p_master_usage IS NOT NULL) THEN
        RAISE EXCEPTION 'trust: a submetered utility''s bill gives the master meter''s reading, '
            'and only it'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_pool := p_amount * (100 - v_plan.owner_percent) / 100;

    IF v_plan.method = 'submeter' THEN
        SELECT k INTO v_unknown FROM jsonb_object_keys(coalesce(p_usage, '{}')) AS k
        WHERE k NOT IN (
            SELECT w.lease_id::text FROM trust_utility_weights(
                p_pmc_id, p_property_id, p_utility, p_period_start, p_period_end, 'units', NULL
            ) AS w
        )
        ORDER BY k
        LIMIT 1;
        IF v_unknown IS NOT NULL THEN
            RAISE EXCEPTION 'trust: % is no lease of the property in effect in the period',
                v_unknown
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        SELECT coalesce(sum(w.usage), 0), min(w.usage) INTO v_total, v_least
        FROM trust_utility_weights(
            p_pmc_id, p_property_id, p_utility, p_period_start, p_period_end, 'submeter', p_usage
        ) AS w;
        IF v_least < 0 THEN
            RAISE EXCEPTION 'trust: a reading is 0 or more'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        IF v_total > p_master_usage THEN
            RAISE EXCEPTION 'trust: the units'' readings, %, come to more than the master '
                'meter''s, %', v_total, p_master_usage
                USING ERRCODE = 'check_violation';
        END IF;
        v_total := p_master_usage;
    ELSIF v_plan.method = 'occupants' THEN
        SELECT coalesce(sum(w.weight), 0) INTO v_total
        FROM trust_utility_weights(
            p_pmc_id, p_property_id, p_utility, p_period_start, p_period_end, 'occupants', NULL
        ) AS w;
    ELSE
        -- Every unit of the property for every day: not archived, or leased in the period.
        SELECT u.display_name INTO v_unknown
        FROM trust_units AS u
        WHERE v_plan.method = 'square_feet'
          AND u.pmc_id = p_pmc_id AND u.property_id = p_property_id
          AND trust_unit_area(u.id) IS NULL
          AND (NOT trust_is_archived(NULL, u.id) OR EXISTS (
              SELECT 1 FROM trust_leases AS l
              WHERE l.unit_id = u.id AND l.starts_on <= p_period_end
                AND (l.ends_on IS NULL OR l.ends_on >= p_period_start)
          ))
        ORDER BY u.display_name COLLATE "C", u.id
        LIMIT 1;
        IF v_unknown IS NOT NULL THEN
            RAISE EXCEPTION 'trust: % has no square feet on file', v_unknown
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT coalesce(sum(
                   CASE v_plan.method WHEN 'units' THEN 1 ELSE trust_unit_area(u.id) END
               ), 0) * (p_period_end - p_period_start + 1)
        INTO v_total
        FROM trust_units AS u
        WHERE u.pmc_id = p_pmc_id AND u.property_id = p_property_id
          AND (NOT trust_is_archived(NULL, u.id) OR EXISTS (
              SELECT 1 FROM trust_leases AS l
              WHERE l.unit_id = u.id AND l.starts_on <= p_period_end
                AND (l.ends_on IS NULL OR l.ends_on >= p_period_start)
          ));
    END IF;

    RETURN QUERY
    SELECT w.lease_id, w.unit_id, w.billed, w.weight, w.usage,
           CASE WHEN w.billed AND v_total > 0
                THEN floor(v_pool * w.weight / v_total * 100) / 100
                ELSE 0::numeric END
    FROM trust_utility_weights(
        p_pmc_id, p_property_id, p_utility, p_period_start, p_period_end, v_plan.method, p_usage
    ) AS w
    ORDER BY w.lease_id;
END;
$$;
REVOKE ALL ON FUNCTION trust_utility_allocation(
    uuid, uuid, text, date, date, numeric, numeric, jsonb
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_utility_allocation(
    uuid, uuid, text, date, date, numeric, numeric, jsonb
) TO trust_app;

-- Bills a property's utility bill for a period back to its leases, once: each share above 0 a
-- fee charged to its lease, due on `p_due_on`, saying what it is for. No two of a property's
-- bills for a utility cover the same day. Maybe naming the vendor's bill it came from, of the
-- same property.
CREATE FUNCTION trust_bill_back_utility(
    p_pmc_id uuid,
    p_property_id uuid,
    p_utility text,
    p_period_start date,
    p_period_end date,
    p_amount numeric,
    p_due_on date,
    p_memo text,
    p_master_usage numeric,
    p_usage jsonb,
    p_bill_id uuid
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bill uuid;
    v_plan trust_utility_plans;
    v_shares jsonb;
    v_share jsonb;
    v_charge uuid;
    v_memo text;
BEGIN
    -- Bills of a property for a utility wait for each other, so two never cover a day.
    PERFORM pg_advisory_xact_lock(
        hashtextextended('utility bills of ' || p_property_id || ' ' || p_utility, 0)
    );
    -- Its shares first: this refuses a bill the plan can't share.
    SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.lease_id), '[]'::jsonb) INTO v_shares
    FROM trust_utility_allocation(
        p_pmc_id, p_property_id, p_utility, p_period_start, p_period_end, p_amount,
        p_master_usage, p_usage
    ) AS a;
    IF EXISTS (
        SELECT 1 FROM trust_utility_bills AS b
        WHERE b.property_id = p_property_id AND b.utility = p_utility
          AND b.period_start <= p_period_end AND b.period_end >= p_period_start
    ) THEN
        RAISE EXCEPTION 'trust: a % bill of the property already covers part of % to %',
            p_utility, p_period_start, p_period_end
            USING ERRCODE = 'unique_violation';
    END IF;
    IF p_bill_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM trust_bills AS b
        JOIN trust_ledger_accounts AS a ON a.ledger_account_id = b.ledger_account_id
        WHERE b.id = p_bill_id AND b.pmc_id = p_pmc_id AND a.property_id = p_property_id
    ) THEN
        RAISE EXCEPTION 'trust: bill % is no bill of the property', p_bill_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT p.* INTO v_plan FROM trust_utility_plan_on(p_property_id, p_utility, p_period_start) AS p;

    INSERT INTO trust_utility_bills (
        pmc_id, property_id, utility, period_start, period_end, amount, method, owner_percent,
        master_usage, due_on, memo, bill_id
    ) VALUES (
        p_pmc_id, p_property_id, p_utility, p_period_start, p_period_end, p_amount,
        v_plan.method, v_plan.owner_percent, p_master_usage, p_due_on, p_memo, p_bill_id
    )
    RETURNING id INTO v_bill;

    v_memo := initcap(p_utility) || ' ' || to_char(p_period_start, 'YYYY-MM-DD') || ' to '
        || to_char(p_period_end, 'YYYY-MM-DD') || ', '
        || CASE v_plan.method
               WHEN 'units' THEN 'by unit'
               WHEN 'square_feet' THEN 'by square feet'
               WHEN 'occupants' THEN 'by occupants'
               ELSE 'by meter'
           END
        || coalesce(': ' || p_memo, '');
    FOR v_share IN SELECT s FROM jsonb_array_elements(v_shares) AS s
    LOOP
        v_charge := NULL;
        IF (v_share ->> 'amount')::numeric > 0 THEN
            INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
            VALUES (
                p_pmc_id, (v_share ->> 'lease_id')::uuid, p_due_on, 'fee',
                (v_share ->> 'amount')::numeric, v_memo
            )
            RETURNING id INTO v_charge;
        END IF;
        INSERT INTO trust_utility_shares (
            utility_bill_id, lease_id, pmc_id, billed, weight, usage, amount, charge_id
        ) VALUES (
            v_bill, (v_share ->> 'lease_id')::uuid, p_pmc_id, (v_share ->> 'billed')::boolean,
            (v_share ->> 'weight')::numeric, (v_share ->> 'usage')::numeric,
            (v_share ->> 'amount')::numeric, v_charge
        );
    END LOOP;
    RETURN v_bill;
END;
$$;
REVOKE ALL ON FUNCTION trust_bill_back_utility(
    uuid, uuid, text, date, date, numeric, date, text, numeric, jsonb, uuid
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_bill_back_utility(
    uuid, uuid, text, date, date, numeric, date, text, numeric, jsonb, uuid
) TO trust_app;

-- --- the company's reply-to email ---------------------------------------------------------------

-- Where replies to the company's emails go (the founder's review q086): its own email, the
-- server's From address staying the server's. None: replies go to the From address.
ALTER TABLE trust_company_profiles ADD COLUMN reply_to text
CHECK (reply_to ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' AND char_length(reply_to) <= 254);

-- --- records ----------------------------------------------------------------------------------

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_unit_areas
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_utility_plans
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_lease_utilities
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_utility_bills
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_unit_areas
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_utility_plans
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_utilities
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_utility_bills
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_utility_shares
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_unit_areas
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_utility_plans
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_utilities
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_utility_bills
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_utility_shares
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_unit_areas FROM PUBLIC;
REVOKE ALL ON trust_utility_plans FROM PUBLIC;
REVOKE ALL ON trust_lease_utilities FROM PUBLIC;
REVOKE ALL ON trust_utility_bills FROM PUBLIC;
REVOKE ALL ON trust_utility_shares FROM PUBLIC;
GRANT SELECT, INSERT ON trust_unit_areas TO trust_app;
GRANT SELECT, INSERT ON trust_utility_plans TO trust_app;
GRANT SELECT, INSERT ON trust_lease_utilities TO trust_app;
GRANT SELECT ON trust_utility_bills TO trust_app;
GRANT SELECT ON trust_utility_shares TO trust_app;
GRANT SELECT ON trust_unit_areas TO trust_ai_agent;
GRANT SELECT ON trust_utility_plans TO trust_ai_agent;
GRANT SELECT ON trust_lease_utilities TO trust_ai_agent;
GRANT SELECT ON trust_utility_bills TO trust_ai_agent;
GRANT SELECT ON trust_utility_shares TO trust_ai_agent;


-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'utility_billing cannot be rolled back: lease charges were made from it';
END;
$$;
