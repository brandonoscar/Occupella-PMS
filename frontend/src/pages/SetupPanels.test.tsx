import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Portfolio } from "../setup";
import { ChartOfAccountsPage } from "./ChartOfAccountsPage";
import type { Panel } from "./SetupPanels";
import {
  AddLeasePanel, AddOwnerPanel, AddPropertyPanel, AddUnitPanel, LateFeesPanel, LeaseBreakFeePanel, NsfFeePanel,
  TermsPanel,
} from "./SetupPanels";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
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
  bank_accounts: [
    { id: "b2", name: "Deposit trust", kind: "security_deposit", opened_at: null, closed_through: null },
    { id: "b1", name: "Operating trust", kind: "operating", opened_at: null, closed_through: null },
  ],
  owners: [{ id: "o1", name: "Owner A" }],
  properties: [
    {
      id: "p1",
      name: "Property A",
      units: [
        {
          id: "u1",
          name: "Unit 1",
          leases: [
            { id: "l1", starts_on: "2026-10-01", ends_on: null, monthly_rent: "1500.00", tenants: ["Tenant A"] },
          ],
        },
        { id: "u2", name: "Unit 2", leases: [] },
      ],
      late_fees: { starts_on: "2026-10-01", grace_days: 5, flat_fee: "50.00", percent: "0", maximum: null },
      nsf_fee: { starts_on: "2026-10-01", fee: "35.00" },
    },
    {
      id: "p2",
      name: "Property B",
      units: [{ id: "u3", name: "Unit 9", leases: [] }],
      late_fees: null,
      nsf_fee: null,
    },
  ],
  accounts: [
    {
      account: "acct-1",
      owner_id: "o1",
      owner: "Owner A",
      property_id: "p1",
      property: "Property A",
      bank_account: "Operating trust",
      agreement: {
        starts_on: "2026-10-01",
        fee_percent: "8",
        minimum_fee: "0",
        flat_fee: "0",
        leasing_fee_percent: "0",
        reserve: "250.00",
        approval_limit: "500.00",
        bill_markup_percent: "0",
      },
      ownership_percent: null,
    },
  ],
  tenants: [
    { id: "t1", name: "Tenant A", property_id: "p1", property: "Property A" },
    { id: "t2", name: "Tenant B", property_id: "p1", property: "Property A" },
  ],
  vendors: [{ id: "v1", name: "Vendor A" }],
  expense_accounts: [{ id: "e1", name: "Landscaping", schedule_e_line: 7 }],
  schedule_e_lines: [
    { line: 7, label: "Cleaning and maintenance" },
    { line: 14, label: "Repairs" },
    { line: 17, label: "Utilities" },
  ],
};

// The API: the books on GET; a POST is added, or refused with `refuse[path]`.
function server(refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(BOOKS), { status: 200 });
    const detail = refuse[path];
    return detail
      ? new Response(JSON.stringify({ detail }), { status: 409 })
      : new Response(JSON.stringify({ id: path.endsWith("/tenants") ? "t9" : "new" }), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

const type = (label: string | RegExp, value: string) =>
  fireEvent.change(within(screen.getByRole("dialog")).getByLabelText(label), { target: { value } });
const press = (name: string) => fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name }));

function panel(): Panel & { said: string[] } {
  const said: string[] = [];
  return { said, say: (s) => { if (s) said.push(s); }, refresh: vi.fn(), close: vi.fn() };
}

