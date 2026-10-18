"""The exact privileges of the app role, and of a role nobody granted anything.

Money can only move through the ledger functions, so trust_app's grants are a money guard. This
pins them down completely: any grant added, dropped or widened by a migration fails here.
`pms_nobody` stands in for any other role on the server (PUBLIC), such as a Supabase `anon`.

trust_ai_agent, the role behind the API key Occupella will hold, reads what trust_app reads and
runs the reports: it can't write a row or call a function that writes (the AI never moves money).
The one exception is AI_NEVER_READS: who can sign in, sessions and credentials, rental
applicants with their screenings, and the files of documents. Their copies in the audit log are
hidden from it too, by the log's row-level security.

Every table, view and function in the schema is pinned, not just the ones that exist today:
test_every_relation_and_function_is_pinned fails on a new one until it is listed here, so no
migration adds one without deciding who may use it. That matters most for functions, because
Postgres lets PUBLIC call a new function unless the migration revokes it.
"""

import pytest
from helpers import NOT_FROM_AN_EXTENSION

TABLES = {
    "pgledger_accounts": {"SELECT"},
    "pgledger_transfers": {"SELECT"},
    "pgledger_entries": {"SELECT"},
    "pgledger_accounts_view": {"SELECT"},
    "pgledger_transfers_view": {"SELECT"},
    "pgledger_entries_view": {"SELECT"},
    "trust_pmcs": {"SELECT", "INSERT"},
    "trust_bank_accounts": {"SELECT", "INSERT"},
    "trust_owners": {"SELECT", "INSERT"},
    "trust_properties": {"SELECT", "INSERT"},
    "trust_tenants": {"SELECT", "INSERT"},
    "trust_vendors": {"SELECT", "INSERT"},
    "trust_ledger_accounts": {"SELECT"},
    "trust_idempotency_keys": set(),  # only trust_post_transfers reads and writes it
    "trust_reconciliations": {"SELECT"},  # written only by trust_approve_reconciliation
    "trust_units": {"SELECT", "INSERT"},
    "trust_leases": {"SELECT"},  # written only by trust_open_lease and trust_end_lease
    "trust_lease_tenants": {"SELECT"},  # written only by trust_open_lease
    "trust_charges": {"SELECT", "INSERT"},
    "trust_charge_waivers": {"SELECT", "INSERT"},
    "trust_charge_payments": {"SELECT"},  # written only by trust_apply_payment
    "trust_payment_reversals": {"SELECT"},  # written only by trust_reverse_payment
    "trust_management_agreements": {"SELECT", "INSERT"},
    "trust_management_fees": {"SELECT"},  # written only by trust_post_management_fee
    "trust_leasing_fees": {"SELECT"},  # written only by trust_post_leasing_fee
    "trust_owner_draws": {"SELECT"},  # written only by trust_draw_owner
    "trust_pmc_fee_withdrawals": {"SELECT"},  # written only by trust_withdraw_pmc_fees
    "trust_bills": {"SELECT", "INSERT"},
    "trust_bill_approvals": {"SELECT", "INSERT"},
    "trust_bill_declines": {"SELECT", "INSERT"},
    "trust_emails": {"SELECT", "INSERT"},
    "trust_email_deliveries": {"SELECT", "INSERT"},
    "trust_email_settings": {"SELECT", "INSERT"},
    "trust_bill_payments": {"SELECT"},  # written only by trust_set_aside_bill, trust_pay_bill
    "trust_work_orders": {"SELECT", "INSERT"},
    "trust_work_order_steps": {"SELECT", "INSERT"},
    "trust_work_order_details": {"SELECT", "INSERT"},
    "trust_late_fee_policies": {"SELECT", "INSERT"},
    "trust_late_fees": {"SELECT"},  # written only by trust_assess_late_fees
    "trust_nsf_fee_policies": {"SELECT", "INSERT"},
    "trust_nsf_fees": {"SELECT"},  # written only by trust_charge_nsf_fee
    "trust_opening_balances": {"SELECT"},  # written only by trust_post_opening_balances
    "trust_staff": {"SELECT", "INSERT"},
    "trust_audit_log": {"SELECT"},  # written only by the trust_audit trigger
    "trust_audit_log_id_seq": set(),  # the log's ids; only the trigger draws them
    "trust_payment_holds": {"SELECT", "INSERT"},
    "trust_payment_hold_releases": {"SELECT", "INSERT"},
    "trust_payment_hold_allowances": {"SELECT", "INSERT"},
    "trust_owner_contributions": {"SELECT"},  # written only by trust_record_owner_contribution
    "trust_recurring_bills": {"SELECT", "INSERT"},
    "trust_recurring_bill_ends": {"SELECT", "INSERT"},
    "trust_recurring_charges": {"SELECT", "INSERT"},
    "trust_recurring_charge_ends": {"SELECT", "INSERT"},
    "trust_vendor_payments_carried_over": {"SELECT", "INSERT"},
    # Logins (the API writes them; UPDATE only on the columns in COLUMN_UPDATES).
    "trust_logins": {"SELECT", "INSERT"},
    "trust_invites": {"SELECT", "INSERT"},
    "trust_sessions": {"SELECT", "INSERT"},
    "trust_login_secrets": {"SELECT", "INSERT"},
    # Maintenance requests: tenants file them through the API, staff link a work order.
    "trust_maintenance_requests": {"SELECT", "INSERT"},
    "trust_maintenance_request_work_orders": {"SELECT", "INSERT"},
    # Rental applications: applicants apply through the API, staff screen and decide.
    "trust_application_links": {"SELECT", "INSERT"},
    "trust_application_link_closings": {"SELECT", "INSERT"},
    "trust_listings": {"SELECT", "INSERT"},
    # Expense accounts (migration 0052): staff add accounts and put bills in them; a bill's
    # latest row is its account, so a correction is an insert too. The ids order the rows.
    "trust_expense_accounts": {"SELECT", "INSERT"},
    "trust_bill_expense_accounts": {"SELECT", "INSERT"},
    "trust_bill_expense_accounts_id_seq": set(),
    "trust_bill_splits": {"SELECT", "INSERT"},
    "trust_bill_splits_id_seq": set(),
    "trust_recurring_bill_expense_accounts": {"SELECT", "INSERT"},
    "trust_recurring_bill_expense_accounts_id_seq": set(),
    # Journal entries (migration 0053): written only by their three functions, so a posting is
    # always proposed by one staff member and posted by another.
    "trust_journal_entries": {"SELECT"},
    "trust_journal_entry_lines": {"SELECT"},
    "trust_journal_entry_decisions": {"SELECT"},
    # Budgets (migration 0055): staff enter a month's budget; a change is a new row, so the
    # latest row is the budget and nothing is updated. The ids order the rows.
    "trust_budgets": {"SELECT", "INSERT"},
    "trust_budgets_id_seq": set(),
    # Custom roles (migration 0057): an admin's inserts; the latest row of each holds, so a
    # change is an insert too. The ids order the rows.
    "trust_custom_roles": {"SELECT", "INSERT"},
    "trust_custom_role_areas": {"SELECT", "INSERT"},
    "trust_custom_role_areas_id_seq": set(),
    "trust_staff_custom_roles": {"SELECT", "INSERT"},
    "trust_staff_custom_roles_id_seq": set(),
    # Vendor insurance and inspections (migration 0058): staff record them; a renewal or a
    # redone inspection is a new row.
    "trust_vendor_insurance": {"SELECT", "INSERT"},
    "trust_vendor_profiles": {"SELECT", "INSERT"},
    "trust_account_signers": {"SELECT", "INSERT"},
    "trust_rent_changes": {"SELECT", "INSERT"},
    "trust_renters_insurance_terms": {"SELECT", "INSERT"},
    "trust_renters_insurance": {"SELECT", "INSERT"},
    "trust_archivals": {"SELECT", "INSERT"},
    "trust_contacts": {"SELECT", "INSERT"},
    "trust_company_profiles": {"SELECT", "INSERT"},
    # Property groups (migration 0065): a group is renamed (UPDATE of display_name only), a
    # property joins or leaves it by an insert.
    "trust_property_groups": {"SELECT", "INSERT"},
    "trust_property_group_members": {"SELECT", "INSERT"},
    # Notes (migration 0066): written once each; a correction is a new note.
    "trust_notes": {"SELECT", "INSERT"},
    # Rent runs (migration 0067): recorded by trust_run_rent, which runs with the app's grants.
    "trust_rent_runs": {"SELECT", "INSERT"},
    # How a payout was sent (migration 0068): recorded once each, never changed.
    "trust_payout_methods": {"SELECT", "INSERT"},
    "trust_inspections": {"SELECT", "INSERT"},
    "trust_inspection_items": {"SELECT", "INSERT"},
    "trust_listing_removals": {"SELECT", "INSERT"},
    "trust_lease_end_adjustments": {"SELECT"},  # written only by trust_end_lease
    "trust_applications": {"SELECT", "INSERT"},
    "trust_applicants": {"SELECT", "INSERT"},
    "trust_screenings": {"SELECT", "INSERT"},
    "trust_application_decisions": {"SELECT", "INSERT"},
    "trust_adverse_actions": {"SELECT", "INSERT"},
    "trust_application_withdrawals": {"SELECT", "INSERT"},
    "trust_lease_offer_charges": {"SELECT", "INSERT"},
    "trust_lease_offer_voids": {"SELECT", "INSERT"},
    # Lease offers: staff offer and send them, the partner's signatures are recorded.
    "trust_lease_offers": {"SELECT", "INSERT"},
    "trust_lease_offer_signers": {"SELECT", "INSERT"},
    "trust_signature_requests": {"SELECT", "INSERT"},
    "trust_signatures": {"SELECT", "INSERT"},
    "trust_lease_offer_completions": {"SELECT"},  # written only by trust_complete_lease_offer
    # Bank statements: staff import them; matches are written only by their functions.
    "trust_bank_statements": {"SELECT", "INSERT"},
    "trust_bank_statement_lines": {"SELECT", "INSERT"},
    "trust_bank_matches": {"SELECT"},
    "trust_bank_match_lines": {"SELECT"},
    "trust_bank_match_transfers": {"SELECT"},
    "trust_bank_deposits": {"SELECT", "INSERT"},
    "trust_bank_deposit_items": {"SELECT", "INSERT"},
    "trust_bank_unmatches": {"SELECT"},
    # The switching tool: items carried over and the sign-off; their matches by function only.
    "trust_cutover_items": {"SELECT", "INSERT"},
    "trust_cutover_signoffs": {"SELECT", "INSERT"},
    "trust_bank_match_cutover_items": {"SELECT"},
    # Staff roles: an admin changes them with inserts (the trigger refuses anyone else);
    # month-end preparations are written only by trust_prepare_bank_reconciliation.
    "trust_staff_roles": {"SELECT", "INSERT"},
    "trust_reconciliation_preparations": {"SELECT"},
    # Deposits returned at move-out: written only by trust_return_deposit.
    "trust_deposit_returns": {"SELECT"},
    "trust_deposit_return_lines": {"SELECT"},
    # Documents: staff upload and remove them with inserts; the triggers keep them whole.
    "trust_documents": {"SELECT", "INSERT"},
    "trust_document_files": {"SELECT", "INSERT"},
    "trust_document_removals": {"SELECT", "INSERT"},
    # A tenant leaving a lease: written only by trust_remove_lease_tenant. Who else lives there,
    # guarantors and lease-break fee terms: staff add them with inserts; a removal is a row.
    "trust_lease_departures": {"SELECT"},
    "trust_lease_occupants": {"SELECT", "INSERT"},
    "trust_lease_pets": {"SELECT", "INSERT"},
    "trust_lease_vehicles": {"SELECT", "INSERT"},
    "trust_lease_guarantors": {"SELECT", "INSERT"},
    "trust_lease_household_removals": {"SELECT", "INSERT"},
    "trust_lease_break_fees": {"SELECT", "INSERT"},
    # A lease's rent due day from a month: staff record it with an insert (migration 0081).
    "trust_rent_due_days": {"SELECT", "INSERT"},
    "trust_management_fee_settings": {"SELECT", "INSERT"},
    "trust_owner_fees": {"SELECT"},  # written only by trust_take_owner_fee
    # One invoice shared across properties: staff enter it and its parts with inserts, and a
    # markup on a bill too; the markup taken is written only by trust_set_aside_bill
    # (migration 0083).
    "trust_shared_bills": {"SELECT", "INSERT"},
    "trust_shared_bill_parts": {"SELECT", "INSERT"},
    "trust_bill_markups": {"SELECT", "INSERT"},
    "trust_bill_markup_fees": {"SELECT"},
    "trust_on_call_staff": {"SELECT", "INSERT"},
    "trust_recurring_work_orders": {"SELECT", "INSERT"},
    "trust_recurring_work_order_ends": {"SELECT", "INSERT"},
    "schema_migrations": set(),  # dbmate's record of applied migrations
}
# Tables trust_app updates column by column, and the only columns it may update.
COLUMN_UPDATES = {
    "trust_logins": {"disabled_at"},
    "trust_invites": {"accepted_at"},
    "trust_sessions": {"ended_at"},
    "trust_login_secrets": {
        "password_hash",
        "totp_secret",
        "totp_confirmed_at",
        "totp_last_step",
        "failed_sign_ins",
        "locked_until",
        "updated_at",
    },
}
# trust_app reads these, and trust_ai_agent never does: who can sign in, their sessions and
# their credentials, and rental applicants with their consumer report results, are no business
# of the role behind Occupella's key.
AI_NEVER_READS = {
    "trust_logins",
    "trust_invites",
    "trust_sessions",
    "trust_login_secrets",
    "trust_application_links",
    "trust_application_link_closings",
    "trust_applications",
    "trust_applicants",
    "trust_screenings",
    "trust_application_decisions",
    "trust_adverse_actions",
    "trust_application_withdrawals",
    "trust_lease_offers",
    "trust_lease_offer_charges",
    "trust_lease_offer_voids",
    "trust_lease_offer_signers",
    "trust_signature_requests",
    "trust_signatures",
    "trust_lease_offer_completions",
    # A document's bytes (a W-9 carries a tax ID): the AI may know it exists, never read it.
    "trust_document_files",
    # Emails to owners and their deliveries: people's addresses (ADR 0004).
    "trust_emails",
    "trust_email_deliveries",
    # Tenants', owners' and vendors' emails, phones and mailing addresses (ADR 0005).
    "trust_contacts",
    # What staff write about people and their homes (ADR 0006).
    "trust_notes",
    # The company's mailing address, with the fee terms new properties start with (ADR 0006).
    "trust_company_profiles",
    # Who else lives in a home, their pets (an assistance animal speaks to a disability) and
    # vehicles, and the lease's guarantors with their contact details (migration 0080).
    "trust_lease_occupants",
    "trust_lease_pets",
    "trust_lease_vehicles",
    "trust_lease_guarantors",
    "trust_lease_household_removals",
}
PRIVILEGES = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]
RENAMEABLE = [
    "trust_pmcs",
    "trust_bank_accounts",
    "trust_owners",
    "trust_properties",
    "trust_tenants",
    "trust_vendors",
    "trust_units",
    "trust_staff",
    "trust_property_groups",
]

