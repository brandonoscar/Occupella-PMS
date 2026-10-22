import { describe, expect, it } from "vitest";
import type { MoveOut } from "./moveout";
import {
  chargeLabel,
  feeLabel,
  hasEnded,
  leaseLabel,
  moveOutStatus,
  needsReceipt,
  preview,
  returnBy,
  sentLine,
  statementPath,
} from "./moveout";

const LEASE: MoveOut = {
  lease_id: "l1", property: "Property 1", unit: "Unit 1", tenants: ["Tenant A", "Tenant B"], tenant_ids: ["t1", "t2"],
  starts_on: "2026-01-01", ends_on: "2026-06-30", owed: "350.00", carried: "0.00", held: "1200.00",
  open_charges: [], owner_accounts: [], settled: null, return_by: "2026-07-14", fees: [],
};

describe("moveout", () => {
  it("keeps what is owed, up to what is held, and gives back the rest", () => {
    expect(preview(LEASE)).toEqual({ kept: "350.00", returned: "850.00" });
    expect(preview({ ...LEASE, owed: "1500.10" })).toEqual({ kept: "1200.00", returned: "0.00" });
    expect(preview({ ...LEASE, owed: "0.00" })).toEqual({ kept: "0.00", returned: "1200.00" });
    // What the lease its tenants moved from still owes is kept too (migration 0091).
    expect(preview({ ...LEASE, carried: "500.00" })).toEqual({ kept: "850.00", returned: "350.00" });
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

  it("reads when the deposit is due back, which fees need a receipt, and what was emailed", () => {
    expect(returnBy(LEASE, "2026-07-14")).toEqual({ label: "Return by 07/14/2026", tone: "caution" });
    expect(returnBy(LEASE, "2026-07-15")).toEqual({ label: "Was due back 07/14/2026", tone: "danger" });
    expect(returnBy({ ...LEASE, return_by: null }, "2026-07-15")).toBeNull();

    const fee = { id: "f1", due_on: "2026-06-30", memo: "Carpet cleaning", amount: "125.01", receipts: [] };
    expect(needsReceipt(fee)).toBe(true);
    expect(needsReceipt({ ...fee, amount: "125.00" })).toBe(false);
    expect(needsReceipt({ ...fee, receipts: [{ id: "d1", title: "Invoice" }] })).toBe(false);
    expect(feeLabel(fee)).toBe("Carpet cleaning, due 06/30/2026");
    expect(feeLabel({ ...fee, memo: null })).toBe("Fee, due 06/30/2026");

    expect(statementPath("l 1", "t/1")).toBe("/api/move-out/l%201/statement?tenant_id=t%2F1");
    expect(sentLine({ emailed: [{ name: "Tenant A", to: "a@example.com" }], without_email: [] }))
      .toBe("Emailed Tenant A at a@example.com.");
    expect(sentLine({ emailed: [{ name: "Tenant A", to: "a@example.com" }], without_email: ["Tenant B"] }))
      .toBe("Emailed Tenant A at a@example.com; no email on file for Tenant B.");
    expect(sentLine({ emailed: [], without_email: ["Tenant B"] }))
      .toBe("No email on file for Tenant B: add one on their page.");
  });
});
