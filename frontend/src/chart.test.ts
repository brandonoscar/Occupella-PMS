import { describe, expect, it } from "vitest";
import { type ChartAccount, CHART_CSV, accountPath, bySection, codeBody, codeOk } from "./chart";

function account(over: Partial<ChartAccount>): ChartAccount {
  return {
    section: "income", code: "4000", name: "Rent", type: "Income line", line: "rent",
    charge_type_id: null, expense_account_id: null, schedule_e_line: null, kept_by: "owner",
    archived: false, ...over,
  };
}

describe("the chart of accounts", () => {
  it("groups accounts by section in the chart's order, leaving out a section with none", () => {
    const sections = bySection([
      account({ section: "expense", code: "6401", name: "Plumbing repairs" }),
      account({ section: "asset", code: "1000", name: "Trust bank cash" }),
      account({}),
    ]);
    expect(sections.map((s) => [s.label, s.accounts.map((a) => a.code)])).toEqual([
      ["Assets", ["1000"]],
      ["Income", ["4000"]],
      ["Expenses", ["6401"]],
    ]);
  });

  it("names the account a code change is for, and only charge types and expense accounts rename", () => {
    expect(codeBody(account({}), "4001")).toEqual({ line: "rent", code: "4001" });
    const kind = account({ line: null, charge_type_id: "t1", code: "4501" });
    const bills = account({ section: "expense", line: null, expense_account_id: "e1" });
    expect(codeBody(kind, "4510")).toEqual({ charge_type_id: "t1", code: "4510" });
    expect(codeBody(bills, "6450")).toEqual({ expense_account_id: "e1", code: "6450" });
    expect(accountPath(account({}))).toBeNull();
    expect(accountPath(kind)).toBe("/api/setup/charge-types/t1");
    expect(accountPath(bills)).toBe("/api/setup/expense-accounts/e1");
  });

  it("takes a code of four digits from 1000", () => {
    expect(["4001", " 6401 ", "1000"].map(codeOk)).toEqual([true, true, true]);
    expect(["401", "40011", "0400", "4a01", ""].map(codeOk)).toEqual([false, false, false, false, false]);
  });

  it("downloads as CSV through the reports' export", () => {
    expect(CHART_CSV).toBe("/api/reports/trust_report_chart_of_accounts?format=csv");
  });
});
