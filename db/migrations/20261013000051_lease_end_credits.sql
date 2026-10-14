-- migrate:up
-- Credit the days after a lease's end (founder decisions, 2026-10-13: "Credit unused days";
-- "Automatically"; "Follow the new end"; "Only the rent run's"). A lease whose end is set, moved
-- or cleared after the rent run charged a month keeps each charged month at the days it is in
-- effect, by actual days as the run charges them (trust_rent_for_month).
--
--   trust_end_lease
--       after setting the end, takes each charge the run makes (rent, and a recurring charge's
--       fee or credit) that, with what was added to or taken off it before, stands at its
--       month's days under the old end, and adds a credit or a fee, due on the charge's own day,
--       so it stands at its month's days under the new end: a month wholly after the end comes
--       to nothing. An earlier end credits the days after it; a later end, or none, charges
--       them back. A charge that stands at anything else (entered by hand, carried over at a
--       different amount) is left for staff. A renewal ends the old lease this way too.
--   trust_lease_end_adjustments
--       each such credit or fee: the charge it adjusts and the end it follows.
--   trust_complete_lease_offer
--       a renewal starting in a month whose rent the old lease was charged also charges the new
--       lease its days of that month, due on its first day, so the month is whole.
--   trust_charge_rent_due, trust_charge_recurring_due
--       take a shared lock that trust_end_lease takes alone, so a run and a lease's end don't
--       interleave: a run charges by the end that is committed, and an end sees every charge.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_lease_end_adjustments (
    adjustment_charge_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    charge_id uuid NOT NULL,
    ends_on date,  -- the lease's end it follows; NULL: no end
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, adjustment_charge_id) REFERENCES trust_charges (pmc_id, id),
    FOREIGN KEY (pmc_id, charge_id) REFERENCES trust_charges (pmc_id, id)
);
CREATE INDEX trust_lease_end_adjustments_charge_id ON trust_lease_end_adjustments (charge_id);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_end_adjustments
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_end_adjustments
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_lease_end_adjustments FROM PUBLIC;
GRANT SELECT ON trust_lease_end_adjustments TO trust_app, trust_ai_agent;

CREATE OR REPLACE FUNCTION trust_end_lease(
    p_pmc_id uuid,
    p_lease_id uuid,
    p_ends_on date
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
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

    -- Each charge the rent run makes (rent, and a recurring charge's) that, with what was added
    -- or taken off it before, stands at its month's days under the old end: added to or taken
    -- off so it stands at its month's days under the new end.
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
                   (CASE WHEN c.kind = 'credit' THEN -1 ELSE 1 END) * greatest((
                       SELECT r.amount FROM trust_rent_for_month(
                           v_old.starts_on, v_old.ends_on,
                           coalesce(rc.amount, v_old.monthly_rent), c.due_on
                       ) AS r
                   ), 0) AS was,
                   (CASE WHEN c.kind = 'credit' THEN -1 ELSE 1 END) * greatest((
                       SELECT r.amount FROM trust_rent_for_month(
                           v_new.starts_on, v_new.ends_on,
                           coalesce(rc.amount, v_new.monthly_rent), c.due_on
                       ) AS r
                   ), 0) AS now
            FROM trust_charges AS c
            LEFT JOIN trust_recurring_charges AS rc ON rc.id = c.recurring_charge_id
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
END;
$$;

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
            WHERE s.offer_id = p_offer_id
            ORDER BY s.position
            RETURNING id
        )
        SELECT array_agg(m.id) INTO v_tenants FROM made AS m;
    ELSE
        SELECT l.* INTO v_old FROM trust_leases AS l WHERE l.id = v_offer.renews_lease_id;
        SELECT array_agg(t.tenant_id) INTO v_tenants
        FROM trust_lease_tenants AS t WHERE t.lease_id = v_old.id;
        IF v_old.ends_on IS NULL OR v_old.ends_on >= v_offer.starts_on THEN
            PERFORM trust_end_lease(p_pmc_id, v_old.id, v_offer.starts_on - 1);
        END IF;
    END IF;

    v_lease := trust_open_lease(
        p_pmc_id, v_offer.unit_id, v_offer.starts_on, v_offer.ends_on, v_offer.monthly_rent,
        v_tenants
    );
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

CREATE OR REPLACE FUNCTION trust_charge_rent_due(p_pmc_id uuid, p_due_on date)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_charged integer;
    v_first integer;
BEGIN
    IF p_due_on IS NULL THEN
        RAISE EXCEPTION 'trust: rent is charged for a due date; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Leases' ends don't change while the run charges (trust_end_lease waits for it).
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('lease days of ' || p_pmc_id, 0));

    -- Every lease in effect on the due date: its rent for the days of the month it is in effect.
    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    SELECT l.pmc_id, l.id, p_due_on, 'rent', r.amount,
           'Rent due ' || to_char(p_due_on, 'YYYY-MM-DD') || r.days
    FROM trust_leases AS l
    CROSS JOIN LATERAL trust_rent_for_month(
        l.starts_on, l.ends_on, l.monthly_rent, p_due_on
    ) AS r
    WHERE l.pmc_id = p_pmc_id AND l.starts_on <= p_due_on
      AND (l.ends_on IS NULL OR p_due_on <= l.ends_on)
      AND r.amount > 0  -- a day of a few cents' rent rounds to nothing
    ORDER BY l.id
    ON CONFLICT (lease_id, due_on) WHERE kind = 'rent' DO NOTHING;
    GET DIAGNOSTICS v_charged = ROW_COUNT;

    -- Every lease that starts later this month, with no rent charged in the month yet: its first
    -- month's rent, due on its first day.
    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    SELECT l.pmc_id, l.id, l.starts_on, 'rent', r.amount,
           'Rent due ' || to_char(l.starts_on, 'YYYY-MM-DD') || r.days
    FROM trust_leases AS l
    CROSS JOIN LATERAL trust_rent_for_month(
        l.starts_on, l.ends_on, l.monthly_rent, l.starts_on
    ) AS r
    WHERE l.pmc_id = p_pmc_id AND l.starts_on > p_due_on AND r.amount > 0
      AND date_trunc('month', l.starts_on::timestamp) = date_trunc('month', p_due_on::timestamp)
      AND NOT EXISTS (
          SELECT c.id FROM trust_charges AS c
          WHERE c.lease_id = l.id AND c.kind = 'rent'
            AND date_trunc('month', c.due_on::timestamp)
                = date_trunc('month', l.starts_on::timestamp)
      )
    ORDER BY l.id
    ON CONFLICT (lease_id, due_on) WHERE kind = 'rent' DO NOTHING;
    GET DIAGNOSTICS v_first = ROW_COUNT;
    RETURN v_charged + v_first;
