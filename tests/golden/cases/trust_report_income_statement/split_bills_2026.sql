-- Owner 1's income statement for 2026 with bills split across expense accounts (migration
-- 0069). Worked by hand: Owner 1 put 1000.00 into Property 1 (a contribution, not income), and
-- three bills were set aside on Mar 2:
--   FIX-1 300.00  split: Repairs 200.00, Supplies 100.00.
--   FIX-2 120.00  first split Repairs 60.00 and Water 60.00, then put wholly in Water: its latest
--                 row counts, so Water 120.00.
--   FIX-3  80.00  first wholly in Repairs, then split Repairs 50.00 and Water 30.00.
-- Repairs (Schedule E line 14) 250.00, Supplies (line 15) 100.00, Water (line 17) 150.00:
-- expenses 500.00, which is every bill set aside; income 0.00, net income -500.00.
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
('00000000-0000-4000-8000-000000000051', '00000000-0000-4000-8000-000000000001',
 'Golden Plumbing');

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
    '00000000-0000-4000-8000-000000000051'
);

SELECT trust_record_owner_contribution(
    '00000000-0000-4000-8000-000000000001', t.ledger_account_id, 'golden-contribution', 1000.00,
    '2026-03-01 12:00:00+00'
)
FROM trust_ledger_accounts AS t
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001' AND t.kind = 'owner_property';

INSERT INTO trust_expense_accounts (id, pmc_id, name, schedule_e_line) VALUES
('00000000-0000-4000-8000-000000000071', '00000000-0000-4000-8000-000000000001', 'Repairs', 14),
('00000000-0000-4000-8000-000000000072', '00000000-0000-4000-8000-000000000001', 'Supplies', 15),
('00000000-0000-4000-8000-000000000073', '00000000-0000-4000-8000-000000000001', 'Water', 17);

INSERT INTO trust_bills (
    pmc_id, vendor_id, ledger_account_id, reference, bill_date, due_on, amount, memo
)
SELECT t.pmc_id, '00000000-0000-4000-8000-000000000051', t.ledger_account_id, b.reference,
       '2026-03-01', '2026-03-15', b.amount, 'Plumbing'
FROM (VALUES ('FIX-1', 300.00), ('FIX-2', 120.00), ('FIX-3', 80.00)) AS b (reference, amount)
CROSS JOIN trust_ledger_accounts AS t
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001' AND t.kind = 'owner_property';

-- Each bill's rows in turn, a split's lines right after its row.
INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)
SELECT b.pmc_id, b.id, '00000000-0000-4000-8000-000000000071' FROM trust_bills AS b
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001' AND b.reference = 'FIX-1';
INSERT INTO trust_bill_splits (pmc_id, bill_id, categorized_id, expense_account_id, amount)
SELECT b.pmc_id, b.id,
       (SELECT max(x.id) FROM trust_bill_expense_accounts AS x WHERE x.bill_id = b.id),
       l.account, l.amount
FROM trust_bills AS b
CROSS JOIN (VALUES ('00000000-0000-4000-8000-000000000071'::uuid, 200.00),
       ('00000000-0000-4000-8000-000000000072'::uuid, 100.00)) AS l (account, amount)
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001' AND b.reference = 'FIX-1'
ORDER BY l.account;

INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)
SELECT b.pmc_id, b.id, '00000000-0000-4000-8000-000000000071' FROM trust_bills AS b
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001' AND b.reference = 'FIX-2';
INSERT INTO trust_bill_splits (pmc_id, bill_id, categorized_id, expense_account_id, amount)
SELECT b.pmc_id, b.id,
       (SELECT max(x.id) FROM trust_bill_expense_accounts AS x WHERE x.bill_id = b.id),
       l.account, l.amount
FROM trust_bills AS b
CROSS JOIN (VALUES ('00000000-0000-4000-8000-000000000071'::uuid, 60.00),
       ('00000000-0000-4000-8000-000000000073'::uuid, 60.00)) AS l (account, amount)
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001' AND b.reference = 'FIX-2'
ORDER BY l.account;
INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)
SELECT b.pmc_id, b.id, '00000000-0000-4000-8000-000000000073' FROM trust_bills AS b
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001' AND b.reference = 'FIX-2';

INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)
SELECT b.pmc_id, b.id, '00000000-0000-4000-8000-000000000071' FROM trust_bills AS b
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001' AND b.reference = 'FIX-3';
INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)
SELECT b.pmc_id, b.id, '00000000-0000-4000-8000-000000000071' FROM trust_bills AS b
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001' AND b.reference = 'FIX-3';
INSERT INTO trust_bill_splits (pmc_id, bill_id, categorized_id, expense_account_id, amount)
SELECT b.pmc_id, b.id,
       (SELECT max(x.id) FROM trust_bill_expense_accounts AS x WHERE x.bill_id = b.id),
       l.account, l.amount
FROM trust_bills AS b
CROSS JOIN (VALUES ('00000000-0000-4000-8000-000000000071'::uuid, 50.00),
       ('00000000-0000-4000-8000-000000000073'::uuid, 30.00)) AS l (account, amount)
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001' AND b.reference = 'FIX-3'
ORDER BY l.account;

INSERT INTO trust_bill_approvals (pmc_id, bill_id, approved_on, approved_by)
SELECT b.pmc_id, b.id, '2026-03-01', 'Owner, by email'
FROM trust_bills AS b
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001'
ORDER BY b.reference;

SELECT trust_set_aside_bill(b.pmc_id, b.id, '2026-03-02 12:00:00+00')
FROM trust_bills AS b
WHERE b.pmc_id = '00000000-0000-4000-8000-000000000001'
ORDER BY b.reference;

SELECT * FROM trust_report_income_statement(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000021',
    '2026-01-01', '2026-12-31'
);
