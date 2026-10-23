-- migrate:up
-- Text messages (the founder, 2026-10-23, lane 2's fourth item; ADR 0009; q068, q091). The PMS
-- texts a tenant, an owner or a vendor only at a number they agreed to be texted at, and stops
-- when they say so (the TCPA's prior express consent, and the carriers' rules on opting in and
-- out; docs/law.md).
--
-- trust_text_consents: a person's word on texts at a mobile number (US numbers, as +1 and ten
-- digits): yes or no, and how it was given. In the portal by the person themselves (by_person,
-- naming no staff member); in writing or by word of mouth, recorded by a staff member; or by a
-- reply to a text: STOP says no, START after a STOP says yes again. Append-only and audited, the
-- latest row for a person holding. The AI role never reads it.
--
-- trust_text_settings: which texts the PMS writes by itself, as trust_email_settings does for
-- email (migration 0077): a rent reminder some days before rent is due, a late notice when a late
-- fee is charged. Append-only and audited, the latest row holding, naming who; the AI reads it.
--
-- trust_texts: the outbox. What staff wrote (a message, naming who) or the PMS wrote by itself,
-- to the number the person's latest consent names, and only while it says yes (a trigger, the
-- consent and the text waiting for each other); once per kind and what it is about; not before a
-- time (the PMS's own texts wait for the day: docs/law.md). trust_text_deliveries: each attempt
-- to hand one to the server's text sender, with the provider's id for it or why not. Neither is
-- audited, as emails are not (migration 0042): they hold numbers and words to people. The AI role
-- reads none of the three.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_text_consents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    tenant_id uuid,
    owner_id uuid,
    vendor_id uuid,
    -- A US mobile number, as +1 and ten digits (NANP: neither the area code nor the exchange
    -- starts with 0 or 1).
    phone text NOT NULL CHECK (phone ~ '^[+]1[2-9][0-9]{2}[2-9][0-9]{6}$'),
    consented boolean NOT NULL,
    how text NOT NULL CHECK (how IN ('portal', 'in writing', 'verbally', 'stop reply', 'start reply')),
    by_person boolean NOT NULL DEFAULT false,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id),
    CHECK (num_nonnulls(tenant_id, owner_id, vendor_id) = 1),
    -- The person's own word in the portal is theirs (a tenant's or an owner's); a reply to a text
    -- says STOP (no) or START (yes).
    CHECK ((how = 'portal') = by_person),
    CHECK (NOT by_person OR vendor_id IS NULL),
    CHECK (how <> 'stop reply' OR NOT consented),
    CHECK (how <> 'start reply' OR consented)
);
CREATE INDEX trust_text_consents_tenant ON trust_text_consents (pmc_id, tenant_id, created_at);
CREATE INDEX trust_text_consents_owner ON trust_text_consents (pmc_id, owner_id, created_at);
CREATE INDEX trust_text_consents_vendor ON trust_text_consents (pmc_id, vendor_id, created_at);
CREATE INDEX trust_text_consents_phone ON trust_text_consents (phone, created_at);
CREATE INDEX trust_text_consents_recorded_by ON trust_text_consents (pmc_id, recorded_by);

-- A person's latest word on texts, or none.
CREATE FUNCTION trust_text_consent_of(
    p_pmc_id uuid, p_tenant_id uuid, p_owner_id uuid, p_vendor_id uuid
) RETURNS uuid
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT c.id FROM trust_text_consents AS c
    WHERE c.pmc_id = p_pmc_id
      AND c.tenant_id IS NOT DISTINCT FROM p_tenant_id
      AND c.owner_id IS NOT DISTINCT FROM p_owner_id
      AND c.vendor_id IS NOT DISTINCT FROM p_vendor_id
    ORDER BY c.created_at DESC, c.id DESC
    LIMIT 1
