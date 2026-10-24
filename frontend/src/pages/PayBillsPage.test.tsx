import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { holdBack } from "../holdBack";
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
const bill = (n: number, amount: string, status: Bill["status"], account = "a1", more: Partial<Bill> = {}): Bill => ({
  id: `b${n}`, vendor_id: "v1", vendor: "Vendor 1", reference: `INV-${n}`, account, owner_id: "o1", owner: "Owner 1",
  property_id: "pr1", property: account === "a1" ? "Property 1" : "Property 2", bill_date: "2026-10-01",
  due_on: `2026-10-1${n}`, amount, memo: "Repair", work_order_id: null, expense_account_id: null,
  expense_account: null, split: false, approval_limit: "1000.00", approved_by: null, declined_by: null,
  decline_reason: null, voided: false, owner_email: null, status, overdue: false, markup: null, markup_percent: null,
  shared_bill_id: null, paid: "0.00", left: amount, held: amount, held_since: null, short: null, needed: null, ...more,
});
// Ready to pay; ready, its money taken before ADR 0010 (set aside); waiting for the owner; ready.
const BILLS = [
  bill(1, "200.00", "ready to pay"), bill(2, "150.00", "ready to pay", "a1", { held_since: "2026-10-05" }),
  bill(3, "900.00", "waiting for owner"), bill(4, "50.00", "ready to pay", "a2"),
];
const owner = (account: string, property: string, balance: string): OwnerMoney => ({
  account, owner_id: "o1", owner: "Owner 1", property_id: "pr1", property, bank_account: "Operating", balance,
  reserve: "0.00", unpaid_bills: "0.00", available: balance, has_agreement: true, payout_method: "check",
});
const OWNERS = [owner("a1", "Property 1", "150.00"), owner("a2", "Property 2", "500.00")];

function server(refuse: Record<string, string> = {}, bills: Bill[] = BILLS) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      const read = path.startsWith("/api/payables/bills") ? { items: bills, total: bills.length, page: 1, per_page: 200 }
        : path.startsWith("/api/payables/owners") ? OWNERS
        : path.startsWith("/api/payouts/runs") ? []
        : { groups: [], properties: [] };
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
  it("takes a bill's markup from the owner with it", () => {
    const marked = BILLS.map((b) => (b.id === "b4" ? { ...b, markup: "5.00", markup_percent: "10" } : b));
    expect(cashOf(marked, new Set(["b4"]), OWNERS)).toEqual([
      { account: "a2", owner: "Owner 1", property: "Property 2", holds: "500.00", chosen: "55.00", after: "445.00" },
    ]);
  });
});

