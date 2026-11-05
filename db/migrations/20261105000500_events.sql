-- migrate:up
-- Events (ADR 0011's third piece, approved by the founder 2026-10-31): what happened in a
-- company, for the software that holds its API key, written by the database in the same
-- transaction as what happened, each carrying its kind and ids only. The receiver reads the rest
-- through the API, as its key allows.
--
--   trust_events                          one thing that happened: its kind (payment.recorded,
--                                         work_order.opened, draft.approved...), the ids it is
--                                         about, and the transaction that wrote it, so a reader
--                                         takes events in the order their transactions finished
--                                         and never skips one still being written.
--   trust_event_subscriptions             a webhook an admin adds: an https address and its
--                                         secret, sealed under the server's PMS_DATA_KEY by the
--                                         app (only its last four shown), each event signed
--                                         with it (HMAC-SHA256).
--   trust_event_subscription_revocations  a webhook taken back, once, by an admin.
--   trust_event_deliveries                each attempt to deliver an event to a webhook: what
--                                         the receiver answered, or why it failed.
--
-- Events are written by one trigger function, trust_record_event, on the tables whose writes
-- they tell of; it runs as the owner, so neither the app nor the AI's role writes an event. They
-- and their deliveries are not audited: each event repeats a write the audit log already keeps.
-- The AI's role reads the events (ids only, of tables it reads), never a webhook or a delivery.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    kind text NOT NULL CHECK (
        kind IN (
            'payment.recorded', 'payment.returned', 'late_fee.charged', 'lease.opened',
            'lease.end_changed', 'bill.entered', 'bill.waiting_for_owner', 'work_order.opened',
            'work_order.assigned', 'work_order.scheduled', 'work_order.completed',
            'work_order.cancelled', 'maintenance.requested', 'draft.approved', 'draft.declined'
        )
    ),
    -- The ids it is about, by name ({"lease_id": ..., "transfer_id": ...}): never a word more.
    subject jsonb NOT NULL CHECK (jsonb_typeof(subject) = 'object'),
    -- The transaction that wrote it: a reader takes events of transactions all finished.
    xact xid8 NOT NULL DEFAULT pg_current_xact_id(),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX trust_events_in_order ON trust_events (pmc_id, xact, id);

