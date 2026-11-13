-- migrate:up
-- Stripe Connect for the hosted product (docs/adr/0014-stripe-connect.md, ADR 0009's "Hosted,
-- free"): each company connects its own Stripe account to Occupella's platform from Company setup
-- (Stripe-hosted onboarding, no keys pasted), and its tenants and owners pay by bank or card
-- through it by direct charges, Occupella's fee an application fee on each. The money lands in the
-- company's own Stripe balance and pays out to its own trust bank account; it never passes through
-- Occupella's (src/pms/connect.py, src/pms/api/connect.py).
--
--   trust_connected_accounts        each company's Stripe account, as an admin connected it (Stripe's
--                                   account id, never a key), append-only, the latest holding; a row
--                                   with none disconnects it. An account belongs to one company.
--   trust_connected_account_states  what Stripe said of an account (account.updated, or the PMS
--                                   asking on the admin's return from onboarding): whether it takes
--                                   charges and pays out, and what Stripe still needs.
--   trust_connected_payments        the connected account each payment, owner's payment or autopay
--                                   was started on, so a report from another account never lands.
--   trust_card_fee_settings         who pays the card fee: the company (the default) or the tenant,
--                                   where the property's state and the card networks allow it.
--   trust_connect_disputes          a card dispute (or a bank's late return, as Stripe reports it)
--                                   opened, won or lost, with the dispute fee Stripe took from the
--                                   company's account or gave back.
--
-- And on the tables that were bank only: a payment may be by card (through Connect only, at most
-- 10,000.00 at a time), names Occupella's fee on it (platform_fee: 0 off Connect), and an autopay
-- says how it pays. The AI role reads the fee, the method, the card fee choice and the disputes;
-- never a connected account or its state (credential-adjacent: docs/adr/0014-stripe-connect.md).

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- --- what was bank only ---------------------------------------------------------------------

-- Every payment there is was a bank payment off Connect, so each holds the new checks as it comes.
ALTER TABLE trust_online_payments ADD COLUMN platform_fee numeric NOT NULL DEFAULT 0;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_online_payments ADD CONSTRAINT trust_online_payments_platform_fee_check
CHECK (platform_fee >= 0 AND platform_fee <= 1000 AND platform_fee = round(platform_fee, 2));
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_online_payments ADD CONSTRAINT trust_online_payments_method_v2
CHECK (method IN ('ach', 'card'));
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_online_payments DROP CONSTRAINT trust_online_payments_method_check;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_online_payments ADD CONSTRAINT trust_online_payments_partner_v2
CHECK (partner IN ('fake', 'stripe', 'fake_connect', 'stripe_connect'));
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_online_payments DROP CONSTRAINT trust_online_payments_partner_check;
-- A card, and Occupella's fee, only through Connect; a card payment of 10,000.00 at most.
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_online_payments ADD CONSTRAINT trust_online_payments_connect_only CHECK (
    partner IN ('fake_connect', 'stripe_connect')
    OR (method = 'ach' AND platform_fee = 0)
);
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_online_payments ADD CONSTRAINT trust_online_payments_card_amount
CHECK (method = 'ach' OR amount <= 10000);

ALTER TABLE trust_owner_online_payments ADD COLUMN platform_fee numeric NOT NULL DEFAULT 0;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_owner_online_payments ADD CONSTRAINT trust_owner_online_payments_platform_fee_check
CHECK (platform_fee >= 0 AND platform_fee <= 1000 AND platform_fee = round(platform_fee, 2));
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_owner_online_payments ADD CONSTRAINT trust_owner_online_payments_method_v2
CHECK (method IN ('ach', 'card'));
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_owner_online_payments DROP CONSTRAINT trust_owner_online_payments_method_check;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_owner_online_payments ADD CONSTRAINT trust_owner_online_payments_partner_v2
CHECK (partner IN ('fake', 'stripe', 'fake_connect', 'stripe_connect'));
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_owner_online_payments DROP CONSTRAINT trust_owner_online_payments_partner_check;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_owner_online_payments ADD CONSTRAINT trust_owner_online_payments_connect_only
CHECK (
    partner IN ('fake_connect', 'stripe_connect')
    OR (method = 'ach' AND platform_fee = 0)
);
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_owner_online_payments ADD CONSTRAINT trust_owner_online_payments_card_amount
CHECK (method = 'ach' OR amount <= 10000);

ALTER TABLE trust_autopay ADD COLUMN method text NOT NULL DEFAULT 'ach';
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_autopay ADD CONSTRAINT trust_autopay_method_check
CHECK (method IN ('ach', 'card'));
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_autopay ADD CONSTRAINT trust_autopay_partner_v2
CHECK (partner IN ('fake', 'stripe', 'fake_connect', 'stripe_connect'));
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_autopay DROP CONSTRAINT trust_autopay_partner_check;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_autopay ADD CONSTRAINT trust_autopay_connect_only
CHECK (partner IN ('fake_connect', 'stripe_connect') OR method = 'ach');

-- An autopay saved at a connected account stops, with an email, when the company disconnects that
-- account or connects another: it can't be charged at another account (src/pms/api/connect.py).
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_autopay_stops ADD CONSTRAINT trust_autopay_stops_why_v2 CHECK (
    why IN (
        'tenant', 'staff', 'lease ended', 'tenant left', 'failed twice', 'replaced',
        'account changed'
    )
);
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_autopay_stops DROP CONSTRAINT trust_autopay_stops_why_check;
-- Staff's stop names them, a stop by itself nobody; one for an account changed names the admin who
-- disconnected it, or nobody when the nightly run finds another account connected.
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_autopay_stops ADD CONSTRAINT trust_autopay_stops_who_v2 CHECK (
    why = 'account changed' OR (why = 'staff') = (recorded_by IS NOT NULL)
);
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_autopay_stops DROP CONSTRAINT trust_autopay_stops_check;

-- --- a company's connected account ------------------------------------------------------------

CREATE TABLE trust_connected_accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- Stripe's id of the company's own account, or none: disconnected.
    account_id text CHECK (account_id ~ '^(acct|fake_acct)_[A-Za-z0-9]{6,64}$'),
    -- The platform it was connected through (the fake one in development and CI).
    partner text NOT NULL CHECK (partner IN ('fake_connect', 'stripe_connect')),
    -- Whether it was made with the platform's live key: a test account takes no real money.
    livemode boolean NOT NULL,
    -- The admin who connected or disconnected it: always named.
    recorded_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_connected_accounts_pmc ON trust_connected_accounts (pmc_id, created_at);
CREATE INDEX trust_connected_accounts_account ON trust_connected_accounts (account_id);
CREATE INDEX trust_connected_accounts_recorded_by
ON trust_connected_accounts (pmc_id, recorded_by);

-- Only an admin connects or disconnects the company's Stripe account, and an account is one
-- company's: never connected to another. Takes the account's lock, so two companies connecting one
-- account at once wait for each other.
CREATE FUNCTION trust_check_connected_account() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM trust_require_role(
        NEW.pmc_id, NEW.recorded_by, 'admin', 'connect the company''s Stripe account'
    );
    IF NEW.account_id IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(
            hashtext('trust_connected_accounts'), hashtext(NEW.account_id)
        );
        IF EXISTS (
            SELECT 1 FROM trust_connected_accounts AS c
            WHERE c.account_id = NEW.account_id AND c.pmc_id <> NEW.pmc_id
        ) THEN
            RAISE EXCEPTION 'trust: that Stripe account is another company''s'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_connected_account() FROM PUBLIC;

-- Named after trust_recorded_by, so it fires once the row names who connected it.
CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_connected_accounts
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_set_by_admin BEFORE INSERT ON trust_connected_accounts
FOR EACH ROW EXECUTE FUNCTION trust_check_connected_account();

CREATE TABLE trust_connected_account_states (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    account_id text NOT NULL CHECK (length(account_id) BETWEEN 1 AND 100),
    charges_enabled boolean NOT NULL,
    payouts_enabled boolean NOT NULL,
    details_submitted boolean NOT NULL,
    -- What Stripe still needs before it takes charges or pays out (its own field names).
    requirements_due text[] NOT NULL DEFAULT '{}'
        CHECK (cardinality(requirements_due) <= 100),
    -- Stripe's event that said so; none when the PMS asked Stripe itself.
    partner_event text UNIQUE CHECK (length(partner_event) BETWEEN 1 AND 200),
    -- When Stripe said so: the latest said holds, whatever order the reports came in.
    reported_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX trust_connected_account_states_account
ON trust_connected_account_states (pmc_id, account_id, reported_at);

-- A state is of an account the company connected.
CREATE FUNCTION trust_check_connected_account_state() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM trust_connected_accounts AS c
        WHERE c.pmc_id = NEW.pmc_id AND c.account_id = NEW.account_id
    ) THEN
        RAISE EXCEPTION 'trust: that Stripe account isn''t one this company connected'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_connected_account_state() FROM PUBLIC;

CREATE TRIGGER trust_check_connected_account_state
BEFORE INSERT ON trust_connected_account_states
FOR EACH ROW EXECUTE FUNCTION trust_check_connected_account_state();

CREATE TABLE trust_connected_payments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    account_id text NOT NULL CHECK (length(account_id) BETWEEN 1 AND 100),
    online_payment_id uuid UNIQUE,
    owner_payment_id uuid UNIQUE,
    autopay_id uuid UNIQUE,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (num_nonnulls(online_payment_id, owner_payment_id, autopay_id) = 1),
    FOREIGN KEY (pmc_id, online_payment_id) REFERENCES trust_online_payments (pmc_id, id),
    FOREIGN KEY (pmc_id, owner_payment_id) REFERENCES trust_owner_online_payments (pmc_id, id),
    FOREIGN KEY (pmc_id, autopay_id) REFERENCES trust_autopay (pmc_id, id)
);
CREATE INDEX trust_connected_payments_account ON trust_connected_payments (pmc_id, account_id);

-- A payment is started on an account its company connected, through a Connect partner.
CREATE FUNCTION trust_check_connected_payment() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_partner text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM trust_connected_accounts AS c
        WHERE c.pmc_id = NEW.pmc_id AND c.account_id = NEW.account_id
    ) THEN
        RAISE EXCEPTION 'trust: that Stripe account isn''t one this company connected'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT coalesce(
        (SELECT p.partner FROM trust_online_payments AS p WHERE p.id = NEW.online_payment_id),
        (SELECT p.partner FROM trust_owner_online_payments AS p WHERE p.id = NEW.owner_payment_id),
        (SELECT a.partner FROM trust_autopay AS a WHERE a.id = NEW.autopay_id)
    ) INTO v_partner;
    IF v_partner IS DISTINCT FROM 'stripe_connect' AND v_partner IS DISTINCT FROM 'fake_connect'
    THEN
        RAISE EXCEPTION 'trust: only a payment through Connect is started on a connected account'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_connected_payment() FROM PUBLIC;

