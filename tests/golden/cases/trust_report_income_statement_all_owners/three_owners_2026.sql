-- The income statement across every owner for 2026 (q108). Worked by hand: Owner 1's Property 1
-- has Unit 1 leased from Oct 1 at 1000.00 a month, its tenant paying October's rent on Oct 3;
-- Owner 2's Property 2 has Unit 2 leased from Oct 1 at 800.00, its tenant paying 800.00 on Oct 5;
-- Owner 3's Property 3 took nothing in. Each property account's lines, named by its property
-- and owner, are what that owner's own statement shows (Property 3's all 0.00), and each line
-- across them is their sum: rent 1800.00, total income, net income and Schedule E line 3
-- 1800.00. The owner line reads all owners.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1'),
('00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000001', 'Owner 2'),
('00000000-0000-4000-8000-000000000023', '00000000-0000-4000-8000-000000000001', 'Owner 3');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1'),
('00000000-0000-4000-8000-000000000032', '00000000-0000-4000-8000-000000000001', 'Property 2'),
('00000000-0000-4000-8000-000000000033', '00000000-0000-4000-8000-000000000001', 'Property 3');

INSERT INTO trust_units (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit 1'),
('00000000-0000-4000-8000-000000000042', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000032', 'Unit 2');

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 1'),
('00000000-0000-4000-8000-000000000062', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000032', 'Tenant 2');

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    a.kind, a.owner, a.property, NULL
)
FROM (VALUES
    (1, 'bank_cash', NULL::uuid, NULL::uuid),
    (2, 'pmc_income', NULL, NULL),
    (3, 'owner_property', '00000000-0000-4000-8000-000000000021'::uuid,
     '00000000-0000-4000-8000-000000000031'::uuid),
    (4, 'owner_property', '00000000-0000-4000-8000-000000000022'::uuid,
     '00000000-0000-4000-8000-000000000032'::uuid),
    (5, 'owner_property', '00000000-0000-4000-8000-000000000023'::uuid,
     '00000000-0000-4000-8000-000000000033'::uuid)
) AS a (n, kind, owner, property)
ORDER BY a.n;

CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id, coalesce(t.owner_id::text, t.kind) AS name
FROM trust_ledger_accounts AS t
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001';

SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', l.unit, '2026-10-01', NULL, l.rent, ARRAY[l.tenant]
)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000041'::uuid, 1000.00,
     '00000000-0000-4000-8000-000000000061'::uuid),
    (2, '00000000-0000-4000-8000-000000000042'::uuid, 800.00,
     '00000000-0000-4000-8000-000000000062'::uuid)
) AS l (n, unit, rent, tenant)
ORDER BY l.n;

SELECT trust_charge_rent_due('00000000-0000-4000-8000-000000000001', '2026-10-01');

-- Each tenant pays October's rent into their owner's account.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'bank_cash'),
    (SELECT id FROM golden_account WHERE name = p.owner),
    p.amount, p.at, jsonb_build_object('memo', p.memo)
)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000021', 1000.00, '2026-10-03 15:00:00+00'::timestamptz,
     'Golden tenant 1'),
    (2, '00000000-0000-4000-8000-000000000022', 800.00, '2026-10-05 15:00:00+00'::timestamptz,
     'Golden tenant 2')
) AS p (n, owner, amount, at, memo)
ORDER BY p.n;

SELECT trust_apply_payment(
    '00000000-0000-4000-8000-000000000001', c.id, t.id, c.amount
)
FROM trust_charges AS c
JOIN trust_leases AS l ON l.id = c.lease_id
JOIN trust_units AS u ON u.id = l.unit_id
JOIN pgledger_transfers AS t
    ON t.metadata ->> 'memo' = 'Golden tenant ' || right(u.display_name, 1)
WHERE c.pmc_id = '00000000-0000-4000-8000-000000000001'
ORDER BY u.display_name;

SELECT * FROM trust_report_income_statement_all_owners(
    '00000000-0000-4000-8000-000000000001', '2026-01-01', '2026-12-31'
);
