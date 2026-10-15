// Staff's day at a glance as the API sends it (src/pms/api/today.py), and how it reads.
import type { JournalEntry } from "./journal";
import { entryTotal } from "./journal";
import type { Application } from "./leasing";
import { money } from "./money";
import type { Offer } from "./offers";
import type { Bill } from "./payables";

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
  return `${lease.unit}, ${lease.property}${who}: ${money(lease.rent_past_due)} of rent past due since ${lease.oldest_due_on} (${days})`;
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
    parts.push(`prepared through ${account.waiting_for_approval}, waiting for approval`);
  }
  return `${account.name}: ${parts.join("; ")}`;
}

// "Unit 1, Property 1: Applicant 1, Applicant 2, applied 2026-10-12".
export function applicationLine(application: Application): string {
  const names = application.applicants.map((a) => a.name).join(", ");
  return `${application.unit}, ${application.property}: ${names}, applied ${application.submitted_at.slice(0, 10)}`;
}

// "Renewal of Unit 1, Property 1 from 2026-11-01: 1 of 2 signed".
export function offerLine(offer: Offer): string {
  const signed = offer.signers.filter((s) => s.signed_at !== null).length;
  const kind = offer.kind === "renewal" ? "Renewal" : "New lease";
  return `${kind} of ${offer.unit}, ${offer.property} from ${offer.starts_on}: ${signed} of ${offer.signers.length} signed`;
}

// "Rent deposited to the wrong property: $25.00, dated 2026-10-10, proposed by Bookkeeper 1".
export function journalLine(entry: JournalEntry): string {
  return `${entry.memo}: ${money(entryTotal(entry))}, dated ${entry.event_on}, proposed by ${entry.proposed_by}`;
}

// "Vendor 1, general liability: expires in 15 days (2026-07-15)".
export function insuranceLine(policy: Lapsing): string {
  return `${policy.vendor}, ${policy.coverage}: ${policy.standing} (${policy.expires_on})`;
}
