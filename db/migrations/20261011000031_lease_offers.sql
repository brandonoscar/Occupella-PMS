-- migrate:up
-- Lease offers, e-signing and completing them: leasing basics, part two (founder, 2026-10-11:
-- docs/adr/0002-portals-and-exports.md; e-sign is built against a fake partner).
--
--   trust_lease_offers            the terms staff offer: a new lease for an approved application,
--                                 or the renewal of a lease, at its unit, with the PMC's own
--                                 lease document (a PDF) the signers sign. The PMS writes no
--                                 lease text of its own.
--   trust_lease_offer_signers     who signs it, in order: a name and an email.
--   trust_signature_requests      the offer as sent to the e-sign partner, once.
--   trust_signatures              each signer's signature, as the partner reported it.
--   trust_lease_offer_completions the lease an offer became, once: trust_complete_lease_offer
--                                 opens it when everyone has signed (a new lease's applicants
--                                 become tenants; a renewal ends the lease it renews the day
--                                 before and keeps its tenants).
--
-- Every table is append-only and audited; no AI role reads any of them.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_lease_offers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    application_id uuid,
    renews_lease_id uuid,
    unit_id uuid NOT NULL,
    starts_on date NOT NULL,
    ends_on date,
    monthly_rent numeric NOT NULL CHECK (monthly_rent > 0),
    document_title text NOT NULL CHECK (document_title ~ '\S' AND length(document_title) <= 200),
    -- The PMC's lease, as uploaded: a PDF of at most 5 MB.
    document bytea NOT NULL CHECK (
        length(document) BETWEEN 5 AND 5000000 AND substring(document FROM 1 FOR 5) = '\x255044462d'
    ),
    offered_by uuid NOT NULL,
    offered_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    CHECK ((application_id IS NULL) <> (renews_lease_id IS NULL)),
    CHECK (ends_on >= starts_on),
    FOREIGN KEY (pmc_id, application_id) REFERENCES trust_applications (pmc_id, id),
    FOREIGN KEY (pmc_id, renews_lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, unit_id) REFERENCES trust_units (pmc_id, id),
    FOREIGN KEY (pmc_id, offered_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_lease_offers_application_id ON trust_lease_offers (application_id);
CREATE INDEX trust_lease_offers_renews_lease_id ON trust_lease_offers (renews_lease_id);

CREATE TABLE trust_lease_offer_signers (
    offer_id uuid NOT NULL,
    position bigint NOT NULL CHECK (position BETWEEN 1 AND 6),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    display_name text NOT NULL CHECK (display_name ~ '\S' AND length(display_name) <= 200),
    email text NOT NULL CHECK (
        email = lower(email) AND email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' AND length(email) <= 254
    ),
    PRIMARY KEY (offer_id, position),
    UNIQUE (offer_id, email),
    FOREIGN KEY (pmc_id, offer_id) REFERENCES trust_lease_offers (pmc_id, id)
);

CREATE TABLE trust_signature_requests (
    offer_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    partner text NOT NULL CHECK (partner ~ '\S'),
    partner_reference text NOT NULL CHECK (partner_reference ~ '\S'),
    sent_by uuid NOT NULL,
    sent_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, offer_id) REFERENCES trust_lease_offers (pmc_id, id),
    FOREIGN KEY (pmc_id, sent_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TABLE trust_signatures (
    offer_id uuid NOT NULL,
    position bigint NOT NULL,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    signed_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (offer_id, position),
    FOREIGN KEY (offer_id, position) REFERENCES trust_lease_offer_signers (offer_id, position),
    -- A signature is the partner's word on an offer that was sent to it.
    FOREIGN KEY (offer_id) REFERENCES trust_signature_requests (offer_id),
    FOREIGN KEY (pmc_id, offer_id) REFERENCES trust_lease_offers (pmc_id, id)
);

CREATE TABLE trust_lease_offer_completions (
    offer_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    lease_id uuid NOT NULL UNIQUE,
    completed_by uuid NOT NULL,
    completed_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, offer_id) REFERENCES trust_lease_offers (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, completed_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_offers
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_offer_signers
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_signature_requests
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_signatures
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_lease_offer_completions
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

-- An offer is for an application the PMC approved (outright or on conditions), at the unit it
-- applied for; or it renews a lease at its unit, starting after that lease does.
CREATE FUNCTION trust_check_lease_offer() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_unit uuid;
    v_starts date;
BEGIN
    IF NEW.application_id IS NOT NULL THEN
        SELECT l.unit_id INTO v_unit
        FROM trust_applications AS a
        JOIN trust_application_links AS l ON l.id = a.link_id
        JOIN trust_application_decisions AS d ON d.application_id = a.id
        WHERE a.id = NEW.application_id AND d.decision IN ('approved', 'conditional');
        IF NOT FOUND THEN
            RAISE EXCEPTION 'trust: application % is not approved; offer it no lease',
                NEW.application_id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT l.unit_id, l.starts_on INTO v_unit, v_starts
        FROM trust_leases AS l WHERE l.id = NEW.renews_lease_id;
        IF NEW.starts_on <= v_starts THEN
            RAISE EXCEPTION 'trust: a renewal of lease % starts after %', NEW.renews_lease_id,
                v_starts
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.unit_id IS DISTINCT FROM v_unit THEN
        RAISE EXCEPTION 'trust: the offer is for unit %, not %', v_unit, NEW.unit_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_lease_offer_terms
BEFORE INSERT ON trust_lease_offers
FOR EACH ROW EXECUTE FUNCTION trust_check_lease_offer();

-- Opens the lease an offer was for, once everyone signed: a new lease's signers become tenants
-- of the unit's property; a renewal keeps the lease's tenants and ends that lease the day
-- before (if it runs that long). Once per offer, and safe to retry: the same offer returns the
-- lease it opened. An application, or a lease, takes one completed offer.
CREATE FUNCTION trust_complete_lease_offer(
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
    INSERT INTO trust_lease_offer_completions (pmc_id, offer_id, lease_id, completed_by)
    VALUES (p_pmc_id, p_offer_id, v_lease, p_completed_by);
    RETURN v_lease;
END;
$$;

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_offers
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_offer_signers
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_signature_requests
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_signatures
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_lease_offer_completions
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_lease_offers, trust_lease_offer_signers, trust_signature_requests,
trust_signatures, trust_lease_offer_completions FROM PUBLIC;
GRANT SELECT, INSERT ON trust_lease_offers, trust_lease_offer_signers, trust_signature_requests,
trust_signatures TO trust_app;
-- Completions are written only by trust_complete_lease_offer.
GRANT SELECT ON trust_lease_offer_completions TO trust_app;

REVOKE ALL ON FUNCTION trust_complete_lease_offer(uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_complete_lease_offer(uuid, uuid, uuid) TO trust_app;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'lease_offers cannot be rolled back: offers, signatures and the leases they '
        'opened are records';
END;
$$;
