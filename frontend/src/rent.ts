// Staff's rent work as the API sends it (src/pms/api/rent.py), and how it reads.
import { day, money, sumMoney, usDate } from "./money";

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
  method: PaymentMethod | "online" | null;
  bounced: boolean;
  // What staff wrote about it as they received it, and its files (a check's photo, a PDF); a
  // tenant never sees the note (ADR 0010 phase 1c).
  note?: string | null;
  attachments?: string[];
};
// How a payment came; recorded as said, nothing sent (ADR 0006).
export type PaymentMethod = "check" | "cashier's check" | "money order" | "EFT" | "cash";
export const PAYMENT_METHODS: { value: PaymentMethod; label: string }[] = [
  { value: "check", label: "Check" },
  { value: "cashier's check", label: "Cashier's check" },
  { value: "money order", label: "Money order" },
  { value: "EFT", label: "EFT" },
  { value: "cash", label: "Cash" },
];
export function methodLabel(method: PaymentMethod | "online"): string {
  // A payment the tenant made online, through the payments partner (never one staff record).
  if (method === "online") return "Paid online (bank)";
  return PAYMENT_METHODS.find((m) => m.value === method)?.label ?? method;
}
// A deposit's kind (migration 0092): each held apart, the security deposit first.
export type DepositKind = "security" | "pet" | "key";
export const DEPOSIT_KINDS: { value: DepositKind; label: string }[] = [
  { value: "security", label: "Security deposit" },
  { value: "pet", label: "Pet deposit" },
  { value: "key", label: "Key deposit" },
];
export type Held = { kind: DepositKind; amount: string };
// deposit is everything held for the tenant; deposits the same by kind, only kinds holding some.
export type LeaseTenant = { id: string; name: string; prepaid: string; deposit: string; deposits: Held[] };

// What the tenants' deposits are, by kind, when any is not a security deposit:
// "Security deposit $1,000.00 · Pet deposit $300.00"; nothing when all are.
export function depositKindsLine(tenants: LeaseTenant[]): string {
  const held = DEPOSIT_KINDS.map((k) => ({
    ...k,
    amount: sumMoney(tenants.flatMap((t) => t.deposits.filter((d) => d.kind === k.value).map((d) => d.amount))),
  })).filter((k) => Number(k.amount) !== 0);
  if (held.every((k) => k.value === "security")) return "";
  return held.map((k) => `${k.label} ${money(k.amount)}`).join(" · ");
}
// A fee the lease owes every month besides its rent, or a credit it gets every month. ends_on is
// the first due date it is no longer charged for.
export type RecurringCharge = {
  id: string;
  kind: "fee" | "credit";
  memo: string;
  amount: string;
  starts_on: string;
  ends_on: string | null;
  charge_type: string | null; // the company's type of fee it is
};
export type RentChange = {
  id: string;
  effective_on: string;
  monthly_rent: string;
  notice_given_on: string;
  served_by: "in person" | "mail" | "posted" | "email" | "other";
  lease_terms: string | null;
};
// The day of the month a lease's rent is due, from the first of a month on (migration 0081).
export type DueDay = { id: string; starts_on: string; due_day: number };
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
// The other lease of a move (migration 0091): where a lease's tenants moved from, or to, and the
// first day of the new lease.
export type Move = { lease_id: string; property: string; unit: string; moved_on: string };
export type RentLease = {
  id: string;
  property: string;
  unit: string;
  starts_on: string;
  ends_on: string | null;
  // The rent in force on the day; every change recorded, past and to come, in rent_changes.
  monthly_rent: string;
  rent_changes: RentChange[];
  // The day of the month rent is due this month (1 unless the lease picked another); every
  // change, past and to come, in due_days.
  rent_due_day: number;
  due_days: DueDay[];
  tenants: LeaseTenant[];
  // Where its payments go: its property's owners' accounts, each with their percent when the
  // property's ownership shares split them (migration 0094); otherwise staff choose one.
  owners: LeaseOwner[];
  owed: string;
  // What the leases its tenants moved from still owe their owners: a payment here pays it first.
  carried: string;
  moved_from: Move | null;
  moved_to: Move | null;
  on_hold: boolean;
  open_charges: OpenCharge[];
  payments: Paid[];
  recurring: RecurringCharge[];
  renters_insurance: RentersInsurance | null;
  // The security deposit its signed lease offer asked, what is held of it and what is still to
  // receive; null when no offer asked one, or once its deposits were returned.
  deposit_asked?: DepositAsked | null;
};
// Asking holds nothing: staff record the deposit when it comes in (migration 0071, q036).
export type DepositAsked = { asked: string; held: string; to_receive: string };
export type LeaseOwner = { account: string; owner: string; percent: string | null };
export type Payment = { transfers: string[]; to_owner: string; to_prepaid: string };
// A payment the bank returned: nsf_fee is the NSF fee the lease was charged for it, if the
// property's terms charge one.
export type Bounce = { transfers: string[]; nsf_fee: string | null };
// What a rent run charged: leases their rent, recurring charges and late fees.
// A run by hand also says how many emails it wrote (rent reminders, late notices, statements).
export type RentRun = {
  rents: number;
  recurring: number;
  late_fees: number;
  emails?: number;
  prepaid_used?: number;
  // Autopay payments started (src/pms/autopay.py), on a server that takes payments online.
  autopay?: number;
  // Fixed-term leases whose term ran out with no renewal, gone month to month (ADR 0010 phase 1c).
  leases_rolled_over?: number;
};
// A run as recorded: the nightly run (by null) or an admin's by hand.
export type RentRunLine = RentRun & { at: string; due_on: string; late_fees_as_of: string; by: string | null };

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

