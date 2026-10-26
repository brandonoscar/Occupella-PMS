// Staff's day at a glance as the API sends it (src/pms/api/today.py), and how it reads.
import type { JournalEntry } from "./journal";
import { entryTotal } from "./journal";
import type { Application } from "./leasing";
import { money, usDate, usDatesIn } from "./money";
import type { Offer } from "./offers";
import type { Bill, CompanyFees } from "./payables";
import { companyFeesLabel } from "./payables";
import type { DrawRun } from "./payouts";
import { drawRunLine } from "./payouts";
import type { Prepared } from "./reconcile";

export type LateLease = {
  lease_id: string;
  property: string;
  unit: string;
  tenants: string[];
  rent_past_due: string;
  oldest_due_on: string;
};
export type Unreconciled = {
  bank_account_id: string;
  name: string;
  closed_through: string | null;
  months: string[];
  waiting_for_approval: string | null;
  // That preparation, for Approve on the Dashboard.
  prepared?: Prepared | null;
};
export type Today = {
  on: string;
  late_rent: LateLease[];
  bills_due: Bill[];
  unreconciled: Unreconciled[];
  applications: Application[];
  unsigned_offers: Offer[];
  journal_waiting: JournalEntry[];
  vendor_insurance: Lapsing[];
  company_fees: CompanyFees[];
  signers: SignerNote[];
  renters_insurance: Uninsured[];
  // Leases not ended whose signed offer asked a security deposit not all held yet.
  deposits_to_receive: DepositDue[];
  // Leases past their last day whose deposits are still held, and when they are due back.
  deposits_to_return: DepositBack[];
  // The owners' draws the nightly run prepared on the draw day, until staff pay them.
  owner_draws?: DrawRun | null;
  // Fixed-term leases the nightly run rolled to month to month in the last 30 days, their term
  // run out with no renewal signed (ADR 0010 phase 1c), while they still are.
  month_to_month?: Holdover[];
};
export type Holdover = {
  lease_id: string;
  property: string;
  unit: string;
  tenants: string[];
  term_ended_on: string;
  rolled_on: string;
};
export type DepositBack = {
  lease_id: string;
  property: string;
  unit: string;
  tenants: string[];
  held: string;
  return_by: string;
  standing: string;
};
export type DepositDue = {
  lease_id: string;
  property: string;
  unit: string;
  tenants: string[];
  asked: string;
  to_receive: string;
};
export type Uninsured = {
  lease_id: string;
  property: string;
  unit: string;
  tenants: string;
  standing: string;
};
export type SignerNote = {
  bank_account_id: string;
  bank_account: string;
  signer: string | null;
  standing: string;
};
export type Lapsing = {
  vendor_id: string;
  vendor: string;
  coverage: string;
  expires_on: string;
  standing: string;
};

// Whole days from one day ("YYYY-MM-DD") to another.
export function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

// "2026-09" as "September 2026".
export function monthLabel(month: string): string {
  const [year, number] = month.split("-");
  const name = new Date(Date.UTC(Number(year), Number(number) - 1, 1)).toLocaleString("en-US", {
    month: "long",
    timeZone: "UTC",
  });
  return `${name} ${year}`;
}

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

// "Unit 1, Property 1 (Tenant A): $1,500.00 of rent past due since 2026-10-03 (10 days)".
export function lateLine(lease: LateLease, on: string): string {
  const who = lease.tenants.length ? ` (${lease.tenants.join(", ")})` : "";
  const days = plural(daysBetween(lease.oldest_due_on, on), "day");
  return `${lease.unit}, ${lease.property}${who}: ${money(lease.rent_past_due)} of rent past due since ${usDate(lease.oldest_due_on)} (${days})`;
}

// "Vendor A INV-1 for Property 1: $120.00, due in 3 days" (or "due today", or "5 days late").
export function billLine(bill: Bill, on: string): string {
  const left = daysBetween(on, bill.due_on);
  const when = left === 0 ? "due today" : left > 0 ? `due in ${plural(left, "day")}` : `${plural(-left, "day")} late`;
  return `${bill.vendor} ${bill.reference} for ${bill.property}: ${money(bill.amount)}, ${when} (${bill.status})`;
}

// "Operating trust: September 2026 not reconciled; prepared through 2026-09-30, waiting for approval".
export function monthEndLine(account: Unreconciled): string {
  const parts = [];
  if (account.months.length) {
    parts.push(`${account.months.map(monthLabel).join(", ")} not reconciled`);
  }
  if (account.waiting_for_approval) {
    parts.push(`prepared through ${usDate(account.waiting_for_approval)}, waiting for approval`);
  }
  return `${account.name}: ${parts.join("; ")}`;
}

