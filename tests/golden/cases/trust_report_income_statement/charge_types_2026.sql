-- Owner 1's income statement for 2026 with the company's charge types (migration 0100). Worked
-- by hand: Unit 1's lease from Oct 1 at 1000.00 a month, with pet rent of 25.00 a month of the
-- type Pet rent. October's rent and pet rent are charged, then a one-off application fee of
-- 50.00 of the type Application fee and a key copy of 10.00 with no type. The tenant paid
-- 1085.00 on Oct 3, matched to all four. Income: rent 1000.00, recurring charges 0.00 (the pet
-- rent has a type), other charges 10.00 (the key copy), then a line for each type with money
-- on it, by name: Application fee 50.00, Pet rent 25.00; Parking, a type nothing was charged
-- under, has no line. Total income and Schedule E line 3: 1085.00; no expenses.
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

INSERT INTO trust_charge_types (id, pmc_id, name) VALUES
('00000000-0000-4000-8000-000000000081', '00000000-0000-4000-8000-000000000001',
 'Application fee'),
('00000000-0000-4000-8000-000000000082', '00000000-0000-4000-8000-000000000001', 'Pet rent'),
('00000000-0000-4000-8000-000000000083', '00000000-0000-4000-8000-000000000001', 'Parking');

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

CREATE TEMP VIEW golden_lease AS
SELECT l.id FROM trust_leases AS l
WHERE l.unit_id = '00000000-0000-4000-8000-000000000041';

-- Pet rent every month, of the type Pet rent.
INSERT INTO trust_recurring_charges (
    pmc_id, lease_id, kind, amount, memo, starts_on, charge_type_id
)
SELECT '00000000-0000-4000-8000-000000000001', l.id, 'fee', 25.00, 'Pet rent', '2026-10-01',
       '00000000-0000-4000-8000-000000000082'
FROM golden_lease AS l;

SELECT trust_charge_rent_due('00000000-0000-4000-8000-000000000001', '2026-10-01');
SELECT trust_charge_recurring_due('00000000-0000-4000-8000-000000000001', '2026-10-01');

-- A one-off fee of a type, and one with none.
INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, charge_type_id)
SELECT '00000000-0000-4000-8000-000000000001', l.id, '2026-10-01', 'fee', f.amount, f.memo,
       f.charge_type
FROM golden_lease AS l
CROSS JOIN (VALUES
    (50.00, 'Application fee', '00000000-0000-4000-8000-000000000081'::uuid),
    (10.00, 'Key copy', NULL)
) AS f (amount, memo, charge_type);

-- The tenant pays all four into the owner's account.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'bank_cash'),
    (SELECT id FROM golden_account WHERE name = 'owner_property'),
    1085.00, '2026-10-03 15:00:00+00'::timestamptz, jsonb_build_object('memo', 'Golden tenant')
);

SELECT trust_apply_payment(
    '00000000-0000-4000-8000-000000000001', c.id,
    (SELECT t.id FROM pgledger_transfers AS t WHERE t.metadata ->> 'memo' = 'Golden tenant'),
    c.amount
)
FROM trust_charges AS c
JOIN golden_lease AS l ON l.id = c.lease_id
ORDER BY c.amount DESC;

SELECT * FROM trust_report_income_statement(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000021',
    '2026-01-01', '2026-12-31'
);
