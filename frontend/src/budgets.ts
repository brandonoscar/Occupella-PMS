// An owner's property account's budget as the API sends it (src/pms/api/budgets.py): each line
// of the income statement, month by month, for a year.
import { fromCents, toCents } from "./reconcile";

export type BudgetLine = {
  line: string;
  expense_account_id: string | null;
  label: string; // "income: rent", "expense: Repairs"
  months: string[]; // January to December
  year: string;
};
export type Budget = {
  account: string;
  owner_id: string;
  owner: string;
  property: string;
  year: number;
  lines: BudgetLine[];
};

// A year's amount spread over its twelve months to the cent; what doesn't divide goes to
// December, so the months add up to the year exactly.
export function spread(year: string): string[] {
  const cents = toCents(year);
  if (cents < 0) throw new Error(`a budget is not negative: ${year}`);
  const month = Math.floor(cents / 12);
  return [...Array(11).fill(fromCents(month)), fromCents(cents - 11 * month)];
}

// "$100.00 a month", or how it varies.
export function monthly(line: BudgetLine): string {
  const [first] = line.months;
  return line.months.every((m) => m === first) ? `${first} a month` : "varies by month";
}

// "income: late fees" reads "Income: late fees".
export function lineLabel(line: BudgetLine): string {
  return line.label.charAt(0).toUpperCase() + line.label.slice(1);
}

export function budgetVsActualHref(ownerId: string, year: number): string {
  const query = new URLSearchParams([
    ["arg", ownerId],
    ["arg", `${year}-01-01`],
    ["arg", `${year}-12-31`],
    ["format", "xlsx"],
  ]);
  return `/api/reports/trust_report_budget_vs_actual?${query}`;
}