CREATE TRIGGER trust_check_connected_payment
BEFORE INSERT ON trust_connected_payments
FOR EACH ROW EXECUTE FUNCTION trust_check_connected_payment();

-- --- who pays the card fee --------------------------------------------------------------------

CREATE TABLE trust_card_fee_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    card_fee_payer text NOT NULL CHECK (card_fee_payer IN ('company', 'tenant')),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_card_fee_settings_pmc ON trust_card_fee_settings (pmc_id, created_at);
CREATE INDEX trust_card_fee_settings_recorded_by ON trust_card_fee_settings (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_card_fee_settings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

-- --- disputes ---------------------------------------------------------------------------------

CREATE TABLE trust_connect_disputes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    online_payment_id uuid,
    owner_payment_id uuid,
    -- Stripe's id of the dispute.
    dispute_id text NOT NULL CHECK (length(dispute_id) BETWEEN 1 AND 200),
    kind text NOT NULL CHECK (kind IN ('opened', 'won', 'lost')),
    -- Opened: the dispute fee Stripe took from the company's balance. Won: what of it came back.
    fee numeric CHECK (fee >= 0 AND fee <= 1000 AND fee = round(fee, 2)),
    partner_event text NOT NULL CHECK (length(partner_event) BETWEEN 1 AND 200),
    happened_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (num_nonnulls(online_payment_id, owner_payment_id) = 1),
    CHECK ((kind = 'lost') = (fee IS NULL)),
    UNIQUE (online_payment_id, kind),
    UNIQUE (owner_payment_id, kind),
    FOREIGN KEY (pmc_id, online_payment_id) REFERENCES trust_online_payments (pmc_id, id),
    FOREIGN KEY (pmc_id, owner_payment_id) REFERENCES trust_owner_online_payments (pmc_id, id)
);
CREATE INDEX trust_connect_disputes_pmc ON trust_connect_disputes (pmc_id, created_at);

