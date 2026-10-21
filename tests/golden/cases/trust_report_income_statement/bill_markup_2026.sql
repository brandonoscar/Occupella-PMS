-- Owner 1's income statement for 2026 with a markup on a bill (migration 0083). Worked by
-- hand: Owner 1 put 1000.00 into Property 1 (a contribution, not income). Their agreement from
-- Jan 1 lets the company add 10% to a bill. Two bills dated Mar 5 were set aside on Mar 10: a
-- plumber's 400.00 in Repairs (Schedule E line 14) with the markup, 40.00, and the water
-- company's 60.00 in Utilities (line 17) without one. The markup counts with the management
-- fees (line 11): expenses 40.00 + 400.00 + 60.00 = 500.00, income 0.00, net income -500.00.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1');

INSERT INTO trust_vendors (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000001', 'Vendor 1');

INSERT INTO trust_expense_accounts (id, pmc_id, name, schedule_e_line) VALUES
('00000000-0000-4000-8000-000000000051', '00000000-0000-4000-8000-000000000001', 'Repairs', 14),
('00000000-0000-4000-8000-000000000052', '00000000-0000-4000-8000-000000000001', 'Utilities',
 17);

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

SELECT trust_open_vendor_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    '00000000-0000-4000-8000-000000000041'
);

INSERT INTO trust_management_agreements (pmc_id, ledger_account_id, starts_on, approval_limit,
                                         bill_markup_percent)
SELECT t.pmc_id, t.ledger_account_id, '2026-01-01', 1000.00, 10
FROM trust_ledger_accounts AS t
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001' AND t.kind = 'owner_property';

SELECT trust_record_owner_contribution(
    '00000000-0000-4000-8000-000000000001', t.ledger_account_id, 'golden-contribution', 1000.00,
    '2026-03-01 12:00:00+00'
)
FROM trust_ledger_accounts AS t
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001' AND t.kind = 'owner_property';

INSERT INTO trust_bills (id, pmc_id, vendor_id, ledger_account_id, reference, bill_date, due_on,
                         amount, memo)
SELECT b.id, t.pmc_id, '00000000-0000-4000-8000-000000000041', t.ledger_account_id, b.reference,
       '2026-03-05', '2026-03-20', b.amount, b.memo
FROM trust_ledger_accounts AS t
CROSS JOIN (VALUES
    ('00000000-0000-4000-8000-000000000061'::uuid, 'PLUMB-1', 400.00, 'Kitchen leak'),
    ('00000000-0000-4000-8000-000000000062'::uuid, 'WATER-3', 60.00, 'Water, February')
) AS b (id, reference, amount, memo)
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001' AND t.kind = 'owner_property';

INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000061',
 '00000000-0000-4000-8000-000000000051'),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000062',
 '00000000-0000-4000-8000-000000000052');

-- The markup on the plumber's bill: the agreement's 10%, worked out by the database.
INSERT INTO trust_bill_markups (bill_id, pmc_id) VALUES
('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001');

SELECT trust_set_aside_bill(
    '00000000-0000-4000-8000-000000000001', b.id, '2026-03-10 12:00:00+00'
)
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000061'::uuid),
    (2, '00000000-0000-4000-8000-000000000062'::uuid)
) AS b (n, id)
ORDER BY b.n;

SELECT * FROM trust_report_income_statement(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000021',
    '2026-01-01', '2026-12-31'
);
