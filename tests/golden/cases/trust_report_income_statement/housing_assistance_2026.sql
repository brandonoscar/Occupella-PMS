-- Owner 1's income statement for 2026 with a housing authority's part of the rent (migration
-- 0095). Worked by hand: Unit 1's lease from Oct 1 at 1000.00 a month; the Golden Housing
-- Authority pays 800.00 of it from October, so October's rent is charged as 200.00 to the tenant
-- and 800.00 to the authority. The tenant paid 200.00 on Oct 3 and the authority 800.00 on
-- Oct 10, both into Owner 1's account: rent received 1000.00 (Schedule E line 3), no expenses,
-- net income 1000.00.
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

INSERT INTO trust_housing_authorities (id, pmc_id, name) VALUES
('00000000-0000-4000-8000-000000000071', '00000000-0000-4000-8000-000000000001',
 'Golden Housing Authority');

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

CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id, t.kind AS name
FROM trust_ledger_accounts AS t
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001';

SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000041',
    '2026-10-01', NULL, 1000.00, ARRAY['00000000-0000-4000-8000-000000000061'::uuid]
);

-- The authority pays 800.00 of each month's rent from October.
INSERT INTO trust_rent_assistance (pmc_id, lease_id, housing_authority_id, starts_on, amount)
SELECT l.pmc_id, l.id, '00000000-0000-4000-8000-000000000071', '2026-10-01', 800.00
FROM trust_leases AS l WHERE l.unit_id = '00000000-0000-4000-8000-000000000041';

-- October's rent, split as it is charged: the tenant 200.00, the authority 800.00.
SELECT trust_charge_rent_due('00000000-0000-4000-8000-000000000001', '2026-10-01');

-- Each pays its part into the owner's account.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'bank_cash'),
    (SELECT id FROM golden_account WHERE name = 'owner_property'),
    p.amount, p.event_at, jsonb_build_object('memo', p.memo)
)
FROM (VALUES
    (1, 200.00, '2026-10-03 15:00:00+00'::timestamptz, 'Golden tenant'),
    (2, 800.00, '2026-10-10 15:00:00+00', 'Golden authority')
) AS p (n, amount, event_at, memo)
ORDER BY p.n;

CREATE TEMP VIEW golden_transfer AS
SELECT t.id, t.metadata ->> 'memo' AS memo
FROM pgledger_transfers AS t
JOIN golden_account AS a ON a.id = t.to_account_id
WHERE t.metadata ->> 'memo' LIKE 'Golden %';

SELECT trust_apply_payment(
    '00000000-0000-4000-8000-000000000001', c.id,
    (SELECT t.id FROM golden_transfer AS t WHERE t.memo = 'Golden tenant'), 200.00
)
FROM trust_charges AS c
JOIN trust_leases AS l ON l.id = c.lease_id
WHERE l.unit_id = '00000000-0000-4000-8000-000000000041' AND c.kind = 'rent';

SELECT trust_apply_assistance_payment(
    '00000000-0000-4000-8000-000000000001', a.id,
    (SELECT t.id FROM golden_transfer AS t WHERE t.memo = 'Golden authority'), 800.00
)
FROM trust_assistance_charges AS a
JOIN trust_leases AS l ON l.id = a.lease_id
WHERE l.unit_id = '00000000-0000-4000-8000-000000000041';

SELECT * FROM trust_report_income_statement(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000021',
    '2026-01-01', '2026-12-31'
);