-- A dispute opens on a payment that came back (its 'returned' report is in), and closes once,
-- won or lost, after it opened. Takes the payment's lock, which its reports take too.
CREATE FUNCTION trust_check_connect_dispute() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_payment uuid := coalesce(NEW.online_payment_id, NEW.owner_payment_id);
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('trust_connect_disputes'), hashtext(v_payment::text));
    IF NEW.kind = 'opened' AND NOT EXISTS (
        SELECT 1 FROM trust_online_payment_events AS e
        WHERE e.payment_id = NEW.online_payment_id AND e.kind = 'returned'
        UNION ALL
        SELECT 1 FROM trust_owner_online_payment_events AS e
        WHERE e.payment_id = NEW.owner_payment_id AND e.kind = 'returned'
    ) THEN
        RAISE EXCEPTION 'trust: payment % didn''t come back, so no dispute opens on it', v_payment
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.kind <> 'opened' AND NOT EXISTS (
        SELECT 1 FROM trust_connect_disputes AS d
        WHERE coalesce(d.online_payment_id, d.owner_payment_id) = v_payment
          AND d.kind = 'opened' AND d.dispute_id = NEW.dispute_id
    ) THEN
        RAISE EXCEPTION 'trust: dispute % isn''t open, so it can''t close', NEW.dispute_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.kind <> 'opened' AND EXISTS (
        SELECT 1 FROM trust_connect_disputes AS d
        WHERE coalesce(d.online_payment_id, d.owner_payment_id) = v_payment
          AND d.kind IN ('won', 'lost')
    ) THEN
        RAISE EXCEPTION 'trust: dispute % closed already', NEW.dispute_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_connect_dispute() FROM PUBLIC;

