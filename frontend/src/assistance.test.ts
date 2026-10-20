import { describe, expect, it } from "vitest";
import type { AssistanceTerms, LeaseAssistance } from "./assistance";
import { nextFirst, paymentLines, paymentTotal, splitLine, termsLine } from "./assistance";

const TERMS: AssistanceTerms = {
  id: "t1", authority_id: "h1", authority: "Synthetic Housing Authority", starts_on: "2026-11-01",
  amount: "800.00", contract: "HAP-0001", recorded_by: "Staff 1",
};
const ASSISTED: LeaseAssistance = {
  in_force: TERMS, monthly_rent: "1000.00", tenant_part: "200.00", terms: [TERMS], charges: [], owed: "0.00",
};

describe("termsLine", () => {
  it("says who pays what from when", () => {
    expect(termsLine(TERMS)).toBe("From 11/01/2026: Synthetic Housing Authority pays $800.00 a month (contract HAP-0001)");
    expect(termsLine({ ...TERMS, contract: null })).toBe("From 11/01/2026: Synthetic Housing Authority pays $800.00 a month");
    expect(termsLine({ ...TERMS, amount: "0.00" })).toBe("From 11/01/2026: no assistance (Synthetic Housing Authority)");
  });
});

describe("splitLine", () => {
  it("splits today's rent between the authority and the tenant, exactly", () => {
    expect(splitLine(ASSISTED)).toBe("Synthetic Housing Authority pays $800.00 of the $1,000.00 rent; the tenant pays $200.00");
    expect(splitLine({ ...ASSISTED, monthly_rent: "1000.01", tenant_part: "200.02" })).toBe(
      "Synthetic Housing Authority pays $799.99 of the $1,000.01 rent; the tenant pays $200.02",
    );
    expect(splitLine({ ...ASSISTED, in_force: null, tenant_part: "1000.00" })).toBe("The tenant pays all of the $1,000.00 rent");
  });
});

describe("nextFirst", () => {
  it("is the first of next month, across a year's end", () => {
    expect(nextFirst(new Date("2026-10-20T12:00:00Z"))).toBe("2026-11-01");
    expect(nextFirst(new Date("2026-12-31T23:00:00Z"))).toBe("2027-01-01");
  });
});

describe("paymentLines and paymentTotal", () => {
  it("keep the leases given an amount above nothing and add them up exactly", () => {
    const amounts = { l1: "800.00", l2: " 0.10 ", l3: "", l4: "0", l5: "0.20" };
    expect(paymentLines(amounts)).toEqual([
      { lease_id: "l1", amount: "800.00" },
      { lease_id: "l2", amount: "0.10" },
      { lease_id: "l5", amount: "0.20" },
    ]);
    expect(paymentTotal(amounts)).toBe("800.30");
    expect(paymentTotal({})).toBe("0.00");
  });
});
