// The reports staff open from Reports (ADR 0006, step 11): each trust_report_* in its group, the
// filters its arguments take (a day, days with presets, an owner, a year), whether it can be
// narrowed to a property group or property, and its links: the preview on screen
// (format=json) and Export as PDF, Excel or CSV, through /api/reports (src/pms/api/app.py),
// which takes the company from the session.
import { day } from "./money";
import type { Scope } from "./scope";
import { scopeParams } from "./scope";

// What a report's arguments are after the PMC, in order.
export type Arg = "asOf" | "from" | "to" | "owner" | "year" | "periodStart" | "periodEnd";

export type ReportSpec = {
  name: string; // without trust_report_
  label: string;
  group: string;
  args: Arg[];
  // Its rows have a property, so it narrows to a property group or property.
  byProperty: boolean;
};

const AS_OF: Arg[] = ["asOf"];
const DAYS: Arg[] = ["from", "to"];

export const REPORTS: ReportSpec[] = [
  { name: "balance_sheet", label: "Balance sheet", group: "Financial", args: AS_OF, byProperty: false },
  { name: "income_statement", label: "Income statement", group: "Financial", args: ["owner", "from", "to"], byProperty: false },
  { name: "cash_flow", label: "Cash flow", group: "Financial", args: DAYS, byProperty: false },
  { name: "trial_balance", label: "Trial balance", group: "Financial", args: AS_OF, byProperty: false },
  { name: "general_ledger", label: "General ledger", group: "Financial", args: DAYS, byProperty: false },
  { name: "security_deposits", label: "Security deposits", group: "Financial", args: AS_OF, byProperty: true },
  { name: "rent_roll", label: "Rent roll", group: "Rentals", args: AS_OF, byProperty: true },
  { name: "delinquency", label: "Delinquency", group: "Rentals", args: AS_OF, byProperty: true },
  { name: "vacancy", label: "Vacancy", group: "Rentals", args: AS_OF, byProperty: true },
  { name: "lease_expirations", label: "Lease expirations", group: "Rentals", args: AS_OF, byProperty: true },
  { name: "tenant_directory", label: "Tenant directory", group: "Rentals", args: AS_OF, byProperty: true },
  { name: "renters_insurance", label: "Renters insurance", group: "Rentals", args: AS_OF, byProperty: true },
  { name: "owner_statement", label: "Owner statement", group: "Owners", args: ["owner", "periodStart", "periodEnd"], byProperty: false },
  { name: "owner_directory", label: "Owner directory", group: "Owners", args: AS_OF, byProperty: true },
  { name: "owner_balances", label: "Owner balances", group: "Owners", args: AS_OF, byProperty: true },
  { name: "owner_year_end", label: "Owner year-end", group: "Owners", args: ["owner", "year"], byProperty: false },
  { name: "budget_vs_actual", label: "Budget vs actual", group: "Owners", args: ["owner", "from", "to"], byProperty: false },
  { name: "vendor_directory", label: "Vendor directory", group: "Vendors", args: AS_OF, byProperty: false },
  { name: "vendor_insurance", label: "Vendor insurance", group: "Vendors", args: AS_OF, byProperty: false },
  { name: "vendor_payments", label: "1099 summary", group: "Vendors", args: ["year"], byProperty: false },
  { name: "unpaid_bills", label: "Unpaid bills", group: "Vendors", args: AS_OF, byProperty: true },
  { name: "work_orders", label: "Work orders", group: "Maintenance", args: AS_OF, byProperty: true },
  { name: "trust_account_signers", label: "Trust account signers", group: "Compliance", args: AS_OF, byProperty: false },
  { name: "audit_log", label: "Audit log", group: "Compliance", args: DAYS, byProperty: false },
];

export const GROUPS = ["Financial", "Rentals", "Owners", "Vendors", "Maintenance", "Compliance"];

export function reportSpec(name: string): ReportSpec | undefined {
  return REPORTS.find((r) => r.name === name);
}

// What the filters say: a day (asOf), days from and to, an owner and a year.
export type Filters = { asOf: string; from: string; to: string; owner: string; year: string };

export type Preset = "this-month" | "last-month" | "ytd" | "last-year";
export const PRESETS: { value: Preset; label: string }[] = [
  { value: "this-month", label: "This month" },
  { value: "last-month", label: "Last month" },
  { value: "ytd", label: "Year to date" },
  { value: "last-year", label: "Last year" },
];

// A preset's days as of now (UTC): this month and the year to date run through today.
export function presetDays(preset: Preset, now: Date): { from: string; to: string } {
  const y = now.getUTCFullYear();
  const m = now.getUTCMonth();
  const utc = (year: number, month: number, date: number) => day(new Date(Date.UTC(year, month, date)));
  if (preset === "this-month") return { from: utc(y, m, 1), to: day(now) };
  if (preset === "last-month") return { from: utc(y, m - 1, 1), to: utc(y, m, 0) };
  if (preset === "ytd") return { from: utc(y, 0, 1), to: day(now) };
  return { from: utc(y - 1, 0, 1), to: utc(y - 1, 11, 31) };
}

export function defaultFilters(now: Date): Filters {
  return { asOf: day(now), ...presetDays("this-month", now), owner: "", year: String(now.getUTCFullYear()) };
}

// The day after a day, at its start in UTC: an owner statement runs to the end of its last day.
function nextMidnight(iso: string): string {
  const next = new Date(`${iso}T00:00:00Z`);
  next.setUTCDate(next.getUTCDate() + 1);
  return next.toISOString().replace(".000", "");
}

// The report's arguments after the PMC, from the filters; null while one it needs is missing.
export function reportArgs(spec: ReportSpec, filters: Filters): string[] | null {
  const args = spec.args.map((arg) => {
    if (arg === "asOf") return filters.asOf;
    if (arg === "from") return filters.from;
    if (arg === "to") return filters.to;
    if (arg === "owner") return filters.owner;
    if (arg === "year") return filters.year;
    if (arg === "periodStart") return filters.from && `${filters.from}T00:00:00Z`;
    return filters.to && nextMidnight(filters.to);
  });
  return args.every((a) => a) ? args : null;
}

// The report's address with its arguments, narrowed to the scope when it is by property.
export function reportHref(spec: ReportSpec, args: string[], scope: Scope, format: "json" | "pdf" | "xlsx" | "csv"): string {
  const query = new URLSearchParams([
    ...args.map((arg) => ["arg", arg]),
    ["format", format],
    ...(spec.byProperty ? Object.entries(scopeParams(scope)) : []),
  ]);
  return `/api/reports/trust_report_${spec.name}?${query}`;
}

// A report's download from the page that shows it (a lease's ledger, a month-end): the same
// report Reports exports, with the page's own arguments.
export function downloadHref(name: string, args: string[], format: "pdf" | "xlsx" | "csv"): string {
  const query = new URLSearchParams([...args.map((arg) => ["arg", arg]), ["format", format]]);
  return `/api/reports/trust_report_${name}?${query}`;
}

export const FORMATS = [["pdf", "PDF"], ["csv", "CSV"], ["xlsx", "Excel"]] as const;

// A report on screen: its columns and rows as text, and how many rows it has in all.
export type Preview = { title: string; columns: string[]; rows: string[][]; total_rows: number };

// "monthly_rent" reads "Monthly rent".
export function columnLabel(column: string): string {
  const words = column.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