# Who may call each function, besides the role that owns it.
FUNCTIONS = {
    # The write paths: the app role and nobody else.
    "trust_open_ledger_account(uuid,uuid,text,uuid,uuid,uuid)": {"trust_app"},
    "trust_post_transfers(uuid,text,transfer_request[],timestamptz,jsonb)": {"trust_app"},
    "trust_open_vendor_account(uuid,uuid,uuid)": {"trust_app"},
    "trust_open_lease(uuid,uuid,date,date,numeric,uuid[])": {"trust_app"},
    "trust_end_lease(uuid,uuid,date)": {"trust_app"},
    "trust_apply_payment(uuid,uuid,text,numeric)": {"trust_app"},
    "trust_reverse_payment(uuid,uuid,text,text,numeric)": {"trust_app"},
    "trust_post_management_fee(uuid,text,date,date,timestamptz)": {"trust_app"},
    "trust_post_leasing_fee(uuid,uuid,text,timestamptz)": {"trust_app"},
    "trust_draw_owner(uuid,text,text,numeric,timestamptz)": {"trust_app"},
    "trust_withdraw_pmc_fees(uuid,uuid,text,numeric,timestamptz)": {"trust_app"},
    "trust_take_owner_fee(uuid,text,text,numeric,text,text,timestamptz)": {"trust_app"},
    "trust_take_monthly_management_fees(uuid,date)": {"trust_app"},
    "trust_set_aside_bill(uuid,uuid,timestamptz)": {"trust_app"},
    "trust_pay_bill(uuid,uuid,timestamptz)": {"trust_app"},
    "trust_charge_rent_due(uuid,date)": {"trust_app"},
    "trust_rent_for_month(date,date,numeric,date)": {"trust_app"},
    "trust_rent_on(uuid,date)": {"trust_app"},
    "trust_open_charges(uuid,uuid,timestamptz)": {"trust_app"},
    "trust_charge_recurring_due(uuid,date)": {"trust_app"},
    "trust_charge_rent_on_due_days(uuid,date,date)": {"trust_app"},
    "trust_charge_recurring_on_due_days(uuid,date,date)": {"trust_app"},
    "trust_rent_due_day(uuid,date)": {"trust_app"},
    "trust_rent_due_on(uuid,date)": {"trust_app"},
    "trust_assess_late_fees(uuid,date)": {"trust_app"},
    "trust_charge_nsf_fee(uuid,text)": {"trust_app"},
    "trust_post_opening_balances(uuid,uuid,date,jsonb,numeric,text)": {"trust_app"},
    "trust_record_owner_contribution(uuid,text,text,numeric,timestamptz,text)": {"trust_app"},
    "trust_complete_lease_offer(uuid,uuid,uuid)": {"trust_app"},
    "trust_enter_recurring_bills(uuid,date)": {"trust_app"},
    "trust_open_recurring_work_orders(uuid,date)": {"trust_app"},
    "trust_match_bank_lines(uuid,uuid,uuid[],text[],uuid[],uuid)": {"trust_app"},
    "trust_match_cutover_opening(uuid,uuid,uuid,uuid)": {"trust_app"},
    "trust_unmatch_bank_lines(uuid,uuid,uuid)": {"trust_app"},
    "trust_prepare_bank_reconciliation(uuid,uuid,date)": {"trust_app"},
    "trust_approve_bank_reconciliation(uuid,uuid)": {"trust_app"},
    "trust_return_deposit(uuid,uuid,text,text,timestamptz)": {"trust_app"},
    "trust_remove_lease_tenant(uuid,uuid,uuid,date,uuid)": {"trust_app"},
    "trust_propose_journal_entry(uuid,date,text,transfer_request[])": {"trust_app"},
    "trust_post_journal_entry(uuid,uuid)": {"trust_app"},
    "trust_decline_journal_entry(uuid,uuid,text)": {"trust_app"},
    # What a lease owes, as the deposit's return counts it: the move-out page reads it.
    "trust_lease_owed(uuid)": {"trust_app"},
    # The app approves only month-end preparations, which another staff member made: this one
    # is the owner's, called by trust_approve_bank_reconciliation.
    "trust_approve_reconciliation(uuid,uuid,timestamptz,timestamptz,numeric,text,text)": set(),
    # Whether a staff member holds a role, and the refusal when they don't: the app's, as the
    # role triggers run with its rights when it writes. They read and write nothing else.
    "trust_staff_holds(uuid,uuid,text)": {"trust_app"},
    "trust_require_role(uuid,uuid,text,text)": {"trust_app"},
    # Whether a staff member's custom roles let them change an area: the app asks on every write.
    "trust_staff_may(uuid,uuid,text)": {"trust_app"},
    # Who signs a trust bank account: the app's, as the signer trigger runs with its rights when
    # it records a change. The refusal of a payout runs only inside the owner's write paths.
    "trust_signer_of(uuid,uuid)": {"trust_app"},
    "trust_broker_of(uuid)": {"trust_app"},
    "trust_require_signer(uuid,date)": set(),
    # Helpers the fee, draw and bill functions call as the owner: nobody else.
    "trust_lock_owner_account(uuid,text)": set(),
    "trust_agreement_on(text,date)": set(),
    "trust_post_fee(trust_ledger_accounts,numeric,timestamptz,text)": set(),
    "trust_lock_bill(uuid,uuid)": set(),
    "trust_lock_journal_entry(uuid,uuid)": set(),
    "trust_unpaid_bills(text,date)": set(),
    # Helpers the bank matching functions call as the owner: nobody else.
    "trust_bank_cash_moved(uuid,text)": set(),
    "trust_lock_bank_matches(uuid,uuid)": set(),
    "trust_bank_match_closed(timestamptz,uuid[],text[],uuid[])": set(),
    # Records every write as the owner: no role may call it, or write the log, on its own.
    "trust_audit()": set(),
    # Checks a payment hold as the owner, to take its lease's lock: no role calls it on its own.
    "trust_check_payment_hold()": set(),
    "trust_check_bill_decision()": set(),
    "trust_check_bank_deposit_item()": set(),  # a trigger's: no one calls it
    "trust_check_charge_waiver()": set(),
    "trust_check_rent_due_day()": set(),
    "trust_check_bill_reference()": set(),  # a trigger's: no one calls it
    "trust_check_shared_bill()": set(),
    "trust_check_work_order_charge()": set(),
    "trust_fill_bill_markup()": set(),
    # Reports read trust records, so only the app and the AI; they run with the caller's own
    # read grants.
    "trust_report_three_way_reconciliation(uuid,uuid)": {"trust_app", "trust_ai_agent"},
    "trust_report_owner_statement(uuid,uuid,timestamptz,timestamptz)": {
        "trust_app",
        "trust_ai_agent",
    },
    "trust_report_rent_roll(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_owner_balances(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_unpaid_bills(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_delinquency(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_tenant_ledger(uuid,uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_trial_balance(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_security_deposits(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_general_ledger(uuid,date,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_audit_log(uuid,date,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_lease_expirations(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_vendor_payments(uuid,integer)": {"trust_app", "trust_ai_agent"},
    "trust_report_bank_reconciliation(uuid,uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_cutover(uuid,uuid)": {"trust_app", "trust_ai_agent"},
    "trust_report_deposit_return(uuid,uuid)": {"trust_app", "trust_ai_agent"},
    "trust_report_owner_year_end(uuid,uuid,integer)": {"trust_app", "trust_ai_agent"},
    "trust_report_income_statement(uuid,uuid,date,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_balance_sheet(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_budget_vs_actual(uuid,uuid,date,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_vendor_insurance(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_trust_account_signers(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_renters_insurance(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_is_archived(uuid,uuid,uuid,uuid)": {"trust_app"},
    "trust_group_properties(uuid)": {"trust_app"},
    "trust_run_rent(uuid,date,date)": {"trust_app"},
    "trust_report_inspection(uuid,uuid)": {"trust_app", "trust_ai_agent"},
    "trust_report_cash_flow(uuid,date,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_vacancy(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_work_orders(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_tenant_directory(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_owner_directory(uuid,date)": {"trust_app", "trust_ai_agent"},
    "trust_report_vendor_directory(uuid,date)": {"trust_app", "trust_ai_agent"},
    # pgledger's posting functions take no idempotency key, so a retried request could post
    # twice: only the owner calls them (trust_post_transfers does, as the owner).
    "pgledger_create_transfer(text,text,numeric,timestamptz,jsonb)": set(),
    "pgledger_create_transfers(transfer_request[])": set(),
    "pgledger_create_transfers(transfer_request[],timestamptz,jsonb)": set(),
    # Opens a bare pgledger account with no trust kind; only trust_open_ledger_account may.
    "pgledger_create_account(text,text,boolean,boolean,jsonb)": set(),
    # Id helpers and checks that read nothing they are not handed: callable by anyone.
    "parse_ulid(text)": {"PUBLIC"},
    "format_ulid(bytea)": {"PUBLIC"},
    "ulid_to_uuid(text)": {"PUBLIC"},
    "uuid_to_ulid(uuid)": {"PUBLIC"},
    "pgledger_uuidv7()": {"PUBLIC"},
    "pgledger_uuidv7_exists()": {"PUBLIC"},
    "pgledger_uuidv7_microsecond()": {"PUBLIC"},
    "pgledger_generate_id(text)": {"PUBLIC"},
    "pgledger_check_account_balance_constraints(pgledger_accounts)": {"PUBLIC"},
    # Trigger functions: Postgres refuses to call one outside its trigger.
    "trust_refuse_ledger_rewrite()": {"PUBLIC"},
    "trust_refuse_negative_balance()": {"PUBLIC"},
    "trust_check_transfer_scope()": {"PUBLIC"},
    "trust_check_bank_tie_out()": {"PUBLIC"},
    "trust_refuse_posting_into_closed_period()": {"PUBLIC"},
    "trust_refuse_moving_closed_through()": {"PUBLIC"},
    "trust_check_account_bank_kind()": {"PUBLIC"},
    "trust_refuse_changing_bank_kind()": {"PUBLIC"},
    "trust_refuse_overlapping_leases()": {"PUBLIC"},
    "trust_check_agreement_account()": {"PUBLIC"},
    "trust_check_bill_account()": {"PUBLIC"},
    "trust_check_work_order()": {"PUBLIC"},
    "trust_check_work_order_step()": {"PUBLIC"},
    "trust_check_work_order_step_vendor()": {"PUBLIC"},
    "trust_check_hold_release()": {"PUBLIC"},
    "trust_check_hold_allowance()": {"PUBLIC"},
    "trust_check_recurring_bill()": {"PUBLIC"},
    "trust_refuse_moving_opened_at()": {"PUBLIC"},
    "trust_check_request_work_order()": {"PUBLIC"},
    "trust_check_application()": {"PUBLIC"},
    "trust_lock_application_link()": {"PUBLIC"},
    "trust_check_application_decision()": {"PUBLIC"},
    "trust_check_adverse_action()": {"PUBLIC"},
    "trust_check_lease_offer()": {"PUBLIC"},
    "trust_check_bank_statement()": {"PUBLIC"},
    "trust_check_bank_statement_line()": {"PUBLIC"},
    "trust_check_cutover_entry()": {"PUBLIC"},
    "trust_check_bank_statement_total()": {"PUBLIC"},
    "trust_check_staff_role()": {"PUBLIC"},
    "trust_check_staff_invite()": {"PUBLIC"},
    "trust_check_cutover_signer()": {"PUBLIC"},
    "trust_check_staff_role_change()": {"PUBLIC"},
    "trust_name_document_uploader()": {"PUBLIC"},
    "trust_name_document_remover()": {"PUBLIC"},
    "trust_check_document_file()": {"PUBLIC"},
    "trust_check_budget()": {"PUBLIC"},
    "trust_name_custom_role_changer()": {"PUBLIC"},
    "trust_name_recorder()": {"PUBLIC"},
    "trust_check_archival()": {"PUBLIC"},
    "trust_check_group_member()": {"PUBLIC"},
    "trust_check_payout_method()": {"PUBLIC"},
    "trust_check_document_has_file()": {"PUBLIC"},
    "trust_check_recurring_charge_amount()": {"PUBLIC"},
    "trust_check_signer_change()": {"PUBLIC"},
    "trust_check_signers_have_a_broker()": {"PUBLIC"},
    "trust_check_bill_payment_signer()": {"PUBLIC"},
    "trust_check_deposit_return_signer()": {"PUBLIC"},
    "trust_check_draw_signer()": {"PUBLIC"},
    "trust_check_journal_signer()": {"PUBLIC"},
    "trust_check_rent_change()": {"PUBLIC"},
    "trust_check_bill_split()": {"PUBLIC"},
    "trust_check_application_withdrawal()": {"PUBLIC"},
    "trust_check_application_not_withdrawn()": {"PUBLIC"},
    "trust_check_completion_not_withdrawn()": {"PUBLIC"},
    "trust_check_lease_offer_charge()": {"PUBLIC"},
    "trust_check_lease_offer_void()": {"PUBLIC"},
    "trust_check_lease_offer_not_void()": {"PUBLIC"},
    "trust_open_lease_offer_charges()": {"PUBLIC"},
}
MONEY_FUNCTIONS = [signature for signature, callers in FUNCTIONS.items() if "PUBLIC" not in callers]
# Run as the table owner, so trust_app needs no write grant of its own.
SECURITY_DEFINER = {
    "trust_open_ledger_account(uuid,uuid,text,uuid,uuid,uuid)",
    "trust_post_transfers(uuid,text,transfer_request[],timestamptz,jsonb)",
    "trust_open_vendor_account(uuid,uuid,uuid)",
    "trust_approve_reconciliation(uuid,uuid,timestamptz,timestamptz,numeric,text,text)",
    "trust_open_lease(uuid,uuid,date,date,numeric,uuid[])",
    "trust_end_lease(uuid,uuid,date)",
    "trust_apply_payment(uuid,uuid,text,numeric)",
    "trust_reverse_payment(uuid,uuid,text,text,numeric)",
    "trust_post_management_fee(uuid,text,date,date,timestamptz)",
    "trust_post_leasing_fee(uuid,uuid,text,timestamptz)",
    "trust_draw_owner(uuid,text,text,numeric,timestamptz)",
    "trust_withdraw_pmc_fees(uuid,uuid,text,numeric,timestamptz)",
    "trust_take_owner_fee(uuid,text,text,numeric,text,text,timestamptz)",
    "trust_set_aside_bill(uuid,uuid,timestamptz)",
    "trust_pay_bill(uuid,uuid,timestamptz)",
    "trust_assess_late_fees(uuid,date)",
    "trust_charge_nsf_fee(uuid,text)",
    "trust_post_opening_balances(uuid,uuid,date,jsonb,numeric,text)",
    "trust_record_owner_contribution(uuid,text,text,numeric,timestamptz,text)",
    "trust_complete_lease_offer(uuid,uuid,uuid)",
    "trust_match_bank_lines(uuid,uuid,uuid[],text[],uuid[],uuid)",
    "trust_match_cutover_opening(uuid,uuid,uuid,uuid)",
    "trust_unmatch_bank_lines(uuid,uuid,uuid)",
    "trust_prepare_bank_reconciliation(uuid,uuid,date)",
    "trust_approve_bank_reconciliation(uuid,uuid)",
    "trust_return_deposit(uuid,uuid,text,text,timestamptz)",
    "trust_remove_lease_tenant(uuid,uuid,uuid,date,uuid)",
    "trust_propose_journal_entry(uuid,date,text,transfer_request[])",
    "trust_post_journal_entry(uuid,uuid)",
    "trust_decline_journal_entry(uuid,uuid,text)",
    "trust_audit()",  # writes the audit log, which no other role may write
    "trust_check_payment_hold()",  # locks the hold's lease, which the app can't
    "trust_check_bill_decision()",  # locks the bill's owner account, which the app can't
    "trust_check_charge_waiver()",  # locks the waived charge, which the app can't
    # Takes a lock on the vendor's reference, and sees a vendor's bills and shared invoices
    # whatever the caller reads.
    "trust_check_bill_reference()",
    "trust_check_shared_bill()",  # checks a shared invoice's parts at commit, as the owner
    "trust_fill_bill_markup()",  # locks the bill's owner account, which the app can't
    # Reads lease offers for the AI role, which never reads them, and returns only each lease's
    # renewal status (founder decision, 2026-10-13).
    "trust_report_lease_expirations(uuid,date)",
}


