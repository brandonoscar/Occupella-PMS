import { describe, expect, it } from "vitest";
import type { YearEndData } from "./yearEnd";
import { groupYearEnd, lastYear, partLabel } from "./yearEnd";

const PARTS = ["opening balance", "collected from tenants", "management fees", "closing balance"];

const PACKAGE: YearEndData = {
  title: "Year-end package, 2025",
  columns: ["line", "property", "item", "amount"],
  rows: [
    ["1", "Synthetic PMC", "PMC", null],
    ["2", "Owner 1", "owner", null],
    ["3", "2025, January 1 to December 31 UTC", "year", null],
    ...PARTS.map((item, n) => [String(4 + n), "Property 1", item, ["100.00", "1500.00", "-150.00", "1450.00"][n] ?? null]),
    ...PARTS.map((item, n) => [String(8 + n), "Property 2", item, ["0.00", "900.00", "-90.00", "810.00"][n] ?? null]),
    ...PARTS.map((item, n) => [String(12 + n), null, `all properties: ${item}`, ["100.00", "2400.00", "-240.00", "2260.00"][n] ?? null]),
    ["16", null, "1099-MISC rents (box 1)", "2400.00"],
  ],
};

describe("yearEnd", () => {
  it("groups the package by property, then the totals and the 1099-MISC rents", () => {
    const grouped = groupYearEnd(PACKAGE);
    expect(grouped.year).toBe("2025, January 1 to December 31 UTC");
    expect(grouped.properties.map((p) => p.name)).toEqual(["Property 1", "Property 2"]);
    expect(grouped.properties[1]?.parts).toEqual([
      { item: "opening balance", amount: "0.00" },
      { item: "collected from tenants", amount: "900.00" },
      { item: "management fees", amount: "-90.00" },
      { item: "closing balance", amount: "810.00" },
    ]);
    expect(grouped.totals.map((t) => t.item)).toEqual(PARTS);
    expect(grouped.totals[1]?.amount).toBe("2400.00");
    expect(grouped.rents).toBe("2400.00");
  });
  it("refuses a package without the columns it reads", () => {
    expect(() => groupYearEnd({ ...PACKAGE, columns: ["line", "item", "amount"] })).toThrow("no property column");
  });
  it("picks last year and names a part", () => {
    expect(lastYear(new Date("2026-01-01T00:30:00Z"))).toBe(2025);
    expect(lastYear(new Date("2026-12-31T23:59:59Z"))).toBe(2025);
    expect(partLabel("collected from tenants")).toBe("Collected from tenants");
  });
});
