import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { holdBack } from "../holdBack";
import type { BreakFee, Household, LeaseDetail, LeasePerson } from "../rentals";
import { petLine, vehicleLine } from "../rentals";
import { EndLeaseForm, HouseholdCards, RemoveTenantForm } from "./LeasePeople";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const LEASE: LeaseDetail = {
  id: "l1", property: "Property 1", unit: "Unit 1", property_id: "p1", unit_id: "u1", status: "current",
  starts_on: "2026-01-01", ends_on: "2026-12-31", monthly_rent: "1200.00", rent_changes: [], rent_due_day: 1, due_days: [], renters_insurance: null,
  tenants: [
    { id: "t1", name: "Tenant 1", prepaid: "0.00", deposit: "1500.00", deposits: [{ kind: "security", amount: "1500.00" }] },
    { id: "t2", name: "Tenant 2", prepaid: "0.00", deposit: "0.00", deposits: [] },
    { id: "t3", name: "Tenant 3", prepaid: "0.00", deposit: "0.00", deposits: [] },
  ],
  people: [
    { id: "t1", name: "Tenant 1", email: null, phone: null, portal: "no login", login_email: null, left_on: null, deposit_to: null },
    { id: "t2", name: "Tenant 2", email: null, phone: null, portal: "no login", login_email: null, left_on: null, deposit_to: null },
    { id: "t3", name: "Tenant 3", email: null, phone: null, portal: "no login", login_email: null, left_on: "2026-03-31", deposit_to: null },
  ],
  owners: [{ account: "a1", owner: "Owner 1", percent: null }],
  owed: "0.00",
  carried: "0.00",
  moved_from: null,
  moved_to: null,
  on_hold: false,
  open_charges: [],
  payments: [],
  recurring: [],
  holds: [],
};
const [FIRST, SECOND] = LEASE.people as [LeasePerson, LeasePerson, LeasePerson];

// A recorder that answers `answer` and keeps what each record said it did.
function recorder(answer: unknown = {}) {
  const said: string[] = [];
  const record = vi.fn(async (_path: string, _body: object, done: (answer: never) => string) => {
    said.push(done(answer as never));
    return true;
  });
  return { record, said };
}

function serve(reads: Record<string, unknown>) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") return new Response("{}", { status: 201 });
    const found = Object.entries(reads).find(([prefix]) => path.startsWith(prefix));
    if (!found) return new Response(JSON.stringify({ detail: "no such page" }), { status: 404 });
    return new Response(JSON.stringify(found[1]), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("RemoveTenantForm", () => {
  it("moves the leaving tenant's deposit to a tenant who stays, or keeps it under their name", async () => {
    const { record, said } = recorder({ moved: "1500.00" });
    render(<RemoveTenantForm lease={LEASE} person={FIRST} record={record} />);
    // Tenant 3 left already: only Tenant 2 stays on with them.
    expect(screen.getByText("Tenant 1 leaves; the lease goes on with Tenant 2.")).toBeTruthy();
    const deposit = screen.getByLabelText("Their $1,500.00 deposit") as HTMLSelectElement;
    expect([...deposit.options].map((o) => o.textContent)).toEqual([
      "Stays under their name until move-out",
      "Moves to Tenant 2",
    ]);
    fireEvent.change(deposit, { target: { value: "t2" } });
    fireEvent.change(screen.getByLabelText("Left on"), { target: { value: "2026-04-30" } });
    fireEvent.click(screen.getByRole("button", { name: "Take Tenant 1 off the lease" }));
    await vi.waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    expect(record.mock.calls[0]?.slice(0, 2)).toEqual([
      "/api/move-out/l1/departures",
      { tenant_id: "t1", left_on: "2026-04-30", deposit_to: "t2" },
    ]);
    expect(said).toEqual(["Tenant 1 left the lease; their $1,500.00 deposit moved to Tenant 2."]);
  });

  it("asks nothing of a deposit a tenant doesn't hold", () => {
    const { record } = recorder();
    render(<RemoveTenantForm lease={LEASE} person={SECOND} record={record} />);
    expect(screen.queryByLabelText(/deposit/)).toBeNull();
  });
});

describe("EndLeaseForm", () => {
  it("offers the lease-break fee when the lease ends before its end date, charged only if ticked", async () => {
    const fee: BreakFee = { early: true, fee: "2050.00", flat_fee: "250.00", rent_months: "1.5" };
    const fetch = serve({ "/api/move-out/l1/break-fee": fee });
    const { record, said } = recorder();
    render(<EndLeaseForm lease={LEASE} record={record} />);
    fireEvent.change(screen.getByLabelText("Last day"), { target: { value: "2026-08-31" } });
    const charge = await screen.findByLabelText("Charge the lease-break fee: $2,050.00");
    expect(fetch.mock.calls.map(([path]) => path)).toContain("/api/move-out/l1/break-fee?ends_on=2026-08-31");
    fireEvent.click(charge);
    fireEvent.click(screen.getByRole("button", { name: "End lease" }));
    await vi.waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    expect(record.mock.calls[0]?.slice(0, 2)).toEqual([
      "/api/move-out/l1/end",
      { ends_on: "2026-08-31", break_fee: true },
    ]);
    expect(said).toEqual(["The lease ends on 08/31/2026; a $2,050.00 lease-break fee was charged."]);
  });

  it("says when the property charges no lease-break fee, and ends the lease without one", async () => {
    serve({ "/api/move-out/l1/break-fee": { early: true, fee: null, flat_fee: null, rent_months: null } });
    const { record, said } = recorder();
    render(<EndLeaseForm lease={LEASE} record={record} />);
    await screen.findByText("This lease's property charges no lease-break fee.");
    expect(screen.queryByRole("checkbox")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "End lease" }));
    await vi.waitFor(() => expect(record).toHaveBeenCalledTimes(1));
    expect(record.mock.calls[0]?.[1]).toEqual({ ends_on: "2026-12-31", break_fee: false });
    expect(said).toEqual(["The lease ends on 12/31/2026."]);
  });
});

