import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { holdBack } from "../holdBack";
import type { Bill, Recurring } from "../payables";
import type { Portfolio } from "../setup";
import { BillsPage } from "./BillsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState(null, "", "/");
  window.localStorage.clear();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1", pmc_name: "Synthetic PMC",
};

const BOOKS: Portfolio = {
  bank_accounts: [],
  owners: [],
  properties: [],
  accounts: [
    { account: "a1", owner_id: "o1", owner: "Owner 1", property_id: "pr1", property: "Property 1", bank_account: "Operating", agreement: null, ownership_percent: null },
  ],
  tenants: [],
  vendors: [{ id: "v1", name: "Vendor 1" }, { id: "v2", name: "Vendor 2" }],
  expense_accounts: [{ id: "e1", name: "Repairs", schedule_e_line: 14 }, { id: "e2", name: "Water", schedule_e_line: 17 }],
  schedule_e_lines: [{ line: 14, label: "Repairs" }, { line: 17, label: "Utilities" }],
};

const BILL: Bill = {
  id: "b1", vendor_id: "v1", vendor: "Vendor 1", reference: "INV-1", account: "a1", owner: "Owner 1",
  property_id: "pr1", property: "Property 1", bill_date: "2026-10-01", due_on: "2026-10-15", amount: "750.00",
  memo: "Roof repair", work_order_id: null, expense_account_id: null, expense_account: null, split: false,
  approval_limit: "500.00", approved_by: null, declined_by: null, decline_reason: null, owner_email: "sent",
  status: "needs approval", overdue: false, markup: null, markup_percent: null, shared_bill_id: null,
};
const BILLS: Bill[] = [
  BILL,
  { ...BILL, id: "b2", reference: "INV-2", amount: "200.00", status: "to set aside", overdue: true },
  { ...BILL, id: "b3", reference: "INV-3", amount: "900.00", status: "set aside", approved_by: "Owner 1" },
];
const RECURRING: Recurring[] = [
  {
    id: "r1", vendor: "Vendor 2", account: "a1", owner: "Owner 1", property: "Property 1", reference_prefix: "LAWN",
    amount: "80.00", memo: "Lawn care", day_of_month: 5, starts_on: "2026-01-01", ends_on: null,
    expense_account_id: null, expense_account: null,
  },
];

const paged = (items: Bill[]) => ({ items, total: items.length, page: 1, per_page: 50 });

function server(answers: Record<string, unknown> = {}, refuse: Record<string, string> = {}, bills: Bill[] = BILLS) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      const read = path in answers ? answers[path]
        : path.startsWith("/api/payables/bills") ? paged(bills)
          : path === "/api/payables/recurring" ? RECURRING
            : path === "/api/setup" ? BOOKS : { groups: [], properties: [] };
      return new Response(JSON.stringify(read), { status: 200 });
    }
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    return new Response(JSON.stringify(answers[path] ?? {}), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);
const reads = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method !== "POST").map(([path]) => String(path));

