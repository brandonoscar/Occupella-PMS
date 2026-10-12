// Staff's month-end as the API sends it (src/pms/api/reconcile.py), and how it reads.
import { day, money } from "./money";

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
export type BankBooks = {
  id: string;
  name: string;
  kind: "operating" | "security_deposit";
  closed_through: string | null;
  next_start: string | null;
  statements: StatementRow[];
  reconciliations: Approved[];
};
export type ReportRow = { line: number; item: string; detail: string | null; amount: string | null };
export type OpenLine = {
  id: string;
  posted_on: string;
  amount: string;
  description: string;
  reference: string | null;
};
export type OpenTransfer = { id: string; on: string; amount: string; memo: string | null };
export type StandingMatch = { id: string; lines: OpenLine[]; transfers: OpenTransfer[] };
export type Worksheet = {
  through: string | null;
  rows: ReportRow[];
  open_lines: OpenLine[];
  open_transfers: OpenTransfer[];
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
  return `${line.posted_on} ${line.description || "No description"}${ref}: ${money(line.amount)}`;
}

export function transferLabel(transfer: OpenTransfer): string {
  return `${transfer.on} ${transfer.memo ?? "No memo"}: ${money(transfer.amount)}`;
}

// A period ends at midnight UTC after its last day; the day staff read is that last day.
export function lastDayOf(periodEnd: string): string {
  return day(new Date(new Date(periodEnd).getTime() - 86_400_000));
}
