-- Owner 1's income statement (accrual basis) for January and February 2026 with concessions
-- (migration 20261106000242). Worked by hand: Unit 1's lease charges 1500.00 rent on each first;
-- a one-time concession of 150.00 (the first month's) on January 1 and a monthly concession of
-- 50.00 from January 1, charged on each first, so 250.00 of concessions, a contra to rent on its
-- own line right after it; a 20.00 credit on February 1 not marked a concession still comes off
-- rent. Rent 3000.00 - 20.00 = 2980.00, concessions -250.00, recurring charges 0.00: income
-- 2730.00, all of it Schedule E line 3. No fee or bill: net income 2730.00.
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
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011', a.kind,
    a.owner, a.property
)
FROM (VALUES
    (1, 'bank_cash', NULL::uuid, NULL::uuid),
    (2, 'owner_property', '00000000-0000-4000-8000-000000000021'::uuid,
     '00000000-0000-4000-8000-000000000031'::uuid)
) AS a (n, kind, owner, property)
ORDER BY a.n;

SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000041',
    '2026-01-01', NULL, 1500.00, ARRAY['00000000-0000-4000-8000-000000000061'::uuid]
);

INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, concession)
SELECT '00000000-0000-4000-8000-000000000001', l.id, c.due_on, c.kind, c.amount, c.memo,
       c.concession
FROM trust_leases AS l
CROSS JOIN (VALUES
    (1, '2026-01-01'::date, 'rent', 1500.00, 'Rent', false),
    (2, '2026-02-01', 'rent', 1500.00, 'Rent', false),
    (3, '2026-01-01', 'credit', 150.00, 'First month concession', true),
    (4, '2026-02-01', 'credit', 20.00, 'Goodwill', false)
) AS c (n, due_on, kind, amount, memo, concession)
WHERE l.unit_id = '00000000-0000-4000-8000-000000000041'
ORDER BY c.n;

INSERT INTO trust_recurring_charges (pmc_id, lease_id, kind, amount, memo, starts_on, concession)
SELECT '00000000-0000-4000-8000-000000000001', l.id, 'credit', 50.00, 'Military discount',
       '2026-01-01', true
FROM trust_leases AS l
WHERE l.unit_id = '00000000-0000-4000-8000-000000000041';

SELECT trust_charge_recurring_due('00000000-0000-4000-8000-000000000001', d.due_on)
FROM (VALUES (1, '2026-01-01'::date), (2, '2026-02-01')) AS d (n, due_on)
ORDER BY d.n;

SELECT * FROM trust_report_income_statement_accrual(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000021',
    '2026-01-01', '2026-02-28'
);
