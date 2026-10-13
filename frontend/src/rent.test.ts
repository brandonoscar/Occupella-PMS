import { describe, expect, it } from "vitest";
import type { OpenCharge, RecurringCharge } from "./rent";
import {
  bounceSummary,
  chargeLabel,
  owedLabel,
  paymentSummary,
  recurringLabel,
  rentRunSummary,
  requestId,
} from "./rent";

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
      "The $1,500.00 payment is owed again.",
    );
    expect(bounceSummary("1500.00", { transfers: ["r", "s"], nsf_fee: "35.00" })).toBe(
      "The $1,500.00 payment is owed again, with a $35.00 NSF fee.",
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
