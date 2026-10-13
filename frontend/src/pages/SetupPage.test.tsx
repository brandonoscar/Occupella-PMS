import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Portfolio } from "../setup";
import { SetupPage } from "./SetupPage";

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
    },
    { id: "p2", name: "Property B", units: [{ id: "u3", name: "Unit 9", leases: [] }], late_fees: null },
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
      },
    },
  ],
  tenants: [
    { id: "t1", name: "Tenant A", property_id: "p1", property: "Property A" },
    { id: "t2", name: "Tenant B", property_id: "p1", property: "Property A" },
  ],
  vendors: [{ id: "v1", name: "Vendor A" }],
};

// The API: the books on GET; a POST is added, or refused with `refuse[path]`.
function server(refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(BOOKS), { status: 200 });
    const detail = refuse[path];
    return detail
      ? new Response(JSON.stringify({ detail }), { status: 409 })
      : new Response(JSON.stringify({ id: "new" }), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

const type = (label: string | RegExp, value: string, within_: HTMLElement = document.body) =>
  fireEvent.change(within(within_).getByLabelText(label), { target: { value } });

async function card(title: string) {
  return (await screen.findByRole("heading", { name: title })).closest("section") as HTMLElement;
}

describe("SetupPage", () => {
  it("shows the books as set up", async () => {
    window.history.replaceState(null, "", "/setup");
    server();
    render(<SetupPage me={ME} />);

    const banks = await card("Trust bank accounts");
    expect(banks.textContent).toContain("Operating trust (Operating)");
    expect(banks.textContent).toContain("Deposit trust (Security deposit)");
    const property = screen.getByRole("listitem", { name: "Property A" });
    expect(property.textContent).toContain("Owned by Owner A. After 5 days' grace: $50.00.");
    expect(property.textContent).toContain(
      "Unit 1: Tenant A: from 2026-10-01, month to month, $1,500.00 a month",
    );
    expect(property.textContent).toContain("Unit 2: vacant");
    expect((await card("Management terms")).textContent).toContain(
      "Owner A, Property A: 8% of rent and recurring fees collected; keeps $250.00 in reserve;" +
        " owner approves bills over $500.00 (from 2026-10-01)",
    );
    expect((await card("Tenants and leases in place")).textContent).toContain("Tenant B (Property A)");
    expect(screen.getByRole("link", { name: "Setup" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Overview" }).getAttribute("aria-current")).toBe(null);
  });

  it("adds a bank account, an owner and a vendor, and clears the name", async () => {
    const fetch = server();
    render(<SetupPage me={ME} />);

    const banks = await card("Trust bank accounts");
    fireEvent.change(within(banks).getByLabelText("Kind"), { target: { value: "security_deposit" } });
    type("Bank account name", "Second deposit trust", banks);
    fireEvent.click(within(banks).getByRole("button", { name: "Add bank account" }));
    type("Owner's name", "Owner B");
    fireEvent.click(within(screen.getByRole("region", { name: "Owners" })).getByRole("button", { name: "Add" }));
    type("Vendor's name", "Vendor B");
    fireEvent.click(within(screen.getByRole("region", { name: "Vendors" })).getByRole("button", { name: "Add" }));

    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(3));
    expect(posted(fetch)).toEqual([
      ["/api/setup/bank-accounts", { kind: "security_deposit", name: "Second deposit trust" }],
      ["/api/setup/owners", { name: "Owner B" }],
      ["/api/setup/vendors", { name: "Vendor B" }],
    ]);
    await vi.waitFor(() =>
      expect((screen.getByLabelText("Owner's name") as HTMLInputElement).value).toBe(""),
    );
  });

  it("adds a property in the only operating account, and to a property its unit, owner and late fees", async () => {
    const fetch = server();
    render(<SetupPage me={ME} />);

    const properties = await card("Properties");
    type("Property name", "Property B", properties);
    fireEvent.change(within(properties).getByLabelText("Owner", { selector: "#property-owner" }), {
      target: { value: "o1" },
    });
    fireEvent.click(within(properties).getByRole("button", { name: "Add property" }));
    const row = screen.getByRole("listitem", { name: "Property A" });
    type("Unit name", "Unit 3", row);
    fireEvent.click(within(row).getByRole("button", { name: "Add unit" }));
    fireEvent.change(within(row).getByLabelText("Owner"), { target: { value: "o1" } });
    fireEvent.click(within(row).getByRole("button", { name: "Add owner" }));
    type("Late fees from", "2026-11-01", row);
    type("Grace days", "3", row);
    type("Percent of unpaid", "5", row);
    fireEvent.click(within(row).getByRole("button", { name: "Set late fees" }));

    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(4));
    expect(posted(fetch)).toEqual([
      ["/api/setup/properties", { name: "Property B", owner_id: "o1", bank_account_id: "b1" }],
      ["/api/setup/units", { property_id: "p1", name: "Unit 3" }],
      ["/api/setup/properties/p1/owners", { owner_id: "o1", bank_account_id: "b1" }],
      [
        "/api/setup/late-fees",
        { property_id: "p1", starts_on: "2026-11-01", grace_days: 3, flat_fee: "0", percent: "5", maximum: null },
      ],
    ]);
  });

  it("sets management terms, with what is left empty as zero", async () => {
    const fetch = server();
    render(<SetupPage me={ME} />);

    const terms = await card("Management terms");
    fireEvent.change(within(terms).getByLabelText("For"), { target: { value: "acct-1" } });
    type("In force from", "2027-01-01", terms);
    type("Fee, percent of rent and recurring fees collected", "9", terms);
    type("Owner approves bills over", "750.00", terms);
    fireEvent.click(within(terms).getByRole("button", { name: "Set terms" }));

    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(1));
    expect(posted(fetch)[0]).toEqual([
      "/api/setup/agreements",
      {
        account: "acct-1",
        starts_on: "2027-01-01",
        fee_percent: "9",
        minimum_fee: "0",
        flat_fee: "0",
        leasing_fee_percent: "0",
        reserve: "0",
        approval_limit: "750.00",
      },
    ]);
  });

  it("adds a tenant, and a lease of a unit for its property's tenants", async () => {
    const fetch = server();
    render(<SetupPage me={ME} />);

    const leases = await card("Tenants and leases in place");
    fireEvent.change(within(leases).getByLabelText("Tenant of"), { target: { value: "p1" } });
    type("Tenant's name", "Tenant C", leases);
    fireEvent.click(within(leases).getByRole("button", { name: "Add tenant" }));
    const addLease = within(leases).getByRole("button", { name: "Add lease" }) as HTMLButtonElement;
    expect(addLease.disabled).toBe(true);
    fireEvent.change(within(leases).getByLabelText("Unit"), { target: { value: "u2" } });
    type("Starts", "2026-11-01", leases);
    type("Rent a month", "1250.00", leases);
    fireEvent.click(within(leases).getByLabelText("Tenant B"));
    fireEvent.click(within(leases).getByLabelText("Tenant A"));
    fireEvent.click(within(leases).getByLabelText("Tenant A"));
    fireEvent.click(addLease);

    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(2));
    expect(posted(fetch)).toEqual([
      ["/api/setup/tenants", { property_id: "p1", name: "Tenant C" }],
      [
        "/api/setup/leases",
        { unit_id: "u2", starts_on: "2026-11-01", ends_on: null, monthly_rent: "1250.00", tenant_ids: ["t2"] },
      ],
    ]);
  });

  it("says when a unit's property has no tenants yet", async () => {
    server();
    render(<SetupPage me={ME} />);
    const leases = await card("Tenants and leases in place");

    fireEvent.change(within(leases).getByLabelText("Unit"), { target: { value: "u3" } });

    expect(within(leases).getByText("Add the tenants of Property B first.")).toBeTruthy();
    expect(within(leases).queryByLabelText("Tenant A")).toBeNull();
    expect(screen.getByRole("listitem", { name: "Property B" }).textContent).toContain(
      "Owned by nobody yet. No late fee terms.",
    );
  });

  it("shows a refusal in the card that asked", async () => {
    server({ "/api/setup/owners": "no such owner" });
    render(<SetupPage me={ME} />);

    type("Owner's name", "Owner B", await card("Owners and vendors"));
    fireEvent.click(within(screen.getByRole("region", { name: "Owners" })).getByRole("button", { name: "Add" }));

    expect((await within(await card("Owners and vendors")).findByRole("alert")).textContent).toBe(
      "no such owner",
    );
    expect((screen.getByLabelText("Owner's name") as HTMLInputElement).value).toBe("Owner B");
  });

  it("says when the books can't be read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })),
    );
    render(<SetupPage me={ME} />);

    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });
});
