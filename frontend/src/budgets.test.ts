import { describe, expect, it } from "vitest";
import type { BudgetLine } from "./budgets";
import { MONTHS, budgetVsActualHref, isAmount, lineLabel, spread, total } from "./budgets";

const line = (months: string[]): BudgetLine => ({
  line: "rent", expense_account_id: null, label: "income: rent", months, year: "",
});

describe("budgets", () => {
  it("spreads a year over its months to the cent, the rest in December", () => {
    expect(spread("1200.00")).toEqual(Array(12).fill("100.00"));
    const odd = spread("1000.00");
    expect(odd.slice(0, 11)).toEqual(Array(11).fill("83.33"));
    expect(odd[11]).toBe("83.37");
    expect(spread("0.05")).toEqual([...Array(11).fill("0.00"), "0.05"]);
    expect(spread("1,234.5")[0]).toBe("102.87");
    expect(() => spread("-1.00")).toThrow("a budget is not negative");
  });

  it("reads a month as typed: whole cents, not negative, within the API's limit", () => {
    expect(["1200", "1,200.50", "0.5", " 7 "].every(isAmount)).toBe(true);
    expect(["", "-1", "1.001", "12a", "100000000.01"].some(isAmount)).toBe(false);
    expect(isAmount("100000000.00")).toBe(true);
  });

  it("totals the months, counting one not yet an amount as nothing, and names the line", () => {
    expect(total(Array(12).fill("100.00"))).toBe("1200.00");
    expect(total(["1,000.10", "oops", ...Array(10).fill("")])).toBe("1000.10");
    expect(MONTHS).toHaveLength(12);
    expect(lineLabel(line([]))).toBe("Income: rent");
  });

  it("links the owner's year of budget against actual", () => {
    expect(budgetVsActualHref("o1", 2026)).toBe(
      "/api/reports/trust_report_budget_vs_actual?arg=o1&arg=2026-01-01&arg=2026-12-31&format=xlsx",
    );
    expect(budgetVsActualHref("o1", 2026, "json", "p1")).toBe(
      "/api/reports/trust_report_budget_vs_actual?arg=o1&arg=2026-01-01&arg=2026-12-31&format=json&property_id=p1",
    );
  });
});
