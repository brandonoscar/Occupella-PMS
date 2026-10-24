// Staff's bills and owners' money as the API sends them (src/pms/api/payables.py), and how
// they read.
import { day, lastMonth, money, sumMoney, usDate } from "./money";

// A bill's status (ADR 0010): entered (dated after today: nothing held yet), waiting for its owner,
// ready to pay (held from the owner's balance), short (the balance can't cover it), partly paid,
// paid, or void (declined by its owner or voided by staff).
export type BillStatus = "entered" | "waiting for owner" | "ready to pay" | "short" | "partly paid" | "paid" | "void";
export type Bill = {
  id: string;
  vendor_id: string;
  vendor: string;
  reference: string;
  account: string;
  owner_id: string;
  owner: string;
  property_id: string;
  property: string;
  bill_date: string;
  due_on: string;
  amount: string;
  memo: string;
  work_order_id: string | null;
  // The expense account it is in (its latest), or null while it is in none or split.
  expense_account_id: string | null;
  expense_account: string | null;
  // Split across expense accounts by lines (on its page).
  split: boolean;
  approval_limit: string;
  approved_by: string | null;
  // Who declined it for the owner (in the portal, or as staff recorded it), and why; for a void,
  // the staff member who voided it and why.
  declined_by: string | null;
  decline_reason: string | null;
  voided: boolean;
  // The email asking the owner to decide it: null when none was written.
  owner_email: EmailStatus | null;
  status: BillStatus;
  // Neither paid nor void, and due before today.
  overdue: boolean;
  // The company's markup on it, at the percent the owner's terms set; null: none.
  markup: string | null;
  markup_percent: string | null;
  // The invoice shared across properties it is a share of, if it is one.
  shared_bill_id: string | null;
  // What was paid of it and what is left to pay the vendor.
  paid: string;
  left: string;
  // While it is open: what it holds of the owner's balance (its markup included), since when once
  // its money left the owner's account, what the balance is short of it, and what the owner must
  // send for it and the bills due before it.
  held: string | null;
  held_since: string | null;
  short: string | null;
  needed: string | null;
};
export type EmailStatus = "sent" | "waiting" | "failed";
// A bill's expense account lines: its lines when split, else the whole bill in its account.
export type BillLine = { expense_account_id: string | null; expense_account: string | null; amount: string };
export type BillEmail = {
  to: string;
  subject: string;
  written_at: string;
  sent_at: string | null;
  status: EmailStatus;
  attempts: number;
  error: string | null;
};
// One vendor invoice shared across properties: a bill for each owner's property account.
export type SharedPart = {
  bill_id: string;
  part: number;
  reference: string;
  owner: string;
  property: string;
  amount: string;
  status: BillStatus;
};
export type SharedBill = { id: string; reference: string; amount: string; parts: SharedPart[] };
// A payment of a bill (whole or a part), how it went and when the bank statement showed it.
export type BillPayment = {
  part: number;
  on: string;
  amount: string;
  method: PayoutMethod | null;
  check_number: string | null;
  transfer_id: string;
  cleared_on: string | null;
};
// Whom the email asking the owner for what their balance is short of goes to: null when they
// have no email on file.
export type AskOwner = { owner: string; to: string | null; amount: string; asked_today: boolean };
export type BillPageData = {
  bill: Bill;
  shared: SharedBill | null;
  lines: BillLine[];
  files: { id: string; title: string; content_type: string; size: number; uploaded_at: string }[];
  email: BillEmail | null;
  asks: BillEmail[];
  payments: BillPayment[];
  ask: AskOwner | null;
  history: { on: string; what: string }[];
};
// One bill of Pay bills, as recorded.
export type BillPaid = {
  bill_id: string;
  vendor: string;
  reference: string;
  amount: string;
  transfer_id: string;
  // The payout run whose file it is in, when the PMS writes one.
  run_id?: string | null;
};
// A bill already entered for the vendor under the same reference but for case and punctuation.
export type SimilarBill = { id: string; reference: string; bill_date: string; amount: string; status: BillStatus };

// Record bill's warning: "Vendor 1 has bill INV-100 already ($200.00, dated 10/01/2026, Ready to
// pay). Check this isn't the same bill."
export function similarNote(vendor: string, similar: SimilarBill[]): string {
  const each = similar.map((b) => `${b.reference} (${money(b.amount)}, dated ${usDate(b.bill_date)}, ${BADGES[b.status].word.toLowerCase()})`);
  const which = similar.length === 1 ? "this isn't the same bill" : "this isn't one of them";
  return `${vendor} has ${similar.length === 1 ? "bill" : "bills"} ${each.join(", ")} already. Check ${which}.`;
}