CREATE TABLE trust_event_subscriptions (
    id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    -- An https address with a host and no credentials; the app checks where it leads each time.
    url text NOT NULL CHECK (url ~ '^https://[^/?#@\s]+(/[^\s]*)?$' AND char_length(url) <= 500),
    -- The signing secret, sealed by the app under PMS_DATA_KEY with this row's id bound in.
    secret_sealed bytea NOT NULL,
    secret_last4 text NOT NULL CHECK (secret_last4 ~ '^[A-Za-z0-9_-]{4}$'),
    -- The admin who added it: the request names them.
    recorded_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_event_subscriptions_recorded_by ON trust_event_subscriptions (
    pmc_id, recorded_by
);

CREATE TABLE trust_event_subscription_revocations (
    subscription_id uuid PRIMARY KEY,
    pmc_id uuid NOT NULL,
    recorded_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, subscription_id) REFERENCES trust_event_subscriptions (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_event_subscription_revocations_recorded_by
ON trust_event_subscription_revocations (pmc_id, recorded_by);

CREATE TABLE trust_event_deliveries (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL,
    subscription_id uuid NOT NULL,
    event_id bigint NOT NULL REFERENCES trust_events (id),
    attempted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    -- The receiver's answer, when there was one; delivered is a 2xx.
    status integer CHECK (status BETWEEN 100 AND 599),
    error text CHECK (char_length(error) <= 1000),
    CHECK (status BETWEEN 200 AND 299 OR error IS NOT NULL),
    FOREIGN KEY (pmc_id, subscription_id) REFERENCES trust_event_subscriptions (pmc_id, id)
);
CREATE INDEX trust_event_deliveries_of ON trust_event_deliveries (subscription_id, event_id);
CREATE INDEX trust_event_deliveries_event ON trust_event_deliveries (event_id);

-- One function writes every event, from the row that tells of it.
CREATE FUNCTION trust_record_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_kind text;
    v_subject jsonb;
    v_meta jsonb;
BEGIN
    CASE TG_TABLE_NAME
    WHEN 'trust_idempotency_keys' THEN
        -- A posting under its key: a tenant's payment, or one bouncing.
        SELECT t.metadata INTO v_meta FROM pgledger_transfers AS t WHERE t.id = NEW.transfer_ids[1];
        v_kind := CASE v_meta ->> 'kind'
            WHEN 'tenant payment' THEN 'payment.recorded'
            WHEN 'bounced payment' THEN 'payment.returned'
        END;
        v_subject := jsonb_build_object(
            'lease_id', v_meta ->> 'lease_id', 'transfer_id', NEW.transfer_ids[1]
        );
    WHEN 'trust_late_fees' THEN
        v_kind := 'late_fee.charged';
        v_subject := jsonb_build_object(
            'lease_id', (SELECT c.lease_id FROM trust_charges AS c WHERE c.id = NEW.fee_charge_id),
            'charge_id', NEW.fee_charge_id, 'rent_charge_id', NEW.rent_charge_id
        );
    WHEN 'trust_leases' THEN
        IF TG_OP = 'INSERT' THEN
            v_kind := 'lease.opened';
        ELSIF OLD.ends_on IS DISTINCT FROM NEW.ends_on THEN
            v_kind := 'lease.end_changed';
        END IF;
        v_subject := jsonb_build_object('lease_id', NEW.id, 'unit_id', NEW.unit_id);
    WHEN 'trust_bills' THEN
        -- Over the approval limit of the agreement in force on its date (0 without one): it
        -- waits for its owner.
        v_kind := CASE WHEN NEW.amount > coalesce((
            SELECT g.approval_limit FROM trust_management_agreements AS g
            WHERE g.ledger_account_id = NEW.ledger_account_id AND g.starts_on <= NEW.bill_date
            ORDER BY g.starts_on DESC LIMIT 1
        ), 0) THEN 'bill.waiting_for_owner' ELSE 'bill.entered' END;
        v_subject := jsonb_build_object('bill_id', NEW.id, 'vendor_id', NEW.vendor_id);
    WHEN 'trust_work_orders' THEN
        v_kind := 'work_order.opened';
        v_subject := jsonb_build_object(
            'work_order_id', NEW.id, 'property_id', NEW.property_id, 'unit_id', NEW.unit_id
        );
    WHEN 'trust_work_order_steps' THEN
        v_kind := 'work_order.' || NEW.step;
        v_subject := jsonb_build_object('work_order_id', NEW.work_order_id);
    WHEN 'trust_maintenance_requests' THEN
        v_kind := 'maintenance.requested';
        v_subject := jsonb_build_object('request_id', NEW.id, 'lease_id', NEW.lease_id);
    WHEN 'trust_draft_decisions' THEN
        v_kind := 'draft.' || NEW.decision;
        v_subject := jsonb_build_object('draft_id', NEW.draft_id);
    END CASE;
    IF v_kind IS NOT NULL THEN
        INSERT INTO trust_events (pmc_id, kind, subject)
        VALUES (NEW.pmc_id, v_kind, jsonb_strip_nulls(v_subject));
    END IF;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION trust_record_event() FROM PUBLIC;

CREATE TRIGGER trust_record_event AFTER INSERT ON trust_idempotency_keys
FOR EACH ROW EXECUTE FUNCTION trust_record_event();
CREATE TRIGGER trust_record_event AFTER INSERT ON trust_late_fees
FOR EACH ROW EXECUTE FUNCTION trust_record_event();
CREATE TRIGGER trust_record_event AFTER INSERT OR UPDATE OF ends_on ON trust_leases
FOR EACH ROW EXECUTE FUNCTION trust_record_event();
CREATE TRIGGER trust_record_event AFTER INSERT ON trust_bills
FOR EACH ROW EXECUTE FUNCTION trust_record_event();
CREATE TRIGGER trust_record_event AFTER INSERT ON trust_work_orders
FOR EACH ROW EXECUTE FUNCTION trust_record_event();
CREATE TRIGGER trust_record_event AFTER INSERT ON trust_work_order_steps
FOR EACH ROW EXECUTE FUNCTION trust_record_event();
CREATE TRIGGER trust_record_event AFTER INSERT ON trust_maintenance_requests
FOR EACH ROW EXECUTE FUNCTION trust_record_event();
CREATE TRIGGER trust_record_event AFTER INSERT ON trust_draft_decisions
FOR EACH ROW EXECUTE FUNCTION trust_record_event();

-- Only an admin adds a webhook or takes one back.
CREATE FUNCTION trust_check_webhook_admin() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM trust_require_role(
        NEW.pmc_id,
        NEW.recorded_by,
        'admin',
        CASE TG_TABLE_NAME WHEN 'trust_event_subscriptions' THEN 'add a webhook'
            ELSE 'take a webhook back' END
    );
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_webhook_admin() FROM PUBLIC;

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_events
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_event_deliveries
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_event_subscriptions
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by_admin BEFORE INSERT ON trust_event_subscriptions
FOR EACH ROW EXECUTE FUNCTION trust_check_webhook_admin();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_event_subscriptions
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_event_subscriptions
FOR EACH ROW EXECUTE FUNCTION trust_audit();

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_event_subscription_revocations
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_recorded_by_admin BEFORE INSERT ON trust_event_subscription_revocations
FOR EACH ROW EXECUTE FUNCTION trust_check_webhook_admin();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_event_subscription_revocations
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_event_subscription_revocations
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_events FROM PUBLIC;
GRANT SELECT ON trust_events TO trust_app;
GRANT SELECT ON trust_events TO trust_ai_agent;
REVOKE ALL ON trust_event_subscriptions FROM PUBLIC;
GRANT SELECT, INSERT ON trust_event_subscriptions TO trust_app;
REVOKE ALL ON trust_event_subscription_revocations FROM PUBLIC;
GRANT SELECT, INSERT ON trust_event_subscription_revocations TO trust_app;
REVOKE ALL ON trust_event_deliveries FROM PUBLIC;
GRANT SELECT, INSERT ON trust_event_deliveries TO trust_app;

-- migrate:down
SET LOCAL lock_timeout = '5s';
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_event_subscriptions) THEN
        RAISE EXCEPTION 'refusing to undo events: an admin added a webhook';
    END IF;
END;
$$;
DROP TRIGGER trust_record_event ON trust_idempotency_keys;
DROP TRIGGER trust_record_event ON trust_late_fees;
DROP TRIGGER trust_record_event ON trust_leases;
DROP TRIGGER trust_record_event ON trust_bills;
DROP TRIGGER trust_record_event ON trust_work_orders;
DROP TRIGGER trust_record_event ON trust_work_order_steps;
DROP TRIGGER trust_record_event ON trust_maintenance_requests;
DROP TRIGGER trust_record_event ON trust_draft_decisions;
-- squawk-ignore ban-drop-table
DROP TABLE trust_event_deliveries;
-- squawk-ignore ban-drop-table
DROP TABLE trust_event_subscription_revocations;
-- squawk-ignore ban-drop-table
DROP TABLE trust_event_subscriptions;
-- squawk-ignore ban-drop-table
DROP TABLE trust_events;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_webhook_admin();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_record_event();
