import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { LeaseDetail, Ledger } from "../rentals";
import { LeasePage } from "./LeasePage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState(null, "", "/");
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const LEASE: LeaseDetail = {
  id: "l1", property: "Property 1", unit: "Unit 1", property_id: "p1", unit_id: "u1", status: "current",
  starts_on: "2026-01-01", ends_on: null, monthly_rent: "1500.00", rent_changes: [], rent_due_day: 1, due_days: [], renters_insurance: null,
  tenants: [
    { id: "t1", name: "Tenant 1", prepaid: "50.00", deposit: "1500.00",
      deposits: [{ kind: "security", amount: "1200.00" }, { kind: "pet", amount: "300.00" }] },
    { id: "t2", name: "Tenant 2", prepaid: "0.00", deposit: "0.00", deposits: [] },
  ],
  people: [
    { id: "t1", name: "Tenant 1", email: "t1@example.com", phone: "555-555-0101", portal: "active", login_email: "t1@example.com", left_on: null, deposit_to: null },
    { id: "t2", name: "Tenant 2", email: null, phone: null, portal: "no login", login_email: null, left_on: null, deposit_to: null },
  ],
  owners: [{ account: "a1", owner: "Owner 1", percent: null }],
  owed: "1550.00",
  carried: "0.00",
  moved_from: null,
  moved_to: null,
  on_hold: false,
  open_charges: [
    { id: "c1", due_on: "2026-10-01", kind: "rent", memo: "Rent due 2026-10-01", amount: "1500.00", open: "1500.00" },
    { id: "c2", due_on: "2026-10-06", kind: "fee", memo: "Late fee", amount: "50.00", open: "50.00" },
  ],
  payments: [
    { transfer_id: "tr1", received_on: "2026-09-01", amount: "1500.00", reference: "Check 101", method: null, bounced: false },
    { transfer_id: "tr0", received_on: "2026-08-01", amount: "1500.00", reference: null, method: "EFT", bounced: true },
  ],
  recurring: [
    { id: "r1", kind: "fee", memo: "Pet rent", amount: "35.00", starts_on: "2026-01-01", ends_on: null, charge_type: null },
    { id: "r2", kind: "credit", memo: "Concession", amount: "100.00", starts_on: "2026-02-01", ends_on: "2026-12-01", charge_type: null },
  ],
  holds: [],
};
const HELD: LeaseDetail = {
  ...LEASE,
  on_hold: true,
  holds: [{ id: "h1", starts_on: "2026-10-01", reason: "Eviction filed", ends_on: null, released_by: null, allowed: 0, in_force: true }],
};

const LEDGER: Ledger = {
  lines: [
    { on: "2026-08-01", kind: "rent", memo: "Rent due 2026-08-01", charged: "1500.00", paid: null, balance: "1500.00", bounced: false, charge_id: "c1", transfer_id: null },
    { on: "2026-08-01", kind: "payment", memo: null, charged: null, paid: "1500.00", balance: "0.00", bounced: true, charge_id: null, transfer_id: "tr0" },
    { on: "2026-08-05", kind: "NSF fee", memo: "NSF fee", charged: "35.00", paid: null, balance: "35.00", bounced: false, charge_id: "c9", transfer_id: null },
    { on: "2026-08-05", kind: "bounced payment", memo: null, charged: null, paid: "-1500.00", balance: "1535.00", bounced: false, charge_id: null, transfer_id: "tr0" },
  ],
  start: null,
  end: "2026-10-25",
  opening: "0.00",
  balance: "1535.00",
};
// From 08/03/2026: the rent and payment of 08/01 brought forward.
const FROM_AUGUST_3: Ledger = { ...LEDGER, start: "2026-08-03", lines: LEDGER.lines.slice(2), opening: "0.00" };

// The API: the lease and its ledger on GET; each POST answers `answers[path]` (or 201 {}), or 409.
function server(lease: LeaseDetail = LEASE, answers: Record<string, unknown> = {}, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      const reads: Record<string, unknown> = {
        "/api/leases/l1": lease,
        "/api/leases/l1/ledger": LEDGER,
        "/api/leases/l1/ledger?start=2026-08-03": FROM_AUGUST_3,
        "/api/documents": { documents: [], subjects: { w9: [], receipt: [], move_out_photo: [], owner_document: [] } },
        "/api/assistance/leases/l1": {
          in_force: null, monthly_rent: lease.monthly_rent, tenant_part: lease.monthly_rent, terms: [], charges: [], owed: "0.00",
        },
        "/api/assistance/authorities": [],
      };
      if (path.startsWith("/api/notes")) return new Response("[]", { status: 200 });
      if (path === "/api/leases/l1/household") {
        return new Response(JSON.stringify({ occupants: [], pets: [], vehicles: [], guarantors: [] }), { status: 200 });
      }
      if (path.startsWith("/api/move-out/l1/break-fee")) {
        return new Response(JSON.stringify({ early: false, fee: null, flat_fee: null, rent_months: null }), { status: 200 });
      }
      if (!(path in reads)) return new Response(JSON.stringify({ detail: "no such page" }), { status: 404 });
      return new Response(JSON.stringify(reads[path]), { status: 200 });
    }
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    return new Response(JSON.stringify(answers[path] ?? {}), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

async function open(lease: LeaseDetail = LEASE) {
  render(<LeasePage me={ME} id="l1" />);
  await screen.findByRole("heading", { level: 1, name: `${lease.property} · ${lease.unit} · Tenant 1, Tenant 2` });
}

// The company's books as /api/setup gives them: two security-deposit trust bank accounts.
const TWO_DEPOSIT_ACCOUNTS = {
  bank_accounts: [
    { id: "b1", name: "Operating 1", kind: "operating", opened_at: null, closed_through: null },
    { id: "b2", name: "Deposits 1", kind: "security_deposit", opened_at: null, closed_through: null },
    { id: "b3", name: "Deposits 2", kind: "security_deposit", opened_at: null, closed_through: null },
  ],
  owners: [], properties: [], accounts: [], tenants: [], vendors: [], expense_accounts: [], schedule_e_lines: [],
};

// One of the lease's side panels, from its button or its ⋯ menu, once the panel before it is gone
// and the page behind it, which React Aria hides from screen readers while a panel is open, is
// shown again. React Aria lifts that a moment after the panel goes, so a click at once can miss
// the button on a busy machine.
async function panel(name: string, fromMenu = false) {
  await vi.waitFor(() => {
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.querySelector("body > [aria-hidden='true']")).toBeNull();
  });
  if (fromMenu) {
    fireEvent.click(screen.getByRole("button", { name: "Actions for this lease" }));
    fireEvent.click(screen.getByRole("menuitem", { name }));
  } else {
    fireEvent.click(screen.getByRole("button", { name }));
  }
  return screen.getByRole("dialog", { name });
}

