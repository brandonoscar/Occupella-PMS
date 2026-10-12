import { describe, expect, it } from "vitest";
import type { ReportRow } from "./reconcile";
import {
  difference,
  figures,
  formatOf,
  fromCents,
  items,
  lastDayOf,
  lineLabel,
  sum,
  toCents,
  transferLabel,
} from "./reconcile";

const ROWS: ReportRow[] = [
  { line: 1, item: "PMC", detail: "Synthetic PMC", amount: null },
  { line: 4, item: "bank balance (statements)", detail: null, amount: "4075.00" },
  { line: 9, item: "difference: adjusted bank balance - trust journal", detail: null, amount: "65.00" },
  { line: 10, item: "deposit in transit", detail: "2026-01-31 Late fee", amount: "50.00" },
];

describe("reconcile", () => {
  it("adds money exactly, in cents", () => {
    expect(toCents("1,234.5")).toBe(123450);
    expect(toCents("-0.05")).toBe(-5);
    expect(toCents("7")).toBe(700);
    expect(fromCents(-5)).toBe("-0.05");
    expect(fromCents(123450)).toBe("1234.50");
    expect(sum(["0.10", "0.20"])).toBe("0.30");
    expect(sum(["2750.00", "-240.00", "-0.01"])).toBe("2509.99");
    expect(sum([])).toBe("0.00");
    expect(() => toCents("1.234")).toThrow("not an amount");
  });

  it("reads the report's figures, items and difference", () => {
    expect(figures(ROWS)).toEqual({
      "bank balance (statements)": "4075.00",
      "difference: adjusted bank balance - trust journal": "65.00",
    });
    expect(items(ROWS).map((r) => r.item)).toEqual(["deposit in transit"]);
    expect(difference(ROWS)).toBe("65.00");
    expect(difference([])).toBeNull();
  });

  it("tells a statement's format by its file name", () => {
    expect(formatOf("january.OFX")).toBe("ofx");
    expect(formatOf("january.qfx")).toBe("ofx");
    expect(formatOf("january.csv")).toBe("csv");
    expect(formatOf("january.txt")).toBe("csv");
  });

  it("reads lines, transfers and a period's last day", () => {
    expect(lineLabel({ id: "l", posted_on: "2026-01-05", amount: "2750.00", description: "Deposit", reference: "D1" }))
      .toBe("2026-01-05 Deposit (D1): $2,750.00");
    expect(lineLabel({ id: "l", posted_on: "2026-01-05", amount: "-5.00", description: "", reference: null }))
      .toBe("2026-01-05 No description: -$5.00");
    expect(transferLabel({ id: "t", on: "2026-01-04", amount: "1500.00", memo: "Rent" }))
      .toBe("2026-01-04 Rent: $1,500.00");
    expect(transferLabel({ id: "t", on: "2026-01-04", amount: "1.00", memo: null }))
      .toBe("2026-01-04 No memo: $1.00");
    expect(lastDayOf("2026-02-01T00:00:00Z")).toBe("2026-01-31");
  });
});
