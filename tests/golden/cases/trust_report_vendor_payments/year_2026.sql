-- What each vendor was paid in 2026. Worked by hand:
--   Vendor 1 (Golden Lawn Care): paid 120.00 on Jan 5 and 120.00 on Feb 5, 2026; its 300.00 of
--   Dec 30, 2025 is the year before. 2 payments, 240.00.
--   Vendor 2 (Golden Plumbing): paid 450.25 on Dec 31, 2026 at 23:30 UTC (still 2026 in UTC);
--   a 75.00 bill set aside but not paid is not a payment. 1 payment, 450.25.
--   Both are marked for a 1099 and paid under 2026's threshold of 2000.00 (q060), so both are
--   under the threshold and the 1099s' total is 0 payments, 0.00; no vendor is marked for none.
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
 'Golden Plumbing');

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
    (1, '00000000-0000-4000-8000-000000000051'::uuid, 'LAWN-2025-12', 300.00,
     '2025-12-20 15:00:00+00'::timestamptz, '2025-12-30 15:00:00+00'::timestamptz),
    (2, '00000000-0000-4000-8000-000000000051', 'LAWN-2026-01', 120.00,
     '2026-01-02 15:00:00+00', '2026-01-05 15:00:00+00'),
    (3, '00000000-0000-4000-8000-000000000051', 'LAWN-2026-02', 120.00,
     '2026-02-02 15:00:00+00', '2026-02-05 15:00:00+00'),
    (4, '00000000-0000-4000-8000-000000000052', 'PL-881', 450.25,
     '2026-12-20 15:00:00+00', '2026-12-31 23:30:00+00'),
    (5, '00000000-0000-4000-8000-000000000052', 'PL-882', 75.00,
     '2026-12-21 15:00:00+00', NULL)
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

SELECT * FROM trust_report_vendor_payments('00000000-0000-4000-8000-000000000001', 2026);
