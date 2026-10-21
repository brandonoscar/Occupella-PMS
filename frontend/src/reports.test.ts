import { describe, expect, it } from "vitest";
import { GROUPS, REPORTS, columnLabel, defaultFilters, presetDays, reportArgs, reportHref, reportSpec } from "./reports";

const NOW = new Date("2026-03-10T02:00:00Z");

describe("reports", () => {
  it("puts every report in a group, each once", () => {
    expect(new Set(REPORTS.map((r) => r.name)).size).toBe(REPORTS.length);
    expect(REPORTS.every((r) => GROUPS.includes(r.group))).toBe(true);
    expect(REPORTS.filter((r) => r.group === "Financial").map((r) => r.label)).toEqual([
      "Balance sheet", "Income statement", "Cash flow", "Trial balance", "General ledger", "Security deposits",
    ]);
    expect(reportSpec("vendor_payments")?.label).toBe("1099 summary");
    expect(reportSpec("nope")).toBeUndefined();
  });

  it("works out each preset's days in UTC", () => {
    expect(presetDays("this-month", NOW)).toEqual({ from: "2026-03-01", to: "2026-03-10" });
    expect(presetDays("last-month", NOW)).toEqual({ from: "2026-02-01", to: "2026-02-28" });
    expect(presetDays("ytd", NOW)).toEqual({ from: "2026-01-01", to: "2026-03-10" });
    expect(presetDays("last-year", NOW)).toEqual({ from: "2025-01-01", to: "2025-12-31" });
    expect(defaultFilters(NOW)).toEqual({ asOf: "2026-03-10", from: "2026-03-01", to: "2026-03-10", owner: "", year: "2026" });
  });

  it("builds each report's arguments from the filters, and none while one is missing", () => {
    const filters = { ...defaultFilters(NOW), owner: "o1" };
    expect(reportArgs(reportSpec("rent_roll")!, filters)).toEqual(["2026-03-10"]);
    expect(reportArgs(reportSpec("income_statement")!, filters)).toEqual(["o1", "2026-03-01", "2026-03-10"]);
    expect(reportArgs(reportSpec("owner_year_end")!, filters)).toEqual(["o1", "2026"]);
    // An owner statement runs from the start of its first day to the end of its last (UTC).
    expect(reportArgs(reportSpec("owner_statement")!, { ...filters, to: "2026-02-28" })).toEqual([
      "o1", "2026-03-01T00:00:00Z", "2026-03-01T00:00:00Z",
    ]);
    expect(reportArgs(reportSpec("income_statement")!, { ...filters, owner: "" })).toBeNull();
  });

  it("links the preview and each export, narrowed only when the report is by property", () => {
    const roll = reportSpec("rent_roll")!;
    expect(reportHref(roll, ["2026-03-10"], { group: "g1" }, "json")).toBe(
      "/api/reports/trust_report_rent_roll?arg=2026-03-10&format=json&group=g1",
    );
    expect(reportHref(roll, ["2026-03-10"], {}, "pdf")).toBe("/api/reports/trust_report_rent_roll?arg=2026-03-10&format=pdf");
    // The trial balance narrows by each account's property (q109); the balance sheet, by trust
    // bank account, does not.
    expect(reportHref(reportSpec("trial_balance")!, ["2026-03-10"], { property: "p1" }, "csv")).toBe(
      "/api/reports/trust_report_trial_balance?arg=2026-03-10&format=csv&property=p1",
    );
    expect(reportHref(reportSpec("balance_sheet")!, ["2026-03-10"], { property: "p1" }, "csv", "o1")).toBe(
      "/api/reports/trust_report_balance_sheet?arg=2026-03-10&format=csv",
    );
    // An owner's properties, with or without a group.
    expect(reportHref(roll, ["2026-03-10"], { group: "g1" }, "json", "o1")).toBe(
      "/api/reports/trust_report_rent_roll?arg=2026-03-10&format=json&group=g1&owner=o1",
    );
  });

  it("names a column as people read it", () => {
    expect(columnLabel("monthly_rent")).toBe("Monthly rent");
    expect(columnLabel("item")).toBe("Item");
  });
});
