-- migrate:up
-- Archiving a vendor (the founder's review q131), as properties, units, tenants and owners are
-- archived (migrations 0063 and 0064): a row of trust_archivals, the latest holding, naming who.
-- An archived vendor leaves the vendors list, its pickers and search, and keeps its history.
-- It is refused while anything is open with them: a bill neither paid nor declined, money set
-- aside for them (their vendor_payable account), a work order still open that was last assigned
-- to them, or a recurring bill or recurring work order of theirs still running. Bringing one back
-- is always allowed. Nothing here moves money.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- Every row so far is another record's, so the new column is NULL in all of them and its key
-- checks nothing; the table is small, so validating the key and the new check in the same step
-- scans little.
ALTER TABLE trust_archivals ADD COLUMN vendor_id uuid;
-- squawk-ignore adding-foreign-key-constraint, constraint-missing-not-valid
ALTER TABLE trust_archivals ADD CONSTRAINT trust_archivals_vendor_fkey
FOREIGN KEY (pmc_id, vendor_id) REFERENCES trust_vendors (pmc_id, id);
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_archivals ADD CONSTRAINT trust_archivals_one_of_five
CHECK (num_nonnulls(property_id, unit_id, tenant_id, owner_id, vendor_id) = 1);
-- The check above takes over from 0064's, which knew four kinds of record.
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_archivals DROP CONSTRAINT trust_archivals_one_record;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_archivals_vendor ON trust_archivals (pmc_id, vendor_id, created_at);

-- Whether a property, unit, tenant, owner or vendor is archived now: its latest archival says
-- so. A call with two, three or four arguments still reaches it.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_is_archived(uuid, uuid, uuid, uuid);
CREATE FUNCTION trust_is_archived(
    p_property_id uuid,
    p_unit_id uuid,
    p_tenant_id uuid DEFAULT NULL,
    p_owner_id uuid DEFAULT NULL,
    p_vendor_id uuid DEFAULT NULL
)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce((
        SELECT a.archived FROM trust_archivals AS a
        WHERE (p_property_id IS NOT NULL AND a.property_id = p_property_id)
           OR (p_unit_id IS NOT NULL AND a.unit_id = p_unit_id)
           OR (p_tenant_id IS NOT NULL AND a.tenant_id = p_tenant_id)
           OR (p_owner_id IS NOT NULL AND a.owner_id = p_owner_id)
           OR (p_vendor_id IS NOT NULL AND a.vendor_id = p_vendor_id)
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT 1
    ), false)
$$;

REVOKE ALL ON FUNCTION trust_is_archived(uuid, uuid, uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_is_archived(uuid, uuid, uuid, uuid, uuid) TO trust_app;

-- 0064's checks, and a vendor's. The day is UTC's today, as the reports count it.
CREATE OR REPLACE FUNCTION trust_check_archival() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_today date := (now() AT TIME ZONE 'UTC')::date;
    v_name text;
BEGIN
    IF trust_is_archived(
        NEW.property_id, NEW.unit_id, NEW.tenant_id, NEW.owner_id, NEW.vendor_id
    ) = NEW.archived THEN
        RAISE EXCEPTION 'trust: it is % already', CASE WHEN NEW.archived THEN 'archived' ELSE 'not archived' END
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT NEW.archived THEN
        RETURN NEW;
    END IF;
    IF NEW.vendor_id IS NOT NULL THEN
        SELECT v.display_name INTO v_name FROM trust_vendors AS v WHERE v.id = NEW.vendor_id;
        IF EXISTS (SELECT 1 FROM trust_bills AS bill
                   WHERE bill.vendor_id = NEW.vendor_id
                     AND NOT EXISTS (SELECT 1 FROM trust_bill_payments AS p
                                     WHERE p.bill_id = bill.id AND p.step = 'paid')
                     AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d
                                     WHERE d.bill_id = bill.id))
        THEN
            RAISE EXCEPTION 'trust: % has a bill not paid; pay or decline it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM trust_ledger_accounts AS a
                   JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
                   WHERE a.kind = 'vendor_payable' AND a.vendor_id = NEW.vendor_id
                     AND b.balance <> 0)
        THEN
            RAISE EXCEPTION 'trust: % has money set aside for them; pay it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM trust_work_orders AS w
                   WHERE w.pmc_id = NEW.pmc_id
                     AND NOT EXISTS (SELECT 1 FROM trust_work_order_steps AS c
                                     WHERE c.work_order_id = w.id
                                       AND c.step IN ('completed', 'cancelled'))
                     AND (SELECT s.vendor_id FROM trust_work_order_steps AS s
                          WHERE s.work_order_id = w.id AND s.step = 'assigned'
                          ORDER BY s.taken_at DESC, s.created_at DESC, s.id DESC
                          LIMIT 1) = NEW.vendor_id)
        THEN
            RAISE EXCEPTION 'trust: % has a work order open; complete, cancel or reassign it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM trust_recurring_bills AS r
                   WHERE r.vendor_id = NEW.vendor_id
                     AND NOT EXISTS (SELECT 1 FROM trust_recurring_bill_ends AS e
                                     WHERE e.recurring_bill_id = r.id AND e.ends_on < v_today))
           OR EXISTS (SELECT 1 FROM trust_recurring_work_orders AS r
                      WHERE r.vendor_id = NEW.vendor_id
                        AND NOT EXISTS (SELECT 1 FROM trust_recurring_work_order_ends AS e
                                        WHERE e.recurring_work_order_id = r.id
                                          AND e.ends_on < v_today))
        THEN
            RAISE EXCEPTION 'trust: % has a recurring bill or job still running; stop it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.tenant_id IS NOT NULL THEN
        SELECT t.display_name INTO v_name FROM trust_tenants AS t WHERE t.id = NEW.tenant_id;
        IF EXISTS (SELECT 1 FROM trust_lease_tenants AS lt JOIN trust_leases AS l ON l.id = lt.lease_id
                   WHERE lt.tenant_id = NEW.tenant_id AND (l.ends_on IS NULL OR l.ends_on >= v_today))
        THEN
            RAISE EXCEPTION 'trust: % is on a lease in effect or to come; end it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM trust_ledger_accounts AS a
                   JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
                   WHERE a.tenant_id = NEW.tenant_id AND b.balance <> 0)
        THEN
            RAISE EXCEPTION 'trust: % has prepaid rent or a deposit held; return it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.unit_id IS NOT NULL THEN
        SELECT u.display_name INTO v_name FROM trust_units AS u WHERE u.id = NEW.unit_id;
        IF EXISTS (SELECT 1 FROM trust_leases AS l
                   WHERE l.unit_id = NEW.unit_id AND (l.ends_on IS NULL OR l.ends_on >= v_today))
        THEN
            RAISE EXCEPTION 'trust: % has a lease in effect or to come; end it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.property_id IS NOT NULL THEN
        SELECT p.display_name INTO v_name FROM trust_properties AS p WHERE p.id = NEW.property_id;
        IF EXISTS (SELECT 1 FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
                   WHERE u.property_id = NEW.property_id
                     AND (l.ends_on IS NULL OR l.ends_on >= v_today))
        THEN
            RAISE EXCEPTION 'trust: % has a lease in effect or to come; end it before archiving',
                v_name USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT o.display_name INTO v_name FROM trust_owners AS o WHERE o.id = NEW.owner_id;
    END IF;
    -- A property's owners' accounts, or an owner's accounts: no money held, no bill left open.
    IF EXISTS (SELECT 1 FROM trust_ledger_accounts AS a
               JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
               WHERE a.kind = 'owner_property' AND b.balance <> 0
                 AND (a.property_id = NEW.property_id OR a.owner_id = NEW.owner_id))
    THEN
        RAISE EXCEPTION 'trust: % holds % money; draw it before archiving', v_name,
            CASE WHEN NEW.owner_id IS NULL THEN 'its owners''' ELSE 'their' END
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM trust_bills AS bill
               JOIN trust_ledger_accounts AS a ON a.ledger_account_id = bill.ledger_account_id
               WHERE (a.property_id = NEW.property_id OR a.owner_id = NEW.owner_id)
                 AND NOT EXISTS (SELECT 1 FROM trust_bill_payments AS p
                                 WHERE p.bill_id = bill.id AND p.step = 'paid')
                 AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d
                                 WHERE d.bill_id = bill.id))
    THEN
        RAISE EXCEPTION 'trust: % has a bill not paid; pay or decline it before archiving', v_name
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

-- migrate:down
DO $$
BEGIN
    RAISE EXCEPTION 'vendor_archivals cannot be rolled back: archivals are records';
END;
$$;
