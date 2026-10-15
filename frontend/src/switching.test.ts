import { describe, expect, it } from "vitest";
import { ROSTER, countedLabel, expectedBank } from "./switching";

describe("switching", () => {
  it("lists the roster files in the order they go in", () => {
    expect(ROSTER.map((r) => r.kind)).toEqual([
      "owners", "vendors", "properties", "units", "tenants", "leases", "charges", "bills",
    ]);
    // Unpaid bills come over with their old dates, and say what to do with one set aside there.
    expect(ROSTER.at(-1)?.columns).toContain("Approved by and Approved on");
    expect(ROSTER.at(-1)?.columns).toContain("carry its money over on the owner's line");
  });

  it("says what a file added and kept", () => {
    expect(countedLabel({ added: 3, kept: 0 })).toBe("3 added.");
    expect(countedLabel({ added: 0, kept: 2 })).toBe("0 added; 2 already here.");
  });

  it("finds the bank balance to expect in the tie-out", () => {
    expect(expectedBank([
      { line: 1, item: "PMC", detail: "Synthetic PMC", amount: null },
      { line: 9, item: "bank balance to expect at the cutover", detail: "less items", amount: "2625.00" },
    ])).toBe("2625.00");
    expect(expectedBank([])).toBeNull();
  });
});
