import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LeaseDetail } from "../rentals";
import { navigate } from "../router";
import type { Portfolio } from "../setup";
import { TransferForm, transferredLine } from "./LeaseTransfer";
import type { Recorder } from "./LeaseForms";

vi.mock("../router", async (original) => ({ ...(await original<typeof import("../router")>()), navigate: vi.fn() }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
beforeEach(() => vi.mocked(navigate).mockClear());

const LEASE = {
  id: "l1", property: "Property 1", unit: "Unit 1", property_id: "p1", unit_id: "u1",
  monthly_rent: "1000.00", rent_due_day: 5,
  tenants: [
    { id: "t1", name: "Tenant 1", prepaid: "0.00", deposit: "1000.00" },
    { id: "t2", name: "Tenant 2", prepaid: "0.00", deposit: "200.50" },
  ],
} as unknown as LeaseDetail;

const OWNER = { owner_id: "o1", property_id: "p1", property: "Property 1", bank_account: "b1", agreement: null };
const BOOKS = {
  bank_accounts: [], owners: [], tenants: [], vendors: [], expense_accounts: [], schedule_e_lines: [],
  properties: [
    { id: "p1", name: "Property 1", late_fees: null, nsf_fee: null,
      units: [{ id: "u1", name: "Unit 1", leases: [] }, { id: "u2", name: "Unit 2", leases: [] }] },
    { id: "p2", name: "Property 2", late_fees: null, nsf_fee: null, units: [{ id: "u7", name: "Unit 7", leases: [] }] },
  ],
  accounts: [
    { ...OWNER, account: "a1", owner: "Owner 1" },
    { ...OWNER, account: "a2", owner: "Owner 2", property_id: "p2", property: "Property 2" },
  ],
} as unknown as Portfolio;

function books(portfolio: Portfolio = BOOKS) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(portfolio), { status: 200 })));
}

// A recorder that answers `answer` and says it recorded, as the page's does.
function recorder(answer: unknown, recorded = true) {
  const said: string[] = [];
  const record = vi.fn(async (_path: string, _body: object, done: (a: never) => string) => {
    if (recorded) said.push(done(answer as never));
    return recorded;
  });
  return { record: record as unknown as Recorder, calls: record.mock.calls, said };
}

describe("transferredLine", () => {
  it("says where the tenants moved, what their prepaid rent paid and what to move at the bank", () => {
    expect(transferredLine({ lease_id: "l2", prepaid_used: "0.00", prepaid_moved: "0.00" }, "Property 2 · Unit 7"))
      .toBe("Moved to Property 2 · Unit 7.");
    expect(transferredLine({ lease_id: "l2", prepaid_used: "1000.00", prepaid_moved: "500.00" }, "Unit 7")).toBe(
      "Moved to Unit 7. Their prepaid rent paid $1,000.00 the old lease owed. Move $500.00 of their prepaid rent "
      + "to the new home's trust bank account at the bank.",
    );
  });
});

describe("TransferForm", () => {
  it("moves the tenants to a unit of another home, their deposit agreed to, and opens the new lease", async () => {
    books();
    const { record, calls, said } = recorder({ lease_id: "l2", prepaid_used: "0.00", prepaid_moved: "0.00" });
    render(<TransferForm lease={LEASE} record={record} />);

    const unit = await screen.findByLabelText("Move to") as HTMLSelectElement;
    // Every unit but the lease's own, by home.
    expect([...unit.options].map((o) => o.textContent)).toEqual(["Choose a unit", "Property 1 · Unit 2", "Property 2 · Unit 7"]);
    expect((screen.getByLabelText("Monthly rent there") as HTMLInputElement).value).toBe("1000.00");
    expect((screen.getByLabelText("Rent due day") as HTMLInputElement).value).toBe("5");
    // One owner's account at the lease's property: nothing to choose.
    expect(screen.queryByLabelText("What this lease still owes is paid to")).toBeNull();
    fireEvent.change(unit, { target: { value: "u7" } });
    fireEvent.change(screen.getByLabelText("First day there"), { target: { value: "2026-11-01" } });
    fireEvent.change(screen.getByLabelText("Monthly rent there"), { target: { value: "1100.00" } });
    fireEvent.change(screen.getByLabelText("Rent due day"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("The tenants agreed in writing that their $1,200.50 deposit stays held, on"),
      { target: { value: "2026-10-15" } });
    fireEvent.click(screen.getByRole("button", { name: "Move the tenants" }));

    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith("/leases/l2"));
    expect(calls).toHaveLength(1);
    const [path, body] = calls[0] as unknown as [string, Record<string, unknown>];
    expect(path).toBe("/api/move-out/l1/transfer");
    expect(body).toMatchObject({
      unit_id: "u7", moved_on: "2026-11-01", ends_on: null, monthly_rent: "1100.00", rent_due_day: 10,
      owner_account: null, deposit_agreed_on: "2026-10-15",
    });
    expect(typeof body.request_id).toBe("string");
    expect(said).toEqual(["Moved to Property 2 · Unit 7."]);
  });

  it("asks whose account the old lease's balance goes to when its property has two, and no agreement without a deposit", async () => {
    books({ ...BOOKS, accounts: [...BOOKS.accounts, { ...OWNER, account: "a3", owner: "Owner 3" }] } as Portfolio);
    const { record, calls } = recorder({ lease_id: "l2", prepaid_used: "0.00", prepaid_moved: "0.00" });
    const noDeposit = { ...LEASE, tenants: LEASE.tenants.map((t) => ({ ...t, deposit: "0.00" })) } as LeaseDetail;
    render(<TransferForm lease={noDeposit} record={record} />);

    const owner = await screen.findByLabelText("What this lease still owes is paid to") as HTMLSelectElement;
    expect([...owner.options].map((o) => o.textContent)).toEqual(["Choose the owner", "Owner 1", "Owner 3"]);
    expect(screen.queryByLabelText(/agreed in writing/)).toBeNull();
    fireEvent.change(screen.getByLabelText("Move to"), { target: { value: "u2" } });
    fireEvent.change(owner, { target: { value: "a3" } });
    fireEvent.change(screen.getByLabelText("Last day there (optional)"), { target: { value: "2027-10-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Move the tenants" }));

    await vi.waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]?.[1]).toMatchObject({ unit_id: "u2", owner_account: "a3", ends_on: "2027-10-31", deposit_agreed_on: null });
  });

  it("stays on the lease when the move is refused, and says why the books can't be read", async () => {
    books();
    const { record } = recorder(null, false);
    render(<TransferForm lease={LEASE} record={record} />);
    fireEvent.change(await screen.findByLabelText("Move to"), { target: { value: "u2" } });
    fireEvent.change(screen.getByLabelText(/agreed in writing/), { target: { value: "2026-10-15" } });
    fireEvent.click(screen.getByRole("button", { name: "Move the tenants" }));
    await vi.waitFor(() => expect(screen.getByRole("button", { name: "Move the tenants" })).toBeTruthy());
    expect(navigate).not.toHaveBeenCalled();

    cleanup();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "no access" }), { status: 403 })));
    render(<TransferForm lease={LEASE} record={record} />);
    expect(await screen.findByText("no access")).toBeTruthy();
  });
});
