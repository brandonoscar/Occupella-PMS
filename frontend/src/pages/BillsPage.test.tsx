import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Bill, OutboxEmail, Recurring } from "../payables";
import type { Portfolio } from "../setup";
import { BillsPage } from "./BillsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff",
  email: "staff@example.com",
  name: "Staff 1",
  person_id: "s1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const BOOKS: Portfolio = {
  bank_accounts: [],
  owners: [],
  properties: [],
  accounts: [
    { account: "a1", owner_id: "o1", owner: "Owner 1", property_id: "pr1", property: "Property 1", bank_account: "Operating", agreement: null },
    { account: "a2", owner_id: "o2", owner: "Owner 2", property_id: "pr1", property: "Property 1", bank_account: "Operating", agreement: null },
  ],
  tenants: [],
  vendors: [
    { id: "v1", name: "Vendor 1" },
    { id: "v2", name: "Vendor 2" },
  ],
};

const BILL: Bill = {
  id: "b1",
  vendor: "Vendor 1",
  reference: "INV-1",
  account: "a1",
  owner: "Owner 1",
  property: "Property 1",
  bill_date: "2026-10-01",
  due_on: "2026-10-15",
  amount: "750.00",
  memo: "Roof repair",
  work_order_id: null,
  approval_limit: "500.00",
  approved_by: null,
  declined_by: null,
  decline_reason: null,
  owner_email: "sent",
  status: "needs approval",
};

const BILLS: Bill[] = [
  BILL,
  { ...BILL, id: "b2", reference: "INV-2", amount: "200.00", status: "to set aside" },
  { ...BILL, id: "b3", reference: "INV-3", amount: "900.00", status: "set aside", approved_by: "Owner 1" },
];

const RECURRING: Recurring[] = [
  {
    id: "r1", vendor: "Vendor 2", account: "a1", owner: "Owner 1", property: "Property 1",
    reference_prefix: "LAWN", amount: "80.00", memo: "Lawn care", day_of_month: 5,
    starts_on: "2026-01-01", ends_on: null,
  },
];

// The API: bills, recurring bills and the books on GET; each POST answers with
// `answers[path]` (or 201 {}), or 409 with `refuse[path]`.
const OUTBOX: OutboxEmail[] = [
  {
    id: "e1",
    to: "owner1@example.com",
    subject: "A bill waits for your approval: Vendor 1, $750.00",
    written_at: "2026-10-02T09:00:00Z",
    status: "sent",
    error: null,
  },
  {
    id: "e2",
    to: "owner2@example.com",
    subject: "A bill waits for your approval: Vendor 1, $900.00",
    written_at: "2026-10-01T09:00:00Z",
    status: "failed",
    error: "SMTPRecipientsRefused: no such mailbox",
  },
];

