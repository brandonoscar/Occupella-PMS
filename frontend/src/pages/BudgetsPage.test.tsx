import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Budget } from "../budgets";
import type { OwnerMoney } from "../payables";
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

const owner = (n: number): OwnerMoney => ({
  account: `a${n}`, owner_id: `o${n}`, owner: `Owner ${n}`, property: `Property ${n}`,
  bank_account: "Operating", balance: "0.00", reserve: "0.00", unpaid_bills: "0.00",
  available: "0.00", has_agreement: true,
});

const budgetOf = (account: string, year: number): Budget => ({
  account, owner_id: `o${account.slice(1)}`, owner: `Owner ${account.slice(1)}`,
  property: `Property ${account.slice(1)}`, year,
  lines: [
    { line: "rent", expense_account_id: null, label: "income: rent", months: Array(12).fill("100.00"), year: "1200.00" },
    { line: "expense account", expense_account_id: "e1", label: "expense: Repairs",
      months: [...Array(11).fill("0.00"), "50.00"], year: "50.00" },
    { line: "late fees", expense_account_id: null, label: "income: late fees", months: Array(12).fill("0.00"), year: "0.00" },
  ],
});

function server(owners: OwnerMoney[] = [owner(1), owner(2)], refuse = "") {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return refuse
        ? new Response(JSON.stringify({ detail: refuse }), { status: 404 })
        : new Response(JSON.stringify({ changed: 12 }), { status: 200 });
    }
    if (path === "/api/payables/owners") return new Response(JSON.stringify(owners), { status: 200 });
    const query = new URLSearchParams(path.split("?")[1]);
    return new Response(JSON.stringify(budgetOf(query.get("account") ?? "", Number(query.get("year")))), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("BudgetsPage", () => {
  it("shows an owner's property's budget for this year, line by line, and links it against actual", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-03-01T12:00:00Z") });
    const fetch = server();
    render(<BudgetsPage me={ME} />);

    const rent = await screen.findByRole("row", { name: /^Income: rent/ });
    expect((within(rent).getByLabelText("Income: rent, a year") as HTMLInputElement).value).toBe("1200.00");
    expect(rent.textContent).toContain("$100.00 a month");
    expect(screen.getByRole("row", { name: /^Expense: Repairs/ }).textContent).toContain("Varies by month");
    expect(screen.getByRole("row", { name: /^Income: late fees/ }).textContent).toContain("Not budgeted");
    expect(fetch.mock.calls.map(([path]) => path)).toContain("/api/budgets?account=a1&year=2026");
    expect(screen.getByRole("link", { name: "Owner 1's budget against actual, 2026 (Excel)" }).getAttribute("href")).toBe(
      "/api/reports/trust_report_budget_vs_actual?arg=o1&arg=2026-01-01&arg=2026-12-31&format=xlsx",
    );

    fireEvent.change(screen.getByLabelText("Owner and property"), { target: { value: "a2" } });
    expect(await screen.findByRole("link", { name: "Owner 2's budget against actual, 2026 (Excel)" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Year"), { target: { value: "2027" } });
    expect(await screen.findByRole("link", { name: "Owner 2's budget against actual, 2027 (Excel)" })).toBeTruthy();
  });

  it("saves a line's year spread over its months", async () => {
    const fetch = server();
    render(<BudgetsPage me={ME} />);
    const rent = await screen.findByRole("row", { name: /^Income: rent/ });

    fireEvent.change(within(rent).getByLabelText("Income: rent, a year"), { target: { value: "1000" } });
    fireEvent.click(within(rent).getByRole("button", { name: "Save income: rent" }));

    expect(await screen.findByText(/^Income: rent: 12 months changed in \d{4}\.$/)).toBeTruthy();
    const [, init] = fetch.mock.calls.find(([, i]) => i?.method === "POST") ?? [];
    const body = JSON.parse(String(init?.body));
    expect(body).toMatchObject({ account: "a1", line: "rent", expense_account_id: null });
    expect(body.months).toEqual([...Array(11).fill("83.33"), "83.37"]);
  });

  it("says what is wrong with an amount, or with the save", async () => {
    server([owner(1)], "no such expense account");
    render(<BudgetsPage me={ME} />);
    const repairs = await screen.findByRole("row", { name: /^Expense: Repairs/ });

    fireEvent.change(within(repairs).getByLabelText("Expense: Repairs, a year"), { target: { value: "lots" } });
    fireEvent.click(within(repairs).getByRole("button", { name: "Save expense: repairs" }));
    expect(repairs.textContent).toContain("Enter an amount, like 1200.00");

    fireEvent.change(within(repairs).getByLabelText("Expense: Repairs, a year"), { target: { value: "600" } });
    fireEvent.click(within(repairs).getByRole("button", { name: "Save expense: repairs" }));
    expect((await screen.findByRole("alert")).textContent).toBe("no such expense account");
  });

  it("asks for an owner's property first", async () => {
    server([]);
    render(<BudgetsPage me={ME} />);
    expect(await screen.findByText("No owner has a property account yet. Add one in Setup.")).toBeTruthy();
  });
});
