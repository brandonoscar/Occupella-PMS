-- migrate:up
-- Payments partner reports refused (docs/edge-cases.md P-010, P-011). A Stripe report signed with
-- a secret the company no longer has in Company setup (the endpoint recreated in Stripe), or
-- signed with its secret but read as too old because the server's clock is off, was answered 400
-- and nobody was told: Stripe gives up after some days and the payments the bank paid wait for
-- the nightly look-up a week on.
--
--   trust_payment_report_refusals   a day a report about one of the company's payments was
--                                   refused: 'signature' (no secret of the company's signed it)
--                                   or 'clock' (its signature good, its time more than five
--                                   minutes from the server's, by `drift_seconds`). Once per
--                                   company, reason and day; append-only, audited. Only a report
--                                   naming a payment or autopay of the company counts, so a
--                                   stranger's post to the address writes nothing.
--
-- Company setup says so in words until a report lands after it, and while the server sends email
-- each admin is emailed, once a day ('payment_reports_refused', a new kind of admin alert).

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_payment_report_refusals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    reason text NOT NULL CHECK (reason IN ('signature', 'clock')),
    day date NOT NULL,
    drift_seconds bigint CHECK (drift_seconds IS NULL OR abs(drift_seconds) > 300),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((reason = 'clock') = (drift_seconds IS NOT NULL)),
    UNIQUE (pmc_id, reason, day)
);

REVOKE ALL ON trust_payment_report_refusals FROM PUBLIC;
GRANT SELECT, INSERT ON trust_payment_report_refusals TO trust_app;

CREATE TRIGGER trust_append_only
BEFORE DELETE OR UPDATE OR TRUNCATE ON trust_payment_report_refusals
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR DELETE OR UPDATE ON trust_payment_report_refusals
FOR EACH ROW EXECUTE FUNCTION trust_audit();

-- Every alert already there is one of the kinds before, so the constraint is added without
-- reading the table, as the emails' kinds were (migration 20261024000152).
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_admin_alerts DROP CONSTRAINT trust_admin_alerts_kind_check;
ALTER TABLE trust_admin_alerts ADD CONSTRAINT trust_admin_alerts_kind_check CHECK (
    kind IN ('nightly_failed', 'deliveries_failing', 'payment_reports_refused')
) NOT VALID;

-- migrate:down
SET LOCAL lock_timeout = '5s';

-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_admin_alerts DROP CONSTRAINT trust_admin_alerts_kind_check;
ALTER TABLE trust_admin_alerts ADD CONSTRAINT trust_admin_alerts_kind_check CHECK (
    kind IN ('nightly_failed', 'deliveries_failing')
) NOT VALID;

-- squawk-ignore ban-drop-table
DROP TABLE trust_payment_report_refusals;
