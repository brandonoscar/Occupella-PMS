-- migrate:up
-- Who keeps a fee (the founder's chart of accounts pass, 2026-11-05). Until now every fee a
-- tenant's payment paid went to the owner's account. A charge type now says whether its fees go
-- to the owner or to the company:
--
--   trust_charge_type_payees  a type's payee from a moment on: 'owner' or 'company'; append-only,
--                             the latest holding, naming who chose it; a type with none is the
--                             owner's.
--   trust_charge_type_payee() a type's payee today; trust_charge_payee() a charge's: its type's
--                             (its own, its monthly charge's, or the one a waiver or a lease end
--                             changes) as it was when the charge was charged, so a fee keeps
--                             the payee it was charged under whenever it is paid, and a change
--                             counts for fees charged from then on.
--   trust_charge_nsf_fee      a returned payment's company part moves back from that account
--                             too, as a bounce moves back everything the payment moved.
--   trust_apply_payment       a fee the company keeps may be paid into the company's fee
--                             account (pmc_income) in the trust bank account that holds an
--                             owner's money for the charge's property, instead of the owner's
--                             account. The app's payments (Receive payment, payment files,
--                             payments online and autopay, prepaid rent used) pay it there; a
--                             deposit kept at move-out still pays the owner's account
--                             (trust_return_deposit is unchanged: a known gap).
--
-- So an owner's cash income statement, year-end and management fee, which count what was paid
-- into the owner's account, leave a fee the company keeps out by themselves; the accrual
-- statement leaves out what the company keeps of what was charged. The company's fees held in
-- trust go out of trust as every fee does (trust_withdraw_pmc_fees, docs/law.md).
-- The AI role reads the payees.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE TABLE trust_charge_type_payees (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pmc_id uuid NOT NULL REFERENCES trust_pmcs (pmc_id),
    charge_type_id uuid NOT NULL,
    payee text NOT NULL CHECK (payee IN ('owner', 'company')),
    recorded_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (pmc_id, charge_type_id) REFERENCES trust_charge_types (pmc_id, id),
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id)
);
CREATE INDEX trust_charge_type_payees_type ON trust_charge_type_payees (charge_type_id, id);
CREATE INDEX trust_charge_type_payees_recorded_by
ON trust_charge_type_payees (pmc_id, recorded_by);

CREATE FUNCTION trust_charge_type_payee(p_id uuid) RETURNS text
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce(
        (SELECT p.payee FROM trust_charge_type_payees AS p
         WHERE p.charge_type_id = p_id ORDER BY p.id DESC LIMIT 1),
        'owner'
    )
$$;
REVOKE ALL ON FUNCTION trust_charge_type_payee(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_charge_type_payee(uuid) TO trust_app;

-- Who keeps a charge's money: its type's payee when it was charged; the owner's for a charge of
-- no type. A waiver or a lease end's adjustment is its charge's.
CREATE FUNCTION trust_charge_payee(p_charge trust_charges) RETURNS text
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT coalesce((
        SELECT py.payee
        FROM trust_charges AS src
        LEFT JOIN trust_recurring_charges AS rc ON rc.id = src.recurring_charge_id
        JOIN trust_charge_type_payees AS py
            ON py.charge_type_id = coalesce(src.charge_type_id, rc.charge_type_id)
           AND py.created_at <= src.created_at
        WHERE src.id = coalesce(
            (SELECT x.charge_id FROM trust_lease_end_adjustments AS x
             WHERE x.adjustment_charge_id = p_charge.id),
            (SELECT w.waived_charge_id FROM trust_charge_waivers AS w
             WHERE w.credit_charge_id = p_charge.id),
            p_charge.id
        )
        ORDER BY py.id DESC
        LIMIT 1
    ), 'owner')
$$;
REVOKE ALL ON FUNCTION trust_charge_payee(trust_charges) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_charge_payee(trust_charges) TO trust_app;

-- A payee chosen changes it.
CREATE FUNCTION trust_check_charge_type_payee() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('chart account ' || NEW.charge_type_id::text, 0));
    IF NEW.payee = trust_charge_type_payee(NEW.charge_type_id) THEN
        RAISE EXCEPTION 'trust: % goes to the % already', trust_charge_type_name(NEW.charge_type_id),
            NEW.payee
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION trust_check_charge_type_payee() FROM PUBLIC;

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_charge_type_payees
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();
CREATE TRIGGER trust_charge_type_payee BEFORE INSERT ON trust_charge_type_payees
FOR EACH ROW EXECUTE FUNCTION trust_check_charge_type_payee();
CREATE TRIGGER trust_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON trust_charge_type_payees
FOR EACH STATEMENT EXECUTE FUNCTION trust_refuse_ledger_rewrite();
CREATE TRIGGER trust_audit AFTER INSERT OR UPDATE OR DELETE ON trust_charge_type_payees
FOR EACH ROW EXECUTE FUNCTION trust_audit();

REVOKE ALL ON trust_charge_type_payees FROM PUBLIC;
GRANT SELECT, INSERT ON trust_charge_type_payees TO trust_app;
GRANT SELECT ON trust_charge_type_payees TO trust_ai_agent;

CREATE OR REPLACE FUNCTION trust_apply_payment(p_pmc_id uuid, p_charge_id uuid, p_transfer_id text, p_amount numeric) RETURNS numeric
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_charge trust_charges;
    v_property_id uuid;
    v_transfer pgledger_transfers;
    v_existing numeric;
    v_paid numeric;
    v_hold trust_payment_holds;
