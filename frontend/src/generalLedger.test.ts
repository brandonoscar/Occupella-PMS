import { describe, expect, it } from "vitest";
import { ledgerHref, monthToDate, nonZero } from "./generalLedger";

describe("the general ledger", () => {
  it("opens on this month so far", () => {
    expect(monthToDate("2026-10-16")).toEqual({ from: "2026-10-01", to: "2026-10-16" });
  });

  it("downloads the same days as the report", () => {
    expect(ledgerHref("2026-10-01", "2026-10-16", "csv")).toBe(
      "/api/reports/trust_report_general_ledger?arg=2026-10-01&arg=2026-10-16&format=csv",
    );
  });

  it("leaves a zero debit or credit blank", () => {
    expect([nonZero("0.00"), nonZero("80.00")]).toEqual([null, "80.00"]);
  });
});
