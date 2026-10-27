import { describe, expect, it } from "vitest";
import type { Bill, CompanyFees, OwnerMoney, Recurring } from "./payables";
import {
  paidSummary,
  companyFeesDue,
  companyFeesLabel,
  emailNote,
  feeLine,
  heldLabel,
  incomeStatementHref,
  lastMonthPeriod,
  nextStep,
  recurringLabel,
  billBadge,
  billsPaidSummary,
  holdLine,
  paymentHow,
  similarNote,
  linesNote,
  ownerFeeSummary,
  evenShares,
  markupLabel,
} from "./payables";

describe("payables", () => {
  it("reads the company's fees in trust and the day to take them out by", () => {
    const held: CompanyFees = {
      bank_account_id: "b1", bank_account: "Operating", held: "1234.50", since: "2026-10-01",
      days_held: 1, take_by: "2026-10-26",
    };
    expect(companyFeesLabel(held)).toBe("$1,234.50 of the company's fees, the oldest since 10/01/2026 (1 day)");
    expect(companyFeesDue(held, "2026-10-26")).toEqual({ word: "take out by 10/26/2026", late: false });
    expect(companyFeesDue(held, "2026-10-27")).toEqual({ word: "take out: past 10/26/2026", late: true });
    const none = { ...held, held: "0.00", since: null, days_held: null, take_by: null };
    expect(companyFeesLabel(none)).toBe("None of the company's fees is held here.");
    expect(companyFeesDue(none, "2026-10-27")).toBeNull();
  });

  it("names a bill's next step, and none once it is paid or void", () => {
    expect(nextStep("waiting for owner")).toBe("Record approval");
    expect(nextStep("ready to pay")).toBe("Record payment");
    expect(nextStep("partly paid")).toBe("Record payment");
    expect(nextStep("short")).toBe("Ask owner");
    expect(nextStep("entered")).toBeNull();
    expect(nextStep("paid")).toBeNull();
    expect(nextStep("void")).toBeNull();
  });

  it("reads a recurring bill, with its last day once it ends", () => {
    const bill: Recurring = {
      id: "r1", vendor: "Vendor 1", account: "a1", owner: "Owner 1", property: "Property 1",
      reference_prefix: "LAWN", amount: "80.00", memo: "Lawn care", day_of_month: 5,
      starts_on: "2026-01-01", ends_on: null, expense_account_id: null, expense_account: null,
    };
    expect(recurringLabel(bill)).toBe(
      "Vendor 1: $80.00 on day 5 of each month from 01/01/2026, Owner 1, Property 1 (LAWN)",
    );
    expect(recurringLabel({ ...bill, ends_on: "2026-12-31" })).toContain("until 12/31/2026,");
  });

  it("links an owner's income statement from the first of the year through today", () => {
    expect(incomeStatementHref("o 1", "2026-10-15")).toBe(
      "/api/reports/trust_report_income_statement?arg=o%201&arg=2026-01-01&arg=2026-10-15&format=pdf",
    );
  });

  it("says what an owner's account keeps back from a draw", () => {
    const owner: OwnerMoney = {
      account: "a1", owner_id: "o1", owner: "Owner 1", property_id: "pr1", property: "Property 1", bank_account: "Operating",
      balance: "1200.00", reserve: "100.00", unpaid_bills: "300.00", available: "800.00",
      has_agreement: true, payout_method: "check",
    };
    expect(heldLabel(owner)).toBe(
      "$1,200.00 held, less a $100.00 reserve and $300.00 of unpaid bills",
    );
    expect(heldLabel({ ...owner, reserve: "0.00" })).toBe(
      "$1,200.00 held, less $300.00 of unpaid bills",
    );
    expect(heldLabel({ ...owner, reserve: "0.00", unpaid_bills: "0.00" })).toBe("$1,200.00 held");
  });

  it("reads a fee run's line: the fee taken, or why not", () => {
    const fee = { account: "a1", owner: "Owner 1", property: "Property 1" };
    expect(feeLine({ ...fee, fee: "150.00", refused: null })).toBe(
      "Owner 1, Property 1: $150.00 taken",
    );
    expect(feeLine({ ...fee, fee: null, refused: "it would go below zero" })).toBe(
      "Owner 1, Property 1: not taken, it would go below zero",
    );
  });

  it("makes last month a fee period that ends on this month's first day", () => {
    expect(lastMonthPeriod(new Date("2026-10-12T15:00:00Z"))).toEqual({
      start: "2026-09-01",
      end: "2026-10-01",
    });
    expect(lastMonthPeriod(new Date("2026-01-31T23:00:00Z"))).toEqual({
      start: "2025-12-01",
      end: "2026-01-01",
    });
  });

  it("tells staff about the owner's email only while a bill waits for the owner", () => {
    const bill = billOf({ status: "waiting for owner", owner_email: "sent" });
    expect(emailNote(bill)).toBe("Emailed the owner to approve or decline it in their portal.");
    expect(emailNote({ ...bill, owner_email: "waiting" })).toBe("An email to the owner waits to be sent.");
    expect(emailNote({ ...bill, owner_email: "failed" })).toContain("could not be sent");
    expect(emailNote({ ...bill, owner_email: null })).toContain("no active portal login");
    expect(emailNote({ ...bill, status: "ready to pay" })).toBeNull();
  });
});

