-- migrate:up

-- A switch-in import (src/pms/api/switching.py) brings a company's thousands of rows in one
-- request. Until autovacuum next looks (once a minute, and only once a tenth of a table has
-- changed), the planner may take the company's thousands of charges for a handful and plan its
-- reports row by row: the delinquency report, which the Dashboard and Needs attention read, ran
-- 1.2-1.6 s for 200 units against 0.03 s once analyzed (11/01, PR #184). So the import analyzes
-- the tables it fills, in its own transaction (its own rows count), and the next page is planned
-- on what they hold. ANALYZE needs the tables' owner: this runs as the owner, reads samples and
-- writes the planner's statistics, and nothing else. It moves no money and changes no record.
CREATE FUNCTION public.trust_analyze_after_import() RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = public, pg_temp
AS $$
BEGIN
    ANALYZE public.trust_owners, public.trust_vendors, public.trust_properties,
        public.trust_units, public.trust_unit_details, public.trust_tenants, public.trust_leases,
        public.trust_lease_tenants, public.trust_charges, public.trust_bills, public.trust_contacts,
        public.trust_history_files, public.trust_history_lines, public.trust_ledger_accounts,
        public.pgledger_accounts, public.pgledger_transfers, public.pgledger_entries;
END;
$$;

REVOKE ALL ON FUNCTION public.trust_analyze_after_import() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.trust_analyze_after_import() TO trust_app;

-- migrate:down

DROP FUNCTION public.trust_analyze_after_import();
