-- A binary field reads as its size, never as its bytes in hex (docs/edge-cases.md F-122,
-- migration 20261105000900): a lease offer's 5 MB PDF read as 10 million characters in one
-- detail cell. Here an API key's SHA-256, 32 bytes, made by the company's admin. Times are left
-- out, as in one_day.
-- Worked by hand, in the order written:
--   Setup, by the owner role with no staff named: the PMC, Manager 1, and their admin role.
--   Manager 1, through trust_app: an API key named Golden key, its secret's hash
--   "(32 bytes)", never its hex. 4 changes in all.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_staff (id, pmc_id, display_name) VALUES
('00000000-0000-4000-8000-000000000072', '00000000-0000-4000-8000-000000000001', 'Manager 1');

INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000072', 'admin', true);

SET LOCAL ROLE trust_app;

SELECT set_config('trust.staff_id', '00000000-0000-4000-8000-000000000072', true);

INSERT INTO trust_api_keys (pmc_id, name, prefix, secret_sha256, recorded_by) VALUES
('00000000-0000-4000-8000-000000000001', 'Golden key', 'occ_golden01',
 sha256('occ_synthetic-golden-key'::bytea), '00000000-0000-4000-8000-000000000072');

RESET ROLE;

SELECT line, item,
       CASE item WHEN 'period' THEN 'yesterday to tomorrow' ELSE staff END AS staff,
       db_role, action, record, detail
FROM trust_report_audit_log(
    '00000000-0000-4000-8000-000000000001',
    (now() AT TIME ZONE 'UTC')::date - 1,
    (now() AT TIME ZONE 'UTC')::date + 1
);
