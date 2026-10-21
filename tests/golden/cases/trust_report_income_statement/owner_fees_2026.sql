-- Owner 1's income statement for 2026 with fees staff took from the owner (migration 0082).
-- Worked by hand: Owner 1 put 1000.00 into Property 1 (a contribution, not income), and on Mar 5
-- staff took an inspection fee of 75.00, another fee of 25.00 and a lease-up fee of 150.00. The
-- lease-up fee counts with the leasing fees (Schedule E line 8, Commissions) and the other two
-- with the management fees (line 11): expenses 250.00, income 0.00, net income -250.00.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1');

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

SELECT trust_record_owner_contribution(
    '00000000-0000-4000-8000-000000000001', t.ledger_account_id, 'golden-contribution', 1000.00,
    '2026-03-01 12:00:00+00'
)
FROM trust_ledger_accounts AS t
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001' AND t.kind = 'owner_property';

SELECT trust_take_owner_fee(
    t.pmc_id, t.ledger_account_id, f.kind, f.amount, f.memo, f.request_key,
    '2026-03-05 12:00:00+00'
)
FROM trust_ledger_accounts AS t
CROSS JOIN (VALUES
    (1, 'inspection', 75.00, 'Move-in inspection, Unit 1', 'golden-fee-1'),
    (2, 'other', 25.00, 'Key copies', 'golden-fee-2'),
    (3, 'lease_up', 150.00, 'Unit 1 leased', 'golden-fee-3')
) AS f (n, kind, amount, memo, request_key)
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001' AND t.kind = 'owner_property'
ORDER BY f.n;

SELECT * FROM trust_report_income_statement(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000021',
    '2026-01-01', '2026-12-31'
);
