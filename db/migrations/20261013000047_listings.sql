-- migrate:up
-- Vacancy listings (founder decision, 2026-10-13, product item 8): a public page of a PMC's open
-- units with their application links. Not syndication: nothing is sent anywhere; the page is
-- there for whoever the PMC points to it.
--
--   trust_listings           an open application link staff put on the PMC's public page, with
--                            an optional description. Its id is what the page links to: anyone
--                            who opens the page may apply through it, as through the link.
--   trust_listing_removals   a listing taken off the page.
--
-- A listing shows while it is not removed and its link is open; closing the link ends it too.
-- An application link takes at most one listing. Records: kept for good.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_listings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    link_id uuid NOT NULL UNIQUE,
    description text CHECK (description ~ '\S' AND length(description) <= 2000),
    listed_by uuid NOT NULL,
    listed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, link_id) REFERENCES trust_application_links (pmc_id, id),
    FOREIGN KEY (pmc_id, listed_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TABLE trust_listing_removals (
    listing_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    removed_by uuid NOT NULL,
    removed_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, listing_id) REFERENCES trust_listings (pmc_id, id),
    FOREIGN KEY (pmc_id, removed_by) REFERENCES trust_staff (pmc_id, id)
);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_listings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_listing_removals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_listings
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_listing_removals
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_listings, trust_listing_removals FROM PUBLIC;
GRANT SELECT, INSERT ON trust_listings, trust_listing_removals TO trust_app;
-- What a public page shows; nothing of an application or an applicant.
GRANT SELECT ON trust_listings, trust_listing_removals TO trust_ai_agent;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'listings cannot be rolled back: listings are records';
END;
$$;
