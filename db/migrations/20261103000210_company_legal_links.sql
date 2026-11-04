-- migrate:up

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- The company's own terms of use and privacy notice, as links (the founder, 11/03): a self-hosted
-- PMS is the company's, which answers to its tenants, owners and applicants for what it keeps
-- of them, so the words are the company's and its lawyer's; the PMS writes none, as it writes no
-- lease. Staff set them in Company setup; the portals' company card, the apply page and the
-- signing page show them. A new profile row, as an address change is; an https link only.
ALTER TABLE public.trust_company_profiles
    ADD COLUMN terms_url text
        CONSTRAINT trust_company_profiles_terms_url_check
        CHECK (terms_url ~ '^https://[^[:space:]]+$' AND char_length(terms_url) <= 500),
    ADD COLUMN privacy_url text
        CONSTRAINT trust_company_profiles_privacy_url_check
        CHECK (privacy_url ~ '^https://[^[:space:]]+$' AND char_length(privacy_url) <= 500);

-- migrate:down

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- The two columns this migration added, and nothing reads them before it.
-- squawk-ignore ban-drop-column
ALTER TABLE public.trust_company_profiles DROP COLUMN terms_url, DROP COLUMN privacy_url;
