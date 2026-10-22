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
// A row of trust_report_bank_reconciliation. `pending`: a reconciling item that may only be
// waiting to be matched (a bank line with a transfer or deposit of its amount open in the books,
// or a transfer with a bank line of its amount not matched yet); the report counts it either way.
export type ReportRow = { line: number; item: string; detail: string | null; amount: string | null; pending?: boolean };
// The bank lines up to the day that no match holds: those with a transfer or bank deposit of their
// amount open in the books (likely in the books already; Match the sure ones pairs `sure` of them
// now), and those with nothing of their amount there (alone).
export type Unmatched = {
  lines: number;
  total: string;
  sure: number;
  likely: number;
  likely_total: string;
  alone: number;
  alone_total: string;
};
export type OpenLine = {
  id: string;
  posted_on: string;
  amount: string;
  description: string;
  reference: string | null;
};
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
  // None before a statement covers the day.
  unmatched: Unmatched | null;
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

// A reconciling item as the page names it. Before matching, the same money is both a bank line
// "not in the books" and a transfer "in transit": one that may only wait to be matched says so.
const WAITING: Record<string, string> = {
  "bank line not in the books": "bank line not matched yet",
  "deposit in transit": "deposit not matched yet",
  "outstanding withdrawal": "withdrawal not matched yet",
};
export function itemWord(row: ReportRow): string {
  return row.pending ? (WAITING[row.item] ?? row.item) : row.item;
}

const bankLines = (n: number) => `${n} bank line${n === 1 ? "" : "s"}`;
const have = (n: number) => (n === 1 ? "has" : "have");

// Where the reconciliation stands, as its badge says: it ties out; bank lines are not matched
// yet (never "off by" money counted twice before matching); or it is off by what is left.
export function standing(gap: string, unmatched: Unmatched | null): { tone: "positive" | "caution"; word: string } {
  if (gap === "0.00") return { tone: "positive", word: "Ties out" };
  if (unmatched && unmatched.lines > 0) return { tone: "caution", word: `${bankLines(unmatched.lines)} not matched` };
  return { tone: "caution", word: `Off by ${money(gap)}` };
}

// What to do before the period can be prepared. Matching comes first: a line with a transfer of
// its amount in the books is likely there already (Match the sure ones, then by hand); only a
// line with nothing of its amount there may be missing from the books, and is recorded only if
// it is.
export function nextSteps(gap: string, unmatched: Unmatched | null): string[] {
  if (gap === "0.00") return [];
  if (!unmatched || unmatched.lines === 0) return [`Off by ${money(gap)}: the reconciling items say where.`];
  const said: string[] = [];
  const { likely, sure, alone } = unmatched;
  if (likely > 0) {
    const them = likely === 1 ? "it" : "them";
    const how = sure === 0 ? `match ${them} by hand`
      : sure === likely ? `Match the sure ones pairs ${likely === 1 ? "it" : "all of them"}`
      : `start with Match the sure ones (it pairs ${sure}), then match the rest by hand`;
    said.push(
      `${bankLines(likely)} of ${money(unmatched.likely_total)} ${have(likely)} a transfer of the same amount in the`
        + ` books, so ${likely === 1 ? "it is" : "they are"} likely there already: ${how}.`,
    );
  }
  if (alone > 0) {
    const each = alone === 1 ? "it" : "each";
    said.push(
      `${bankLines(alone)} of ${money(unmatched.alone_total)} ${have(alone)} nothing of ${alone === 1 ? "its" : "their"}`
        + ` amount in the books: match ${each} to the transfers it adds up to, or, if the books lack it, record it`
        + " and then match it.",
    );
  }
  return said;
}

// Until the lines with a transfer of their amount are matched, the figures count that money
// twice: in the bank balance, and again in transit (or outstanding).
export function countedTwice(unmatched: Unmatched | null): string | null {
  if (!unmatched || unmatched.likely === 0) return null;
  return "Until they are matched, money both the bank and the books show counts twice in these figures:"
    + " in the bank balance, and again in transit or outstanding. The figures settle as you match.";
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