CREATE TRIGGER trust_check_connect_dispute
BEFORE INSERT ON trust_connect_disputes
FOR EACH ROW EXECUTE FUNCTION trust_check_connect_dispute();

-- --- every new table: append-only, audited, the app's alone to write -------------------------

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_connected_accounts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_connected_account_states
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_connected_payments
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_card_fee_settings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_connect_disputes
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_connected_accounts
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_connected_account_states
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_connected_payments
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_card_fee_settings
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_connect_disputes
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_connected_accounts FROM PUBLIC;
REVOKE ALL ON trust_connected_account_states FROM PUBLIC;
REVOKE ALL ON trust_connected_payments FROM PUBLIC;
REVOKE ALL ON trust_card_fee_settings FROM PUBLIC;
REVOKE ALL ON trust_connect_disputes FROM PUBLIC;
GRANT SELECT, INSERT ON trust_connected_accounts TO trust_app;
GRANT SELECT, INSERT ON trust_connected_account_states TO trust_app;
GRANT SELECT, INSERT ON trust_connected_payments TO trust_app;
GRANT SELECT, INSERT ON trust_card_fee_settings TO trust_app;
GRANT SELECT, INSERT ON trust_connect_disputes TO trust_app;
-- The AI role reads who pays the card fee and the disputes (amounts and payments), as it reads
-- payments online; never a connected account, its state or which account a payment went to.
GRANT SELECT ON trust_card_fee_settings TO trust_ai_agent;
GRANT SELECT ON trust_connect_disputes TO trust_ai_agent;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- A connected account is where a company's money went; undoing the tables would leave the
-- payments it took without that record.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_connected_accounts)
        OR EXISTS (SELECT 1 FROM trust_online_payments WHERE method <> 'ach' OR platform_fee <> 0)
        OR EXISTS (
            SELECT 1 FROM trust_owner_online_payments WHERE method <> 'ach' OR platform_fee <> 0
        )
        OR EXISTS (SELECT 1 FROM trust_autopay WHERE method <> 'ach')
        OR EXISTS (SELECT 1 FROM trust_autopay_stops WHERE why = 'account changed')
    THEN
        RAISE EXCEPTION 'trust: a company connected its Stripe account; this migration is not'
            ' rolled back'
            USING ERRCODE = 'object_in_use';
    END IF;
