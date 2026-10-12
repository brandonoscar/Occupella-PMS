// Staff's move-outs as the API sends them (src/pms/api/moveout.py), and how they read.
import { fromCents, toCents } from "./reconcile";

export type OpenCharge = { id: string; due_on: string; kind: "rent" | "fee"; memo: string | null; open: string };
export type OwnerAccount = { id: string; owner: string };
export type Settled = { held: string; kept: string; returned: string; returned_on: string };
export type MoveOut = {
  lease_id: string;
  property: string;
  unit: string;
  tenants: string[];
  starts_on: string;
  ends_on: string | null;
  owed: string;
  held: string;
  open_charges: OpenCharge[];
  owner_accounts: OwnerAccount[];
  settled: Settled | null;
};

// What returning the deposits now would keep for what the lease owes, and give back.
export function preview(lease: MoveOut): { kept: string; returned: string } {
  const held = toCents(lease.held);
  const kept = Math.min(toCents(lease.owed), held);
  return { kept: fromCents(kept), returned: fromCents(held - kept) };
}

export function leaseLabel(lease: MoveOut): string {
  return `${lease.property} / ${lease.unit}: ${lease.tenants.join(", ")}`;
}

export function chargeLabel(charge: OpenCharge): string {
  return `${charge.due_on} ${charge.kind}${charge.memo ? `: ${charge.memo}` : ""}`;
}

// The lease ended before `today` (YYYY-MM-DD, UTC), so its deposit may go back.
export function hasEnded(lease: MoveOut, today: string): boolean {
  return lease.ends_on !== null && lease.ends_on < today;
}