// "Unit 1, Property 1: Applicant 1, Applicant 2, applied 2026-10-12".
export function applicationLine(application: Application): string {
  const names = application.applicants.map((a) => a.name).join(", ");
  return `${application.unit}, ${application.property}: ${names}, applied ${usDate(application.submitted_at)}`;
}

// "Renewal of Unit 1, Property 1 from 2026-11-01: 1 of 2 signed".
export function offerLine(offer: Offer): string {
  const signed = offer.signers.filter((s) => s.signed_at !== null).length;
  const kind = offer.kind === "renewal" ? "Renewal" : "New lease";
  return `${kind} of ${offer.unit}, ${offer.property} from ${usDate(offer.starts_on)}: ${signed} of ${offer.signers.length} signed`;
}

// "Rent deposited to the wrong property: $25.00, dated 2026-10-10, proposed by Bookkeeper 1".
export function journalLine(entry: JournalEntry): string {
  return `${usDatesIn(entry.memo)}: ${money(entryTotal(entry))}, dated ${usDate(entry.event_on)}, proposed by ${entry.proposed_by}`;
}

// "Vendor 1, general liability: expires in 15 days (07/15/2026)".
export function insuranceLine(policy: Lapsing): string {
  return `${policy.vendor}, ${policy.coverage}: ${policy.standing} (${usDate(policy.expires_on)})`;
}

// "Operating trust: $150.00 of the company's fees, the oldest since 09/20/2026 (25 days); take
// them out by 10/15/2026" (or "past 10/15/2026").
function companyFeesLine(fees: CompanyFees, on: string): string {
  const by = fees.take_by === null ? "" : fees.take_by < on ? `; past ${usDate(fees.take_by)} to take them out` : `; take them out by ${usDate(fees.take_by)}`;
  return `${fees.bank_account}: ${companyFeesLabel(fees)}${by}`;
}

// "Operating trust: no broker on file", or "Operating trust, Clerk 1: bond runs out in 5 days".
function signerNoteLine(note: SignerNote): string {
  return note.signer === null
    ? `${note.bank_account}: ${note.standing}`
    : `${note.bank_account}, ${note.signer}: ${note.standing}`;
}

// "Unit 1, Property 1 (Tenant A): renters insurance none on file".
function uninsuredLine(lease: Uninsured): string {
  const who = lease.tenants ? ` (${lease.tenants})` : "";
  return `${lease.unit}, ${lease.property}${who}: renters insurance ${lease.standing}`;
}

// "Unit 1, Property 1 (Tenant 1): $1,200.00 held, due back by 11/04/2026 (due in 13 days)".
function depositBackLine(back: DepositBack): string {
  const who = back.tenants.length ? ` (${back.tenants.join(", ")})` : "";
  return `${back.unit}, ${back.property}${who}: ${money(back.held)} held, due back by ${usDate(back.return_by)} (${back.standing})`;
}

// "Unit 1, Property 1 (Tenant A): $500.00 of the $1,500.00 security deposit the signed lease asked
// still to receive".
// "Unit 4, Elm Court (Tenant 1): its term ended 10/31/2026 with no renewal signed; month to month
// at the same rent since 11/01/2026".
function holdoverLine(lease: Holdover): string {
  const who = lease.tenants.length ? ` (${lease.tenants.join(", ")})` : "";
  return `${lease.unit}, ${lease.property}${who}: its term ended ${usDate(lease.term_ended_on)} with no renewal signed;`
    + ` month to month at the same rent since ${usDate(lease.rolled_on)}`;
}

function depositDueLine(due: DepositDue): string {
  const who = due.tenants.length ? ` (${due.tenants.join(", ")})` : "";
  return `${due.unit}, ${due.property}${who}: ${money(due.to_receive)} of the ${money(due.asked)} security deposit the signed lease asked still to receive`;
}

// --- Needs attention: one list, most urgent first (ADR 0010 phase 3) ---------------------------

// What a row's button does: one of the five actions done right there, or the way to its page.
export type Action =
  | { kind: "remind"; leaseId: string }
  | { kind: "pay"; bill: Bill }
  | { kind: "draws"; run: DrawRun }
  | { kind: "approve"; account: Unreconciled; prepared: Prepared }
  | { kind: "go"; label: string; href: string };

// One thing that needs someone: what kind it is, its line, the day it was due or is due by
// (YYYY-MM-DD), and its action.
export type AttentionRow = { key: string; what: string; line: string; due: string; action: Action };

// The first day of the month after "YYYY-MM": a month is reconciled once it has ended.
function monthAfter(month: string): string {
  const [year, number] = month.split("-").map(Number);
  const next = new Date(Date.UTC(year ?? 1970, number ?? 1, 1));
  return next.toISOString().slice(0, 10);
}

