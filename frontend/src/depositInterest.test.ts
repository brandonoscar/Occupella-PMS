import { describe, expect, it } from "vitest";
import { paidLine, rateLine } from "./depositInterest";

describe("deposit interest", () => {
  it("says a property's rate, none, or no terms", () => {
    expect(rateLine({ starts_on: "2026-01-01", annual_percent: "1.5" })).toBe("1.5% a year (from 01/01/2026)");
    expect(rateLine({ starts_on: "2026-07-01", annual_percent: "0" })).toBe("None from 07/01/2026");
    expect(rateLine(null)).toBe("No deposit interest");
  });

  it("says what a payment was and how it was paid", () => {
    expect(paidLine({ from_on: "2025-01-01", through_on: "2025-12-31", amount: "12.00", paid_as: "rent_credit" }))
      .toBe("$12.00 credited against the rent, 01/01/2025 to 12/31/2025");
    expect(paidLine({ from_on: "2026-01-01", through_on: "2026-01-04", amount: "0.13", paid_as: "deposit" }))
      .toBe("$0.13 added to the deposit, 01/01/2026 to 01/04/2026");
  });
});
