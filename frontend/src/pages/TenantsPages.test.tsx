import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Contact, Tenant, TenantRow } from "../directory";
import { holdBack } from "../holdBack";
import type { Paged } from "../rentals";
import { archivable, TenantPage, TenantsPage } from "./TenantsPages";
import { autopayWord, leaseShown } from "./TenantLeaseCard";

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
// A server that sends email: an invite is emailed.
const EMAIL_ON: Me = { ...ME, email_on: true };

const NO_CONTACT: Contact = {
  email: null, phone: null, alternate_phone: null, address_line_1: null, address_line_2: null,
  city: null, state: null, postal_code: null,
};

const ROWS: Paged<TenantRow> = {
  items: [
    { id: "t1", name: "Tenant 1", property: { id: "p1", name: "Property 1" },
      lease: { id: "l1", unit: { id: "u1", name: "Unit 1" }, status: "current" },
      email: "tenant1@example.com", phone: "555-555-0100", archived: false, owed: "50.00", portal: "active" },
    { id: "t2", name: "Tenant 2", property: { id: "p1", name: "Property 1" }, lease: null,
      email: null, phone: null, archived: false, owed: "0.00", portal: "no login" },
  ],
  total: 2,
  page: 1,
  per_page: 50,
};

const LINE = {
  id: "l1", tenants: ["Tenant 1"], people: [{ id: "t1", name: "Tenant 1" }], starts_on: "2026-01-01", ends_on: null, monthly_rent: "1500.00",
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
  contact_by_tenant_at: null,
  prepaid: "25.00",
  deposit: "1500.00",
  leases: [LINE, { ...LINE, id: "l0", starts_on: "2025-01-01", ends_on: "2025-12-31", owed: "0.00", status: "ended" }],
  portal: "invited",
  login_email: "tenant1.login@example.com",
  same_person: [],
};

// The lease in effect as its page has it: rent due on the 5th, $50.00 owed.
const LEASE = {
  id: "l1", property: "Property 1", unit: "Unit 1", property_id: "p1", unit_id: "u1", status: "current",
  starts_on: "2026-01-01", ends_on: null, monthly_rent: "1500.00", rent_changes: [], rent_due_day: 5, due_days: [],
  tenants: [{ id: "t1", name: "Tenant 1", prepaid: "25.00", deposit: "1500.00" }],
  owners: [{ account: "a1", owner: "Owner 1", percent: null }], owed: "50.00", carried: "0.00",
  moved_from: null, moved_to: null, on_hold: false, open_charges: [], payments: [], recurring: [],
  renters_insurance: null, people: [], holds: [],
};
const AUTOPAY = [{ id: "ap1", tenant: "Tenant 1", rule: "balance", amount: "2000.00", account: "Test bank ••••0000",
  started_at: "2026-09-01T00:00:00Z", stopped_at: null, stopped: null, runs: [] }];
