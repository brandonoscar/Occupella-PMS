import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { holdBack } from "../holdBack";
import type { Bill, BillPageData } from "../payables";
import type { Portfolio } from "../setup";
import { BillPage } from "./BillPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1", pmc_name: "Synthetic PMC",
};
const BOOKS = {
  bank_accounts: [], owners: [], properties: [], accounts: [], tenants: [], vendors: [],
  expense_accounts: [{ id: "e1", name: "Repairs", schedule_e_line: 14 }, { id: "e2", name: "Water", schedule_e_line: 17 }],
  schedule_e_lines: [{ line: 14, label: "Repairs" }, { line: 17, label: "Utilities" }],
} as unknown as Portfolio;
const BILL: Bill = {
  id: "b1", vendor_id: "v1", vendor: "Vendor 1", reference: "FIX-1", account: "a1", owner_id: "o1", owner: "Owner 1",
  property_id: "pr1", property: "Property 1", bill_date: "2026-10-01", due_on: "2026-10-15", amount: "300.00",
  memo: "Plumbing", work_order_id: "w1", expense_account_id: null, expense_account: null, split: true,
  approval_limit: "200.00", approved_by: null, declined_by: null, decline_reason: null, voided: false, owner_email: "failed",
  status: "waiting for owner", overdue: false, markup: null, markup_percent: null, shared_bill_id: null,
  paid: "0.00", left: "300.00", held: "300.00", held_since: null, short: null, needed: null,
};
const PAGE: BillPageData = {
  bill: BILL,
  shared: null,
  lines: [
    { expense_account_id: "e1", expense_account: "Repairs", amount: "200.00" },
    { expense_account_id: "e2", expense_account: "Water", amount: "100.00" },
  ],
  files: [],
  email: {
    to: "owner1@example.com", subject: "A bill waits for your approval: Vendor 1, $300.00",
    written_at: "2026-10-01T09:00:00Z", sent_at: null, status: "failed", attempts: 5,
    error: "SMTPRecipientsRefused: no such mailbox",
  },
  asks: [],
  payments: [],
  ask: null,
  history: [
    { on: "2026-10-01", what: "Entered" },
    { on: "2026-10-01", what: "Email to owner1@example.com asking Owner 1 to approve or decline it: not sent" },
  ],
};

