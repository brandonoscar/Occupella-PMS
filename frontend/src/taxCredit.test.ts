import { describe, expect, it } from "vitest";
import { HOUSEHOLDS, bedroomsLabel, designationLine, grossLine, incomeTone, rentTone } from "./taxCredit";

describe("tax credit", () => {
  it("names the table's household sizes and a unit's bedrooms", () => {
    expect(HOUSEHOLDS).toEqual([
      "1 person", "2 people", "3 people", "4 people", "5 people", "6 people", "7 people", "8 people",
    ]);
    expect([0, 1, 2].map(bedroomsLabel)).toEqual(["no bedroom", "1 bedroom", "2 bedrooms"]);
  });

  it("says a unit's designation, or the day it ended", () => {
    expect(designationLine({ starts_on: "2026-01-01", income_percent: 60, bedrooms: 1, utility_allowance: "50.00" }))
      .toBe("From 01/01/2026: 60% of median, 1 bedroom, utility allowance $50.00");
    expect(designationLine({ starts_on: "2027-01-01", income_percent: null, bedrooms: null, utility_allowance: null }))
      .toBe("From 01/01/2027: not a tax credit unit");
  });

  it("marks each test's words", () => {
    expect(incomeTone("qualified")).toBe("positive");
    expect(incomeTone("over the limit at move-in")).toBe("danger");
    expect(incomeTone("over 140%: next available unit rule")).toBe("caution");
    expect(incomeTone("no certification on file")).toBe("caution");
    expect(incomeTone("vacant")).toBe("neutral");
    expect(rentTone("within the limit")).toBe("positive");
    expect(rentTone("over by 210.00")).toBe("danger");
    expect(rentTone("no income limits on file")).toBe("caution");
  });

  it("says the gross rent and the most it may be", () => {
    const lease = {
      income_percent: 60, bedrooms: 1, certification: "current", income_test: "qualified",
      max_gross_rent: "964", gross_rent: "950.00", rent_test: "within the limit",
    };
    expect(grossLine(lease)).toBe("$950.00, the rent and the utility allowance; at most $964.00");
    expect(grossLine({ ...lease, max_gross_rent: null })).toBe("$950.00, the rent and the utility allowance");
  });
});
