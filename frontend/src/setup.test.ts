import { describe, expect, it } from "vitest";
import type { Agreement, LateFees } from "./setup";
import { agreementSummary, kindLabel, lateFeeSummary, leaseBreakFeeSummary, leaseSummary, nsfFeeSummary } from "./setup";

const NONE: Agreement = {
  starts_on: "2026-10-01",
  fee_percent: "0",
  minimum_fee: "0",
  flat_fee: "0",
  leasing_fee_percent: "0",
  reserve: "0",
  approval_limit: "0",
};

describe("agreementSummary", () => {
  it("reads every part of the terms", () => {
    expect(
      agreementSummary({
        ...NONE,
        fee_percent: "8.5",
        minimum_fee: "50.00",
        flat_fee: "10.00",
        leasing_fee_percent: "50",
        reserve: "250.00",
        approval_limit: "500.00",
      }),
    ).toBe(
      "8.5% of rent and recurring fees collected (at least $50.00); a $10.00 flat fee;" +
        " 50% of a new lease's monthly rent; keeps $250.00 in reserve;" +
        " owner approves bills over $500.00",
    );
  });
  it("says when there is no management fee, and when every bill needs approval", () => {
    expect(agreementSummary(NONE)).toBe("No management fee; owner approves every bill");
    expect(agreementSummary({ ...NONE, minimum_fee: "75.00" })).toBe(
      "a $75.00 minimum fee; owner approves every bill",
    );
    expect(agreementSummary({ ...NONE, fee_percent: "10" })).toBe(
      "10% of rent and recurring fees collected; owner approves every bill",
    );
  });
});

const FEES: LateFees = {
  starts_on: "2026-10-01",
  grace_days: 5,
  flat_fee: "50.00",
  percent: "5",
  maximum: "100.00",
};

describe("nsfFeeSummary", () => {
  it("names the fee, or says there is none", () => {
    expect(nsfFeeSummary({ starts_on: "2026-10-01", fee: "35.00" })).toBe("NSF fee $35.00");
    expect(nsfFeeSummary({ starts_on: "2026-10-01", fee: "0.00" })).toBe("No NSF fee");
    expect(nsfFeeSummary(null)).toBe("No NSF fee");
  });
});

describe("leaseBreakFeeSummary", () => {
  it("names the flat fee and the months of rent, or says there is none", () => {
    const terms = { starts_on: "2026-01-01", flat_fee: "250.00", rent_months: "1.5" };
    expect(leaseBreakFeeSummary(terms)).toBe("Lease-break fee $250.00 and 1.5 months' rent");
    expect(leaseBreakFeeSummary({ ...terms, flat_fee: "0.00", rent_months: "1" })).toBe("Lease-break fee 1 month's rent");
    expect(leaseBreakFeeSummary({ ...terms, rent_months: "0" })).toBe("Lease-break fee $250.00");
    expect(leaseBreakFeeSummary({ ...terms, flat_fee: "0.00", rent_months: "0" })).toBe("No lease-break fee");
    expect(leaseBreakFeeSummary(null)).toBe("No lease-break fee");
  });
});

describe("lateFeeSummary", () => {
  it("reads the grace, the fees and the cap", () => {
    expect(lateFeeSummary(FEES)).toBe(
      "After 5 days' grace: $50.00 and 5% of what is unpaid, up to $100.00",
    );
    expect(lateFeeSummary({ ...FEES, grace_days: 1, percent: "0", maximum: null })).toBe(
      "After 1 day's grace: $50.00",
    );
    expect(lateFeeSummary({ ...FEES, flat_fee: "0" })).toBe(
      "After 5 days' grace: 5% of what is unpaid, up to $100.00",
    );
  });
  it("says when terms charge nothing", () => {
    expect(lateFeeSummary({ ...FEES, flat_fee: "0", percent: "0" })).toBe("No late fee");
  });
});

describe("leaseSummary and kindLabel", () => {
  it("reads a lease with an end and one month to month", () => {
    const lease = {
      id: "l1",
      starts_on: "2026-10-01",
      ends_on: "2027-09-30",
      monthly_rent: "1500.00",
      tenants: ["Tenant A", "Tenant B"],
    };
    expect(leaseSummary(lease)).toBe(
      "Tenant A, Tenant B: 10/01/2026 – 09/30/2027, $1,500.00 a month",
    );
    expect(leaseSummary({ ...lease, ends_on: null })).toBe(
      "Tenant A, Tenant B: 10/01/2026 – Month to month, $1,500.00 a month",
    );
  });
  it("names the kinds of trust bank account", () => {
    expect(kindLabel("operating")).toBe("Operating");
    expect(kindLabel("security_deposit")).toBe("Security deposit");
  });
});
