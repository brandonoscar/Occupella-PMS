-- Security deposit register with deposits of each kind (migration 0092), at the end of January
-- 31, 2026. Worked by hand:
--   Tenant A: security deposit 1200.00, pet deposit 300.00 and key deposit 50.00, each on a line
--   of its own, the security deposit first; Tenant B: security deposit 900.00.
--   held = 1200.00 + 50.00 + 300.00 + 900.00 = 2450.00, and the book cash is 2450.00.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000012', '00000000-0000-4000-8000-000000000001',
 'security_deposit', 'Golden security deposit account');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1');

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant A'),
('00000000-0000-4000-8000-000000000062', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant B');

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000012',
    'bank_cash', NULL, NULL, NULL
);
-- Each deposit: (tenant, kind, amount), the pet and key deposits opened before the security one
-- so the register's order is its own, not the order they were opened in.
SELECT count(*) FROM trust_post_transfers(
    '00000000-0000-4000-8000-000000000001',
    'deposits-2026-01',
    ARRAY(
        SELECT (cash.ledger_account_id,
                trust_open_deposit_account(
                    '00000000-0000-4000-8000-000000000001',
                    '00000000-0000-4000-8000-000000000012', d.tenant, d.kind
                ),
                d.amount)::transfer_request
        FROM (VALUES
            (1, '00000000-0000-4000-8000-000000000061'::uuid, 'pet', 300.00),
            (2, '00000000-0000-4000-8000-000000000061', 'key', 50.00),
            (3, '00000000-0000-4000-8000-000000000061', 'security', 1200.00),
            (4, '00000000-0000-4000-8000-000000000062', 'security', 900.00)
        ) AS d (n, tenant, kind, amount)
        CROSS JOIN trust_ledger_accounts AS cash
        WHERE cash.bank_account_id = '00000000-0000-4000-8000-000000000012'
          AND cash.kind = 'bank_cash'
        ORDER BY d.n
    ),
    '2026-01-02 15:00:00+00',
    '{"memo": "Deposits"}'
);

SELECT * FROM trust_report_security_deposits('00000000-0000-4000-8000-000000000001', '2026-01-31');
