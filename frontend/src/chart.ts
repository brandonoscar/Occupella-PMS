// Settings, Chart of accounts (the founder's chart of accounts pass, 2026-11-05): every account of
// the company's chart by section and code, as GET /api/setup/chart-of-accounts gives it
// (src/pms/api/chart.py), and what each edit posts.
import type { ScheduleELine } from "./setup";

export type Section = "asset" | "liability" | "income" | "expense";
export type Payee = "owner" | "company";

export type ChartAccount = {
  section: Section;
  code: string;
  name: string;
  type: string;
  // Exactly one names the account: a fixed line, a charge type or an expense account.
  line: string | null;
  charge_type_id: string | null;
  expense_account_id: string | null;
  schedule_e_line: number | null;
  kept_by: Payee | null;
  archived: boolean;
};

export type Chart = { accounts: ChartAccount[]; schedule_e_lines: ScheduleELine[] };

// The sections in the order a chart lists them; the trust ledger holds no equity of its own.
const SECTIONS: { section: Section; label: string }[] = [
  { section: "asset", label: "Assets" },
  { section: "liability", label: "Liabilities" },
  { section: "income", label: "Income" },
  { section: "expense", label: "Expenses" },
];

// Each section's accounts, in the order the API gave them (by code); a section with none is left
// out.
export function bySection(accounts: ChartAccount[]): { section: Section; label: string; accounts: ChartAccount[] }[] {
  return SECTIONS.map((s) => ({ ...s, accounts: accounts.filter((a) => a.section === s.section) }))
    .filter((s) => s.accounts.length > 0);
}

// What names the account in a code change (POST /api/setup/codes).
export function codeBody(account: ChartAccount, code: string): Record<string, string> {
  if (account.charge_type_id) return { charge_type_id: account.charge_type_id, code };
  if (account.expense_account_id) return { expense_account_id: account.expense_account_id, code };
  return { line: account.line ?? "", code };
}

// The account's own path for a rename or an archive; null for a fixed line, which keeps its name.
export function accountPath(account: ChartAccount): string | null {
  if (account.charge_type_id) return `/api/setup/charge-types/${account.charge_type_id}`;
  if (account.expense_account_id) return `/api/setup/expense-accounts/${account.expense_account_id}`;
  return null;
}

// A code as the database takes it: four digits, from 1000.
export function codeOk(code: string): boolean {
  return /^[1-9][0-9]{3}$/.test(code.trim());
}

// The range a section's codes are in, as the page says it.
export const RANGES: Record<Section, string> = {
  asset: "1000 to 1999",
  liability: "2000 to 2999",
  income: "4000 to 4999",
  expense: "5000 to 6999",
};

// The CSV of the chart: its report through the export pipeline, which writes no formula.
export const CHART_CSV = "/api/reports/trust_report_chart_of_accounts?format=csv";

export const PAYEES: { value: Payee; label: string }[] = [
  { value: "owner", label: "The owner" },
  { value: "company", label: "The company" },
];
