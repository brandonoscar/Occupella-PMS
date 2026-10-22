-- The 1099 summary for 2026 (q060): only vendors marked for a 1099 are on it, against 2026's
-- threshold of 2000.00. Worked by hand:
--   Golden Electric, marked: 1500.00 on Mar 1 and 600.00 on Apr 1, 2 payments, 2100.00: on it.
--   Golden Plumbing, marked: 2000.00 on May 1, exactly the threshold: on it.
--   The 1099s' total: 3 payments, 4100.00.
--   Golden Lawn Care, marked: 1999.99 on Jun 1, a cent short: under the threshold.
--   Golden Painting, no profile: 300.00 on Jul 1: no 1099 setting, listed so staff can say.
--   Golden Roofing Inc, marked and then not (the latest holds): 800.00 on Aug 1: one vendor
--   marked for none, 1 payment, 800.00.
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
 'Golden Electric'),
('00000000-0000-4000-8000-000000000052', '00000000-0000-4000-8000-000000000001',
 'Golden Lawn Care'),
('00000000-0000-4000-8000-000000000053', '00000000-0000-4000-8000-000000000001',
 'Golden Plumbing'),
('00000000-0000-4000-8000-000000000054', '00000000-0000-4000-8000-000000000001',
 'Golden Painting'),
('00000000-0000-4000-8000-000000000055', '00000000-0000-4000-8000-000000000001',
 'Golden Roofing Inc');

INSERT INTO trust_vendor_profiles (pmc_id, vendor_id, gets_1099) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000051', true),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000052', true),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000053', true),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000055', true);

-- Golden Roofing Inc is a corporation after all: its latest profile says no 1099.
INSERT INTO trust_vendor_profiles (pmc_id, vendor_id, gets_1099) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000055', false);

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
    10000.00, '2025-12-01 15:00:00+00', NULL
);

-- Bills: (vendor, reference, amount, set aside at, paid at).
CREATE TEMP TABLE golden_bill AS
SELECT n, vendor_id, reference, amount, set_aside_at, paid_at
FROM (VALUES
    (1, '00000000-0000-4000-8000-000000000051'::uuid, 'EL-1', 1500.00,
     '2026-02-25 15:00:00+00'::timestamptz, '2026-03-01 15:00:00+00'::timestamptz),
    (2, '00000000-0000-4000-8000-000000000051', 'EL-2', 600.00,
     '2026-03-25 15:00:00+00', '2026-04-01 15:00:00+00'),
    (3, '00000000-0000-4000-8000-000000000053', 'PL-1', 2000.00,
     '2026-04-25 15:00:00+00', '2026-05-01 15:00:00+00'),
    (4, '00000000-0000-4000-8000-000000000052', 'LAWN-1', 1999.99,
     '2026-05-25 15:00:00+00', '2026-06-01 15:00:00+00'),
    (5, '00000000-0000-4000-8000-000000000054', 'PAINT-1', 300.00,
     '2026-06-25 15:00:00+00', '2026-07-01 15:00:00+00'),
    (6, '00000000-0000-4000-8000-000000000055', 'ROOF-1', 800.00,
     '2026-07-25 15:00:00+00', '2026-08-01 15:00:00+00')
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
