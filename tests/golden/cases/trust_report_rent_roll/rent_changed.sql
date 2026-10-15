-- A month-to-month rent changed. Unit 101 is leased from Jan 1 at 1500.00; notice given on
-- Jan 30 raises it to 1575.00 from Mar 1. The rent run charges January, February and March.
-- Worked by hand, at the end of Mar 15:
--   monthly rent  1575.00 (in force on Mar 15)
--   charged       1500.00 + 1500.00 + 1575.00 = 4575.00
--   paid          nothing; due 4575.00
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
 '00000000-0000-4000-8000-000000000031', 'Unit 101');

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name) VALUES
('00000000-0000-4000-8000-000000000061', '00000000-0000-4000-8000-000000000001',
 '00000000-0000-4000-8000-000000000031', 'Tenant 1');

SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000041',
    '2026-01-01', NULL, 1500.00, ARRAY['00000000-0000-4000-8000-000000000061']::uuid[]
);

INSERT INTO trust_rent_changes (
    pmc_id, lease_id, effective_on, monthly_rent, notice_given_on, served_by
)
SELECT '00000000-0000-4000-8000-000000000001', l.id, '2026-03-01', 1575.00, '2026-01-30', 'mail'
FROM trust_leases AS l WHERE l.unit_id = '00000000-0000-4000-8000-000000000041';

SELECT trust_charge_rent_due('00000000-0000-4000-8000-000000000001', '2026-01-01');
SELECT trust_charge_rent_due('00000000-0000-4000-8000-000000000001', '2026-02-01');
SELECT trust_charge_rent_due('00000000-0000-4000-8000-000000000001', '2026-03-01');

SELECT * FROM trust_report_rent_roll('00000000-0000-4000-8000-000000000001', '2026-03-15');