describe("BillsPage", () => {
  it("lists each bill with its due day, vendor, property, memo, ref, amount and status, the row opening it", async () => {
    server();
    render(<BillsPage me={ME} />);

    const row = await screen.findByRole("row", { name: "Vendor 1 INV-1" });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "10/15/2026", "Vendor 1", "Property 1", "Roof repair", "INV-1", "$750.00", "Awaiting owner approval", "",
    ]);
    expect(within(screen.getByRole("row", { name: "Vendor 1 INV-2" })).getByTestId("status").textContent).toBe("Overdue");
    expect(within(screen.getByRole("row", { name: "Vendor 1 INV-3" })).getByTestId("status").textContent).toBe("Set aside");
    fireEvent.click(within(row).getByText("Roof repair"));
    expect(window.location.pathname).toBe("/bills/b1");
    expect(screen.getByRole("link", { name: "Pay bills" }).getAttribute("href")).toBe("/pay-bills");
  });

  it("narrows to a vendor, a due day and a tab, starting from the first page", async () => {
    const fetch = server();
    render(<BillsPage me={ME} />);
    await screen.findByRole("row", { name: "Vendor 1 INV-1" });

    const vendor = screen.getByRole("combobox", { name: "Vendor" });
    await within(vendor).findByRole("option", { name: "Vendor 2" });
    fireEvent.change(vendor, { target: { value: "v2" } });
    fireEvent.change(screen.getByLabelText("Due by"), { target: { value: "2026-10-31" } });
    fireEvent.click(screen.getByRole("tab", { name: "Paid" }));

    await vi.waitFor(() =>
      expect(reads(fetch)).toContain("/api/payables/bills?show=paid&page=1&vendor=v2&due_by=2026-10-31"));
  });

  it("takes a bill's next step from its row's menu in a side panel that closes once recorded", async () => {
    const fetch = server();
    render(<BillsPage me={ME} />);

    fireEvent.click(await screen.findByRole("button", { name: "Actions for Vendor 1 INV-1" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Record approval" }));
    const panel = screen.getByRole("dialog", { name: "Record approval" });
    fireEvent.change(within(panel).getByLabelText("Approved by"), { target: { value: "Owner 1, by phone" } });
    fireEvent.change(within(panel).getByLabelText("Approved on"), { target: { value: "2026-10-02" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Record approval" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText("The owner's approval of INV-1 is recorded.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Actions for Vendor 1 INV-2" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Set aside" }));
    fireEvent.change(within(screen.getByRole("dialog", { name: "Set aside" })).getByLabelText("Set aside on"), { target: { value: "2026-10-03" } });
    fireEvent.click(within(screen.getByRole("dialog", { name: "Set aside" })).getByRole("button", { name: "Set aside" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "Actions for Vendor 1 INV-3" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Pay" }));
    const pay = screen.getByRole("dialog", { name: "Pay" });
    fireEvent.change(within(pay).getByLabelText("Sent by"), { target: { value: "EFT" } });
    fireEvent.change(within(pay).getByLabelText("Paid on"), { target: { value: "2026-10-04" } });
    fireEvent.click(within(pay).getByRole("button", { name: "Pay $900.00" }));
    const confirm = screen.getByRole("alertdialog");
    expect(confirm.textContent).toContain("Pay $900.00 to Vendor 1 for INV-3 by EFT on 10/04/2026?");
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    expect(posted(fetch)).toEqual([
      ["/api/payables/bills/b1/approval", { approved_on: "2026-10-02", approved_by: "Owner 1, by phone" }],
      ["/api/payables/bills/b2/set-aside", { on: "2026-10-03" }],
      ["/api/payables/bills/b3/pay", { on: "2026-10-04", method: "EFT" }],
    ]);
  });

  it("records an owner's decline, and keeps the panel open with why one was refused", async () => {
    server({}, { "/api/payables/bills/b1/decline": "the owner declined this bill" });
    render(<BillsPage me={ME} />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Vendor 1 INV-1" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Record decline" }));
    const panel = screen.getByRole("dialog", { name: "Record decline" });
    fireEvent.change(within(panel).getByLabelText("Declined by"), { target: { value: "Owner 1" } });
    fireEvent.change(within(panel).getByLabelText("Why"), { target: { value: "Not ours" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Record decline" }));

    expect((await within(panel).findByRole("alert")).textContent).toBe("the owner declined this bill");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  it("records a bill split across expense accounts from Record bill", async () => {
    const fetch = server();
    render(<BillsPage me={ME} />);
    await screen.findByRole("row", { name: "Vendor 1 INV-1" });
    fireEvent.click(screen.getByRole("button", { name: "Record bill" }));
    const panel = await screen.findByRole("dialog", { name: "Record bill" });

    fireEvent.change(within(panel).getByLabelText("Ref #"), { target: { value: "FIX-1" } });
    fireEvent.change(within(panel).getByLabelText("Bill date"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(panel).getByLabelText("Due"), { target: { value: "2026-10-20" } });
    fireEvent.change(within(panel).getByLabelText("Amount"), { target: { value: "300.00" } });
    fireEvent.change(within(panel).getByLabelText("Memo"), { target: { value: "Plumbing" } });
    fireEvent.click(within(panel).getByLabelText("Split across expense accounts"));
    fireEvent.change(within(panel).getByLabelText("Expense account, line 1"), { target: { value: "e1" } });
    fireEvent.change(within(panel).getByLabelText("Amount, line 1"), { target: { value: "200.00" } });
    fireEvent.change(within(panel).getByLabelText("Expense account, line 2"), { target: { value: "e2" } });
    fireEvent.change(within(panel).getByLabelText("Amount, line 2"), { target: { value: "100.00" } });
    expect(within(panel).getByTestId("lines-total").textContent).toBe("Lines come to $300.00 of $300.00.");
    fireEvent.click(within(panel).getByRole("button", { name: "Record bill" }));

    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posted(fetch)).toEqual([[
      "/api/payables/bills",
      {
        vendor_id: "v1", account: "a1", reference: "FIX-1", bill_date: "2026-10-01", due_on: "2026-10-20",
        amount: "300.00", memo: "Plumbing",
        lines: [{ expense_account_id: "e1", amount: "200.00" }, { expense_account_id: "e2", amount: "100.00" }],
      },
    ]]);
  });

  it("enters the recurring bills due now, as the nightly run would, and says what it did", async () => {
    const fetch = server({ "/api/payables/recurring/enter": { entered: 1, emails: 0 } });
    render(<BillsPage me={ME} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Recurring" }));

    fireEvent.click(await screen.findByRole("button", { name: "Enter bills due now" }));

    expect(await screen.findByText("Entered 1 bill.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/payables/recurring/enter", {}]]);
    cleanup();

    // With no recurring bill there is nothing to enter, and no button.
    server({ "/api/payables/recurring": [] });
    render(<BillsPage me={ME} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Recurring" }));
    await screen.findByTestId("empty-state");
    expect(screen.queryByRole("button", { name: "Enter bills due now" })).toBeNull();
  });

  it("lists recurring bills, adds one and stops one, each in a side panel", async () => {
    const fetch = server();
    render(<BillsPage me={ME} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Recurring" }));

    const lawn = await screen.findByRole("row", { name: "Vendor 2 LAWN" });
    expect(within(lawn).getAllByRole("cell").map((c) => c.textContent).slice(0, 7)).toEqual([
      "Vendor 2", "Owner 1, Property 1", "LAWN", "$80.00", "5", "01/01/2026", "",
    ]);
    fireEvent.click(within(lawn).getByRole("button", { name: "Actions for Vendor 2 LAWN" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Stop it" }));
    const stop = screen.getByRole("dialog", { name: "Stop recurring bill" });
    fireEvent.change(within(stop).getByLabelText("Last day"), { target: { value: "2026-12-31" } });
    fireEvent.click(within(stop).getByRole("button", { name: "Stop it" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(within(screen.getByRole("row", { name: "Vendor 2 LAWN" })).getByRole("button", { name: "Actions for Vendor 2 LAWN" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Change expense account" }));
    const moving = screen.getByRole("dialog", { name: "Expense account" });
    fireEvent.change(within(moving).getByLabelText("Expense account"), { target: { value: "e2" } });
    fireEvent.click(within(moving).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText("LAWN is entered in Water from now on.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Add recurring bill" }));
    const adding = screen.getByRole("dialog", { name: "Add recurring bill" });
    fireEvent.change(within(adding).getByLabelText("Ref # prefix"), { target: { value: "HOA" } });
    fireEvent.change(within(adding).getByLabelText("Monthly amount"), { target: { value: "120.00" } });
    fireEvent.change(within(adding).getByLabelText("Memo"), { target: { value: "Dues" } });
    fireEvent.change(within(adding).getByLabelText("First day"), { target: { value: "2026-11-01" } });
    fireEvent.change(within(adding).getByLabelText("Expense account"), { target: { value: "e1" } });
    fireEvent.click(within(adding).getByRole("button", { name: "Add recurring bill" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    expect(posted(fetch)).toEqual([
      ["/api/payables/recurring/r1/end", { ends_on: "2026-12-31" }],
      ["/api/payables/recurring/r1/expense-account", { expense_account_id: "e2" }],
      ["/api/payables/recurring", {
        vendor_id: "v1", account: "a1", reference_prefix: "HOA", amount: "120.00", memo: "Dues", day_of_month: 1,
        starts_on: "2026-11-01", expense_account_id: "e1",
      }],
    ]);
  });

  it("says when a tab has nothing, and when the bills can't be read", async () => {
    server({}, {}, []);
    render(<BillsPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("Nothing left to pay.");
    fireEvent.click(screen.getByRole("tab", { name: "Declined" }));
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No bill was declined.");
    cleanup();

    server({ "/api/payables/recurring": [] });
    render(<BillsPage me={ME} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Recurring" }));
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No recurring bills yet.");
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<BillsPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });

  it("shows the bills of the vendor picked now, even when an earlier vendor's answer comes back last", async () => {
    server({
      "/api/payables/bills?show=open&page=1&vendor=v1": paged([BILL]),
      "/api/payables/bills?show=open&page=1&vendor=v2": paged([{ ...BILL, id: "b9", vendor_id: "v2", vendor: "Vendor 2", reference: "INV-9" }]),
    });
    const release = holdBack((path) => path.includes("vendor=v1"));
    render(<BillsPage me={ME} />);
    const vendor = screen.getByRole("combobox", { name: "Vendor" });
    await within(vendor).findByRole("option", { name: "Vendor 2" });

    fireEvent.change(vendor, { target: { value: "v1" } });
    fireEvent.change(vendor, { target: { value: "v2" } });
    await screen.findByRole("row", { name: "Vendor 2 INV-9" });
    await release();

    expect(screen.getByRole("row", { name: "Vendor 2 INV-9" })).toBeTruthy();
    expect(screen.queryByRole("row", { name: "Vendor 1 INV-1" })).toBeNull();
  });
});
