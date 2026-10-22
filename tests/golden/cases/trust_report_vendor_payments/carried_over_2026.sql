-- What each vendor was paid in 2026, by a PMC that switched to the PMS on March 1, 2026.
-- Worked by hand:
--   Vendor 1 (Golden Lawn Care): the old system paid it 360.00 before March 1, 2026; the PMS
--   paid it 120.00 on Mar 5 and 120.00 on Apr 5, 2026. 2 payments here, 600.00 in all, of which
--   360.00 was carried over.
--   Vendor 2 (Golden Plumbing): paid only in the old system, 1,250.75 before March 1, 2026.
--   0 payments here, 1250.75 in all, all carried over.
--   Vendor 3 (Golden Roofing): 400.00 carried over for 2025 is the year before.
--   All three are marked for a 1099 and the 2026 ones were paid under 2026's threshold of
--   2000.00 (q060): under the threshold, with what was carried over under each, and the 1099s'
--   total is 0 payments, 0.00.
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
 'Golden Lawn Care'),
('00000000-0000-4000-8000-000000000052', '00000000-0000-4000-8000-000000000001',
 'Golden Plumbing'),
('00000000-0000-4000-8000-000000000053', '00000000-0000-4000-8000-000000000001',
 'Golden Roofing');

INSERT INTO trust_vendor_profiles (pmc_id, vendor_id, gets_1099)
SELECT '00000000-0000-4000-8000-000000000001', v.id, true
FROM trust_vendors AS v WHERE v.pmc_id = '00000000-0000-4000-8000-000000000001';

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'bank_cash', NULL, NULL, NULL
);

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    'owner_property', '00000000-0000-4000-8000-000000000021',
    '00000000-0000-4000-8000-000000000031', NULL
);

SELECT trust_open_vendor_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011', v.id
)
FROM trust_vendors AS v
WHERE v.pmc_id = '00000000-0000-4000-8000-000000000001'
ORDER BY v.display_name;

-- Owner 1's rent, enough for every bill.
SELECT pgledger_create_transfer(
    (SELECT ledger_account_id FROM trust_ledger_accounts
     WHERE pmc_id = '00000000-0000-4000-8000-000000000001' AND kind = 'bank_cash'),
    (SELECT ledger_account_id FROM trust_ledger_accounts
     WHERE pmc_id = '00000000-0000-4000-8000-000000000001' AND kind = 'owner_property'),
    5000.00, '2025-12-01 15:00:00+00', NULL
);

-- Bills: (vendor, reference, amount, set aside at, paid at or NULL).
CREATE TEMP TABLE golden_bill AS
SELECT n, vendor_id, reference, amount, set_aside_at, paid_at
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000051'::uuid, 'LAWN-2026-03', 120.00,
     '2026-03-02 15:00:00+00'::timestamptz, '2026-03-05 15:00:00+00'::timestamptz),
    (2, '00000000-0000-4000-8000-000000000051', 'LAWN-2026-04', 120.00,
     '2026-04-02 15:00:00+00', '2026-04-05 15:00:00+00')
) AS b (n, vendor_id, reference, amount, set_aside_at, paid_at);

INSERT INTO trust_bills (pmc_id, vendor_id, ledger_account_id, reference, bill_date, due_on,
                         amount, memo)
SELECT '00000000-0000-4000-8000-000000000001', b.vendor_id,
       (SELECT ledger_account_id FROM trust_ledger_accounts
        WHERE pmc_id = '00000000-0000-4000-8000-000000000001' AND kind = 'owner_property'),
       b.reference, (b.set_aside_at AT TIME ZONE 'UTC')::date,
       (b.set_aside_at AT TIME ZONE 'UTC')::date, b.amount, 'Golden'
FROM golden_bill AS b ORDER BY b.n;

-- No agreement, so a limit of 0: the owner approves every bill.
INSERT INTO trust_bill_approvals (pmc_id, bill_id, approved_on, approved_by)
SELECT t.pmc_id, t.id, t.bill_date, 'Owner 1, by email'
FROM trust_bills AS t WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001';

SELECT trust_set_aside_bill('00000000-0000-4000-8000-000000000001', t.id, b.set_aside_at)
FROM golden_bill AS b JOIN trust_bills AS t
    ON t.pmc_id = '00000000-0000-4000-8000-000000000001' AND t.reference = b.reference
ORDER BY b.n;

SELECT trust_pay_bill('00000000-0000-4000-8000-000000000001', t.id, b.paid_at)
FROM golden_bill AS b JOIN trust_bills AS t
    ON t.pmc_id = '00000000-0000-4000-8000-000000000001' AND t.reference = b.reference
WHERE b.paid_at IS NOT NULL
ORDER BY b.n;

-- What the old system paid, carried over at the cutover.
INSERT INTO trust_vendor_payments_carried_over (pmc_id, vendor_id, year, paid_before, amount)
VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000051', 2026,
 '2026-03-01', 360.00),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000052', 2026,
 '2026-03-01', 1250.75),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000053', 2025,
 '2026-01-01', 400.00);

SELECT * FROM trust_report_vendor_payments('00000000-0000-4000-8000-000000000001', 2026);
