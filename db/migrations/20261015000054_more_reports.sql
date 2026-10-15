-- migrate:up
-- More reports (founder, 2026-10-15, the fourth gap against Buildium): a balance sheet of the
-- trust, cash flow, vacancy, work orders, and directories of tenants, owners and vendors. Each
-- reads and writes nothing, takes the PMC first and runs with its caller's rights, so
-- trust_app and trust_ai_agent run them like every report. Days are UTC and text sorts by
-- bytes (COLLATE "C"), so a report prints the same on every server.
--
--   trust_report_balance_sheet       each trust bank account's cash by the books against what
--                                    it holds, by kind, at a day's end; then all of them.
--   trust_report_cash_flow           each trust bank account's cash over days: opening, what
--                                    came in and went out by what it was, closing.
--   trust_report_vacancy             each unit without a lease in effect on a day: since when,
--                                    its last rent and the next lease booked; then occupancy.
--   trust_report_work_orders         each work order opened by a day, open ones first: its
--                                    status and vendor then, days open and what was billed.
--   trust_report_tenant_directory    each tenant and their lease on a day (or the next one, or
--                                    the last one), with who else is on it.
--   trust_report_owner_directory     each owner's property accounts: the agreement in force on
--                                    a day and the balance at its end.
--   trust_report_vendor_directory    each vendor: W-9 on file, open work orders, unpaid bills,
--                                    paid this calendar year and last paid, as of a day.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE FUNCTION trust_report_balance_sheet(p_pmc_id uuid, p_as_of date)
RETURNS TABLE (line integer, section text, bank text, item text, amount numeric)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a balance sheet is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH balances AS (
        SELECT t.bank_account_id, t.kind,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0.00) AS balance
        FROM trust_ledger_accounts AS t
        WHERE t.pmc_id = p_pmc_id
    ),
    -- What a trust bank account holds, by kind, in this order.
    kinds (kind, step, label) AS (
        VALUES ('owner_property', 1, 'owners'' funds'),
               ('tenant_deposit', 2, 'tenants'' security deposits'),
               ('prepaid_rent', 3, 'tenants'' prepaid rent'),
               ('vendor_payable', 4, 'set aside for vendors'' bills'),
               ('pmc_income', 5, 'the company''s fees not yet taken')
    ),
    banks AS (
        -- Book cash is what the bank holds: the cash account's balance runs below zero as
        -- money arrives.
        SELECT b.id, b.display_name || ' (' || b.kind || ')' AS name,
               -coalesce(sum(x.balance) FILTER (WHERE x.kind = 'bank_cash'), 0.00) AS cash,
               coalesce(sum(x.balance) FILTER (WHERE x.kind <> 'bank_cash'), 0.00) AS held
        FROM trust_bank_accounts AS b
        LEFT JOIN balances AS x ON x.bank_account_id = b.id
        WHERE b.pmc_id = p_pmc_id
        GROUP BY b.id, b.display_name, b.kind
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS step,
               'PMC' AS part_name, pmc.display_name AS label, NULL::numeric AS value
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, 'as of', to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC',
               NULL
        UNION ALL
        SELECT 1, b.name, b.id, 0, 'assets', 'cash in the bank, by the books', b.cash
        FROM banks AS b
        UNION ALL
        SELECT 1, b.name, b.id, 10 + k.step, 'held in trust', k.label, sum(x.balance)
        FROM banks AS b
        JOIN balances AS x ON x.bank_account_id = b.id
        JOIN kinds AS k ON k.kind = x.kind
        GROUP BY b.name, b.id, k.step, k.label
        UNION ALL
        SELECT 1, b.name, b.id, 20, 'held in trust', 'total held in trust', b.held
        FROM banks AS b
        UNION ALL
        SELECT 1, b.name, b.id, 21, 'difference', 'cash less what is held', b.cash - b.held
        FROM banks AS b
        UNION ALL
        SELECT 2, NULL, NULL, 0, 'all trust bank accounts', 'cash in the bank, by the books',
               coalesce((SELECT sum(b.cash) FROM banks AS b), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 10 + k.step, 'all trust bank accounts', k.label, sum(x.balance)
        FROM balances AS x
        JOIN kinds AS k ON k.kind = x.kind
        GROUP BY k.step, k.label
        UNION ALL
        SELECT 2, NULL, NULL, 20, 'all trust bank accounts', 'total held in trust',
               coalesce((SELECT sum(b.held) FROM banks AS b), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 21, 'all trust bank accounts', 'cash less what is held',
               coalesce((SELECT sum(b.cash - b.held) FROM banks AS b), 0.00)
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.bank_name COLLATE "C", l.bank_id, l.step
            ))::integer,
           l.part_name, l.bank_name, l.label, l.value
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE FUNCTION trust_report_cash_flow(p_pmc_id uuid, p_from date, p_through date)
RETURNS TABLE (line integer, section text, bank text, item text, amount numeric)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz := p_from::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := (p_through + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_from IS NULL OR p_through IS NULL OR p_through < p_from THEN
        RAISE EXCEPTION 'trust: cash flow is over days, from one through another no earlier'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH cash AS (
        SELECT t.ledger_account_id AS id, b.id AS bank_id,
               b.display_name || ' (' || b.kind || ')' AS name
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        WHERE t.pmc_id = p_pmc_id AND t.kind = 'bank_cash'
    ),
    -- Book cash at a moment: what the bank held, by the books, just before it.
    opening AS (
        SELECT c.bank_id,
               -coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = c.id AND tr.event_at < v_start
               ), 0.00) AS amount
        FROM cash AS c
    ),
    -- Each transfer in the days that moved a bank's cash: in when money came into the bank
    -- (from its cash account to what holds it), out when it left. A move between two banks'
    -- cash is out of one and into the other.
    moves AS (
        SELECT c.bank_id, tr.id AS transfer_id, tr.amount,
               tr.from_account_id = c.id AS came_in,
               o.kind AS other_kind
        FROM cash AS c
        JOIN pgledger_transfers AS tr
          ON (tr.from_account_id = c.id OR tr.to_account_id = c.id)
         AND tr.event_at >= v_start AND tr.event_at < v_end
        JOIN trust_ledger_accounts AS o
          ON o.ledger_account_id = CASE WHEN tr.from_account_id = c.id
                                        THEN tr.to_account_id ELSE tr.from_account_id END
    ),
    named AS (
        SELECT m.bank_id, m.amount, m.came_in,
               CASE
                   WHEN m.other_kind = 'bank_cash' THEN 7
                   WHEN m.came_in AND EXISTS (
                       SELECT o.bank_account_id FROM trust_opening_balances AS o
                       WHERE m.transfer_id = ANY (o.transfer_ids)
                   ) THEN 1
                   WHEN m.came_in AND EXISTS (
                       SELECT x.transfer_id FROM trust_owner_contributions AS x
                       WHERE x.transfer_id = m.transfer_id
                   ) THEN 3
                   WHEN m.came_in AND m.other_kind = 'owner_property' THEN 2
                   WHEN m.came_in AND m.other_kind = 'prepaid_rent' THEN 4
                   WHEN m.came_in AND m.other_kind = 'tenant_deposit' THEN 5
                   WHEN m.came_in THEN 6
                   WHEN m.other_kind = 'vendor_payable' THEN 11
                   WHEN m.other_kind = 'owner_property' AND EXISTS (
                       SELECT x.transfer_id FROM trust_owner_draws AS x
                       WHERE x.transfer_id = m.transfer_id
                   ) THEN 12
                   WHEN m.other_kind = 'owner_property' THEN 13
                   WHEN m.other_kind = 'prepaid_rent' THEN 14
                   WHEN m.other_kind = 'tenant_deposit' THEN 15
                   WHEN m.other_kind = 'pmc_income' THEN 16
                   ELSE 17
               END AS step
        FROM moves AS m
    ),
    labels (step, label) AS (
        VALUES (1, 'in: opening balances carried over'),
               (2, 'in: received for owners'),
               (3, 'in: owner contributions'),
               (4, 'in: received as prepaid rent'),
               (5, 'in: security deposits received'),
               (6, 'in: other'),
               (7, 'moved between trust bank accounts'),
               (11, 'out: bills paid'),
               (12, 'out: owner draws'),
               (13, 'out: paid back from owners'' accounts (returned payments, other)'),
               (14, 'out: paid back from prepaid rent'),
               (15, 'out: security deposits paid out (returned, or kept for owners)'),
               (16, 'out: fees paid to the company'),
               (17, 'out: other')
    ),
    sums AS (
        -- A move between banks is signed: in is more, out is less.
        SELECT n.bank_id, n.step,
               sum(CASE WHEN n.came_in OR n.step <> 7 THEN n.amount ELSE -n.amount END) AS amount
        FROM named AS n
        GROUP BY n.bank_id, n.step
    ),
    totals AS (
        SELECT c.bank_id, c.name, op.amount AS opening,
               coalesce((SELECT sum(n.amount) FROM named AS n
                         WHERE n.bank_id = c.bank_id AND n.came_in), 0.00) AS money_in,
               coalesce((SELECT sum(n.amount) FROM named AS n
                         WHERE n.bank_id = c.bank_id AND NOT n.came_in), 0.00) AS money_out
        FROM cash AS c
        JOIN opening AS op ON op.bank_id = c.bank_id
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS bank_name, NULL::uuid AS bank_id, 0 AS step,
               'PMC' AS part_name, pmc.display_name AS label, NULL::numeric AS value
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 1, 'period',
               to_char(p_from, 'YYYY-MM-DD') || ' to ' || to_char(p_through, 'YYYY-MM-DD')
               || ' UTC', NULL
        UNION ALL
        SELECT 1, t.name, t.bank_id, 0, 'cash', 'opening cash, by the books', t.opening
        FROM totals AS t
        UNION ALL
        SELECT 1, t.name, t.bank_id, s.step, 'cash', l.label, s.amount
        FROM totals AS t
        JOIN sums AS s ON s.bank_id = t.bank_id
        JOIN labels AS l ON l.step = s.step
        UNION ALL
        SELECT 1, t.name, t.bank_id, 30, 'cash', 'total in', t.money_in FROM totals AS t
        UNION ALL
        SELECT 1, t.name, t.bank_id, 31, 'cash', 'total out', t.money_out FROM totals AS t
        UNION ALL
        SELECT 1, t.name, t.bank_id, 32, 'cash', 'closing cash, by the books',
               t.opening + t.money_in - t.money_out
        FROM totals AS t
        UNION ALL
        SELECT 2, NULL, NULL, 0, 'all trust bank accounts', 'opening cash, by the books',
               coalesce((SELECT sum(t.opening) FROM totals AS t), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, s.step, 'all trust bank accounts', l.label, sum(s.amount)
        FROM sums AS s
        JOIN labels AS l ON l.step = s.step
        GROUP BY s.step, l.label
        UNION ALL
        SELECT 2, NULL, NULL, 30, 'all trust bank accounts', 'total in',
               coalesce((SELECT sum(t.money_in) FROM totals AS t), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 31, 'all trust bank accounts', 'total out',
               coalesce((SELECT sum(t.money_out) FROM totals AS t), 0.00)
        UNION ALL
        SELECT 2, NULL, NULL, 32, 'all trust bank accounts', 'closing cash, by the books',
               coalesce((SELECT sum(t.opening + t.money_in - t.money_out) FROM totals AS t), 0.00)
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.bank_name COLLATE "C", l.bank_id, l.step
            ))::integer,
           l.part_name, l.bank_name, l.label, l.value
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE FUNCTION trust_report_vacancy(p_pmc_id uuid, p_as_of date)
RETURNS TABLE (
    line integer, item text, property text, unit text, vacant_since date, days_vacant integer,
    last_rent numeric, next_lease_starts date
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: vacancy is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH units AS (
        SELECT u.id, pr.display_name AS place, u.display_name AS unit_name,
               EXISTS (
                   SELECT l.id FROM trust_leases AS l
                   WHERE l.unit_id = u.id AND l.starts_on <= p_as_of
                     AND (l.ends_on IS NULL OR l.ends_on >= p_as_of)
               ) AS occupied,
               last.ends_on + 1 AS since,
               last.monthly_rent AS rent,
               (SELECT min(l.starts_on) FROM trust_leases AS l
                WHERE l.unit_id = u.id AND l.starts_on > p_as_of) AS next_start
        FROM trust_units AS u
        JOIN trust_properties AS pr ON pr.id = u.property_id
        LEFT JOIN LATERAL (
            SELECT l.ends_on, l.monthly_rent FROM trust_leases AS l
            WHERE l.unit_id = u.id AND l.ends_on < p_as_of
            ORDER BY l.ends_on DESC
            LIMIT 1
        ) AS last ON true
        WHERE u.pmc_id = p_pmc_id
    ),
    counted AS (
        SELECT count(*) AS total, count(*) FILTER (WHERE o.occupied) AS occupied FROM units AS o
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS place, NULL::text AS unit_name, NULL::uuid AS unit_id,
               0 AS step, 'PMC' AS what, pmc.display_name AS label, NULL::date AS since,
               NULL::integer AS days, NULL::numeric AS rent, NULL::date AS next_start
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, 1, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL
        UNION ALL
        -- A unit never leased has no day it became vacant: unknown, not zero.
        SELECT 1, o.place, o.unit_name, o.id, 0, 'vacant', o.place, o.since,
               p_as_of - o.since + 1, o.rent, o.next_start
        FROM units AS o
        WHERE NOT o.occupied
        UNION ALL
        SELECT 2, NULL, NULL, NULL, 0, 'total',
               CASE WHEN c.total = 0 THEN 'no units'
                    ELSE c.total || ' units: ' || c.occupied || ' occupied, '
                         || (c.total - c.occupied) || ' vacant ('
                         || round(100.0 * c.occupied / c.total, 1) || '% occupied)'
               END,
               NULL, NULL, NULL, NULL
        FROM counted AS c
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.place COLLATE "C", l.unit_name COLLATE "C", l.unit_id, l.step
            ))::integer,
           l.what, l.label, l.unit_name, l.since, l.days, l.rent, l.next_start
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE FUNCTION trust_report_work_orders(p_pmc_id uuid, p_as_of date)
RETURNS TABLE (
    line integer, item text, property text, unit text, summary text, opened_on date,
    status text, vendor text, days_open integer, billed numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: work orders are as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH orders AS (
        SELECT w.id, pr.display_name AS place, u.display_name AS unit_name, w.summary AS what,
               (w.opened_at AT TIME ZONE 'UTC')::date AS opened,
               coalesce(last.step, 'open') AS state,
               (last.taken_at AT TIME ZONE 'UTC')::date AS last_on,
               coalesce(last.step IN ('completed', 'cancelled'), false) AS closed,
               (SELECT v.display_name
                FROM trust_work_order_steps AS s
                JOIN trust_vendors AS v ON v.id = s.vendor_id
                WHERE s.work_order_id = w.id AND s.step = 'assigned' AND s.taken_at < v_cutoff
                ORDER BY s.taken_at DESC, s.created_at DESC, s.id DESC
                LIMIT 1) AS vendor_name,
               -- Bills entered for it by the day and not declined by then.
               coalesce((
                   SELECT sum(b.amount) FROM trust_bills AS b
                   WHERE b.work_order_id = w.id AND b.bill_date <= p_as_of
                     AND NOT EXISTS (
                         SELECT d.bill_id FROM trust_bill_declines AS d
                         WHERE d.bill_id = b.id AND d.declined_on <= p_as_of
                     )
               ), 0.00) AS billed_total
        FROM trust_work_orders AS w
        JOIN trust_properties AS pr ON pr.id = w.property_id
        LEFT JOIN trust_units AS u ON u.id = w.unit_id
        LEFT JOIN LATERAL (
            SELECT s.step, s.taken_at FROM trust_work_order_steps AS s
            WHERE s.work_order_id = w.id AND s.taken_at < v_cutoff
            ORDER BY s.taken_at DESC, s.created_at DESC, s.id DESC
            LIMIT 1
        ) AS last ON true
        WHERE w.pmc_id = p_pmc_id AND w.opened_at < v_cutoff
    ),
    lines AS (
        SELECT 0 AS part, 0 AS step, NULL::date AS opened, NULL::text AS place,
               NULL::uuid AS order_id, 'PMC' AS what, pmc.display_name AS label,
               NULL::text AS unit_name, NULL::text AS about, NULL::text AS state,
               NULL::text AS vendor_name, NULL::integer AS days, NULL::numeric AS billed_total
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, 1, NULL, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL,
               NULL, NULL
        UNION ALL
        -- Open ones first, oldest first; then the closed ones.
        SELECT 1, CASE WHEN o.closed THEN 1 ELSE 0 END, o.opened, o.place, o.id,
               CASE WHEN o.closed THEN 'closed' ELSE 'open' END, o.place, o.unit_name, o.what,
               o.state, o.vendor_name,
               CASE WHEN o.closed THEN o.last_on ELSE p_as_of END - o.opened, o.billed_total
        FROM orders AS o
        UNION ALL
        SELECT 2, 0, NULL, NULL, NULL, 'total', NULL, NULL,
               count(*) FILTER (WHERE NOT o.closed) || ' open ('
               || count(*) FILTER (WHERE o.state = 'assigned') || ' assigned), '
               || count(*) FILTER (WHERE o.state = 'completed') || ' completed, '
               || count(*) FILTER (WHERE o.state = 'cancelled') || ' cancelled',
               NULL, NULL, NULL, coalesce(sum(o.billed_total), 0.00)
        FROM orders AS o
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.step, l.opened, l.place COLLATE "C", l.order_id
            ))::integer,
           l.what, l.label, l.unit_name, l.about, l.opened, l.state, l.vendor_name, l.days,
           l.billed_total
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE FUNCTION trust_report_tenant_directory(p_pmc_id uuid, p_as_of date)
RETURNS TABLE (
    line integer, item text, tenant text, property text, unit text, lease_starts date,
    lease_ends date, monthly_rent numeric, co_tenants text
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a tenant directory is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH tenants AS (
        SELECT t.id, t.display_name AS who, coalesce(lp.display_name, tp.display_name) AS place,
               lu.display_name AS unit_name, l.lease_id, l.starts_on, l.ends_on, l.rent,
               coalesce(l.standing, 4) AS standing
        FROM trust_tenants AS t
        JOIN trust_properties AS tp ON tp.id = t.property_id
        -- Their lease in effect on the day; or else the next one; or else the last one.
        LEFT JOIN LATERAL (
            SELECT l.id AS lease_id, l.unit_id, l.starts_on, l.ends_on, l.monthly_rent AS rent,
                   CASE WHEN l.starts_on <= p_as_of
                             AND (l.ends_on IS NULL OR l.ends_on >= p_as_of) THEN 1
                        WHEN l.starts_on > p_as_of THEN 2
                        ELSE 3 END AS standing
            FROM trust_lease_tenants AS lt
            JOIN trust_leases AS l ON l.id = lt.lease_id
            WHERE lt.tenant_id = t.id
            ORDER BY standing,
                     CASE WHEN l.starts_on > p_as_of THEN l.starts_on - p_as_of
                          WHEN l.ends_on < p_as_of THEN p_as_of - l.ends_on
                          ELSE 0 END,
                     l.starts_on, l.id
            LIMIT 1
        ) AS l ON true
        LEFT JOIN trust_units AS lu ON lu.id = l.unit_id
        LEFT JOIN trust_properties AS lp ON lp.id = lu.property_id
        WHERE t.pmc_id = p_pmc_id
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS place, NULL::text AS unit_name, NULL::text AS who,
               NULL::uuid AS tenant_id, 0 AS step, 'PMC' AS what, pmc.display_name AS label,
               NULL::date AS starts_on, NULL::date AS ends_on, NULL::numeric AS rent,
               NULL::text AS others
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, NULL, 1, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, x.place, x.unit_name, x.who, x.id, 0,
               (ARRAY['current lease', 'next lease', 'last lease', 'no lease'])[x.standing],
               x.who, x.starts_on, x.ends_on, x.rent,
               (SELECT string_agg(o.display_name, ', ' ORDER BY o.display_name COLLATE "C", o.id)
                FROM trust_lease_tenants AS lt
                JOIN trust_tenants AS o ON o.id = lt.tenant_id
                WHERE lt.lease_id = x.lease_id AND lt.tenant_id <> x.id)
        FROM tenants AS x
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.place COLLATE "C", l.unit_name COLLATE "C" NULLS LAST,
                         l.who COLLATE "C", l.tenant_id, l.step
            ))::integer,
           l.what, l.label, l.place, l.unit_name, l.starts_on, l.ends_on, l.rent, l.others
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE FUNCTION trust_report_owner_directory(p_pmc_id uuid, p_as_of date)
RETURNS TABLE (
    line integer, item text, owner text, property text, bank text, fee_percent numeric,
    minimum_fee numeric, flat_fee numeric, reserve numeric, approval_limit numeric,
    balance numeric
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: an owner directory is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH accounts AS (
        SELECT o.id AS owner_id, o.display_name AS who, pr.display_name AS place,
               b.display_name || ' (' || b.kind || ')' AS bank_name, t.ledger_account_id,
               -- The agreement in force on the day, as the fee and draw functions take it.
               (SELECT a FROM trust_management_agreements AS a
                WHERE a.ledger_account_id = t.ledger_account_id AND a.starts_on <= p_as_of
                ORDER BY a.starts_on DESC
                LIMIT 1) AS terms,
               coalesce((
                   SELECT sum(e.amount)
                   FROM pgledger_entries AS e
                   JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
                   WHERE e.account_id = t.ledger_account_id AND tr.event_at < v_cutoff
               ), 0.00) AS held
        FROM trust_owners AS o
        JOIN trust_ledger_accounts AS t ON t.owner_id = o.id AND t.kind = 'owner_property'
        JOIN trust_properties AS pr ON pr.id = t.property_id
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        WHERE o.pmc_id = p_pmc_id
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS who, NULL::uuid AS owner_id, NULL::text AS place,
               NULL::text AS account_id, 'PMC' AS what, pmc.display_name AS label,
               NULL::text AS bank_name, NULL::trust_management_agreements AS terms,
               NULL::numeric AS held
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, NULL, NULL, 'as of',
               to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC', NULL, NULL, NULL
        UNION ALL
        -- No agreement in force leaves its terms empty: unknown, not zero.
        SELECT 1, x.who, x.owner_id, x.place, x.ledger_account_id,
               CASE WHEN x.terms IS NULL THEN 'property, no agreement in force' ELSE 'property'
               END,
               x.who, x.bank_name, x.terms, x.held
        FROM accounts AS x
        UNION ALL
        SELECT 1, o.display_name, o.id, NULL, NULL, 'owner, no property account',
               o.display_name, NULL, NULL, NULL
        FROM trust_owners AS o
        WHERE o.pmc_id = p_pmc_id
          AND NOT EXISTS (SELECT x.owner_id FROM accounts AS x WHERE x.owner_id = o.id)
    )
    SELECT (row_number() OVER (
                ORDER BY l.part, l.who COLLATE "C", l.owner_id, l.place COLLATE "C",
                         l.account_id
            ))::integer,
           l.what, l.label, l.place, l.bank_name, (l.terms).fee_percent, (l.terms).minimum_fee,
           (l.terms).flat_fee, (l.terms).reserve, (l.terms).approval_limit, l.held
    FROM lines AS l
    ORDER BY 1;
END;
$$;

CREATE FUNCTION trust_report_vendor_directory(p_pmc_id uuid, p_as_of date)
RETURNS TABLE (
    line integer, item text, vendor text, w9_on_file text, open_work_orders integer,
    unpaid_bills integer, unpaid numeric, paid_this_year numeric, last_paid_on date
)
LANGUAGE plpgsql
STABLE
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_cutoff timestamptz := (p_as_of + 1)::timestamp AT TIME ZONE 'UTC';
    v_year_start timestamptz := date_trunc('year', p_as_of::timestamp) AT TIME ZONE 'UTC';
BEGIN
    IF p_as_of IS NULL THEN
        RAISE EXCEPTION 'trust: a vendor directory is as of a day; none was given'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH paid AS (
        -- Each bill paid by the end of the day, and when the bank paid it.
        SELECT b.vendor_id, b.amount, tr.event_at
        FROM trust_bills AS b
        JOIN trust_bill_payments AS p ON p.bill_id = b.id AND p.step = 'paid'
        JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
        WHERE b.pmc_id = p_pmc_id AND tr.event_at < v_cutoff
    ),
    unpaid AS (
        -- Entered by the day, not paid by its end and not declined by then.
        SELECT b.vendor_id, b.amount
        FROM trust_bills AS b
        WHERE b.pmc_id = p_pmc_id AND b.bill_date <= p_as_of
          AND NOT EXISTS (
              SELECT p.bill_id FROM trust_bill_payments AS p
              JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
              WHERE p.bill_id = b.id AND p.step = 'paid' AND tr.event_at < v_cutoff
          )
          AND NOT EXISTS (
              SELECT d.bill_id FROM trust_bill_declines AS d
              WHERE d.bill_id = b.id AND d.declined_on <= p_as_of
          )
    ),
    assigned AS (
        -- Each work order open at the day's end, and the vendor it was last assigned to.
        SELECT (SELECT s.vendor_id FROM trust_work_order_steps AS s
                WHERE s.work_order_id = w.id AND s.step = 'assigned' AND s.taken_at < v_cutoff
                ORDER BY s.taken_at DESC, s.created_at DESC, s.id DESC
                LIMIT 1) AS vendor_id
        FROM trust_work_orders AS w
        WHERE w.pmc_id = p_pmc_id AND w.opened_at < v_cutoff
          AND NOT EXISTS (
              SELECT s.work_order_id FROM trust_work_order_steps AS s
              WHERE s.work_order_id = w.id AND s.step IN ('completed', 'cancelled')
                AND s.taken_at < v_cutoff
          )
    ),
    lines AS (
        SELECT 0 AS part, NULL::text AS who, NULL::uuid AS vendor_id, 'PMC' AS what,
               pmc.display_name AS label, NULL::text AS w9, NULL::integer AS orders,
               NULL::integer AS bills, NULL::numeric AS owed, NULL::numeric AS year_paid,
               NULL::date AS last_paid
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 0, NULL, NULL, 'as of', to_char(p_as_of, 'YYYY-MM-DD') || ', end of day UTC',
               NULL, NULL, NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 1, v.display_name, v.id, 'vendor', v.display_name,
               -- A W-9 kept by the day's end and not removed by then.
               CASE WHEN EXISTS (
                   SELECT d.id FROM trust_documents AS d
                   WHERE d.vendor_id = v.id AND d.kind = 'w9' AND d.created_at < v_cutoff
                     AND NOT EXISTS (
                         SELECT r.document_id FROM trust_document_removals AS r
                         WHERE r.document_id = d.id AND r.created_at < v_cutoff
                     )
               ) THEN 'yes' ELSE 'no' END,
               (SELECT count(*) FROM assigned AS a WHERE a.vendor_id = v.id)::integer,
               (SELECT count(*) FROM unpaid AS u WHERE u.vendor_id = v.id)::integer,
               coalesce((SELECT sum(u.amount) FROM unpaid AS u WHERE u.vendor_id = v.id), 0.00),
               coalesce((SELECT sum(p.amount) FROM paid AS p
                         WHERE p.vendor_id = v.id AND p.event_at >= v_year_start), 0.00),
               (SELECT max((p.event_at AT TIME ZONE 'UTC')::date) FROM paid AS p
                WHERE p.vendor_id = v.id)
        FROM trust_vendors AS v
        WHERE v.pmc_id = p_pmc_id
    )
    SELECT (row_number() OVER (ORDER BY l.part, l.who COLLATE "C", l.vendor_id))::integer,
           l.what, l.label, l.w9, l.orders, l.bills, l.owed, l.year_paid, l.last_paid
    FROM lines AS l
    ORDER BY 1;
END;
$$;

REVOKE ALL ON FUNCTION trust_report_balance_sheet(uuid, date) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_report_cash_flow(uuid, date, date) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_report_vacancy(uuid, date) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_report_work_orders(uuid, date) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_report_tenant_directory(uuid, date) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_report_owner_directory(uuid, date) FROM PUBLIC;
REVOKE ALL ON FUNCTION trust_report_vendor_directory(uuid, date) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_balance_sheet(uuid, date) TO trust_app, trust_ai_agent;
GRANT EXECUTE ON FUNCTION trust_report_cash_flow(uuid, date, date) TO trust_app, trust_ai_agent;
GRANT EXECUTE ON FUNCTION trust_report_vacancy(uuid, date) TO trust_app, trust_ai_agent;
GRANT EXECUTE ON FUNCTION trust_report_work_orders(uuid, date) TO trust_app, trust_ai_agent;
GRANT EXECUTE ON FUNCTION trust_report_tenant_directory(uuid, date) TO trust_app, trust_ai_agent;
GRANT EXECUTE ON FUNCTION trust_report_owner_directory(uuid, date) TO trust_app, trust_ai_agent;
GRANT EXECUTE ON FUNCTION trust_report_vendor_directory(uuid, date) TO trust_app, trust_ai_agent;

-- migrate:down
-- Reports read the ledger and write nothing; no other function calls them.
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_vendor_directory(uuid, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_owner_directory(uuid, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_tenant_directory(uuid, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_work_orders(uuid, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_vacancy(uuid, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_cash_flow(uuid, date, date);
-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_balance_sheet(uuid, date);
