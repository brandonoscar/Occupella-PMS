-- Renters insurance at the end of June 30, 2026. Worked by hand:
--   Unit 1  requires it from 2026-01-01, at least 100000.00 of liability. RI-1 (300000.00) runs
--           through 2026-07-10: expires in 10 days, not below the minimum. Its renewal RI-1R
--           starts 2026-07-11 and is not the tenant's yet.
--   Unit 2  required from 2026-06-01 with no minimum; RI-2 ran out 2026-05-31: expired.
--   Unit 3  required, at least 500000.00; RI-3 runs to 2027-01-31 with 300000.00: current, below
--           the minimum.
--   Unit 4  not required; nothing on file. Unit 5's lease ended 2026-05-31: not listed.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_properties (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000031', '00000000-0000-4000-8000-000000000001', 'Property 1');

INSERT INTO trust_units (id, pmc_id, property_id, display_name)
SELECT ('00000000-0000-4000-8000-00000000004' || n)::uuid, '00000000-0000-4000-8000-000000000001',
       '00000000-0000-4000-8000-000000000031', 'Unit ' || n
FROM generate_series(1, 5) AS n;

INSERT INTO trust_tenants (id, pmc_id, property_id, display_name)
SELECT ('00000000-0000-4000-8000-00000000006' || n)::uuid, '00000000-0000-4000-8000-000000000001',
       '00000000-0000-4000-8000-000000000031', 'Tenant ' || n
FROM generate_series(1, 5) AS n;

SELECT trust_open_lease(
    '00000000-0000-4000-8000-000000000001', ('00000000-0000-4000-8000-00000000004' || n)::uuid,
    '2026-01-01', CASE WHEN n = 5 THEN '2026-05-31'::date END, 1500.00,
    ARRAY[('00000000-0000-4000-8000-00000000006' || n)::uuid]
)
FROM generate_series(1, 5) AS n;

-- The leases by unit number, for the rows below.
CREATE TEMP VIEW golden_lease AS
SELECT right(u.display_name, 1)::integer AS n, l.id
FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id
WHERE l.pmc_id = '00000000-0000-4000-8000-000000000001';

INSERT INTO trust_renters_insurance_terms (pmc_id, lease_id, required, minimum_liability, starts_on)
SELECT '00000000-0000-4000-8000-000000000001', l.id, t.required, t.minimum, t.starts_on
FROM golden_lease AS l
JOIN (VALUES (1, true, 100000.00, '2026-01-01'::date),
             (2, false, NULL, '2026-01-01'::date),
             (2, true, NULL, '2026-06-01'::date),
             (3, true, 500000.00, '2026-01-01'::date),
             (4, false, NULL, '2026-01-01'::date)) AS t (n, required, minimum, starts_on)
  ON t.n = l.n;

INSERT INTO trust_renters_insurance (
    pmc_id, lease_id, carrier, policy_number, starts_on, expires_on, liability_limit
)
SELECT '00000000-0000-4000-8000-000000000001', l.id, p.carrier, p.number, p.starts_on,
       p.expires_on, p.liability
FROM golden_lease AS l
JOIN (VALUES (1, 'Carrier A', 'RI-1', '2025-07-11'::date, '2026-07-10'::date, 300000.00),
             (1, 'Carrier A', 'RI-1R', '2026-07-11'::date, '2027-07-10'::date, 300000.00),
             (2, 'Carrier B', 'RI-2', '2025-06-01'::date, '2026-05-31'::date, 100000.00),
             (3, 'Carrier C', 'RI-3', '2026-02-01'::date, '2027-01-31'::date, 300000.00))
  AS p (n, carrier, number, starts_on, expires_on, liability)
  ON p.n = l.n;

SELECT * FROM trust_report_renters_insurance('00000000-0000-4000-8000-000000000001', '2026-06-30');
