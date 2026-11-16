// Staff's move-outs as the API sends them (src/pms/api/moveout.py), and how they read.
import type { OwnerHeld } from "./ownerHeld";
import { fromCents, toCents } from "./reconcile";
import { money, usDate, usDatesIn } from "./money";

// home: the home of a lease the tenants moved from ("Birch Flats, Unit A"); null for the lease's own.
export type OpenCharge = {
  id: string; due_on: string; kind: "rent" | "fee"; memo: string | null; open: string; home?: string | null;
};
// percent: their share when the property's ownership shares split what is kept (migration 0094).
export type OwnerAccount = { id: string; owner: string; percent: string | null };
// prepaid_used, prepaid_returned: what the tenants' prepaid rent paid of what was owed, and what
// of it went back to them (migration 20261115000240, M-072).
export type Settled = {
  held: string; kept: string; returned: string; returned_on: string;
  prepaid_used?: string; prepaid_returned?: string;
};
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
  // The prepaid rent its tenants hold: the return uses it first for what is owed, then pays back
  // the rest (M-072).
  prepaid?: string;
  open_charges: OpenCharge[];
  owner_accounts: OwnerAccount[];
  settled: Settled | null;
  // The day the deposit and its statement are due back by the strictest deadline found
  // (docs/law.md); null before the lease has a last day, or once it was returned.
  return_by: string | null;
  fees: Fee[];
  // Its tenants' deposits the owner holds, outside trust: the owner returns them.
  owner_held?: OwnerHeld[];
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

// What returning the deposits now would use of the tenants' prepaid rent for what the lease owes
// (first, M-072), and give back of it.
export function prepaidPreview(lease: MoveOut): { used: string; back: string } {
  const prepaid = toCents(lease.prepaid ?? "0.00");
  const used = Math.min(toCents(lease.owed) + toCents(lease.carried), prepaid);
  return { used: fromCents(used), back: fromCents(prepaid - used) };
}

// What returning the deposits now would keep of them for what the lease owes once its tenants'
// prepaid rent paid what it could, and give back.
export function preview(lease: MoveOut): { kept: string; returned: string } {
  const held = toCents(lease.held);
  const owing = toCents(lease.owed) + toCents(lease.carried) - toCents(prepaidPreview(lease).used);
  const kept = Math.min(owing, held);
  return { kept: fromCents(kept), returned: fromCents(held - kept) };
}

// The question asked before a return: what it keeps and pays back, prepaid rent included.
export function returnQuestion(lease: MoveOut, on: string): string {
  const next = preview(lease);
  const prepaid = prepaidPreview(lease);
  const after = " This records what you paid at the bank; it sends no money or email.";
  if (toCents(lease.prepaid ?? "0.00") === 0) {
    return `Return the deposit on ${usDate(on)}: keep ${money(next.kept)} for what the lease owes and`
      + ` pay ${money(next.returned)} back to the tenants?${after}`;
  }
  return `Return the deposit on ${usDate(on)}: use ${money(prepaid.used)} of the tenants' prepaid rent`
    + ` and keep ${money(next.kept)} of the deposits for what the lease owes, and pay`
    + ` ${money(next.returned)} of the deposits and ${money(prepaid.back)} of prepaid rent back to the`
    + ` tenants?${after}`;
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

// What a return said it paid back, prepaid rent included (M-072).
export function returnedLine(done: { returned: string; prepaid_returned?: string }): string {
  const prepaid = toCents(done.prepaid_returned ?? "0.00");
  const back = prepaid > 0 ? ` and ${money(fromCents(prepaid))} of prepaid rent` : "";
  return `Returned ${money(done.returned)}${back} to the tenants; the rest paid what the lease owed.`;
}

// What a settled return did with the tenants' prepaid rent, when it held any; "" otherwise.
export function settledPrepaidLine(settled: Settled): string {
  const used = toCents(settled.prepaid_used ?? "0.00");
  const back = toCents(settled.prepaid_returned ?? "0.00");
  if (used + back === 0) return "";
  return ` Their prepaid rent paid ${money(fromCents(used))} of what was owed, and`
    + ` ${money(fromCents(back))} of it went back to them.`;
}