describe("setup panels", () => {
  it("adds a property in the only operating account and opens it", async () => {
    const fetch = server();
    const p = panel();
    const opened = vi.fn();
    render(<AddPropertyPanel panel={p} opened={opened} />);
    await screen.findByLabelText("Owner");

    type("Property name", "Property C");
    type("Owner", "o1");
    expect((screen.getByLabelText("Held in") as HTMLSelectElement).value).toBe("b1");
    press("Add property");
    await vi.waitFor(() => expect(opened).toHaveBeenCalledWith("new"));
    expect(posted(fetch)).toEqual([["/api/setup/properties", { name: "Property C", owner_id: "o1", bank_account_id: "b1" }]]);
    expect(p.said).toEqual(["Added Property C."]);
    expect(p.close).toHaveBeenCalled();
  });

  it("adds a unit and another owner to a property", async () => {
    const fetch = server();
    render(<AddUnitPanel panel={panel()} propertyId="p1" />);
    type("Unit name", "Unit 3");
    press("Add unit");
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(1));
    cleanup();

    const p = panel();
    render(<AddOwnerPanel panel={p} propertyId="p1" />);
    await screen.findByLabelText("Owner");
    type("Owner", "o1");
    press("Add owner");
    await vi.waitFor(() => expect(p.said).toEqual(["Added Owner A's account."]));
    expect(posted(fetch)).toEqual([
      ["/api/setup/units", { property_id: "p1", name: "Unit 3" }],
      ["/api/setup/properties/p1/owners", { owner_id: "o1", bank_account_id: "b1" }],
    ]);
  });

  it("sets management terms from today, with what is left empty as zero", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-10-17T12:00:00Z") });
    const fetch = server();
    const p = panel();
    render(<TermsPanel panel={p} accounts={[{ account: "acct-1", label: "Owner A, Property A" }, { account: "acct-2", label: "Owner A, Property B" }]} />);

    type("For", "acct-2");
    type("Fee, percent of rent and recurring fees collected", "8.5");
    type("Reserve", "250.00");
    type("Markup on bills, percent", "10");
    press("Set terms");
    await vi.waitFor(() => expect(p.said).toEqual(["New terms for Owner A, Property B from 2026-10-17."]));
    expect(posted(fetch)).toEqual([["/api/setup/agreements", {
      account: "acct-2", starts_on: "2026-10-17", fee_percent: "8.5", minimum_fee: "0", flat_fee: "0",
      leasing_fee_percent: "0", reserve: "250.00", approval_limit: "0", bill_markup_percent: "10",
    }]]);
  });

  it("sets late fees, with no cap when none is given, and an NSF fee", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-10-17T12:00:00Z") });
    const fetch = server();
    render(<LateFeesPanel panel={panel()} propertyId="p1" />);
    type("Grace days", "3");
    type("Flat fee", "25.00");
    press("Set late fees");
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(1));
    cleanup();

    render(<NsfFeePanel panel={panel()} propertyId="p1" />);
    type("NSF fee", "35.00");
    press("Set NSF fee");
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(2));
    expect(posted(fetch)).toEqual([
      ["/api/setup/late-fees", {
        property_id: "p1", starts_on: "2026-10-17", grace_days: 3, flat_fee: "25.00", percent: "0", maximum: null,
      }],
      ["/api/setup/nsf-fees", { property_id: "p1", starts_on: "2026-10-17", fee: "35.00" }],
    ]);
  });

  it("sets the lease-break fee leases starting from a day break under", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-10-17T12:00:00Z") });
    const fetch = server();
    const p = panel();
    render(<LeaseBreakFeePanel panel={p} propertyId="p1" />);
    type("Flat fee", "250.00");
    type("Months of rent", "1.5");
    press("Set lease-break fee");
    await vi.waitFor(() => expect(p.said).toEqual(["New lease-break fee for leases starting from 2026-10-17."]));
    expect(posted(fetch)).toEqual([["/api/setup/lease-break-fees", {
      property_id: "p1", starts_on: "2026-10-17", flat_fee: "250.00", rent_months: "1.5",
    }]]);
  });

  it("adds a lease in place for the property's tenants, one of them added there first, due on its day", async () => {
    const fetch = server();
    const p = panel();
    render(<AddLeasePanel panel={p} unitId="u2" propertyId="p1" propertyName="Property A" />);
    await screen.findByLabelText("Tenant A");
    expect(screen.queryByLabelText("Tenant B")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Tenant A"));
    type("Tenant's name", "Tenant C");
    press("Add tenant");
    expect(await screen.findByText("Added Tenant C as a tenant of Property A.")).toBeTruthy();
    type("Starts", "2026-10-01");
    type("Rent a month", "1,240.00");
    expect((screen.getByLabelText("Rent due on day (1 to 28)") as HTMLInputElement).value).toBe("1");
    type("Rent due on day (1 to 28)", "15");
    press("Add lease");
    await vi.waitFor(() => expect(p.close).toHaveBeenCalled());
    expect(posted(fetch)).toEqual([
      ["/api/setup/tenants", { property_id: "p1", name: "Tenant C" }],
      ["/api/setup/leases", { unit_id: "u2", starts_on: "2026-10-01", ends_on: null, monthly_rent: "1,240.00", tenant_ids: ["t1", "t9"], rent_due_day: 15 }],
    ]);
  });

  it("says when a property has no tenants yet, and why a save was refused, keeping the panel open", async () => {
    server({ "/api/setup/units": "a unit of that name is already there" });
    render(<AddLeasePanel panel={panel()} unitId="u3" propertyId="p2" propertyName="Property B" />);
    expect(await screen.findByText("No tenant of Property B yet.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Add lease" }) as HTMLButtonElement).disabled).toBe(true);
    cleanup();

    const p = panel();
    render(<AddUnitPanel panel={p} propertyId="p1" />);
    type("Unit name", "Unit 1");
    press("Add unit");
    expect(await screen.findByText("a unit of that name is already there")).toBeTruthy();
    expect(p.close).not.toHaveBeenCalled();
  });

  it("says when the books can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<AddOwnerPanel panel={panel()} propertyId="p1" />);
    expect(await screen.findByText("sign in first")).toBeTruthy();
  });
});

describe("ChartOfAccountsPage", () => {
  it("lists the expense accounts on their Schedule E lines and adds one from a side panel", async () => {
    const fetch = server();
    render(<ChartOfAccountsPage me={ME} />);

    const row = await screen.findByRole("row", { name: "Landscaping" });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual(["Landscaping", "7: Cleaning and maintenance"]);
    fireEvent.click(screen.getByRole("button", { name: "Add account" }));
    type("Expense account", "Repairs");
    type("Schedule E line", "14");
    press("Add account");
    expect(await screen.findByText("Added Repairs.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(posted(fetch)).toEqual([["/api/setup/expense-accounts", { name: "Repairs", schedule_e_line: 14 }]]);
  });

  it("says when there is no expense account yet", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...BOOKS, expense_accounts: [] }), { status: 200 })));
    render(<ChartOfAccountsPage me={ME} />);
    expect(await screen.findByText("No expense accounts yet")).toBeTruthy();
  });
});
