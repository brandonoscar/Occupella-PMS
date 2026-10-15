import { describe, expect, it } from "vitest";
import type { Application } from "./leasing";
import type { Offer } from "./offers";
import type { Bill } from "./payables";
import { applicationLine, billLine, daysBetween, lateLine, monthEndLine, monthLabel, offerLine } from "./today";

const BILL: Bill = {
  id: "b1", vendor: "Vendor A", reference: "INV-1", account: "a1", owner: "Owner A",
  property: "Property 1", bill_date: "2026-10-01", due_on: "2026-10-16", amount: "1200.00",
  memo: "Repair", work_order_id: null, expense_account_id: null, expense_account: null,
  approval_limit: "500.00", approved_by: null,
  declined_by: null, decline_reason: null, owner_email: null, status: "needs approval",
};

describe("today", () => {
  it("counts whole days between two days, across a month's end", () => {
    expect(daysBetween("2026-10-13", "2026-10-16")).toBe(3);
    expect(daysBetween("2026-10-13", "2026-10-08")).toBe(-5);
    expect(daysBetween("2026-02-27", "2026-03-02")).toBe(3);
  });
  it("names a month", () => {
    expect(monthLabel("2026-09")).toBe("September 2026");
    expect(monthLabel("2027-01")).toBe("January 2027");
  });
  it("reads late rent", () => {
    const lease = {
      lease_id: "l1", property: "Property 1", unit: "Unit 1", tenants: ["Tenant A", "Tenant B"],
      rent_past_due: "1500.00", oldest_due_on: "2026-10-03",
    };
    expect(lateLine(lease, "2026-10-13")).toBe(
      "Unit 1, Property 1 (Tenant A, Tenant B): $1,500.00 of rent past due since 2026-10-03 (10 days)",
    );
    expect(lateLine({ ...lease, tenants: [], oldest_due_on: "2026-10-12" }, "2026-10-13")).toBe(
      "Unit 1, Property 1: $1,500.00 of rent past due since 2026-10-12 (1 day)",
    );
  });
  it("reads a bill by when it is due", () => {
    expect(billLine(BILL, "2026-10-13")).toBe(
      "Vendor A INV-1 for Property 1: $1,200.00, due in 3 days (needs approval)",
    );
    expect(billLine({ ...BILL, due_on: "2026-10-13", status: "set aside" }, "2026-10-13")).toBe(
      "Vendor A INV-1 for Property 1: $1,200.00, due today (set aside)",
    );
    expect(billLine({ ...BILL, due_on: "2026-10-12" }, "2026-10-13")).toBe(
      "Vendor A INV-1 for Property 1: $1,200.00, 1 day late (needs approval)",
    );
  });
  it("reads where month-end stands", () => {
    const account = {
      bank_account_id: "b1", name: "Operating trust", closed_through: "2026-07-31",
      months: ["2026-08", "2026-09"], waiting_for_approval: null,
    };
    expect(monthEndLine(account)).toBe("Operating trust: August 2026, September 2026 not reconciled");
    expect(monthEndLine({ ...account, months: [], waiting_for_approval: "2026-09-30" })).toBe(
      "Operating trust: prepared through 2026-09-30, waiting for approval",
    );
  });
  it("reads an application and an offer", () => {
    const application = {
      id: "a1", property: "Property 1", unit: "Unit 2", monthly_rent: "1500.00",
      move_in_on: "2026-11-01", monthly_income: "4500.00", submitted_at: "2026-10-12T15:00:00Z",
      applicants: [{ name: "Applicant 1", email: "a1@example.com" }, { name: "Applicant 2", email: "a2@example.com" }],
      screenings: [], decision: null,
    } as Application;
    expect(applicationLine(application)).toBe("Unit 2, Property 1: Applicant 1, Applicant 2, applied 2026-10-12");
    const offer = {
      id: "o1", kind: "renewal", application_id: null, renews_lease_id: "l1", property: "Property 1",
      unit: "Unit 1", starts_on: "2026-11-01", ends_on: null, monthly_rent: "1550.00",
      document_title: "Renewal", offered_at: "2026-10-01T00:00:00Z", sent: true, lease_id: null,
      signers: [
        { position: 1, name: "Tenant A", email: "a@example.com", signed_at: "2026-10-02T00:00:00Z" },
        { position: 2, name: "Tenant B", email: "b@example.com", signed_at: null },
      ],
    } as Offer;
    expect(offerLine(offer)).toBe("Renewal of Unit 1, Property 1 from 2026-11-01: 1 of 2 signed");
    expect(offerLine({ ...offer, kind: "new lease" })).toBe(
      "New lease of Unit 1, Property 1 from 2026-11-01: 1 of 2 signed",
    );
  });
});
