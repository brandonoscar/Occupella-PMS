-- A company's chart of accounts (migrations 20261106000205 to 20261106000230, and the second
-- pass's lines of 20261106000243). Worked by hand: the fixed lines keep their starting codes
-- (1000 trust bank cash; 2000 to 2400 what the trust holds, with 2150 owner-held deposits, a
-- memo, and 2250 unapplied cash; 4000 to 4400 the income statement's own income lines, with 4010
-- concessions, a contra to rent; 5000 bills in no account, 5800 leasing fees, 6100 management
-- fees, 6900 deposit interest) but rent, which staff numbered 4001. Three charge types take 4501, 4502 and 4503 as they are added: Application fee (the
-- owner's), Admin fee (the company keeps it) and Key copy, renamed Key replacement and then
-- archived. Expense accounts take the next code of their Schedule E line's block: Plumbing and
-- Electrical on line 14 take 6401 and 6402, Water on line 17 takes 6701, Ads on line 5 takes
-- 5501 and is renamed Advertising, and Electrical is archived. Staff give Water 6750. Every
-- row is by section (Assets, Liabilities, Income, Expenses), then by code.
INSERT INTO trust_pmcs (pmc_id, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'Golden PMC');

INSERT INTO trust_charge_types (id, pmc_id, name) VALUES
('00000000-0000-4000-8000-000000000081', '00000000-0000-4000-8000-000000000001',
 'Application fee');
INSERT INTO trust_charge_types (id, pmc_id, name) VALUES
('00000000-0000-4000-8000-000000000082', '00000000-0000-4000-8000-000000000001', 'Admin fee');
INSERT INTO trust_charge_types (id, pmc_id, name) VALUES
('00000000-0000-4000-8000-000000000083', '00000000-0000-4000-8000-000000000001', 'Key copy');

INSERT INTO trust_charge_type_payees (pmc_id, charge_type_id, payee) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000082', 'company');
INSERT INTO trust_charge_type_names (pmc_id, charge_type_id, name) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000083',
 'Key replacement');
INSERT INTO trust_charge_type_archivals (pmc_id, charge_type_id, archived) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000083', true);

INSERT INTO trust_expense_accounts (id, pmc_id, name, schedule_e_line) VALUES
('00000000-0000-4000-8000-000000000071', '00000000-0000-4000-8000-000000000001', 'Plumbing',
 14);
INSERT INTO trust_expense_accounts (id, pmc_id, name, schedule_e_line) VALUES
('00000000-0000-4000-8000-000000000072', '00000000-0000-4000-8000-000000000001', 'Electrical',
 14);
INSERT INTO trust_expense_accounts (id, pmc_id, name, schedule_e_line) VALUES
('00000000-0000-4000-8000-000000000073', '00000000-0000-4000-8000-000000000001', 'Water', 17);
INSERT INTO trust_expense_accounts (id, pmc_id, name, schedule_e_line) VALUES
('00000000-0000-4000-8000-000000000074', '00000000-0000-4000-8000-000000000001', 'Ads', 5);

INSERT INTO trust_expense_account_names (pmc_id, expense_account_id, name) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000074',
 'Advertising');
INSERT INTO trust_expense_account_archivals (pmc_id, expense_account_id, archived) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000072', true);

INSERT INTO trust_account_codes (pmc_id, line, code) VALUES
('00000000-0000-4000-8000-000000000001', 'rent', '4001');
INSERT INTO trust_account_codes (pmc_id, expense_account_id, code) VALUES
('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000073', '6750');

SELECT * FROM trust_report_chart_of_accounts('00000000-0000-4000-8000-000000000001');