const HOUSEHOLD = { occupants: [], pets: [], vehicles: [], guarantors: [] };
const LEASE_ANSWERS = {
  "/api/leases/l1": LEASE,
  "/api/payments/autopay?lease=l1": AUTOPAY,
  "/api/leases/l1/household": HOUSEHOLD,
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
      "/api/tenants?page=1&archived=false&property=p1": { ...ROWS, items: [ROWS.items[0]], total: 1 },
      "/api/tenants?page=1&archived=true&property=p1": { ...ROWS, items: [], total: 0 },
      "/api/property-groups": { groups: [], properties: [{ id: "p1", name: "Property 1" }] },
    });
    render(<TenantsPage me={ME} />);

    const first = await screen.findByRole("row", { name: "Tenant 1" });
    expect(within(first).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Tenant 1", "Property 1", "Unit 1", "555-555-0100", "tenant1@example.com", "$50.00", "Active",
    ]);
    expect(within(first).getAllByRole("cell")[5]?.className).toContain("text-danger");
    expect(within(first).getByRole("link", { name: "Tenant 1" }).getAttribute("href")).toBe("/tenants/t1");
    const second = within(screen.getByRole("row", { name: "Tenant 2" })).getAllByRole("cell");
    expect([second[2]?.textContent, second[5]?.textContent, second[6]?.textContent]).toEqual([
      "No lease", "$0.00", "None",
    ]);
    // The whole row opens the tenant.
    fireEvent.click(second[1]!);
    expect(window.location.pathname).toBe("/tenants/t2");
    window.history.pushState(null, "", "/tenants");

    const picker = screen.getByRole("combobox", { name: "Properties shown" });
    await within(picker).findByRole("option", { name: "Property 1" });
    fireEvent.change(picker, { target: { value: "property:p1" } });
    await vi.waitFor(() => expect(screen.queryByRole("row", { name: "Tenant 2" })).toBeNull());
    fireEvent.change(screen.getByRole("combobox", { name: "Status" }), { target: { value: "archived" } });
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No archived tenants.");
    expect(fetch.mock.calls.map(([path]) => path).filter((path) => path !== "/api/property-groups")).toEqual([
      "/api/tenants?page=1&archived=false",
      "/api/tenants?page=1&archived=false&property=p1",
      "/api/tenants?page=1&archived=true&property=p1",
    ]);
  });

  it("shows the tenants of the property picked now, even when an earlier property's answer comes back last", async () => {
    server({
      "/api/tenants?page=1&archived=false": ROWS,
      "/api/tenants?page=1&archived=false&property=p1": { ...ROWS, items: [ROWS.items[0]], total: 1 },
      "/api/tenants?page=1&archived=false&property=p2": {
        ...ROWS, items: [{ ...ROWS.items[1]!, id: "t3", name: "Tenant 3", property: { id: "p2", name: "Property 2" } }], total: 1,
      },
      "/api/property-groups": { groups: [], properties: [{ id: "p1", name: "Property 1" }, { id: "p2", name: "Property 2" }] },
    });
    const release = holdBack((path) => path.includes("property=p1"));
    render(<TenantsPage me={ME} />);
    await screen.findByRole("row", { name: "Tenant 1" });
    const picker = screen.getByRole("combobox", { name: "Properties shown" });
    await within(picker).findByRole("option", { name: "Property 2" });

    fireEvent.change(picker, { target: { value: "property:p1" } });
    fireEvent.change(picker, { target: { value: "property:p2" } });
    await screen.findByRole("row", { name: "Tenant 3" });
    await release();

    expect(screen.getByRole("row", { name: "Tenant 3" })).toBeTruthy();
    expect(screen.queryByRole("row", { name: "Tenant 1" })).toBeNull();
  });

  it("says when the tenants can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<TenantsPage me={ME} />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });
});

