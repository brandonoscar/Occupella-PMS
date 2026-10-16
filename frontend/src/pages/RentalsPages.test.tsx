import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { RentLease } from "../rent";
import type { Paged, Property, PropertyRow, Unit } from "../rentals";
import { LeasePage, PropertiesPage, PropertyPage, UnitPage } from "./RentalsPages";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState(null, "", "/");
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const ROWS: Paged<PropertyRow> = {
  items: [
    { id: "p1", name: "Property 1", owners: ["Owner 1", "Owner 2"], units: 5, occupied: 4, archived: false },
    { id: "p2", name: "Property 2", owners: ["Owner 3"], units: 2, occupied: 0, archived: false },
    { id: "p3", name: "Property 3", owners: [], units: 0, occupied: 0, archived: false },
  ],
  total: 3,
  page: 1,
  per_page: 50,
};

const LINE = {
  id: "l1", tenants: ["Tenant 1", "Tenant 2"], starts_on: "2026-01-01", ends_on: null,
  monthly_rent: "1500.00", owed: "50.00", status: "current" as const,
};

const PROPERTY: Property = {
  id: "p1",
  name: "Property 1",
  archived: false,
  owners: [{ owner_id: "o1", owner: "Owner 1", account: "a1", bank_account: "Operating 1", balance: "2400.00" }],
  units: [
    { id: "u1", name: "Unit 1", archived: false, lease: LINE },
    { id: "u2", name: "Unit 2", archived: false, lease: { ...LINE, id: "l2", status: "future", starts_on: "2026-11-01", owed: "0.00" } },
    { id: "u3", name: "Unit 3", archived: false, lease: null },
    { id: "u4", name: "Unit 4", archived: true, lease: null },
  ],
};

const UNIT: Unit = {
  id: "u1",
  name: "Unit 1",
  archived: false,
  property: { id: "p1", name: "Property 1" },
  leases: [LINE, { ...LINE, id: "l0", tenants: ["Tenant 0"], starts_on: "2025-01-01", ends_on: "2025-12-31", owed: "0.00", status: "ended" }],
};

const LEASE: RentLease & { property_id: string; unit_id: string } = {
  id: "l1", property: "Property 1", unit: "Unit 1", property_id: "p1", unit_id: "u1",
  starts_on: "2026-01-01", ends_on: null, monthly_rent: "1500.00", rent_changes: [], renters_insurance: null,
  tenants: [{ id: "t1", name: "Tenant 1", prepaid: "0.00", deposit: "1500.00" }],
  owed: "50.00", on_hold: false, open_charges: [], payments: [], recurring: [],
};

