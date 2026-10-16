import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
  starts_on: "2026-01-01", ends_on: null, monthly_rent: "1500.00", rent_changes: [], renters_insurance: null,
  tenants: [
    { id: "t1", name: "Tenant 1", prepaid: "50.00", deposit: "1500.00" },
    { id: "t2", name: "Tenant 2", prepaid: "0.00", deposit: "0.00" },
  ],
  people: [
    { id: "t1", name: "Tenant 1", email: "t1@example.com", phone: "555-555-0101", portal: "active", login_email: "t1@example.com" },
    { id: "t2", name: "Tenant 2", email: null, phone: null, portal: "no login", login_email: null },
  ],
  owed: "1550.00",
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
    { id: "r1", kind: "fee", memo: "Pet rent", amount: "35.00", starts_on: "2026-01-01", ends_on: null },
    { id: "r2", kind: "credit", memo: "Concession", amount: "100.00", starts_on: "2026-02-01", ends_on: "2026-12-01" },
  ],
};

const LEDGER: Ledger = {
  lines: [
    { on: "2026-08-01", kind: "rent", memo: "Rent due 2026-08-01", charged: "1500.00", paid: null, balance: "1500.00", bounced: false },
    { on: "2026-08-01", kind: "payment", memo: null, charged: null, paid: "1500.00", balance: "0.00", bounced: true },
    { on: "2026-08-05", kind: "NSF fee", memo: "NSF fee", charged: "35.00", paid: null, balance: "35.00", bounced: false },
    { on: "2026-08-05", kind: "bounced payment", memo: null, charged: null, paid: "-1500.00", balance: "1535.00", bounced: false },
  ],
  balance: "1535.00",
};

