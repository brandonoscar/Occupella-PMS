-- migrate:up
-- Listing syndication (the founder's decisions of 10/23: "if Buildium has it, we have it"): a
-- public feed of a company's listed units in the formats listing sites take, Zillow's rental
-- listing feed (hotPadsItems 2.1, published on Zillow, Trulia and HotPads) and the MITS
-- Property Marketing/ILS standard (Apartments.com and the other sites; Zillow takes it too). The
-- PMS sends nothing and signs in nowhere: each site fetches the feed's URL, which the company
-- gives it when it signs up with the site.
--
--   trust_listing_feed_choices   staff's choice that a listing on the public page goes in the
--                                feed too, or no longer does: the latest row for a listing
--                                holds (none: not in it), each row changes it, naming who.
--   trust_listing_feed_contacts  the leasing contact the feed names for every listing (sites
--                                ask an email and a phone): the latest row holds, naming who.
--
-- Both append-only and audited. Nothing here moves money. A listing leaves the feed as it leaves
-- the public page (removed, or its link closed): src/pms/api/listing_feeds.py reads only
-- listings the page shows.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_listing_feed_choices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    listing_id uuid NOT NULL,
    in_feed boolean NOT NULL,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, listing_id) REFERENCES trust_listings (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_listing_feed_choices_listing
ON trust_listing_feed_choices (listing_id, created_at DESC, id DESC);
CREATE INDEX trust_listing_feed_choices_pmc ON trust_listing_feed_choices (pmc_id);
CREATE INDEX trust_listing_feed_choices_recorded_by
ON trust_listing_feed_choices (pmc_id, recorded_by);

CREATE TABLE trust_listing_feed_contacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- Who or what a renter reaches ("Leasing office"); none, the company's name.
    contact_name text CHECK (contact_name = btrim(contact_name) AND length(contact_name) BETWEEN 1 AND 100),
    email text NOT NULL
        CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' AND char_length(email) <= 254),
    -- Ten digits, as the sites take a US number.
    phone text NOT NULL CHECK (phone ~ '^[0-9]{10}$'),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_listing_feed_contacts_pmc
ON trust_listing_feed_contacts (pmc_id, created_at DESC, id DESC);
CREATE INDEX trust_listing_feed_contacts_recorded_by
ON trust_listing_feed_contacts (pmc_id, recorded_by);

-- Whether a listing is in the feed now: its latest choice, none meaning no.
CREATE FUNCTION trust_listing_in_feed(p_listing_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce((
        SELECT c.in_feed FROM trust_listing_feed_choices AS c
        WHERE c.listing_id = p_listing_id
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT 1
    ), false)
$$;

REVOKE ALL ON FUNCTION trust_listing_in_feed(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_listing_in_feed(uuid) TO trust_app;

-- A choice changes something: a listing goes in that is out, or comes out that is in. Choices
-- for one listing wait for each other, so two at once can't both put it in.
CREATE FUNCTION trust_check_listing_feed_choice() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('listing feed of ' || NEW.listing_id, 0));
    IF trust_listing_in_feed(NEW.listing_id) = NEW.in_feed THEN
        RAISE EXCEPTION 'trust: this listing is % in the feed',
            CASE WHEN NEW.in_feed THEN 'already' ELSE 'not' END
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_listing_feed_choice BEFORE INSERT ON trust_listing_feed_choices
FOR EACH ROW EXECUTE FUNCTION trust_check_listing_feed_choice();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_listing_feed_choices
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_listing_feed_contacts
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_listing_feed_choices
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_listing_feed_contacts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_listing_feed_choices
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_listing_feed_contacts
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_listing_feed_choices, trust_listing_feed_contacts FROM PUBLIC;
GRANT SELECT, INSERT ON trust_listing_feed_choices, trust_listing_feed_contacts TO trust_app;
-- Which listings go to the sites is what the public page already shows; the leasing contact is
-- the company's, kept from the AI as the company's profile is.
GRANT SELECT ON trust_listing_feed_choices TO trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'listing feeds cannot be rolled back: their choices are records';
END;
$$;
