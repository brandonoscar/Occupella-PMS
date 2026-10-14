// Staff's bills and owners' money as the API sends them (src/pms/api/payables.py), and how
// they read.
import { day, lastMonth, money } from "./money";

export type BillStatus = "needs approval" | "declined" | "to set aside" | "set aside" | "paid";
export type Bill = {
  id: string;
  vendor: string;
  reference: string;
  account: string;
  owner: string;
  property: string;
  bill_date: string;
  due_on: string;
  amount: string;
  memo: string;
  work_order_id: string | null;
  approval_limit: string;
  approved_by: string | null;
  // Who declined it for the owner (in the portal, or as staff recorded it), and why.
  declined_by: string | null;
  decline_reason: string | null;
  // The email asking the owner to decide it: null when none was written.
  owner_email: EmailStatus | null;
  status: BillStatus;
};
export type EmailStatus = "sent" | "waiting" | "failed";
export type OutboxEmail = {
  id: string;
  to: string;
  subject: string;
  written_at: string;
  status: EmailStatus;
  error: string | null;
};

// What staff are told about the email to a bill's owner, while the bill waits for them.
export function emailNote(bill: Bill): string | null {
  if (bill.status !== "needs approval") return null;
  if (bill.owner_email === "sent") return "Emailed the owner to approve or decline it in their portal.";
  if (bill.owner_email === "waiting") return "An email to the owner waits to be sent.";
  if (bill.owner_email === "failed") return "The email to the owner could not be sent; see Emails to owners.";
  return "No email went to the owner: they have no active portal login. Ask them yourself.";
}
export type Recurring = {
  id: string;
  vendor: string;
  account: string;
  owner: string;
  property: string;
  reference_prefix: string;
  amount: string;
  memo: string;
  day_of_month: number;
  starts_on: string;
  ends_on: string | null;
};
export type OwnerMoney = {
  account: string;
  owner: string;
  property: string;
  bank_account: string;
  balance: string;
  reserve: string;
  unpaid_bills: string;
  available: string;
  has_agreement: boolean;
};
export type Fee = {
  account: string;
  owner: string;
  property: string;
  fee: string | null;
  refused: string | null;
};

// What staff do next with a bill, as its button says; nothing once it is paid.
// A bill's status as a badge: waiting on the owner is a caution, on its way is news, paid is
// done, declined is closed.
export function statusTone(status: BillStatus): "caution" | "info" | "positive" | "neutral" {
  if (status === "needs approval") return "caution";
  if (status === "paid") return "positive";
  if (status === "declined") return "neutral";
  return "info";
}

export function nextStep(status: BillStatus): string | null {
  if (status === "needs approval") return "Record approval";
  if (status === "to set aside") return "Set aside";
  if (status === "set aside") return "Pay";
  return null;
}

export function recurringLabel(bill: Recurring): string {
  const until = bill.ends_on ? ` until ${bill.ends_on}` : "";
  return (
    `${bill.vendor}: ${money(bill.amount)} on day ${bill.day_of_month} of each month ` +
    `from ${bill.starts_on}${until}, ${bill.owner}, ${bill.property} (${bill.reference_prefix})`
  );
}

// Why a draw is less than the balance, in a line: "$1,200.00 held, less a $100.00 reserve and
// $300.00 of unpaid bills".
export function heldLabel(owner: OwnerMoney): string {
  const less = [];
  if (Number(owner.reserve) > 0) less.push(`a ${money(owner.reserve)} reserve`);
  if (Number(owner.unpaid_bills) > 0) less.push(`${money(owner.unpaid_bills)} of unpaid bills`);
  return `${money(owner.balance)} held` + (less.length ? `, less ${less.join(" and ")}` : "");
}

// A management fee run in a line per owner account: the fee taken, or why not.
export function feeLine(fee: Fee): string {
  const who = `${fee.owner}, ${fee.property}`;
  return fee.fee !== null ? `${who}: ${money(fee.fee)} taken` : `${who}: not taken, ${fee.refused}`;
}

// Last month as a fee period: from its first day up to, not including, this month's first.
export function lastMonthPeriod(today: Date): { start: string; end: string } {
  const thisMonth = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1));
  return { start: lastMonth(today).start, end: day(thisMonth) };
}