@pytest.fixture
def nobody(conn):
    conn.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pms_nobody') "
        "THEN CREATE ROLE pms_nobody NOLOGIN; END IF; END $$"
    )
    return "pms_nobody"


# Everything a migration created in the public schema.
RELATIONS = f"""
    SELECT c.relname FROM pg_class c
    WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
      AND {NOT_FROM_AN_EXTENSION.format(catalog="'pg_class'", oid="c.oid")}
"""
ROUTINES = f"""
    SELECT p.oid::regprocedure::text FROM pg_proc p
    WHERE p.pronamespace = 'public'::regnamespace
      AND {NOT_FROM_AN_EXTENSION.format(catalog="'pg_proc'", oid="p.oid")}
"""


def callers(conn, signature):
    """Roles other than the owner that hold EXECUTE, with PUBLIC spelled out."""
    return {
        row[0]
        for row in conn.execute(
            """
            SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END
            FROM pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
            WHERE p.oid = %s::regprocedure AND a.privilege_type = 'EXECUTE'
              AND a.grantee <> p.proowner
            """,
            (signature,),
        )
    }


def granted(conn, role, table):
    return {
        privilege
        for privilege in PRIVILEGES
        if conn.execute(
            "SELECT has_table_privilege(%s, %s, %s)", (role, table, privilege)
        ).fetchone()[0]
    }


