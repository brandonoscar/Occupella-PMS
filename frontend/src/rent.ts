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
};
export type Payment = { transfers: string[]; to_owner: string; to_prepaid: string };

// A fresh id for one thing staff record: sent again (a double click, a retry), it records
// nothing new.
export function requestId(): string {
  return crypto.randomUUID();
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

export function chargeLabel(charge: OpenCharge): string {
  const what = charge.memo ?? (charge.kind === "rent" ? "Rent" : "Fee");
  const part = charge.open === charge.amount ? "" : ` of ${money(charge.amount)}`;
  return `${charge.due_on} ${what}: ${money(charge.open)} open${part}`;
}
