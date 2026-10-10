-- Delinquency at the end of April 30, 2026. Worked by hand (days past due = Apr 30 - due):
--   Property 1 / Unit 1  Tenant 1, Tenant 2. Rent 1500.00 from Jan; Jan and Feb paid, March
--                        paid 1000.00 (500.00 left, due Mar 1: 60 days, 31-60), April unpaid
--                        (due Apr 1: 29 days, 1-30). Total 2000.00.
--   Property 1 / Unit 2  Tenant 4. April's 900.00 paid; a 50.00 pet fee due today: current.
--   Property 2 / Unit 3  Tenant 3. January and February unpaid (1200.00 each); a 200.00
--                        credit comes off January's first: 1000.00 over 90 (119 days), 1200.00
--                        61-90 (88 days). Total 2200.00.
--   Totals: current 50.00, 1-30 1500.00, 31-60 500.00, 61-90 1200.00, over 90 1000.00,
--   total 4250.00.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1'),
('00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000001', 'Owner 2');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1'),
('00000000-0000-4000-8000-000000000032', '00000000-0000-4000-8000-000000000001', 'Property 2');

INSERT INTO trust_units (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit 1'),
('00000000-0000-4000-8000-000000000042', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit 2'),
('00000000-0000-4000-8000-000000000043', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000032', 'Unit 3');

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 1'),
('00000000-0000-4000-8000-000000000062', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 2'),
('00000000-0000-4000-8000-000000000063', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000032', 'Tenant 3'),
('00000000-0000-4000-8000-000000000064', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 4');

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    a.kind, a.owner, a.property, NULL
)
FROM (VALUES
    (1, 'bank_cash', NULL::uuid, NULL::uuid),
    (2, 'owner_property', '00000000-0000-4000-8000-000000000021'::uuid,
     '00000000-0000-4000-8000-000000000031'::uuid),
    (3, 'owner_property', '00000000-0000-4000-8000-000000000022',
     '00000000-0000-4000-8000-000000000032')
) AS a (n, kind, owner, property)
ORDER BY a.n;

CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id, coalesce(pr.display_name, t.kind) AS name
FROM trust_ledger_accounts AS t
LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001';

-- Leases from Jan 1, month to month: (unit, rent, tenants).
SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', l.unit, '2026-01-01', NULL, l.rent, l.tenants
)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000041'::uuid, 1500.00,
     ARRAY['00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000062']::uuid[]),
    (2, '00000000-0000-4000-8000-000000000042', 900.00,
     ARRAY['00000000-0000-4000-8000-000000000064']::uuid[]),
    (3, '00000000-0000-4000-8000-000000000043', 1200.00,
     ARRAY['00000000-0000-4000-8000-000000000063']::uuid[])
) AS l (n, unit, rent, tenants)
ORDER BY l.n;

-- Charges: (id, unit, due, kind, amount, memo).
INSERT INTO trust_charges (id, pmc_id, lease_id, due_on, kind, amount, memo)
SELECT c.id, l.pmc_id, l.id, c.due_on, c.kind, c.amount, c.memo
FROM (VALUES
    ('00000000-0000-4000-8000-000000000101'::uuid, '00000000-0000-4000-8000-000000000041'::uuid,
     '2026-01-01'::date, 'rent', 1500.00, 'January rent'),
    ('00000000-0000-4000-8000-000000000102', '00000000-0000-4000-8000-000000000041',
     '2026-02-01', 'rent', 1500.00, 'February rent'),
    ('00000000-0000-4000-8000-000000000103', '00000000-0000-4000-8000-000000000041',
     '2026-03-01', 'rent', 1500.00, 'March rent'),
    ('00000000-0000-4000-8000-000000000104', '00000000-0000-4000-8000-000000000041',
     '2026-04-01', 'rent', 1500.00, 'April rent'),
    ('00000000-0000-4000-8000-000000000201', '00000000-0000-4000-8000-000000000042',
     '2026-04-01', 'rent', 900.00, 'April rent'),
    ('00000000-0000-4000-8000-000000000202', '00000000-0000-4000-8000-000000000042',
     '2026-04-30', 'fee', 50.00, 'Pet fee'),
    ('00000000-0000-4000-8000-000000000301', '00000000-0000-4000-8000-000000000043',
     '2026-01-01', 'rent', 1200.00, 'January rent'),
    ('00000000-0000-4000-8000-000000000302', '00000000-0000-4000-8000-000000000043',
     '2026-02-01', 'rent', 1200.00, 'February rent'),
    ('00000000-0000-4000-8000-000000000303', '00000000-0000-4000-8000-000000000043',
     '2026-02-15', 'credit', 200.00, 'Repair credit')
) AS c (id, unit, due_on, kind, amount, memo)
JOIN trust_leases AS l ON l.unit_id = c.unit;

-- Payments: (charge, into, amount, date, memo), one statement each so they post in order.
SELECT trust_apply_payment(
    '00000000-0000-4000-8000-000000000001', p.charge_id,
    (pgledger_create_transfer(
        (SELECT id FROM golden_account WHERE name = 'bank_cash'),
        (SELECT id FROM golden_account WHERE name = p.target),
        p.amount, p.event_at, jsonb_build_object('memo', p.memo)
    )).id,
    p.amount
)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000101'::uuid, 'Property 1', 1500.00,
     '2026-01-03 15:00:00+00'::timestamptz, 'Check 101'),
    (2, '00000000-0000-4000-8000-000000000102', 'Property 1', 1500.00,
     '2026-02-03 15:00:00+00', 'Check 102'),
    (3, '00000000-0000-4000-8000-000000000103', 'Property 1', 1000.00,
     '2026-03-05 15:00:00+00', 'Check 103'),
    (4, '00000000-0000-4000-8000-000000000201', 'Property 1', 900.00,
     '2026-04-02 15:00:00+00', 'Check 201')
) AS p (n, charge_id, target, amount, event_at, memo)
ORDER BY p.n;

SELECT * FROM trust_report_delinquency('00000000-0000-4000-8000-000000000001', '2026-04-30');