@pytest.mark.parametrize("table", TABLES)
def test_app_role_table_privileges_are_exact(conn, table):
    assert granted(conn, "trust_app", table) == TABLES[table]


def updatable_columns(conn, table):
    columns = [
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,)
        )
    ]
    return {
        column
        for column in columns
        if conn.execute(
            "SELECT has_column_privilege('trust_app', %s, %s, 'UPDATE')", (table, column)
        ).fetchone()[0]
    }


@pytest.mark.parametrize("table", RENAMEABLE)
def test_app_role_may_update_only_display_names(conn, table):
    assert updatable_columns(conn, table) == {"display_name"}


@pytest.mark.parametrize("table", COLUMN_UPDATES)
def test_app_role_may_update_only_these_columns(conn, table):
    assert updatable_columns(conn, table) == COLUMN_UPDATES[table]


def test_no_other_table_has_a_column_the_app_may_update(conn):
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT c.relname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid,"
            " aclexplode(a.attacl) x WHERE c.relnamespace = 'public'::regnamespace"
            " AND x.grantee = 'trust_app'::regrole AND x.privilege_type = 'UPDATE'"
        )
    }
    assert tables == set(RENAMEABLE) | set(COLUMN_UPDATES)


def test_every_relation_and_function_is_pinned(conn):
    relations = {row[0] for row in conn.execute(RELATIONS)}
    routines = {row[0] for row in conn.execute(ROUTINES)}
    pinned = {
        row[0]
        for row in conn.execute(
            "SELECT s::regprocedure::text FROM unnest(%s::text[]) AS s", (list(FUNCTIONS),)
        )
    }

    assert relations == set(TABLES), "pin every table and view in TABLES, with its grants"
    assert routines == pinned, "pin every function in FUNCTIONS, with who may call it"


