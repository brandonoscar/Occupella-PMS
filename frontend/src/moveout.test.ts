import { describe, expect, it } from "vitest";
import type { MoveOut } from "./moveout";
import { chargeLabel, hasEnded, leaseLabel, moveOutStatus, preview } from "./moveout";

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
      .toBe("06/30/2026 fee: Cleaning");
    expect(chargeLabel({ id: "c", due_on: "2026-06-01", kind: "rent", memo: null, open: "5.00" }))
      .toBe("06/01/2026 rent");
  });

  it("says when a lease has ended", () => {
    expect(hasEnded(LEASE, "2026-07-01")).toBe(true);
    expect(hasEnded(LEASE, "2026-06-30")).toBe(false);
    expect(hasEnded({ ...LEASE, ends_on: null }, "2026-07-01")).toBe(false);
  });
});

describe("moveOutStatus", () => {
  it("says where a lease's move-out stands", () => {
    const today = "2026-07-10";
    expect(moveOutStatus({ ...LEASE, ends_on: null }, today)).toEqual({ label: "In effect", tone: "neutral" });
    expect(moveOutStatus({ ...LEASE, ends_on: "2026-07-31" }, today)).toEqual({ label: "Ends 07/31/2026", tone: "info" });
    // Its last day is still in effect; the day after, the deposit may go back.
    expect(moveOutStatus({ ...LEASE, ends_on: "2026-07-10" }, today)).toEqual({ label: "Ends 07/10/2026", tone: "info" });
    expect(moveOutStatus(LEASE, today)).toEqual({ label: "Ended, deposit to return", tone: "caution" });
    const settled = { held: "1200.00", kept: "350.00", returned: "850.00", returned_on: "2026-07-05" };
    expect(moveOutStatus({ ...LEASE, settled }, today)).toEqual({ label: "Deposit returned", tone: "positive" });
  });
});