// The open charges staff chose for a payment to pay first, as the API takes them: each ticked one
// with what it pays of it.
export type ChargePart = { charge_id: string; amount: string };

export function chosenCharges(picked: Record<string, string>): ChargePart[] {
  return Object.entries(picked).map(([charge_id, amount]) => ({ charge_id, amount: amount.trim() }));
}

// The choices with one charge ticked (a part), or unticked (undefined).
export function withPart(was: Record<string, string>, id: string, part: string | undefined): Record<string, string> {
  const next = { ...was };
  if (part === undefined) delete next[id];
  else next[id] = part;
  return next;
}

// What the choices come to, counting only amounts typed in full (a part typed half way counts 0).
export function partsTotal(parts: ChargePart[]): string {
  return sumMoney(parts.map((part) => part.amount).filter((amount) => /^\d+(\.\d{1,2})?$/.test(amount)));
}

// What a recorded payment did, in a line.
export function paymentSummary(payment: Payment): string {
  const parts = [];
  if (Number(payment.to_owner) > 0) parts.push(`${money(payment.to_owner)} paid what is owed`);
  if (Number(payment.to_prepaid) > 0) parts.push(`${money(payment.to_prepaid)} held as prepaid rent`);
  return parts.join("; ") + ".";
}

export function chargeLabel(charge: OpenCharge): string {
  const what = charge.memo ?? (charge.kind === "rent" ? "Rent" : "Fee");
  const part = charge.open === charge.amount ? "" : ` of ${money(charge.amount)}`;
  return `${usDate(charge.due_on)} ${what}: ${money(charge.open)} open${part}`;
}

export function recurringLabel(recurring: RecurringCharge): string {
  const amount = recurring.kind === "credit" ? `credit of ${money(recurring.amount)}` : money(recurring.amount);
  const stops = recurring.ends_on ? `, stops ${usDate(recurring.ends_on)}` : "";
  const typed = recurring.charge_type ? ` (${recurring.charge_type})` : "";
  return `${recurring.memo}${typed}: ${amount} a month from ${usDate(recurring.starts_on)}${stops}`;
}

