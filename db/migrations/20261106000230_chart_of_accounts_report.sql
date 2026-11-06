-- migrate:up
-- The chart of accounts as a report (the founder's chart of accounts pass, 2026-11-05): every
-- account of a company's chart, by section and code, as Settings, Chart of accounts lists it and
-- its CSV download has it.
--
--   trust_report_chart_of_accounts(pmc)  each account: its section (Assets, Liabilities, Income,
--                                        Expenses; the trust ledger holds no equity), code, name
--                                        today, type, Schedule E line (an expense's), who keeps
--                                        an income line's money (a charge type's payee today;
--                                        the owner for the statement's own income lines), and
--                                        whether it is active or archived.
--
-- It runs as the owner, so the AI role, which calls no function but the reports, reads the
-- chart through the app's helpers (trust_chart_lines, trust_account_codes_of...) and nothing
-- else: it reads every table behind it already. Read only.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE FUNCTION trust_report_chart_of_accounts(p_pmc_id uuid)
RETURNS TABLE (
    line integer, section text, code text, name text, account_type text, schedule_e integer,
    kept_by text, status text
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
#variable_conflict use_column
BEGIN
    IF NOT EXISTS (SELECT 1 FROM trust_pmcs AS p WHERE p.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH codes AS (
        SELECT * FROM trust_account_codes_of(p_pmc_id)
    ),
    accounts AS (
        SELECT l.section, c.code, l.name, l.account_type, l.schedule_e,
               CASE WHEN l.section = 'income' THEN 'owner' END AS kept_by,
               'active' AS status
        FROM trust_chart_lines() AS l
        JOIN codes AS c ON c.line = l.line
        UNION ALL
        SELECT 'income', c.code, trust_charge_type_name(t.id), 'Charge type', NULL::integer,
               trust_charge_type_payee(t.id),
               CASE WHEN trust_charge_type_archived(t.id) THEN 'archived' ELSE 'active' END
        FROM trust_charge_types AS t
        JOIN codes AS c ON c.charge_type_id = t.id
        WHERE t.pmc_id = p_pmc_id
        UNION ALL
        SELECT 'expense', c.code, trust_expense_account_name(e.id), 'Expense account',
               e.schedule_e_line, NULL,
               CASE WHEN trust_expense_account_archived(e.id) THEN 'archived' ELSE 'active' END
        FROM trust_expense_accounts AS e
        JOIN codes AS c ON c.expense_account_id = e.id
        WHERE e.pmc_id = p_pmc_id
    )
    SELECT (row_number() OVER (
                ORDER BY s.ord, a.code COLLATE "C", a.name COLLATE "C"
            ))::integer,
           s.label, a.code, a.name, a.account_type, a.schedule_e, a.kept_by, a.status
    FROM accounts AS a
    JOIN (VALUES ('asset', 1, 'Assets'), ('liability', 2, 'Liabilities'), ('income', 4, 'Income'),
                 ('expense', 5, 'Expenses')) AS s (section, ord, label)
        ON s.section = a.section
    ORDER BY 1;
END;
$$;
REVOKE ALL ON FUNCTION trust_report_chart_of_accounts(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION trust_report_chart_of_accounts(uuid) TO trust_app, trust_ai_agent;

-- migrate:down
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- squawk-ignore ban-drop-function
DROP FUNCTION trust_report_chart_of_accounts(uuid);
