-- Vendor insurance at the end of June 30, 2026. Worked by hand:
--   Vendor 1  general liability: GL-1 ran to 2026-06-30, so it expires today; its renewal GL-1R
--             starts 2026-07-01 and is not yet theirs. Workers compensation WC-1 runs through
--             2026-12-31: current, limit 1000000.00.
--   Vendor 2  commercial auto AU-1 ran out on 2026-05-31: expired. General liability GL-2 runs
--             to 2026-07-15: expires in 15 days.
--   Vendor 3  nothing on file. Vendor 4's only policy starts 2026-07-01: nothing on file yet.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_vendors (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000051', '00000000-0000-4000-8000-000000000001', 'Vendor 1'),
('00000000-0000-4000-8000-000000000052', '00000000-0000-4000-8000-000000000001', 'Vendor 2'),
('00000000-0000-4000-8000-000000000053', '00000000-0000-4000-8000-000000000001', 'Vendor 3'),
('00000000-0000-4000-8000-000000000054', '00000000-0000-4000-8000-000000000001', 'Vendor 4');

INSERT INTO trust_vendor_insurance (
    pmc_id, vendor_id, coverage, carrier, policy_number, starts_on, expires_on, limit_amount
) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000051',
 'general liability', 'Carrier A', 'GL-1', '2025-07-01', '2026-06-30', 2000000.00),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000051',
 'general liability', 'Carrier A', 'GL-1R', '2026-07-01', '2027-06-30', 2000000.00),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000051',
 'workers compensation', 'Carrier B', 'WC-1', '2026-01-01', '2026-12-31', 1000000.00),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000052',
 'commercial auto', 'Carrier C', 'AU-1', '2025-01-01', '2026-05-31', NULL),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000052',
 'general liability', 'Carrier A', 'GL-2', '2026-06-01', '2026-07-15', 500000.00),
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000054',
 'general liability', 'Carrier D', 'GL-4', '2026-07-01', '2027-06-30', 1000000.00);

SELECT * FROM trust_report_vendor_insurance('00000000-0000-4000-8000-000000000001', '2026-06-30');
