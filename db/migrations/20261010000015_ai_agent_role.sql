-- migrate:up
-- The role behind the API key Occupella will hold (founder decision, 2026-10-10; AgenticHelixis
-- ADR-free-pms-placement, "Where the approval gate sits"). The AI never moves money, and this
-- makes the database refuse it too: trust_ai_agent reads what trust_app reads and runs the
-- reports, and holds no other grant. It can't call any function that writes, and can't insert,
-- update or delete a row anywhere. tests/test_privileges.py pins this for every table and
-- function, so a later migration can't widen it unnoticed.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- NOLOGIN: a deployment grants it to the login role the API key's requests run as. Roles
-- belong to the whole server, not one database, so create it only if it is missing.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trust_ai_agent') THEN
        CREATE ROLE trust_ai_agent NOLOGIN;
    END IF;
END;
$$;

GRANT SELECT ON
    pgledger_accounts, pgledger_transfers, pgledger_entries,
    pgledger_accounts_view, pgledger_transfers_view, pgledger_entries_view,
    trust_pmcs, trust_bank_accounts, trust_owners, trust_properties, trust_tenants,
    trust_vendors, trust_ledger_accounts, trust_reconciliations,
    trust_units, trust_leases, trust_lease_tenants, trust_charges, trust_charge_payments,
    trust_payment_reversals
TO trust_ai_agent;

-- The reports run with the caller's own read grants, so these add no access of their own.
GRANT EXECUTE ON FUNCTION
    trust_report_three_way_reconciliation(uuid, uuid),
    trust_report_owner_statement(uuid, uuid, timestamptz, timestamptz),
    trust_report_rent_roll(uuid, date)
TO trust_ai_agent;

-- migrate:down
-- The role stays: it belongs to the server, and another database may use it.
REVOKE EXECUTE ON FUNCTION
    trust_report_three_way_reconciliation(uuid, uuid),
    trust_report_owner_statement(uuid, uuid, timestamptz, timestamptz),
    trust_report_rent_roll(uuid, date)
FROM trust_ai_agent;

REVOKE SELECT ON
    pgledger_accounts, pgledger_transfers, pgledger_entries,
    pgledger_accounts_view, pgledger_transfers_view, pgledger_entries_view,
    trust_pmcs, trust_bank_accounts, trust_owners, trust_properties, trust_tenants,
    trust_vendors, trust_ledger_accounts, trust_reconciliations,
    trust_units, trust_leases, trust_lease_tenants, trust_charges, trust_charge_payments,
    trust_payment_reversals
FROM trust_ai_agent;