// A bill as the API sends one, ready to pay unless told otherwise.
function billOf(more: Partial<Bill> = {}): Bill {
  return {
    id: "b1", vendor_id: "v1", vendor: "Vendor 1", reference: "INV-1", account: "a1", owner_id: "o1",
    owner: "Owner A", property_id: "p1", property: "Property 1", bill_date: "2026-10-01",
    due_on: "2026-10-15", amount: "240.00", memo: "Gutters", work_order_id: null,
    expense_account_id: null, expense_account: null, split: false, approval_limit: "500.00",
    approved_by: null, declined_by: null, decline_reason: null, voided: false, owner_email: null,
    status: "ready to pay", overdue: false, markup: null, markup_percent: null, shared_bill_id: null,
    paid: "0.00", left: "240.00", held: "240.00", held_since: null, short: null, needed: null,
    ...more,
  };
}

describe("billBadge", () => {
  it("says a bill's status in plain words (ADR 0010)", () => {
    const words = (["entered", "waiting for owner", "ready to pay", "short", "partly paid", "paid", "void"] as const)
      .map((status) => billBadge({ status }).word);
    expect(words).toEqual(["Entered", "Waiting for owner", "Ready to pay", "Short", "Partly paid", "Paid", "Void"]);
    expect(billBadge({ status: "short" }).tone).toBe("danger");
    expect(billBadge({ status: "waiting for owner" }).tone).toBe("caution");
  });
});

describe("holdLine", () => {
  it("says what a bill holds of the owner's balance, and what is short", () => {
    expect(holdLine(billOf())).toBe("$240.00 held from Owner A's balance for this bill.");
    expect(holdLine(billOf({ markup: "24.00", held: "264.00" }))).toBe(
      "$264.00 held from Owner A's balance for this bill and its $24.00 markup.",
    );
    expect(holdLine(billOf({ status: "short", held: "100.00", short: "140.00", needed: "140.00" }))).toBe(
      "$100.00 held from Owner A's balance for this bill. Owner A needs to send $140.00.",
    );
    expect(holdLine(billOf({ status: "short", held: "0.00", short: "240.00", needed: "300.00" }))).toBe(
      "Nothing of Owner A's balance is left to hold for this bill. Owner A needs to send $300.00.",
    );
    expect(holdLine(billOf({ status: "waiting for owner" }))).toBe(
      "$240.00 held from Owner A's balance for this bill until Owner A decides.",
    );
  });
  it("says when the money left the owner, and what paid, void and entered mean", () => {
    expect(holdLine(billOf({ status: "partly paid", paid: "100.00", left: "140.00", held: "140.00", held_since: "2026-10-05" })))
      .toBe("$100.00 paid. $140.00 held for this bill since 10/05/2026, when it left Owner A's balance.");
    expect(holdLine(billOf({ held_since: "2026-10-05" })))
      .toBe("$240.00 held for this bill since 10/05/2026, when it left Owner A's balance.");
    expect(holdLine(billOf({ status: "paid", paid: "240.00", left: "0.00", held: null }))).toBe("Paid in full: $240.00.");
    expect(holdLine(billOf({ status: "void", held: null }))).toBe(
      "Nothing was paid; the hold went back to Owner A's balance.",
    );
    expect(holdLine(billOf({ status: "entered", held: null, bill_date: "2026-11-01" }))).toBe(
      "Nothing is held for it until its date, 11/01/2026.",
    );
  });
});

