-- Owner 1's balance sheet at the end of 2026, accrual basis, of the accrual income statement's
-- books. Worked by hand:
--   Property 1  cash 1500.00 (Jan rent, bounced, paid again) + 75.00 (key) - 750.00 leasing fee
--               - 130.00 management fee - 60.00 - 30.00 - 25.00 (LAWN-1, LAWN-R-2026-02 and
--               MISC-1 set aside on Feb 16): 580.00. Owed by tenants: February's rent 1500.00
--               less the 100.00 concession, 1400.00. No housing authority. Bills not yet set
--               aside: PLUMB-1, 120.00 (PAINT-1 was declined). Equity 1860.00.
--   Property 2  cash 1285.00 - 123.50 fee - 250.00 - 40.00 (set aside Feb 16): 871.50. Owed by
--               tenants: rent 1200.00 and pet rent 35.00 of February: 1235.00. Equity 2106.50.
--   All properties: cash 1451.50, owed by tenants 2635.00, bills 120.00, equity 3966.50: the
--   year's net income on the accrual basis, as the owner drew and put in nothing.
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
 'Golden Roofing'),
('00000000-0000-4000-8000-000000000052', '00000000-0000-4000-8000-000000000001',
 'Golden Lawn Care');

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
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011', v.id
)
FROM trust_vendors AS v
WHERE v.pmc_id = '00000000-0000-4000-8000-000000000001'
ORDER BY v.id;

-- Each account by a short name, for the steps below.
CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id, coalesce(pr.display_name, t.kind) AS name
FROM trust_ledger_accounts AS t
LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001' AND t.kind <> 'vendor_payable';

INSERT INTO trust_management_agreements (
    pmc_id, ledger_account_id, starts_on, fee_percent, minimum_fee, flat_fee,
    leasing_fee_percent, reserve
)
SELECT '00000000-0000-4000-8000-000000000001', a.id, '2026-01-01', g.pct, 0.00, g.flat,
       g.leasing, 0.00
FROM (VALUES
    ('Property 1', 8.00, 10.00, 50.00),
    ('Property 2', 10.00, 0.00, 0.00)
) AS g (name, pct, flat, leasing)
JOIN golden_account AS a ON a.name = g.name;

INSERT INTO trust_late_fee_policies (pmc_id, property_id, starts_on, grace_days, flat_fee)
VALUES ('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000032',
        '2026-01-01', 5, 50.00);

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

INSERT INTO trust_recurring_charges (pmc_id, lease_id, kind, amount, memo, starts_on)
SELECT l.pmc_id, l.id, 'fee', 35.00, 'Pet rent', '2026-01-01'
FROM trust_leases AS l
WHERE l.unit_id = '00000000-0000-4000-8000-000000000042';

SELECT trust_charge_rent_due('00000000-0000-4000-8000-000000000001', '2026-01-01');
SELECT trust_charge_recurring_due('00000000-0000-4000-8000-000000000001', '2026-01-01');

INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
SELECT l.pmc_id, l.id, '2026-01-15', 'fee', 75.00, 'Key replacement'
FROM trust_leases AS l
WHERE l.unit_id = '00000000-0000-4000-8000-000000000041';

-- Unit 2's rent and pet rent are still open at the end of January 6: its late fee.
SELECT trust_assess_late_fees('00000000-0000-4000-8000-000000000001', '2026-01-07');

-- Each charge by a short name: (unit, memo).
CREATE TEMP VIEW golden_charge AS
SELECT c.id, u.display_name AS unit, c.kind, c.memo
FROM trust_charges AS c
JOIN trust_leases AS l ON l.id = c.lease_id
JOIN trust_units AS u ON u.id = l.unit_id
WHERE c.pmc_id = '00000000-0000-4000-8000-000000000001';

-- Money moved: (from, to, amount, date, memo), one statement each so they post in this order.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = p.source),
    (SELECT id FROM golden_account WHERE name = p.target),
    p.amount, p.event_at, jsonb_build_object('memo', p.memo)
)
FROM (VALUES
    (1, 'bank_cash', 'Property 1', 1500.00, '2026-01-03 15:00:00+00'::timestamptz,
     'Golden Unit 1 rent'),
    (2, 'Property 1', 'bank_cash', 1500.00, '2026-01-04 15:00:00+00', 'Golden Unit 1 bounce'),
    (3, 'bank_cash', 'Property 1', 1500.00, '2026-01-05 15:00:00+00', 'Golden Unit 1 again'),
    (4, 'bank_cash', 'Property 2', 1285.00, '2026-01-10 15:00:00+00', 'Golden Unit 2 all'),
    (5, 'bank_cash', 'Property 1', 75.00, '2026-01-20 15:00:00+00', 'Golden Unit 1 key'),
    (6, 'bank_cash', 'Property 3', 999.00, '2026-02-01 15:00:00+00', 'Golden Owner 2')
) AS p (n, source, target, amount, event_at, memo)
ORDER BY p.n;