type Tone = "caution" | "info" | "positive" | "neutral" | "danger";
const BADGES: Record<BillStatus, { word: string; tone: Tone }> = {
  entered: { word: "Entered", tone: "neutral" },
  "waiting for owner": { word: "Waiting for owner", tone: "caution" },
  "ready to pay": { word: "Ready to pay", tone: "info" },
  short: { word: "Short", tone: "danger" },
  "partly paid": { word: "Partly paid", tone: "info" },
  paid: { word: "Paid", tone: "positive" },
  void: { word: "Void", tone: "neutral" },
};

// A bill's status in plain words (ADR 0010).
export function billBadge(bill: Pick<Bill, "status">): { word: string; tone: Tone } {
  return BADGES[bill.status];
}

// What a bill holds of its owner's balance, in a sentence: "$240.00 held from Owner A's balance
// for this bill."; what the owner needs to send while it is short; what paid or void means.
export function holdLine(bill: Bill): string {
  const markup = bill.markup !== null ? ` and its ${money(bill.markup)} markup` : "";
  switch (bill.status) {
    case "paid":
      return `Paid in full: ${money(bill.paid)}.`;
    case "void":
      return `Nothing was paid; the hold went back to ${bill.owner}'s balance.`;
    case "entered":
      return `Nothing is held for it until its date, ${usDate(bill.bill_date)}.`;
    default:
      break;
  }
  if (bill.held_since !== null) {
    const paid = bill.status === "partly paid" ? `${money(bill.paid)} paid. ` : "";
    return `${paid}${money(bill.held ?? bill.left)} held for this bill since ${usDate(bill.held_since)}, when it left ${bill.owner}'s balance.`;
  }
  const until = bill.status === "waiting for owner" ? ` until ${bill.owner} decides` : "";
  const held = Number(bill.held ?? "0") > 0
    ? `${money(bill.held ?? "0")} held from ${bill.owner}'s balance for this bill${markup}${until}.`
    : `Nothing of ${bill.owner}'s balance is left to hold for this bill${until}.`;
  if (bill.needed === null) return held;
  return `${held} ${bill.owner} needs to send ${money(bill.needed)}.`;
}

// What Pay bills did, in a line.
export function billsPaidSummary(paid: BillPaid[], method: PayoutMethod): string {
  const total = sumMoney(paid.map((p) => p.amount));
  return `${paid.length} bill${paid.length === 1 ? "" : "s"} of ${money(total)} paid, by ${method === "EFT" ? "ACH" : method}.`;
}

// "$25.00 (10% in the owner's terms)", or null for a bill with no markup.
export function markupLabel(bill: Pick<Bill, "markup" | "markup_percent">): string | null {
  if (bill.markup === null) return null;
  return `${money(bill.markup)} (${bill.markup_percent}% in the owner's terms)`;
}

// A total shared evenly among n, to the cent, in whole cents as sumMoney counts them (never as
// floats): the cents left over go to the first shares. Blanks while the total isn't money yet.
export function evenShares(total: string, n: number): string[] {
  const plain = total.trim().replace(/[$,]/g, "");
  if (n < 1) return [];
  if (!/^\d+(\.\d{0,2})?$/.test(plain)) return Array.from({ length: n }, () => "");
  const [whole = "0", part = ""] = plain.split(".");
  const cents = BigInt(whole) * 100n + BigInt((part + "00").slice(0, 2));
  const count = BigInt(n);
  const each = cents / count;
  const left = cents - each * count;
  return Array.from({ length: n }, (_, i) => {
    const share = each + (BigInt(i) < left ? 1n : 0n);
    return `${share / 100n}.${String(share % 100n).padStart(2, "0")}`;
  });
}

// What a bill's split lines come to, against the bill: "Lines come to $250.00 of $300.00".
export function linesNote(amounts: string[], amount: string): string {
  return `Lines come to ${money(sumMoney(amounts.map((a) => a || "0")))} of ${money(amount || "0")}.`;
}

