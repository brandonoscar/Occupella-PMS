import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BankSettings, PayoutRun } from "../payouts";
import { achLine, PayoutFiles, PayoutsTab } from "./PayoutFiles";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const BARE: BankSettings = {
  bank_account_id: "b1", bank_routing_number: null, bank_name: null, origin_id: null, origin_name: null,
  company_id: null, company_name: null, first_check_number: null, ach_ready: false, next_check_number: 1,
  changed_at: null, changed_by: null,
};

const RUN: PayoutRun = {
  id: "r1", kind: "owners", method: "EFT", bank_account_id: "b1", bank_account: "Operating", paid_on: "2026-10-24",
  payouts: 2, total: "650.00", recorded_by: "Staff 1", recorded_at: "2026-10-24T09:30:00Z", downloads: 1,
};

function server(settings: BankSettings, runs: PayoutRun[], refuse?: string) {
  let now = settings;
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 422 });
      const body = JSON.parse(String(init.body));
      now = { ...now, ...body, ach_ready: body.bank_name !== null, next_check_number: body.first_check_number ?? 1 };
      return new Response(JSON.stringify(now), { status: 200 });
    }
    if (path.startsWith("/api/payouts/runs")) return new Response(JSON.stringify(runs), { status: 200 });
    return new Response(JSON.stringify(now), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("PayoutFiles", () => {
  it("lists each run with the link to its file", async () => {
    const fetch = server(BARE, [RUN, { ...RUN, id: "r2", kind: "bills", method: "check", payouts: 1, total: "40.00", downloads: 0 }]);
    render(<PayoutFiles query={{ kind: "owners" }} />);
    const table = await screen.findByRole("table", { name: "Payout files" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getAllByRole("cell").slice(0, 7).map((c) => c.textContent))).toEqual([
      ["10/24/2026", "Owners", "ACH", "Operating", "2", "$650.00", "Staff 1"],
      ["10/24/2026", "Bills", "Check", "Operating", "1", "$40.00", "Staff 1"],
    ]);
    expect(within(rows[0]!).getByRole("link", { name: "Download the ACH file" }).getAttribute("href")).toBe("/api/payouts/runs/r1/ach");
    expect(within(rows[0]!).getByText("Taken 1 time")).toBeTruthy();
    expect(within(rows[1]!).getByRole("link", { name: "Print the checks" }).getAttribute("href")).toBe("/api/payouts/runs/r2/checks");
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/payouts/runs?kind=owners");
  });

  it("says when no file was made yet", async () => {
    server(BARE, []);
    render(<PayoutFiles query={{}} />);
    expect(await screen.findByText(/No payout file made yet/)).toBeTruthy();
  });
});

describe("PayoutsTab", () => {
  it("says an account without its bank's details makes no ACH file", () => {
    expect(achLine(BARE)).toBe("Not set: ACH files can't be made from this account yet.");
  });

  it("saves the bank's ACH details and the first check number, all at once", async () => {
    const fetch = server(BARE, []);
    render(<PayoutsTab bankId="b1" />);
    expect((await screen.findByTestId("ach-details")).textContent).toContain("Not set");
    expect(screen.getByTestId("next-check").textContent).toBe("Number 1");
    fireEvent.click(screen.getByRole("button", { name: "Edit ACH and checks" }));
    const panel = screen.getByRole("dialog", { name: "ACH and checks" });
    const type = (label: string, value: string) => fireEvent.change(within(panel).getByLabelText(label), { target: { value } });
    type("Bank's routing number", "001234561");
    type("Bank's name", "Synthetic Bank");
    type("Origin ID", "1000000000");
    type("Origin name", "Golden PMC");
    type("Company ID", "1000000000");
    type("Company name", "Golden");
    type("First check number", "2001");
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved the account's ACH details and checks.")).toBeTruthy();
    expect((await screen.findByTestId("ach-details")).textContent).toBe(
      "Synthetic Bank, routing 001234561; origin 1000000000 Golden PMC; company 1000000000 Golden",
    );
    expect(screen.getByTestId("next-check").textContent).toBe("Number 2001");
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/payouts/bank-accounts/b1");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      bank_routing_number: "001234561", bank_name: "Synthetic Bank", origin_id: "1000000000",
      origin_name: "Golden PMC", company_id: "1000000000", company_name: "Golden", first_check_number: 2001,
    });
    // The account's own files are listed below.
    expect(fetch.mock.calls.some(([path]) => path === "/api/payouts/runs?bank_account_id=b1")).toBe(true);
  });

  it("keeps the panel open with why the details were refused", async () => {
    server(BARE, [], "an ACH file needs every one of the bank's details, or none");
    render(<PayoutsTab bankId="b1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit ACH and checks" }));
    const panel = screen.getByRole("dialog", { name: "ACH and checks" });
    fireEvent.change(within(panel).getByLabelText("Bank's name"), { target: { value: "Synthetic Bank" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect((await within(panel).findByRole("alert")).textContent).toContain("every one of the bank's details");
  });
});
