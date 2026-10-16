import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Breakdown, RegisterLine } from "../banking";
import { BreakdownTab, RegisterTab } from "./BankAccountTabs";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const LINES: RegisterLine[] = [
  {
    transfer_id: "t2", on: "2026-10-15", type: "Owner draw", party: "Owner 1", memo: "Owner draw",
    property: "Property 1", deposit: null, withdrawal: "300.00", balance: "700.00",
  },
  {
    transfer_id: "t1", on: "2026-10-10", type: "Rent payment", party: "Tenant A", memo: "Rent – Tenant A, Property 1 Unit 1",
    property: "Property 1", deposit: "1000.00", withdrawal: null, balance: "1000.00",
  },
];

function server(answer: (path: string) => unknown) {
  const fetch = vi.fn(async (path: string) => {
    const body = answer(path);
    return typeof body === "string"
      ? new Response(JSON.stringify({ detail: body }), { status: 403 })
      : new Response(JSON.stringify(body), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

// A table's body rows, each as its cells' text.
const cells = (table: HTMLElement) =>
  within(table)
    .getAllByRole("row")
    .filter((row) => within(row).queryAllByRole("cell").length > 0)
    .map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent));

describe("RegisterTab", () => {
  it("lists each move of the account's cash, newest first, with the balance after it, a page at a time", async () => {
    const fetch = server(() => ({ items: LINES, total: 52, page: 1, per_page: 50 }));
    render(<RegisterTab bankId="b1" />);

    const register = await screen.findByRole("table", { name: "Register" });
    expect(cells(register)).toEqual([
      ["10/15/2026", "Owner draw", "Owner 1", "Owner draw", "Property 1", "", "$300.00", "$700.00"],
      ["10/10/2026", "Rent payment", "Tenant A", "Rent – Tenant A, Property 1 Unit 1", "Property 1", "$1,000.00", "", "$1,000.00"],
    ]);
    // The last ninety days at first.
    const first = new URL(String(fetch.mock.calls[0]?.[0]), "http://pms.test");
    expect(first.pathname).toBe("/api/bank-accounts/b1/register");
    const days = (Date.parse(first.searchParams.get("end")!) - Date.parse(first.searchParams.get("start")!)) / 86_400_000;
    expect(days).toBe(90);

    fireEvent.click(within(screen.getByRole("navigation", { name: "Pages" })).getByRole("button", { name: "Next" }));
    await vi.waitFor(() => expect(String(fetch.mock.calls.at(-1)?.[0])).toContain("page=2"));
    // New days start again from the first page.
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-10-12" } });
    await vi.waitFor(() =>
      expect(fetch.mock.calls.at(-1)?.[0]).toBe("/api/bank-accounts/b1/register?start=2026-10-01&end=2026-10-12&page=1"),
    );
  });

  it("says when no money moved in the days, and when the register can't be read", async () => {
    server(() => ({ items: [], total: 0, page: 1, per_page: 50 }));
    render(<RegisterTab bankId="b1" />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No money moved in these days.");
    cleanup();

    server(() => "only staff may do that");
    render(<RegisterTab bankId="b1" />);
    expect((await screen.findByRole("alert")).textContent).toBe("only staff may do that");
  });
});

const BREAKDOWN: Breakdown = {
  book_cash: "725.00",
  held: [
    { kind: "owner_property", label: "Owners' money", amount: "700.00" },
    { kind: "pmc_income", label: "The company's fees", amount: "25.00" },
  ],
  owners: [
    {
      account: "a1", owner_id: "o1", owner: "Owner 1", property_id: "pr1", property: "Property 1",
      bank_account: "Operating", balance: "700.00", reserve: "100.00", unpaid_bills: "50.00", available: "550.00",
      has_agreement: true,
    },
  ],
};

describe("BreakdownTab", () => {
  it("shows what the account holds by kind, its book cash, and each owner's money in it", async () => {
    const fetch = server(() => BREAKDOWN);
    render(<BreakdownTab bankId="b1" />);

    const held = await screen.findByLabelText("What it holds");
    expect(held.textContent).toBe("Owners' money$700.00The company's fees$25.00Book cash$725.00");
    expect(screen.getByTestId("breakdown-cash").textContent).toBe("$725.00");
    const owner = screen.getByRole("row", { name: "Owner 1, Property 1" });
    expect(within(owner).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Owner 1", "Property 1", "$700.00", "$100.00", "$50.00", "$550.00",
    ]);
    expect(within(owner).getByRole("link", { name: "Owner 1" }).getAttribute("href")).toBe("/owners/o1");
    expect(within(owner).getByRole("link", { name: "Property 1" }).getAttribute("href")).toBe("/properties/pr1");
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/bank-accounts/b1/breakdown");
  });

  it("leaves out the owners where none has money in the account, and says when it can't be read", async () => {
    server(() => ({ book_cash: "900.00", held: [{ kind: "tenant_deposit", label: "Tenants' security deposits", amount: "900.00" }], owners: [] }));
    render(<BreakdownTab bankId="b2" />);
    expect((await screen.findByLabelText("What it holds")).textContent).toContain("Tenants' security deposits$900.00");
    expect(screen.queryByRole("table", { name: "By owner" })).toBeNull();
    cleanup();

    server(() => "only staff may do that");
    render(<BreakdownTab bankId="b2" />);
    expect((await screen.findByRole("alert")).textContent).toBe("only staff may do that");
  });
});
