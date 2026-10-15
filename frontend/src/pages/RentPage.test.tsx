import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { RentLease } from "../rent";
import { RentPage } from "./RentPage";

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

const LEASE: RentLease = {
  id: "l1",
  property: "Property 1",
  unit: "Unit 1",
  starts_on: "2026-01-01",
  ends_on: null,
  monthly_rent: "1500.00",
  rent_changes: [],
  renters_insurance: null,
  tenants: [
    { id: "t1", name: "Tenant 1", prepaid: "50.00", deposit: "1500.00" },
    { id: "t2", name: "Tenant 2", prepaid: "0.00", deposit: "0.00" },
  ],
  owed: "1550.00",
  on_hold: false,
  open_charges: [
    { id: "c1", due_on: "2026-10-01", kind: "rent", memo: "Rent due 2026-10-01", amount: "1500.00", open: "1500.00" },
    { id: "c2", due_on: "2026-10-06", kind: "fee", memo: "Late fee", amount: "50.00", open: "50.00" },
  ],
  payments: [
    { transfer_id: "tr1", received_on: "2026-09-01", amount: "1500.00", reference: "Check 101", bounced: false },
    { transfer_id: "tr0", received_on: "2026-08-01", amount: "1500.00", reference: null, bounced: true },
  ],
  recurring: [
    { id: "r1", kind: "fee", memo: "Pet rent", amount: "35.00", starts_on: "2026-01-01", ends_on: null },
    { id: "r2", kind: "credit", memo: "Concession", amount: "100.00", starts_on: "2026-02-01", ends_on: "2026-12-01" },
  ],
};