CREATE TEMP VIEW golden_transfer AS
SELECT t.id, t.metadata ->> 'memo' AS memo
FROM pgledger_transfers AS t
WHERE t.metadata ->> 'memo' LIKE 'Golden %';

SELECT trust_apply_payment(
    '00000000-0000-4000-8000-000000000001',
    (SELECT c.id FROM golden_charge AS c WHERE c.unit = m.unit AND c.memo = m.charge),
    (SELECT t.id FROM golden_transfer AS t WHERE t.memo = m.memo), m.amount
)
FROM (VALUES
    (1, 'Unit 1', 'Rent due 2026-01-01', 'Golden Unit 1 rent', 1500.00),
    (2, 'Unit 2', 'Rent due 2026-01-01', 'Golden Unit 2 all', 1200.00),
    (3, 'Unit 2', 'Pet rent due 2026-01-01', 'Golden Unit 2 all', 35.00),
    (4, 'Unit 1', 'Key replacement', 'Golden Unit 1 key', 75.00)
) AS m (n, unit, charge, memo, amount)
ORDER BY m.n;

SELECT trust_apply_payment(
    '00000000-0000-4000-8000-000000000001', c.id,
    (SELECT t.id FROM golden_transfer AS t WHERE t.memo = 'Golden Unit 2 all'), 50.00
)
FROM golden_charge AS c
JOIN trust_late_fees AS f ON f.fee_charge_id = c.id;

-- The Unit 1 payment bounced, and was paid again.
SELECT trust_reverse_payment(
    '00000000-0000-4000-8000-000000000001',
    (SELECT c.id FROM golden_charge AS c WHERE c.unit = 'Unit 1' AND c.kind = 'rent'),
    (SELECT t.id FROM golden_transfer AS t WHERE t.memo = 'Golden Unit 1 rent'),
    (SELECT t.id FROM golden_transfer AS t WHERE t.memo = 'Golden Unit 1 bounce'),
    1500.00
);

SELECT trust_apply_payment(
    '00000000-0000-4000-8000-000000000001',
    (SELECT c.id FROM golden_charge AS c WHERE c.unit = 'Unit 1' AND c.kind = 'rent'),
    (SELECT t.id FROM golden_transfer AS t WHERE t.memo = 'Golden Unit 1 again'), 1500.00
);

-- Fees.
SELECT trust_post_leasing_fee(
    '00000000-0000-4000-8000-000000000001',
    (SELECT id FROM trust_leases WHERE unit_id = '00000000-0000-4000-8000-000000000041'),
    (SELECT id FROM golden_account WHERE name = 'Property 1'),
    '2026-01-06 09:00:00+00'
);

SELECT trust_post_management_fee(
    '00000000-0000-4000-8000-000000000001', a.id, '2026-01-01', '2026-02-01',
    '2026-01-31 18:00:00+00'
)
FROM golden_account AS a
WHERE a.name IN ('Property 1', 'Property 2')
ORDER BY a.name;

-- Expense accounts, each on its Schedule E line.
INSERT INTO trust_expense_accounts (id, pmc_id, name, schedule_e_line) VALUES
('00000000-0000-4000-8000-000000000071', '00000000-0000-4000-8000-000000000001',
 'Landscaping', 7),
('00000000-0000-4000-8000-000000000072', '00000000-0000-4000-8000-000000000001',
 'Roof repairs', 14),
('00000000-0000-4000-8000-000000000073', '00000000-0000-4000-8000-000000000001',
 'Water', 17);

-- A lawn service every month, in Landscaping: February's bill is entered from it.
INSERT INTO trust_recurring_bills (
    id, pmc_id, vendor_id, ledger_account_id, reference_prefix, amount, memo, day_of_month,
    starts_on
)
SELECT '00000000-0000-4000-8000-000000000091', '00000000-0000-4000-8000-000000000001',
       '00000000-0000-4000-8000-000000000052', a.id, 'LAWN-R', 30.00, 'Lawn service', 15,
       '2026-02-01'
FROM golden_account AS a WHERE a.name = 'Property 1';

