import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Tenant } from "../directory";
import { TenantPage } from "./TenantsPages";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState(null, "", "/");
  window.localStorage.clear();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const TENANT: Tenant = {
  id: "t1",
  name: "Tenant 1",
  archived: false,
  property: { id: "p1", name: "Property 1" },
  contact: {
    email: "tenant1@example.com", phone: null, alternate_phone: null, address_line_1: null,
    address_line_2: null, city: null, state: null, postal_code: null,
  },
  contact_by_tenant_at: null,
  prepaid: "0.00",
  deposit: "0.00",
  leases: [],
  portal: "active",
  login_email: "tenant1@example.com",
  same_person: [],
};

const PROPERTIES = {
  items: [
    { id: "p1", name: "Property 1", owners: [], units: 1, occupied: 1, archived: false },
    { id: "p2", name: "Property 2", owners: [], units: 1, occupied: 0, archived: false },
    { id: "p3", name: "Property 3", owners: [], units: 1, occupied: 0, archived: false },
  ],
  total: 3,
  page: 1,
  per_page: 200,
};

const FOUND = [
  { kind: "tenant", id: "t1", label: "Tenant 1", detail: "Unit 1, Property 1", href: "/tenants/t1" },
  { kind: "tenant", id: "t7", label: "Tenant 1", detail: "Unit 2, Property 3", href: "/tenants/t7" },
  { kind: "owner", id: "o1", label: "Owner 1", detail: "Owner", href: "/owners/o1" },
];

// The API: GETs from `answers`; a POST answers `made[path]` (201), or `refuse[path]`.
function server(
  answers: Record<string, unknown>,
  made: Record<string, unknown> = {},
  refuse: Record<string, [number, string]> = {},
) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const no = refuse[path];
      if (no) return new Response(JSON.stringify({ detail: no[1] }), { status: no[0] });
      if (path in made) return new Response(JSON.stringify(made[path]), { status: 201 });
      return new Response(null, { status: 204 });
    }
    const found = Object.keys(answers).find((key) => path.startsWith(key));
    if (found === undefined) return new Response(JSON.stringify({ detail: "no such page" }), { status: 404 });
    return new Response(JSON.stringify(answers[found]), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

const card = async () =>
  (await screen.findByRole("heading", { name: "Other properties" })).closest("section") as HTMLElement;

describe("Other properties on a tenant's page", () => {
  it("offers to make them a tenant at another property, then opens their record there", async () => {
    const fetch = server(
      { "/api/tenants/t1": TENANT, "/api/properties": PROPERTIES },
      { "/api/tenants/t1/elsewhere": { id: "t9" } },
    );
    render(<TenantPage me={ME} id="t1" />);

    expect((await card()).textContent).toContain("Renting at another property too?");
    fireEvent.click(within(await card()).getByRole("button", { name: "Rent at another property" }));
    const panel = await screen.findByRole("dialog", { name: "Rent at another property" });
    const choice = (await within(panel).findAllByRole("option")).map((option) => option.textContent);
    // Not the property they rent at already.
    expect(choice).toEqual(["Pick a property", "Property 2", "Property 3"]);
    fireEvent.change(within(panel).getByLabelText("Property"), { target: { value: "p2" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Make them a tenant there" }));

    await vi.waitFor(() => expect(window.location.pathname).toBe("/tenants/t9"));
    expect(posted(fetch)).toEqual([["/api/tenants/t1/elsewhere", { property_id: "p2" }]]);
  });

  it("finds the same person at another property and says so, or why not", async () => {
    const fetch = server(
      { "/api/tenants/t1": TENANT, "/api/search": FOUND },
      {},
      { "/api/tenants/t1/same-person": [409, "Tenant 1 signs in to the portal: link Tenant 1 to them instead"] },
    );
    render(<TenantPage me={ME} id="t1" />);

    fireEvent.click(within(await card()).getByRole("button", { name: "Same person as…" }));
    const panel = await screen.findByRole("dialog", { name: "Same person as…" });
    fireEvent.change(within(panel).getByLabelText("Name, email or phone"), { target: { value: "Tenant 1" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Find" }));
    const found = await within(panel).findByRole("list", { name: "Tenants found" });
    // Only other tenants: not this record, not an owner.
    expect(within(found).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Tenant 1 Unit 2, Property 3This is themLinks the two for good: one portal sign-in, and each record keeps its own leases and money.",
    ]);
    fireEvent.click(within(found).getByRole("button", { name: "Same person: Tenant 1, Unit 2, Property 3" }));

    expect(await within(panel).findByText("Tenant 1 signs in to the portal: link Tenant 1 to them instead")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/tenants/t1/same-person", { tenant_id: "t7" }]]);
  });

  it("lists their records elsewhere, and a record that isn't their first sends staff to it to invite", async () => {
    server({
      "/api/tenants/t9": {
        ...TENANT, id: "t9", property: { id: "p2", name: "Property 2" },
        same_person: [{ id: "t1", property: { id: "p1", name: "Property 1" }, first: true }],
      },
    });
    render(<TenantPage me={ME} id="t9" />);

    const records = within(await card()).getByRole("list", { name: "Tenant 1 at other properties" });
    expect(within(records).getByRole("link", { name: "Tenant at Property 1" }).getAttribute("href")).toBe("/tenants/t1");
    expect(records.textContent).toContain("Signs in here");
    const portal = screen.getByRole("heading", { name: "Portal" }).closest("section") as HTMLElement;
    expect(portal.textContent).toContain("signs in once, as their record at Property 1");
    expect(within(portal).queryByRole("button")).toBeNull();
    expect(within(portal).getByRole("link", { name: "Property 1" }).getAttribute("href")).toBe("/tenants/t1");
  });
});
