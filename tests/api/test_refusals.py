"""A database refusal as the API says it (pms.api.deps.refusal): the trust_* refusal's own
words, and the ledger's below-zero refusal naming whose money is short, not an account id."""

import psycopg
import pytest

from pms.api.deps import refusal


@pytest.mark.parametrize(
    ("kind", "says"),
    [
        ("owner_property", "the owner's account"),
        ("prepaid_rent", "the tenant's prepaid rent"),
        ("tenant_deposit", "the tenant's deposit"),
        ("vendor_payable", "the vendor's account"),
        ("pmc_income", "the company's fee account"),
    ],
)
def test_a_below_zero_refusal_says_whose_money_is_short_and_by_how_much(kind, says):
    error = psycopg.errors.CheckViolation(
        f"trust: {kind} account pgla_01M4XVRFWBEB8RFX7SB01YKSP4 would go below zero"
        " (balance -1400.5)\nCONTEXT: PL/pgSQL function trust_check_balance()"
    )
    assert refusal(error) == f"not enough money: {says} would be $1,400.50 short"


def test_any_other_refusal_keeps_its_own_words_without_the_prefix():
    assert refusal(psycopg.errors.CheckViolation("trust: this bill was declined\nDETAIL: x")) == (
        "this bill was declined"
    )
    # A kind it doesn't know, or a balance that isn't below zero, is left as the database said it.
    for text in (
        "trust: bank_cash account pgla_1 would go below zero (balance -5.00)",
        "trust: owner_property account pgla_1 would go below zero (balance 5.00)",
    ):
        assert refusal(psycopg.errors.CheckViolation(text)) == text.removeprefix("trust: ")


def test_a_draw_over_what_is_available_says_how_much_is_and_why():
    def said(available, asked):
        return refusal(
            psycopg.errors.RaiseException(
                f"trust: {available} is available to draw from pgla_01M4XW7V6HFFHVB7XW4DP5B7Y5"
                f" (its balance less a reserve of 200.00 and unpaid bills of 1250.5); {asked} is"
                " too much"
            )
        )

    # Unpaid bills are named only when there are some.
    assert (
        refusal(
            psycopg.errors.CheckViolation(
                "trust: 0.00 is available to draw from pgla_1 (its balance less a reserve of 200.00"
                " and unpaid bills of 0); 0.00 is too much"
            )
        )
        == "nothing is available to draw: what the account holds is kept for the $200.00 reserve"
    )
    assert said("0.00", "0.00") == (
        "nothing is available to draw: what the account holds is kept for the $200.00 reserve"
        " and $1,250.50 of unpaid bills"
    )
    # Everything asked of an account holding less than its reserve: what it asks is negative.
    assert said("0", "-100.00") == (
        "nothing is available to draw: what the account holds is kept for the $200.00 reserve"
        " and $1,250.50 of unpaid bills"
    )
    assert said("75.25", "100.00") == (
        "only $75.25 is available to draw (the balance less the $200.00 reserve and $1,250.50 of"
        " unpaid bills); $100.00 is more than that"
    )


def test_the_last_admin_refusal_says_what_to_do_not_whose_id():
    error = psycopg.errors.CheckViolation(
        "trust: staff member 3f1c2b9a-0000-4000-8000-000000000001 is the PMC's last admin"
    )
    assert refusal(error) == "the company keeps an admin: give another staff member admin first"


def test_a_tie_out_refusal_says_money_moves_with_its_cash():
    error = psycopg.errors.CheckViolation(
        "trust: trust bank account 3f1c2b9a-0000-4000-8000-000000000001 does not tie out"
        " (off by -300.00)\nHINT: Money held in one trust bank account moves to another only with"
    )
    assert refusal(error) == (
        "money moves to another trust bank account only with its cash: this would leave one off"
        " by $300.00"
    )


def test_a_closed_period_refusal_says_to_date_it_later():
    error = psycopg.errors.CheckViolation(
        "trust: trust bank account 3f1c2b9a-0000-4000-8000-000000000001 is reconciled and closed"
        " through 2026-02-01 00:00:00+00; a transfer dated 2026-01-15 00:00:00+00 can't be posted"
    )
    assert refusal(error) == (
        "that day is in a period already reconciled and closed: date it in the next open one"
    )
