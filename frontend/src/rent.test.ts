import { describe, expect, it } from "vitest";
import type { OpenCharge, RecurringCharge } from "./rent";
import {
  bounceSummary,
  earliestRentChange,
  insuranceBadge,
  insuranceLine,
  rentChangeLine,
  chargeLabel,
  owedLabel,
  owedTone,
  paymentSummary,
  recurringLabel,
  rentRunSummary,
  requestId,
} from "./rent";

describe("renters insurance", () => {
  const ON_FILE = {
    required: true, minimum_liability: "100000.00", carrier: "Carrier A", policy_number: "RI-1",
    expires_on: "2027-01-31", liability_limit: "300000.00", standing: "current", below_minimum: false,
  };

  it("reads what the lease asks and the policy on file", () => {
    expect(insuranceLine(ON_FILE)).toBe(
      "Renters insurance required (at least $100,000.00 of liability): Carrier A RI-1 through 2027-01-31, $300,000.00 of liability",
    );
    expect(insuranceLine({ ...ON_FILE, required: false, minimum_liability: null, carrier: null,
      policy_number: null, expires_on: null, liability_limit: null, standing: "none on file" }))
      .toBe("Renters insurance not required: none on file");
  });

  it("marks a policy below the minimum or missing where required, and not where it isn't", () => {
    expect(insuranceBadge(ON_FILE)).toEqual({ word: "current", tone: "positive" });
    expect(insuranceBadge({ ...ON_FILE, below_minimum: true })).toEqual({ word: "below the minimum", tone: "danger" });
    expect(insuranceBadge({ ...ON_FILE, standing: "expires in 5 days" })).toEqual({ word: "expires in 5 days", tone: "caution" });
    expect(insuranceBadge({ ...ON_FILE, standing: "none on file" })).toEqual({ word: "none on file", tone: "danger" });
    expect(insuranceBadge({ ...ON_FILE, required: false, standing: "expired" })).toEqual({ word: "expired", tone: "neutral" });
  });
});

describe("rent changes", () => {
  it("reads a change, with its notice and the lease term that allows it", () => {
    const change = {
      id: "rc1", effective_on: "2026-12-01", monthly_rent: "1575.00", notice_given_on: "2026-10-15",
      served_by: "mail" as const, lease_terms: null,
    };
    expect(rentChangeLine(change)).toBe("From 2026-12-01: $1,575.00 a month (notice by mail on 2026-10-15)");
    expect(rentChangeLine({ ...change, served_by: "in person", lease_terms: "Section 4" })).toBe(
      "From 2026-12-01: $1,575.00 a month (notice in person on 2026-10-15; Section 4)",
    );
  });

  it("starts a change no sooner than a full month's notice given today allows", () => {
    expect(earliestRentChange(new Date("2026-10-01T12:00:00Z"))).toBe("2026-11-01");
    expect(earliestRentChange(new Date("2026-10-02T00:00:00Z"))).toBe("2026-12-01");
    expect(earliestRentChange(new Date("2026-12-15T00:00:00Z"))).toBe("2027-02-01");
  });
});

describe("rent", () => {
  it("says what a payment did", () => {
    expect(paymentSummary({ transfers: ["t"], to_owner: "1500.00", to_prepaid: "0.00" })).toBe(
      "$1,500.00 paid what is owed.",
    );
    expect(paymentSummary({ transfers: ["t", "u"], to_owner: "100.00", to_prepaid: "50.00" })).toBe(
      "$100.00 paid what is owed; $50.00 held as prepaid rent.",
    );
    expect(paymentSummary({ transfers: ["u"], to_owner: "0.00", to_prepaid: "50.00" })).toBe(
      "$50.00 held as prepaid rent.",
    );
  });
  it("says what a bounce did, with its NSF fee when there is one", () => {
    expect(bounceSummary("1500.00", { transfers: ["r"], nsf_fee: null })).toBe(
      "The $1,500.00 payment bounced and is undone.",
    );
    expect(bounceSummary("1500.00", { transfers: ["r", "s"], nsf_fee: "35.00" })).toBe(
      "The $1,500.00 payment bounced and is undone; a $35.00 NSF fee is charged.",
    );
  });
  it("reads what a lease owes", () => {
    expect(owedLabel("1500.00")).toBe("Owes $1,500.00");
    expect(owedLabel("0.00")).toBe("Paid up");
    expect(owedLabel("-25.00")).toBe("In credit $25.00");
  });
  it("reads an open charge", () => {
    const rent: OpenCharge = {
      id: "c", due_on: "2026-10-01", kind: "rent", memo: null, amount: "1500.00", open: "1500.00",
    };
    expect(chargeLabel(rent)).toBe("2026-10-01 Rent: $1,500.00 open");
    expect(chargeLabel({ ...rent, kind: "fee", open: "20.00", amount: "50.00" })).toBe(
      "2026-10-01 Fee: $20.00 open of $50.00",
    );
    expect(chargeLabel({ ...rent, memo: "Rent due 2026-10-01" })).toBe(
      "2026-10-01 Rent due 2026-10-01: $1,500.00 open",
    );
  });
  it("reads a recurring charge", () => {
    const pet: RecurringCharge = {
      id: "r", kind: "fee", memo: "Pet rent", amount: "35.00", starts_on: "2026-01-01", ends_on: null,
    };
    expect(recurringLabel(pet)).toBe("Pet rent: $35.00 a month from 2026-01-01");
    expect(recurringLabel({ ...pet, kind: "credit", memo: "Concession", ends_on: "2026-07-01" })).toBe(
      "Concession: credit of $35.00 a month from 2026-01-01, stops 2026-07-01",
    );
  });
  it("says what the rent run charged", () => {
    expect(rentRunSummary({ charged: 1, recurring: 1 })).toBe("1 rent and 1 recurring charge charged.");
    expect(rentRunSummary({ charged: 0, recurring: 2 })).toBe("0 rents and 2 recurring charges charged.");
  });
  it("makes a new request id each time", () => {
    const one = requestId();
    expect(one).toMatch(/^[0-9a-f-]{36}$/);
    expect(requestId()).not.toBe(one);
  });
});

describe("owedTone", () => {
  it("marks owing as a caution, paid up as fine and in credit as news", () => {
    expect(["1500.00", "0.00", "-25.00"].map(owedTone)).toEqual(["caution", "positive", "info"]);
  });
});
