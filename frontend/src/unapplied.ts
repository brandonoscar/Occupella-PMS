import { methodLabel } from "./rent";
import type { PaymentMethod } from "./rent";

// Money that came into an operating trust bank account staff can't match yet (migration
// 20261106000240, src/pms/api/unapplied.py): held in that account's unapplied cash account until
// it is applied to a lease or refunded. Month-end isn't approved while any is held.
export const UNAPPLIED = "/api/unapplied";

export type Unapplied = {
  id: string;
  bank_account_id: string;
  bank_account: string;
  received_on: string;
  payer: string;
  memo: string | null;
  method: PaymentMethod;
  reference: string | null;
  amount: string;
  // What is still held of it: not yet applied to a lease nor refunded.
  left: string;
  // Days since it came in, by the company's day.
  age_days: number;
};

// How it came: "Check 4410", "Cash".
export function cameAs(u: Pick<Unapplied, "method" | "reference">): string {
  const how = methodLabel(u.method);
  return u.reference ? `${how} ${u.reference}` : how;
}

// How long it has waited: "Today", "1 day", "12 days".
export function waited(days: number): string {
  if (days <= 0) return "Today";
  return days === 1 ? "1 day" : `${days} days`;
}

// Held a month or more: it should have found its lease by now.
export const LONG_WAIT_DAYS = 30;
