import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { groupIncomeStatement } from "../incomeStatement";
import type { YearEndData } from "../yearEnd";
import { OwnerIncomeStatement } from "./OwnerIncomeStatement";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const answer = (body: unknown, status = 200) =>
  vi.fn(async (_path: string) => new Response(JSON.stringify(body), { status }));

const line = (n: number, property: string | null, item: string, schedule: number | null, amount: string) => [
  String(n), property, item, schedule === null ? null : String(schedule), amount,
];
// As trust_report_income_statement sends it (its golden case's layout), cut short.
const STATEMENT: YearEndData = {
  title: "Income statement, 2025",
  columns: ["line", "property", "item", "schedule_e", "amount"],
  rows: [
    ["1", "Synthetic PMC", "PMC", null, null],
    ["2", "Owner 1", "owner", null, null],
    ["3", "2025-01-01 to 2025-12-31 UTC, cash basis", "period", null, null],
    line(4, "Property 1", "income: rent", 3, "1500.00"),
    line(5, "Property 1", "income: late fees", 3, "50.00"),
    line(6, "Property 1", "total income", null, "1550.00"),
    line(7, "Property 1", "expense: management fees", 11, "120.00"),
    line(8, "Property 1", "expense: Repairs", 14, "200.00"),
    line(9, "Property 1", "total expenses", null, "320.00"),
    line(10, "Property 1", "net income", null, "1230.00"),
    line(11, "Property 1", "Schedule E line 3: Rents received", 3, "1550.00"),
    line(12, "Property 1", "Schedule E line 14: Repairs", 14, "200.00"),
    line(13, null, "all properties: income: rent", 3, "1500.00"),
    line(14, null, "all properties: total income", null, "1550.00"),
    line(15, null, "all properties: net income", null, "1230.00"),
  ],
};

describe("groupIncomeStatement", () => {
  it("groups each property's income, expenses, net income and Schedule E lines, then the totals", () => {
    const grouped = groupIncomeStatement(STATEMENT);

    expect(grouped.period).toBe("2025-01-01 to 2025-12-31 UTC, cash basis");
    expect(grouped.sections.map((s) => s.name)).toEqual(["Property 1", "All properties"]);
    const [first, all] = grouped.sections;
    expect(first?.income).toEqual([
      { label: "Rent", amount: "1500.00" },
      { label: "Late fees", amount: "50.00" },
    ]);
    expect(first?.expenses).toEqual([
      { label: "Management fees", amount: "120.00" },
      { label: "Repairs", amount: "200.00" },
    ]);
    expect([first?.totalIncome, first?.totalExpenses, first?.net]).toEqual(["1550.00", "320.00", "1230.00"]);
    expect(first?.scheduleE.map((l) => l.label)).toEqual([
      "Schedule E line 3: Rents received",
      "Schedule E line 14: Repairs",
    ]);
    expect([all?.income.length, all?.totalIncome, all?.net]).toEqual([1, "1550.00", "1230.00"]);
  });

  it("refuses rows without the report's columns", () => {
    expect(() => groupIncomeStatement({ ...STATEMENT, columns: ["line", "item"] })).toThrow(
      "the income statement has no property column",
    );
  });
});

describe("OwnerIncomeStatement", () => {
  it("shows last year's statement by property with its Schedule E lines, and downloads it", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-03-01T12:00:00Z") });
    const fetch = answer(STATEMENT);
    vi.stubGlobal("fetch", fetch);
    render(<OwnerIncomeStatement />);

    const property = await screen.findByRole("table", { name: "Property 1" });
    expect(within(property).getByRole("row", { name: "Repairs $200.00" })).toBeTruthy();
    expect(within(property).getByRole("row", { name: "Net income $1,230.00" })).toBeTruthy();
    const scheduleE = screen.getByRole("list", { name: "Schedule E, Property 1" });
    expect(scheduleE.textContent).toContain("Schedule E line 14: Repairs$200.00");
    expect(screen.getByRole("table", { name: "All properties" }).textContent).toContain("$1,550.00");
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/owner/income-statement?year=2025");
    expect(screen.getByRole("link", { name: "Excel" }).getAttribute("href")).toBe(
      "/api/owner/income-statement/download?year=2025&format=xlsx",
    );
  });

  it("shows another year, and says why one can't be read", async () => {
    const fetch = answer(STATEMENT);
    vi.stubGlobal("fetch", fetch);
    render(<OwnerIncomeStatement />);
    await screen.findByRole("table", { name: "Property 1" });

    fetch.mockImplementation(async () => new Response(JSON.stringify({ detail: "no such year" }), { status: 422 }));
    fireEvent.change(screen.getByLabelText("Year"), { target: { value: "2024" } });
    fireEvent.click(screen.getByRole("button", { name: "Show" }));

    expect((await screen.findByRole("alert")).textContent).toBe("no such year");
    expect(fetch.mock.calls.at(-1)?.[0]).toBe("/api/owner/income-statement?year=2024");
    expect(screen.queryByRole("table")).toBeNull();
  });
});