INSERT INTO trust_recurring_bill_expense_accounts (pmc_id, recurring_bill_id, expense_account_id)
VALUES ('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000091',
        '00000000-0000-4000-8000-000000000071');

SELECT trust_enter_recurring_bills('00000000-0000-4000-8000-000000000001', '2026-02-28');

-- Bills: (property, vendor, reference, amount, set aside on).
INSERT INTO trust_bills (pmc_id, vendor_id, ledger_account_id, reference, bill_date, due_on,
                         amount, memo)
SELECT '00000000-0000-4000-8000-000000000001', b.vendor, a.id, b.reference, '2026-02-01',
       '2026-02-15', b.amount, b.memo
FROM (VALUES
    ('Property 2', '00000000-0000-4000-8000-000000000051'::uuid, 'ROOF-1', 250.00,
     'Roof repair'),
    ('Property 2', '00000000-0000-4000-8000-000000000052', 'WATER-1', 40.00, 'Water'),
    ('Property 1', '00000000-0000-4000-8000-000000000052', 'LAWN-1', 60.00, 'Lawn cleanup'),
    ('Property 1', '00000000-0000-4000-8000-000000000052', 'MISC-1', 25.00, 'Hose'),
    ('Property 3', '00000000-0000-4000-8000-000000000051', 'ROOF-3', 99.00, 'Owner 2 roof')
) AS b (property, vendor, reference, amount, memo)
JOIN golden_account AS a ON a.name = b.property;

-- What each bill is: WATER-1 is put in Landscaping, then moved to Water. MISC-1 is in none.
INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)
SELECT b.pmc_id, b.id, x.account
FROM (VALUES
    (1, 'ROOF-1', '00000000-0000-4000-8000-000000000072'::uuid),
    (2, 'WATER-1', '00000000-0000-4000-8000-000000000071'),
    (3, 'LAWN-1', '00000000-0000-4000-8000-000000000071'),
    (4, 'WATER-1', '00000000-0000-4000-8000-000000000073'),
    (5, 'ROOF-3', '00000000-0000-4000-8000-000000000072')
) AS x (n, reference, account)
JOIN trust_bills AS b ON b.reference = x.reference
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001'
ORDER BY x.n;

INSERT INTO trust_bill_approvals (pmc_id, bill_id, approved_on, approved_by)
SELECT b.pmc_id, b.id, '2026-02-01', 'Owner, by email'
FROM trust_bills AS b
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001' AND b.reference <> 'ROOF-3'
ORDER BY b.reference;

SELECT trust_set_aside_bill(b.pmc_id, b.id, '2026-02-16 12:00:00+00')
FROM trust_bills AS b
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001' AND b.reference <> 'ROOF-3'
ORDER BY b.reference;

-- The accrual basis's own: February's rent and pet rent charged and never paid, a 100.00
-- concession on Unit 1 in February, a plumbing bill dated December 20 never set aside, and a
-- painting bill the owner declined.
SELECT trust_charge_rent_due('00000000-0000-4000-8000-000000000001', '2026-02-01');
SELECT trust_charge_recurring_due('00000000-0000-4000-8000-000000000001', '2026-02-01');

INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)
SELECT l.pmc_id, l.id, '2026-02-10', 'credit', 100.00, 'Concession'
FROM trust_leases AS l
WHERE l.unit_id = '00000000-0000-4000-8000-000000000041';

INSERT INTO trust_bills (pmc_id, vendor_id, ledger_account_id, reference, bill_date, due_on,
                         amount, memo)
SELECT '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000051', a.id,
       b.reference, b.dated, b.dated + 14, b.amount, b.memo
FROM (VALUES
    ('PLUMB-1', '2026-12-20'::date, 120.00, 'Drain'),
    ('PAINT-1', '2026-03-01', 800.00, 'Painting')
) AS b (reference, dated, amount, memo)
JOIN golden_account AS a ON a.name = 'Property 1';

INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)
SELECT b.pmc_id, b.id, '00000000-0000-4000-8000-000000000072'
FROM trust_bills AS b
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001' AND b.reference = 'PLUMB-1';

INSERT INTO trust_bill_declines (pmc_id, bill_id, declined_on, declined_by, reason)
SELECT b.pmc_id, b.id, '2026-03-02', 'Owner 1, by email', 'Not needed'
FROM trust_bills AS b
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001' AND b.reference = 'PAINT-1';

SELECT * FROM trust_report_owner_balance_sheet(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000021', '2026-12-31'
);
