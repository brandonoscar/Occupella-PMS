-- Owner 1's balance sheet at the end of January 2026, when the lease owes fees the company keeps
-- (migration 20261106000220): those are owed to the company, never to the owner, so "owed by
-- tenants" leaves them out, and the lease's end on January 15 credits each monthly charge its
-- days after, the benefit package's credit coming off the package (migration 20261107000100).
-- Worked by hand:
--   Property 1  no cash held. January's rent 1000.00 for 15 of 31 days is 483.87 (a 516.13
--               credit); the resident benefit package, 25.00 a month, 12.10 (a 12.90 credit,
--               off the package), and with the admin fee (150.00) the company's. Owed by
--               tenants: 483.87 and the key replacement 75.00: 558.87. Equity 558.87.
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
    '2026-01-01', NULL, 1000.00, ARRAY['00000000-0000-4000-8000-000000000061'::uuid]
);

-- Two kinds of fee the company keeps.
INSERT INTO trust_charge_types (id, pmc_id, name) VALUES
('00000000-0000-4000-8000-000000000071', '00000000-0000-4000-8000-000000000001',
 'Resident benefit package'),
('00000000-0000-4000-8000-000000000072', '00000000-0000-4000-8000-000000000001', 'Admin fee');

INSERT INTO trust_charge_type_payees (pmc_id, charge_type_id, payee)
SELECT t.pmc_id, t.id, 'company'
FROM trust_charge_types AS t
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001';

INSERT INTO trust_recurring_charges (
    pmc_id, lease_id, kind, amount, memo, starts_on, charge_type_id
)
SELECT l.pmc_id, l.id, 'fee', 25.00, 'Resident benefit package', '2026-01-01',
       '00000000-0000-4000-8000-000000000071'
FROM trust_leases AS l
WHERE l.unit_id = '00000000-0000-4000-8000-000000000041';

SELECT trust_charge_rent_due('00000000-0000-4000-8000-000000000001', '2026-01-01');
SELECT trust_charge_recurring_due('00000000-0000-4000-8000-000000000001', '2026-01-01');

INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, charge_type_id)
SELECT l.pmc_id, l.id, c.due_on, 'fee', c.amount, c.memo, c.kind
FROM trust_leases AS l
CROSS JOIN (VALUES
    ('2026-01-10'::date, 75.00, 'Key replacement', NULL::uuid),
    ('2026-01-12', 150.00, 'Admin fee', '00000000-0000-4000-8000-000000000072')
) AS c (due_on, amount, memo, kind)
WHERE l.unit_id = '00000000-0000-4000-8000-000000000041';

SELECT trust_end_lease(
    '00000000-0000-4000-8000-000000000001',
    (SELECT l.id FROM trust_leases AS l WHERE l.unit_id = '00000000-0000-4000-8000-000000000041'),
    '2026-01-15'
);

SELECT * FROM trust_report_owner_balance_sheet(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000021', '2026-01-31'
);
