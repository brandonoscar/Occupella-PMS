import { describe, expect, it } from "vitest";
import type { MoveOut } from "./moveout";
import { chargeLabel, hasEnded, leaseLabel, preview } from "./moveout";

const LEASE: MoveOut = {
  lease_id: "l1", property: "Property 1", unit: "Unit 1", tenants: ["Tenant A", "Tenant B"],
  starts_on: "2026-01-01", ends_on: "2026-06-30", owed: "350.00", held: "1200.00",
  open_charges: [], owner_accounts: [], settled: null,
};

describe("moveout", () => {
  it("keeps what is owed, up to what is held, and gives back the rest", () => {
    expect(preview(LEASE)).toEqual({ kept: "350.00", returned: "850.00" });
    expect(preview({ ...LEASE, owed: "1500.10" })).toEqual({ kept: "1200.00", returned: "0.00" });
    expect(preview({ ...LEASE, owed: "0.00" })).toEqual({ kept: "0.00", returned: "1200.00" });
  });

  it("reads a lease and its charges", () => {
    expect(leaseLabel(LEASE)).toBe("Property 1 / Unit 1: Tenant A, Tenant B");
    expect(chargeLabel({ id: "c", due_on: "2026-06-30", kind: "fee", memo: "Cleaning", open: "5.00" }))
      .toBe("2026-06-30 fee: Cleaning");
    expect(chargeLabel({ id: "c", due_on: "2026-06-01", kind: "rent", memo: null, open: "5.00" }))
      .toBe("2026-06-01 rent");
  });

  it("says when a lease has ended", () => {
    expect(hasEnded(LEASE, "2026-07-01")).toBe(true);
    expect(hasEnded(LEASE, "2026-06-30")).toBe(false);
    expect(hasEnded({ ...LEASE, ends_on: null }, "2026-07-01")).toBe(false);
  });
});
