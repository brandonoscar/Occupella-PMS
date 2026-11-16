import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { OwnerHome, youOweLine } from "./OwnerHome";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "owner", email: "owner@example.com", name: "Owner 1", person_id: "o1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

// The statement as the API sends it: trust_report_owner_statement's rows, each posting naming the
// other side by its ledger kind.
const STATEMENT = {
  title: "Owner statement",
  columns: ["line", "property", "posted_on", "item", "detail", "amount", "balance"],
  rows: [
    ["1", null, null, "period", "2026-09-01 00:00:00 to 2026-10-01 00:00:00 UTC", null, null],
    ["2", "Property 1", null, "opening balance", null, null, "250.00"],
    ["3", "Property 1", "2026-09-04", "posting", "bank_cash: Operating trust account", "1430.00", "1680.00"],
    ["4", "Property 1", "2026-09-30", "posting", "pmc_income: Synthetic PMC - Management fee 2026-09", "-143.00", "1537.00"],
    ["5", "Property 1", "2026-09-30", "posting", "bank_cash: Operating trust account - Owner draw", "-1287.00", "250.00"],
    ["6", "Property 1", null, "closing balance", null, null, "250.00"],
    ["7", null, null, "all properties: opening balance", null, null, "250.00"],
    ["8", null, null, "all properties: money in", null, "1430.00", null],
    ["9", null, null, "all properties: money out", null, "-1430.00", null],
    ["10", null, null, "all properties: closing balance", null, null, "250.00"],
  ],
};

const OWNED = [{ account_id: "a1", property_id: "pr1", name: "Property 1", bank: "Operating trust account", balance: "250.00", owns_today: true }];

function server(properties: unknown[] = OWNED) {
  const fetch = vi.fn(async (path: string) => {
    const body = path.startsWith("/api/owner/properties")
      ? properties
      : path.startsWith("/api/owner/statement")
        ? STATEMENT
        : path.startsWith("/api/owner/year-end")
          ? { title: "Year-end", columns: ["property", "item", "amount"], rows: [] }
          : path.startsWith("/api/payments/owner")
            ? { available: false, why_not: "off", how_to_pay: null, accounts: [], payments: [] }
            : [];
    return new Response(JSON.stringify(body), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
}

describe("OwnerHome", () => {
  it("links each property they own today, and only shows the money of one they no longer own", async () => {
    // docs/edge-cases.md U-085: sold, or its ownership moved to another owner.
    server([
      ...OWNED,
      { account_id: "a2", property_id: "pr2", name: "Property 2", bank: "Operating trust account", balance: "0.00", owns_today: false },
    ]);
    render(<OwnerHome me={ME} section="properties" />);

    const owned = await screen.findByRole("link", { name: "Property 1" });
    expect(owned.getAttribute("href")).toBe("/properties/pr1");
    expect(screen.queryByRole("link", { name: /Property 2/ })).toBeNull();
    expect(screen.getByText("No longer yours")).toBeTruthy();
  });

  it("reads the statement in words: the period, and what each posting was and with whom", async () => {
    server();
    render(<OwnerHome me={ME} section="statements" />);

    expect(await screen.findByText("September 1 to September 30, 2026")).toBeTruthy();
    const statement = screen.getByRole("table", { name: "Property 1" });
    const rows = within(statement)
      .getAllByRole("row")
      .map((row) => within(row).queryAllByRole("cell").map((cell) => cell.textContent));
    expect(rows.slice(2, 5)).toEqual([
      ["09/04/2026", "Received · Operating trust account", "$1,430.00", "$1,680.00"],
      ["09/30/2026", "Management fee 2026-09 · Synthetic PMC", "($143.00)", "$1,537.00"],
      ["09/30/2026", "Owner draw · Operating trust account", "($1,287.00)", "$250.00"],
    ]);
    // No ledger kind reaches the owner, and money is in the page's own face, not mono.
    expect(statement.textContent).not.toMatch(/bank_cash|pmc_income/);
    expect(statement.querySelector(".font-mono")).toBeNull();
  });
});

// docs/edge-cases.md M-060: what the owner owes the company back, in plain words.
describe("youOweLine", () => {
  it("says what the owner owes the company and why", () => {
    expect(youOweLine("Property 1", "110.00")).toBe(
      "You owe the company $110.00 for Property 1: a tenant's payment came back after your money had gone out, and the company covered your account from its own money.",
    );
  });
});