describe("PayBillsPage", () => {
  it("pays what is left of a bill paid in part, by check with each check's number, and says why a short bill waits", async () => {
    const part = bill(5, "500.00", "partly paid", "a1", { paid: "200.00", left: "300.00", held: "300.00", held_since: "2026-10-05" });
    const short = bill(6, "800.00", "short", "a2", { held: "500.00", short: "300.00", needed: "300.00" });
    const fetch = server({}, [part, short]);
    render(<PayBillsPage me={ME} />);
    await screen.findByRole("row", { name: "Vendor 1 INV-5" });
    expect(screen.queryByRole("row", { name: "Vendor 1 INV-6" })).toBeNull();
    expect(screen.getByRole("list", { name: "Not ready to pay" }).textContent).toBe(
      "Vendor 1 INV-6, $800.00: Owner 1's balance is $300.00 short; they need to send $300.00.",
    );
    fireEvent.click(screen.getByLabelText("Pay Vendor 1 INV-5"));
    fireEvent.change(screen.getByLabelText("Check number, Vendor 1 INV-5"), { target: { value: "1042" } });
    fireEvent.click(screen.getByRole("button", { name: "Pay $300.00" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Pay" }));

    await screen.findByText("2 bills of $350.00 paid, by check.");
    const [[, init]] = fetch.mock.calls.filter(([, i]) => i?.method === "POST") as [[string, RequestInit]];
    expect(JSON.parse(String(init.body)).check_numbers).toEqual({ b5: "1042" });
  });

  it("lists the bills ready to pay, shows each property's cash, and pays those chosen after asking", async () => {
    const fetch = server();
    render(<PayBillsPage me={ME} />);
    await screen.findByRole("row", { name: "Vendor 1 INV-1" });
    // Waiting for the owner, a bill is not ready to pay, and says so.
    expect(screen.queryByRole("row", { name: "Vendor 1 INV-3" })).toBeNull();
    expect(screen.getByRole("list", { name: "Not ready to pay" }).textContent).toBe(
      "Vendor 1 INV-3, $900.00: waits for Owner 1's approval.",
    );
    expect(screen.getByRole("button", { name: "Choose bills to pay" })).toHaveProperty("disabled", true);

    fireEvent.click(screen.getByLabelText("Pay Vendor 1 INV-1"));
    fireEvent.click(screen.getByLabelText("Pay Vendor 1 INV-2"));
    const cash = within(screen.getByRole("table", { name: "Each property's cash" })).getByRole("row", { name: "Owner 1, Property 1" });
    expect(within(cash).getAllByRole("cell").map((c) => c.textContent)).toEqual(["Owner 1, Property 1", "$150.00", "$200.00", "($50.00)", "Short"]);
    fireEvent.change(screen.getByLabelText("Sent by"), { target: { value: "EFT" } });
    fireEvent.change(screen.getByLabelText("Paid on"), { target: { value: "2026-10-16" } });
    fireEvent.click(screen.getByRole("button", { name: "Pay $350.00" }));
    const confirm = screen.getByRole("alertdialog");
    expect(confirm.textContent).toContain(
      "Pay $350.00 for 2 bills by ACH on 10/16/2026? It comes out of each property's money in trust.",
    );
    fireEvent.click(within(confirm).getByRole("button", { name: "Pay" }));

    expect(await screen.findByText("2 bills of $350.00 paid, by ACH.")).toBeTruthy();
    const [[path, init]] = fetch.mock.calls.filter(([, i]) => i?.method === "POST") as [[string, RequestInit]];
    expect([path, JSON.parse(String(init.body))]).toEqual([
      "/api/payables/bills/pay",
      { on: "2026-10-16", method: "EFT", bill_ids: ["b1", "b2"], check_numbers: {}, output: "record", request_id: expect.any(String) },
    ]);
    expect(screen.getByRole("button", { name: "Choose bills to pay" })).toBeTruthy();
  });

  it("prints the checks when asked: no check numbers to type, the request keyed, its file listed below", async () => {
    const fetch = server();
    render(<PayBillsPage me={ME} />);
    await screen.findByRole("row", { name: "Vendor 1 INV-1" });
    fireEvent.click(screen.getByLabelText("Pay Vendor 1 INV-1"));
    expect(screen.getByLabelText("Check number, Vendor 1 INV-1")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Payout file"), { target: { value: "file" } });
    expect(screen.queryByLabelText("Check number, Vendor 1 INV-1")).toBeNull();
    expect(within(screen.getByLabelText("Payout file")).getByRole("option", { name: "Checks to print" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Pay $200.00" }));
    const confirm = screen.getByRole("alertdialog");
    expect(confirm.textContent).toContain("The PMS prints the checks; the bank moves the money.");
    fireEvent.click(within(confirm).getByRole("button", { name: "Pay" }));

    expect(await screen.findByText("2 bills of $350.00 paid, by check. Their file is under Payout files below.")).toBeTruthy();
    const posts = fetch.mock.calls.filter(([, i]) => i?.method === "POST").map(([, i]) => JSON.parse(String(i?.body)));
    expect(posts[0]).toMatchObject({ method: "check", output: "file", check_numbers: {} });
    await vi.waitFor(() =>
      expect(fetch.mock.calls.filter(([path]) => path === "/api/payouts/runs?kind=bills").length).toBeGreaterThan(1));
    // The next payment is a new request.
    fireEvent.click(screen.getByLabelText("Pay Vendor 1 INV-1"));
    fireEvent.change(screen.getByLabelText("Sent by"), { target: { value: "EFT" } });
    expect(within(screen.getByLabelText("Payout file")).getByRole("option", { name: "ACH file" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Pay $200.00" }));
    expect(screen.getByRole("alertdialog").textContent).toContain("The PMS writes the ACH file");
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Pay" }));
    await vi.waitFor(() => expect(fetch.mock.calls.filter(([, i]) => i?.method === "POST")).toHaveLength(2));
    const second = JSON.parse(String(fetch.mock.calls.filter(([, i]) => i?.method === "POST")[1]?.[1]?.body));
    expect(second.request_id).not.toBe(posts[0].request_id);
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
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Pay" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Vendor 1 INV-1: not enough money: the owner's account would be $50.00 short",
    );
  });

  it("says when no bill is ready to pay, and when the bills can't be read", async () => {
    server({}, [bill(3, "900.00", "waiting for owner")]);
    render(<PayBillsPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No bill is ready to pay.");
    expect(screen.getByRole("list", { name: "Not ready to pay" }).textContent).toContain("waits for Owner 1's approval");
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<PayBillsPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });

  it("shows the bills and cash of the property group picked now, even when an earlier group's answers come back last", async () => {
    const groups = { groups: [{ id: "g1", name: "North", properties: [] }, { id: "g2", name: "South", properties: [] }], properties: [] };
    vi.stubGlobal("fetch", vi.fn(async (path: string) => {
      const group = path.includes("group=g1") ? "g1" : path.includes("group=g2") ? "g2" : "";
      const bills = group === "g1" ? [bill(1, "200.00", "ready to pay")] : group === "g2" ? [bill(4, "50.00", "ready to pay", "a2")] : [];
      const read = path.startsWith("/api/payables/bills") ? { items: bills, total: bills.length, page: 1, per_page: 200 }
        : path.startsWith("/api/payables/owners") ? (group === "g1" ? [OWNERS[0]] : group === "g2" ? [OWNERS[1]] : [])
        : path.startsWith("/api/payouts/runs") ? []
        : groups;
      return new Response(JSON.stringify(read), { status: 200 });
    }));
    const releaseBills = holdBack((path) => path.startsWith("/api/payables/bills") && path.includes("group=g1"));
    const releaseOwners = holdBack((path) => path.startsWith("/api/payables/owners") && path.includes("group=g1"));
    render(<PayBillsPage me={ME} />);
    await screen.findByTestId("empty-state");
    const picker = screen.getByRole("combobox", { name: "Properties shown" });
    await within(picker).findByRole("option", { name: "South" });

    fireEvent.change(picker, { target: { value: "group:g1" } });
    fireEvent.change(picker, { target: { value: "group:g2" } });
    fireEvent.click(await screen.findByLabelText("Pay Vendor 1 INV-4"));
    const cash = () => within(screen.getByRole("table", { name: "Each property's cash" })).getAllByRole("row").slice(1);
    await vi.waitFor(() => expect(cash().map((row) => row.getAttribute("aria-label"))).toEqual(["Owner 1, Property 2"]));
    await releaseBills();
    await releaseOwners();

    expect(screen.getByRole("row", { name: "Vendor 1 INV-4" })).toBeTruthy();
    expect(screen.queryByRole("row", { name: "Vendor 1 INV-1" })).toBeNull();
    expect(cash().map((row) => row.getAttribute("aria-label"))).toEqual(["Owner 1, Property 2"]);
  });
});