describe("TenantPage", () => {
  it("shows their home, phone, email, money held, contact details and every lease", async () => {
    server({ "/api/tenants/t1": TENANT, ...LEASE_ANSWERS });
    render(<TenantPage me={EMAIL_ON} id="t1" />);

    expect(await screen.findByRole("heading", { level: 1, name: "Tenant 1" })).toBeTruthy();
    const header = screen.getByTestId("tenant-header");
    expect(within(header).getByRole("link", { name: "Unit 1, Property 1" }).getAttribute("href")).toBe("/units/u1");
    expect(within(header).getByRole("link", { name: "555-555-0100" }).getAttribute("href")).toBe("tel:5555550100");
    expect(within(header).getByRole("link", { name: "tenant1@example.com" }).getAttribute("href")).toBe(
      "mailto:tenant1@example.com",
    );
    expect(screen.getByTestId("held").textContent).toBe("Prepaid rent $25.00 · Deposit held $1,500.00");
    const contact = screen.getByRole("group", { name: "Contact details, Tenant 1" });
    expect(within(contact).getByRole("link", { name: "tenant1@example.com" }).getAttribute("href")).toBe(
      "mailto:tenant1@example.com",
    );
    expect(contact.textContent).toContain("555-555-0101");
    expect(contact.textContent).toContain("100 Example Street, Apt 2Sampletown, ZZ 00000");
    expect(contact.textContent).not.toContain("changed these in the portal");
    const portal = screen.getByRole("heading", { name: "Portal" }).closest("section") as HTMLElement;
    expect(portal.textContent).toContain("InvitedSigns in as tenant1.login@example.com");
    // No email box: the invite goes to the contact email, in one click.
    expect(within(portal).queryByLabelText("Email to sign in with")).toBeNull();
    expect(within(portal).getByRole("button", { name: "Send a new invite" })).toBeTruthy();
    expect(portal.textContent).toContain("Goes to tenant1@example.com, the email in their contact details.");
    fireEvent.click(screen.getByRole("tab", { name: "Leases" }));
    const rows = screen.getAllByRole("row", { name: "Property 1, Unit 1" });
    expect(rows.map((row) => within(row).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["Unit 1, Property 1", "01/01/2026 – Month to month", "Current", "$1,500.00", "$50.00"],
      ["Unit 1, Property 1", "01/01/2025 – 12/31/2025", "Ended", "$1,500.00", "$0.00"],
    ]);
    expect(within(rows[0]!).getByRole("link").getAttribute("href")).toBe("/leases/l1");
  });

  it("shows the tenant opened now, even when an earlier tenant's answer comes back last", async () => {
    server({ "/api/tenants/t1": TENANT, "/api/tenants/t2": { ...TENANT, id: "t2", name: "Tenant 2" }, ...LEASE_ANSWERS });
    const release = holdBack((path) => path === "/api/tenants/t1");
    const { rerender } = render(<TenantPage me={ME} id="t1" />);

    rerender(<TenantPage me={ME} id="t2" />);
    await screen.findByRole("heading", { level: 1, name: "Tenant 2" });
    await release();

    expect(screen.getByRole("heading", { level: 1, name: "Tenant 2" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 1, name: "Tenant 1" })).toBeNull();
  });

  it("says when the tenant changed their contact details themselves, in the portal", async () => {
    server({ "/api/tenants/t1": { ...TENANT, contact_by_tenant_at: "2026-10-20T15:00:00Z" }, ...LEASE_ANSWERS });
    render(<TenantPage me={ME} id="t1" />);

    const contact = await screen.findByRole("group", { name: "Contact details, Tenant 1" });
    expect(contact.textContent).toContain("Tenant 1 changed these in the portal on 10/20/2026.");
  });

  it("invites them to the portal from their page, naming the email first", async () => {
    const fetch = server({ "/api/tenants/t1": { ...TENANT, portal: "no login", login_email: null }, ...LEASE_ANSWERS });
    render(<TenantPage me={EMAIL_ON} id="t1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Invite to the portal" }));
    const ask = await screen.findByRole("alertdialog");
    expect(ask.textContent).toContain("Email Tenant 1 an invite to the portal at tenant1@example.com?");
    expect(posted(fetch)).toEqual([]);
    fireEvent.click(within(ask).getByRole("button", { name: "Send invite" }));
    await vi.waitFor(() => expect(posted(fetch)).toEqual([
      ["/api/invites", { kind: "tenant", person_id: "t1", email: TENANT.contact.email }],
    ]));
  });

  it("says an invite is only a link to give them when this server sends no email", async () => {
    const fetch = server({ "/api/tenants/t1": { ...TENANT, portal: "no login", login_email: null }, ...LEASE_ANSWERS });
    render(<TenantPage me={ME} id="t1" />);
    const invite = await screen.findByRole("button", { name: "Invite to the portal" });
    expect(screen.getByText(/This server sends no email, so you give them the link/)).toBeTruthy();
    fireEvent.click(invite);
    const ask = await screen.findByRole("alertdialog");
    expect(ask.textContent).toContain(
      "Make Tenant 1 an invite link for tenant1@example.com? Nothing is emailed: you give them the link shown here.",
    );
    fireEvent.click(within(ask).getByRole("button", { name: "Make the link" }));
    await vi.waitFor(() => expect(posted(fetch)).toEqual([
      ["/api/invites", { kind: "tenant", person_id: "t1", email: TENANT.contact.email }],
    ]));
  });

  it("edits their contact details whole, and says why a save or an archive was refused", async () => {
    const fetch = server(
      { "/api/tenants/t2": { ...TENANT, id: "t2", name: "Tenant 2", contact: NO_CONTACT, leases: [] } },
      {
        "/api/tenants/t2/archive": [409, "Tenant 2 has prepaid rent or a deposit held; return it before archiving"],
      },
    );
    render(<TenantPage me={ME} id="t2" />);
    expect((await screen.findByRole("group", { name: "Contact details, Tenant 2" })).textContent).toContain(
      "No contact details yet.",
    );
    fireEvent.click(screen.getByRole("tab", { name: "Leases" }));
    expect(screen.getByTestId("empty-state").textContent).toContain("No lease yet.");
    fireEvent.click(screen.getByRole("tab", { name: "Summary" }));
    const contact = screen.getByRole("group", { name: "Contact details, Tenant 2" });

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

    fireEvent.click(screen.getByRole("button", { name: "Actions for Tenant 2" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Archive" }));
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

describe("a tenant's lease, invite and archive on their page (ADR 0010 phase 3)", () => {
  it("shows the lease's rent, due day, term, deposit held, autopay and balance, with its actions", async () => {
    server({ "/api/tenants/t1": TENANT, ...LEASE_ANSWERS });
    render(<TenantPage me={ME} id="t1" />);

    const lease = (await screen.findByRole("heading", { name: "Lease" })).closest("section") as HTMLElement;
    expect((await within(lease).findByTestId("lease-rent")).textContent).toBe("$1,500.00 a month, due on the 5th");
    expect(within(lease).getByTestId("lease-ends").textContent).toBe("Month to month");
    expect(within(lease).getByTestId("lease-deposit").textContent).toBe("$1,500.00");
    expect((await within(lease).findByTestId("lease-autopay")).textContent).toBe("On: the balance due, up to $2,000.00");
    expect(within(lease).getByTestId("lease-balance").textContent).toBe("$50.00");
    expect(within(lease).getByRole("link", { name: "Unit 1, Property 1" }).getAttribute("href")).toBe("/leases/l1");
    expect(within(lease).getByRole("button", { name: "Renew lease" })).toBeTruthy();

    fireEvent.click(within(lease).getByRole("button", { name: "Receive payment" }));
    expect(await screen.findByRole("dialog", { name: "Receive payment" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(within(lease).getByRole("button", { name: "Change end date" }));
    const end = await screen.findByRole("dialog", { name: "Change end date" });
    expect(within(end).getByLabelText("Last day")).toBeTruthy();
    // Renew lease opens its panel right here (ADR 0010 phase 3, part L).
    fireEvent.click(within(end).getByRole("button", { name: "Close" }));
    await vi.waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(document.querySelector("body > [aria-hidden='true']")).toBeNull();
    });
    fireEvent.click(within(lease).getByRole("button", { name: "Renew lease" }));
    expect(await screen.findByRole("dialog", { name: "Renew lease" })).toBeTruthy();
    expect(window.location.pathname).toBe("/");
  });

  it("says so when they have no lease, and an ended lease takes no payment or renewal here", async () => {
    server({ "/api/tenants/t2": { ...TENANT, id: "t2", name: "Tenant 2", leases: [] } });
    render(<TenantPage me={ME} id="t2" />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No lease yet.");
    cleanup();

    server({ "/api/tenants/t1": { ...TENANT, leases: [{ ...LINE, status: "ended", ends_on: "2026-06-30" }] }, ...LEASE_ANSWERS });
    render(<TenantPage me={ME} id="t1" />);
    const lease = (await screen.findByRole("heading", { name: "Lease" })).closest("section") as HTMLElement;
    await within(lease).findByTestId("lease-rent");
    expect(within(lease).queryByRole("button", { name: "Receive payment" })).toBeNull();
    expect(within(lease).queryByRole("button", { name: "Change end date" })).toBeNull();
  });

  it("with no email on file, says so and opens their contact details to add one", async () => {
    server({ "/api/tenants/t1": { ...TENANT, contact: NO_CONTACT, portal: "no login", login_email: null }, ...LEASE_ANSWERS });
    render(<TenantPage me={ME} id="t1" />);

    const header = await screen.findByTestId("tenant-header");
    expect(header.textContent).toContain("No phone on file");
    expect(header.textContent).toContain("No email on file");
    const portal = screen.getByRole("heading", { name: "Portal" }).closest("section") as HTMLElement;
    expect(portal.textContent).toContain("No email on file, so no invite can go to Tenant 1.");
    expect(within(portal).queryByRole("button", { name: "Invite to the portal" })).toBeNull();
    fireEvent.click(within(portal).getByRole("button", { name: "Edit contact details" }));
    expect(await screen.findByLabelText("Street address")).toBeTruthy();
  });

  it("offers Archive only once they have moved out or never had a lease", async () => {
    server({ "/api/tenants/t1": TENANT, ...LEASE_ANSWERS });
    render(<TenantPage me={ME} id="t1" />);
    await screen.findByRole("heading", { level: 1, name: "Tenant 1" });
    expect(screen.queryByRole("button", { name: "Actions for Tenant 1" })).toBeNull();
    cleanup();

    server({ "/api/tenants/t1": { ...TENANT, leases: [{ ...LINE, status: "ended", ends_on: "2026-06-30" }] }, ...LEASE_ANSWERS });
    render(<TenantPage me={ME} id="t1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Tenant 1" }));
    expect(await screen.findByRole("menuitem", { name: "Archive" })).toBeTruthy();

    expect(archivable({ leases: [] })).toBe(true);
    expect(archivable({ leases: [{ ...LINE, status: "future" }] })).toBe(false);
  });

  it("shows the lease in effect, else the next to start, else the last; and autopay as it stands", () => {
    const ended = { ...LINE, id: "old", status: "ended" as const, starts_on: "2024-01-01" };
    const later = { ...LINE, id: "later", status: "ended" as const, starts_on: "2025-01-01" };
    const next = { ...LINE, id: "next", status: "future" as const, starts_on: "2027-01-01" };
    const soon = { ...LINE, id: "soon", status: "future" as const, starts_on: "2026-12-01" };
    expect(leaseShown([ended, LINE, next])?.id).toBe("l1");
    expect(leaseShown([ended, next, soon])?.id).toBe("soon");
    expect(leaseShown([ended, later])?.id).toBe("later");
    expect(leaseShown([])).toBeNull();
    expect(autopayWord([], "Tenant 1")).toBe("Not set up");
    expect(autopayWord([{ ...AUTOPAY[0]!, stopped_at: "2026-10-01T00:00:00Z" }] as never, "Tenant 1")).toBe("Not set up");
  });
});
