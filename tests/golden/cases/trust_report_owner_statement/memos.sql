-- Owner 1's statement for January 2026 with the second pass's lines (migrations 20261106000240 to
-- 20261106000243). Worked by hand: 500.00 came in on January 5 that no one could match, held as
-- unapplied cash; on January 9 it was applied to Unit 1's lease, a payment into Property 1 from
-- the unapplied cash account, named so ("unapplied_cash: received, not yet applied"). Opening
-- 0.00, closing 500.00. Then, after the closing balance, memo lines that change no balance: the
-- 150.00 concession given on January 1 (a 30.00 credit not marked one is not counted), and Tenant
-- 1's security deposit of 1200.00 that the owner holds, outside trust, since December 20. Tenant
-- 1's key deposit of 50.00 the owner held was returned on January 20, before the period's end, so
-- it is not listed.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account');

INSERT INTO trust_owners (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1');

INSERT INTO trust_units (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Unit 1');

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 1');

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011', a.kind,
    a.owner, a.property
)
FROM (VALUES
    (1, 'bank_cash', NULL::uuid, NULL::uuid),
    (2, 'owner_property', '00000000-0000-4000-8000-000000000021'::uuid,
     '00000000-0000-4000-8000-000000000031'::uuid),
    (3, 'unapplied_cash', NULL, NULL)
) AS a (n, kind, owner, property)
ORDER BY a.n;

SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000041',
    '2025-12-01', '2026-01-31', 1200.00, ARRAY['00000000-0000-4000-8000-000000000061'::uuid]
);

CREATE TEMP VIEW golden_account AS
SELECT t.ledger_account_id AS id, t.kind AS name
FROM trust_ledger_accounts AS t
WHERE t.pmc_id = '00000000-0000-4000-8000-000000000001';

-- Received, then applied to the lease.
SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'bank_cash'),
    (SELECT id FROM golden_account WHERE name = 'unapplied_cash'),
    500.00, '2026-01-05 00:00:00+00', '{"memo": "Unapplied cash from J. Sample"}'
);

SELECT pgledger_create_transfer(
    (SELECT id FROM golden_account WHERE name = 'unapplied_cash'),
    (SELECT id FROM golden_account WHERE name = 'owner_property'),
    500.00, '2026-01-09 00:00:00+00', '{"memo": "Rent payment"}'
);

INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, concession)
SELECT '00000000-0000-4000-8000-000000000001', l.id, c.due_on, c.kind, c.amount, c.memo,
       c.concession
FROM trust_leases AS l
CROSS JOIN (VALUES
    (1, '2026-01-01'::date, 'rent', 1200.00, 'Rent', false),
    (2, '2026-01-01', 'credit', 150.00, 'Move-in special', true),
    (3, '2026-01-15', 'credit', 30.00, 'Goodwill', false)
) AS c (n, due_on, kind, amount, memo, concession)
WHERE l.unit_id = '00000000-0000-4000-8000-000000000041'
ORDER BY c.n;

INSERT INTO trust_owner_held_deposits (pmc_id, lease_id, tenant_id, deposit_kind, amount,
                                       held_since)
SELECT '00000000-0000-4000-8000-000000000001', l.id, '00000000-0000-4000-8000-000000000061',
       d.kind, d.amount, '2025-12-20'
FROM trust_leases AS l
CROSS JOIN (VALUES (1, 'security', 1200.00), (2, 'key', 50.00)) AS d (n, kind, amount)
WHERE l.unit_id = '00000000-0000-4000-8000-000000000041'
ORDER BY d.n;

INSERT INTO trust_owner_held_deposit_outcomes (held_id, pmc_id, returned, kept, on_day)
SELECT h.id, h.pmc_id, 50.00, 0.00, '2026-01-20'
FROM trust_owner_held_deposits AS h
WHERE h.pmc_id = '00000000-0000-4000-8000-000000000001' AND h.deposit_kind = 'key';

SELECT * FROM trust_report_owner_statement(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000021',
    '2026-01-01 00:00:00+00', '2026-02-01 00:00:00+00'
);