// The API: `answers[path]` on GET (the paths asked are kept); a POST answers 204, or 409 with
// `refuse[path]`.
function server(answers: Record<string, unknown>, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
      return new Response(null, { status: 204 });
    }
    if (!(path in answers)) return new Response(JSON.stringify({ detail: "no such page" }), { status: 404 });
    return new Response(JSON.stringify(answers[path]), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const asked = (fetch: ReturnType<typeof server>) => fetch.mock.calls.map(([path, init]) =>
  init?.method === "POST" ? [path, JSON.parse(String(init.body))] : path);

describe("PropertiesPage", () => {
  it("lists the properties a page at a time, each with its owners and how full it is", async () => {
    const fetch = server({
      "/api/properties?page=1&archived=false": { ...ROWS, total: 60 },
      "/api/properties?page=2&archived=false": { ...ROWS, page: 2, total: 60 },
      "/api/properties?page=1&archived=false&q=Property+2": { ...ROWS, items: [ROWS.items[1]], total: 1 },
      "/api/properties?page=1&archived=true&q=Property+2": { ...ROWS, items: [], total: 0 },
    });
    render(<PropertiesPage me={ME} />);

    const first = await screen.findByRole("row", { name: "Property 1" });
    expect(within(first).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Property 1", "Owner 1, Owner 2", "4 of 5 occupied",
    ]);
    expect(within(first).getByRole("link", { name: "Property 1" }).getAttribute("href")).toBe("/properties/p1");
    expect(within(screen.getByRole("row", { name: "Property 2" })).getAllByRole("cell")[2]?.textContent).toBe("Vacant");
    expect(within(screen.getByRole("row", { name: "Property 3" })).getAllByRole("cell")[2]?.textContent).toBe("No units");
    expect(screen.getByRole("link", { name: "Add property" }).getAttribute("href")).toBe("/settings/setup");

    const pages = screen.getByRole("navigation", { name: "Pages" });
    expect(pages.textContent).toContain("1–3 of 60");
    expect((within(pages).getByRole("button", { name: "Previous" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(pages).getByRole("button", { name: "Next" }));
    expect(await screen.findByText("51–53 of 60")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Find a property"), { target: { value: "Property 2" } });
    await vi.waitFor(() => expect(screen.queryByRole("row", { name: "Property 1" })).toBeNull());
    expect(screen.getByRole("row", { name: "Property 2" })).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Archived only"));
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No archived properties.");
    expect(asked(fetch)).toEqual([
      "/api/properties?page=1&archived=false",
      "/api/properties?page=2&archived=false",
      "/api/properties?page=1&archived=false&q=Property+2",
      "/api/properties?page=1&archived=true&q=Property+2",
    ]);
  });

  it("says when the properties can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<PropertiesPage me={ME} />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });
});

describe("PropertyPage", () => {
  it("shows what its owners hold and each unit's state, tenants and lease", async () => {
    server({ "/api/properties/p1": PROPERTY });
    render(<PropertyPage me={ME} id="p1" />);

    expect(await screen.findByRole("heading", { name: "Property 1" })).toBeTruthy();
    const owners = screen.getAllByRole("table")[0]!;
    expect(within(owners).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Owner 1", "Operating 1", "$2,400.00",
    ]);
    const units = ["Unit 1", "Unit 2", "Unit 3", "Unit 4"].map((name) => screen.getByRole("row", { name }));
    expect(units.map((row) => within(row).getAllByRole("cell")[1]?.textContent)).toEqual([
      "Occupied", "Starts 11/01/2026", "Vacant", "Archived",
    ]);
    expect(within(units[0]!).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Unit 1", "Occupied", "Tenant 1, Tenant 2", "$1,500.00", "$50.00", "Open lease",
    ]);
    expect(within(units[0]!).getByRole("link", { name: "Unit 1" }).getAttribute("href")).toBe("/units/u1");
    expect(within(units[0]!).getByRole("link", { name: "Open lease" }).getAttribute("href")).toBe("/leases/l1");
    expect(within(units[2]!).queryByRole("link", { name: "Open lease" })).toBeNull();
  });

  it("renames it, and archives it only when asked twice, saying why one is refused", async () => {
    const fetch = server(
      { "/api/properties/p1": PROPERTY },
      { "/api/properties/p1/archive": "Property 1 has a lease in effect or to come; end it before archiving" },
    );
    render(<PropertyPage me={ME} id="p1" />);
    await screen.findByRole("heading", { name: "Property 1" });

    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    fireEvent.change(screen.getByLabelText("New name"), { target: { value: " Property 1A " } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    expect(await screen.findByText("Renamed Property 1A.")).toBeTruthy();
    expect(screen.queryByLabelText("New name")).toBeNull();

    // Archive asks first; Cancel sends nothing.
    fireEvent.click(screen.getByRole("button", { name: "Archive" }));
    expect(screen.getByText("Property 1 leaves the lists; its history stays.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Archive" }));
    fireEvent.click(screen.getByRole("button", { name: "Archive Property 1" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Property 1 has a lease in effect or to come; end it before archiving",
    );

    expect(asked(fetch).filter((call) => Array.isArray(call))).toEqual([
      ["/api/properties/p1/name", { name: " Property 1A " }],
      ["/api/properties/p1/archive", { archived: true }],
    ]);
  });

  it("brings an archived one back, and says when it has no owner's account or unit", async () => {
    const fetch = server({ "/api/properties/p3": { ...PROPERTY, id: "p3", name: "Property 3", archived: true, owners: [], units: [] } });
    render(<PropertyPage me={ME} id="p3" />);
    await screen.findByRole("heading", { name: "Property 3" });

    expect(screen.getAllByTestId("empty-state").map((e) => e.textContent)).toEqual([
      "No owner's account yet.Open one from Settings, Company setup.",
      "No units yet.Add units from Settings, Company setup.",
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Bring back" }));
    expect(screen.getByText("Property 3 comes back into lists.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Bring back Property 3" }));
    expect(await screen.findByText("Property 3 is back.")).toBeTruthy();
    expect(asked(fetch)).toContainEqual(["/api/properties/p3/archive", { archived: false }]);
  });

  it("says when there is no such property", async () => {
    server({});
    render(<PropertyPage me={ME} id="nope" />);
    expect((await screen.findByRole("alert")).textContent).toBe("no such page");
  });
});

describe("UnitPage", () => {
  it("shows its property and every lease it has had, newest first", async () => {
    server({ "/api/units/u1": UNIT });
    render(<UnitPage me={ME} id="u1" />);

    expect(await screen.findByRole("heading", { name: "Unit 1, Property 1" })).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "Property 1" }).at(-1)?.getAttribute("href")).toBe("/properties/p1");
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["Tenant 1, Tenant 2", "From 01/01/2026, month to month", "Current", "$1,500.00", "$50.00"],
      ["Tenant 0", "01/01/2025 to 12/31/2025", "Ended", "$1,500.00", "$0.00"],
    ]);
    expect(within(rows[0]!).getByRole("link").getAttribute("href")).toBe("/leases/l1");
  });

  it("archives a unit with nothing on it, and says when it has no lease yet", async () => {
    const fetch = server({ "/api/units/u3": { ...UNIT, id: "u3", name: "Unit 3", leases: [] } });
    render(<UnitPage me={ME} id="u3" />);
    await screen.findByRole("heading", { name: "Unit 3, Property 1" });

    expect(screen.getByTestId("empty-state").textContent).toContain("No lease yet.");
    fireEvent.click(screen.getByRole("button", { name: "Archive" }));
    fireEvent.click(screen.getByRole("button", { name: "Archive Unit 3" }));
    expect(await screen.findByText("Unit 3 is archived.")).toBeTruthy();
    expect(asked(fetch)).toContainEqual(["/api/units/u3/archive", { archived: true }]);
  });

  it("says when there is no such unit", async () => {
    server({});
    render(<UnitPage me={ME} id="nope" />);
    expect((await screen.findByRole("alert")).textContent).toBe("no such page");
  });
});

describe("LeasePage", () => {
  it("shows the lease with its property and unit, and its rent work", async () => {
    server({ "/api/leases/l1": LEASE });
    render(<LeasePage me={ME} id="l1" />);

    expect(await screen.findByRole("heading", { name: "Unit 1, Property 1" })).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "Property 1" }).at(-1)?.getAttribute("href")).toBe("/properties/p1");
    expect(screen.getByRole("link", { name: "Unit 1" }).getAttribute("href")).toBe("/units/u1");
    expect(screen.getByText("From 01/01/2026, month to month")).toBeTruthy();
    expect(screen.getByText("$1,500.00 a month")).toBeTruthy();
    expect(screen.getAllByText("Owes $50.00")).toHaveLength(1);
    // The lease's own rent work, and no move-out while it has no end.
    expect(screen.getByRole("listitem", { name: "Property 1, Unit 1" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Move-out" })).toBeNull();
  });

  it("links an ending lease to its move-out", async () => {
    server({ "/api/leases/l1": { ...LEASE, ends_on: "2026-09-30" } });
    render(<LeasePage me={ME} id="l1" />);
    expect((await screen.findByRole("link", { name: "Move-out" })).getAttribute("href")).toBe("/move-out");
  });

  it("says when there is no such lease", async () => {
    server({});
    render(<LeasePage me={ME} id="nope" />);
    expect((await screen.findByRole("alert")).textContent).toBe("no such page");
  });
});