// The API: the lease and its ledger on GET; each POST answers `answers[path]` (or 201 {}), or 409.
function server(lease: LeaseDetail = LEASE, answers: Record<string, unknown> = {}, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      const reads: Record<string, unknown> = {
        "/api/leases/l1": lease,
        "/api/leases/l1/ledger": LEDGER,
        "/api/documents": { documents: [], subjects: { w9: [], receipt: [], move_out_photo: [], owner_document: [] } },
      };
      if (path.startsWith("/api/notes")) return new Response("[]", { status: 200 });
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

// One of the lease's side panels, from its button or its ⋯ menu.
function panel(name: string, fromMenu = false) {
  if (fromMenu) {
    fireEvent.click(screen.getByRole("button", { name: "Actions for this lease" }));
    fireEvent.click(screen.getByRole("menuitem", { name }));
  } else {
    fireEvent.click(screen.getByRole("button", { name }));
  }
  return screen.getByRole("dialog", { name });
}

describe("LeasePage", () => {
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
    expect(screen.getByTestId("prepaid").textContent).toBe("$50.00");
    expect(document.body.textContent).toContain("10/06/2026 Late fee: $50.00 open");
    expect(document.body.textContent).toContain("Pet rent: $35.00 a month from 01/01/2026");
    expect(screen.getByText("Not asked for.")).toBeTruthy();
    expect(screen.queryByText(/payment hold/)).toBeNull();
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
    let form = panel("Receive payment");
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
    form = panel("Receive payment");
    fireEvent.change(within(form).getByLabelText("Payment received"), { target: { value: "10.00" } });
    fireEvent.click(within(form).getByRole("button", { name: "Record payment" }));
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(2));
    const second = posted(fetch)[1]?.[1] as Record<string, unknown>;
    expect(second.request_id).not.toBe(body.request_id);
    expect(second.reference).toBe(null);
  });

  it("uses prepaid rent, enters a credit and records a deposit", async () => {
    const fetch = server(LEASE, {
      "/api/rent/leases/l1/use-prepaid": { transfers: ["p"], to_owner: "50.00", to_prepaid: "0.00" },
    });
    await open();
    fireEvent.click(within(panel("Receive payment")).getByRole("button", { name: "Use prepaid rent" }));
    expect(await screen.findByText("$50.00 paid what is owed.")).toBeTruthy();
    const charge = panel("Enter charge");
    fireEvent.change(within(charge).getByLabelText("Add a"), { target: { value: "credit" } });
    fireEvent.change(within(charge).getByLabelText("Amount"), { target: { value: "25.00" } });
    fireEvent.change(within(charge).getByLabelText("What for"), { target: { value: "Leak repair" } });
    fireEvent.click(within(charge).getByRole("button", { name: "Add" }));
    expect(await screen.findByText("Credit of $25.00 added.")).toBeTruthy();
    const deposit = panel("Record deposit", true);
    fireEvent.change(within(deposit).getByLabelText("Deposit from"), { target: { value: "t2" } });
    fireEvent.change(within(deposit).getByLabelText("Deposit received"), { target: { value: "500.00" } });
    fireEvent.click(within(deposit).getByRole("button", { name: "Record deposit" }));
    expect(await screen.findByText("Deposit of $500.00 held.")).toBeTruthy();

    const calls = posted(fetch);
    expect(calls.map(([path]) => path)).toEqual([
      "/api/rent/leases/l1/use-prepaid", "/api/rent/leases/l1/charges", "/api/rent/deposits",
    ]);
    expect(calls[1]?.[1]).toMatchObject({ kind: "credit", amount: "25.00", memo: "Leak repair" });
    expect(calls[2]?.[1]).toMatchObject({ tenant_id: "t2", amount: "500.00" });
  });

  it("shows the ledger with its running balance, and marks a payment bounced from its ⋯ menu", async () => {
    const fetch = server(LEASE, { "/api/rent/payments/tr1/bounce": { transfers: ["r"], nsf_fee: "35.00" } });
    await open();
    fireEvent.click(screen.getByRole("tab", { name: "Ledger" }));
    const ledger = screen.getByRole("table", { name: "Ledger" });
    expect(within(ledger).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["08/01/2026", "Rent", "Rent due 2026-08-01", "$1,500.00", "", "$1,500.00"],
      ["08/01/2026", "Payment Bounced", "", "", "$1,500.00", "$0.00"],
      ["08/05/2026", "NSF fee", "NSF fee", "$35.00", "", "$35.00"],
      ["08/05/2026", "Bounced payment", "", "", "($1,500.00)", "$1,535.00"],
    ]);
    // Each payment as it came in, whole; It bounced is in the ⋯ menu of the one that hasn't.
    const received = screen.getByRole("table", { name: "Payments received" });
    expect(within(received).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["09/01/2026Check 101", "$1,500.00", ""],
      ["08/01/2026EFT", "$1,500.00", "Bounced"],  // how it came, when no reference says
    ]);
    const bounced = () => {
      fireEvent.click(within(received).getByRole("button", { name: "Actions for 09/01/2026 payment of $1,500.00" }));
      fireEvent.click(within(received).getByRole("menuitem", { name: "It bounced" }));
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

  it("sends Record NSF to the ledger, and Renew lease and Move out to their pages", async () => {
    server();
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Actions for this lease" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Record NSF" }));
    expect(screen.getByRole("table", { name: "Payments received" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Actions for this lease" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Renew lease" }));
    expect(window.location.pathname).toBe("/draft-leases");
    fireEvent.click(screen.getByRole("button", { name: "Actions for this lease" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Move out" }));
    expect(window.location.pathname).toBe("/move-out");
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

    const form = panel("Add recurring charge", true);
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
    const form = panel("Change rent", true);
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

  it("asks a lease with an end date for the term that allows a rent change", async () => {
    server({ ...LEASE, ends_on: "2026-12-31" });
    await open();
    expect(within(panel("Change rent", true)).getByLabelText("The lease term that allows it")).toBeTruthy();
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
    let form = panel("Renters insurance", true);
    fireEvent.change(within(form).getByLabelText("Least liability coverage (optional)"), { target: { value: "200000.00" } });
    fireEvent.change(within(form).getByLabelText("Asked from"), { target: { value: "2026-11-01" } });
    fireEvent.click(within(form).getByRole("button", { name: "Set insurance terms" }));
    expect(await screen.findByText("Renters insurance is required from 11/01/2026.")).toBeTruthy();
    form = panel("Renters insurance", true);
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
    server({ ...LEASE, on_hold: true }, {}, { "/api/rent/leases/l1/payments": "lease is on a payment hold" });
    await open();
    expect(screen.getByText(/On a payment hold/)).toBeTruthy();
    const form = panel("Receive payment");
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
    // No email on file: one is asked for.
    const two = screen.getByRole("row", { name: "Tenant 2" });
    fireEvent.change(within(two).getByLabelText("Email, Tenant 2"), { target: { value: "t2@example.com" } });
    fireEvent.click(within(two).getByRole("button", { name: "Invite" }));
    expect(await screen.findByText("Invite link made for t2@example.com; give it to Tenant 2.")).toBeTruthy();
    expect((within(two).getByLabelText("Invite link, Tenant 2") as HTMLInputElement).value).toBe("http://localhost/invite#abc");
    expect(posted(fetch)).toEqual([["/api/invites", { kind: "tenant", person_id: "t2", email: "t2@example.com" }]]);
  });

  it("opens its files and notes", async () => {
    server();
    await open();
    fireEvent.click(screen.getByRole("tab", { name: "Files" }));
    expect(await screen.findByText("No file yet.")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Notes" }));
    expect(await screen.findByText("No notes yet.")).toBeTruthy();
  });

  it("says when there is no such lease", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "no such lease" }), { status: 404 })));
    render(<LeasePage me={ME} id="nope" />);
    expect((await screen.findByRole("alert")).textContent).toBe("no such lease");
  });
});
