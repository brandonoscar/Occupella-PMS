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
  enteredWords,
  billBadge,
  billsPaidSummary,
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

  it("names a bill's next step, and none once it is paid", () => {
    expect(nextStep("needs approval")).toBe("Record approval");
    expect(nextStep("to set aside")).toBe("Set aside");
    expect(nextStep("set aside")).toBe("Pay");
    expect(nextStep("paid")).toBeNull();
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
      has_agreement: true,
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
    const bill = { status: "needs approval", owner_email: "sent" } as Bill;
    expect(emailNote(bill)).toBe("Emailed the owner to approve or decline it in their portal.");
    expect(emailNote({ ...bill, owner_email: "waiting" })).toBe("An email to the owner waits to be sent.");
    expect(emailNote({ ...bill, owner_email: "failed" })).toContain("could not be sent");
    expect(emailNote({ ...bill, owner_email: null })).toContain("no active portal login");
    expect(emailNote({ ...bill, status: "to set aside" })).toBeNull();
    expect(nextStep("declined")).toBeNull();
  });
});

describe("billBadge", () => {
  it("says a bill's status in Buildium's words, overdue before its next step", () => {
    const bill = { status: "needs approval", overdue: false } as Bill;
    const words = (["needs approval", "to set aside", "set aside", "paid", "declined"] as const).map((status) =>
      billBadge({ ...bill, status, overdue: false }));
    expect(words).toEqual([
      { word: "Awaiting owner approval", tone: "caution" },
      { word: "Approved", tone: "info" },
      { word: "Set aside", tone: "info" },
      { word: "Paid", tone: "positive" },
      { word: "Declined", tone: "neutral" },
    ]);
    expect(billBadge({ ...bill, overdue: true })).toEqual({ word: "Overdue", tone: "danger" });
  });
});

describe("billsPaidSummary and linesNote", () => {
  it("says how many bills Pay bills paid, their total and how", () => {
    const paid = (amount: string) => ({ bill_id: "b", vendor: "V", reference: "R", amount, transfer_id: "t" });
    expect(billsPaidSummary([paid("0.10"), paid("0.20")], "EFT")).toBe("2 bills of $0.30 paid, by EFT.");
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
    expect(paidSummary([paid("0.10"), paid("0.20")], "EFT")).toBe("2 payouts of $0.30 recorded, by EFT.");
    expect(paidSummary([paid("900.00")], "check")).toBe("1 payout of $900.00 recorded, by check.");
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

describe("enteredWords", () => {
  it("says what entering the recurring bills due now did", () => {
    expect(enteredWords({ entered: 0, emails: 0 })).toBe("No recurring bill is due to enter: each month's is in already.");
    expect(enteredWords({ entered: 1, emails: 0 })).toBe("Entered 1 bill.");
    expect(enteredWords({ entered: 3, emails: 1 })).toBe("Entered 3 bills; emailed 1 owner to approve one.");
    expect(enteredWords({ entered: 2, emails: 2 })).toBe("Entered 2 bills; emailed 2 owners to approve one.");
  });
});
