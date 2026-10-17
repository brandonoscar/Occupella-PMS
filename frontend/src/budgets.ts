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

export const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

// A month's budget as typed: whole cents, not negative, at most 100,000,000.00 (the API's limit).
export function isAmount(text: string): boolean {
  const plain = text.replace(/,/g, "").trim();
  return /^\d+(\.\d{1,2})?$/.test(plain) && toCents(plain) <= 10_000_000_000;
}

// The months' total, as text; a month not yet an amount counts as nothing.
export function total(months: string[]): string {
  return fromCents(months.reduce((sum, m) => sum + (isAmount(m) ? toCents(m.replace(/,/g, "")) : 0), 0));
}

// "income: late fees" reads "Income: late fees".
export function lineLabel(line: BudgetLine): string {
  return line.label.charAt(0).toUpperCase() + line.label.slice(1);
}

// The owner's year of budget against what happened, as the report writes it: for Export, or
// (format "json", narrowed to one property) for the Budget vs actual view.
export function budgetVsActualHref(ownerId: string, year: number, format = "xlsx", propertyId = ""): string {
  const query = new URLSearchParams([
    ["arg", ownerId],
    ["arg", `${year}-01-01`],
    ["arg", `${year}-12-31`],
    ["format", format],
  ]);
  if (propertyId) query.set("property_id", propertyId);
  return `/api/reports/trust_report_budget_vs_actual?${query}`;
}