@pytest.mark.parametrize("signature", FUNCTIONS)
def test_function_callers_are_exact(conn, signature):
    assert callers(conn, signature) == FUNCTIONS[signature]


@pytest.mark.parametrize("signature", FUNCTIONS)
def test_app_role_function_privileges_are_exact(conn, signature):
    allowed = conn.execute(
        "SELECT has_function_privilege('trust_app', %s, 'EXECUTE')", (signature,)
    ).fetchone()[0]
    assert allowed is bool(FUNCTIONS[signature] & {"trust_app", "PUBLIC"})


def test_no_function_that_runs_as_owner_is_callable_by_public(conn):
    # Independent of the pins above: a SECURITY DEFINER function runs with the owner's rights,
    # so leaving PUBLIC's default EXECUTE on one would let any role on the server use them.
    exposed = [
        signature
        for (signature,) in conn.execute(ROUTINES + " AND p.prosecdef")
        if "PUBLIC" in callers(conn, signature)
    ]
    assert exposed == []


def test_no_role_but_the_owner_may_delete_or_truncate(conn):
    # Ledger history is append-only and the app role never deletes (CLAUDE.md), whatever a
    # migration grants.
    grants = conn.execute(
        """
        SELECT c.relname, CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END,
               a.privilege_type
        FROM pg_class c, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
        WHERE c.relnamespace = 'public'::regnamespace AND a.grantee <> c.relowner
          AND a.privilege_type IN ('DELETE', 'TRUNCATE')
        """
    ).fetchall()
    assert grants == []


