import { describe, expect, it } from "vitest";
import { staffDownloads } from "./reports";

describe("staffDownloads", () => {
  it("names each report as of today (UTC), cash flow over last month, and the whole ledger", () => {
    const links = Object.fromEntries(
      staffDownloads(new Date("2026-03-01T02:00:00Z")).map((d) => [d.label, d.href]),
    );

    expect(links["Balance sheet (PDF)"]).toBe("/api/reports/trust_report_balance_sheet?arg=2026-03-01&format=pdf");
    expect(links["Cash flow, last month (Excel)"]).toBe(
      "/api/reports/trust_report_cash_flow?arg=2026-02-01&arg=2026-02-28&format=xlsx",
    );
    expect(links["Vendor directory (Excel)"]).toBe(
      "/api/reports/trust_report_vendor_directory?arg=2026-03-01&format=xlsx",
    );
    expect(Object.keys(links)).toEqual([
      "Rent roll (PDF)",
      "Trial balance (Excel)",
      "Balance sheet (PDF)",
      "Cash flow, last month (Excel)",
      "Vacancy (PDF)",
      "Work orders (Excel)",
      "Leases ending in 90 days (Excel)",
      "Tenant directory (Excel)",
      "Owner directory (Excel)",
      "Vendor directory (Excel)",
      "Whole ledger (CSV, zip)",
    ]);
    expect(links["Whole ledger (CSV, zip)"]).toBe("/api/ledger.zip");
  });
});
