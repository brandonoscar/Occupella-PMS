// Staff's move-outs as the API sends them (src/pms/api/moveout.py), and how they read.
import { fromCents, toCents } from "./reconcile";
import { usDate, usDatesIn } from "./money";

// home: the home of a lease the tenants moved from ("Birch Flats, Unit A"); null for the lease's own.
export type OpenCharge = {
  id: string; due_on: string; kind: "rent" | "fee"; memo: string | null; open: string; home?: string | null;
};
// percent: their share when the property's ownership shares split what is kept (migration 0094).
export type OwnerAccount = { id: string; owner: string; percent: string | null };
export type Settled = { held: string; kept: string; returned: string; returned_on: string };
// A fee charged at move-out (or with a receipt on file), and its receipts (migration 0120).
export type ReceiptFile = { id: string; title: string };
export type Fee = { id: string; due_on: string; memo: string | null; amount: string; receipts: ReceiptFile[] };
export type MoveOut = {
  lease_id: string;
  property: string;
  unit: string;
  tenants: string[];
  // Each tenant's id, in the order of their names.
  tenant_ids: string[];
  starts_on: string;
  ends_on: string | null;
  owed: string;
  // What the leases its tenants moved from still owe (migration 0091): the deposits pay it first.
  carried: string;
  held: string;
  open_charges: OpenCharge[];
  owner_accounts: OwnerAccount[];
  settled: Settled | null;
  // The day the deposit and its statement are due back by the strictest deadline found
  // (docs/law.md); null before the lease has a last day, or once it was returned.
  return_by: string | null;
  fees: Fee[];
};

// California asks for the receipts of repairs and cleaning over $125 with the statement, the
// strictest found (docs/law.md): a fee over it with none on file says so.
const RECEIPT_OVER = "125.00";

export function needsReceipt(fee: Fee): boolean {
  return fee.receipts.length === 0 && toCents(fee.amount) > toCents(RECEIPT_OVER);
}

export function feeLabel(fee: Fee): string {
  return `${usDatesIn(fee.memo ?? "Fee")}, due ${usDate(fee.due_on)}`;
}

// The day the deposit is due back, as a badge: caution until then, danger once it has passed.
export function returnBy(lease: MoveOut, today: string): { label: string; tone: "caution" | "danger" } | null {
  if (!lease.return_by) return null;
  return lease.return_by < today
    ? { label: `Was due back ${usDate(lease.return_by)}`, tone: "danger" }
    : { label: `Return by ${usDate(lease.return_by)}`, tone: "caution" };
}

export function statementPath(leaseId: string, tenantId: string): string {
  return `/api/move-out/${encodeURIComponent(leaseId)}/statement?tenant_id=${encodeURIComponent(tenantId)}`;
}

export type StatementSent = { emailed: { name: string; to: string }[]; without_email: string[] };

// "Emailed Tenant 1 at t@example.com; no email on file for Tenant 2."
export function sentLine(sent: StatementSent): string {
  const to = sent.emailed.map((e) => `${e.name} at ${e.to}`).join(", ");
  const none = sent.without_email.join(", ");
  if (!to) return `No email on file for ${none}: add one on their page.`;
  return `Emailed ${to}${none ? `; no email on file for ${none}` : ""}.`;
}

// What returning the deposits now would keep for what the lease owes, and give back.
export function preview(lease: MoveOut): { kept: string; returned: string } {
  const held = toCents(lease.held);
  const kept = Math.min(toCents(lease.owed) + toCents(lease.carried), held);
  return { kept: fromCents(kept), returned: fromCents(held - kept) };
}

export function leaseLabel(lease: MoveOut): string {
  return `${lease.property} / ${lease.unit}: ${lease.tenants.join(", ")}`;
}

export function chargeLabel(charge: OpenCharge): string {
  return `${usDate(charge.due_on)} ${charge.kind}${charge.memo ? `: ${usDatesIn(charge.memo)}` : ""}`;
}

// The lease ended before `today` (YYYY-MM-DD, UTC), so its deposit may go back.
export function hasEnded(lease: MoveOut, today: string): boolean {
  return lease.ends_on !== null && lease.ends_on < today;
}

// Where a lease's move-out stands, as a badge: its deposit returned, waiting to go back once the
// lease has ended, or the lease still running (to a set last day, or month to month).
export function moveOutStatus(
  lease: MoveOut,
  today: string,
): { label: string; tone: "positive" | "caution" | "info" | "neutral" } {
  if (lease.settled) return { label: "Deposit returned", tone: "positive" };
  if (hasEnded(lease, today)) return { label: "Ended, deposit to return", tone: "caution" };
  if (lease.ends_on !== null) return { label: `Ends ${usDate(lease.ends_on)}`, tone: "info" };
  return { label: "In effect", tone: "neutral" };
}