@pytest.mark.parametrize("table", TABLES)
def test_ai_role_reads_only_what_the_app_reads(conn, table):
    expected = set() if table in AI_NEVER_READS else TABLES[table] & {"SELECT"}
    assert granted(conn, "trust_ai_agent", table) == expected


def test_ai_role_holds_no_column_grant(conn):
    # trust_app renames records through column grants; the AI role must hold none, since a
    # column-level UPDATE or INSERT is a write the table-level pins above don't show.
    grants = conn.execute(
        """
        SELECT c.relname, a.attname, x.privilege_type
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid, aclexplode(a.attacl) x
        WHERE c.relnamespace = 'public'::regnamespace
          AND x.grantee = 'trust_ai_agent'::regrole
        """
    ).fetchall()
    assert grants == []


@pytest.mark.parametrize("signature", FUNCTIONS)
def test_ai_role_calls_only_the_reports(conn, signature):
    # PUBLIC's functions are the id helpers, a balance check and trigger functions, none of
    # which writes; everything else it may call is a report.
    allowed = conn.execute(
        "SELECT has_function_privilege('trust_ai_agent', %s, 'EXECUTE')", (signature,)
    ).fetchone()[0]
    assert allowed is (signature.startswith("trust_report_") or "PUBLIC" in FUNCTIONS[signature])


