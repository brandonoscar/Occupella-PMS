// The reports staff download from the overview: each trust_report_* as of today (UTC), or over
// last month, through /api/reports (src/pms/api/app.py), which takes the company from the
// session.
import { day, lastMonth } from "./money";

export type Download = { label: string; href: string };

function report(name: string, format: "pdf" | "xlsx", ...args: string[]): string {
  const query = new URLSearchParams([...args.map((arg) => ["arg", arg]), ["format", format]]);
  return `/api/reports/trust_report_${name}?${query}`;
}

export function staffDownloads(now: Date): Download[] {
  const today = day(now);
  const month = lastMonth(now);
  return [
    { label: "Rent roll (PDF)", href: report("rent_roll", "pdf", today) },
    { label: "Trial balance (Excel)", href: report("trial_balance", "xlsx", today) },
    { label: "Balance sheet (PDF)", href: report("balance_sheet", "pdf", today) },
    { label: "Cash flow, last month (Excel)", href: report("cash_flow", "xlsx", month.start, month.end) },
    { label: "Vacancy (PDF)", href: report("vacancy", "pdf", today) },
    { label: "Work orders (Excel)", href: report("work_orders", "xlsx", today) },
    { label: "Leases ending in 90 days (Excel)", href: report("lease_expirations", "xlsx", today) },
    { label: "Tenant directory (Excel)", href: report("tenant_directory", "xlsx", today) },
    { label: "Owner directory (Excel)", href: report("owner_directory", "xlsx", today) },
    { label: "Vendor directory (Excel)", href: report("vendor_directory", "xlsx", today) },
    { label: "Whole ledger (CSV, zip)", href: "/api/ledger.zip" },
  ];
}
