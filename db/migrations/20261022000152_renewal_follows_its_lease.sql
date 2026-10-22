-- migrate:up
-- A renewal follows the lease it renews (H7, the walkthrough of 2026-10-22): a draft renewal of
-- a lease staff then ended early, whose deposit they returned, could still be sent; both former
-- tenants were emailed signing links and the portal offered them a renewal of a home they had
-- left. From now on a renewal not yet a lease is no longer sent, given a link, acted on or signed
-- through one, signed or made the lease once the lease it renews
--   - ends before the day before the renewal starts (a fixed-term lease ending 12/31 renewed from
--     01/01 is renewed as before, and so is a lease with no end),
--   - was moved from: its tenants moved to another unit (trust_lease_transfers, migration 0091),
--   - or had its deposit returned (trust_deposit_returns, migration 0035).
-- The refusal says why in words; staff void such a renewal. Nothing here moves money.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- Why a renewal, not a lease yet, can no longer be sent, signed or made the lease, in words;
-- NULL when it still can, for an offer that is a lease already and for a new lease's offer.
CREATE FUNCTION trust_renewal_closed(p_offer_id uuid) RETURNS text
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT CASE
        WHEN l.ends_on < o.starts_on - 1 AND l.ends_on < (now() AT TIME ZONE 'UTC')::date THEN
            'the lease it renews ended on ' || to_char(l.ends_on, 'MM/DD/YYYY')
        WHEN l.ends_on < o.starts_on - 1 THEN
            'the lease it renews ends on ' || to_char(l.ends_on, 'MM/DD/YYYY')
            || ', not the day before the renewal starts'
        WHEN t.from_lease_id IS NOT NULL THEN
            'its tenants moved to another unit on ' || to_char(t.moved_on, 'MM/DD/YYYY')
        WHEN r.lease_id IS NOT NULL THEN
            'the deposit of the lease it renews was returned on '
            || to_char(r.returned_at AT TIME ZONE 'UTC', 'MM/DD/YYYY')
    END
    FROM trust_lease_offers AS o
    JOIN trust_leases AS l ON l.id = o.renews_lease_id
    LEFT JOIN trust_lease_transfers AS t ON t.from_lease_id = l.id
    LEFT JOIN trust_deposit_returns AS r ON r.lease_id = l.id
    WHERE o.id = p_offer_id
      AND NOT EXISTS (
          SELECT 1 FROM trust_lease_offer_completions AS c WHERE c.offer_id = o.id
      )
$$;

REVOKE ALL ON FUNCTION trust_renewal_closed(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_renewal_closed(uuid) TO trust_app;

-- A void offer is never sent, signed or made a lease (migration 0071), nor is a renewal that no
-- longer follows the lease it renews. Runs before a signature request, a signing link, a
-- signature and a completion is written.
CREATE OR REPLACE FUNCTION trust_check_lease_offer_not_void() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_offer trust_lease_offers;
    v_closed text;
BEGIN
    SELECT o.* INTO v_offer FROM trust_lease_offers AS o WHERE o.id = NEW.offer_id;
    -- Offers of one application or one lease wait for each other (trust_complete_lease_offer).
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'lease offer of ' || coalesce(v_offer.application_id, v_offer.renews_lease_id), 0
    ));
    IF EXISTS (
        SELECT v.offer_id FROM trust_lease_offer_voids AS v WHERE v.offer_id = NEW.offer_id
    ) THEN
        RAISE EXCEPTION 'trust: this lease offer was voided; it takes nothing more'
            USING ERRCODE = 'check_violation';
    END IF;
    IF v_offer.renews_lease_id IS NOT NULL THEN
        -- The end of the lease it renews waits for this, and this for an end under way
        -- (trust_end_lease takes the lock alone), so the lease is read as it ends.
        PERFORM pg_advisory_xact_lock_shared(
            hashtextextended('lease days of ' || v_offer.pmc_id, 0)
        );
        v_closed := trust_renewal_closed(NEW.offer_id);
        IF v_closed IS NOT NULL THEN
            RAISE EXCEPTION
                'trust: this renewal can no longer be sent, signed or made a lease: %', v_closed
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- Whether a link still signs: made in the last 30 days for an offer sent to sign here, neither
-- void nor a lease, whose signer hasn't signed (migration 0097), and, for a renewal, one that
-- still follows the lease it renews: no step through it is recorded otherwise.
CREATE OR REPLACE FUNCTION trust_signing_link_open(p_link_id uuid) RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM trust_signing_links AS l
        JOIN trust_signature_requests AS r
            ON r.offer_id = l.offer_id AND r.partner = 'PMS e-sign'
        WHERE l.id = p_link_id
          AND l.created_at > now() - interval '30 days'
          AND NOT EXISTS (
              SELECT 1 FROM trust_lease_offer_voids AS v WHERE v.offer_id = l.offer_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM trust_lease_offer_completions AS c WHERE c.offer_id = l.offer_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM trust_signatures AS g
              WHERE g.offer_id = l.offer_id AND g.position = l.position
          )
          AND trust_renewal_closed(l.offer_id) IS NULL
    );
$$;

-- Completing an offer (migration 0031, as 0095 left it), now refusing a renewal that no longer
-- follows the lease it renews before anything is written: a lease ended early would otherwise
-- stay ended while the renewal opened a lease for its former tenants after it.
CREATE OR REPLACE FUNCTION trust_complete_lease_offer(
    p_pmc_id uuid, p_offer_id uuid, p_completed_by uuid
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
    v_closed text;
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
    IF v_offer.renews_lease_id IS NOT NULL THEN
        -- The lease it renews is read as it ends: an end under way (trust_end_lease, which takes
        -- this lock too, below or from elsewhere) commits first or waits for this.
        PERFORM pg_advisory_xact_lock(hashtextextended('lease days of ' || p_pmc_id, 0));
        v_closed := trust_renewal_closed(p_offer_id);
        IF v_closed IS NOT NULL THEN
            RAISE EXCEPTION
                'trust: this renewal can no longer be sent, signed or made a lease: %', v_closed
                USING ERRCODE = 'check_violation';
        END IF;
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
    RAISE EXCEPTION 'renewal_follows_its_lease cannot be rolled back: offers are records';
END;
$$;
