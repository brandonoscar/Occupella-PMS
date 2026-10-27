// Staff's month-end as the API sends it (src/pms/api/reconcile.py), and how it reads.
import type { Me } from "./api";
import { day, money, usDate } from "./money";

export type StatementRow = {
  id: string;
  first_day: string;
  last_day: string;
  opening_balance: string;
  closing_balance: string;
  lines: number;
  source: "csv" | "ofx";
  imported_at: string;
};
export type Approved = {
  id: string;
  period_start: string;
  period_end: string;
  statement_balance: string;
  book_balance: string;
  prepared_by: string;
  approved_by: string;
  approved_at: string;
};
// A period one staff member prepared and another has yet to approve.
export type Prepared = {
  id: string;
  through: string;
  bank_balance: string;
  book_cash: string;
  prepared_by: string;
  preparer_id: string;
  prepared_at: string;
};
export type BankBooks = {
  id: string;
  name: string;
  kind: "operating" | "security_deposit";
  book_cash: string;
  closed_through: string | null;
  next_start: string | null;
  statements: StatementRow[];
  reconciliations: Approved[];
  prepared: Prepared | null;
};
export type ReportRow = { line: number; item: string; detail: string | null; amount: string | null };
export type OpenLine = {
  id: string;
  posted_on: string;
  amount: string;
  description: string;
  reference: string | null;
};
// What a money-out line no match holds may be (ADR 0010): a bill payment recorded already of its
// amount ("match"), or a bill ready to pay with exactly that much left ("record": confirming
// records it paid on the line's day and matches the two).
export type BillSuggestion = {
  line: OpenLine;
  bill_id: string;
  vendor: string;
  reference: string;
  owner: string;
  property: string;
  kind: "match" | "record";
  transfer_id: string | null;
  amount: string;
  check_number: string | null;
};

// What confirming a suggestion does, as its question asks it.
export function suggestionQuestion(s: BillSuggestion): string {
  if (s.kind === "match") {
    return `Match the ${money(s.amount)} bank line of ${usDate(s.line.posted_on)} to the payment of ${s.vendor} ${s.reference}? No money moves.`;
  }
  const how = s.check_number ? `check ${s.check_number}` : "ACH";
  return `Record ${s.vendor} ${s.reference} paid ${money(s.amount)} by ${how} on ${usDate(s.line.posted_on)}, and match it to this bank line? It comes out of ${s.owner}'s money in trust for ${s.property}.`;
}

// A transfer in a bank deposit names it and its day: its one bank line matches the whole deposit.
export type OpenTransfer = {
  id: string; on: string; amount: string; memo: string | null;
  deposit_id?: string | null; deposited_on?: string | null;
};
// A check or deposit carried over from the old system that the bank had not cleared.
export type OpenItem = {
  id: string;
  issued_on: string;
  amount: string;
  description: string;
  reference: string | null;
};
export type StandingMatch = {
  id: string;
  lines: OpenLine[];
  transfers: OpenTransfer[];
  items: OpenItem[];
};
// A migrated account's cutover, as the opening match needs it.
export type Opening = {
  book_cash: string;
  carried: string;
  brought_forward: string;
  signed_off: boolean;
  matched: boolean;
};
export type Worksheet = {
  through: string | null;
  rows: ReportRow[];
  open_lines: OpenLine[];
  open_transfers: OpenTransfer[];
  open_items: OpenItem[];
  opening: Opening | null;
  matches: StandingMatch[];
};

// Money as whole cents, so a sum of amounts is exact: "1,234.50" and "-0.05" both read.
export function toCents(amount: string): number {
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(amount.replaceAll(",", "").trim());
  if (!match) throw new Error(`not an amount: ${amount}`);
  const [, sign, whole = "0", part = ""] = match;
  const cents = Number(whole) * 100 + Number(part.padEnd(2, "0"));
  return sign === "-" ? -cents : cents;
}

export function fromCents(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

export function sum(amounts: string[]): string {
  return fromCents(amounts.reduce((total, amount) => total + toCents(amount), 0));
}

// The reconciliation's figures by name (lines 4 to 9 of trust_report_bank_reconciliation).
export function figures(rows: ReportRow[]): Record<string, string> {
  return Object.fromEntries(
    rows.filter((r) => r.line >= 4 && r.line <= 9 && r.amount !== null).map((r) => [r.item, r.amount ?? ""]),
  );
}

// The items that explain the figures: transfers the bank had not cleared, lines the books lack.
export function items(rows: ReportRow[]): ReportRow[] {
  return rows.filter((r) => r.line > 9);
}

export function difference(rows: ReportRow[]): string | null {
  return rows.find((r) => r.line === 9)?.amount ?? null;
}

// A file's statement format, from its name: OFX (and its QFX flavour), or CSV.
export function formatOf(name: string): "csv" | "ofx" {
  return /\.(ofx|qfx)$/i.test(name) ? "ofx" : "csv";
}

export function lineLabel(line: OpenLine): string {
  const ref = line.reference ? ` (${line.reference})` : "";
  return `${usDate(line.posted_on)} ${line.description || "No description"}${ref}: ${money(line.amount)}`;
}

export function transferLabel(transfer: OpenTransfer): string {
  const deposit = transfer.deposited_on ? ` · deposit of ${usDate(transfer.deposited_on)}` : "";
  return `${usDate(transfer.on)} ${transfer.memo ?? "No memo"}: ${money(transfer.amount)}${deposit}`;
}

// Ticking a transfer in a bank deposit ticks (or unticks) the whole deposit with it.
export function toggleTransfer(all: OpenTransfer[], chosen: string[], id: string): string[] {
  const deposit = all.find((t) => t.id === id)?.deposit_id ?? null;
  const ids = deposit === null ? [id] : all.filter((t) => t.deposit_id === deposit).map((t) => t.id);
  return chosen.includes(id) ? chosen.filter((x) => !ids.includes(x)) : [...chosen, ...ids.filter((x) => !chosen.includes(x))];
}

// A period ends at midnight UTC after its last day; the day staff read is that last day.
export function lastDayOf(periodEnd: string): string {
  return day(new Date(new Date(periodEnd).getTime() - 86_400_000));
}

export function itemLabel(item: OpenItem): string {
  const ref = item.reference ? ` (${item.reference})` : "";
  return `${usDate(item.issued_on)} ${item.description}${ref}, from before the cutover: ${money(item.amount)}`;
}

// Who may approve a prepared period: anyone but its preparer, holding the approve role.
export function approver(me: Me, prepared: Prepared): "preparer" | "may" | "may not" {
  if (me.person_id === prepared.preparer_id) return "preparer";
  return me.roles?.includes("approve") ? "may" : "may not";
}

// The matches newest first, by their bank line's day (or, with no line, their transfer's or
// carried item's): the order a statement reads in, not the order they were made.
export function byDay(matches: StandingMatch[]): StandingMatch[] {
  const dayOf = (m: StandingMatch) =>
    m.lines[0]?.posted_on ?? m.transfers[0]?.on ?? m.items[0]?.issued_on ?? "";
  return [...matches].sort((a, b) => (dayOf(a) < dayOf(b) ? 1 : dayOf(a) > dayOf(b) ? -1 : 0));
}
