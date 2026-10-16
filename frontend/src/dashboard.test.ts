import { describe, expect, it } from "vitest";
import type { Dashboard } from "./dashboard";
import { tiles } from "./dashboard";

const QUIET: Dashboard = {
  on: "2026-10-13", outstanding: "0.00", leases_owing: 0, open_work_orders: 0,
  leases_ending: { within_30: 0, within_60: 0, within_90: 0 }, vacant_units: 0, applications_waiting: 0,
};

describe("tiles", () => {
  it("counts one lease as one and many as many, and adds up the leases ending", () => {
    const [owing, , ending] = tiles({
      ...QUIET, outstanding: "1500.00", leases_owing: 1,
      leases_ending: { within_30: 2, within_60: 1, within_90: 0 },
    });
    expect([owing?.value, owing?.detail]).toEqual(["$1,500.00", "1 lease owes"]);
    expect([ending?.value, ending?.detail]).toEqual(["3", "2 in 30 days, 1 in 60, 0 in 90"]);
    expect(tiles({ ...QUIET, leases_owing: 2 })[0]?.detail).toBe("2 leases owe");
  });

  it("shows a quiet company's zeros as zeros", () => {
    expect(tiles(QUIET).map((tile) => tile.value)).toEqual(["$0.00", "0", "0", "0", "0"]);
  });
});
