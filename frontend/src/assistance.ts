import type { BadgeTone } from "./components/Badge";
import { money, sumMoney, usDate } from "./money";
import type { PaymentMethod } from "./rent";

// A housing authority's part of a lease's rent (migration 0095, the founder's review q034;
// src/pms/api/assistance.py): the authority pays its part of each month's rent, the tenant the
// rest. Money stays text from the API to the screen.

export type Authority = { id: string; name: string; leases: number; owed: string };

export type AssistanceTerms = {
  id: string;
  authority_id: string;
  authority: string;
  starts_on: string;
  // What the authority pays of each month's rent; "0.00" ended the assistance.
  amount: string;
  contract: string | null;
  recorded_by: string | null;
};

export type AuthorityCharge = {
  id: string;
  lease_id: string;
  authority_id: string;
  due_on: string;
  memo: string;
  // "late fee": the authority's late payment fee on a rent's part (migration 0116).
  source: "rent" | "staff" | "late fee";
  charged: string;
  // What a lease's end or staff's credits added to it or took off it (signed).
  adjusted: string;
  paid: string;
  // Below 0 when the authority paid more than it now owes.
  open: string;
};

export type Certification = {
  id: string;
  kind: "move-in" | "annual" | "interim";
  effective_on: string;
  household_size: number;
  annual_income: string;
  // The day the next annual certification is due.
  next_due_on: string;
  recorded_by: string | null;
};

export type Inspection = {
  id: string;
  authority_id: string;
  authority: string;
  inspected_on: string;
  result: "passed" | "failed";
  // Passed: the next is due by it. Failed: the home must pass a reinspection by it.
  due_on: string;
  recorded_by: string | null;
};

// Keeping the lease in compliance (migration 0108, the founder's review q143): where the latest
// certification and inspection in effect today stand, in the compliance report's words.
export type Compliance = {
  recertify_by: string | null;
  recertification: string;
  inspect_by: string | null;
  inspection: string;
  certifications: Certification[];
  inspections: Inspection[];
};

export type LeaseAssistance = {
  in_force: AssistanceTerms | null;
  monthly_rent: string;
  tenant_part: string;
  terms: AssistanceTerms[];
  charges: AuthorityCharge[];
  owed: string;
  compliance: Compliance;
};

// A standing's badge: current is fine, due soon or a reinspection waiting is caution, nothing on
// file asks for one, and overdue is late.
export function complianceTone(standing: string): BadgeTone {
  if (standing === "current") return "positive";
  if (standing === "overdue" || standing === "failed, reinspection overdue") return "danger";
  return "caution";
}

// "Recertify by 07/01/2027", or what to do when none is on file.
export function dueLine(what: "Recertify" | "Inspect", by: string | null): string {
  if (by === null) return what === "Recertify" ? "No income certification on file" : "No inspection on file";
  return `${what} by ${usDate(by)}`;
}

export type AssistedLease = {
  lease_id: string;
  home: string;
  monthly: string | null;
  contract: string | null;
  owed: string;
  open_charges: AuthorityCharge[];
  // A payment hold on the lease in force today (an eviction filed): from when, and why.
  hold_since: string | null;
  hold_reason: string | null;
};

// The authority's late payment fee in force today (migration 0116, q034).
export type AuthorityLateFee = { starts_on: string; grace_days: number; flat_fee: string };

// One of the authority's payments, and the day the bank returned it, once it bounced.
export type AuthorityPayment = {
  transfer_id: string;
  received_on: string;
  amount: string;
  reference: string | null;
  method: PaymentMethod | null;
  bounced_on: string | null;
};

export type AuthorityPage = {
  id: string;
  name: string;
  leases: AssistedLease[];
  owed: string;
  late_fee: AuthorityLateFee | null;
  payments: AuthorityPayment[];
};

// "$25.00 when its part of a rent is still open 5 days after it was due", or none.
export function lateFeeLine(fee: AuthorityLateFee | null): string {
  if (fee === null) return "No late payment fee.";
  const days = fee.grace_days === 1 ? "1 day" : `${fee.grace_days} days`;
  return `A late payment fee of ${money(fee.flat_fee)} when its part of a rent is still open ${days} after it was due (from ${usDate(fee.starts_on)}).`;
}

// "From 11/01/2026: Synthetic Housing Authority pays $800.00 a month (contract HAP-0001)".
export function termsLine(terms: AssistanceTerms): string {
  const from = `From ${usDate(terms.starts_on)}: `;
  if (Number(terms.amount) === 0) return `${from}no assistance (${terms.authority})`;
  const contract = terms.contract ? ` (contract ${terms.contract})` : "";
  return `${from}${terms.authority} pays ${money(terms.amount)} a month${contract}`;
}

// How today's rent splits: "Synthetic Housing Authority pays $800.00 of the $1,000.00 rent; the
// tenant pays $200.00".
export function splitLine(assistance: LeaseAssistance): string {
  const terms = assistance.in_force;
  if (terms === null) return `The tenant pays all of the ${money(assistance.monthly_rent)} rent`;
  const paid = sumMoney([assistance.monthly_rent, `-${assistance.tenant_part}`]);
  return `${terms.authority} pays ${money(paid)} of the ${money(assistance.monthly_rent)} rent; the tenant pays ${money(assistance.tenant_part)}`;
}

// The first of next month (UTC), as YYYY-MM-DD: the first month assistance can usually start.
export function nextFirst(today: Date): string {
  const first = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() + 1, 1));
  return first.toISOString().slice(0, 10);
}

// A payment's lines: each lease given an amount above nothing.
export function paymentLines(amounts: Record<string, string>): { lease_id: string; amount: string }[] {
  return Object.entries(amounts)
    .map(([lease_id, amount]) => ({ lease_id, amount: amount.trim() }))
    .filter((line) => line.amount !== "" && Number(line.amount) > 0);
}

export function paymentTotal(amounts: Record<string, string>): string {
  return sumMoney(paymentLines(amounts).map((line) => line.amount));
}