describe("paymentHow and similarNote", () => {
  it("reads how a payment went", () => {
    expect(paymentHow({ method: "check", check_number: "1042" })).toBe("Check 1042");
    expect(paymentHow({ method: "check", check_number: null })).toBe("Check");
    expect(paymentHow({ method: "EFT", check_number: null })).toBe("ACH");
    expect(paymentHow({ method: null, check_number: null })).toBe("Not recorded");
  });
  it("warns of a bill entered under the same reference", () => {
    const one = { id: "b", reference: "INV-100", bill_date: "2026-10-01", amount: "200.00", status: "ready to pay" as const };
    expect(similarNote("Vendor 1", [one])).toBe(
      "Vendor 1 has bill INV-100 ($200.00, dated 10/01/2026, ready to pay) already. Check this isn't the same bill.",
    );
    expect(similarNote("Vendor 1", [one, { ...one, reference: "inv 100", status: "void" }])).toBe(
      "Vendor 1 has bills INV-100 ($200.00, dated 10/01/2026, ready to pay), inv 100 ($200.00, dated 10/01/2026, void) already. Check this isn't one of them.",
    );
  });
});

describe("billsPaidSummary and linesNote", () => {
  it("says how many bills Pay bills paid, their total and how", () => {
    const paid = (amount: string) => ({ bill_id: "b", vendor: "V", reference: "R", amount, transfer_id: "t" });
    expect(billsPaidSummary([paid("0.10"), paid("0.20")], "EFT")).toBe("2 bills of $0.30 paid, by ACH.");
    expect(billsPaidSummary([paid("75.00")], "check")).toBe("1 bill of $75.00 paid, by check.");
  });
  it("says what split lines come to against the bill, an empty line as nothing", () => {
    expect(linesNote(["200.00", "50.5"], "300.00")).toBe("Lines come to $250.50 of $300.00.");
    expect(linesNote(["", ""], "")).toBe("Lines come to $0.00 of $0.00.");
  });
});

describe("paidSummary", () => {
  it("says how many payouts, their total and how they were sent", () => {
    const paid = (amount: string) => ({ account: "a", owner: "O", property: "P", amount, transfer_id: "t" });
    expect(paidSummary([paid("0.10"), paid("0.20")], "EFT")).toBe("2 payouts of $0.30 recorded, by ACH.");
    expect(paidSummary([paid("900.00")], "check")).toBe("1 payout of $900.00 recorded, by check.");
    // Each owner's way: both ways named, ACH first, whatever order the payouts came in.
    expect(paidSummary([paid("1.00"), paid("2.00")], ["check", "EFT", "check"])).toBe(
      "2 payouts of $3.00 recorded, by ACH and check.",
    );
  });
});

describe("owner fees", () => {
  it("says what fee was taken from which property", () => {
    expect(ownerFeeSummary("inspection", "75.00", "Property 1")).toBe("A $75.00 inspection fee is taken from Property 1.");
    expect(ownerFeeSummary("lease_up", "1200", "Property 2")).toBe("A $1,200.00 lease-up fee is taken from Property 2.");
    expect(ownerFeeSummary("other", "5.5", "Property 3")).toBe("A $5.50 other fee is taken from Property 3.");
  });
});

describe("shared bills and markups", () => {
  it("shares a total evenly to the cent, the cents left over to the first shares", () => {
    expect(evenShares("900.00", 2)).toEqual(["450.00", "450.00"]);
    expect(evenShares("100", 3)).toEqual(["33.34", "33.33", "33.33"]);
    expect(evenShares("$1,000.01", 4)).toEqual(["250.01", "250.00", "250.00", "250.00"]);
    expect(evenShares("0.02", 3)).toEqual(["0.01", "0.01", "0.00"]);
    // Not money yet: blanks; nobody to share among: nothing.
    expect(evenShares("ten", 2)).toEqual(["", ""]);
    expect(evenShares("1.234", 2)).toEqual(["", ""]);
    expect(evenShares("10.00", 0)).toEqual([]);
  });
  it("says a bill's markup and the owner's percent, or nothing", () => {
    expect(markupLabel({ markup: "25.00", markup_percent: "10" })).toBe("$25.00 (10% in the owner's terms)");
    expect(markupLabel({ markup: null, markup_percent: null })).toBeNull();
  });
});