END;
$$;
-- squawk-ignore ban-drop-table
DROP TABLE trust_connect_disputes;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_connect_dispute();
-- squawk-ignore ban-drop-table
DROP TABLE trust_card_fee_settings;
-- squawk-ignore ban-drop-table
DROP TABLE trust_connected_payments;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_connected_payment();
-- squawk-ignore ban-drop-table
DROP TABLE trust_connected_account_states;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_connected_account_state();
-- squawk-ignore ban-drop-table
DROP TABLE trust_connected_accounts;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_connected_account();

-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_autopay_stops ADD CONSTRAINT trust_autopay_stops_check CHECK (
    (why = 'staff') = (recorded_by IS NOT NULL)
);
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_autopay_stops DROP CONSTRAINT trust_autopay_stops_who_v2;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_autopay_stops ADD CONSTRAINT trust_autopay_stops_why_check CHECK (
    why IN ('tenant', 'staff', 'lease ended', 'tenant left', 'failed twice', 'replaced')
);
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_autopay_stops DROP CONSTRAINT trust_autopay_stops_why_v2;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_autopay DROP CONSTRAINT trust_autopay_connect_only;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_autopay ADD CONSTRAINT trust_autopay_partner_check
CHECK (partner IN ('fake', 'stripe'));
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_autopay DROP CONSTRAINT trust_autopay_partner_v2;
-- squawk-ignore ban-drop-column
ALTER TABLE trust_autopay DROP COLUMN method;

-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_owner_online_payments DROP CONSTRAINT trust_owner_online_payments_card_amount;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_owner_online_payments DROP CONSTRAINT trust_owner_online_payments_connect_only;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_owner_online_payments ADD CONSTRAINT trust_owner_online_payments_partner_check
CHECK (partner IN ('fake', 'stripe'));
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_owner_online_payments DROP CONSTRAINT trust_owner_online_payments_partner_v2;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_owner_online_payments ADD CONSTRAINT trust_owner_online_payments_method_check
CHECK (method = 'ach');
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_owner_online_payments DROP CONSTRAINT trust_owner_online_payments_method_v2;
-- squawk-ignore ban-drop-column
ALTER TABLE trust_owner_online_payments DROP COLUMN platform_fee;

-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_online_payments DROP CONSTRAINT trust_online_payments_card_amount;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_online_payments DROP CONSTRAINT trust_online_payments_connect_only;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_online_payments ADD CONSTRAINT trust_online_payments_partner_check
CHECK (partner IN ('fake', 'stripe'));
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_online_payments DROP CONSTRAINT trust_online_payments_partner_v2;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_online_payments ADD CONSTRAINT trust_online_payments_method_check
CHECK (method = 'ach');
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_online_payments DROP CONSTRAINT trust_online_payments_method_v2;
-- squawk-ignore ban-drop-column
ALTER TABLE trust_online_payments DROP COLUMN platform_fee;
