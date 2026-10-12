-- migrate:up
-- Staff roles (founder, 2026-10-12: docs/adr/0003-staff-back-office.md, item 4: who may approve
-- reconciliations and pay out money; until now one staff member prepared and approved).
--
--   trust_staff_roles                   grants and revocations of a role to a staff member,
--                                       append-only; the latest row for a staff member and role
--                                       says whether they hold it (trust_staff_holds):
--                                         admin     changes roles, and invites or disables a
--                                                   staff login
--                                         approve   approves a reconciliation, signs off a
--                                                   cutover's tie-out
--                                         disburse  pays out money: pays a bill, an owner draw
--   trust_reconciliation_preparations   month-end, prepared: a staff member's figures for a
--                                       trust bank account through a day, the books lacking no
--                                       line of the statements.
--
-- The rules hold for whoever the application names (trust.staff_id, as the API does for every
-- staff request): a payment of a bill or an owner draw needs a staff member who holds disburse,
-- an approved reconciliation one who holds approve. A write that names nobody, as the server's
-- command line does, is not held to them, as the audit log records it with no staff member.
-- Month-end is prepared by one staff member and approved by another
-- (trust_approve_bank_reconciliation now takes the preparation), on the figures that were
-- prepared; trust_app no longer calls trust_approve_reconciliation itself.
--
-- Only an admin changes roles, and a PMC keeps one admin. Its first admin is granted with
-- nobody named (python -m pms.admin grant). On upgrade every staff member holds every role, as
-- each could do everything before; an admin then takes away what they shouldn't hold.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_staff_roles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    staff_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('admin', 'approve', 'disburse')),
    granted boolean NOT NULL,
    -- The staff member who made the change (NULL: the server's command line, or the upgrade).
    -- Both are set by the trigger below.
    changed_by uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (staff_id, role, created_at),
    FOREIGN KEY (pmc_id, staff_id) REFERENCES trust_staff (pmc_id, id),
    FOREIGN KEY (pmc_id, changed_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_staff_roles_pmc_id ON trust_staff_roles (pmc_id);

CREATE TABLE trust_reconciliation_preparations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    bank_account_id uuid NOT NULL,
    through date NOT NULL,
    bank_balance numeric NOT NULL,
    book_cash numeric NOT NULL,
    prepared_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (pmc_id, bank_account_id) REFERENCES trust_bank_accounts (pmc_id, id),
    FOREIGN KEY (pmc_id, prepared_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_reconciliation_preparations_bank_account_id
ON trust_reconciliation_preparations (bank_account_id, created_at);

CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_staff_roles
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_reconciliation_preparations
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();

CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_staff_roles
FOR EACH ROW EXECUTE FUNCTION trust_audit();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_reconciliation_preparations
FOR EACH ROW EXECUTE FUNCTION trust_audit();

-- Every staff member already here holds every role: each could do everything before.
INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted)
SELECT s.pmc_id, s.id, r.role, true
FROM trust_staff AS s
CROSS JOIN (VALUES ('admin'), ('approve'), ('disburse')) AS r (role);

-- Whether a staff member of the PMC holds a role now.
CREATE FUNCTION trust_staff_holds(p_pmc_id uuid, p_staff_id uuid, p_role text) RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce((
        SELECT r.granted FROM trust_staff_roles AS r
        WHERE r.pmc_id = p_pmc_id AND r.staff_id = p_staff_id AND r.role = p_role
        ORDER BY r.created_at DESC
        LIMIT 1
    ), false)
$$;

-- Refuses unless the staff member (if one is named) holds the role.
CREATE FUNCTION trust_require_role(p_pmc_id uuid, p_staff_id uuid, p_role text, p_doing text)
RETURNS void
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
BEGIN
    IF p_staff_id IS NOT NULL AND NOT trust_staff_holds(p_pmc_id, p_staff_id, p_role) THEN
        RAISE EXCEPTION 'trust: % may not % (the % role)',
            coalesce((SELECT s.display_name FROM trust_staff AS s WHERE s.id = p_staff_id),
                     p_staff_id::text),
            p_doing, p_role
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'An admin of the PMC grants the role.';
    END IF;
END;
$$;

-- A row that needs the named staff member to hold a role: TG_ARGV is the role and what it does.
CREATE FUNCTION trust_check_staff_role() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM trust_require_role(
        NEW.pmc_id, nullif(current_setting('trust.staff_id', true), '')::uuid,
        TG_ARGV[0], TG_ARGV[1]
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_disburse
BEFORE INSERT ON trust_bill_payments
FOR EACH ROW WHEN (NEW.step = 'paid')
EXECUTE FUNCTION trust_check_staff_role('disburse', 'pay out money');
CREATE TRIGGER trust_disburse
BEFORE INSERT ON trust_owner_draws
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('disburse', 'pay out money');
CREATE TRIGGER trust_approve
BEFORE INSERT ON trust_reconciliations
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role('approve', 'approve a reconciliation');
CREATE TRIGGER trust_admin
BEFORE UPDATE OF disabled_at ON trust_logins
FOR EACH ROW WHEN (OLD.kind = 'staff')
EXECUTE FUNCTION trust_check_staff_role('admin', 'disable a staff login');

-- An invite to a staff login sets its password: only an admin sends one.
CREATE FUNCTION trust_check_staff_invite() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    IF (SELECT l.kind FROM trust_logins AS l WHERE l.id = NEW.login_id) = 'staff' THEN
        PERFORM trust_require_role(
            NEW.pmc_id, nullif(current_setting('trust.staff_id', true), '')::uuid,
            'admin', 'invite a staff member'
        );
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_admin
BEFORE INSERT ON trust_invites
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_invite();

-- Whoever signs off a cutover's tie-out holds approve.
CREATE FUNCTION trust_check_cutover_signer() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM trust_require_role(NEW.pmc_id, NEW.signed_by, 'approve', 'sign off a cutover');
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_approve
BEFORE INSERT ON trust_cutover_signoffs
FOR EACH ROW EXECUTE FUNCTION trust_check_cutover_signer();

-- A role changes only by an admin, who is the staff member named; the change has to change
-- something; a PMC with an admin keeps one. With nobody named, only a PMC's first admin is
-- granted. Changes of one PMC wait for each other.
CREATE FUNCTION trust_check_staff_role_change() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_has_admin boolean;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('staff roles of ' || NEW.pmc_id, 0));
    NEW.changed_by := nullif(current_setting('trust.staff_id', true), '')::uuid;
    NEW.created_at := clock_timestamp();
    v_has_admin := EXISTS (
        SELECT s.id FROM trust_staff AS s
        WHERE s.pmc_id = NEW.pmc_id AND trust_staff_holds(NEW.pmc_id, s.id, 'admin')
    );
    IF NEW.changed_by IS NULL THEN
        IF v_has_admin OR NEW.role <> 'admin' OR NOT NEW.granted THEN
            RAISE EXCEPTION 'trust: name the admin who changes a role'
                USING ERRCODE = 'insufficient_privilege',
                      HINT = 'With nobody named, only a PMC''s first admin is granted.';
        END IF;
    ELSE
        PERFORM trust_require_role(NEW.pmc_id, NEW.changed_by, 'admin', 'change a role');
    END IF;
    IF trust_staff_holds(NEW.pmc_id, NEW.staff_id, NEW.role) = NEW.granted THEN
        RAISE EXCEPTION 'trust: staff member % % the % role already', NEW.staff_id,
            CASE WHEN NEW.granted THEN 'holds' ELSE 'does not hold' END, NEW.role
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT NEW.granted AND NEW.role = 'admin' AND NOT EXISTS (
        SELECT s.id FROM trust_staff AS s
        WHERE s.pmc_id = NEW.pmc_id AND s.id <> NEW.staff_id
          AND trust_staff_holds(NEW.pmc_id, s.id, 'admin')
    ) THEN
        RAISE EXCEPTION 'trust: staff member % is the PMC''s last admin', NEW.staff_id
            USING ERRCODE = 'check_violation',
                  HINT = 'Make another staff member an admin first.';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trust_staff_role_change
BEFORE INSERT ON trust_staff_roles
FOR EACH ROW EXECUTE FUNCTION trust_check_staff_role_change();

-- Month-end, prepared: the named staff member's figures for a trust bank account through the
-- end of a day, once it has ended and the books lack no line of the statements up to it. (The
-- report reads them in one snapshot; the approval checks them again under the account's locks.)
CREATE FUNCTION trust_prepare_bank_reconciliation(
    p_pmc_id uuid,
    p_bank_account_id uuid,
    p_through date
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_start timestamptz;
    v_figures numeric[];
    v_id uuid;
BEGIN
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who prepares the reconciliation'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT coalesce(b.closed_through, b.opened_at, (
               SELECT min(s.first_day)::timestamp AT TIME ZONE 'UTC'
               FROM trust_bank_statements AS s WHERE s.bank_account_id = b.id
           ))
    INTO v_start
    FROM trust_bank_accounts AS b WHERE b.id = p_bank_account_id AND b.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no trust bank account % in PMC %', p_bank_account_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF (p_through + 1)::timestamp AT TIME ZONE 'UTC' <= v_start THEN
        RAISE EXCEPTION 'trust: the next period of trust bank account % starts at %',
            p_bank_account_id, v_start
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF (p_through + 1)::timestamp AT TIME ZONE 'UTC' > now() THEN
        RAISE EXCEPTION 'trust: % has not ended yet', p_through
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- The bank balance, the book cash and the difference.
    SELECT array_agg(r.amount ORDER BY r.line) INTO v_figures
    FROM trust_report_bank_reconciliation(p_pmc_id, p_bank_account_id, p_through) AS r
    WHERE r.line IN (4, 8, 9);
    IF v_figures[3] <> 0 THEN
        RAISE EXCEPTION 'trust: lines of trust bank account % come to % that the books lack '
            'by the end of %', p_bank_account_id, v_figures[3], p_through
            USING ERRCODE = 'check_violation',
                  HINT = 'Record them in the books and match them first.';
    END IF;
    INSERT INTO trust_reconciliation_preparations (
        pmc_id, bank_account_id, through, bank_balance, book_cash, prepared_by
    ) VALUES (p_pmc_id, p_bank_account_id, p_through, v_figures[1], v_figures[2], v_staff)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- Approves a month-end preparation: the named staff member is not the one who prepared it (and
-- holds approve: trust_reconciliations checks), it is the account's latest, and the books are
-- still as prepared. The account is then closed through its day.
-- On purpose: approving takes a preparation now (below); the app calls only the new one.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_approve_bank_reconciliation(uuid, uuid, date, text, text);
CREATE FUNCTION trust_approve_bank_reconciliation(
    p_pmc_id uuid,
    p_preparation_id uuid
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_staff uuid := nullif(current_setting('trust.staff_id', true), '')::uuid;
    v_prepared trust_reconciliation_preparations;
    v_bank trust_bank_accounts;
    v_start timestamptz;
    v_figures numeric[];
BEGIN
    SELECT p.* INTO v_prepared FROM trust_reconciliation_preparations AS p
    WHERE p.id = p_preparation_id AND p.pmc_id = p_pmc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no reconciliation prepared as % in PMC %', p_preparation_id,
            p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_staff IS NULL THEN
        RAISE EXCEPTION 'trust: name the staff member who approves the reconciliation'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_staff = v_prepared.prepared_by THEN
        RAISE EXCEPTION 'trust: the staff member who prepared a reconciliation does not approve it'
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'Another staff member who holds the approve role approves it.';
    END IF;
    v_bank := trust_lock_bank_matches(p_pmc_id, v_prepared.bank_account_id);
    -- Matches and statements of the account wait for this (trust_approve_reconciliation then
    -- holds postings off).
    PERFORM pg_advisory_xact_lock(hashtextextended('bank statements of ' || v_bank.id, 0));
    IF EXISTS (
        SELECT p.id FROM trust_reconciliation_preparations AS p
        WHERE p.bank_account_id = v_bank.id AND p.created_at > v_prepared.created_at
    ) THEN
        RAISE EXCEPTION 'trust: trust bank account % was prepared again since; approve the '
            'latest', v_bank.id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT coalesce(b.closed_through, b.opened_at, (
               SELECT min(s.first_day)::timestamp AT TIME ZONE 'UTC'
               FROM trust_bank_statements AS s WHERE s.bank_account_id = b.id
           ))
    INTO v_start
    FROM trust_bank_accounts AS b WHERE b.id = v_bank.id;
    IF v_start >= (v_prepared.through + 1)::timestamp AT TIME ZONE 'UTC' THEN
        RAISE EXCEPTION 'trust: trust bank account % is approved through % already', v_bank.id,
            v_prepared.through
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT array_agg(r.amount ORDER BY r.line) INTO v_figures
    FROM trust_report_bank_reconciliation(p_pmc_id, v_bank.id, v_prepared.through) AS r
    WHERE r.line IN (4, 8, 9);
    -- (The bank balance through the day can't have changed: its statements are whole and
    -- append-only.)
    IF v_figures[2] <> v_prepared.book_cash THEN
        RAISE EXCEPTION 'trust: the books through % changed since they were prepared: % '
            '(prepared %)', v_prepared.through, v_figures[2], v_prepared.book_cash
            USING ERRCODE = 'check_violation',
                  HINT = 'Prepare it again.';
    END IF;
    IF v_figures[3] <> 0 THEN
        RAISE EXCEPTION 'trust: lines of trust bank account % come to % that the books lack '
            'by the end of %', v_bank.id, v_figures[3], v_prepared.through
            USING ERRCODE = 'check_violation',
                  HINT = 'Record them in the books and match them first.';
    END IF;
    RETURN trust_approve_reconciliation(
        p_pmc_id, v_bank.id, v_start,
        (v_prepared.through + 1)::timestamp AT TIME ZONE 'UTC', v_figures[1],
        (SELECT s.display_name FROM trust_staff AS s WHERE s.id = v_prepared.prepared_by),
        (SELECT s.display_name FROM trust_staff AS s WHERE s.id = v_staff)
    );
END;
$$;

REVOKE ALL ON trust_staff_roles, trust_reconciliation_preparations FROM PUBLIC;
-- An admin changes roles with plain inserts; the trigger says who changed them, and when.
GRANT SELECT, INSERT ON trust_staff_roles TO trust_app;
-- Preparations are written only by trust_prepare_bank_reconciliation.
GRANT SELECT ON trust_reconciliation_preparations TO trust_app;
GRANT SELECT ON trust_staff_roles, trust_reconciliation_preparations TO trust_ai_agent;

REVOKE ALL ON FUNCTION trust_staff_holds(uuid, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_staff_holds(uuid, uuid, text) TO trust_app;
REVOKE ALL ON FUNCTION trust_require_role(uuid, uuid, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_require_role(uuid, uuid, text, text) TO trust_app;
REVOKE ALL ON FUNCTION trust_prepare_bank_reconciliation(uuid, uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_prepare_bank_reconciliation(uuid, uuid, date) TO trust_app;
REVOKE ALL ON FUNCTION trust_approve_bank_reconciliation(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_approve_bank_reconciliation(uuid, uuid) TO trust_app;
-- The app approves only month-end preparations, where preparer and approver differ.
REVOKE EXECUTE ON FUNCTION trust_approve_reconciliation(
    uuid, uuid, timestamptz, timestamptz, numeric, text, text
) FROM trust_app;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

GRANT EXECUTE ON FUNCTION trust_approve_reconciliation(
    uuid, uuid, timestamptz, timestamptz, numeric, text, text
) TO trust_app;
-- Rolling back puts back what 0033 made, so what this migration made goes, on purpose.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_approve_bank_reconciliation(uuid, uuid);
CREATE FUNCTION trust_approve_bank_reconciliation(
    p_pmc_id uuid,
    p_bank_account_id uuid,
    p_through date,
    p_prepared_by text,
    p_approved_by text
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_bank trust_bank_accounts := trust_lock_bank_matches(p_pmc_id, p_bank_account_id);
    v_start timestamptz;
    v_balance numeric;
    v_difference numeric;
BEGIN
    -- Matches and statements of the account wait for this (trust_approve_reconciliation then
    -- holds postings off).
    PERFORM pg_advisory_xact_lock(hashtextextended('bank statements of ' || v_bank.id, 0));
    SELECT r.amount INTO v_balance
    FROM trust_report_bank_reconciliation(p_pmc_id, p_bank_account_id, p_through) AS r
    WHERE r.line = 4;
    SELECT r.amount INTO v_difference
    FROM trust_report_bank_reconciliation(p_pmc_id, p_bank_account_id, p_through) AS r
    WHERE r.line = 9;
    IF v_difference <> 0 THEN
        RAISE EXCEPTION 'trust: lines of trust bank account % come to % that the books lack '
            'by the end of %', p_bank_account_id, v_difference, p_through
            USING ERRCODE = 'check_violation',
                  HINT = 'Record them in the books and match them first.';
    END IF;
    SELECT coalesce(b.closed_through, b.opened_at, (
               SELECT min(s.first_day)::timestamp AT TIME ZONE 'UTC'
               FROM trust_bank_statements AS s WHERE s.bank_account_id = b.id
           ))
    INTO v_start
    FROM trust_bank_accounts AS b WHERE b.id = v_bank.id;
    RETURN trust_approve_reconciliation(
        p_pmc_id, p_bank_account_id, v_start, (p_through + 1)::timestamp AT TIME ZONE 'UTC',
        v_balance, p_prepared_by, p_approved_by
    );
END;
$$;
REVOKE ALL ON FUNCTION trust_approve_bank_reconciliation(uuid, uuid, date, text, text)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_approve_bank_reconciliation(uuid, uuid, date, text, text)
TO trust_app;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_prepare_bank_reconciliation(uuid, uuid, date);
DROP TRIGGER trust_staff_role_change ON trust_staff_roles;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_staff_role_change();
DROP TRIGGER trust_approve ON trust_cutover_signoffs;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_cutover_signer();
DROP TRIGGER trust_admin ON trust_invites;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_staff_invite();
DROP TRIGGER trust_admin ON trust_logins;
DROP TRIGGER trust_approve ON trust_reconciliations;
DROP TRIGGER trust_disburse ON trust_owner_draws;
DROP TRIGGER trust_disburse ON trust_bill_payments;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_staff_role();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_require_role(uuid, uuid, text, text);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_staff_holds(uuid, uuid, text);
-- squawk-ignore ban-drop-table
DROP TABLE trust_reconciliation_preparations;
-- squawk-ignore ban-drop-table
DROP TABLE trust_staff_roles;
