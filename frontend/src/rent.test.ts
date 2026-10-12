import { describe, expect, it } from "vitest";
import type { OpenCharge } from "./rent";
import { chargeLabel, owedLabel, paymentSummary, requestId } from "./rent";

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
  it("makes a new request id each time", () => {
    const one = requestId();
    expect(one).toMatch(/^[0-9a-f-]{36}$/);
    expect(requestId()).not.toBe(one);
  });
});