const go = (label: string, href: string): Action => ({ kind: "go", label, href });

// Every row, each kind's in the order the list once grouped them, then the whole list by the day
// each was due or is due by: the longest overdue first, then what comes due soonest. Rows due the
// same day keep that order (money owed and money out first).
export function attentionRows(today: Today): AttentionRow[] {
  const on = today.on;
  const rows: AttentionRow[] = [
    ...today.late_rent.map((lease) => ({
      key: `late:${lease.lease_id}`, what: "Late rent", line: lateLine(lease, on), due: lease.oldest_due_on,
      action: { kind: "remind", leaseId: lease.lease_id } as Action,
    })),
    ...today.bills_due.map((bill) => ({
      key: `bill:${bill.id}`, what: "Bill due", line: billLine(bill, on), due: bill.due_on,
      // Waiting for its owner, or the owner's balance short of it: its page says what it needs.
      action: bill.status === "waiting for owner" || bill.status === "short"
        ? go("Open bill", `/bills/${bill.id}`)
        : ({ kind: "pay", bill } as Action),
    })),
    ...(today.owner_draws && today.owner_draws.lines.length > 0
      ? [{ key: `draws:${today.owner_draws.id}`, what: "Owner draws ready", line: drawRunLine(today.owner_draws),
           due: today.owner_draws.prepared_on, action: { kind: "draws", run: today.owner_draws } as Action }]
      : []),
    ...today.unreconciled.map((account) => ({
      key: `month-end:${account.bank_account_id}`, what: "Month-end", line: monthEndLine(account),
      due: account.months[0] ? monthAfter(account.months[0]) : (account.prepared?.prepared_at ?? on).slice(0, 10),
      action: account.prepared
        ? ({ kind: "approve", account, prepared: account.prepared } as Action)
        : go("Reconcile", `/bank-accounts/${account.bank_account_id}`),
    })),
    ...today.applications.map((application) => ({
      key: `application:${application.id}`, what: "Application", line: applicationLine(application),
      due: application.submitted_at.slice(0, 10), action: go("Review application", `/applicants/${application.id}`),
    })),
    ...today.unsigned_offers.map((offer) => ({
      key: `offer:${offer.id}`, what: "Not yet signed", line: offerLine(offer), due: offer.starts_on,
      action: go("Open draft lease", `/draft-leases/${offer.id}`),
    })),
    ...today.journal_waiting.map((entry) => ({
      key: `journal:${entry.id}`, what: "Journal entry", line: journalLine(entry),
      due: entry.proposed_at.slice(0, 10), action: go("Open journal", "/journal"),
    })),
    ...today.vendor_insurance.map((policy) => ({
      key: `insurance:${policy.vendor_id}:${policy.coverage}`, what: "Vendor insurance", line: insuranceLine(policy),
      due: policy.expires_on, action: go("Open vendor", `/vendors/${policy.vendor_id}`),
    })),
    ...today.company_fees.map((fees) => ({
      key: `fees:${fees.bank_account_id}`, what: "Company fees in trust", line: companyFeesLine(fees, on),
      due: fees.take_by ?? on, action: go("Open Banking", "/banking"),
    })),
    ...today.signers.map((note) => ({
      key: `signer:${note.bank_account_id}:${note.signer ?? ""}`, what: "Trust account signers",
      line: signerNoteLine(note), due: on, action: go("Open Staff and roles", "/settings/staff"),
    })),
    ...today.renters_insurance.map((lease) => ({
      key: `renters:${lease.lease_id}`, what: "Renters insurance", line: uninsuredLine(lease), due: on,
      action: go("Open lease", `/leases/${lease.lease_id}`),
    })),
    ...today.deposits_to_receive.map((due) => ({
      key: `deposit-in:${due.lease_id}`, what: "Deposit to receive", line: depositDueLine(due), due: on,
      action: go("Open lease", `/leases/${due.lease_id}`),
    })),
    ...today.deposits_to_return.map((back) => ({
      key: `deposit-out:${back.lease_id}`, what: "Deposit to return", line: depositBackLine(back), due: back.return_by,
      action: go("Open Move-outs", "/move-out"),
    })),
    ...(today.month_to_month ?? []).map((lease) => ({
      key: `holdover:${lease.lease_id}`, what: "Month to month", line: holdoverLine(lease), due: lease.rolled_on,
      action: go("Open lease", `/leases/${lease.lease_id}`),
    })),
  ];
  // A stable sort: rows due the same day keep the order above.
  return rows.map((row, n) => ({ row, n }))
    .sort((a, b) => (a.row.due < b.row.due ? -1 : a.row.due > b.row.due ? 1 : a.n - b.n))
    .map(({ row }) => row);
}