def test_the_function_that_writes_the_ledger_keeps_its_search_path_pinned(conn):
    # pgledger_create_transfers does every ledger write (trust_post_transfers calls it). It runs
    # as its caller since 20261009000007 and keeps the search_path 20261009000002 pinned, so
    # whoever calls it, the tables it writes are the schema's own, never look-alikes earlier on
    # a session's search_path.
    config = conn.execute(
        "SELECT proconfig FROM pg_proc WHERE oid = %s::regprocedure",
        ("pgledger_create_transfers(transfer_request[],timestamptz,jsonb)",),
    ).fetchone()[0]
    assert "search_path=public, pg_temp" in (config or [])


def test_ai_role_has_no_powers_beyond_its_grants(conn):
    attributes = conn.execute(
        "SELECT rolsuper, rolcreaterole, rolcreatedb, rolcanlogin, rolreplication, rolbypassrls"
        " FROM pg_roles WHERE rolname = 'trust_ai_agent'"
    ).fetchone()
    assert attributes == (False,) * 6
    # Not a member of trust_app or any other role whose grants it would inherit.
    memberships = conn.execute(
        "SELECT roleid::regrole::text FROM pg_auth_members WHERE member = 'trust_ai_agent'::regrole"
    ).fetchall()
    assert memberships == []