BEGIN
    -- The sums below must count every match that committed while this waited for the locks.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: apply a payment at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a payment applies a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Matches to one charge, and then to one transfer, wait for each other.
    SELECT c.* INTO v_charge
    FROM trust_charges AS c
    WHERE c.id = p_charge_id AND c.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no charge % in PMC %', p_charge_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_charge.kind = 'credit' THEN
        RAISE EXCEPTION 'trust: a credit is not paid; it takes its amount off what is owed'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.* INTO v_transfer
    FROM pgledger_transfers AS t
    WHERE t.id = p_transfer_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no transfer %', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF EXISTS (
        SELECT c.transfer_id FROM trust_owner_contributions AS c
        WHERE c.transfer_id = p_transfer_id
    ) THEN
        RAISE EXCEPTION 'trust: transfer % is an owner''s contribution, not a tenant''s payment',
            p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT u.property_id INTO v_property_id
    FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
    WHERE l.id = v_charge.lease_id;
    -- Into the owner's account at the charge's property, or, for a fee the company keeps
    -- (trust_charge_payee, migration 20261106000220), into the company's fee account in the
    -- trust bank account that holds an owner's money for that property.
    IF NOT EXISTS (
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = v_transfer.to_account_id AND t.pmc_id = p_pmc_id
          AND (
              (t.kind = 'owner_property' AND t.property_id = v_property_id)
              OR (t.kind = 'pmc_income' AND trust_charge_payee(v_charge) = 'company'
                  AND EXISTS (
                      SELECT o.ledger_account_id FROM trust_ledger_accounts AS o
                      WHERE o.pmc_id = p_pmc_id AND o.kind = 'owner_property'
                        AND o.property_id = v_property_id AND o.bank_account_id = t.bank_account_id
                  ))
          )
    ) OR NOT EXISTS (
        SELECT f.ledger_account_id FROM trust_ledger_accounts AS f
        WHERE f.ledger_account_id = v_transfer.from_account_id AND f.pmc_id = p_pmc_id
          AND (
              f.kind = 'bank_cash'
              OR (f.kind IN ('prepaid_rent', 'tenant_deposit') AND f.tenant_id IN (
                  SELECT lt.tenant_id FROM trust_lease_tenants AS lt
                  WHERE lt.lease_id = v_charge.lease_id
              ))
          )
    ) THEN
        RAISE EXCEPTION 'trust: transfer % did not pay into the owner''s account for this '
            'charge''s property (or the company''s fee account for a fee the company keeps), from '
            'cash or money held for a tenant on its lease', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT p.amount INTO v_existing
    FROM trust_charge_payments AS p
    WHERE p.charge_id = p_charge_id AND p.transfer_id = p_transfer_id;
    IF FOUND THEN
        IF v_existing <> p_amount THEN
            RAISE EXCEPTION 'trust: transfer % already pays % of charge %, not %',
                p_transfer_id, v_existing, p_charge_id, p_amount
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_existing;  -- a retry: the original match
    END IF;

    -- A payment hold on the lease (an eviction, say) in force on the transfer's day (UTC)
    -- refuses it, unless the transfer was allowed for that hold. The lease's row lock orders
    -- this against a hold being placed: one waits for the other, then sees it.
    PERFORM l.id FROM trust_leases AS l WHERE l.id = v_charge.lease_id FOR SHARE;
    SELECT h.* INTO v_hold
    FROM trust_payment_holds AS h
    LEFT JOIN trust_payment_hold_releases AS r ON r.hold_id = h.id
    WHERE h.lease_id = v_charge.lease_id
      AND h.starts_on <= (v_transfer.event_at AT TIME ZONE 'UTC')::date
      AND (r.ends_on IS NULL OR (v_transfer.event_at AT TIME ZONE 'UTC')::date < r.ends_on)
      AND NOT EXISTS (
          SELECT a.hold_id FROM trust_payment_hold_allowances AS a
          WHERE a.hold_id = h.id AND a.transfer_id = p_transfer_id
      )
    ORDER BY h.starts_on, h.id
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: lease % is on a payment hold from % (%); transfer % is not '
            'allowed for it', v_charge.lease_id, v_hold.starts_on, v_hold.reason, p_transfer_id
            USING ERRCODE = 'check_violation',
                  HINT = 'Record an allowance for this transfer (trust_payment_hold_allowances) '
                         'to accept it.';
    END IF;

    -- Paid so far: matched, less what bounced back (trust_reverse_payment).
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.charge_id = p_charge_id;
    SELECT v_paid - coalesce(sum(r.amount), 0) INTO v_paid
    FROM trust_payment_reversals AS r WHERE r.charge_id = p_charge_id;
    -- What waivers took off it (credits linked to it, migration 0079) is settled too.
    SELECT v_paid + coalesce(sum(k.amount), 0) INTO v_paid
    FROM trust_charge_waivers AS w
    JOIN trust_charges AS k ON k.id = w.credit_charge_id
    WHERE w.waived_charge_id = p_charge_id;
    IF v_paid + p_amount > v_charge.amount THEN
        RAISE EXCEPTION 'trust: charge % is % and already paid or waived %; % more is too much',
            p_charge_id, v_charge.amount, v_paid, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    -- A transfer pays only once, even after a reversal: its money left the owner's account.
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.transfer_id = p_transfer_id;
    IF v_paid + p_amount > v_transfer.amount THEN
        RAISE EXCEPTION 'trust: transfer % moved % and already pays %; % more is too much',
            p_transfer_id, v_transfer.amount, v_paid, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_charge_payments (pmc_id, charge_id, transfer_id, amount)
    VALUES (p_pmc_id, p_charge_id, p_transfer_id, p_amount);
    RETURN p_amount;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_income_statement_accrual(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date) RETURNS TABLE(line integer, property text, item text, schedule_e integer, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz;
    v_end timestamptz;
BEGIN
    IF p_from IS NULL OR p_through IS NULL OR p_through < p_from THEN
        RAISE EXCEPTION 'trust: an income statement runs from a day through a day no earlier, not % to %',
            p_from, p_through
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_start := p_from::timestamp AT TIME ZONE 'UTC';
    v_end := (p_through + 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH schedule_e (n, label) AS (
        VALUES (3, 'Rents received'), (5, 'Advertising'), (6, 'Auto and travel'),
               (7, 'Cleaning and maintenance'), (8, 'Commissions'), (9, 'Insurance'),
               (10, 'Legal and other professional fees'), (11, 'Management fees'),
               (12, 'Mortgage interest paid to banks, etc.'), (13, 'Other interest'),
               (14, 'Repairs'), (15, 'Supplies'), (16, 'Taxes'), (17, 'Utilities'),
               (18, 'Depreciation expense or depletion'), (19, 'Other')
    ),
    incomes (part, label) AS (
        VALUES (1, 'income: rent'), (2, 'income: recurring charges'), (3, 'income: late fees'),
               (4, 'income: NSF fees'), (5, 'income: other charges')
    ),
    accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS name, t.property_id
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- What each charge is: rent, a recurring charge (pet rent, parking), a late fee, an NSF
    -- fee, or another charge, and the company's own type of it if it has one (its own, or its
    -- recurring charge's), as the cash statement has it. A lease end's adjustment of a rent or
    -- recurring charge (migration 0051), and a waiver (migration 0079), are what they change. A
    -- credit given on its own comes off rent, as credits come off a lease's rent first
    -- (migration 0093).
    classes AS (
        SELECT c.id,
               coalesce(o.charge_type_id, c.charge_type_id, rc.charge_type_id) AS charge_type,
               CASE
                   WHEN coalesce(o.kind, c.kind) = 'rent' THEN 1
                   WHEN coalesce(o.recurring_charge_id, c.recurring_charge_id) IS NOT NULL THEN 2
                   WHEN lf.fee_charge_id IS NOT NULL THEN 3
                   WHEN nf.fee_charge_id IS NOT NULL THEN 4
                   WHEN coalesce(o.kind, c.kind) = 'credit' THEN 1
                   ELSE 5
               END AS part
        FROM trust_charges AS c
        LEFT JOIN trust_lease_end_adjustments AS adj ON adj.adjustment_charge_id = c.id
        LEFT JOIN trust_charge_waivers AS w ON w.credit_charge_id = c.id
        LEFT JOIN trust_charges AS o ON o.id = coalesce(adj.charge_id, w.waived_charge_id)
        LEFT JOIN trust_late_fees AS lf ON lf.fee_charge_id = coalesce(o.id, c.id)
        LEFT JOIN trust_nsf_fees AS nf ON nf.fee_charge_id = coalesce(o.id, c.id)
        LEFT JOIN trust_recurring_charges AS rc
            ON rc.id = coalesce(o.recurring_charge_id, c.recurring_charge_id)
        WHERE c.pmc_id = p_pmc_id
    ),
    -- Each account's share of its property on a day: the account's percent of the ownership in
    -- force (migration 0090), all of it when the property has none.
    -- Charged in the days (accrual basis): each charge due in them of a lease at the account's
    -- property (a credit as less), and the housing authority's part of the rent and its
    -- adjustments (migration 0095) as rent, each at the account's share on its due day, to the
    -- cent.
    owed AS (
        SELECT l.id AS lease, u.property_id, c.id AS charge, NULL::integer AS part, c.due_on,
               CASE c.kind WHEN 'credit' THEN -c.amount ELSE c.amount END AS amount
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE c.pmc_id = p_pmc_id AND c.due_on BETWEEN p_from AND p_through
          -- A fee the company keeps is its income, never the owner's, and so is what a waiver
          -- or a lease end takes off one (migration 20261106000220): who keeps it is its type's
          -- payee when it was charged, as trust_charge_payee has it (written out here: the AI
          -- role runs this report and calls no function but the reports).
          AND coalesce((
              SELECT py.payee
              FROM trust_charges AS src
              LEFT JOIN trust_recurring_charges AS rc ON rc.id = src.recurring_charge_id
              JOIN trust_charge_type_payees AS py
                  ON py.charge_type_id = coalesce(src.charge_type_id, rc.charge_type_id)
                 AND py.created_at <= src.created_at
              WHERE src.id = coalesce(
                  (SELECT x.charge_id FROM trust_lease_end_adjustments AS x
                   WHERE x.adjustment_charge_id = c.id),
                  (SELECT w.waived_charge_id FROM trust_charge_waivers AS w
                   WHERE w.credit_charge_id = c.id),
                  c.id
              )
              ORDER BY py.id DESC
              LIMIT 1
          ), 'owner') = 'owner'
        UNION ALL
        -- An authority's late fee (migration 0116) is a late fee.
        SELECT l.id, u.property_id, NULL, CASE ac.source WHEN 'late fee' THEN 3 ELSE 1 END,
               ac.due_on, CASE ac.kind WHEN 'credit' THEN -ac.amount ELSE ac.amount END
        FROM trust_assistance_charges AS ac
        JOIN trust_leases AS l ON l.id = ac.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE ac.pmc_id = p_pmc_id AND ac.due_on BETWEEN p_from AND p_through
    ),
    charged AS (
        SELECT a.account, coalesce(x.part, k.part) AS part, k.charge_type,
               round(x.amount * CASE
                   WHEN EXISTS (SELECT 1 FROM trust_ownership_shares AS sh
                                WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on)
                   THEN coalesce((
                       SELECT sl.percent FROM trust_ownership_share_lines AS sl
                       WHERE sl.ledger_account_id = a.account AND sl.share_id = (
                           SELECT sh.id FROM trust_ownership_shares AS sh
                           WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on
                           ORDER BY sh.starts_on DESC, sh.created_at DESC, sh.id DESC LIMIT 1
                       )
                   ), 0) / 100
                   ELSE 1
               END, 2) AS amount
        FROM owed AS x
        JOIN accounts AS a ON a.property_id = x.property_id
        LEFT JOIN classes AS k ON k.id = x.charge
    ),
    -- The owner's bills dated in the days, but those the owner declined by their end
    -- (migration 0041): one is never paid.
    entered AS (
        SELECT b.id, b.ledger_account_id AS account, b.amount
        FROM trust_bills AS b
        JOIN accounts AS a ON a.account = b.ledger_account_id
        WHERE b.pmc_id = p_pmc_id AND b.bill_date BETWEEN p_from AND p_through
          AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d
                          WHERE d.bill_id = b.id AND d.declined_on <= p_through)
    ),
    fees AS (
        SELECT f.ledger_account_id AS account, 0 AS ord, tr.amount
        FROM trust_management_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT f.ledger_account_id, 1, tr.amount
        FROM trust_leasing_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A fee staff took from the owner (migration 0082): a lease-up fee with the leasing
        -- fees, an inspection or other fee with the management fees.
        SELECT f.ledger_account_id, CASE f.kind WHEN 'lease_up' THEN 1 ELSE 0 END, tr.amount
        FROM trust_owner_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A bill's markup (migration 0083), with the management fees, with its bill.
        SELECT b.account, 0, m.amount
        FROM trust_bill_markups AS m
        JOIN entered AS b ON b.id = m.bill_id
    ),
    -- Interest on tenants' deposits the owner's account paid into them (migration 0122), when it
    -- moved; interest paid as a rent credit moves no money and is less rent, as a credit is.
    interest AS (
        SELECT l.owner_account AS account, l.amount
        FROM trust_deposit_interest_lines AS l
        JOIN pgledger_transfers AS tr ON tr.id = l.transfer_id
        WHERE l.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    -- Each bill's expense account: its latest row.
    categorized AS (
        SELECT DISTINCT ON (x.bill_id) x.id, x.bill_id, x.expense_account_id
        FROM trust_bill_expense_accounts AS x
        WHERE x.pmc_id = p_pmc_id
        ORDER BY x.bill_id, x.id DESC
    ),
    -- Bills dated in the days (accrual basis): a split bill (migration 0069) by its lines,
    -- each in its expense account, which come to the bill.
    spent AS (
        SELECT b.account,
               coalesce(sp.expense_account_id, cz.expense_account_id) AS expense,
               coalesce(sp.amount, b.amount) AS amount
        FROM entered AS b
        LEFT JOIN categorized AS cz ON cz.bill_id = b.id
        LEFT JOIN trust_bill_splits AS sp ON sp.categorized_id = cz.id
    ),
    -- Each account's lines: (group, order within it, item, Schedule E line, amount). Groups:
    -- 1 income, 3 expenses. Income and the fees show even when nothing moved; each of the
    -- company's charge types has a line of its own after them once money was collected on it,
    -- and isn't counted again in the line it would be in without one.
    items AS (
        SELECT a.account, 1 AS grp, i.part AS ord, i.label AS item, 3 AS sched,
               coalesce((SELECT sum(c.amount) FROM charged AS c
                         WHERE c.account = a.account AND c.part = i.part
                           AND c.charge_type IS NULL), 0) AS total
        FROM accounts AS a
        CROSS JOIN incomes AS i
        UNION ALL
        -- Each type by its name today (migration 20261106000210).
        SELECT c.account, 1, 6,
               'income: ' || coalesce((SELECT n.name FROM trust_charge_type_names AS n
                                    WHERE n.charge_type_id = ct.id ORDER BY n.id DESC LIMIT 1),
                                   ct.name),
               3, sum(c.amount)
        FROM charged AS c
        JOIN accounts AS a ON a.account = c.account
        JOIN trust_charge_types AS ct ON ct.id = c.charge_type
        GROUP BY c.account, ct.id, ct.name
        UNION ALL
        SELECT a.account, 3, f.ord,
               CASE f.ord WHEN 0 THEN 'expense: management fees' ELSE 'expense: leasing fees' END,
               CASE f.ord WHEN 0 THEN 11 ELSE 8 END,
               coalesce((SELECT sum(x.amount) FROM fees AS x
                         WHERE x.account = a.account AND x.ord = f.ord), 0)
        FROM accounts AS a
        CROSS JOIN (VALUES (0), (1)) AS f (ord)
        UNION ALL
        SELECT s.account, 3, coalesce(ea.schedule_e_line, 99),
               CASE WHEN ea.id IS NULL THEN 'expense: bills not in an expense account'
                    -- Each account by its name today (migration 20261106000210).
                    ELSE 'expense: ' || coalesce((SELECT n.name FROM trust_expense_account_names AS n
                                          WHERE n.expense_account_id = ea.id
                                          ORDER BY n.id DESC LIMIT 1), ea.name) END,
               ea.schedule_e_line,
               sum(s.amount)
        FROM spent AS s
        JOIN accounts AS a ON a.account = s.account
        LEFT JOIN trust_expense_accounts AS ea ON ea.id = s.expense
        GROUP BY s.account, ea.id, ea.name, ea.schedule_e_line
        UNION ALL
        -- Deposit interest, on Schedule E's line 19 (Other), once some was paid.
        SELECT d.account, 3, 19, 'expense: security deposit interest', 19, sum(d.amount)
        FROM interest AS d
        JOIN accounts AS a ON a.account = d.account
        GROUP BY d.account
    ),
    sums AS (
        SELECT i.account,
               coalesce(sum(i.total) FILTER (WHERE i.grp = 1), 0) AS income,
               coalesce(sum(i.total) FILTER (WHERE i.grp = 3), 0) AS expenses
        FROM items AS i
        GROUP BY i.account
    ),
    -- The same by Schedule E line (group 6): rents received, and each expense line with money
    -- on it; bills in no expense account are on no line until they are given one.
    schedule AS (
        SELECT i.account, 6 AS grp, i.sched AS ord,
               'Schedule E line ' || i.sched || ': ' || e.label AS item, i.sched,
               sum(i.total) AS total
        FROM items AS i
        JOIN schedule_e AS e ON e.n = i.sched
        GROUP BY i.account, i.sched, e.label
        HAVING i.sched = 3 OR sum(i.total) <> 0
        UNION ALL
        SELECT i.account, 6, 99, 'Schedule E: on no line, bills not in an expense account',
               NULL::integer, sum(i.total)
        FROM items AS i
        WHERE i.grp = 3 AND i.sched IS NULL
        GROUP BY i.account
    ),
    rows AS (
        SELECT i.account, i.grp, i.ord, i.item, i.sched, i.total FROM items AS i
        UNION ALL
        SELECT s.account, 2, 0, 'total income', NULL::integer, s.income FROM sums AS s
        UNION ALL
        SELECT s.account, 4, 0, 'total expenses', NULL::integer, s.expenses FROM sums AS s
        UNION ALL
        SELECT s.account, 5, 0, 'net income', NULL::integer, s.income - s.expenses
        FROM sums AS s
        UNION ALL
        SELECT h.account, h.grp, h.ord, h.item, h.sched, h.total FROM schedule AS h
    ),
    lines AS (
        -- (section, property, account, group, order, item) orders the statement; names sort
        -- by byte (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS name, NULL::text AS account, 0 AS grp, 1 AS ord,
               'PMC' AS item, pmc.display_name AS label, NULL::integer AS sched,
               NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, 2, 'owner', o.display_name, NULL, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, 3, 'period',
               p_from::text || ' to ' || p_through::text || ' UTC, accrual basis', NULL, NULL
        UNION ALL
        SELECT 1, a.name, r.account, r.grp, r.ord, r.item, a.name, r.sched, r.total
        FROM rows AS r
        JOIN accounts AS a ON a.account = r.account
        UNION ALL
        SELECT 2, NULL, NULL, r.grp, r.ord, 'all properties: ' || r.item, NULL, r.sched,
               sum(r.total)
        FROM rows AS r
        GROUP BY r.grp, r.ord, r.item, r.sched
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.account COLLATE "C", l.grp, l.ord,
                        l.item COLLATE "C"
           )::integer,
           -- The PMC's, owner's and period's names sit in the property column of the first
           -- lines.
           l.label,
           l.item,
           l.sched,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE OR REPLACE FUNCTION trust_charge_nsf_fee(p_pmc_id uuid, p_returned_key text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_key trust_idempotency_keys;
    v_lease trust_leases;
    v_on date;
    v_terms trust_nsf_fee_policies;
    v_fee_id uuid;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: charge an NSF fee at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;

    SELECT k.* INTO v_key FROM trust_idempotency_keys AS k
    WHERE k.pmc_id = p_pmc_id AND k.idempotency_key = p_returned_key
    FOR NO KEY UPDATE;
    IF NOT FOUND OR v_key.request -> 'metadata' ->> 'kind' IS DISTINCT FROM 'bounced payment' THEN
        RAISE EXCEPTION 'trust: no returned payment % in PMC %', p_returned_key, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT f.fee_charge_id INTO v_fee_id FROM trust_nsf_fees AS f
    WHERE f.pmc_id = p_pmc_id AND f.returned_key = p_returned_key;
    IF FOUND THEN
        RETURN v_fee_id;
    END IF;

    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.pmc_id = p_pmc_id AND l.id::text = v_key.request -> 'metadata' ->> 'lease_id';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: returned payment % names no lease of PMC %',
            p_returned_key, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Money the bank sent back: each posting moves it from what the payment paid into (the
    -- owner's account, the company's fee account for a fee it keeps, or the tenant's prepaid
    -- rent) back to a trust bank account's cash.
    IF EXISTS (
        SELECT t.id
        FROM pgledger_transfers AS t
        JOIN trust_ledger_accounts AS f ON f.ledger_account_id = t.from_account_id
        JOIN trust_ledger_accounts AS c ON c.ledger_account_id = t.to_account_id
        WHERE t.id = ANY (v_key.transfer_ids)
          AND (f.kind NOT IN ('owner_property', 'pmc_income', 'prepaid_rent')
               OR c.kind <> 'bank_cash')
    ) THEN
        RAISE EXCEPTION 'trust: % moves money that is not a returned payment', p_returned_key
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT (min(t.event_at) AT TIME ZONE 'UTC')::date INTO v_on
    FROM pgledger_transfers AS t WHERE t.id = ANY (v_key.transfer_ids);
    -- The terms in force that day: the latest starting on or before it.
    SELECT p.* INTO v_terms
    FROM trust_nsf_fee_policies AS p
    JOIN trust_units AS u ON u.property_id = p.property_id
    WHERE u.id = v_lease.unit_id AND p.starts_on <= v_on
    ORDER BY p.starts_on DESC
    LIMIT 1;
    IF NOT FOUND OR v_terms.fee = 0 THEN
        RETURN NULL;
    END IF;

    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    VALUES (
        p_pmc_id, v_lease.id, v_on, 'fee', v_terms.fee,
        'NSF fee, payment returned ' || to_char(v_on, 'YYYY-MM-DD')
    )
    RETURNING id INTO v_fee_id;
    INSERT INTO trust_nsf_fees (pmc_id, returned_key, policy_id, fee, fee_charge_id)
    VALUES (p_pmc_id, p_returned_key, v_terms.id, v_terms.fee, v_fee_id);
    RETURN v_fee_id;
END;
$$;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM trust_charge_payments AS cp
        JOIN pgledger_transfers AS t ON t.id = cp.transfer_id
        JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id
        WHERE a.kind = 'pmc_income'
    ) THEN
        RAISE EXCEPTION 'refusing to undo who keeps a fee: a payment paid a fee into the '
            'company''s account';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION trust_apply_payment(p_pmc_id uuid, p_charge_id uuid, p_transfer_id text, p_amount numeric) RETURNS numeric
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_charge trust_charges;
    v_property_id uuid;
    v_transfer pgledger_transfers;
    v_existing numeric;
    v_paid numeric;
    v_hold trust_payment_holds;
BEGIN
    -- The sums below must count every match that committed while this waited for the locks.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: apply a payment at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'trust: a payment applies a positive amount, not %', p_amount
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Matches to one charge, and then to one transfer, wait for each other.
    SELECT c.* INTO v_charge
    FROM trust_charges AS c
    WHERE c.id = p_charge_id AND c.pmc_id = p_pmc_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no charge % in PMC %', p_charge_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_charge.kind = 'credit' THEN
        RAISE EXCEPTION 'trust: a credit is not paid; it takes its amount off what is owed'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT t.* INTO v_transfer
    FROM pgledger_transfers AS t
    WHERE t.id = p_transfer_id
    FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: no transfer %', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF EXISTS (
        SELECT c.transfer_id FROM trust_owner_contributions AS c
        WHERE c.transfer_id = p_transfer_id
    ) THEN
        RAISE EXCEPTION 'trust: transfer % is an owner''s contribution, not a tenant''s payment',
            p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT u.property_id INTO v_property_id
    FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
    WHERE l.id = v_charge.lease_id;
    IF NOT EXISTS (
        SELECT t.ledger_account_id FROM trust_ledger_accounts AS t
        WHERE t.ledger_account_id = v_transfer.to_account_id AND t.pmc_id = p_pmc_id
          AND t.kind = 'owner_property' AND t.property_id = v_property_id
    ) OR NOT EXISTS (
        SELECT f.ledger_account_id FROM trust_ledger_accounts AS f
        WHERE f.ledger_account_id = v_transfer.from_account_id AND f.pmc_id = p_pmc_id
          AND (
              f.kind = 'bank_cash'
              OR (f.kind IN ('prepaid_rent', 'tenant_deposit') AND f.tenant_id IN (
                  SELECT lt.tenant_id FROM trust_lease_tenants AS lt
                  WHERE lt.lease_id = v_charge.lease_id
              ))
          )
    ) THEN
        RAISE EXCEPTION 'trust: transfer % did not pay into the owner''s account for this '
            'charge''s property, from cash or money held for a tenant on its lease', p_transfer_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT p.amount INTO v_existing
    FROM trust_charge_payments AS p
    WHERE p.charge_id = p_charge_id AND p.transfer_id = p_transfer_id;
    IF FOUND THEN
        IF v_existing <> p_amount THEN
            RAISE EXCEPTION 'trust: transfer % already pays % of charge %, not %',
                p_transfer_id, v_existing, p_charge_id, p_amount
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_existing;  -- a retry: the original match
    END IF;

    -- A payment hold on the lease (an eviction, say) in force on the transfer's day (UTC)
    -- refuses it, unless the transfer was allowed for that hold. The lease's row lock orders
    -- this against a hold being placed: one waits for the other, then sees it.
    PERFORM l.id FROM trust_leases AS l WHERE l.id = v_charge.lease_id FOR SHARE;
    SELECT h.* INTO v_hold
    FROM trust_payment_holds AS h
    LEFT JOIN trust_payment_hold_releases AS r ON r.hold_id = h.id
    WHERE h.lease_id = v_charge.lease_id
      AND h.starts_on <= (v_transfer.event_at AT TIME ZONE 'UTC')::date
      AND (r.ends_on IS NULL OR (v_transfer.event_at AT TIME ZONE 'UTC')::date < r.ends_on)
      AND NOT EXISTS (
          SELECT a.hold_id FROM trust_payment_hold_allowances AS a
          WHERE a.hold_id = h.id AND a.transfer_id = p_transfer_id
      )
    ORDER BY h.starts_on, h.id
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'trust: lease % is on a payment hold from % (%); transfer % is not '
            'allowed for it', v_charge.lease_id, v_hold.starts_on, v_hold.reason, p_transfer_id
            USING ERRCODE = 'check_violation',
                  HINT = 'Record an allowance for this transfer (trust_payment_hold_allowances) '
                         'to accept it.';
    END IF;

    -- Paid so far: matched, less what bounced back (trust_reverse_payment).
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.charge_id = p_charge_id;
    SELECT v_paid - coalesce(sum(r.amount), 0) INTO v_paid
    FROM trust_payment_reversals AS r WHERE r.charge_id = p_charge_id;
    -- What waivers took off it (credits linked to it, migration 0079) is settled too.
    SELECT v_paid + coalesce(sum(k.amount), 0) INTO v_paid
    FROM trust_charge_waivers AS w
    JOIN trust_charges AS k ON k.id = w.credit_charge_id
    WHERE w.waived_charge_id = p_charge_id;
    IF v_paid + p_amount > v_charge.amount THEN
        RAISE EXCEPTION 'trust: charge % is % and already paid or waived %; % more is too much',
            p_charge_id, v_charge.amount, v_paid, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    -- A transfer pays only once, even after a reversal: its money left the owner's account.
    SELECT coalesce(sum(p.amount), 0) INTO v_paid
    FROM trust_charge_payments AS p WHERE p.transfer_id = p_transfer_id;
    IF v_paid + p_amount > v_transfer.amount THEN
        RAISE EXCEPTION 'trust: transfer % moved % and already pays %; % more is too much',
            p_transfer_id, v_transfer.amount, v_paid, p_amount
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO trust_charge_payments (pmc_id, charge_id, transfer_id, amount)
    VALUES (p_pmc_id, p_charge_id, p_transfer_id, p_amount);
    RETURN p_amount;
END;
$$;

CREATE OR REPLACE FUNCTION trust_report_income_statement_accrual(p_pmc_id uuid, p_owner_id uuid, p_from date, p_through date) RETURNS TABLE(line integer, property text, item text, schedule_e integer, amount numeric)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz;
    v_end timestamptz;
BEGIN
    IF p_from IS NULL OR p_through IS NULL OR p_through < p_from THEN
        RAISE EXCEPTION 'trust: an income statement runs from a day through a day no earlier, not % to %',
            p_from, p_through
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (
        SELECT o.id FROM trust_owners AS o WHERE o.id = p_owner_id AND o.pmc_id = p_pmc_id
    ) THEN
        RAISE EXCEPTION 'trust: no owner % in PMC %', p_owner_id, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_start := p_from::timestamp AT TIME ZONE 'UTC';
    v_end := (p_through + 1)::timestamp AT TIME ZONE 'UTC';

    RETURN QUERY
    WITH schedule_e (n, label) AS (
        VALUES (3, 'Rents received'), (5, 'Advertising'), (6, 'Auto and travel'),
               (7, 'Cleaning and maintenance'), (8, 'Commissions'), (9, 'Insurance'),
               (10, 'Legal and other professional fees'), (11, 'Management fees'),
               (12, 'Mortgage interest paid to banks, etc.'), (13, 'Other interest'),
               (14, 'Repairs'), (15, 'Supplies'), (16, 'Taxes'), (17, 'Utilities'),
               (18, 'Depreciation expense or depletion'), (19, 'Other')
    ),
    incomes (part, label) AS (
        VALUES (1, 'income: rent'), (2, 'income: recurring charges'), (3, 'income: late fees'),
               (4, 'income: NSF fees'), (5, 'income: other charges')
    ),
    accounts AS (
        SELECT t.ledger_account_id AS account, pr.display_name AS name, t.property_id
        FROM trust_ledger_accounts AS t
        JOIN trust_properties AS pr ON pr.id = t.property_id
        WHERE t.pmc_id = p_pmc_id AND t.owner_id = p_owner_id AND t.kind = 'owner_property'
    ),
    -- What each charge is: rent, a recurring charge (pet rent, parking), a late fee, an NSF
    -- fee, or another charge, and the company's own type of it if it has one (its own, or its
    -- recurring charge's), as the cash statement has it. A lease end's adjustment of a rent or
    -- recurring charge (migration 0051), and a waiver (migration 0079), are what they change. A
    -- credit given on its own comes off rent, as credits come off a lease's rent first
    -- (migration 0093).
    classes AS (
        SELECT c.id,
               coalesce(o.charge_type_id, c.charge_type_id, rc.charge_type_id) AS charge_type,
               CASE
                   WHEN coalesce(o.kind, c.kind) = 'rent' THEN 1
                   WHEN coalesce(o.recurring_charge_id, c.recurring_charge_id) IS NOT NULL THEN 2
                   WHEN lf.fee_charge_id IS NOT NULL THEN 3
                   WHEN nf.fee_charge_id IS NOT NULL THEN 4
                   WHEN coalesce(o.kind, c.kind) = 'credit' THEN 1
                   ELSE 5
               END AS part
        FROM trust_charges AS c
        LEFT JOIN trust_lease_end_adjustments AS adj ON adj.adjustment_charge_id = c.id
        LEFT JOIN trust_charge_waivers AS w ON w.credit_charge_id = c.id
        LEFT JOIN trust_charges AS o ON o.id = coalesce(adj.charge_id, w.waived_charge_id)
        LEFT JOIN trust_late_fees AS lf ON lf.fee_charge_id = coalesce(o.id, c.id)
        LEFT JOIN trust_nsf_fees AS nf ON nf.fee_charge_id = coalesce(o.id, c.id)
        LEFT JOIN trust_recurring_charges AS rc
            ON rc.id = coalesce(o.recurring_charge_id, c.recurring_charge_id)
        WHERE c.pmc_id = p_pmc_id
    ),
    -- Each account's share of its property on a day: the account's percent of the ownership in
    -- force (migration 0090), all of it when the property has none.
    -- Charged in the days (accrual basis): each charge due in them of a lease at the account's
    -- property (a credit as less), and the housing authority's part of the rent and its
    -- adjustments (migration 0095) as rent, each at the account's share on its due day, to the
    -- cent.
    owed AS (
        SELECT l.id AS lease, u.property_id, c.id AS charge, NULL::integer AS part, c.due_on,
               CASE c.kind WHEN 'credit' THEN -c.amount ELSE c.amount END AS amount
        FROM trust_charges AS c
        JOIN trust_leases AS l ON l.id = c.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE c.pmc_id = p_pmc_id AND c.due_on BETWEEN p_from AND p_through
        UNION ALL
        -- An authority's late fee (migration 0116) is a late fee.
        SELECT l.id, u.property_id, NULL, CASE ac.source WHEN 'late fee' THEN 3 ELSE 1 END,
               ac.due_on, CASE ac.kind WHEN 'credit' THEN -ac.amount ELSE ac.amount END
        FROM trust_assistance_charges AS ac
        JOIN trust_leases AS l ON l.id = ac.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE ac.pmc_id = p_pmc_id AND ac.due_on BETWEEN p_from AND p_through
    ),
    charged AS (
        SELECT a.account, coalesce(x.part, k.part) AS part, k.charge_type,
               round(x.amount * CASE
                   WHEN EXISTS (SELECT 1 FROM trust_ownership_shares AS sh
                                WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on)
                   THEN coalesce((
                       SELECT sl.percent FROM trust_ownership_share_lines AS sl
                       WHERE sl.ledger_account_id = a.account AND sl.share_id = (
                           SELECT sh.id FROM trust_ownership_shares AS sh
                           WHERE sh.property_id = a.property_id AND sh.starts_on <= x.due_on
                           ORDER BY sh.starts_on DESC, sh.created_at DESC, sh.id DESC LIMIT 1
                       )
                   ), 0) / 100
                   ELSE 1
               END, 2) AS amount
        FROM owed AS x
        JOIN accounts AS a ON a.property_id = x.property_id
        LEFT JOIN classes AS k ON k.id = x.charge
    ),
    -- The owner's bills dated in the days, but those the owner declined by their end
    -- (migration 0041): one is never paid.
    entered AS (
        SELECT b.id, b.ledger_account_id AS account, b.amount
        FROM trust_bills AS b
        JOIN accounts AS a ON a.account = b.ledger_account_id
        WHERE b.pmc_id = p_pmc_id AND b.bill_date BETWEEN p_from AND p_through
          AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS d
                          WHERE d.bill_id = b.id AND d.declined_on <= p_through)
    ),
    fees AS (
        SELECT f.ledger_account_id AS account, 0 AS ord, tr.amount
        FROM trust_management_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        SELECT f.ledger_account_id, 1, tr.amount
        FROM trust_leasing_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A fee staff took from the owner (migration 0082): a lease-up fee with the leasing
        -- fees, an inspection or other fee with the management fees.
        SELECT f.ledger_account_id, CASE f.kind WHEN 'lease_up' THEN 1 ELSE 0 END, tr.amount
        FROM trust_owner_fees AS f
        JOIN pgledger_transfers AS tr ON tr.id = f.transfer_id
        WHERE f.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
        UNION ALL
        -- A bill's markup (migration 0083), with the management fees, with its bill.
        SELECT b.account, 0, m.amount
        FROM trust_bill_markups AS m
        JOIN entered AS b ON b.id = m.bill_id
    ),
    -- Interest on tenants' deposits the owner's account paid into them (migration 0122), when it
    -- moved; interest paid as a rent credit moves no money and is less rent, as a credit is.
    interest AS (
        SELECT l.owner_account AS account, l.amount
        FROM trust_deposit_interest_lines AS l
        JOIN pgledger_transfers AS tr ON tr.id = l.transfer_id
        WHERE l.pmc_id = p_pmc_id AND tr.event_at >= v_start AND tr.event_at < v_end
    ),
    -- Each bill's expense account: its latest row.
    categorized AS (
        SELECT DISTINCT ON (x.bill_id) x.id, x.bill_id, x.expense_account_id
        FROM trust_bill_expense_accounts AS x
        WHERE x.pmc_id = p_pmc_id
        ORDER BY x.bill_id, x.id DESC
    ),
    -- Bills dated in the days (accrual basis): a split bill (migration 0069) by its lines,
    -- each in its expense account, which come to the bill.
    spent AS (
        SELECT b.account,
               coalesce(sp.expense_account_id, cz.expense_account_id) AS expense,
               coalesce(sp.amount, b.amount) AS amount
        FROM entered AS b
        LEFT JOIN categorized AS cz ON cz.bill_id = b.id
        LEFT JOIN trust_bill_splits AS sp ON sp.categorized_id = cz.id
    ),
    -- Each account's lines: (group, order within it, item, Schedule E line, amount). Groups:
    -- 1 income, 3 expenses. Income and the fees show even when nothing moved; each of the
    -- company's charge types has a line of its own after them once money was collected on it,
    -- and isn't counted again in the line it would be in without one.
    items AS (
        SELECT a.account, 1 AS grp, i.part AS ord, i.label AS item, 3 AS sched,
               coalesce((SELECT sum(c.amount) FROM charged AS c
                         WHERE c.account = a.account AND c.part = i.part
                           AND c.charge_type IS NULL), 0) AS total
        FROM accounts AS a
        CROSS JOIN incomes AS i
        UNION ALL
        -- Each type by its name today (migration 20261106000210).
        SELECT c.account, 1, 6,
               'income: ' || coalesce((SELECT n.name FROM trust_charge_type_names AS n
                                    WHERE n.charge_type_id = ct.id ORDER BY n.id DESC LIMIT 1),
                                   ct.name),
               3, sum(c.amount)
        FROM charged AS c
        JOIN accounts AS a ON a.account = c.account
        JOIN trust_charge_types AS ct ON ct.id = c.charge_type
        GROUP BY c.account, ct.id, ct.name
        UNION ALL
        SELECT a.account, 3, f.ord,
               CASE f.ord WHEN 0 THEN 'expense: management fees' ELSE 'expense: leasing fees' END,
               CASE f.ord WHEN 0 THEN 11 ELSE 8 END,
               coalesce((SELECT sum(x.amount) FROM fees AS x
                         WHERE x.account = a.account AND x.ord = f.ord), 0)
        FROM accounts AS a
        CROSS JOIN (VALUES (0), (1)) AS f (ord)
        UNION ALL
        SELECT s.account, 3, coalesce(ea.schedule_e_line, 99),
               CASE WHEN ea.id IS NULL THEN 'expense: bills not in an expense account'
                    -- Each account by its name today (migration 20261106000210).
                    ELSE 'expense: ' || coalesce((SELECT n.name FROM trust_expense_account_names AS n
                                          WHERE n.expense_account_id = ea.id
                                          ORDER BY n.id DESC LIMIT 1), ea.name) END,
               ea.schedule_e_line,
               sum(s.amount)
        FROM spent AS s
        JOIN accounts AS a ON a.account = s.account
        LEFT JOIN trust_expense_accounts AS ea ON ea.id = s.expense
        GROUP BY s.account, ea.id, ea.name, ea.schedule_e_line
        UNION ALL
        -- Deposit interest, on Schedule E's line 19 (Other), once some was paid.
        SELECT d.account, 3, 19, 'expense: security deposit interest', 19, sum(d.amount)
        FROM interest AS d
        JOIN accounts AS a ON a.account = d.account
        GROUP BY d.account
    ),
    sums AS (
        SELECT i.account,
               coalesce(sum(i.total) FILTER (WHERE i.grp = 1), 0) AS income,
               coalesce(sum(i.total) FILTER (WHERE i.grp = 3), 0) AS expenses
        FROM items AS i
        GROUP BY i.account
    ),
    -- The same by Schedule E line (group 6): rents received, and each expense line with money
    -- on it; bills in no expense account are on no line until they are given one.
    schedule AS (
        SELECT i.account, 6 AS grp, i.sched AS ord,
               'Schedule E line ' || i.sched || ': ' || e.label AS item, i.sched,
               sum(i.total) AS total
        FROM items AS i
        JOIN schedule_e AS e ON e.n = i.sched
        GROUP BY i.account, i.sched, e.label
        HAVING i.sched = 3 OR sum(i.total) <> 0
        UNION ALL
        SELECT i.account, 6, 99, 'Schedule E: on no line, bills not in an expense account',
               NULL::integer, sum(i.total)
        FROM items AS i
        WHERE i.grp = 3 AND i.sched IS NULL
        GROUP BY i.account
    ),
    rows AS (
        SELECT i.account, i.grp, i.ord, i.item, i.sched, i.total FROM items AS i
        UNION ALL
        SELECT s.account, 2, 0, 'total income', NULL::integer, s.income FROM sums AS s
        UNION ALL
        SELECT s.account, 4, 0, 'total expenses', NULL::integer, s.expenses FROM sums AS s
        UNION ALL
        SELECT s.account, 5, 0, 'net income', NULL::integer, s.income - s.expenses
        FROM sums AS s
        UNION ALL
        SELECT h.account, h.grp, h.ord, h.item, h.sched, h.total FROM schedule AS h
    ),
    lines AS (
        -- (section, property, account, group, order, item) orders the statement; names sort
        -- by byte (COLLATE "C"), the same on every server.
        SELECT 0 AS section, NULL::text AS name, NULL::text AS account, 0 AS grp, 1 AS ord,
               'PMC' AS item, pmc.display_name AS label, NULL::integer AS sched,
               NULL::numeric AS total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, 2, 'owner', o.display_name, NULL, NULL
        FROM trust_owners AS o WHERE o.id = p_owner_id
        UNION ALL
        SELECT 0, NULL, NULL, 0, 3, 'period',
               p_from::text || ' to ' || p_through::text || ' UTC, accrual basis', NULL, NULL
        UNION ALL
        SELECT 1, a.name, r.account, r.grp, r.ord, r.item, a.name, r.sched, r.total
        FROM rows AS r
        JOIN accounts AS a ON a.account = r.account
        UNION ALL
        SELECT 2, NULL, NULL, r.grp, r.ord, 'all properties: ' || r.item, NULL, r.sched,
               sum(r.total)
        FROM rows AS r
        GROUP BY r.grp, r.ord, r.item, r.sched
    )
    SELECT row_number() OVER (
               ORDER BY l.section, l.name COLLATE "C", l.account COLLATE "C", l.grp, l.ord,
                        l.item COLLATE "C"
           )::integer,
           -- The PMC's, owner's and period's names sit in the property column of the first
           -- lines.
           l.label,
           l.item,
           l.sched,
           round(l.total, 2)
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE OR REPLACE FUNCTION trust_charge_nsf_fee(p_pmc_id uuid, p_returned_key text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
DECLARE
    v_key trust_idempotency_keys;
    v_lease trust_leases;
    v_on date;
    v_terms trust_nsf_fee_policies;
    v_fee_id uuid;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'trust: charge an NSF fee at READ COMMITTED, not %',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;

    SELECT k.* INTO v_key FROM trust_idempotency_keys AS k
    WHERE k.pmc_id = p_pmc_id AND k.idempotency_key = p_returned_key
    FOR NO KEY UPDATE;
    IF NOT FOUND OR v_key.request -> 'metadata' ->> 'kind' IS DISTINCT FROM 'bounced payment' THEN
        RAISE EXCEPTION 'trust: no returned payment % in PMC %', p_returned_key, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT f.fee_charge_id INTO v_fee_id FROM trust_nsf_fees AS f
    WHERE f.pmc_id = p_pmc_id AND f.returned_key = p_returned_key;
    IF FOUND THEN
        RETURN v_fee_id;
    END IF;

    SELECT l.* INTO v_lease FROM trust_leases AS l
    WHERE l.pmc_id = p_pmc_id AND l.id::text = v_key.request -> 'metadata' ->> 'lease_id';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trust: returned payment % names no lease of PMC %',
            p_returned_key, p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Money the bank sent back: each posting moves it from what the payment paid into (the
    -- owner's account or the tenant's prepaid rent) back to a trust bank account's cash.
    IF EXISTS (
        SELECT t.id
        FROM pgledger_transfers AS t
        JOIN trust_ledger_accounts AS f ON f.ledger_account_id = t.from_account_id
        JOIN trust_ledger_accounts AS c ON c.ledger_account_id = t.to_account_id
        WHERE t.id = ANY (v_key.transfer_ids)
          AND (f.kind NOT IN ('owner_property', 'prepaid_rent') OR c.kind <> 'bank_cash')
    ) THEN
        RAISE EXCEPTION 'trust: % moves money that is not a returned payment', p_returned_key
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT (min(t.event_at) AT TIME ZONE 'UTC')::date INTO v_on
    FROM pgledger_transfers AS t WHERE t.id = ANY (v_key.transfer_ids);
    -- The terms in force that day: the latest starting on or before it.
    SELECT p.* INTO v_terms
    FROM trust_nsf_fee_policies AS p
    JOIN trust_units AS u ON u.property_id = p.property_id
    WHERE u.id = v_lease.unit_id AND p.starts_on <= v_on
    ORDER BY p.starts_on DESC
    LIMIT 1;
    IF NOT FOUND OR v_terms.fee = 0 THEN
        RETURN NULL;
    END IF;

    INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
    VALUES (
        p_pmc_id, v_lease.id, v_on, 'fee', v_terms.fee,
        'NSF fee, payment returned ' || to_char(v_on, 'YYYY-MM-DD')
    )
    RETURNING id INTO v_fee_id;
    INSERT INTO trust_nsf_fees (pmc_id, returned_key, policy_id, fee, fee_charge_id)
    VALUES (p_pmc_id, p_returned_key, v_terms.id, v_terms.fee, v_fee_id);
    RETURN v_fee_id;
END;
$$;

-- squawk-ignore ban-drop-table
DROP TABLE trust_charge_type_payees;
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_check_charge_type_payee();
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_charge_payee(trust_charges);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_charge_type_payee(uuid);
