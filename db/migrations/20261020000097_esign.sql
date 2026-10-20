-- migrate:up
-- E-signing built in (ADR 0008, docs/law.md): the PMS hosts the signing of a lease offer itself
-- instead of an outside partner. An offer sent to sign here has its signature request from
-- 'PMS e-sign'. Each signer signs through a link of their own:
--
--   trust_signing_links       a signer's link, its token kept only as its SHA-256, naming the
--                             staff member who made it (nobody when the tenant asked for it in
--                             the portal). A link works for 30 days while its offer is neither
--                             void nor a lease and its signer hasn't signed; a signer may hold
--                             several. Append-only.
--   trust_signing_events      what the signer did through a link, each once: agreed to sign
--                             electronically after reading the consumer disclosures (ESIGN,
--                             15 U.S.C. 7001(c)), whose text is kept with it; opened the lease,
--                             only after agreeing; or asked to sign on paper, after which the
--                             link signs nothing.
--   trust_signature_evidence  the signature: the name typed, the signature drawn if any, the
--                             SHA-256 of the lease signed, the address and browser it came from
--                             and when. It writes the signer's trust_signatures row with it; an
--                             offer sent to sign here takes no signature without its evidence.
--
-- Times are the database's own clock, never the caller's. Like applicants, offers and their
-- signatures, the AI role reads none of it.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_signing_links (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    offer_id uuid NOT NULL,
    position bigint NOT NULL,
    token_sha256 bytea NOT NULL UNIQUE CHECK (length(token_sha256) = 32),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (offer_id, position) REFERENCES trust_lease_offer_signers (offer_id, position),
    FOREIGN KEY (pmc_id, offer_id) REFERENCES trust_lease_offers (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_signing_links_signer ON trust_signing_links (offer_id, position);
CREATE INDEX trust_signing_links_recorded_by ON trust_signing_links (pmc_id, recorded_by);

CREATE TABLE trust_signing_events (
    link_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('consented', 'opened', 'paper')),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- The consumer disclosures the signer read and agreed to, word for word.
    disclosure text CHECK (btrim(disclosure) <> '' AND char_length(disclosure) <= 5000),
    ip inet,
    user_agent text CHECK (char_length(user_agent) <= 500),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (link_id, kind),
    CHECK ((kind = 'consented') = (disclosure IS NOT NULL)),
    FOREIGN KEY (pmc_id, link_id) REFERENCES trust_signing_links (pmc_id, id)
);

CREATE TABLE trust_signature_evidence (
    offer_id uuid NOT NULL,
    position bigint NOT NULL,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    link_id uuid NOT NULL UNIQUE,
    typed_name text NOT NULL CHECK (typed_name ~ '\S' AND char_length(typed_name) <= 200),
    -- The signature drawn, if the signer drew one: a PNG of at most 100 KB.
    drawing bytea CHECK (
        length(drawing) BETWEEN 9 AND 100000
        AND substring(drawing FROM 1 FOR 8) = '\x89504e470d0a1a0a'
    ),
    document_sha256 bytea NOT NULL CHECK (length(document_sha256) = 32),
    ip inet,
    user_agent text CHECK (char_length(user_agent) <= 500),
    signed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (offer_id, position),
    FOREIGN KEY (offer_id, position) REFERENCES trust_lease_offer_signers (offer_id, position),
    FOREIGN KEY (pmc_id, offer_id) REFERENCES trust_lease_offers (pmc_id, id),
    FOREIGN KEY (pmc_id, link_id) REFERENCES trust_signing_links (pmc_id, id)
);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_signing_links
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_signing_links
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_signing_events
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_signature_evidence
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_signing_links
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_signing_events
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_signature_evidence
FOR EACH ROW EXECUTE FUNCTION trust_audit();

-- A void offer takes no new link (and its links sign nothing: trust_signing_link_open).
CREATE TRIGGER trust_lease_offer_not_void
BEFORE INSERT ON trust_signing_links
FOR EACH ROW EXECUTE FUNCTION trust_check_lease_offer_not_void();

-- Whether a link still signs: made in the last 30 days for an offer sent to sign here, neither
-- void nor a lease, whose signer hasn't signed.
CREATE FUNCTION trust_signing_link_open(p_link_id uuid) RETURNS boolean
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
    );
$$;

-- A link is made for a signer of an offer sent to sign here, not a lease yet, who hasn't signed.
CREATE FUNCTION trust_check_signing_link() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM trust_signature_requests AS r
        WHERE r.offer_id = NEW.offer_id AND r.partner = 'PMS e-sign'
    ) THEN
        RAISE EXCEPTION 'trust: this offer is not sent to sign here'
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM trust_lease_offer_completions AS c WHERE c.offer_id = NEW.offer_id
    ) THEN
        RAISE EXCEPTION 'trust: this offer is a lease already' USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM trust_signatures AS g
        WHERE g.offer_id = NEW.offer_id AND g.position = NEW.position
    ) THEN
        RAISE EXCEPTION 'trust: this signer has signed already' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_signing_link_signer
BEFORE INSERT ON trust_signing_links
FOR EACH ROW EXECUTE FUNCTION trust_check_signing_link();