// The API: the leases on GET; each POST answers with `answers[path]` (or 201 {}), or 409.
function server(answers: Record<string, unknown> = {}, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      return new Response(JSON.stringify(answers[path] ?? [LEASE]), { status: 200 });
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

async function row() {
  return screen.findByRole("listitem", { name: "Property 1, Unit 1" });
}

describe("RentPage", () => {
  it("shows what each lease owes, its tenants, open charges and payments", async () => {
    server();
    render(<RentPage me={ME} />);

    const lease = await row();
    expect(within(lease).getByTestId("owed").textContent).toBe("Owes $1,550.00");
    expect(lease.textContent).toContain("Tenant 1: prepaid $50.00, deposit $1,500.00");
    expect(lease.textContent).toContain("2026-10-06 Late fee: $50.00 open");
    // The payments as rows: the day with its reference under it, the amount, and a badge on the one
    // that bounced.
    const payments = within(lease).getAllByRole("row").slice(1);
    expect(payments.map((r) => within(r).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["2026-09-01Check 101", "$1,500.00", "It bounced"],
      ["2026-08-01", "$1,500.00", "Bounced"],
    ]);
    expect(within(payments[0]!).getByText("Check 101").className).toContain("text-ink-muted");
    expect(within(lease).getByTestId("owed").getAttribute("data-tone")).toBe("caution");
    expect(within(payments[1]!).getByText("Bounced").closest("[data-tone]")?.getAttribute("data-tone")).toBe("danger");
    expect(within(lease).getAllByRole("button", { name: "It bounced" })).toHaveLength(1);
    expect(screen.queryByText(/payment hold/)).toBeNull();
  });

  it("runs the rent and late fees and says how many were charged", async () => {
    const fetch = server({
      "/api/rent/charge-rent": { charged: 3, recurring: 1 },
      "/api/rent/late-fees": { charged: 1 },
    });
    render(<RentPage me={ME} />);
    await row();

    fireEvent.change(screen.getByLabelText("Rent due on"), { target: { value: "2026-11-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Charge rent" }));
    expect(await screen.findByText("3 rents and 1 recurring charge charged.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Late fees as of"), { target: { value: "2026-11-06" } });
    fireEvent.click(screen.getByRole("button", { name: "Assess late fees" }));
    expect(await screen.findByText("1 late fee charged.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/rent/charge-rent", { on: "2026-11-01" }],
      ["/api/rent/late-fees", { on: "2026-11-06" }],
    ]);
  });

  it("records a payment once per request, and says where it went", async () => {
    const fetch = server({
      "/api/rent/leases/l1/payments": { transfers: ["a", "b"], to_owner: "1550.00", to_prepaid: "50.00" },
    });
    render(<RentPage me={ME} />);
    const lease = await row();

    fireEvent.change(within(lease).getByLabelText("Payment received"), { target: { value: "1600.00" } });
    fireEvent.change(within(lease).getByLabelText("Received on"), { target: { value: "2026-10-02" } });
    fireEvent.change(within(lease).getByLabelText("Check or reference"), { target: { value: "Check 102" } });
    fireEvent.click(within(lease).getByRole("button", { name: "Record payment" }));

    expect(
      await within(lease).findByText("$1,550.00 paid what is owed; $50.00 held as prepaid rent."),
    ).toBeTruthy();
    const [path, body] = posted(fetch)[0] as [string, Record<string, unknown>];
    expect(path).toBe("/api/rent/leases/l1/payments");
    expect(body).toMatchObject({ amount: "1600.00", received_on: "2026-10-02", reference: "Check 102" });
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/);

    // The next payment is a new request.
    fireEvent.change(within(lease).getByLabelText("Payment received"), { target: { value: "10.00" } });
    fireEvent.click(within(lease).getByRole("button", { name: "Record payment" }));
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(2));
    const second = posted(fetch)[1]?.[1] as Record<string, unknown>;
    expect(second.request_id).not.toBe(body.request_id);
    expect(second.reference).toBe(null);
  });

  it("uses prepaid rent, adds a credit, records a deposit and a bounce", async () => {
    const fetch = server({
      "/api/rent/leases/l1/use-prepaid": { transfers: ["p"], to_owner: "50.00", to_prepaid: "0.00" },
      "/api/rent/payments/tr1/bounce": { transfers: ["r"], nsf_fee: "35.00" },
    });
    render(<RentPage me={ME} />);
    const lease = await row();

    fireEvent.click(within(lease).getByRole("button", { name: "Use prepaid rent" }));
    expect(await within(lease).findByText("$50.00 paid what is owed.")).toBeTruthy();
    fireEvent.change(within(lease).getByLabelText("Add a"), { target: { value: "credit" } });
    fireEvent.change(within(lease).getByLabelText("Amount"), { target: { value: "25.00" } });
    fireEvent.change(within(lease).getByLabelText("What for"), { target: { value: "Leak repair" } });
    fireEvent.click(within(lease).getByRole("button", { name: "Add" }));
    expect(await within(lease).findByText("Credit of $25.00 added.")).toBeTruthy();
    fireEvent.change(within(lease).getByLabelText("Deposit from"), { target: { value: "t2" } });
    fireEvent.change(within(lease).getByLabelText("Deposit received"), { target: { value: "500.00" } });
    fireEvent.click(within(lease).getByRole("button", { name: "Record deposit" }));
    expect(await within(lease).findByText("Deposit of $500.00 held.")).toBeTruthy();
    // A bounce asks for the day it came back before anything is recorded; Cancel records nothing.
    expect(within(lease).queryByLabelText("Bounced on, 2026-09-01 payment")).toBeNull();
    fireEvent.click(within(lease).getByRole("button", { name: "It bounced" }));
    fireEvent.click(within(lease).getByRole("button", { name: "Cancel" }));
    expect(within(lease).queryByLabelText("Bounced on, 2026-09-01 payment")).toBeNull();
    fireEvent.click(within(lease).getByRole("button", { name: "It bounced" }));
    fireEvent.change(within(lease).getByLabelText("Bounced on, 2026-09-01 payment"), {
      target: { value: "2026-09-05" },
    });
    fireEvent.click(within(lease).getByRole("button", { name: "Record the bounce" }));
    expect(
      await within(lease).findByText("The $1,500.00 payment bounced and is undone; a $35.00 NSF fee is charged."),
    ).toBeTruthy();

    const calls = posted(fetch);
    expect(calls.map(([path]) => path)).toEqual([
      "/api/rent/leases/l1/use-prepaid",
      "/api/rent/leases/l1/charges",
      "/api/rent/deposits",
      "/api/rent/payments/tr1/bounce",
    ]);
    expect(calls[1]?.[1]).toMatchObject({ kind: "credit", amount: "25.00", memo: "Leak repair" });
    expect(calls[2]?.[1]).toMatchObject({ tenant_id: "t2", amount: "500.00" });
    expect(calls[3]?.[1]).toEqual({ on: "2026-09-05" });
  });

  it("shows a lease's recurring charges, adds one and stops one", async () => {
    const fetch = server();
    render(<RentPage me={ME} />);
    const lease = await row();

    expect(lease.textContent).toContain("Pet rent: $35.00 a month from 2026-01-01");
    expect(lease.textContent).toContain(
      "Concession: credit of $100.00 a month from 2026-02-01, stops 2026-12-01",
    );
    // Only one still to stop.
    expect(within(lease).getAllByRole("button", { name: "Stop" })).toHaveLength(1);
    // Stopping asks for the day it stops first.
    expect(within(lease).queryByLabelText("Stops on, Pet rent")).toBeNull();
    fireEvent.click(within(lease).getByRole("button", { name: "Stop" }));
    fireEvent.change(within(lease).getByLabelText("Stops on, Pet rent"), {
      target: { value: "2026-11-01" },
    });
    fireEvent.click(within(lease).getByRole("button", { name: "Stop it" }));
    expect(await within(lease).findByText("Pet rent stops 2026-11-01.")).toBeTruthy();

    fireEvent.change(within(lease).getByLabelText("Every month, a"), { target: { value: "fee" } });
    fireEvent.change(within(lease).getByLabelText("Monthly amount"), { target: { value: "50.00" } });
    fireEvent.change(within(lease).getByLabelText("First due on"), { target: { value: "2026-11-01" } });
    fireEvent.change(within(lease).getByLabelText("Monthly, for"), { target: { value: "Parking" } });
    fireEvent.click(within(lease).getByRole("button", { name: "Add every month" }));
    expect(await within(lease).findByText("Parking of $50.00 added every month.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/rent/recurring-charges/r1/stop", { ends_on: "2026-11-01" }],
      [
        "/api/rent/leases/l1/recurring-charges",
        { kind: "fee", amount: "50.00", memo: "Parking", starts_on: "2026-11-01" },
      ],
    ]);
  });

  it("shows a lease's rent changes and records one after a full month's notice", async () => {
    const changed: RentLease = {
      ...LEASE,
      rent_changes: [{
        id: "rc1", effective_on: "2026-12-01", monthly_rent: "1575.00", notice_given_on: "2026-10-15",
        served_by: "mail", lease_terms: null,
      }],
    };
    const fetch = server({ "/api/rent": [changed], "/api/rent/leases/l1/rent-changes": { id: "rc2" } });
    render(<RentPage me={ME} />);
    const lease = await row();

    expect(within(lease).getByRole("list", { name: "Rent changes, Unit 1" }).textContent).toBe(
      "From 2026-12-01: $1,575.00 a month (notice by mail on 2026-10-15)",
    );
    // A month-to-month lease names no lease term.
    expect(within(lease).queryByLabelText("The lease term that allows it")).toBeNull();
    fireEvent.change(within(lease).getByLabelText("New monthly rent"), { target: { value: "1600.00" } });
    fireEvent.change(within(lease).getByLabelText("From (the first of a month)"), { target: { value: "2027-01-01" } });
    fireEvent.change(within(lease).getByLabelText("Written notice given on"), { target: { value: "2026-11-01" } });
    fireEvent.change(within(lease).getByLabelText("Served"), { target: { value: "in person" } });
    fireEvent.click(within(lease).getByRole("button", { name: "Change rent" }));
    expect(await within(lease).findByText("Rent of $1,600.00 a month from 2027-01-01 recorded.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      [
        "/api/rent/leases/l1/rent-changes",
        {
          effective_on: "2027-01-01", monthly_rent: "1600.00", notice_given_on: "2026-11-01",
          served_by: "in person", lease_terms: null,
        },
      ],
    ]);
  });

  it("shows a lease's renters insurance, sets its terms and records a policy", async () => {
    const insured: RentLease = {
      ...LEASE,
      renters_insurance: {
        required: true, minimum_liability: "100000.00", carrier: null, policy_number: null,
        expires_on: null, liability_limit: null, standing: "none on file", below_minimum: false,
      },
    };
    const fetch = server({ "/api/rent": [insured] });
    render(<RentPage me={ME} />);
    const lease = await row();

    const line = within(lease).getByTestId("renters-insurance");
    expect(line.textContent).toBe(
      "Renters insurance required (at least $100,000.00 of liability): none on filenone on file",
    );
    fireEvent.change(within(lease).getByLabelText("Least liability coverage (optional)"), {
      target: { value: "200000.00" },
    });
    fireEvent.change(within(lease).getByLabelText("Asked from"), { target: { value: "2026-11-01" } });
    fireEvent.click(within(lease).getByRole("button", { name: "Set insurance terms" }));
    expect(await within(lease).findByText("Renters insurance is required from 2026-11-01.")).toBeTruthy();

    fireEvent.change(within(lease).getByLabelText("Insurer"), { target: { value: "Carrier A" } });
    fireEvent.change(within(lease).getByLabelText("Policy number"), { target: { value: "RI-1" } });
    fireEvent.change(within(lease).getByLabelText("Policy starts"), { target: { value: "2026-11-01" } });
    fireEvent.change(within(lease).getByLabelText("Policy runs out"), { target: { value: "2027-10-31" } });
    fireEvent.click(within(lease).getByLabelText("The landlord is named an interested party"));
    fireEvent.click(within(lease).getByRole("button", { name: "Record policy" }));
    expect(await within(lease).findByText("Policy RI-1 from Carrier A recorded.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/rent/leases/l1/renters-insurance-terms", { required: true, minimum_liability: "200000.00", starts_on: "2026-11-01" }],
      [
        "/api/rent/leases/l1/renters-insurance",
        {
          carrier: "Carrier A", policy_number: "RI-1", starts_on: "2026-11-01", expires_on: "2027-10-31",
          liability_limit: null, landlord_interested_party: true,
        },
      ],
    ]);
  });

  it("asks a lease with an end date for the term that allows a rent change", async () => {
    server({ "/api/rent": [{ ...LEASE, ends_on: "2026-12-31" }] });
    render(<RentPage me={ME} />);
    const lease = await row();

    expect(within(lease).getByLabelText("The lease term that allows it")).toBeTruthy();
  });

  it("shows a refusal on the lease, and a hold", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init?: RequestInit) =>
        init?.method === "POST"
          ? new Response(JSON.stringify({ detail: `lease is on a payment hold (${path})` }), { status: 409 })
          : new Response(JSON.stringify([{ ...LEASE, on_hold: true, payments: [], open_charges: [] }]), { status: 200 }),
      ),
    );
    render(<RentPage me={ME} />);
    const lease = await row();

    expect(within(lease).getByText(/On a payment hold/)).toBeTruthy();
    fireEvent.change(within(lease).getByLabelText("Payment received"), { target: { value: "10.00" } });
    fireEvent.click(within(lease).getByRole("button", { name: "Record payment" }));

    expect(
      await within(lease).findByText("lease is on a payment hold (/api/rent/leases/l1/payments)"),
    ).toBeTruthy();
  });

  it("says when no lease needs rent work, and when the leases can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
    render(<RentPage me={ME} />);
    expect(await screen.findByText("No lease is in effect or owes anything.")).toBeTruthy();
    cleanup();

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })),
    );
    render(<RentPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });

  it("shows why a run was refused", async () => {
    server({}, { "/api/rent/late-fees": "late fees are assessed as of today or an earlier day" });
    render(<RentPage me={ME} />);
    await row();

    fireEvent.click(screen.getByRole("button", { name: "Assess late fees" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "late fees are assessed as of today or an earlier day",
    );
  });
});
