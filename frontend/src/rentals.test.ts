import { describe, expect, it } from "vitest";
import type { PropertyRow, UnitLine } from "./rentals";
import { hasNext, occupancy, pageLabel, rentLabel, termLabel, unitState } from "./rentals";

const page = (n: number, items: number, total: number) => ({
  items: Array.from({ length: items }, (_, i) => i), total, page: n, per_page: 50,
});

describe("pages", () => {
  it("says where a page sits among them and whether one follows", () => {
    expect([pageLabel(page(1, 50, 120)), hasNext(page(1, 50, 120))]).toEqual(["1–50 of 120", true]);
    expect([pageLabel(page(3, 20, 120)), hasNext(page(3, 20, 120))]).toEqual(["101–120 of 120", false]);
    expect(pageLabel(page(1, 0, 0))).toBe("None");
  });
});

describe("properties and units", () => {
  const row = (units: number, occupied: number): PropertyRow => ({
    id: "p1", name: "Property 1", owners: [], units, occupied, archived: false,
  });
  it("reads a property's occupancy", () => {
    expect([occupancy(row(5, 4)), occupancy(row(2, 0)), occupancy(row(0, 0))]).toEqual([
      "4 of 5 occupied", "Vacant", "No units",
    ]);
  });

  it("reads a unit's state as a badge", () => {
    const lease = { id: "l1", tenants: [], starts_on: "2026-11-01", ends_on: null, monthly_rent: "1.00", owed: "0.00" };
    const unit = (over: Partial<UnitLine>): UnitLine => ({ id: "u1", name: "Unit 1", archived: false, lease: null, ...over });
    expect(unitState(unit({}))).toEqual({ word: "Vacant", tone: "caution" });
    expect(unitState(unit({ lease: { ...lease, status: "current" } }))).toEqual({ word: "Occupied", tone: "positive" });
    expect(unitState(unit({ lease: { ...lease, status: "future" } }))).toEqual({ word: "Starts 11/01/2026", tone: "info" });
    expect(unitState(unit({ archived: true }))).toEqual({ word: "Archived", tone: "neutral" });
  });

  it("reads a lease's term and rent", () => {
    expect(termLabel({ starts_on: "2026-01-01", ends_on: null })).toBe("01/01/2026 – Month to month");
    expect(termLabel({ starts_on: "2026-01-01", ends_on: "2026-12-31" })).toBe("01/01/2026 – 12/31/2026");
    expect(rentLabel("1500.00")).toBe("$1,500.00 a month");
  });
});
