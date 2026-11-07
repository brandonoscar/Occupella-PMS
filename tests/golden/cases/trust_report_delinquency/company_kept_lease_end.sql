-- Delinquency at the end of February 20, 2026, of a lease that ended mid-January owing a fee the
-- company keeps (migration 20261106000220). Its end on January 15 credits each of January's
-- charges the days after; the resident benefit package's credit comes off the package itself, as
-- a waiver does, never off the owner's rent (migration 20261107000100). Worked by hand (days past
-- due = Feb 20 - due):
--   Property 1 / Unit 1  Tenant 1. December's rent 1000.00 unpaid (due Dec 1: 81 days, 61-90).
--                        January's rent 1000.00 for 15 of 31 days is 483.87, its 516.13 credit
--                        coming off December's rent first (rent first, migration 0093): 483.87
--                        left of December. January's rent 1000.00 and the package's 12.10 (25.00
--                        less its 12.90 credit) due Jan 1: 50 days, 31-60, 1012.10. Total
--                        1495.97. (Before the change, the package's credit came off December's
--                        rent too: 470.97 and 1025.00.)
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1');

INSERT INTO trust_units (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit 1');

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 1');

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    a.kind, a.owner, a.property, NULL
)
FROM (VALUES
    (1, 'bank_cash', NULL::uuid, NULL::uuid),
    (2, 'pmc_income', NULL, NULL),
    (3, 'owner_property', '00000000-0000-4000-8000-000000000021'::uuid,
     '00000000-0000-4000-8000-000000000031'::uuid)
) AS a (n, kind, owner, property)
ORDER BY a.n;

SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000041',
    '2025-12-01', NULL, 1000.00, ARRAY['00000000-0000-4000-8000-000000000061'::uuid]
);

INSERT INTO trust_charge_types (id, pmc_id, name) VALUES
('00000000-0000-4000-8000-000000000071', '00000000-0000-4000-8000-000000000001',
 'Resident benefit package');

INSERT INTO trust_charge_type_payees (pmc_id, charge_type_id, payee) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000071', 'company');

INSERT INTO trust_recurring_charges (
    pmc_id, lease_id, kind, amount, memo, starts_on, charge_type_id
)
SELECT l.pmc_id, l.id, 'fee', 25.00, 'Resident benefit package', '2026-01-01',
       '00000000-0000-4000-8000-000000000071'
FROM trust_leases AS l
WHERE l.unit_id = '00000000-0000-4000-8000-000000000041';

SELECT trust_charge_rent_due('00000000-0000-4000-8000-000000000001', '2025-12-01');
SELECT trust_charge_rent_due('00000000-0000-4000-8000-000000000001', '2026-01-01');
SELECT trust_charge_recurring_due('00000000-0000-4000-8000-000000000001', '2026-01-01');

SELECT trust_end_lease(
    '00000000-0000-4000-8000-000000000001',
    (SELECT l.id FROM trust_leases AS l WHERE l.unit_id = '00000000-0000-4000-8000-000000000041'),
    '2026-01-15'
);

SELECT * FROM trust_report_delinquency('00000000-0000-4000-8000-000000000001', '2026-02-20');
