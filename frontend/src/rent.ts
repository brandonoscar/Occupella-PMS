// Staff's rent work as the API sends it (src/pms/api/rent.py), and how it reads.
import { day, money, usDate } from "./money";

export type OpenCharge = {
  id: string;
  due_on: string;
  kind: "rent" | "fee";
  memo: string | null;
  amount: string;
  open: string;
};
export type Paid = {
  transfer_id: string;
  received_on: string;
  amount: string;
  reference: string | null;
  bounced: boolean;
};
export type LeaseTenant = { id: string; name: string; prepaid: string; deposit: string };
// A fee the lease owes every month besides its rent, or a credit it gets every month. ends_on is
// the first due date it is no longer charged for.
export type RecurringCharge = {
  id: string;
  kind: "fee" | "credit";
  memo: string;
  amount: string;
  starts_on: string;
  ends_on: string | null;
};
export type RentChange = {
  id: string;
  effective_on: string;
  monthly_rent: string;
  notice_given_on: string;
  served_by: "in person" | "mail" | "posted" | "email" | "other";
  lease_terms: string | null;
};
// A lease's renters insurance on the day: what the lease asks, and the policy that counts.
export type RentersInsurance = {
  required: boolean;
  minimum_liability: string | null;
  carrier: string | null;
  policy_number: string | null;
  expires_on: string | null;
  liability_limit: string | null;
  // current, expires today, expires in N days, expired, or none on file.
  standing: string;
  below_minimum: boolean;
};
export type RentLease = {
  id: string;
  property: string;
  unit: string;
  starts_on: string;
  ends_on: string | null;
  // The rent in force on the day; every change recorded, past and to come, in rent_changes.
  monthly_rent: string;
  rent_changes: RentChange[];
  tenants: LeaseTenant[];
  owed: string;
  on_hold: boolean;
  open_charges: OpenCharge[];
  payments: Paid[];
  recurring: RecurringCharge[];
  renters_insurance: RentersInsurance | null;
};
export type Payment = { transfers: string[]; to_owner: string; to_prepaid: string };
// A payment the bank returned: nsf_fee is the NSF fee the lease was charged for it, if the
// property's terms charge one.
export type Bounce = { transfers: string[]; nsf_fee: string | null };
// What the rent run charged: leases their rent, and recurring charges.
export type RentRun = { charged: number; recurring: number };

// A fresh id for one thing staff record: sent again (a double click, a retry), it records
// nothing new.
export function requestId(): string {
  return crypto.randomUUID();
}

// What recording a returned payment did, in a line. A bounce undoes the whole payment: what it
// paid of the lease's charges is owed again, and what it held as prepaid rent is gone.
export function bounceSummary(amount: string, bounce: Bounce): string {
  const fee = bounce.nsf_fee === null ? "" : `; a ${money(bounce.nsf_fee)} NSF fee is charged`;
  return `The ${money(amount)} payment bounced and is undone${fee}.`;
}

// What a recorded payment did, in a line.
export function paymentSummary(payment: Payment): string {
  const parts = [];
  if (Number(payment.to_owner) > 0) parts.push(`${money(payment.to_owner)} paid what is owed`);
  if (Number(payment.to_prepaid) > 0) parts.push(`${money(payment.to_prepaid)} held as prepaid rent`);
  return parts.join("; ") + ".";
}

// A lease's balance in words: what it owes, or what is owed back to it.
export function owedLabel(owed: string): string {
  const value = Number(owed);
  if (value > 0) return `Owes ${money(owed)}`;
  if (value < 0) return `In credit ${money(String(-value))}`;
  return "Paid up";
}

// The badge a lease's balance wears: owing is a caution, paid up is fine, in credit is news.
export function owedTone(owed: string): "caution" | "positive" | "info" {
  const value = Number(owed);
  if (value > 0) return "caution";
  if (value < 0) return "info";
  return "positive";
}

export function chargeLabel(charge: OpenCharge): string {
  const what = charge.memo ?? (charge.kind === "rent" ? "Rent" : "Fee");
  const part = charge.open === charge.amount ? "" : ` of ${money(charge.amount)}`;
  return `${usDate(charge.due_on)} ${what}: ${money(charge.open)} open${part}`;
}

export function recurringLabel(recurring: RecurringCharge): string {
  const amount = recurring.kind === "credit" ? `credit of ${money(recurring.amount)}` : money(recurring.amount);
  const stops = recurring.ends_on ? `, stops ${usDate(recurring.ends_on)}` : "";
  return `${recurring.memo}: ${amount} a month from ${usDate(recurring.starts_on)}${stops}`;
}

export function rentRunSummary(run: RentRun): string {
  const rents = `${run.charged} rent${run.charged === 1 ? "" : "s"}`;
  const recurring = `${run.recurring} recurring charge${run.recurring === 1 ? "" : "s"}`;
  return `${rents} and ${recurring} charged.`;
}

const SERVED: Record<RentChange["served_by"], string> = {
  mail: "by mail",
  "in person": "in person",
  posted: "posted at the unit",
  email: "by email",
  other: "given another way",
};

// "From 2026-12-01: $1,575.00 a month (notice by mail on 2026-10-15)".
export function rentChangeLine(change: RentChange): string {
  const terms = change.lease_terms ? `; ${change.lease_terms}` : "";
  return (
    `From ${usDate(change.effective_on)}: ${money(change.monthly_rent)} a month ` +
    `(notice ${SERVED[change.served_by]} on ${usDate(change.notice_given_on)}${terms})`
  );
}

// The first month a rent change may start with written notice given today: notice of a full
// month is given by the first of the month before (docs/law.md).
export function earliestRentChange(today: Date): string {
  const months = today.getUTCDate() === 1 ? 1 : 2;
  return day(new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() + months, 1)));
}

// "Renters insurance required (at least $100,000.00 of liability): Carrier A RI-1 through
// 2027-01-31", or what the lease asks with none on file, or "not required".
export function insuranceLine(insurance: RentersInsurance): string {
  const asks = insurance.required
    ? `Renters insurance required${insurance.minimum_liability ? ` (at least ${money(insurance.minimum_liability)} of liability)` : ""}`
    : "Renters insurance not required";
  if (insurance.policy_number === null) return `${asks}: none on file`;
  const limit = insurance.liability_limit ? `, ${money(insurance.liability_limit)} of liability` : "";
  return `${asks}: ${insurance.carrier} ${insurance.policy_number} through ${usDate(insurance.expires_on)}${limit}`;
}

// The badge for it: a policy below what the lease asks, or not current where it is required.
export function insuranceBadge(insurance: RentersInsurance): { word: string; tone: "positive" | "caution" | "danger" | "neutral" } {
  if (insurance.below_minimum) return { word: "below the minimum", tone: "danger" };
  if (insurance.standing === "current") return { word: "current", tone: "positive" };
  if (!insurance.required) return { word: insurance.standing, tone: "neutral" };
  return {
    word: insurance.standing,
    tone: insurance.standing === "expired" || insurance.standing === "none on file" ? "danger" : "caution",
  };
}