// What staff are told about the email to a bill's owner, while the bill waits for them.
export function emailNote(bill: Bill): string | null {
  if (bill.status !== "waiting for owner") return null;
  if (bill.owner_email === "sent") return "Emailed the owner to approve or decline it in their portal.";
  if (bill.owner_email === "waiting") return "An email to the owner waits to be sent.";
  if (bill.owner_email === "failed") return "The email to the owner could not be sent; its page says why.";
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
  expense_account_id: string | null;
  expense_account: string | null;
};
export type OwnerMoney = {
  account: string;
  owner_id: string;
  owner: string;
  property_id: string;
  property: string;
  bank_account: string;
  balance: string;
  reserve: string;
  unpaid_bills: string;
  available: string;
  has_agreement: boolean;
  // How the owner is paid: a check unless they chose ACH.
  payout_method: PayoutMethod;
};
export type Fee = {
  account: string;
  owner: string;
  property: string;
  fee: string | null;
  refused: string | null;
};
// A management fee for a period: taken already, or what Collect fees would take, or why not.
export type FeeDue = Fee & { taken: boolean };
// One payout of Pay owners, as recorded.
export type OwnerPaid = {
  account: string;
  owner: string;
  property: string;
  amount: string;
  transfer_id: string;
  run_id?: string | null;
};
export type PayoutMethod = "check" | "EFT";

// What Pay owners did, in a line: how many payouts, their total and how they went (ACH, check,
// or both when each went its owner's way).
export function paidSummary(paid: OwnerPaid[], method: PayoutMethod | PayoutMethod[]): string {
  const total = sumMoney(paid.map((p) => p.amount));
  const owners = `${paid.length} payout${paid.length === 1 ? "" : "s"}`;
  const ways = [...new Set(Array.isArray(method) ? method : [method])].sort();
  const how = ways.map((m) => (m === "EFT" ? "ACH" : "check")).join(" and ");
  return `${owners} of ${money(total)} recorded, by ${how}.`;
}


// What staff do next with a bill, as its button says; nothing once it is paid or void.
export function nextStep(status: BillStatus): string | null {
  if (status === "waiting for owner") return "Record approval";
  if (status === "ready to pay" || status === "partly paid") return "Record payment";
  if (status === "short") return "Ask owner";
  return null;
}

// "How it went", as a payment's line reads: "Check 1042", "ACH", or nothing recorded.
export function paymentHow(payment: Pick<BillPayment, "method" | "check_number">): string {
  if (payment.method === "check") return payment.check_number ? `Check ${payment.check_number}` : "Check";
  if (payment.method === "EFT") return "ACH";
  return "Not recorded";
}

export function recurringLabel(bill: Recurring): string {
  const until = bill.ends_on ? ` until ${usDate(bill.ends_on)}` : "";
  return (
    `${bill.vendor}: ${money(bill.amount)} on day ${bill.day_of_month} of each month ` +
    `from ${usDate(bill.starts_on)}${until}, ${bill.owner}, ${bill.property} (${bill.reference_prefix})`
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

// A fee taken from an owner besides the management and leasing fees (migration 0082, q051).
export type OwnerFeeKind = "inspection" | "lease_up" | "other";
export const OWNER_FEE_KINDS: [OwnerFeeKind, string][] = [
  ["inspection", "Inspection fee"],
  ["lease_up", "Lease-up fee"],
  ["other", "Other fee"],
];

// "A $75.00 inspection fee is taken from Property 1."
export function ownerFeeSummary(kind: OwnerFeeKind, amount: string, property: string): string {
  const label = (OWNER_FEE_KINDS.find(([value]) => value === kind)?.[1] ?? "Fee").toLowerCase();
  return `A ${money(amount)} ${label} is taken from ${property}.`;
}

// An owner's income statement from the first of the year through today (UTC), as a PDF: every
// property of theirs, with the Schedule E lines.
export function incomeStatementHref(ownerId: string, today: string): string {
  const args = [ownerId, `${today.slice(0, 4)}-01-01`, today].map((a) => `arg=${encodeURIComponent(a)}`);
  return `/api/reports/trust_report_income_statement?${args.join("&")}&format=pdf`;
}

// The company's earned fees still in a trust bank account at a day's end: since when the oldest
// waited, and the day to take them out by (25 days after it, the strictest deadline found).
export type CompanyFees = {
  bank_account_id: string;
  bank_account: string;
  held: string;
  since: string | null;
  days_held: number | null;
  take_by: string | null;
};

// "$150.00 of the company's fees, the oldest since 09/20/2026 (5 days)", or that none is held.
export function companyFeesLabel(fees: CompanyFees): string {
  if (fees.since === null) return "None of the company's fees is held here.";
  const days = fees.days_held === 1 ? "1 day" : `${fees.days_held} days`;
  return `${money(fees.held)} of the company's fees, the oldest since ${usDate(fees.since)} (${days})`;
}

// Whether the oldest is past the day to take it out by ("late"), or not yet.
export function companyFeesDue(fees: CompanyFees, on: string): { word: string; late: boolean } | null {
  if (fees.take_by === null) return null;
  return fees.take_by < on
    ? { word: `take out: past ${usDate(fees.take_by)}`, late: true }
    : { word: `take out by ${usDate(fees.take_by)}`, late: false };
}