@pytest.mark.parametrize("table", TABLES)
def test_other_roles_get_no_table_privileges(conn, nobody, table):
    assert granted(conn, nobody, table) == set()


@pytest.mark.parametrize("signature", MONEY_FUNCTIONS)
def test_other_roles_cannot_call_money_functions(conn, nobody, signature):
    allowed = conn.execute(
        "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (nobody, signature)
    ).fetchone()[0]
    assert allowed is False


@pytest.mark.parametrize("signature", FUNCTIONS)
def test_only_the_write_paths_run_as_owner_with_a_pinned_search_path(conn, signature):
    definer, config = conn.execute(
        "SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure", (signature,)
    ).fetchone()
    assert definer is (signature in SECURITY_DEFINER)
    if definer:
        assert "search_path=public, pg_temp" in (config or [])


def test_row_level_security_only_hides_the_audit_rows_the_ai_may_not_read(conn):
    """The audit log is the one table with row-level security (migration 20261013000043): the
    app reads all of it, and the AI only the rows of tables it may read. Nobody else may read
    the log, and the policies only ever filter what a grant already allows."""
    secured = conn.execute(
        "SELECT relname, relforcerowsecurity FROM pg_class"
        " WHERE relnamespace = 'public'::regnamespace AND relrowsecurity"
    ).fetchall()
    policies = conn.execute(
        "SELECT policyname, permissive, roles::text[], cmd, qual FROM pg_policies"
        " WHERE schemaname = 'public' ORDER BY policyname"
    ).fetchall()

    assert secured == [("trust_audit_log", False)]
    assert policies == [
        (
            "trust_audit_log_ai",
            "PERMISSIVE",
            ["trust_ai_agent"],
            "SELECT",
            "has_table_privilege((to_regclass(('public.'::text || quote_ident(table_name))))"
            "::oid, 'SELECT'::text)",
        ),
        ("trust_audit_log_app", "PERMISSIVE", ["trust_app"], "SELECT", "true"),
    ]
