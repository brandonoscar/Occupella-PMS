import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Budget } from "../budgets";
import { holdBack } from "../holdBack";
import type { OwnerMoney } from "../payables";
import type { Preview } from "../reports";
import { BudgetsPage } from "./BudgetsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const owner = (n: number, property = n): OwnerMoney => ({
  account: `a${n}`, owner_id: `o${n}`, owner: `Owner ${n}`, property_id: `pr${property}`, property: `Property ${property}`,
  bank_account: "Operating", balance: "0.00", reserve: "0.00", unpaid_bills: "0.00",
  available: "0.00", has_agreement: true, payout_method: "check",
});

const budgetOf = (account: string, year: number, property = `Property ${account.slice(1)}`): Budget => ({
  account, owner_id: `o${account.slice(1)}`, owner: `Owner ${account.slice(1)}`, property, year,
  lines: [
    { line: "rent", expense_account_id: null, label: "income: rent", months: Array(12).fill("100.00"), year: "1200.00" },
    { line: "late fees", expense_account_id: null, label: "income: late fees", months: Array(12).fill("0.00"), year: "0.00" },
    { line: "expense account", expense_account_id: "e1", label: "expense: Repairs",
      months: [...Array(11).fill("0.00"), "50.00"], year: "50.00" },
  ],
});

const VERSUS: Preview = {
  title: "Budget vs actual",
  columns: ["line", "property", "item", "budget", "actual", "actual_less_budget"],
  rows: [
    ["1", "Synthetic PMC", "PMC", "", "", ""],
    ["2", "Property 1", "income: rent", "1200.00", "1500.00", "300.00"],
    ["3", "Property 1", "total income", "1200.00", "1500.00", "300.00"],
    ["4", "Property 1", "net income", "1150.00", "1490.00", "340.00"],
    ["5", "Property 9", "income: rent", "1.00", "1.00", "0.00"],
    ["6", "narrowed to Property 1", "", "", "", ""],
  ],
  total_rows: 6,
};

function server(owners: OwnerMoney[] = [owner(2), owner(1)], refuse = "", versus = (_path: string) => VERSUS) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return refuse
        ? new Response(JSON.stringify({ detail: refuse }), { status: 404 })
        : new Response(JSON.stringify({ changed: 13 }), { status: 200 });
    }
    if (path === "/api/payables/owners") return new Response(JSON.stringify(owners), { status: 200 });
    if (path.startsWith("/api/reports/")) return new Response(JSON.stringify(versus(path)), { status: 200 });
    const query = new URLSearchParams(path.split("?")[1]);
    const account = query.get("account") ?? "";
    const at = owners.find((o) => o.account === account)?.property;
    return new Response(JSON.stringify(budgetOf(account, Number(query.get("year")), at)), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const cell = (name: string) => screen.getByLabelText(name) as HTMLInputElement;
const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, i]) => i?.method === "POST").map(([p, i]) => [p, JSON.parse(String(i?.body))]);

