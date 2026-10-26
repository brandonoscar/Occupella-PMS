-- A tenant's check of 1600.00 on Jan 3, 2026 against January's rent of 1500.00 (ADR 0010 phase 4,
-- receipts): 1500.00 pays the rent, the 100.00 left is held as Tenant 1's prepaid rent. The
-- company has a mailing address, so the receipt says who received it and where. Worked by hand:
--   Amount 1600.00 = Rent, due 01/01/2026: 1500.00 + Held as prepaid rent: 100.00
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_company_profiles (pmc_id, address_line_1, city, state, postal_code)
VALUES ('00000000-0000-4000-8000-000000000001', '1 Example Way', 'Sampletown', 'ZZ', '00000');

INSERT INTO trust_bank_accounts (id, pmc_id, kind, display_name) VALUES
('00000000-0000-4000-8000-000000000011', '00000000-0000-4000-8000-000000000001', 'operating',
 'Golden operating trust account');

INSERT INTO trust_owners (id, pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000021', '00000000-0000-4000-8000-000000000001', 'Owner 1');

INSERT INTO trust_properties (id, pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1');

INSERT INTO trust_units (id, pmc_id, property_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000041', '00000000-0000-4000-8000-000000000001',
        '00000000-0000-4000-8000-000000000031', 'Unit 1');

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 1'),
('00000000-0000-4000-8000-000000000062', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 2');

SELECT trust_open_ledger_account(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000011',
    a.kind, a.owner, a.property, NULL
)
FROM (VALUES
    (1, 'bank_cash', NULL::uuid, NULL::uuid),
    (2, 'owner_property', '00000000-0000-4000-8000-000000000021'::uuid,
     '00000000-0000-4000-8000-000000000031'::uuid)
) AS a (n, kind, owner, property)
ORDER BY a.n;

SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000041',
    '2026-01-01', NULL, 1500.00,
    ARRAY['00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000062']::uuid[]
);

INSERT INTO trust_charges (id, pmc_id, lease_id, due_on, kind, amount, memo)
SELECT '00000000-0000-4000-8000-000000000101', l.pmc_id, l.id, '2026-01-01', 'rent', 1500.00,
       'January rent'
FROM trust_leases AS l
WHERE l.unit_id = '00000000-0000-4000-8000-000000000041'