$$;
REVOKE ALL ON FUNCTION trust_text_consent_of(uuid, uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_text_consent_of(uuid, uuid, uuid, uuid) TO trust_app;

-- Names who recorded a consent (the person themselves and a reply name nobody), waits for the
-- person's other consents and texts, and takes a START only after a STOP at the same number.
CREATE FUNCTION trust_check_text_consent() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_latest trust_text_consents%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('texts of ' || coalesce(NEW.tenant_id, NEW.owner_id, NEW.vendor_id), 0)
    );
    NEW.created_at := clock_timestamp();
    NEW.recorded_by := CASE
        WHEN NEW.how IN ('in writing', 'verbally')
            THEN nullif(current_setting('trust.staff_id', true), '')::uuid
    END;
    IF NEW.how = 'start reply' THEN
        SELECT * INTO v_latest FROM trust_text_consents AS c
        WHERE c.id = trust_text_consent_of(NEW.pmc_id, NEW.tenant_id, NEW.owner_id, NEW.vendor_id);
        IF v_latest.how IS DISTINCT FROM 'stop reply' OR v_latest.phone <> NEW.phone THEN
            RAISE EXCEPTION 'trust: a START reply takes back only a STOP from the same number'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_text_consent() FROM PUBLIC;

CREATE TRIGGER trust_check_text_consent BEFORE INSERT ON trust_text_consents
FOR EACH ROW EXECUTE FUNCTION trust_check_text_consent();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_text_consents
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_text_consents
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TABLE trust_text_settings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- Days before rent is due that its reminder is texted; none texts no reminder.
    -- squawk-ignore prefer-bigint-over-int
    rent_reminder_days integer CHECK (rent_reminder_days BETWEEN 1 AND 15),
    late_notices boolean NOT NULL,
    -- A work order's visit texted to the tenants of the home when it is set.
    visits boolean NOT NULL DEFAULT false,
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_text_settings_pmc ON trust_text_settings (pmc_id, created_at);
CREATE INDEX trust_text_settings_recorded_by ON trust_text_settings (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_text_settings
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_text_settings
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_text_settings
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TABLE trust_texts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    kind text NOT NULL CHECK (kind IN (
        'message', 'rent_reminder', 'late_notice', 'balance_notice', 'visit'
    )),
    tenant_id uuid,
    owner_id uuid,
    vendor_id uuid,
    -- The consent it goes under, the person's latest when written, and its number.
    consent_id uuid NOT NULL,
    to_phone text NOT NULL,
    body text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 480),
    sent_by uuid,
    lease_id uuid,
    period date,
    charge_id uuid,
    -- A balance notice's text (migration 20261023000201's notice), and a visit's step.
    notice_id uuid,
    work_order_step_id uuid,
    not_before timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, tenant_id) REFERENCES trust_tenants (pmc_id, id),
    FOREIGN KEY (pmc_id, owner_id) REFERENCES trust_owners (pmc_id, id),
    FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id),
    FOREIGN KEY (pmc_id, consent_id) REFERENCES trust_text_consents (pmc_id, id),
    FOREIGN KEY (pmc_id, sent_by) REFERENCES trust_staff (pmc_id, id),
    FOREIGN KEY (pmc_id, lease_id) REFERENCES trust_leases (pmc_id, id),
    FOREIGN KEY (pmc_id, charge_id) REFERENCES trust_charges (pmc_id, id),
    FOREIGN KEY (notice_id) REFERENCES trust_balance_notices (notice_id),
    FOREIGN KEY (work_order_step_id) REFERENCES trust_work_order_steps (id),
    CHECK (num_nonnulls(tenant_id, owner_id, vendor_id) = 1),
    -- What staff wrote names who; the PMS's own name what they are about.
    CHECK ((kind = 'message') = (sent_by IS NOT NULL)),
    CHECK (kind <> 'rent_reminder' OR (tenant_id IS NOT NULL AND lease_id IS NOT NULL
                                       AND period IS NOT NULL)),
    CHECK (kind <> 'late_notice' OR (tenant_id IS NOT NULL AND lease_id IS NOT NULL
                                     AND charge_id IS NOT NULL)),
    CHECK ((kind = 'balance_notice') = (notice_id IS NOT NULL)),
    CHECK (kind <> 'balance_notice' OR (tenant_id IS NOT NULL AND lease_id IS NOT NULL)),
    CHECK ((kind = 'visit') = (work_order_step_id IS NOT NULL)),
    CHECK (kind <> 'visit' OR tenant_id IS NOT NULL)
);
CREATE INDEX trust_texts_tenant ON trust_texts (pmc_id, tenant_id, created_at);
CREATE INDEX trust_texts_owner ON trust_texts (pmc_id, owner_id, created_at);
CREATE INDEX trust_texts_vendor ON trust_texts (pmc_id, vendor_id, created_at);
CREATE INDEX trust_texts_consent ON trust_texts (pmc_id, consent_id);
CREATE INDEX trust_texts_sent_by ON trust_texts (pmc_id, sent_by);
CREATE INDEX trust_texts_charge ON trust_texts (pmc_id, charge_id);
CREATE INDEX trust_texts_waiting ON trust_texts (not_before, created_at);
-- Each of the PMS's own once.
CREATE UNIQUE INDEX trust_texts_rent_reminder
ON trust_texts (lease_id, tenant_id, period) WHERE kind = 'rent_reminder';
CREATE UNIQUE INDEX trust_texts_late_notice
ON trust_texts (charge_id, tenant_id) WHERE kind = 'late_notice';
CREATE UNIQUE INDEX trust_texts_balance_notice
ON trust_texts (notice_id, tenant_id) WHERE kind = 'balance_notice';
CREATE UNIQUE INDEX trust_texts_visit
ON trust_texts (work_order_step_id, tenant_id) WHERE kind = 'visit';
CREATE INDEX trust_texts_lease ON trust_texts (pmc_id, lease_id);
CREATE INDEX trust_texts_step ON trust_texts (pmc_id, work_order_step_id);

