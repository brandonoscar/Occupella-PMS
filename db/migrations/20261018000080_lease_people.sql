-- migrate:up
-- Who is on a lease besides its tenants, and a tenant leaving it (the founder's review: q003,
-- q007, q011, q012).
--
--   trust_lease_departures          a tenant leaving a lease others stay on, from a day, once.
--                                   Their deposit either moves to a tenant who stays (it is the
--                                   lease's money, and stays in the security-deposit account) or
--                                   stays under their name until the lease's deposit is returned
--                                   at move-out. Written only by trust_remove_lease_tenant.
--   trust_lease_occupants           others living there who aren't tenants (a child, a partner).
--   trust_lease_pets                a pet, or an assistance animal, which the Fair Housing Act
--                                   says is not a pet: no pet rent, fee or deposit is charged for
--                                   it (docs/law.md).
--   trust_lease_vehicles            a vehicle kept there.
--   trust_lease_guarantors          someone who guarantees the lease without living there. A
--                                   lease offer's signer may be one (trust_lease_offer_signers.
--                                   role): they sign, and become the lease's guarantor, not a
--                                   tenant.
--   trust_lease_household_removals  an occupant, pet, vehicle or guarantor taken off, once.
--   trust_lease_break_fees          a property's lease-break fee terms from a day: a flat fee and
--                                   a number of months' rent. A lease breaks under the terms in
--                                   force on its first day, and only when staff charge it.
--
-- All append-only, audited and naming who recorded them. None of it moves money except a
-- departing tenant's deposit moving to a tenant who stays, inside one trust bank account. The AI
-- role reads departures and fee terms, and none of the household or guarantors (personal details,
-- and an assistance animal speaks to a disability).

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_lease_departures (
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    left_on date NOT NULL,
    -- The tenant staying on the lease who took over the deposit; NULL keeps it under the
    -- departing tenant's name until the lease's deposit is returned.
    deposit_to uuid,
    moved numeric NOT NULL DEFAULT 0 CHECK (moved >= 0),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (lease_id, tenant_id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (lease_id, tenant_id) REFERENCES trust_lease_tenants (lease_id, tenant_id),
    FOREIGN KEY (lease_id, deposit_to) REFERENCES trust_lease_tenants (lease_id, tenant_id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id),
    CHECK (deposit_to <> tenant_id),
    CHECK (deposit_to IS NOT NULL OR moved = 0)
);
CREATE INDEX trust_lease_departures_tenant ON trust_lease_departures (tenant_id);
CREATE INDEX trust_lease_departures_deposit_to ON trust_lease_departures (lease_id, deposit_to);
CREATE INDEX trust_lease_departures_recorded_by ON trust_lease_departures (pmc_id, recorded_by);

CREATE TABLE trust_lease_occupants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    display_name text NOT NULL CHECK (display_name ~ '\S' AND length(display_name) <= 200),
    relationship text CHECK (relationship ~ '\S' AND length(relationship) <= 100),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_lease_occupants_lease ON trust_lease_occupants (pmc_id, lease_id);
CREATE INDEX trust_lease_occupants_recorded_by ON trust_lease_occupants (pmc_id, recorded_by);

CREATE TABLE trust_lease_pets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    display_name text NOT NULL CHECK (display_name ~ '\S' AND length(display_name) <= 100),
    kind text NOT NULL CHECK (kind ~ '\S' AND length(kind) <= 50),
    breed text CHECK (breed ~ '\S' AND length(breed) <= 100),
    weight_lbs numeric CHECK (weight_lbs > 0 AND weight_lbs <= 500 AND weight_lbs = round(weight_lbs, 1)),
    assistance_animal boolean NOT NULL DEFAULT false,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_lease_pets_lease ON trust_lease_pets (pmc_id, lease_id);
CREATE INDEX trust_lease_pets_recorded_by ON trust_lease_pets (pmc_id, recorded_by);

CREATE TABLE trust_lease_vehicles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    make text NOT NULL CHECK (make ~ '\S' AND length(make) <= 50),
    model text CHECK (model ~ '\S' AND length(model) <= 50),
    color text CHECK (color ~ '\S' AND length(color) <= 30),
    plate text NOT NULL CHECK (plate ~ '\S' AND length(plate) <= 15),
    plate_state text CHECK (plate_state ~ '\S' AND length(plate_state) <= 30),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_lease_vehicles_lease ON trust_lease_vehicles (pmc_id, lease_id);
CREATE INDEX trust_lease_vehicles_recorded_by ON trust_lease_vehicles (pmc_id, recorded_by);

CREATE TABLE trust_lease_guarantors (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL,
    display_name text NOT NULL CHECK (display_name ~ '\S' AND length(display_name) <= 200),
    email text CHECK (
        email = lower(email) AND email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' AND length(email) <= 254
    ),
    phone text CHECK (phone ~ '\S' AND length(phone) <= 30),
    -- The lease offer they signed as its guarantor, when they did.
    offer_id uuid,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, offer_id) REFERENCES trust_lease_offers (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_lease_guarantors_lease ON trust_lease_guarantors (pmc_id, lease_id);
CREATE INDEX trust_lease_guarantors_offer ON trust_lease_guarantors (pmc_id, offer_id);
CREATE INDEX trust_lease_guarantors_recorded_by ON trust_lease_guarantors (pmc_id, recorded_by);

CREATE TABLE trust_lease_household_removals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    occupant_id uuid UNIQUE,
    pet_id uuid UNIQUE,
    vehicle_id uuid UNIQUE,
    guarantor_id uuid UNIQUE,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (num_nonnulls(occupant_id, pet_id, vehicle_id, guarantor_id) = 1),
    FOREIGN KEY (pmc_id, occupant_id) REFERENCES trust_lease_occupants (pmc_id, id),
    FOREIGN KEY (pmc_id, pet_id) REFERENCES trust_lease_pets (pmc_id, id),
    FOREIGN KEY (pmc_id, vehicle_id) REFERENCES trust_lease_vehicles (pmc_id, id),
    FOREIGN KEY (pmc_id, guarantor_id) REFERENCES trust_lease_guarantors (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_lease_household_removals_pmc ON trust_lease_household_removals (pmc_id);
CREATE INDEX trust_lease_household_removals_recorded_by
ON trust_lease_household_removals (pmc_id, recorded_by);

CREATE TABLE trust_lease_break_fees (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    property_id uuid NOT NULL,
    starts_on date NOT NULL,
    flat_fee numeric NOT NULL DEFAULT 0 CHECK (flat_fee >= 0 AND flat_fee = round(flat_fee, 2)),
    rent_months numeric NOT NULL DEFAULT 0
        CHECK (rent_months >= 0 AND rent_months <= 12 AND rent_months = round(rent_months, 2)),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (property_id, starts_on),
    FOREIGN KEY (pmc_id, property_id) REFERENCES trust_properties (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_lease_break_fees_pmc ON trust_lease_break_fees (pmc_id, property_id);
CREATE INDEX trust_lease_break_fees_recorded_by ON trust_lease_break_fees (pmc_id, recorded_by);

-- A lease offer's signer is a tenant to be, or the lease's guarantor.
ALTER TABLE trust_lease_offer_signers
ADD COLUMN role text NOT NULL DEFAULT 'tenant';
ALTER TABLE trust_lease_offer_signers
ADD CONSTRAINT trust_lease_offer_signers_role CHECK (role IN ('tenant', 'guarantor')) NOT VALID;
-- Every row there holds the default just added (at most six signers an offer), so validating
-- reads a small table once.
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_lease_offer_signers VALIDATE CONSTRAINT trust_lease_offer_signers_role;

-- A tenant leaves a lease others stay on, from a day, once; what moved of their deposit. Their
-- deposit moves to p_deposit_to, a tenant staying on the lease, or (NULL) stays under their name
-- until the lease's deposit is returned. The same call again returns what moved the first time.
CREATE FUNCTION trust_remove_lease_tenant(
    p_pmc_id uuid,
    p_lease_id uuid,
    p_tenant_id uuid,
    p_left_on date,
    p_deposit_to uuid
) RETURNS numeric
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_lease trust_leases;
    v_done trust_lease_departures;
    v_name text;
    v_still record;
    v_deposit record;
    v_to text;
    v_moved numeric := 0;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: remove tenants at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.id = p_lease_id AND l.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no lease % in PMC %', p_lease_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- A lease's rent work, deposit return and departures wait for each other.
    PERFORM pg_advisory_xact_lock(hashtextextended('rent of lease ' || p_lease_id, 0));

    SELECT t.display_name INTO v_name FROM trust_lease_tenants AS lt
    JOIN trust_tenants AS t ON t.id = lt.tenant_id
    WHERE lt.lease_id = p_lease_id AND lt.tenant_id = p_tenant_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: tenant % is not on lease %', p_tenant_id, p_lease_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT d.* INTO v_done FROM trust_lease_departures AS d
    WHERE d.lease_id = p_lease_id AND d.tenant_id = p_tenant_id;
    IF FOUND THEN
        IF v_done.left_on = p_left_on AND v_done.deposit_to IS NOT DISTINCT FROM p_deposit_to THEN
            RETURN v_done.moved;
        END IF;
        RAISE EXCEPTION 'trust: % left this lease on % already', v_name, v_done.left_on
            USING ERRCODE = 'unique_violation';
    END IF;
    IF EXISTS (SELECT r.lease_id FROM trust_deposit_returns AS r WHERE r.lease_id = p_lease_id) THEN
        RAISE EXCEPTION 'trust: the deposit of lease % was returned already', p_lease_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF p_left_on IS NULL OR p_left_on < v_lease.starts_on OR p_left_on > v_lease.ends_on THEN
        RAISE EXCEPTION 'trust: a tenant leaves a lease within its days: from % to %',
            v_lease.starts_on, coalesce(v_lease.ends_on::text, 'month to month')
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT lt.tenant_id FROM trust_lease_tenants AS lt
        WHERE lt.lease_id = p_lease_id AND lt.tenant_id <> p_tenant_id AND NOT EXISTS (
            SELECT d.tenant_id FROM trust_lease_departures AS d
            WHERE d.lease_id = lt.lease_id AND d.tenant_id = lt.tenant_id
        )
    ) THEN
        RAISE EXCEPTION 'trust: % is the last tenant on this lease: end the lease instead',
            v_name
            USING ERRCODE = 'check_violation';
    END IF;

    IF p_deposit_to IS NOT NULL THEN
        IF p_deposit_to = p_tenant_id OR NOT EXISTS (
            SELECT lt.tenant_id FROM trust_lease_tenants AS lt
            WHERE lt.lease_id = p_lease_id AND lt.tenant_id = p_deposit_to
        ) OR EXISTS (
            SELECT d.tenant_id FROM trust_lease_departures AS d
            WHERE d.lease_id = p_lease_id AND d.tenant_id = p_deposit_to
        ) THEN
            RAISE EXCEPTION 'trust: tenant % is not staying on lease %', p_deposit_to, p_lease_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        -- A deposit is held per tenant: on another lease still, theirs may be that lease's.
        SELECT u.display_name AS unit INTO v_still
        FROM trust_lease_tenants AS other
        JOIN trust_leases AS l ON l.id = other.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE other.tenant_id = p_tenant_id AND l.id <> p_lease_id
          AND (l.ends_on IS NULL OR l.ends_on >= p_left_on)
          AND NOT EXISTS (
              SELECT d.tenant_id FROM trust_lease_departures AS d
              WHERE d.lease_id = l.id AND d.tenant_id = p_tenant_id
          )
        ORDER BY l.starts_on, l.id
        LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'trust: % is on the lease of % still, and their deposit stays with it',
                v_name, v_still.unit
                USING ERRCODE = 'check_violation';
        END IF;
        FOR v_deposit IN
            SELECT t.ledger_account_id AS account, t.bank_account_id, a.balance
            FROM trust_ledger_accounts AS t
            JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
            WHERE t.pmc_id = p_pmc_id AND t.tenant_id = p_tenant_id
              AND t.kind = 'tenant_deposit' AND a.balance > 0
            ORDER BY t.ledger_account_id
            FOR UPDATE OF a
        LOOP
            SELECT t.ledger_account_id INTO v_to FROM trust_ledger_accounts AS t
            WHERE t.bank_account_id = v_deposit.bank_account_id AND t.kind = 'tenant_deposit'
              AND t.tenant_id = p_deposit_to;
            IF NOT FOUND THEN
                v_to := trust_open_ledger_account(
                    p_pmc_id, v_deposit.bank_account_id, 'tenant_deposit',
                    p_tenant_id => p_deposit_to
                );
            END IF;
            -- Within one trust bank account: its cash doesn't move.
            PERFORM pgledger_create_transfers(
                ARRAY[(v_deposit.account, v_to, v_deposit.balance)::transfer_request],
                now(),
                jsonb_build_object(
                    'memo', 'Security deposit moved as ' || v_name || ' left the lease',
                    'kind', 'deposit moved', 'lease_id', p_lease_id
                )
            );
            v_moved := v_moved + v_deposit.balance;
        END LOOP;
    END IF;

    INSERT INTO trust_lease_departures (pmc_id, lease_id, tenant_id, left_on, deposit_to, moved)
    VALUES (p_pmc_id, p_lease_id, p_tenant_id, p_left_on, p_deposit_to, v_moved);
    RETURN v_moved;
END;
$$;
REVOKE ALL ON FUNCTION trust_remove_lease_tenant(uuid, uuid, uuid, date, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_remove_lease_tenant(uuid, uuid, uuid, date, uuid) TO trust_app;

-- A lease offer's tenant signers become a new lease's tenants and its guarantor signers its
-- guarantors; a renewal keeps the tenants who haven't left the lease it renews (0080).
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

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_lease_departures
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_lease_occupants
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_lease_pets
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_lease_vehicles
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_lease_guarantors
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_lease_household_removals
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_lease_break_fees
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_departures
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_occupants
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_pets
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_vehicles
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_guarantors
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_household_removals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_break_fees
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_departures
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_occupants
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_pets
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_vehicles
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_guarantors
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_household_removals
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_break_fees
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_lease_departures FROM PUBLIC;
REVOKE ALL ON trust_lease_occupants FROM PUBLIC;
REVOKE ALL ON trust_lease_pets FROM PUBLIC;
REVOKE ALL ON trust_lease_vehicles FROM PUBLIC;
REVOKE ALL ON trust_lease_guarantors FROM PUBLIC;
REVOKE ALL ON trust_lease_household_removals FROM PUBLIC;
REVOKE ALL ON trust_lease_break_fees FROM PUBLIC;
-- Departures are written only by trust_remove_lease_tenant.
GRANT SELECT ON trust_lease_departures TO trust_app;
GRANT SELECT, INSERT ON trust_lease_occupants TO trust_app;
GRANT SELECT, INSERT ON trust_lease_pets TO trust_app;
GRANT SELECT, INSERT ON trust_lease_vehicles TO trust_app;
GRANT SELECT, INSERT ON trust_lease_guarantors TO trust_app;
GRANT SELECT, INSERT ON trust_lease_household_removals TO trust_app;
GRANT SELECT, INSERT ON trust_lease_break_fees TO trust_app;
GRANT SELECT ON trust_lease_departures TO trust_ai_agent;
GRANT SELECT ON trust_lease_break_fees TO trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'lease_people cannot be rolled back: departures and households are records';
END;
$$;