export function rentRunSummary(run: RentRun): string {
  const count = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;
  const charged = `${count(run.rents, "rent")}, ${count(run.recurring, "recurring charge")} and ${count(run.late_fees, "late fee")} charged.`;
  // Prepaid rent used by itself (migration 0117), for a company that turned it on.
  const prepaid = run.prepaid_used ? ` Prepaid rent paid what ${count(run.prepaid_used, "lease")} owed.` : "";
  const autopay = run.autopay ? ` ${count(run.autopay, "autopay payment")} started.` : "";
  const rolled = run.leases_rolled_over
    ? ` ${count(run.leases_rolled_over, "lease")} went month to month.`
    : "";
  return run.emails
    ? `${charged}${prepaid}${autopay}${rolled} ${count(run.emails, "email")} written.`
    : `${charged}${prepaid}${autopay}${rolled}`;
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

// "1st", "2nd", "3rd", "4th", "11th", "21st": a day of the month as people say it.
export function ordinal(n: number): string {
  const teen = n % 100 >= 11 && n % 100 <= 13;
  const suffix = teen ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th";
  return `${n}${suffix}`;
}

// "Due on the 10th" (or "the 1st"): when the lease's rent is due each month.
export function dueDayLabel(day: number): string {
  return `Due on the ${ordinal(day)}`;
}

// "From 11/01/2026: due on the 10th of each month".
export function dueDayLine(change: DueDay): string {
  return `From ${usDate(change.starts_on)}: due on the ${ordinal(change.due_day)} of each month`;
}

// The first of next month: the earliest a new due day usually starts (this month's rent is
// charged already, or will be by its day).
export function nextMonthFirst(today: Date): string {
  return day(new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() + 1, 1)));
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

// A payment hold (an eviction filed): from its first day a payment to the lease is refused unless
// staff accept it past the hold; a release ends it from a day.
export type Hold = {
  id: string;
  starts_on: string;
  reason: string;
  ends_on: string | null;
  released_by: string | null;
  allowed: number;
  in_force: boolean;
};
export function holdLine(hold: Hold): string {
  const since = `Since ${usDate(hold.starts_on)}: ${hold.reason}`;
  const ended = hold.ends_on === null ? "" : `, released from ${usDate(hold.ends_on)}${hold.released_by ? ` by ${hold.released_by}` : ""}`;
  const allowed = hold.allowed === 0 ? "" : `; ${hold.allowed} payment${hold.allowed === 1 ? "" : "s"} accepted past it`;
  return `${since}${ended}${allowed}`;
}

// What a payment on the lease pays: its own balance and what the leases its tenants moved from
// still owe.
export function balanceDue(lease: RentLease): string {
  return sumMoney([lease.owed, lease.carried]);
}

// "Birch Flats · Unit A": the other home of a move.
export function moveHome(move: Move): string {
  return `${move.property} · ${move.unit}`;
}

// "The signed lease asked $1,500.00: $1,000.00 held, $500.00 still to receive", or "... all held".
export function depositAskedLine(deposit: DepositAsked): string {
  const asked = `The signed lease asked ${money(deposit.asked)}`;
  return Number(deposit.to_receive) > 0
    ? `${asked}: ${money(deposit.held)} held, ${money(deposit.to_receive)} still to receive`
    : `${asked}: all held`;
}

// The tenant's words: "Security deposit your lease asks: $1,500.00. Held: $1,000.00; $500.00 still
// to give the manager", or "... Held: all of it".
export function tenantDepositLine(deposit: DepositAsked): string {
  const asks = `Security deposit your lease asks: ${money(deposit.asked)}.`;
  return Number(deposit.to_receive) > 0
    ? `${asks} Held: ${money(deposit.held)}; ${money(deposit.to_receive)} still to give the manager.`
    : `${asks} Held: all of it.`;
}