-- A text goes to the number of its person's latest consent, only while it says yes.
CREATE FUNCTION trust_check_text() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_consent trust_text_consents%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('texts of ' || coalesce(NEW.tenant_id, NEW.owner_id, NEW.vendor_id), 0)
    );
    SELECT * INTO v_consent FROM trust_text_consents AS c
    WHERE c.id = trust_text_consent_of(NEW.pmc_id, NEW.tenant_id, NEW.owner_id, NEW.vendor_id);
    IF v_consent.id IS DISTINCT FROM NEW.consent_id OR NOT v_consent.consented
       OR v_consent.phone <> NEW.to_phone THEN
        RAISE EXCEPTION 'trust: a text goes only to the number its person last agreed to be texted at'
            USING ERRCODE = 'check_violation';
    END IF;
    -- A balance notice's text is of that notice's lease; a visit's, of the company's work order.
    IF NEW.notice_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM trust_balance_notices AS n
        WHERE n.notice_id = NEW.notice_id AND n.pmc_id = NEW.pmc_id AND n.lease_id = NEW.lease_id
    ) OR NEW.work_order_step_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM trust_work_order_steps AS w
        WHERE w.id = NEW.work_order_step_id AND w.pmc_id = NEW.pmc_id AND w.step = 'scheduled'
    ) THEN
        RAISE EXCEPTION 'trust: a text is about its own company''s notice or scheduled visit'
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_text() FROM PUBLIC;

CREATE TRIGGER trust_check_text BEFORE INSERT ON trust_texts
FOR EACH ROW EXECUTE FUNCTION trust_check_text();
CREATE TRIGGER trust_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_texts
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

CREATE TABLE trust_text_deliveries (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    text_id uuid NOT NULL REFERENCES trust_texts (id),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    sender text NOT NULL CHECK (sender IN ('fake', 'twilio')),
    -- The provider's id for the message it took.
    provider_id text CHECK (char_length(provider_id) BETWEEN 1 AND 100),
    error text CHECK (error IS NULL OR (error <> '' AND char_length(error) <= 1000)),
    attempted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (error IS NULL OR provider_id IS NULL)
);
CREATE INDEX trust_text_deliveries_text ON trust_text_deliveries (text_id, attempted_at);
CREATE INDEX trust_text_deliveries_pmc ON trust_text_deliveries (pmc_id);
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_text_deliveries
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

REVOKE ALL ON trust_text_consents, trust_text_settings, trust_texts, trust_text_deliveries
FROM PUBLIC;
GRANT SELECT, INSERT ON trust_text_consents, trust_text_settings, trust_texts,
trust_text_deliveries TO trust_app;
-- Which texts go and when, as the email settings: the AI reads them.
GRANT SELECT ON trust_text_settings TO trust_ai_agent;


-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-table
DROP TABLE trust_text_deliveries;
-- squawk-ignore ban-drop-table
DROP TABLE trust_texts;
-- squawk-ignore ban-drop-table
DROP TABLE trust_text_settings;
-- squawk-ignore ban-drop-table
DROP TABLE trust_text_consents;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_text();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_text_consent();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_text_consent_of(uuid, uuid, uuid, uuid);
