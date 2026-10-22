import { describe, expect, it } from "vitest";
import type { Ownership, PropertyRow, UnitLine } from "./rentals";
import {
  hasNext, occupancy, ownershipStates, pageLabel, percentParts, rentLabel, sharesLine, sharesTotal, termLabel, unitState,
} from "./rentals";

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
    id: "p1", name: "Property 1", address: null, owners: [], units, occupied, archived: false,
  });
  it("reads a property's occupancy", () => {
    expect([occupancy(row(5, 4)), occupancy(row(2, 0)), occupancy(row(0, 0))]).toEqual([
      "4 of 5 occupied", "Vacant", "No units",
    ]);
  });

  it("reads a unit's state as a badge", () => {
    const lease = { id: "l1", tenants: [], people: [], starts_on: "2026-11-01", ends_on: null, monthly_rent: "1.00", owed: "0.00" };
    const details = { bedrooms: null, bathrooms: null, square_feet: null, market_rent: null };
    const unit = (over: Partial<UnitLine>): UnitLine => ({ id: "u1", name: "Unit 1", archived: false, details, lease: null, ...over });
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

describe("ownership", () => {
  const owned = (id: string, starts_on: string): Ownership => ({
    id, starts_on, recorded_by: "Staff 1",
    shares: [{ account: "a1", owner: "Owner 1", percent: "60" }, { account: "a2", owner: "Owner 2", percent: "40" }],
  });

  it("reads the shares as they read", () => {
    expect(sharesLine(owned("s1", "2026-01-01").shares)).toBe("Owner 1 60% · Owner 2 40%");
  });

  it("marks the shares in force today, those to come and those replaced", () => {
    // Newest first, as the API sends them; two from the same day, the newer one first.
    const all = [owned("s4", "2026-12-01"), owned("s3", "2026-06-01"), owned("s2", "2026-06-01"), owned("s1", "2026-01-01")];
    expect(ownershipStates(all, "2026-10-19")).toEqual(["to come", "in force", "replaced", "replaced"]);
    expect(ownershipStates(all, "2026-12-01")).toEqual(["in force", "replaced", "replaced", "replaced"]);
    expect(ownershipStates(all, "2025-12-31")).toEqual(["to come", "to come", "to come", "to come"]);
    expect(ownershipStates([], "2026-10-19")).toEqual([]);
  });

  it("reads a typed percent to four decimals, above 0, at most 100, without floats", () => {
    expect(["60", "33.3333", "0.0001", "100", " 12.5 "].map(percentParts)).toEqual([600_000, 333_333, 1, 1_000_000, 125_000]);
    expect(["0", "100.0001", "101", "1.23456", "-5", "1e2", "", "abc"].map(percentParts)).toEqual(
      [null, null, null, null, null, null, null, null]);
  });

  it("adds typed shares exactly, blanks left out", () => {
    expect(sharesTotal(["33.3333", "33.3333", "33.3334"])).toEqual({ parts: 1_000_000, text: "100%", valid: true });
    expect(sharesTotal(["0.1", "0.2", ""])).toEqual({ parts: 3_000, text: "0.3%", valid: true });
    expect(sharesTotal(["60", "30.25"])).toEqual({ parts: 902_500, text: "90.25%", valid: true });
    expect(sharesTotal(["60", "x"]).valid).toBe(false);
  });
});
