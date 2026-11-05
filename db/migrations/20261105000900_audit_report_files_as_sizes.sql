-- migrate:up

-- docs/edge-cases.md F-122: the audit log keeps each row as written (to_jsonb), so a lease offer
-- saved with its PDF, or a signature with its drawing, kept the file as hex: a 5 MB PDF read as
-- 10 million characters in one detail cell of the audit report, which its PDF export never
-- finished wrapping and an Excel cell can't hold. The report now reads a binary field (bytea in
-- its table, as the database's catalog says) as its size, "(5000000 bytes)". The log itself
-- keeps every row as it was.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_report_audit_log(p_pmc_id uuid, p_from date, p_to date) RETURNS TABLE(line integer, item text, at text, staff text, db_role text, action text, record text, detail text)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz := p_from::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := (p_to + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_from IS NULL OR p_to IS NULL OR p_to < p_from THEN
        RAISE EXCEPTION 'trust: an audit log runs from a day to the same day or a later one '
            '(got % to %)', p_from, p_to
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH holders AS (
        SELECT t.ledger_account_id AS id,
               t.kind || ': ' || CASE t.kind
                   WHEN 'bank_cash' THEN b.display_name
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
        WHERE t.pmc_id = p_pmc_id
    ),
    changes AS (
        SELECT a.id, a.at, coalesce(s.display_name, 'not recorded') AS staff, a.db_role,
               a.action, regexp_replace(a.table_name, '^(trust|pgledger)_', '') AS record,
               CASE
                   -- A transfer: how much, from and to whom, dated when, and its memo.
                   WHEN a.table_name = 'pgledger_transfers' THEN
                       (a.row_data ->> 'amount') || ' from ' || f.holder || ' to ' || t.holder
                       || ' dated ' || (a.row_data ->> 'event_at')
                       || coalesce(' - ' || (a.row_data -> 'metadata' ->> 'memo'), '')
                   -- An update: each field it changed, from what to what.
                   WHEN a.action = 'update' THEN (
                       SELECT coalesce(string_agg(
                           n.key || ': ' || CASE WHEN EXISTS (
                               SELECT att.attnum FROM pg_attribute AS att
                               WHERE att.attrelid = to_regclass(a.table_name)
                                 AND att.attname = n.key AND att.atttypid = 'bytea'::regtype
                           )
                               -- A file's bytes, as hex: its size before and after.
                               THEN coalesce('(' || (length(a.before ->> n.key) - 2) / 2
                                       || ' bytes)', 'null')
                                   || ' to ' || coalesce('(' || (length(n.value #>> '{}') - 2) / 2
                                       || ' bytes)', 'null')
                               ELSE coalesce(a.before ->> n.key, 'null') || ' to '
                                   || coalesce(n.value #>> '{}', 'null')
                           END,
                           ', ' ORDER BY n.key COLLATE "C"
                       ), 'no change')
                       FROM jsonb_each(a.row_data) AS n
                       WHERE n.value IS DISTINCT FROM a.before -> n.key
                   )
                   -- Anything else: its fields, but ids and the clock's time of writing it
                   -- (the log's own time says when).
                   ELSE (
                       SELECT string_agg(
                           n.key || ': ' || CASE WHEN EXISTS (
                               SELECT att.attnum FROM pg_attribute AS att
                               WHERE att.attrelid = to_regclass(a.table_name)
                                 AND att.attname = n.key AND att.atttypid = 'bytea'::regtype
                           )
                               -- A file's bytes, as hex ("\\x2550..."): their size.
                               THEN '(' || (length(n.value) - 2) / 2 || ' bytes)'
                               ELSE n.value
                           END,
                           ', ' ORDER BY n.key COLLATE "C"
                       )
                       FROM jsonb_each_text(a.row_data) AS n
                       WHERE n.value IS NOT NULL AND n.key <> 'id' AND n.key NOT LIKE '%\_id'
                         AND n.key NOT IN ('created_at', 'approved_at')
                   )
               END AS detail
        FROM trust_audit_log AS a
        LEFT JOIN trust_staff AS s ON s.id = a.staff_id
        LEFT JOIN holders AS f ON f.id = a.row_data ->> 'from_account_id'
        LEFT JOIN holders AS t ON t.id = a.row_data ->> 'to_account_id'
        WHERE a.pmc_id = p_pmc_id AND a.at >= v_start AND a.at < v_end
    ),
    lines AS (
        SELECT 0 AS section, NULL::bigint AS id, 'PMC' AS item, NULL::timestamptz AS at,
               pmc.display_name AS staff, NULL::text AS db_role, NULL::text AS action,
               NULL::text AS record, NULL::text AS detail
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 1, NULL, 'period', NULL,
               to_char(p_from, 'YYYY-MM-DD') || ' to ' || to_char(p_to, 'YYYY-MM-DD')
                   || ', whole days UTC',
               NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 2, c.id, 'change', c.at, c.staff, c.db_role, c.action, c.record, c.detail
        FROM changes AS c
        UNION ALL
        SELECT 3, NULL, 'total', NULL, NULL, NULL, NULL, NULL,
               (SELECT count(*) FROM changes) || ' changes'
    )
    SELECT row_number() OVER (ORDER BY l.section, l.id)::integer,
           l.item,
           to_char(l.at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US'),
           -- The PMC's name and the period sit in the staff column of the first two lines.
           l.staff,
           l.db_role,
           l.action,
           l.record,
           l.detail
    FROM lines AS l
    ORDER BY 1;
END;
$$;

-- migrate:down

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE OR REPLACE FUNCTION trust_report_audit_log(p_pmc_id uuid, p_from date, p_to date) RETURNS TABLE(line integer, item text, at text, staff text, db_role text, action text, record text, detail text)
    LANGUAGE plpgsql STABLE
    SET search_path TO 'public', 'pg_temp'
    AS $$
#variable_conflict use_column
DECLARE
    v_start timestamptz := p_from::timestamp AT TIME ZONE 'UTC';
    v_end timestamptz := (p_to + 1)::timestamp AT TIME ZONE 'UTC';
BEGIN
    IF p_from IS NULL OR p_to IS NULL OR p_to < p_from THEN
        RAISE EXCEPTION 'trust: an audit log runs from a day to the same day or a later one '
            '(got % to %)', p_from, p_to
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT pmc.pmc_id FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id) THEN
        RAISE EXCEPTION 'trust: no PMC %', p_pmc_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    RETURN QUERY
    WITH holders AS (
        SELECT t.ledger_account_id AS id,
               t.kind || ': ' || CASE t.kind
                   WHEN 'bank_cash' THEN b.display_name
                   WHEN 'owner_property' THEN o.display_name || ' / ' || pr.display_name
                   WHEN 'vendor_payable' THEN v.display_name
                   WHEN 'pmc_income' THEN pmc.display_name
                   ELSE tn.display_name
               END AS holder
        FROM trust_ledger_accounts AS t
        JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
        JOIN trust_pmcs AS pmc ON pmc.pmc_id = t.pmc_id
        LEFT JOIN trust_owners AS o ON o.id = t.owner_id
        LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
        LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
        LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
        WHERE t.pmc_id = p_pmc_id
    ),
    changes AS (
        SELECT a.id, a.at, coalesce(s.display_name, 'not recorded') AS staff, a.db_role,
               a.action, regexp_replace(a.table_name, '^(trust|pgledger)_', '') AS record,
               CASE
                   -- A transfer: how much, from and to whom, dated when, and its memo.
                   WHEN a.table_name = 'pgledger_transfers' THEN
                       (a.row_data ->> 'amount') || ' from ' || f.holder || ' to ' || t.holder
                       || ' dated ' || (a.row_data ->> 'event_at')
                       || coalesce(' - ' || (a.row_data -> 'metadata' ->> 'memo'), '')
                   -- An update: each field it changed, from what to what.
                   WHEN a.action = 'update' THEN (
                       SELECT coalesce(string_agg(
                           n.key || ': ' || coalesce(a.before ->> n.key, 'null') || ' to '
                           || coalesce(n.value #>> '{}', 'null'),
                           ', ' ORDER BY n.key COLLATE "C"
                       ), 'no change')
                       FROM jsonb_each(a.row_data) AS n
                       WHERE n.value IS DISTINCT FROM a.before -> n.key
                   )
                   -- Anything else: its fields, but ids and the clock's time of writing it
                   -- (the log's own time says when).
                   ELSE (
                       SELECT string_agg(
                           n.key || ': ' || n.value, ', ' ORDER BY n.key COLLATE "C"
                       )
                       FROM jsonb_each_text(a.row_data) AS n
                       WHERE n.value IS NOT NULL AND n.key <> 'id' AND n.key NOT LIKE '%\_id'
                         AND n.key NOT IN ('created_at', 'approved_at')
                   )
               END AS detail
        FROM trust_audit_log AS a
        LEFT JOIN trust_staff AS s ON s.id = a.staff_id
        LEFT JOIN holders AS f ON f.id = a.row_data ->> 'from_account_id'
        LEFT JOIN holders AS t ON t.id = a.row_data ->> 'to_account_id'
        WHERE a.pmc_id = p_pmc_id AND a.at >= v_start AND a.at < v_end
    ),
    lines AS (
        SELECT 0 AS section, NULL::bigint AS id, 'PMC' AS item, NULL::timestamptz AS at,
               pmc.display_name AS staff, NULL::text AS db_role, NULL::text AS action,
               NULL::text AS record, NULL::text AS detail
        FROM trust_pmcs AS pmc WHERE pmc.pmc_id = p_pmc_id
        UNION ALL
        SELECT 1, NULL, 'period', NULL,
               to_char(p_from, 'YYYY-MM-DD') || ' to ' || to_char(p_to, 'YYYY-MM-DD')
                   || ', whole days UTC',
               NULL, NULL, NULL, NULL
        UNION ALL
        SELECT 2, c.id, 'change', c.at, c.staff, c.db_role, c.action, c.record, c.detail
        FROM changes AS c
        UNION ALL
        SELECT 3, NULL, 'total', NULL, NULL, NULL, NULL, NULL,
               (SELECT count(*) FROM changes) || ' changes'
    )
    SELECT row_number() OVER (ORDER BY l.section, l.id)::integer,
           l.item,
           to_char(l.at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US'),
           -- The PMC's name and the period sit in the staff column of the first two lines.
           l.staff,
           l.db_role,
           l.action,
           l.record,
           l.detail
    FROM lines AS l
    ORDER BY 1;
END;
$$;
