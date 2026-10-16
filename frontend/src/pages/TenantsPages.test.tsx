import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Contact, Tenant, TenantRow } from "../directory";
import type { Paged } from "../rentals";
import { TenantPage, TenantsPage } from "./TenantsPages";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const NO_CONTACT: Contact = {
  email: null, phone: null, alternate_phone: null, address_line_1: null, address_line_2: null,
  city: null, state: null, postal_code: null,
};

const ROWS: Paged<TenantRow> = {
  items: [
    { id: "t1", name: "Tenant 1", property: { id: "p1", name: "Property 1" },
      lease: { id: "l1", unit: { id: "u1", name: "Unit 1" }, status: "current" },
      email: "tenant1@example.com", phone: "555-555-0100", archived: false },
    { id: "t2", name: "Tenant 2", property: { id: "p1", name: "Property 1" }, lease: null,
      email: null, phone: null, archived: false },
  ],
  total: 2,
  page: 1,
  per_page: 50,
};

const LINE = {
  id: "l1", tenants: ["Tenant 1"], starts_on: "2026-01-01", ends_on: null, monthly_rent: "1500.00",
  owed: "50.00", status: "current" as const, unit: { id: "u1", name: "Unit 1" },
  property: { id: "p1", name: "Property 1" },
};

const TENANT: Tenant = {
  id: "t1",
  name: "Tenant 1",
  archived: false,
  property: { id: "p1", name: "Property 1" },
  contact: {
    ...NO_CONTACT, email: "tenant1@example.com", phone: "555-555-0100", alternate_phone: "555-555-0101",
    address_line_1: "100 Example Street", address_line_2: "Apt 2", city: "Sampletown", state: "ZZ",
    postal_code: "00000",
  },
  prepaid: "25.00",
  deposit: "1500.00",
  leases: [LINE, { ...LINE, id: "l0", starts_on: "2025-01-01", ends_on: "2025-12-31", owed: "0.00", status: "ended" }],
};

