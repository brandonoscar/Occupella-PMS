import { describe, expect, it } from "vitest";
import type { Dashboard } from "./dashboard";
import { countChange, moneyChange, pointsChange, tiles } from "./dashboard";

const QUIET: Dashboard = {
  on: "2026-10-13", month_ago: "2026-09-13",
  occupancy: { percent: null, then_percent: null },
  rent: { collected: "0.00", due: "0.00", then_collected: "0.00", then_due: "0.00" },
  past_due: { total: "0.00", tenants: 0, then_total: "0.00", then_tenants: 0 },
  leases_ending: { within_60: 0, then_within_60: 0 },
  vacant: { units: 0, average_days: null, then_units: 0, then_average_days: null },
  next_draw: { on: null, total: null, last_total: null },
  unreconciled: { accounts: 0, then_accounts: 0 },
};

describe("changes from a month before", () => {
  it("says up, down or the same, with the day it compares with", () => {
    expect(countChange(3, 1, "2026-09-13")).toBe("Up 2 from 09/13/2026");
    expect(countChange(1, 3, "2026-09-13")).toBe("Down 2 from 09/13/2026");
    expect(countChange(2, 2, "2026-09-13")).toBe("Same as 09/13/2026");
  });

  it("works money out in cents, never as a float", () => {
    expect(moneyChange("0.30", "0.10", "2026-09-13")).toBe("Up $0.20 from 09/13/2026");
    expect(moneyChange("1750.50", "2000.50", "2026-09-13")).toBe("Down $250.00 from 09/13/2026");
    expect(moneyChange("12.00", "12.00", "2026-09-13")).toBe("Same as 09/13/2026");
  });

  it("gives a percent's change in points to one place, one point as one", () => {
    expect(pointsChange("92.5", "90.0", "2026-09-13")).toBe("Up 2.5 points from 09/13/2026");
    expect(pointsChange("89.0", "90.0", "2026-09-13")).toBe("Down 1.0 point from 09/13/2026");
    expect(pointsChange("90.0", "90.0", "2026-09-13")).toBe("Same as 09/13/2026");
    expect(pointsChange("90.0", null, "2026-09-13")).toBe("No units on 09/13/2026");
  });
});

describe("tiles", () => {
  it("shows no fixed counts: each figure changes, a quiet company's zeros as zeros", () => {
    expect(tiles(QUIET).map((tile) => [tile.label, tile.value, tile.detail])).toEqual([
      ["Occupancy", "No units", "Add a property and its units"],
      ["Rent collected this month", "$0.00 of $0.00", "$0.00 of $0.00 by 09/13/2026"],
      ["Past due", "$0.00", "0 tenants; Same as 09/13/2026"],
      ["Leases ending in 60 days", "0", "Same as 09/13/2026"],
      ["Vacant units", "0", "Same as 09/13/2026"],
      ["Next draw", "No draw day", "Set the draw day in Company setup"],
      ["Accounts not reconciled", "0", "Same as 09/13/2026"],
    ]);
    expect(tiles(QUIET).find((t) => t.label === "Next draw")?.href).toBe("/settings/setup");
  });

  it("counts one tenant as one, and leaves out the last draws before there were any", () => {
    const [, , owing, , vacant, draw] = tiles({
      ...QUIET,
      past_due: { total: "100.00", tenants: 1, then_total: "0.00", then_tenants: 0 },
      vacant: { units: 1, average_days: 1, then_units: 0, then_average_days: null },
      next_draw: { on: "2026-11-05", total: "0.00", last_total: null },
    });
    expect(owing?.detail).toBe("1 tenant; Up $100.00 from 09/13/2026");
    expect(vacant?.value).toBe("1, 1 day vacant on average");
    expect([draw?.value, draw?.detail, draw?.href]).toEqual(["11/05/2026", "$0.00 available to pay now", "/owner-draws"]);
  });
});
