import { describe, expect, it } from "vitest";
import type { Bill, CompanyFees, OwnerMoney, Recurring } from "./payables";
import {
  companyFeesDue,
  companyFeesLabel,
  emailNote,
  feeLine,
  heldLabel,
  incomeStatementHref,
  lastMonthPeriod,
  nextStep,
  recurringLabel,
  statusTone,
} from "./payables";

describe("payables", () => {
  it("reads the company's fees in trust and the day to take them out by", () => {
    const held: CompanyFees = {
      bank_account_id: "b1", bank_account: "Operating", held: "1234.50", since: "2026-10-01",
      days_held: 1, take_by: "2026-10-26",
    };
    expect(companyFeesLabel(held)).toBe("$1,234.50 of the company's fees, the oldest since 2026-10-01 (1 day)");
    expect(companyFeesDue(held, "2026-10-26")).toEqual({ word: "take out by 2026-10-26", late: false });
    expect(companyFeesDue(held, "2026-10-27")).toEqual({ word: "take out: past 2026-10-26", late: true });
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
      "Vendor 1: $80.00 on day 5 of each month from 2026-01-01, Owner 1, Property 1 (LAWN)",
    );
    expect(recurringLabel({ ...bill, ends_on: "2026-12-31" })).toContain("until 2026-12-31,");
  });

  it("links an owner's income statement from the first of the year through today", () => {
    expect(incomeStatementHref("o 1", "2026-10-15")).toBe(
      "/api/reports/trust_report_income_statement?arg=o%201&arg=2026-01-01&arg=2026-10-15&format=pdf",
    );
  });

  it("says what an owner's account keeps back from a draw", () => {
    const owner: OwnerMoney = {
      account: "a1", owner_id: "o1", owner: "Owner 1", property: "Property 1", bank_account: "Operating",
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

describe("statusTone", () => {
  it("marks a bill waiting on its owner as a caution, paid as done and declined as closed", () => {
    expect((["needs approval", "to set aside", "set aside", "paid", "declined"] as const).map(statusTone)).toEqual([
      "caution", "info", "info", "positive", "neutral",
    ]);
  });
});