function server(
  answers: Record<string, unknown> = {},
  refuse: Record<string, string> = {},
  bills: Bill[] = BILLS,
) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      const body = path.startsWith("/api/payables/bills")
        ? path.endsWith("show=all") ? [...bills, { ...BILL, id: "b4", reference: "INV-4", status: "paid" }] : bills
        : path === "/api/payables/recurring" ? RECURRING
        : path === "/api/payables/emails" ? OUTBOX : BOOKS;
      return new Response(JSON.stringify(body), { status: 200 });
    }
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    return new Response(JSON.stringify(answers[path] ?? {}), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

const bill = (name: string) => screen.findByRole("listitem", { name });

describe("BillsPage", () => {
  it("lists the bills not paid yet, each with its next step", async () => {
    server();
    render(<BillsPage me={ME} />);

    const first = await bill("Vendor 1 INV-1");
    expect(first.textContent).toContain("Owner 1, Property 1 · Roof repair · dated 2026-10-01, due 2026-10-15");
    expect(within(first).getByTestId("status").textContent).toBe("needs approval");
    expect(first.textContent).toContain("Over the owner's approval limit of $500.00.");
    expect(within(first).getByRole("button", { name: "Record approval" })).toBeTruthy();
    expect(within(await bill("Vendor 1 INV-2")).getByRole("button", { name: "Set aside" })).toBeTruthy();
    const third = await bill("Vendor 1 INV-3");
    expect(within(third).getByRole("button", { name: "Pay" })).toBeTruthy();
    expect(third.textContent).toContain("Approved by Owner 1.");
  });

  it("shows paid bills too when asked, with no step left", async () => {
    server();
    render(<BillsPage me={ME} />);
    await bill("Vendor 1 INV-1");

    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "all" } });

    const paid = await bill("Vendor 1 INV-4");
    expect(within(paid).getByTestId("status").textContent).toBe("paid");
    expect(within(paid).queryByRole("button")).toBeNull();
  });

  it("enters a bill against the chosen vendor and owner", async () => {
    const fetch = server({ "/api/payables/bills": { id: "b9" } });
    render(<BillsPage me={ME} />);
    await bill("Vendor 1 INV-1");

    fireEvent.change(screen.getByLabelText("Vendor"), { target: { value: "v2" } });
    fireEvent.change(screen.getByLabelText("Owner and property"), { target: { value: "a2" } });
    fireEvent.change(screen.getByLabelText("Vendor's reference"), { target: { value: "INV-9" } });
    fireEvent.change(screen.getByLabelText("Bill date"), { target: { value: "2026-10-02" } });
    fireEvent.change(screen.getByLabelText("Due on"), { target: { value: "2026-10-30" } });
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "120.00" } });
    fireEvent.change(screen.getByLabelText("What for"), { target: { value: "Plumbing" } });
    fireEvent.click(screen.getByRole("button", { name: "Enter bill" }));

    expect(await screen.findByText("Bill INV-9 entered.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      [
        "/api/payables/bills",
        {
          vendor_id: "v2", account: "a2", reference: "INV-9", bill_date: "2026-10-02",
          due_on: "2026-10-30", amount: "120.00", memo: "Plumbing",
        },
      ],
    ]);
  });

  it("records the owner's approval, sets a bill aside and pays one", async () => {
    const fetch = server();
    render(<BillsPage me={ME} />);

    const first = await bill("Vendor 1 INV-1");
    fireEvent.change(within(first).getByLabelText("Approved by"), { target: { value: "Owner 1" } });
    fireEvent.change(within(first).getByLabelText("Approved on"), { target: { value: "2026-10-03" } });
    fireEvent.click(within(first).getByRole("button", { name: "Record approval" }));
    expect(await within(first).findByText("The owner's approval of INV-1 is recorded.")).toBeTruthy();

    const second = await bill("Vendor 1 INV-2");
    fireEvent.change(within(second).getByLabelText("Set aside on"), { target: { value: "2026-10-04" } });
    fireEvent.click(within(second).getByRole("button", { name: "Set aside" }));
    expect(await within(second).findByText("$200.00 is set aside for INV-2.")).toBeTruthy();

    const third = await bill("Vendor 1 INV-3");
    fireEvent.change(within(third).getByLabelText("Paid on"), { target: { value: "2026-10-05" } });
    fireEvent.click(within(third).getByRole("button", { name: "Pay" }));
    expect(await within(third).findByText("INV-3 is paid.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/payables/bills/b1/approval", { approved_on: "2026-10-03", approved_by: "Owner 1" }],
      ["/api/payables/bills/b2/set-aside", { on: "2026-10-04" }],
      ["/api/payables/bills/b3/pay", { on: "2026-10-05" }],
    ]);
  });

  it("records the owner's decline behind its own button", async () => {
    const fetch = server();
    render(<BillsPage me={ME} />);

    const first = await bill("Vendor 1 INV-1");
    expect(within(first).queryByLabelText("Why")).toBeNull();
    fireEvent.click(within(first).getByRole("button", { name: "Record a decline instead" }));
    fireEvent.change(within(first).getByLabelText("Declined by"), { target: { value: "Owner 1, by phone" } });
    fireEvent.change(within(first).getByLabelText("Declined on"), { target: { value: "2026-10-03" } });
    fireEvent.change(within(first).getByLabelText("Why"), { target: { value: "Get a second quote" } });
    fireEvent.click(within(first).getByRole("button", { name: "Record decline" }));
    expect(await within(first).findByText("The owner's decline of INV-1 is recorded.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      [
        "/api/payables/bills/b1/decline",
        { declined_on: "2026-10-03", declined_by: "Owner 1, by phone", reason: "Get a second quote" },
      ],
    ]);
  });

  it("shows a declined bill with who declined it and why, and no step", async () => {
    server({}, {}, [
      {
        ...BILL,
        status: "declined",
        declined_by: "Owner 1, in the owner portal",
        decline_reason: "Get a second quote",
      },
    ]);
    render(<BillsPage me={ME} />);

    const first = await bill("Vendor 1 INV-1");
    expect(within(first).getByTestId("status").textContent).toBe("declined");
    expect(within(first).getByText(/Declined by Owner 1, in the owner portal: Get a second quote/)).toBeTruthy();
    expect(within(first).queryAllByRole("button")).toEqual([]);
  });

  it("says whether the owner was emailed about a bill waiting for them", async () => {
    server({}, {}, [
      BILL,
      { ...BILL, id: "b5", reference: "INV-5", owner_email: "waiting" },
      { ...BILL, id: "b6", reference: "INV-6", owner_email: null },
    ]);
    render(<BillsPage me={ME} />);

    expect(within(await bill("Vendor 1 INV-1")).getByText(/Emailed the owner/)).toBeTruthy();
    expect(within(await bill("Vendor 1 INV-5")).getByText("An email to the owner waits to be sent.")).toBeTruthy();
    expect(within(await bill("Vendor 1 INV-6")).getByText(/no active portal login/)).toBeTruthy();
  });

  it("lists the emails to owners and whether each went", async () => {
    server();
    render(<BillsPage me={ME} />);

    const outbox = await screen.findByRole("list", { name: "Emails to owners" });
    expect(outbox.textContent).toContain(
      "2026-10-02 to owner1@example.com: A bill waits for your approval: Vendor 1, $750.00. Sent.",
    );
    expect(outbox.textContent).toContain("Not sent (SMTPRecipientsRefused: no such mailbox).");
  });

  it("shows why a step was refused on its bill", async () => {
    server({}, { "/api/payables/bills/b2/set-aside": "the owner's account would go below zero" });
    render(<BillsPage me={ME} />);

    const second = await bill("Vendor 1 INV-2");
    fireEvent.click(within(second).getByRole("button", { name: "Set aside" }));

    expect(await within(second).findByText("the owner's account would go below zero")).toBeTruthy();
  });

  it("adds, enters and stops recurring bills", async () => {
    const fetch = server({ "/api/payables/recurring/enter": { entered: 2 } });
    render(<BillsPage me={ME} />);
    expect(
      await screen.findByText(
        "Vendor 2: $80.00 on day 5 of each month from 2026-01-01, Owner 1, Property 1 (LAWN)",
      ),
    ).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Enter bills due through"), { target: { value: "2026-10-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Enter recurring bills" }));
    expect(await screen.findByText("2 bills entered.")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Last day, LAWN"), { target: { value: "2026-12-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Stop it" }));
    expect(await screen.findByText("LAWN stops after 2026-12-31.")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Recurring vendor"), { target: { value: "v1" } });
    fireEvent.change(screen.getByLabelText("For owner and property"), { target: { value: "a2" } });
    fireEvent.change(screen.getByLabelText("Reference prefix"), { target: { value: "POOL" } });
    fireEvent.change(screen.getByLabelText("Monthly amount"), { target: { value: "60.00" } });
    fireEvent.change(screen.getByLabelText("For"), { target: { value: "Pool service" } });
    fireEvent.change(screen.getByLabelText("Day of the month"), { target: { value: "15" } });
    fireEvent.change(screen.getByLabelText("First day"), { target: { value: "2026-11-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Add recurring bill" }));
    expect(await screen.findByText("POOL is entered each month.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/payables/recurring/enter", { on: "2026-10-31" }],
      ["/api/payables/recurring/r1/end", { ends_on: "2026-12-31" }],
      [
        "/api/payables/recurring",
        {
          vendor_id: "v1", account: "a2", reference_prefix: "POOL", amount: "60.00",
          memo: "Pool service", day_of_month: 15, starts_on: "2026-11-01",
        },
      ],
    ]);
  });

  it("says when every bill is paid, and when there is nothing to bill yet", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) =>
        new Response(
          JSON.stringify(path === "/api/setup" ? { ...BOOKS, vendors: [] } : []),
          { status: 200 },
        ),
      ),
    );
    render(<BillsPage me={ME} />);

    expect(await screen.findByText("Nothing left to pay: every bill is paid or declined.")).toBeTruthy();
    expect(await screen.findByText("Add a vendor and a property with an owner in Setup first.")).toBeTruthy();
    expect(screen.getByText("No recurring bills yet.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "all" } });
    expect(await screen.findByText("No bills yet.")).toBeTruthy();
  });

  it("says when the bills can't be read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })),
    );
    render(<BillsPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });
});
