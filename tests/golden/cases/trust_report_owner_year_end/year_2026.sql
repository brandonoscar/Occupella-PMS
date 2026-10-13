-- Owner 1's year-end package for 2026. Worked by hand:
--   Property 1  agreement 8%, flat 10.00, leasing 50%, reserve 200.00. 300.00 came in on
--               Dec 15, 2025: the opening balance. January rent 1500.00 collected; leasing fee
--               750.00 (50% of 1500.00); management fee 130.00 (8% of 1500.00, + 10.00); on
--               Feb 1 the owner is paid everything above the reserve: 920.00 - 200.00 = 720.00.
--               On Jun 1, 100.00 moved to Property 2 (other). Closing 100.00. A payment of
--               Jan 2, 2027 is the next year.
--   Property 2  agreement 10%. January rent 1200.00 paid, bounced and paid again: 1200.00
--               collected; management fee 120.00 (10% of 1200.00). The owner put in 400.00;
--               a 250.00 roof bill was set aside to pay; 100.00 came from Property 1 (other).
--               Closing 1330.00.
--   All properties: opening 300.00, collected 2700.00, management fees -250.00, leasing fees
--   -750.00, bills -250.00, draws -720.00, contributions 400.00, other 0.00, closing 1430.00.
--   1099-MISC rents: 2700.00. Owner 2's 999.00 at Property 3 is not Owner 1's.
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

INSERT INTO trust_vendors (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000051', '00000000-0000-4000-8000-000000000001',
 'Golden Roofing');

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
    (4, 'owner_property', '00000000-0000-4000-8000-000000000021',
     '00000000-0000-4000-8000-000000000032'),
    (5, 'owner_property', '00000000-0000-4000-8000-000000000022',
     '00000000-0000-4000-8000-000000000033')
) AS a (n, kind, owner, property)
ORDER BY a.n;

SELECT trust_open_vendor_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    '00000000-0000-4000-8000-000000000051'
);

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
SELECT '00000000-0000-4000-8000-000000000001', a.id, '2026-01-01', g.pct, 0.00, g.flat,
       g.leasing, g.reserve
FROM (VALUES
    ('Property 1', 8.00, 10.00, 50.00, 200.00),
    ('Property 2', 10.00, 0.00, 0.00, 0.00)
) AS g (name, pct, flat, leasing, reserve)
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

-- Money moved: (from, to, amount, date, memo), one statement each so they post in this order.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = p.source),
    (SELECT id FROM golden_account WHERE name = p.target),
    p.amount, p.event_at, jsonb_build_object('memo', p.memo)
)
FROM (VALUES
    (1, 'bank_cash', 'Property 1', 300.00, '2025-12-15 15:00:00+00'::timestamptz,
     'Golden December'),
    (2, 'bank_cash', 'Property 1', 1500.00, '2026-01-03 15:00:00+00', 'Golden Unit 1 rent'),
    (3, 'bank_cash', 'Property 2', 1200.00, '2026-01-05 15:00:00+00', 'Golden Unit 2 rent'),
    (4, 'Property 2', 'bank_cash', 1200.00, '2026-01-10 15:00:00+00', 'Golden Unit 2 bounce'),
    (5, 'bank_cash', 'Property 2', 1200.00, '2026-01-12 15:00:00+00', 'Golden Unit 2 again'),
    (6, 'bank_cash', 'Property 3', 999.00, '2026-02-01 15:00:00+00', 'Golden Owner 2')
) AS p (n, source, target, amount, event_at, memo)
ORDER BY p.n;

CREATE TEMP VIEW golden_transfer AS
SELECT t.id, t.metadata ->> 'memo' AS memo
FROM pgledger_transfers AS t
JOIN golden_account AS a ON a.id = t.to_account_id
WHERE t.metadata ->> 'memo' LIKE 'Golden %';

SELECT trust_apply_payment(
    '00000000-0000-4000-8000-000000000001', m.charge_id,
    (SELECT t.id FROM golden_transfer AS t WHERE t.memo = m.memo), m.amount
)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000081'::uuid, 'Golden Unit 1 rent', 1500.00),
    (2, '00000000-0000-4000-8000-000000000082', 'Golden Unit 2 rent', 1200.00)
) AS m (n, charge_id, memo, amount)
ORDER BY m.n;

-- The Unit 2 payment bounced, and was paid again.
SELECT trust_reverse_payment(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000082',
    (SELECT t.id FROM golden_transfer AS t WHERE t.memo = 'Golden Unit 2 rent'),
    (SELECT t.id FROM golden_transfer AS t WHERE t.memo = 'Golden Unit 2 bounce'),
    1200.00
);

SELECT trust_apply_payment(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000082',
    (SELECT t.id FROM golden_transfer AS t WHERE t.memo = 'Golden Unit 2 again'), 1200.00
);

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
WHERE a.name IN ('Property 1', 'Property 2')
ORDER BY a.name;

SELECT trust_draw_owner(
    '00000000-0000-4000-8000-000000000001',
    (SELECT id FROM golden_account WHERE name = 'Property 1'),
    'golden-february-draw', NULL, '2026-02-01 10:00:00+00'
);

SELECT trust_record_owner_contribution(
    '00000000-0000-4000-8000-000000000001',
    (SELECT id FROM golden_account WHERE name = 'Property 2'),
    'golden-roof-fund', 400.00, '2026-03-01 12:00:00+00', 'Roof fund'
);

-- The roof bill: entered, approved (no limit), set aside, paid.
INSERT INTO trust_bills (pmc_id, vendor_id, ledger_account_id, reference, bill_date, due_on,
                         amount, memo)
VALUES ('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000051',
        (SELECT id FROM golden_account WHERE name = 'Property 2'), 'ROOF-1', '2026-04-01',
        '2026-04-15', 250.00, 'Roof repair');

INSERT INTO trust_bill_approvals (pmc_id, bill_id, approved_on, approved_by)
SELECT b.pmc_id, b.id, '2026-04-01', 'Owner 1, by email'
FROM trust_bills AS b
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001' AND b.reference = 'ROOF-1';

SELECT trust_set_aside_bill(b.pmc_id, b.id, '2026-04-02 12:00:00+00')
FROM trust_bills AS b
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001' AND b.reference = 'ROOF-1';

SELECT trust_pay_bill(b.pmc_id, b.id, '2026-04-10 12:00:00+00')
FROM trust_bills AS b
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001' AND b.reference = 'ROOF-1';

-- Later in the year, and the next year, posted after the draw: a draw pays out what the account
-- holds when it is made.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = p.source),
    (SELECT id FROM golden_account WHERE name = p.target),
    p.amount, p.event_at, jsonb_build_object('memo', p.memo)
)
FROM (VALUES
    (1, 'Property 1', 'Property 2', 100.00, '2026-06-01 15:00:00+00'::timestamptz,
     'Golden move'),
    (2, 'bank_cash', 'Property 1', 500.00, '2027-01-02 15:00:00+00', 'Golden next year')
) AS p (n, source, target, amount, event_at, memo)
ORDER BY p.n;

SELECT * FROM trust_report_owner_year_end(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000021', 2026
);
