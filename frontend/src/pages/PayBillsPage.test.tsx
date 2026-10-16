import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Bill, OwnerMoney } from "../payables";
import { PayBillsPage, cashOf } from "./PayBillsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState(null, "", "/");
  window.localStorage.clear();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1", pmc_name: "Synthetic PMC",
};
const bill = (n: number, amount: string, status: Bill["status"], account = "a1"): Bill => ({
  id: `b${n}`, vendor_id: "v1", vendor: "Vendor 1", reference: `INV-${n}`, account, owner: "Owner 1",
  property_id: "pr1", property: account === "a1" ? "Property 1" : "Property 2", bill_date: "2026-10-01",
  due_on: `2026-10-1${n}`, amount, memo: "Repair", work_order_id: null, expense_account_id: null,
  expense_account: null, split: false, approval_limit: "1000.00", approved_by: null, declined_by: null,
  decline_reason: null, owner_email: null, status, overdue: false,
});
const BILLS = [bill(1, "200.00", "to set aside"), bill(2, "150.00", "set aside"), bill(3, "900.00", "needs approval"), bill(4, "50.00", "to set aside", "a2")];
const owner = (account: string, property: string, balance: string): OwnerMoney => ({
  account, owner_id: "o1", owner: "Owner 1", property_id: "pr1", property, bank_account: "Operating", balance,
  reserve: "0.00", unpaid_bills: "0.00", available: balance, has_agreement: true,
});
const OWNERS = [owner("a1", "Property 1", "150.00"), owner("a2", "Property 2", "500.00")];

function server(refuse: Record<string, string> = {}, bills: Bill[] = BILLS) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      const read = path.startsWith("/api/payables/bills") ? { items: bills, total: bills.length, page: 1, per_page: 200 }
        : path.startsWith("/api/payables/owners") ? OWNERS : { groups: [], properties: [] };
      return new Response(JSON.stringify(read), { status: 200 });
    }
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    return new Response(JSON.stringify([
      { bill_id: "b1", vendor: "Vendor 1", reference: "INV-1", amount: "200.00", transfer_id: "t1" },
      { bill_id: "b2", vendor: "Vendor 1", reference: "INV-2", amount: "150.00", transfer_id: "t2" },
    ]), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("cashOf", () => {
  it("takes each property's bills not set aside yet from what it holds", () => {
    expect(cashOf(BILLS, new Set(["b1", "b2", "b4"]), OWNERS)).toEqual([
      { account: "a1", owner: "Owner 1", property: "Property 1", holds: "150.00", chosen: "200.00", after: "-50.00" },
      { account: "a2", owner: "Owner 1", property: "Property 2", holds: "500.00", chosen: "50.00", after: "450.00" },
    ]);
    expect(cashOf(BILLS, new Set(["b2"]), OWNERS)).toEqual([]);
  });
});

describe("PayBillsPage", () => {
  it("lists the bills ready to pay, shows each property's cash, and pays those chosen after asking", async () => {
    const fetch = server();
    render(<PayBillsPage me={ME} />);
    await screen.findByRole("row", { name: "Vendor 1 INV-1" });
    // Waiting for the owner, a bill is not ready to pay.
    expect(screen.queryByRole("row", { name: "Vendor 1 INV-3" })).toBeNull();
    expect(screen.getByRole("button", { name: "Choose bills to pay" })).toHaveProperty("disabled", true);

    fireEvent.click(screen.getByLabelText("Pay Vendor 1 INV-1"));
    fireEvent.click(screen.getByLabelText("Pay Vendor 1 INV-2"));
    const cash = within(screen.getByRole("table", { name: "Each property's cash" })).getByRole("row", { name: "Owner 1, Property 1" });
    expect(within(cash).getAllByRole("cell").map((c) => c.textContent)).toEqual(["Owner 1, Property 1", "$150.00", "$200.00", "($50.00)", "Short"]);
    fireEvent.change(screen.getByLabelText("Sent by"), { target: { value: "EFT" } });
    fireEvent.change(screen.getByLabelText("Paid on"), { target: { value: "2026-10-16" } });
    fireEvent.click(screen.getByRole("button", { name: "Pay $350.00" }));
    const confirm = screen.getByRole("alertdialog");
    expect(confirm.textContent).toContain("Pay $350.00 for 2 bills by EFT on 10/16/2026?");
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm" }));

    expect(await screen.findByText("2 bills of $350.00 paid, by EFT.")).toBeTruthy();
    const [[path, init]] = fetch.mock.calls.filter(([, i]) => i?.method === "POST") as [[string, RequestInit]];
    expect([path, JSON.parse(String(init.body))]).toEqual([
      "/api/payables/bills/pay", { on: "2026-10-16", method: "EFT", bill_ids: ["b1", "b2"] },
    ]);
    expect(screen.getByRole("button", { name: "Choose bills to pay" })).toBeTruthy();
  });

  it("chooses every bill and none, and says why a payment was refused", async () => {
    server({ "/api/payables/bills/pay": "Vendor 1 INV-1: not enough money: the owner's account would be $50.00 short" });
    render(<PayBillsPage me={ME} />);
    await screen.findByRole("row", { name: "Vendor 1 INV-1" });
    fireEvent.click(screen.getByLabelText("Choose every bill"));
    expect(screen.getByRole("button", { name: "Pay $400.00" })).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Choose every bill"));
    expect(screen.getByRole("button", { name: "Choose bills to pay" })).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Pay Vendor 1 INV-1"));
    fireEvent.click(screen.getByRole("button", { name: "Pay $200.00" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Confirm" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Vendor 1 INV-1: not enough money: the owner's account would be $50.00 short",
    );
  });

  it("says when no bill is ready to pay, and when the bills can't be read", async () => {
    server({}, [bill(3, "900.00", "needs approval")]);
    render(<PayBillsPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No bill is ready to pay.");
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<PayBillsPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });
});