describe("BudgetsPage", () => {
  it("shows the first property's year in one grid: each line by month, its total and the net income", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-03-01T12:00:00Z") });
    const fetch = server();
    render(<BudgetsPage me={ME} />);

    await screen.findByRole("row", { name: "Income: rent" });
    expect(fetch.mock.calls.map(([path]) => path)).toContain("/api/budgets?account=a1&year=2026");
    expect(within(screen.getByLabelText("Property")).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Property 1", "Property 2",
    ]);
    // One owner at the property: no owner to choose.
    expect(screen.queryByLabelText("Owner")).toBeNull();
    const grid = screen.getByRole("table", { name: "Budget" });
    expect(within(grid).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Line", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Total",
    ]);
    expect(cell("Income: rent, March").value).toBe("100.00");
    expect(cell("Income: rent, total").value).toBe("1200.00");
    expect(cell("Expense: Repairs, total").value).toBe("50.00");
    const net = within(grid).getByRole("row", { name: "Net income" });
    expect(within(net).getAllByRole("cell").map((c) => c.textContent).slice(-2)).toEqual(["$50.00", "$1,150.00"]);
    expect(screen.getByRole("link", { name: "Export" }).getAttribute("href")).toBe(
      "/api/reports/trust_report_budget_vs_actual?arg=o1&arg=2026-01-01&arg=2026-12-31&format=xlsx",
    );
  });

  it("saves every line at once, a total typed in spread over its months, and says how many months changed", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-03-01T12:00:00Z") });
    const fetch = server();
    render(<BudgetsPage me={ME} />);
    await screen.findByRole("row", { name: "Income: rent" });

    fireEvent.change(cell("Income: rent, March"), { target: { value: "1,250" } });
    expect(cell("Income: rent, total").value).toBe("2350.00");
    expect(screen.getByText("Unsaved changes")).toBeTruthy();
    fireEvent.change(cell("Income: late fees, total"), { target: { value: "1000" } });
    fireEvent.blur(cell("Income: late fees, total"));
    expect(cell("Income: late fees, January").value).toBe("83.33");
    expect(cell("Income: late fees, December").value).toBe("83.37");
    // A total left as it was spreads nothing.
    fireEvent.change(cell("Expense: Repairs, total"), { target: { value: "50.00" } });
    fireEvent.keyDown(cell("Expense: Repairs, total"), { key: "Enter" });
    expect(cell("Expense: Repairs, December").value).toBe("50.00");

    fireEvent.click(screen.getByRole("button", { name: "Save budget" }));
    expect(await screen.findByText("Saved Property 1's 2026 budget: 13 months changed.")).toBeTruthy();
    const [[path, body]] = posted(fetch) as [[string, { account: string; year: number; lines: { line: string; months: string[] }[] }]];
    expect(path).toBe("/api/budgets");
    expect([body.account, body.year, body.lines.length]).toEqual(["a1", 2026, 3]);
    expect(body.lines[0]!.months[2]).toBe("1250");
    expect(body.lines[1]!.months[11]).toBe("83.37");
    expect(body.lines[2]).toMatchObject({ line: "expense account", expense_account_id: "e1" });
    // The grid starts again from what the server holds.
    await vi.waitFor(() => expect(cell("Income: rent, March").value).toBe("100.00"));
  });

  it("names a month that isn't an amount instead of saving, and says why the server refused", async () => {
    const fetch = server(undefined, "no such expense account");
    render(<BudgetsPage me={ME} />);
    await screen.findByRole("row", { name: "Income: rent" });

    fireEvent.change(cell("Expense: Repairs, May"), { target: { value: "lots" } });
    expect(cell("Expense: Repairs, May").getAttribute("aria-invalid")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Save budget" }));
    expect(await screen.findByText("Expense: Repairs, May: enter an amount, like 1200.00.")).toBeTruthy();
    expect(posted(fetch)).toEqual([]);

    fireEvent.change(cell("Expense: Repairs, May"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save budget" }));
    expect(await screen.findByText("no such expense account")).toBeTruthy();
  });

  it("picks another property and year, and an owner where a property has more than one", async () => {
    const fetch = server([owner(1), owner(2), owner(3, 2)]);
    render(<BudgetsPage me={ME} />);
    await screen.findByRole("row", { name: "Income: rent" });

    fireEvent.change(screen.getByLabelText("Property"), { target: { value: "pr2" } });
    expect(within(screen.getByLabelText("Owner")).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Owner 2", "Owner 3",
    ]);
    fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "a3" } });
    fireEvent.change(screen.getByLabelText("Year"), { target: { value: "2027" } });
    await vi.waitFor(() =>
      expect(fetch.mock.calls.map(([path]) => path)).toContain("/api/budgets?account=a3&year=2027"),
    );
    expect(fetch.mock.calls.map(([path]) => path)).toContain("/api/budgets?account=a2&year=2026");
    // A year still being typed asks for nothing.
    fireEvent.change(screen.getByLabelText("Year"), { target: { value: "20" } });
    expect(fetch.mock.calls.map(([path]) => path)).not.toContain("/api/budgets?account=a3&year=20");
  });

  it("lays the property's lines against what happened, from the report narrowed to it", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-03-01T12:00:00Z") });
    const fetch = server();
    render(<BudgetsPage me={ME} />);
    await screen.findByRole("row", { name: "Income: rent" });

    fireEvent.click(screen.getByRole("tab", { name: "Budget vs actual" }));
    const table = await screen.findByRole("table", { name: "Budget vs actual" });
    expect(fetch.mock.calls.map(([path]) => path)).toContain(
      "/api/reports/trust_report_budget_vs_actual?arg=o1&arg=2026-01-01&arg=2026-12-31&format=json&property_id=pr1",
    );
    expect(within(table).getAllByRole("row").slice(1).map((r) => r.getAttribute("aria-label"))).toEqual([
      "Income: rent", "Total income", "Net income",
    ]);
    expect(within(within(table).getByRole("row", { name: "Income: rent" })).getAllByRole("cell").map((c) => c.textContent))
      .toEqual(["Income: rent", "$1,200.00", "$1,500.00", "$300.00"]);
    expect(screen.queryByRole("table", { name: "Budget" })).toBeNull();
  });

  it("says when no property has an owner, and when the owners can't be read", async () => {
    server([]);
    render(<BudgetsPage me={ME} />);
    expect(await screen.findByText("No property has an owner yet")).toBeTruthy();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<BudgetsPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });

  it("shows the budget of the property picked now, even when an earlier property's answer comes back last", async () => {
    server([owner(1), owner(2), owner(3)]);
    const release = holdBack((path) => path.startsWith("/api/budgets?account=a2&"));
    render(<BudgetsPage me={ME} />);
    await screen.findByRole("row", { name: "Income: rent" });

    fireEvent.change(screen.getByLabelText("Property"), { target: { value: "pr2" } });
    fireEvent.change(screen.getByLabelText("Property"), { target: { value: "pr3" } });
    await vi.waitFor(() => expect(screen.getByRole("link", { name: "Export" }).getAttribute("href")).toContain("?arg=o3&"));
    await release();

    expect(screen.getByRole("link", { name: "Export" }).getAttribute("href")).toContain("?arg=o3&");
  });

  it("lays the property picked now against what happened, even when an earlier property's answer comes back last", async () => {
    const second: Preview = { ...VERSUS, rows: [["1", "Property 2", "income: rent", "900.00", "950.00", "50.00"]], total_rows: 1 };
    server([owner(1), owner(2)], "", (path) => (path.includes("property_id=pr2") ? second : VERSUS));
    const release = holdBack((path) => path.startsWith("/api/reports/") && path.includes("property_id=pr1"));
    render(<BudgetsPage me={ME} />);
    await screen.findByRole("row", { name: "Income: rent" });
    const rows = () =>
      within(screen.getByRole("table", { name: "Budget vs actual" })).getAllByRole("row").slice(1)
        .map((r) => within(r).getAllByRole("cell").map((c) => c.textContent));

    fireEvent.click(screen.getByRole("tab", { name: "Budget vs actual" }));
    fireEvent.change(screen.getByLabelText("Property"), { target: { value: "pr2" } });
    await vi.waitFor(() => expect(rows()).toEqual([["Income: rent", "$900.00", "$950.00", "$50.00"]]));
    await release();

    expect(rows()).toEqual([["Income: rent", "$900.00", "$950.00", "$50.00"]]);
  });
});