END;
$$;

CREATE OR REPLACE FUNCTION trust_charge_recurring_due(p_pmc_id uuid, p_due_on date)
RETURNS integer
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_charged integer;
    v_first integer;
BEGIN
    IF p_due_on IS NULL THEN
        RAISE EXCEPTION 'trust: recurring charges are charged for a due date; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Leases' ends don't change while the run charges (trust_end_lease waits for it).
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('lease days of ' || p_pmc_id, 0));

    -- Every recurring charge in force on the due date whose lease is in effect then: its amount
    -- for the days of the month the lease is in effect.
    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, recurring_charge_id)
    SELECT r.pmc_id, r.lease_id, p_due_on, r.kind, p.amount,
           r.memo || ' due ' || to_char(p_due_on, 'YYYY-MM-DD') || p.days, r.id
    FROM trust_recurring_charges AS r
    JOIN trust_leases AS l ON l.id = r.lease_id
    LEFT JOIN trust_recurring_charge_ends AS e ON e.recurring_charge_id = r.id
    CROSS JOIN LATERAL trust_rent_for_month(l.starts_on, l.ends_on, r.amount, p_due_on) AS p
    WHERE r.pmc_id = p_pmc_id AND r.starts_on <= p_due_on
      AND (e.ends_on IS NULL OR p_due_on < e.ends_on)
      AND l.starts_on <= p_due_on AND (l.ends_on IS NULL OR p_due_on <= l.ends_on)
      AND p.amount > 0  -- a day of a few cents rounds to nothing
    ORDER BY r.id
    ON CONFLICT (recurring_charge_id, due_on) WHERE recurring_charge_id IS NOT NULL DO NOTHING;
    GET DIAGNOSTICS v_charged = ROW_COUNT;

    -- Every recurring charge in force on the first day of a lease that starts later this month,
    -- with nothing charged for it in the month yet: its first part month, due on that day.
    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, recurring_charge_id)
    SELECT r.pmc_id, r.lease_id, l.starts_on, r.kind, p.amount,
           r.memo || ' due ' || to_char(l.starts_on, 'YYYY-MM-DD') || p.days, r.id
    FROM trust_recurring_charges AS r
    JOIN trust_leases AS l ON l.id = r.lease_id
    LEFT JOIN trust_recurring_charge_ends AS e ON e.recurring_charge_id = r.id
    CROSS JOIN LATERAL trust_rent_for_month(l.starts_on, l.ends_on, r.amount, l.starts_on) AS p
    WHERE r.pmc_id = p_pmc_id AND l.starts_on > p_due_on
      AND date_trunc('month', l.starts_on::timestamp) = date_trunc('month', p_due_on::timestamp)
      AND r.starts_on <= l.starts_on AND (e.ends_on IS NULL OR l.starts_on < e.ends_on)
      AND p.amount > 0
      AND NOT EXISTS (
          SELECT c.id FROM trust_charges AS c
          WHERE c.recurring_charge_id = r.id
            AND date_trunc('month', c.due_on::timestamp)
                = date_trunc('month', l.starts_on::timestamp)
      )
    ORDER BY r.id
    ON CONFLICT (recurring_charge_id, due_on) WHERE recurring_charge_id IS NOT NULL DO NOTHING;
    GET DIAGNOSTICS v_first = ROW_COUNT;
    RETURN v_charged + v_first;
END;
$$;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'lease_end_credits cannot be rolled back: its credits are records';
END;
$$;