// The API: `answers[path]` on GET; a POST answers 204, or 409/422 with `refuse[path]`.
function server(answers: Record<string, unknown>, refuse: Record<string, [number, string]> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const no = refuse[path];
      if (no) return new Response(JSON.stringify({ detail: no[1] }), { status: no[0] });
      return new Response(null, { status: 204 });
    }
    if (!(path in answers)) return new Response(JSON.stringify({ detail: "no such tenant" }), { status: 404 });
    return new Response(JSON.stringify(answers[path]), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("TenantsPage", () => {
  it("lists the tenants a page at a time, each with their unit, lease and contact", async () => {
    const fetch = server({
      "/api/tenants?page=1&archived=false": ROWS,
      "/api/tenants?page=1&archived=false&q=Unit+1": { ...ROWS, items: [ROWS.items[0]], total: 1 },
      "/api/tenants?page=1&archived=true&q=Unit+1": { ...ROWS, items: [], total: 0 },
    });
    render(<TenantsPage me={ME} />);

    const first = await screen.findByRole("row", { name: "Tenant 1" });
    expect(within(first).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Tenant 1", "Property 1", "Unit 1", "Current", "tenant1@example.com", "555-555-0100",
    ]);
    expect(within(first).getByRole("link", { name: "Tenant 1" }).getAttribute("href")).toBe("/tenants/t1");
    expect(within(screen.getByRole("row", { name: "Tenant 2" })).getAllByRole("cell")[3]?.textContent).toBe(
      "No lease",
    );

    fireEvent.change(screen.getByLabelText("Find a tenant, property or unit"), { target: { value: "Unit 1" } });
    await vi.waitFor(() => expect(screen.queryByRole("row", { name: "Tenant 2" })).toBeNull());
    fireEvent.click(screen.getByLabelText("Archived only"));
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No archived tenants.");
    expect(fetch.mock.calls.map(([path]) => path)).toEqual([
      "/api/tenants?page=1&archived=false",
      "/api/tenants?page=1&archived=false&q=Unit+1",
      "/api/tenants?page=1&archived=true&q=Unit+1",
    ]);
  });

  it("says when the tenants can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<TenantsPage me={ME} />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });
});

describe("TenantPage", () => {
  it("shows their property, money held, contact details and every lease", async () => {
    server({ "/api/tenants/t1": TENANT });
    render(<TenantPage me={ME} id="t1" />);

    expect(await screen.findByRole("heading", { level: 1, name: "Tenant 1" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Property 1" }).getAttribute("href")).toBe("/properties/p1");
    expect(screen.getByTestId("held").textContent).toBe("Prepaid rent $25.00 · Deposit held $1,500.00");
    const contact = screen.getByRole("group", { name: "Contact details, Tenant 1" });
    expect(within(contact).getByRole("link", { name: "tenant1@example.com" }).getAttribute("href")).toBe(
      "mailto:tenant1@example.com",
    );
    expect(contact.textContent).toContain("555-555-0101");
    expect(contact.textContent).toContain("100 Example Street, Apt 2Sampletown, ZZ 00000");
    const rows = screen.getAllByRole("row", { name: "Property 1, Unit 1" });
    expect(rows.map((row) => within(row).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["Unit 1, Property 1", "From 2026-01-01, month to month", "Current", "$1,500.00", "$50.00"],
      ["Unit 1, Property 1", "2025-01-01 to 2025-12-31", "Ended", "$1,500.00", "$0.00"],
    ]);
    expect(within(rows[0]!).getByRole("link").getAttribute("href")).toBe("/leases/l1");
  });

  it("edits their contact details whole, and says why a save or an archive was refused", async () => {
    const fetch = server(
      { "/api/tenants/t2": { ...TENANT, id: "t2", name: "Tenant 2", contact: NO_CONTACT, leases: [] } },
      {
        "/api/tenants/t2/archive": [409, "Tenant 2 has prepaid rent or a deposit held; return it before archiving"],
      },
    );
    render(<TenantPage me={ME} id="t2" />);
    const contact = await screen.findByRole("group", { name: "Contact details, Tenant 2" });
    expect(contact.textContent).toContain("No contact details yet.");
    expect(screen.getByTestId("empty-state").textContent).toContain("No lease yet.");

    // Cancel sends nothing; Save sends every field.
    fireEvent.click(within(contact).getByRole("button", { name: "Edit contact details" }));
    fireEvent.click(within(contact).getByRole("button", { name: "Cancel" }));
    fireEvent.click(within(contact).getByRole("button", { name: "Edit contact details" }));
    fireEvent.change(within(contact).getByLabelText("Email"), { target: { value: "tenant2@example.com" } });
    fireEvent.change(within(contact).getByLabelText("Street address"), { target: { value: "1 Example Road" } });
    fireEvent.change(within(contact).getByLabelText("City"), { target: { value: "Sampletown" } });
    fireEvent.change(within(contact).getByLabelText("State"), { target: { value: "ZZ" } });
    fireEvent.change(within(contact).getByLabelText("ZIP code"), { target: { value: "00000" } });
    fireEvent.click(within(contact).getByRole("button", { name: "Save contact details" }));
    expect(await within(contact).findByText("Contact details saved.")).toBeTruthy();
    expect(within(contact).queryByLabelText("Email")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Archive" }));
    fireEvent.click(screen.getByRole("button", { name: "Archive Tenant 2" }));
    expect(await screen.findByText("Tenant 2 has prepaid rent or a deposit held; return it before archiving")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      [
        "/api/tenants/t2/contact",
        { ...NO_CONTACT, email: "tenant2@example.com", address_line_1: "1 Example Road", city: "Sampletown",
          state: "ZZ", postal_code: "00000" },
      ],
      ["/api/tenants/t2/archive", { archived: true }],
    ]);
  });

  it("keeps the form open when a save is refused", async () => {
    server(
      { "/api/tenants/t2": { ...TENANT, id: "t2", name: "Tenant 2", contact: NO_CONTACT } },
      { "/api/tenants/t2/contact": [422, "a mailing address needs its street, city, state and ZIP code"] },
    );
    render(<TenantPage me={ME} id="t2" />);
    const contact = await screen.findByRole("group", { name: "Contact details, Tenant 2" });
    fireEvent.click(within(contact).getByRole("button", { name: "Edit contact details" }));
    fireEvent.change(within(contact).getByLabelText("Street address"), { target: { value: "1 Example Road" } });
    fireEvent.click(within(contact).getByRole("button", { name: "Save contact details" }));

    expect((await within(contact).findByRole("alert")).textContent).toBe(
      "a mailing address needs its street, city, state and ZIP code",
    );
    expect(within(contact).getByLabelText("Street address")).toBeTruthy();
  });

  it("says when there is no such tenant", async () => {
    server({});
    render(<TenantPage me={ME} id="nope" />);
    expect((await screen.findByRole("alert")).textContent).toBe("no such tenant");
  });
});
