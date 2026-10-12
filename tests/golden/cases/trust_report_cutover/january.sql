-- A synthetic PMC's operating trust account cut over from its old system on Jan 1, with two
-- items the bank had not cleared, signed off on Jan 2. Worked by hand:
--   opening balances = 1200.00 + 800.00 + 300.00 + 150.00 + 50.00 = 2500.00 = old book cash
--   items not cleared = check 1041 (-200.00) and a deposit in transit (75.00) = -125.00
--   bank balance to expect = 2500.00 - (-125.00) = 2625.00
-- The opening and the sign-off are written directly, so their times in the report are fixed;
-- trust_post_opening_balances and a sign-off insert stamp now().
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

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 1');

INSERT INTO trust_vendors (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000051', '00000000-0000-4000-8000-000000000001', 'Vendor 1');

INSERT INTO trust_staff (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001', 'Staff 1');

-- Staff 1 is the PMC's first admin, and may approve: signing off the tie-out takes that.
INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000061', 'admin', true);
SELECT set_config('trust.staff_id', '00000000-0000-4000-8000-000000000061', true);
INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000061', 'approve', true);

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'bank_cash', NULL, NULL, NULL
);

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'pmc_income', NULL, NULL, NULL
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

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'prepaid_rent', NULL, NULL, '00000000-0000-4000-8000-000000000041'
);

SELECT trust_open_vendor_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    '00000000-0000-4000-8000-000000000051'
);

CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id,
       CASE t.kind
           WHEN 'bank_cash' THEN 'cash'
           WHEN 'owner_property' THEN o.display_name
           WHEN 'vendor_payable' THEN 'vendor'
           WHEN 'pmc_income' THEN 'fees'
           ELSE 'prepaid'
       END AS name
FROM trust_ledger_accounts AS t
LEFT JOIN trust_owners AS o ON o.id = t.owner_id
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001';

-- The opening, as trust_post_opening_balances posts it: from cash, at the start of Jan 1.
CREATE TEMP TABLE golden_opening AS
SELECT a.name, b.amount, (
    SELECT pgledger_create_transfer(
        (SELECT id FROM golden_account WHERE name = 'cash'), a.id, b.amount,
        '2026-01-01 00:00:00+00', '{"memo": "Opening balance"}'
    )
) AS transfer
FROM (VALUES ('Owner 1', 1200.00), ('Owner 2', 800.00), ('prepaid', 300.00),
             ('vendor', 150.00), ('fees', 50.00)) AS b (name, amount)
JOIN golden_account AS a ON a.name = b.name;

INSERT INTO trust_opening_balances (
    bank_account_id, pmc_id, opened_on, balances, book_cash, entered_by, transfer_ids,
    created_at
)
SELECT '00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001',
       '2026-01-01',
       (SELECT jsonb_object_agg(a.id, o.amount) FROM golden_opening AS o
        JOIN golden_account AS a ON a.name = o.name),
       2500.00, 'Preparer 1',
       (SELECT array_agg((o.transfer).id ORDER BY o.name) FROM golden_opening AS o),
       '2026-01-01 15:00:00+00';

UPDATE trust_bank_accounts SET opened_at = '2026-01-01 00:00:00+00'
WHERE id = '00000000-0000-4000-8000-000000000011';

INSERT INTO trust_cutover_items (
    pmc_id, bank_account_id, issued_on, amount, description, reference, entered_by
) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
 '2025-12-28', -200.00, 'Check to Vendor 1', '1041', '00000000-0000-4000-8000-000000000061'),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
 '2025-12-31', 75.00, 'Deposit, Tenant 1', NULL, '00000000-0000-4000-8000-000000000061');

INSERT INTO trust_cutover_signoffs (bank_account_id, pmc_id, signed_by, signed_at) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000061', '2026-01-02 10:30:00+00');

SELECT * FROM trust_report_cutover(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011'
);