-- Through a link that still signs: agreeing to sign electronically unless the signer asked for
-- paper through it, opening the lease only once agreed, and asking for paper.
CREATE FUNCTION trust_check_signing_event() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    NEW.created_at := clock_timestamp();
    IF NOT trust_signing_link_open(NEW.link_id) THEN
        RAISE EXCEPTION 'trust: this signing link no longer signs; ask for a new one'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.kind <> 'paper' AND EXISTS (
        SELECT 1 FROM trust_signing_events AS e
        WHERE e.link_id = NEW.link_id AND e.kind = 'paper'
    ) THEN
        RAISE EXCEPTION 'trust: the signer asked to sign on paper through this link'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.kind = 'opened' AND NOT EXISTS (
        SELECT 1 FROM trust_signing_events AS e
        WHERE e.link_id = NEW.link_id AND e.kind = 'consented'
    ) THEN
        RAISE EXCEPTION 'trust: the signer agrees to sign electronically before opening the lease'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_signing_event_order
BEFORE INSERT ON trust_signing_events
FOR EACH ROW EXECUTE FUNCTION trust_check_signing_event();

-- A signature through the signer's own link that still signs, once they agreed to sign
-- electronically and opened the lease and haven't asked for paper through it: their name as it
-- is on the offer, on the lease that was sent.
CREATE FUNCTION trust_check_signature_evidence() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_link trust_signing_links;
BEGIN
    NEW.signed_at := clock_timestamp();
    -- Offers of one application or one lease wait for each other (trust_complete_lease_offer),
    -- so a void and a signature do too.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'lease offer of ' || coalesce(o.application_id, o.renews_lease_id), 0
    ))
    FROM trust_lease_offers AS o WHERE o.id = NEW.offer_id;
    SELECT l.* INTO v_link FROM trust_signing_links AS l WHERE l.id = NEW.link_id;
    IF v_link.offer_id IS DISTINCT FROM NEW.offer_id OR v_link.position <> NEW.position THEN
        RAISE EXCEPTION 'trust: this link is another signer''s' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT trust_signing_link_open(NEW.link_id) THEN
        RAISE EXCEPTION 'trust: this signing link no longer signs; ask for a new one'
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM trust_signing_events AS e WHERE e.link_id = NEW.link_id AND e.kind = 'paper'
    ) THEN
        RAISE EXCEPTION 'trust: the signer asked to sign on paper through this link'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM trust_signing_events AS e
        WHERE e.link_id = NEW.link_id AND e.kind = 'consented'
    ) OR NOT EXISTS (
        SELECT 1 FROM trust_signing_events AS e
        WHERE e.link_id = NEW.link_id AND e.kind = 'opened'
    ) THEN
        RAISE EXCEPTION 'trust: the signer agrees to sign electronically and opens the lease first'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM trust_lease_offer_signers AS s
        WHERE s.offer_id = NEW.offer_id AND s.position = NEW.position
          AND lower(btrim(s.display_name)) = lower(btrim(NEW.typed_name))
    ) THEN
        RAISE EXCEPTION 'trust: type your name as it is on the lease'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM trust_lease_offers AS o
        WHERE o.id = NEW.offer_id AND sha256(o.document) = NEW.document_sha256
    ) THEN
        RAISE EXCEPTION 'trust: this is not the lease sent to sign' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_signature_evidence_link
BEFORE INSERT ON trust_signature_evidence
FOR EACH ROW EXECUTE FUNCTION trust_check_signature_evidence();

-- The signature the evidence is for.
CREATE FUNCTION trust_record_evidenced_signature() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    INSERT INTO trust_signatures (pmc_id, offer_id, position, signed_at)
    VALUES (NEW.pmc_id, NEW.offer_id, NEW.position, NEW.signed_at);
    RETURN NULL;
END;
$$;

CREATE TRIGGER trust_signature_evidence_signs
AFTER INSERT ON trust_signature_evidence
FOR EACH ROW EXECUTE FUNCTION trust_record_evidenced_signature();

-- An offer sent to sign here is signed only with its evidence.
CREATE FUNCTION trust_check_signature_evidenced() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM trust_signature_requests AS r
        WHERE r.offer_id = NEW.offer_id AND r.partner = 'PMS e-sign'
    ) AND NOT EXISTS (
        SELECT 1 FROM trust_signature_evidence AS s
        WHERE s.offer_id = NEW.offer_id AND s.position = NEW.position
    ) THEN
        RAISE EXCEPTION 'trust: a signature here is made through the signer''s link'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_signature_evidenced
BEFORE INSERT ON trust_signatures
FOR EACH ROW EXECUTE FUNCTION trust_check_signature_evidenced();

REVOKE ALL ON trust_signing_links, trust_signing_events, trust_signature_evidence FROM PUBLIC;
GRANT SELECT, INSERT ON trust_signing_links, trust_signing_events, trust_signature_evidence
TO trust_app;
REVOKE ALL ON FUNCTION trust_signing_link_open(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_signing_link_open(uuid) TO trust_app;
REVOKE ALL ON FUNCTION trust_check_signing_link() FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_check_signing_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_check_signature_evidence() FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_record_evidenced_signature() FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_check_signature_evidenced() FROM PUBLIC;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'esign cannot be rolled back: signing links, what signers did through them '
        'and their signatures are records';
END;
$$;