describe("LeasePage", () => {
  it("drops a late answer for a lease it has left, as when a move opens the new lease", async () => {
    const MOVED: LeaseDetail = { ...LEASE, id: "l2", property: "Property 2", unit: "Unit 7" };
    const reads = server();
    let late: (answer: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/api/leases/l1") return new Promise<Response>((resolve) => { late = resolve; });
      if (path === "/api/leases/l2") return new Response(JSON.stringify(MOVED), { status: 200 });
      if (path === "/api/leases/l2/ledger") return new Response(JSON.stringify(LEDGER), { status: 200 });
      return reads(path, init);
    }));
    const { rerender } = render(<LeasePage me={ME} id="l1" />);
    rerender(<LeasePage me={ME} id="l2" />);
    const heading = "Property 2 · Unit 7 · Tenant 1, Tenant 2";
    await screen.findByRole("heading", { level: 1, name: heading });
    await act(async () => {
      late(new Response(JSON.stringify(LEASE), { status: 200 }));
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(heading);
  });

  it("heads the lease with its property, unit, tenants, where it stands and the balance due", async () => {
    server();
    await open();
    expect(screen.getByText("Current")).toBeTruthy();
    const due = screen.getByTestId("balance-due");
    expect(due.textContent).toBe("$1,550.00");
    expect(due.className).toContain("text-danger");
    expect(screen.getByRole("link", { name: "Property 1" }).getAttribute("href")).toBe("/properties/p1");
    expect(screen.getByRole("link", { name: "Unit 1" }).getAttribute("href")).toBe("/units/u1");
    expect(screen.getAllByText("01/01/2026 – Month to month").length).toBeGreaterThan(0);
    // Summary: the rent, what is held, what is open, the monthly charges, renters insurance.
    expect(screen.getByText("$1,500.00 a month")).toBeTruthy();
    expect(screen.getByTestId("deposit-held").textContent).toBe("$1,500.00");
    expect(screen.getByTestId("deposit-kinds").textContent).toBe("Security deposit $1,200.00 · Pet deposit $300.00");
    expect(screen.getByTestId("prepaid").textContent).toBe("$50.00");
    expect(document.body.textContent).toContain("10/06/2026 Late fee: $50.00 open");
    expect(document.body.textContent).toContain("Pet rent: $35.00 a month from 01/01/2026");
    expect(screen.getByText("Not asked for.")).toBeTruthy();
    expect(screen.queryByText(/payment hold/)).toBeNull();
  });

  it("adds what the lease its tenants moved from still owes to the balance due, and links it", async () => {
    server({
      ...LEASE, owed: "100.00", carried: "1000.00",
      moved_from: { lease_id: "l0", property: "Property 0", unit: "Unit 0", moved_on: "2026-04-01" },
    });
    await open();
    expect(screen.getByTestId("balance-due").textContent).toBe("$1,100.00");
    expect(screen.getByTestId("carried").textContent).toBe("Includes $1,000.00 still owed at Property 0 · Unit 0");
    expect(screen.getByTestId("moved-from").textContent).toBe(
      "Moved here on 04/01/2026 from Property 0 · Unit 0. What that lease still owes is its owner's, and a payment here pays it first.",
    );
    expect(screen.getByRole("link", { name: "Property 0 · Unit 0" }).getAttribute("href")).toBe("/leases/l0");
  });

  it("says where a lease's tenants moved, and offers no second move", async () => {
    server({ ...LEASE, moved_to: { lease_id: "l9", property: "Property 9", unit: "Unit 9", moved_on: "2026-11-01" } });
    await open();
    expect(document.body.textContent).toContain(
      "The tenants moved to Property 9 · Unit 9 on 11/01/2026; their deposits went with them.",
    );
    expect(screen.getByRole("link", { name: "Open the new lease" }).getAttribute("href")).toBe("/leases/l9");
    expect(screen.queryByTestId("carried")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Actions for this lease" }));
    expect(screen.queryByRole("menuitem", { name: "Move to another unit" })).toBeNull();
  });

  it("opens Move to another unit from the ⋯ menu", async () => {
    server();
    await open();
    const moving = await panel("Move to another unit", true);
    expect(await within(moving).findByText(/no such page|Loading/)).toBeTruthy();
  });

  it("says nothing is due in plain ink, and a lease to come", async () => {
    server({ ...LEASE, owed: "0.00", status: "future" });
    await open();
    expect(screen.getByTestId("balance-due").className).not.toContain("text-danger");
    expect(screen.getByText("Future")).toBeTruthy();
  });

  it("receives a payment in a side panel, once per request, and closes it", async () => {
    const fetch = server(LEASE, {
      "/api/rent/leases/l1/payments": { transfers: ["a", "b"], to_owner: "1550.00", to_prepaid: "50.00" },
    });
    await open();
    let form = await panel("Receive payment");
    fireEvent.change(within(form).getByLabelText("Payment received"), { target: { value: "1600.00" } });
    fireEvent.change(within(form).getByLabelText("Received on"), { target: { value: "2026-10-02" } });
    fireEvent.change(within(form).getByLabelText("Check or reference"), { target: { value: "Check 102" } });
    fireEvent.click(within(form).getByRole("button", { name: "Record payment" }));
    expect(await screen.findByText("$1,550.00 paid what is owed; $50.00 held as prepaid rent.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    const [path, body] = posted(fetch)[0] as [string, Record<string, unknown>];
    expect(path).toBe("/api/rent/leases/l1/payments");
    expect(body).toMatchObject({ amount: "1600.00", received_on: "2026-10-02", reference: "Check 102" });
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/);

    // The next payment is a new request.
    form = await panel("Receive payment");
    fireEvent.change(within(form).getByLabelText("Payment received"), { target: { value: "10.00" } });
    fireEvent.click(within(form).getByRole("button", { name: "Record payment" }));
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(2));
    const second = posted(fetch)[1]?.[1] as Record<string, unknown>;
    expect(second.request_id).not.toBe(body.request_id);
    expect(second.reference).toBe(null);
  });

  it("splits a payment by the property's ownership shares, or asks which owner without them", async () => {
    const owners = [{ account: "a1", owner: "Owner 1", percent: "60" }, { account: "a2", owner: "Owner 2", percent: "40" }];
    let fetch = server({ ...LEASE, owners });
    await open();
    let form = await panel("Receive payment");
    expect(within(form).getByTestId("split-by-ownership").textContent).toBe("Split by ownership: Owner 1 60% · Owner 2 40%");
    expect(within(form).queryByLabelText("Owner's account")).toBeNull();
    fireEvent.change(within(form).getByLabelText("Payment received"), { target: { value: "100.00" } });
    fireEvent.click(within(form).getByRole("button", { name: "Record payment" }));
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(1));
    expect(posted(fetch)[0]?.[1]).not.toHaveProperty("account");
    cleanup();

    // Two owners without shares: staff choose the account, for the payment and the prepaid rent.
    fetch = server({ ...LEASE, owners: owners.map((o) => ({ ...o, percent: null })) });
    await open();
    form = await panel("Receive payment");
    expect(within(form).queryByTestId("split-by-ownership")).toBeNull();
    expect([...(within(form).getByLabelText("Owner's account") as HTMLSelectElement).options].map((o) => o.textContent))
      .toEqual(["Owner 1", "Owner 2"]);
    fireEvent.change(within(form).getByLabelText("Owner's account"), { target: { value: "a2" } });
    fireEvent.change(within(form).getByLabelText("Payment received"), { target: { value: "100.00" } });
    fireEvent.click(within(form).getByRole("button", { name: "Record payment" }));
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(1));
    expect(posted(fetch)[0]?.[1]).toMatchObject({ amount: "100.00", account: "a2" });
    form = await panel("Receive payment");
    fireEvent.change(within(form).getByLabelText("Owner's account"), { target: { value: "a2" } });
    fireEvent.click(within(form).getByRole("button", { name: "Use prepaid rent" }));
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(2));
    expect(posted(fetch)[1]).toEqual(["/api/rent/leases/l1/use-prepaid", expect.objectContaining({ account: "a2" })]);
  });

  it("uses prepaid rent, enters a credit and records a deposit", async () => {
    const fetch = server(LEASE, {
      "/api/rent/leases/l1/use-prepaid": { transfers: ["p"], to_owner: "50.00", to_prepaid: "0.00" },
    });
    await open();
    fireEvent.click(within(await panel("Receive payment")).getByRole("button", { name: "Use prepaid rent" }));
    expect(await screen.findByText("$50.00 paid what is owed.")).toBeTruthy();
    const charge = await panel("Enter charge");
    fireEvent.change(within(charge).getByLabelText("Add a"), { target: { value: "credit" } });
    fireEvent.change(within(charge).getByLabelText("Amount"), { target: { value: "25.00" } });
    fireEvent.change(within(charge).getByLabelText("What for"), { target: { value: "Leak repair" } });
    fireEvent.click(within(charge).getByRole("button", { name: "Add" }));
    expect(await screen.findByText("Credit of $25.00 added.")).toBeTruthy();
    const deposit = await panel("Record deposit", true);
    fireEvent.change(within(deposit).getByLabelText("Deposit from"), { target: { value: "t2" } });
    fireEvent.change(within(deposit).getByLabelText("Deposit received"), { target: { value: "500.00" } });
    fireEvent.click(within(deposit).getByRole("button", { name: "Record deposit" }));
    expect(await screen.findByText("Security deposit of $500.00 held.")).toBeTruthy();
    // A pet deposit, held apart from the security deposit.
    const pet = await panel("Record deposit", true);
    expect([...(within(pet).getByLabelText("Kind of deposit") as HTMLSelectElement).options].map((o) => o.textContent))
      .toEqual(["Security deposit", "Pet deposit", "Key deposit"]);
    fireEvent.change(within(pet).getByLabelText("Kind of deposit"), { target: { value: "pet" } });
    fireEvent.change(within(pet).getByLabelText("Deposit received"), { target: { value: "300.00" } });
    fireEvent.click(within(pet).getByRole("button", { name: "Record deposit" }));
    expect(await screen.findByText("Pet deposit of $300.00 held.")).toBeTruthy();

    const calls = posted(fetch);
    expect(calls.map(([path]) => path)).toEqual([
      "/api/rent/leases/l1/use-prepaid", "/api/rent/leases/l1/charges", "/api/rent/deposits", "/api/rent/deposits",
    ]);
    // One owner: no account to choose.
    expect(calls[0]?.[1]).not.toHaveProperty("account");
    expect(calls[1]?.[1]).toMatchObject({ kind: "credit", amount: "25.00", memo: "Leak repair" });
    expect(calls[2]?.[1]).toMatchObject({ tenant_id: "t2", amount: "500.00", kind: "security" });
    // No books to read here: no deposit account to choose, so the API takes the only one.
    expect(calls[2]?.[1]).not.toHaveProperty("bank_account_id");
    expect(calls[3]?.[1]).toMatchObject({ tenant_id: "t1", amount: "300.00", kind: "pet" });
  });

  it("says what deposit the signed lease asked and records the rest from its card", async () => {
    const ASKED: LeaseDetail = { ...LEASE, deposit_asked: { asked: "2000.00", held: "1200.00", to_receive: "800.00" } };
    const fetch = server(ASKED);
    await open(ASKED);

    const asked = screen.getByTestId("deposit-asked");
    expect(asked.textContent).toBe("The signed lease asked $2,000.00: $1,200.00 held, $800.00 still to receiveTo receive");
    fireEvent.click(within(asked.closest("section") as HTMLElement).getByRole("button", { name: "Record deposit" }));
    const deposit = screen.getByRole("dialog", { name: "Record deposit" });
    // It starts from what the signed lease asked and isn't held yet.
    expect((within(deposit).getByLabelText("Deposit received") as HTMLInputElement).value).toBe("800.00");
    fireEvent.click(within(deposit).getByRole("button", { name: "Record deposit" }));
    expect(await screen.findByText("Security deposit of $800.00 held.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/rent/deposits", expect.objectContaining({ tenant_id: "t1", amount: "800.00", kind: "security" })],
    ]);
  });

  it("shows a deposit the signed lease asked as held once it all is, with nothing to record", async () => {
    const HELD: LeaseDetail = { ...LEASE, deposit_asked: { asked: "1200.00", held: "1200.00", to_receive: "0.00" } };
    server(HELD);
    await open(HELD);

    const asked = screen.getByTestId("deposit-asked");
    expect(asked.textContent).toBe("The signed lease asked $1,200.00: all heldHeld");
    expect(within(asked.closest("section") as HTMLElement).queryByRole("button", { name: "Record deposit" })).toBeNull();
  });

  it("asks which deposit account a deposit goes in when the company has two, and only then", async () => {
    const base = server();
    const fetch = vi.fn((path: string, init?: RequestInit) =>
      path === "/api/setup" ? Promise.resolve(new Response(JSON.stringify(TWO_DEPOSIT_ACCOUNTS), { status: 200 }))
        : base(path, init));
    vi.stubGlobal("fetch", fetch);
    await open();

    const deposit = await panel("Record deposit", true);
    const account = await within(deposit).findByLabelText("Deposit account");
    expect([...(account as HTMLSelectElement).options].map((o) => o.textContent)).toEqual(["Deposits 1", "Deposits 2"]);
    fireEvent.change(account, { target: { value: "b3" } });
    fireEvent.change(within(deposit).getByLabelText("Deposit received"), { target: { value: "500.00" } });
    fireEvent.click(within(deposit).getByRole("button", { name: "Record deposit" }));
    expect(await screen.findByText("Security deposit of $500.00 held.")).toBeTruthy();

    expect(posted(base)).toEqual([["/api/rent/deposits", expect.objectContaining({ amount: "500.00", bank_account_id: "b3" })]]);
  });

  it("shows the ledger with its running balance, and marks a payment bounced from its ⋯ menu", async () => {
    const fetch = server(LEASE, { "/api/rent/payments/tr1/bounce": { transfers: ["r"], nsf_fee: "35.00" } });
    await open();
    fireEvent.click(screen.getByRole("tab", { name: "Ledger" }));
    const ledger = await screen.findByRole("table", { name: "Ledger" });
    expect(within(ledger).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["08/01/2026", "Rent", "Rent due 08/01/2026", "$1,500.00", "", "$1,500.00"],
      ["08/01/2026", "Payment Bounced", "", "", "$1,500.00", "$0.00"],
      ["08/05/2026", "NSF fee", "NSF fee", "$35.00", "", "$35.00"],
      ["08/05/2026", "Bounced payment", "", "", "($1,500.00)", "$1,535.00"],
    ]);
    // The ledger downloads as the tenant's own does, as of today.
    const today = new Date().toISOString().slice(0, 10);
    const downloads = within(screen.getByRole("group", { name: "Download the ledger" })).getAllByRole("link");
    expect(downloads.map((link) => [link.textContent, link.getAttribute("href")])).toEqual(
      (["pdf", "csv", "xlsx"] as const).map((format, n) => [
        ["PDF", "CSV", "Excel"][n],
        `/api/reports/trust_report_tenant_ledger?arg=l1&arg=${today}&format=${format}`,
      ]),
    );
    // Each payment as it came in, whole; It bounced is in the ⋯ menu of the one that hasn't.
    const received = screen.getByRole("table", { name: "Payments received" });
    expect(within(received).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["09/01/2026Check 101Receipt (PDF)", "$1,500.00", ""],
      ["08/01/2026EFTReceipt (PDF)", "$1,500.00", "Bounced"],  // how it came, when no reference says
    ]);
    const bounced = () => {
      fireEvent.click(within(received).getByRole("button", { name: "Actions for 09/01/2026 payment of $1,500.00" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "It bounced" })); // the menu opens over the page
    };
    bounced();
    fireEvent.click(within(received).getByRole("button", { name: "Cancel" }));
    expect(within(received).queryByLabelText("Bounced on, 09/01/2026 payment")).toBeNull();
    bounced();
    fireEvent.change(within(received).getByLabelText("Bounced on, 09/01/2026 payment"), { target: { value: "2026-09-05" } });
    fireEvent.click(within(received).getByRole("button", { name: "Record the $1,500.00 bounce" }));
    expect(await screen.findByText("The $1,500.00 payment bounced and is undone; a $35.00 NSF fee is charged.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/rent/payments/tr1/bounce", { on: "2026-09-05" }]]);
  });

  it("sends Record NSF to the ledger and Move out to its page, and renews the lease in a side panel", async () => {
    server();
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Actions for this lease" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Record NSF" }));
    expect(screen.getByRole("table", { name: "Payments received" })).toBeTruthy();
    // Renew lease stays on the lease (ADR 0010 phase 3, part L): no trip to Draft leases.
    const renew = await panel("Renew lease", true);
    expect(renew).toBeTruthy();
    expect(window.location.pathname).toBe("/");
    fireEvent.click(within(renew).getByRole("button", { name: "Close" }));
    await vi.waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(document.querySelector("body > [aria-hidden='true']")).toBeNull();
    });
    fireEvent.click(screen.getByRole("button", { name: "Actions for this lease" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Move out" }));
    expect(window.location.pathname).toBe("/move-out");
  });

  it("offers no renewal of a lease that has ended", async () => {
    server({ ...LEASE, status: "ended", ends_on: "2026-09-30" });
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Actions for this lease" }));
    expect(screen.queryByRole("menuitem", { name: "Renew lease" })).toBeNull();
    expect(screen.getByRole("menuitem", { name: "End lease" })).toBeTruthy();
  });

  it("stops a monthly charge from its ⋯ menu and adds one from the lease's", async () => {
    const fetch = server();
    await open();
    expect(document.body.textContent).toContain("Concession: credit of $100.00 a month from 02/01/2026, stops 12/01/2026");
    // Only one still to stop.
    expect(screen.getAllByRole("button", { name: /^Actions for (Pet rent|Concession)$/ }).map((b) => b.getAttribute("aria-label"))).toEqual([
      "Actions for Pet rent",
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Actions for Pet rent" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Stop" }));
    fireEvent.change(screen.getByLabelText("Stops on, Pet rent"), { target: { value: "2026-11-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Stop it" }));
    expect(await screen.findByText("Pet rent stops 11/01/2026.")).toBeTruthy();

    const form = await panel("Add recurring charge", true);
    fireEvent.change(within(form).getByLabelText("Every month, a"), { target: { value: "fee" } });
    fireEvent.change(within(form).getByLabelText("Monthly amount"), { target: { value: "50.00" } });
    fireEvent.change(within(form).getByLabelText("First due on"), { target: { value: "2026-11-01" } });
    fireEvent.change(within(form).getByLabelText("Monthly, for"), { target: { value: "Parking" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add every month" }));
    expect(await screen.findByText("Parking of $50.00 added every month.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/rent/recurring-charges/r1/stop", { ends_on: "2026-11-01" }],
      ["/api/rent/leases/l1/recurring-charges", { kind: "fee", amount: "50.00", memo: "Parking", starts_on: "2026-11-01" }],
    ]);
  });

  it("shows the rent changes and records one after a full month's notice", async () => {
    const fetch = server({
      ...LEASE,
      rent_changes: [{
        id: "rc1", effective_on: "2026-12-01", monthly_rent: "1575.00", notice_given_on: "2026-10-15",
        served_by: "mail", lease_terms: null,
      }],
    });
    await open();
    expect(screen.getByRole("list", { name: "Rent changes, Unit 1" }).textContent).toBe(
      "From 12/01/2026: $1,575.00 a month (notice by mail on 10/15/2026)",
    );
    const form = await panel("Change rent", true);
    // A month-to-month lease names no lease term.
    expect(within(form).queryByLabelText("The lease term that allows it")).toBeNull();
    fireEvent.change(within(form).getByLabelText("New monthly rent"), { target: { value: "1600.00" } });
    fireEvent.change(within(form).getByLabelText("From (the first of a month)"), { target: { value: "2027-01-01" } });
    fireEvent.change(within(form).getByLabelText("Written notice given on"), { target: { value: "2026-11-01" } });
    fireEvent.change(within(form).getByLabelText("Served"), { target: { value: "in person" } });
    fireEvent.click(within(form).getByRole("button", { name: "Change rent" }));
    expect(await screen.findByText("Rent of $1,600.00 a month from 01/01/2027 recorded.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/rent/leases/l1/rent-changes", {
        effective_on: "2027-01-01", monthly_rent: "1600.00", notice_given_on: "2026-11-01", served_by: "in person",
        lease_terms: null,
      }],
    ]);
  });

  it("says when rent is due, lists its due days and moves it from a month", async () => {
    const fetch = server({
      ...LEASE,
      rent_due_day: 5,
      due_days: [{ id: "d1", starts_on: "2026-09-01", due_day: 5 }],
    }, { "/api/rent/leases/l1/due-day": { id: "d2", starts_on: "2027-01-01", due_day: 12 } });
    await open();
    expect(screen.getByTestId("rent-due-day").textContent).toBe("Due on the 5th");
    expect(screen.getByRole("list", { name: "Rent due day, Unit 1" }).textContent).toBe(
      "From 09/01/2026: due on the 5th of each month",
    );
    const form = await panel("Change rent due day", true);
    expect((within(form).getByLabelText("Rent due on day") as HTMLInputElement).value).toBe("5");
    fireEvent.change(within(form).getByLabelText("Rent due on day"), { target: { value: "12" } });
    fireEvent.change(within(form).getByLabelText("From (the first of a month)"), { target: { value: "2027-01-01" } });
    fireEvent.click(within(form).getByRole("button", { name: "Change due day" }));
    expect(await screen.findByText("Rent due on the 12th from 01/01/2027.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/rent/leases/l1/due-day", { starts_on: "2027-01-01", due_day: 12 }]]);
  });

  it("keeps the due day panel open with the reason a charged month is refused", async () => {
    server(LEASE, {}, {
      "/api/rent/leases/l1/due-day": "rent due 2026-11-01 is charged already; a new due day starts after its month",
    });
    await open();
    const form = await panel("Change rent due day", true);
    fireEvent.click(within(form).getByRole("button", { name: "Change due day" }));
    expect(await within(form).findByText(
      "rent due 2026-11-01 is charged already; a new due day starts after its month",
    )).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Change rent due day" })).toBeTruthy();
  });

  it("asks a lease with an end date for the term that allows a rent change", async () => {
    server({ ...LEASE, ends_on: "2026-12-31" });
    await open();
    expect(within(await panel("Change rent", true)).getByLabelText("The lease term that allows it")).toBeTruthy();
  });

  it("shows the lease's housing assistance on its summary", async () => {
    server();
    await open();
    expect((await screen.findByTestId("assistance-split")).textContent).toBe("The tenant pays all of the $1,500.00 rent.");
    expect(screen.getByRole("button", { name: "Set housing assistance" })).toBeTruthy();
  });

  it("shows renters insurance, sets its terms and records a policy", async () => {
    const fetch = server({
      ...LEASE,
      renters_insurance: {
        required: true, minimum_liability: "100000.00", carrier: null, policy_number: null,
        expires_on: null, liability_limit: null, standing: "none on file", below_minimum: false,
      },
    });
    await open();
    expect(screen.getByTestId("renters-insurance").textContent).toBe(
      "Renters insurance required (at least $100,000.00 of liability): none on filenone on file",
    );
    let form = await panel("Renters insurance", true);
    fireEvent.change(within(form).getByLabelText("Least liability coverage (optional)"), { target: { value: "200000.00" } });
    fireEvent.change(within(form).getByLabelText("Asked from"), { target: { value: "2026-11-01" } });
    fireEvent.click(within(form).getByRole("button", { name: "Set insurance terms" }));
    expect(await screen.findByText("Renters insurance is required from 11/01/2026.")).toBeTruthy();
    form = await panel("Renters insurance", true);
    fireEvent.change(within(form).getByLabelText("Insurer"), { target: { value: "Carrier A" } });
    fireEvent.change(within(form).getByLabelText("Policy number"), { target: { value: "RI-1" } });
    fireEvent.change(within(form).getByLabelText("Policy starts"), { target: { value: "2026-11-01" } });
    fireEvent.change(within(form).getByLabelText("Policy runs out"), { target: { value: "2027-10-31" } });
    fireEvent.click(within(form).getByLabelText("The landlord is named an interested party"));
    fireEvent.click(within(form).getByRole("button", { name: "Record policy" }));
    expect(await screen.findByText("Policy RI-1 from Carrier A recorded.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/rent/leases/l1/renters-insurance-terms", { required: true, minimum_liability: "200000.00", starts_on: "2026-11-01" }],
      ["/api/rent/leases/l1/renters-insurance", {
        carrier: "Carrier A", policy_number: "RI-1", starts_on: "2026-11-01", expires_on: "2027-10-31",
        liability_limit: null, landlord_interested_party: true,
      }],
    ]);
  });

  it("keeps a refused payment's panel open with the reason, and says when the lease is on a hold", async () => {
    server(HELD, {}, { "/api/rent/leases/l1/payments": "lease is on a payment hold" });
    await open();
    expect(screen.getByText(/On a payment hold/)).toBeTruthy();
    const form = await panel("Receive payment");
    fireEvent.change(within(form).getByLabelText("Payment received"), { target: { value: "10.00" } });
    fireEvent.click(within(form).getByRole("button", { name: "Record payment" }));
    expect(await within(form).findByText("lease is on a payment hold")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Receive payment" })).toBeTruthy();
    // Said once, in the panel, not again on the page behind it.
    expect(screen.getAllByText("lease is on a payment hold")).toHaveLength(1);
    fireEvent.click(within(form).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getAllByText("lease is on a payment hold")).toHaveLength(1);
  });

  it("waives an open charge, all of it or part, with why, from its ⋯ menu", async () => {
    const fetch = server(LEASE, { "/api/rent/charges/c2/waive": { amount: "20.00", open: "30.00" } });
    await open();
    const charges = screen.getByRole("list", { name: "Open charges" });
    expect(within(charges).getAllByRole("listitem").map((li) => li.querySelector("span")?.textContent)).toEqual([
      "10/01/2026 Rent due 10/01/2026: $1,500.00 open",
      "10/06/2026 Late fee: $50.00 open",
    ]);
    fireEvent.click(within(charges).getByRole("button", { name: "Actions for Late fee due 10/06/2026" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Waive" }));
    expect((within(charges).getByLabelText("Amount to waive") as HTMLInputElement).value).toBe("50.00");
    fireEvent.change(within(charges).getByLabelText("Amount to waive"), { target: { value: "20.00" } });
    fireEvent.change(within(charges).getByLabelText("Why"), { target: { value: "First time late" } });
    fireEvent.click(within(charges).getByRole("button", { name: "Waive $20.00" }));
    expect(await screen.findByText("Waived $20.00 of Late fee.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/rent/charges/c2/waive", { amount: "20.00", reason: "First time late" }]]);
  });

  it("opens each ledger line's charge or payment in a side panel", async () => {
    server();
    await open();
    fireEvent.click(screen.getByRole("tab", { name: "Ledger" }));
    const ledger = await screen.findByRole("table", { name: "Ledger" });
    const line = (n: number) => within(ledger).getAllByRole("row")[n] as HTMLElement;

    // The rent: still open, waived from right there.
    fireEvent.click(within(line(1)).getByRole("button", { name: "Rent" }));
    let item = screen.getByRole("dialog", { name: "Charge" });
    expect(within(item).getByTestId("charge-open").textContent).toBe("$1,500.00");
    expect(within(item).getByRole("list", { name: "Open charge" }).textContent).toContain("Rent");
    fireEvent.click(within(item).getByRole("button", { name: "Close" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // The NSF fee, nothing open of it now.
    fireEvent.click(line(3));
    item = await screen.findByRole("dialog", { name: "Charge" });
    expect(within(item).getByTestId("charge-open").textContent).toBe("Nothing: paid, credited or waived");
    fireEvent.click(within(item).getByRole("button", { name: "Close" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // The bounce opens the payment that bounced, with its receipt.
    fireEvent.click(line(4));
    item = await screen.findByRole("dialog", { name: "Bounced payment" });
    expect(item.textContent).toContain("This payment bounced: $1,500.00 came back on 08/05/2026.");
    expect(within(item).getByRole("link", { name: "Receipt (PDF)" }).getAttribute("href")).toBe("/api/rent/payments/tr0/receipt");
  });

  it("narrows the ledger to days with the balance brought forward, exports what it shows, and reads it again on focus", async () => {
    const fetch = server();
    await open();
    fireEvent.click(screen.getByRole("tab", { name: "Ledger" }));
    await screen.findByRole("table", { name: "Ledger" });
    expect(screen.getByRole("link", { name: "Export CSV" }).getAttribute("href")).toBe("/api/leases/l1/ledger?format=csv");
    expect(screen.getByTestId("updated").textContent).toMatch(/^Updated \d\d:\d\d [AP]M$/);

    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-08-03" } });

    const ledger = screen.getByRole("table", { name: "Ledger" });
    await vi.waitFor(() => expect(within(ledger).getAllByRole("row")).toHaveLength(4));
    expect(within(ledger).getAllByRole("row").slice(1, 2).map((r) => within(r).getAllByRole("cell").map((c) => c.textContent)))
      .toEqual([["08/03/2026", "Balance forward", "$0.00"]]);
    expect(screen.getByRole("link", { name: "Export CSV" }).getAttribute("href"))
      .toBe("/api/leases/l1/ledger?start=2026-08-03&format=csv");

    // The browser tab back in view: the lease and its ledger are read again.
    const reads = () => fetch.mock.calls.filter(([path]) => String(path).startsWith("/api/leases/l1/ledger")).length;
    const before = reads();
    act(() => { window.dispatchEvent(new Event("focus")); });
    await vi.waitFor(() => expect(reads()).toBe(before + 1));
  });

  it("links each payment to its receipt", async () => {
    server();
    await open();
    fireEvent.click(screen.getByRole("tab", { name: "Ledger" }));
    const table = screen.getByRole("table", { name: "Payments received" });
    expect(within(table).getAllByRole("link", { name: "Receipt (PDF)" }).map((a) => a.getAttribute("href"))).toEqual([
      "/api/rent/payments/tr1/receipt",
      "/api/rent/payments/tr0/receipt",
    ]);
  });

  it("places a payment hold from its ⋯ menu", async () => {
    const fetch = server(LEASE, {
      "/api/rent/leases/l1/holds": { id: "h1", starts_on: "2026-10-01", reason: "Eviction filed", ends_on: null, released_by: null, allowed: 0, in_force: true },
    });
    await open();
    const form = await panel("Place payment hold", true);
    fireEvent.change(within(form).getByLabelText("Hold payments from"), { target: { value: "2026-10-01" } });
    fireEvent.click(within(form).getByRole("button", { name: "Place hold" }));
    expect(await screen.findByText("On a payment hold since 10/01/2026.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/rent/leases/l1/holds", { starts_on: "2026-10-01", reason: "Eviction filed" }]]);
    // A lease with no hold has nothing to accept a payment past.
    expect(within(await panel("Receive payment")).queryByLabelText("Accept this payment past the payment hold")).toBeNull();
  });

  it("says since when and why a lease is on hold, accepts one payment past it and releases it", async () => {
    const fetch = server(HELD, {
      "/api/rent/holds/h1/release": { ...HELD.holds[0], ends_on: "2026-10-20", released_by: "Staff 1", in_force: false },
    });
    await open();
    expect(screen.getByText(/Since 10\/01\/2026: Eviction filed\. A payment is refused unless you accept it past the hold\./)).toBeTruthy();
    const pay = await panel("Receive payment");
    fireEvent.change(within(pay).getByLabelText("Payment received"), { target: { value: "700.00" } });
    fireEvent.click(within(pay).getByLabelText("Accept this payment past the payment hold"));
    fireEvent.click(within(pay).getByRole("button", { name: "Record payment" }));
    await vi.waitFor(() => expect(posted(fetch).map(([path]) => path)).toContain("/api/rent/leases/l1/payments"));
    const [, body] = posted(fetch).find(([path]) => path === "/api/rent/leases/l1/payments") ?? [];
    expect(body).toMatchObject({ amount: "700.00", past_hold: true });

    const release = await panel("Release payment hold", true);
    expect(within(release).getByText("On hold since 10/01/2026: Eviction filed")).toBeTruthy();
    fireEvent.change(within(release).getByLabelText("Accept payments again from"), { target: { value: "2026-10-20" } });
    fireEvent.click(within(release).getByRole("button", { name: "Release hold" }));
    expect(await screen.findByText("The payment hold is released from 10/20/2026.")).toBeTruthy();
    expect(posted(fetch).at(-1)).toEqual(["/api/rent/holds/h1/release", { ends_on: "2026-10-20" }]);
  });

  it("lists its tenants with their contact and portal, and makes an invite link", async () => {
    const fetch = server(LEASE, { "/api/invites": { login_id: "g2", url: "http://localhost/invite#abc", expires_at: "2026-10-20T00:00:00Z" } });
    await open();
    fireEvent.click(screen.getByRole("tab", { name: "Tenants" }));
    const one = screen.getByRole("row", { name: "Tenant 1" });
    expect(within(one).getAllByRole("cell").map((c) => c.textContent).slice(0, 4)).toEqual([
      "Tenant 1", "555-555-0101", "t1@example.com", "Active",
    ]);
    expect(within(one).getByRole("link", { name: "Tenant 1" }).getAttribute("href")).toBe("/tenants/t1");
    expect(within(one).getByRole("button", { name: "Resend invite" })).toBeTruthy();
    // Each invite says, before it is made, whom it is for and at which email (ADR 0010, rule 1).
    expect(within(one).getByTestId("button-says").textContent).toBe(
      "Makes a portal invite link for Tenant 1 at t1@example.com to hand them; nothing is emailed. Using it sets a new password.",
    );
    // No email on file: one is asked for.
    const two = screen.getByRole("row", { name: "Tenant 2" });
    fireEvent.change(within(two).getByLabelText("Email, Tenant 2"), { target: { value: "t2@example.com" } });
    expect(within(two).getByTestId("button-says").textContent).toBe(
      "Makes a portal invite link for Tenant 2 at t2@example.com to hand them; nothing is emailed.",
    );
    fireEvent.click(within(two).getByRole("button", { name: "Invite" }));
    expect(await screen.findByText("Invite link made for t2@example.com; give it to Tenant 2.")).toBeTruthy();
    expect((within(two).getByLabelText("Invite link, Tenant 2") as HTMLInputElement).value).toBe("http://localhost/invite#abc");
    expect(posted(fetch)).toEqual([["/api/invites", { kind: "tenant", person_id: "t2", email: "t2@example.com" }]]);
  });

  it("takes one tenant off from their ⋯ menu, their deposit moving to who stays, and marks who left", async () => {
    const fetch = server(LEASE, { "/api/move-out/l1/departures": { moved: "1500.00" } });
    await open();
    fireEvent.click(screen.getByRole("tab", { name: "Tenants" }));
    fireEvent.click(within(screen.getByRole("row", { name: "Tenant 1" })).getByRole("button", { name: "Actions for Tenant 1" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Take off the lease" }));
    const dialog = screen.getByRole("dialog", { name: "Take a tenant off the lease" });
    fireEvent.change(within(dialog).getByLabelText("Their $1,500.00 deposit"), { target: { value: "t2" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Take Tenant 1 off the lease" }));
    expect(await screen.findByText("Tenant 1 left the lease; their $1,500.00 deposit moved to Tenant 2.")).toBeTruthy();
    expect(posted(fetch)[0]).toEqual(["/api/move-out/l1/departures", {
      tenant_id: "t1", left_on: expect.any(String), deposit_to: "t2",
    }]);
    cleanup();

    // Once one left, the one who stays can't be taken off: the lease ends instead.
    server({
      ...LEASE,
      people: LEASE.people.map((p) => (p.id === "t1" ? { ...p, left_on: "2026-03-31", deposit_to: "t2" } : p)),
    });
    await open();
    fireEvent.click(screen.getByRole("tab", { name: "Tenants" }));
    expect(within(screen.getByRole("row", { name: "Tenant 1" })).getByText("Left 03/31/2026")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Actions for Tenant 2" })).toBeNull();
    // Below them, who else is on the lease: none yet of guarantors, occupants, pets or vehicles.
    expect(await screen.findAllByText("None on file.")).toHaveLength(4);
  });

  it("ends the lease from its ⋯ menu", async () => {
    const fetch = server();
    await open();
    const dialog = await panel("End lease", true);
    fireEvent.change(within(dialog).getByLabelText("Last day"), { target: { value: "2026-11-30" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "End lease" }));
    expect(await screen.findByText("The lease ends on 11/30/2026.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/move-out/l1/end", { ends_on: "2026-11-30", break_fee: false }]]);
  });

  it("opens its files and notes", async () => {
    server();
    await open();
    fireEvent.click(screen.getByRole("tab", { name: "Files" }));
    expect(await screen.findByText("No file yet.")).toBeTruthy();
    // Its tenants see its lease documents in the portal; staff are told so.
    expect(screen.getByText(/Its tenants download its lease documents in the portal/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Notes" }));
    expect(await screen.findByText("No notes yet.")).toBeTruthy();
  });

  it("says when there is no such lease", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "no such lease" }), { status: 404 })));
    render(<LeasePage me={ME} id="nope" />);
    expect((await screen.findByRole("alert")).textContent).toBe("no such lease");
  });
});