function server(page: BillPageData = PAGE, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      const read = path === "/api/payables/bills/b1" ? page : path === "/api/setup" ? BOOKS
        : path === "/api/documents" ? { documents: [], subjects: {} } : {};
      return new Response(JSON.stringify(read), { status: path === "/api/payables/bills/zz" ? 404 : 200 });
    }
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    return new Response("{}", { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("BillPage", () => {
  it("records part of a payment by check with its number, after saying what is left", async () => {
    const fetch = server({ ...PAGE, bill: { ...BILL, status: "ready to pay" } });
    render(<BillPage me={ME} id="b1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Record payment" }));
    const panel = screen.getByRole("dialog", { name: "Record payment" });
    expect((within(panel).getByLabelText("Amount") as HTMLInputElement).value).toBe("300.00");
    fireEvent.change(within(panel).getByLabelText("Amount"), { target: { value: "100.00" } });
    fireEvent.change(within(panel).getByLabelText("Check number"), { target: { value: "1042" } });
    fireEvent.change(within(panel).getByLabelText("Paid on"), { target: { value: "2026-10-03" } });
    expect(panel.textContent).toContain("$200.00 stays left to pay");
    fireEvent.click(within(panel).getByRole("button", { name: "Pay $100.00" }));
    const confirm = screen.getByRole("alertdialog");
    expect(confirm.textContent).toContain(
      "Pay $100.00 to Vendor 1 for FIX-1 by check 1042 on 10/03/2026? $200.00 is left to pay after it.",
    );
    fireEvent.click(within(confirm).getByRole("button", { name: "Pay" }));

    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [[path, body]] = posted(fetch) as [[string, Record<string, unknown>]];
    expect(path).toBe("/api/payables/bills/b1/pay");
    expect({ ...body, request_id: typeof body.request_id }).toEqual({
      on: "2026-10-03", method: "check", amount: "100.00", check_number: "1042", request_id: "string",
    });
  });

  it("voids a bill entered by mistake from its ⋯ menu, saying nothing was paid", async () => {
    const fetch = server({ ...PAGE, bill: { ...BILL, status: "ready to pay" } });
    render(<BillPage me={ME} id="b1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Vendor 1 FIX-1" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Void" }));
    const panel = screen.getByRole("dialog", { name: "Void" });
    expect(panel.textContent).toContain("Nothing was paid; the hold goes back to Owner 1's balance.");
    fireEvent.change(within(panel).getByLabelText("Why"), { target: { value: "Entered twice" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Void" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Void" }));

    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posted(fetch)).toEqual([["/api/payables/bills/b1/void", { reason: "Entered twice" }]]);
  });

  it("asks the owner for what their balance is short of, naming who gets the email first", async () => {
    const short = { ...BILL, status: "short" as const, held: "100.00", short: "200.00", needed: "200.00" };
    const fetch = server({ ...PAGE, bill: short, ask: { owner: "Owner 1", to: "owner1@example.com", amount: "200.00", asked_today: false } });
    render(<BillPage me={ME} id="b1" />);
    expect((await screen.findByTestId("hold")).textContent).toBe(
      "$100.00 held from Owner 1's balance for this bill. Owner 1 needs to send $200.00.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Ask Owner 1 for $200.00" }));
    const confirm = screen.getByRole("alertdialog");
    expect(confirm.textContent).toContain("Email Owner 1 at owner1@example.com asking them to send $200.00? Nothing is paid or charged");
    fireEvent.click(within(confirm).getByRole("button", { name: "Send email" }));

    expect(await screen.findByText("Owner 1 is asked to send $200.00.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/payables/bills/b1/ask-owner", {}]]);
  });

  it("says when the owner has no email to ask, and when they were asked today", async () => {
    const short = { ...BILL, status: "short" as const, held: "0.00", short: "300.00", needed: "300.00" };
    server({ ...PAGE, bill: short, ask: { owner: "Owner 1", to: null, amount: "300.00", asked_today: false } });
    render(<BillPage me={ME} id="b1" />);
    expect((await screen.findByRole("link", { name: "Add one to their contact details" })).getAttribute("href")).toBe("/owners/o1");
    expect(screen.queryByRole("button", { name: /Ask Owner 1/ })).toBeNull();
    cleanup();

    server({ ...PAGE, bill: short, ask: { owner: "Owner 1", to: "owner1@example.com", amount: "300.00", asked_today: true } });
    render(<BillPage me={ME} id="b1" />);
    expect(await screen.findByText("Owner 1 was asked for it today; you can ask again tomorrow.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Ask Owner 1/ })).toBeNull();
  });

  it("lists its payments and when the bank showed them", async () => {
    server({
      ...PAGE,
      bill: { ...BILL, status: "partly paid", paid: "100.00", left: "200.00", held: "200.00", held_since: "2026-10-03" },
      payments: [
        { part: 1, on: "2026-10-03", amount: "100.00", method: "check", check_number: "1042", transfer_id: "t1", cleared_on: "2026-10-07" },
        { part: 2, on: "2026-10-04", amount: "50.00", method: "EFT", check_number: null, transfer_id: "t2", cleared_on: null },
      ],
    });
    render(<BillPage me={ME} id="b1" />);
    const rows = within(await screen.findByRole("table", { name: "Payments" })).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      "10/03/2026$100.00Check 1042Cleared 10/07/2026",
      "10/04/2026$50.00ACHNot on a statement yet",
    ]);
    // Money was taken for it: no void.
    expect(screen.queryByRole("button", { name: "Actions for Vendor 1 FIX-1" })).toBeNull();
  });

  it("shows the bill, its split lines, the email that was not sent and its history", async () => {
    server();
    render(<BillPage me={ME} id="b1" />);

    expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("Vendor 1 FIX-1");
    expect(screen.getByTestId("status").textContent).toBe("Waiting for owner");
    expect(screen.getByTestId("bill-amount").textContent).toBe("$300.00");
    expect(screen.getByTestId("hold").textContent).toBe(
      "$300.00 held from Owner 1's balance for this bill until Owner 1 decides.",
    );
    const summary = screen.getByLabelText("Summary");
    expect(summary.textContent).toContain("Due10/15/2026");
    expect(within(summary).getByRole("link", { name: "Open it" }).getAttribute("href")).toBe("/work-orders/w1");
    const lines = within(screen.getByRole("table", { name: "Lines" })).getAllByRole("row").slice(1);
    expect(lines.map((r) => r.textContent)).toEqual(["Repairs$200.00", "Water$100.00"]);
    expect(screen.getByTestId("bill-email").textContent).toBe(
      "A bill waits for your approval: Vendor 1, $300.00, to owner1@example.com, written 10/01/2026: not sent" +
        " (SMTPRecipientsRefused: no such mailbox, after 5 tries)",
    );
    expect(within(screen.getByRole("list", { name: "History" })).getAllByRole("listitem").map((l) => l.textContent)).toEqual([
      "10/01/2026 Entered",
      "10/01/2026 Email to owner1@example.com asking Owner 1 to approve or decline it: not sent",
    ]);
  });

  it("shows the markup, and the other shares of an invoice shared across properties", async () => {
    server({
      ...PAGE,
      bill: { ...BILL, reference: "ROOF-1/1", markup: "30.00", markup_percent: "10", shared_bill_id: "s1" },
      shared: {
        id: "s1", reference: "ROOF-1", amount: "900.00",
        parts: [
          { bill_id: "b1", part: 1, reference: "ROOF-1/1", owner: "Owner 1", property: "Property 1", amount: "300.00", status: "waiting for owner" },
          { bill_id: "b9", part: 2, reference: "ROOF-1/2", owner: "Owner 2", property: "Property 2", amount: "600.00", status: "paid" },
        ],
      },
    });
    render(<BillPage me={ME} id="b1" />);

    const summary = await screen.findByLabelText("Summary");
    expect(summary.textContent).toContain("Markup$30.00 (10% in the owner's terms)");
    const shares = within(screen.getByRole("table", { name: "Shares" })).getAllByRole("row").slice(1);
    expect(shares.map((r) => r.textContent)).toEqual([
      "ROOF-1/1Owner 1Property 1$300.00Waiting for owner",
      "ROOF-1/2Owner 2Property 2$600.00Paid",
    ]);
    // This share is the page itself; the other links to its own.
    expect(within(shares[0]!).queryByRole("link")).toBeNull();
    expect(within(shares[1]!).getByRole("link", { name: "ROOF-1/2" }).getAttribute("href")).toBe("/bills/b9");
    expect(screen.getByText("Shared across properties: ROOF-1, $900.00")).toBeTruthy();
  });

  it("records the owner's approval from beside its title", async () => {
    const fetch = server();
    render(<BillPage me={ME} id="b1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Record approval" }));
    const panel = screen.getByRole("dialog", { name: "Record approval" });
    fireEvent.change(within(panel).getByLabelText("Approved by"), { target: { value: "Owner 1" } });
    fireEvent.change(within(panel).getByLabelText("Approved on"), { target: { value: "2026-10-02" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Record approval" }));

    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posted(fetch)).toEqual([["/api/payables/bills/b1/approval", { approved_on: "2026-10-02", approved_by: "Owner 1" }]]);
  });

  it("changes a split bill to one expense account, and back to lines that come to the bill", async () => {
    const fetch = server({ ...PAGE, bill: { ...BILL, status: "ready to pay" } });
    render(<BillPage me={ME} id="b1" />);
    expect(await screen.findByRole("button", { name: "Record payment" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Change" }));
    let panel = screen.getByRole("dialog", { name: "Expense accounts" });
    // It opens on the bill's own lines.
    expect((within(panel).getByLabelText("Amount, line 2") as HTMLInputElement).value).toBe("100.00");
    fireEvent.click(within(panel).getByLabelText("Split across expense accounts"));
    fireEvent.change(within(panel).getByLabelText("Expense account"), { target: { value: "e2" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "Change" }));
    panel = screen.getByRole("dialog", { name: "Expense accounts" });
    fireEvent.change(within(panel).getByLabelText("Amount, line 2"), { target: { value: "90.00" } });
    expect(within(panel).getByTestId("lines-total").textContent).toBe("Lines come to $290.00 of $300.00.");
    expect((within(panel).getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(panel).getByRole("button", { name: "Add line" }));
    fireEvent.change(within(panel).getByLabelText("Expense account, line 3"), { target: { value: "e1" } });
    fireEvent.change(within(panel).getByLabelText("Amount, line 3"), { target: { value: "10.00" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Remove line 3" }));
    expect(within(panel).queryByLabelText("Amount, line 3")).toBeNull();

    expect(posted(fetch)).toEqual([["/api/payables/bills/b1/expense-account", { expense_account_id: "e2" }]]);
  });

  it("shows the bill opened now, even when an earlier bill's answer comes back last", async () => {
    const reads = server();
    const other: BillPageData = { ...PAGE, bill: { ...BILL, id: "b9", reference: "ROOF-1/2", amount: "600.00" } };
    vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) =>
      path === "/api/payables/bills/b9" ? new Response(JSON.stringify(other), { status: 200 }) : reads(path, init)));
    const release = holdBack((path) => path === "/api/payables/bills/b1");
    const { rerender } = render(<BillPage me={ME} id="b1" />);
    rerender(<BillPage me={ME} id="b9" />);
    await screen.findByRole("heading", { level: 1, name: "Vendor 1 ROOF-1/2" });
    await release();

    expect(screen.getByRole("heading", { level: 1, name: "Vendor 1 ROOF-1/2" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 1, name: "Vendor 1 FIX-1" })).toBeNull();
  });

  it("offers no step for a paid bill, and says when there is no such bill", async () => {
    server({ ...PAGE, bill: { ...BILL, status: "paid" }, email: null });
    render(<BillPage me={ME} id="b1" />);
    expect((await screen.findByTestId("status")).textContent).toBe("Paid");
    expect(screen.queryByRole("button", { name: /Record|Pay|Ask/ })).toBeNull();
    // Nothing to void once it is paid: the ⋯ menu is hidden.
    expect(screen.queryByRole("button", { name: "Actions for Vendor 1 FIX-1" })).toBeNull();
    expect(screen.queryByTestId("bill-email")).toBeNull();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async (path: string) =>
      new Response(JSON.stringify(path === "/api/setup" ? BOOKS : { detail: "no such bill" }), { status: path === "/api/setup" ? 200 : 404 })));
    render(<BillPage me={ME} id="zz" />);
    expect((await screen.findByRole("alert")).textContent).toBe("no such bill");
  });
});
