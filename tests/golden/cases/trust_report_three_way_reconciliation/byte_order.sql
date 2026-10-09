-- Ledger lines list in byte order, the same on every server: "Owner b" before "owner a" ("O" is
-- 0x4F, "o" is 0x6F), where a server sorting by language rules would list "owner a" first. Two
-- owners, 10.00 each in January; the statement agrees with the books.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'owner a'),
('00000000-0000-4000-8000-000000000022', '00000000-0000-4000-8000-000000000001', 'Owner b');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1'),
('00000000-0000-4000-8000-000000000032', '00000000-0000-4000-8000-000000000001', 'Property 2');

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'bank_cash', NULL, NULL, NULL
);

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'owner_property', '00000000-0000-4000-8000-000000000021',
    '00000000-0000-4000-8000-000000000031', NULL
);

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'owner_property', '00000000-0000-4000-8000-000000000022',
    '00000000-0000-4000-8000-000000000032', NULL
);

SELECT pgledger_create_transfer(
    (SELECT ledger_account_id FROM trust_ledger_accounts
     WHERE bank_account_id = '00000000-0000-4000-8000-000000000011' AND kind = 'bank_cash'),
    (SELECT ledger_account_id FROM trust_ledger_accounts
     WHERE owner_id = '00000000-0000-4000-8000-000000000021'),
    10.00, '2026-01-05 12:00:00+00'
);

SELECT pgledger_create_transfer(
    (SELECT ledger_account_id FROM trust_ledger_accounts
     WHERE bank_account_id = '00000000-0000-4000-8000-000000000011' AND kind = 'bank_cash'),
    (SELECT ledger_account_id FROM trust_ledger_accounts
     WHERE owner_id = '00000000-0000-4000-8000-000000000022'),
    10.00, '2026-01-06 12:00:00+00'
);

-- Approved on Feb 3, inserted directly so the approval time in the report is fixed.
INSERT INTO trust_reconciliations (
    pmc_id, bank_account_id, period_start, period_end, statement_balance, book_balance,
    prepared_by, approved_by, approved_at
) VALUES (
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    '2026-01-01 00:00:00+00', '2026-02-01 00:00:00+00', 20.00, 20.00,
    'Preparer 1', 'Approver 1', '2026-02-03 17:30:00+00'
);

SELECT * FROM trust_report_three_way_reconciliation(
    '00000000-0000-4000-8000-000000000001',
    (SELECT id FROM trust_reconciliations
     WHERE pmc_id = '00000000-0000-4000-8000-000000000001')
);
