-- Owner balances at the end of February 1, 2026. Worked by hand:
--   Owner 1 / Property 1  agreement 8%, minimum 50.00, flat 10.00, leasing 50%, reserve 200.00.
--                         January rent 1500.00; leasing fee 750.00 (50% of 1500.00);
--                         management fee 130.00 (8% of 1500.00 = 120.00, + 10.00); on Feb 1
--                         the owner is paid everything above the reserve: 620.00 - 200.00.
--                         Holds 200.00, keeps 200.00, available 0.00.
--   Owner 2 / Property 2  agreement 10%, reserve 300.00. January rent 1200.00, fee 120.00.
--                         Holds 1080.00, keeps 300.00, available 780.00.
--   Owner 2 / Property 3  no agreement. The owner put in 400.00. Available 400.00.
--   Totals: held 1680.00, kept 500.00, available 1180.00.
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

-- Ledger accounts: (kind, owner, property).
SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    a.kind, a.owner, a.property, NULL
)
FROM (VALUES
    (1, 'bank_cash', NULL::uuid, NULL::uuid),
    (2, 'pmc_income', NULL, NULL),
    (3, 'owner_property', '00000000-0000-4000-8000-000000000021'::uuid,
     '00000000-0000-4000-8000-000000000031'::uuid),
    (4, 'owner_property', '00000000-0000-4000-8000-000000000022',
     '00000000-0000-4000-8000-000000000032'),
    (5, 'owner_property', '00000000-0000-4000-8000-000000000022',
     '00000000-0000-4000-8000-000000000033')
) AS a (n, kind, owner, property)
ORDER BY a.n;

-- Each account by a short name, for the steps below.
CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id, coalesce(pr.display_name, t.kind) AS name
FROM trust_ledger_accounts AS t
LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001';

INSERT INTO trust_management_agreements (
    pmc_id, ledger_account_id, starts_on, fee_percent, minimum_fee, flat_fee,
    leasing_fee_percent, reserve
)
SELECT '00000000-0000-4000-8000-000000000001', a.id, '2026-01-01', g.pct, g.minimum, g.flat,
       g.leasing, g.reserve
FROM (VALUES
    ('Property 1', 8.00, 50.00, 10.00, 50.00, 200.00),
    ('Property 2', 10.00, 0.00, 0.00, 0.00, 300.00)
) AS g (name, pct, minimum, flat, leasing, reserve)
JOIN golden_account AS a ON a.name = g.name;

-- Leases: (unit, rent, tenant).
SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', l.unit, '2026-01-01', NULL, l.rent,
    ARRAY[l.tenant]
)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000041'::uuid, 1500.00,
     '00000000-0000-4000-8000-000000000061'::uuid),
    (2, '00000000-0000-4000-8000-000000000042', 1200.00, '00000000-0000-4000-8000-000000000062')
) AS l (n, unit, rent, tenant)
ORDER BY l.n;

INSERT INTO trust_charges (id, pmc_id, lease_id, due_on, kind, amount, memo)
SELECT c.id, l.pmc_id, l.id, '2026-01-01', 'rent', l.monthly_rent, 'January rent'
FROM (VALUES
    ('00000000-0000-4000-8000-000000000081'::uuid, '00000000-0000-4000-8000-000000000041'::uuid),
    ('00000000-0000-4000-8000-000000000082', '00000000-0000-4000-8000-000000000042')
) AS c (id, unit)
JOIN trust_leases AS l ON l.unit_id = c.unit;

-- Money in: (to, amount, date, memo), one statement each so they post in this order.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'bank_cash'),
    (SELECT id FROM golden_account WHERE name = p.target),
    p.amount, p.event_at, jsonb_build_object('memo', p.memo)
)
FROM (VALUES
    (1, 'Property 1', 1500.00, '2026-01-03 15:00:00+00'::timestamptz, 'Golden Unit 1 rent'),
    (2, 'Property 2', 1200.00, '2026-01-05 15:00:00+00', 'Golden Unit 2 rent'),
    (3, 'Property 3', 400.00, '2026-01-20 15:00:00+00', 'Golden owner contribution')
) AS p (n, target, amount, event_at, memo)
ORDER BY p.n;

SELECT trust_apply_payment(
    '00000000-0000-4000-8000-000000000001', m.charge_id,
    (SELECT t.id FROM pgledger_transfers AS t
     JOIN golden_account AS a ON a.id = t.to_account_id
     WHERE t.metadata ->> 'memo' = m.memo),
    m.amount
)
FROM (VALUES
    ('00000000-0000-4000-8000-000000000081'::uuid, 'Golden Unit 1 rent', 1500.00),
    ('00000000-0000-4000-8000-000000000082', 'Golden Unit 2 rent', 1200.00)
) AS m (charge_id, memo, amount);

-- Fees, then the draw.
SELECT trust_post_leasing_fee(
    '00000000-0000-4000-8000-000000000001',
    (SELECT id FROM trust_leases WHERE unit_id = '00000000-0000-4000-8000-000000000041'),
    (SELECT id FROM golden_account WHERE name = 'Property 1'),
    '2026-01-04 09:00:00+00'
);

SELECT trust_post_management_fee(
    '00000000-0000-4000-8000-000000000001', a.id, '2026-01-01', '2026-02-01',
    '2026-01-31 18:00:00+00'
)
FROM golden_account AS a
WHERE a.name IN ('Property 1', 'Property 2');

SELECT trust_draw_owner(
    '00000000-0000-4000-8000-000000000001',
    (SELECT id FROM golden_account WHERE name = 'Property 1'),
    'golden-february-draw', NULL, '2026-02-01 10:00:00+00'
);

SELECT * FROM trust_report_owner_balances('00000000-0000-4000-8000-000000000001', '2026-02-01');