const HOUSEHOLD: Household = {
  guarantors: [{ id: "g1", name: "Guarantor 1", email: "guarantor1@example.com", phone: null, signed: true }],
  occupants: [],
  pets: [{ id: "p1", name: "Pet 1", kind: "Dog", breed: "Breed 1", weight_lbs: "42.5", assistance_animal: true }],
  vehicles: [{ id: "v1", make: "Make 1", model: "Model 1", color: "Blue", plate: "SYN-001", plate_state: "State 1" }],
};

describe("HouseholdCards", () => {
  it("lists who else is on the lease and says an assistance animal is charged no pet fees", async () => {
    serve({ "/api/leases/l1/household": HOUSEHOLD });
    render(<HouseholdCards leaseId="l1" />);
    const guarantors = await screen.findByRole("table", { name: "Guarantors" });
    expect(within(guarantors).getByText("Signed the lease")).toBeTruthy();
    expect(screen.getByText("None on file.")).toBeTruthy(); // no other occupants
    expect(screen.getByText("Pet 1, Dog (Breed 1), 42.5 lb")).toBeTruthy();
    expect(screen.getByText("Assistance animal")).toBeTruthy();
    expect(screen.getByText(/no pet rent, fee or deposit is charged for it \(Fair Housing Act\)/)).toBeTruthy();
    expect(screen.getByText("Make 1 Model 1, Blue, SYN-001 (State 1)")).toBeTruthy();
  });

  it("shows the household of the lease opened now, even when an earlier lease's answer comes back last", async () => {
    serve({
      "/api/leases/l1/household": HOUSEHOLD,
      "/api/leases/l2/household": { ...HOUSEHOLD, guarantors: [{ ...HOUSEHOLD.guarantors[0]!, id: "g2", name: "Guarantor 2" }] },
    });
    const release = holdBack((path) => path === "/api/leases/l1/household");
    const { rerender } = render(<HouseholdCards leaseId="l1" />);
    rerender(<HouseholdCards leaseId="l2" />);
    await screen.findByRole("row", { name: "Guarantor 2" });
    await release();

    expect(screen.getByRole("row", { name: "Guarantor 2" })).toBeTruthy();
    expect(screen.queryByRole("row", { name: "Guarantor 1" })).toBeNull();
  });

  it("adds a pet in a side panel and takes a vehicle off once confirmed", async () => {
    const fetch = serve({ "/api/leases/l1/household": HOUSEHOLD });
    render(<HouseholdCards leaseId="l1" />);
    await screen.findByRole("table", { name: "Guarantors" });
    fireEvent.click(screen.getByRole("button", { name: "Add pet" }));
    const dialog = screen.getByRole("dialog", { name: "Add pet" });
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Pet 2" } });
    fireEvent.change(within(dialog).getByLabelText("Kind (dog, cat...)"), { target: { value: "Cat" } });
    fireEvent.click(within(dialog).getByRole("checkbox"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Add pet" }));
    await screen.findByText("Pet 2 is on the lease.");
    const posts = () => fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts()[0]?.[0]).toBe("/api/leases/l1/pets");
    expect(JSON.parse(String(posts()[0]?.[1]?.body))).toEqual({
      name: "Pet 2", kind: "Cat", breed: null, weight_lbs: null, assistance_animal: true,
    });

    const vehicle = "Make 1 Model 1, Blue, SYN-001 (State 1)";
    fireEvent.click(screen.getByRole("button", { name: `Actions for ${vehicle}` }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Take off the lease" }));
    const asked = screen.getByRole("alertdialog", { name: `Take ${vehicle} off this lease?` });
    fireEvent.click(within(asked).getByRole("button", { name: "Take off the lease" }));
    await screen.findByText(`${vehicle} is off the lease.`);
    expect(posts()[1]?.[0]).toBe("/api/leases/l1/vehicles/v1/remove");
  });
});

describe("household lines", () => {
  it("reads a pet and a vehicle in a line, leaving out what isn't known", () => {
    expect(petLine({ id: "p", name: "Pet 1", kind: "Cat", breed: null, weight_lbs: null, assistance_animal: false }))
      .toBe("Pet 1, Cat");
    expect(vehicleLine({ id: "v", make: "Make 1", model: null, color: null, plate: "SYN-002", plate_state: null }))
      .toBe("Make 1, SYN-002");
  });
});
