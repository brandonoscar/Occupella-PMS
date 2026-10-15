// Staff's rent work as the API sends it (src/pms/api/rent.py), and how it reads.
import { money } from "./money";

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
export type RentLease = {
  id: string;
  property: string;
  unit: string;
  starts_on: string;
  ends_on: string | null;
  monthly_rent: string;
  tenants: LeaseTenant[];
  owed: string;
  on_hold: boolean;
  open_charges: OpenCharge[];
  payments: Paid[];
  recurring: RecurringCharge[];
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
  return `${charge.due_on} ${what}: ${money(charge.open)} open${part}`;
}

export function recurringLabel(recurring: RecurringCharge): string {
  const amount = recurring.kind === "credit" ? `credit of ${money(recurring.amount)}` : money(recurring.amount);
  const stops = recurring.ends_on ? `, stops ${recurring.ends_on}` : "";
  return `${recurring.memo}: ${amount} a month from ${recurring.starts_on}${stops}`;
}

export function rentRunSummary(run: RentRun): string {
  const rents = `${run.charged} rent${run.charged === 1 ? "" : "s"}`;
  const recurring = `${run.recurring} recurring charge${run.recurring === 1 ? "" : "s"}`;
  return `${rents} and ${recurring} charged.`;
}
