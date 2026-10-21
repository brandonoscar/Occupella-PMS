import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { holdBack } from "../holdBack";
import type { Paged, Property, PropertyRow, Unit } from "../rentals";
import { PropertiesPage, PropertyPage, UnitPage, tenantsEmailed } from "./RentalsPages";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState(null, "", "/");
  window.localStorage.clear();
});

const GROUPS = {
  groups: [{ id: "g1", name: "North", properties: [{ id: "p2", name: "Property 2" }] }],
  properties: [{ id: "p1", name: "Property 1" }, { id: "p2", name: "Property 2" }],
};

// Opens a row's ⋯ menu, picks the action and confirms it.
function choose(menu: string, action: string) {
  fireEvent.click(screen.getByRole("button", { name: `Actions for ${menu}` }));
  fireEvent.click(screen.getByRole("menuitem", { name: action }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: action }));
}

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
  id: "l1", tenants: ["Tenant 1", "Tenant 2"], people: [{ id: "t1", name: "Tenant 1" }, { id: "t2", name: "Tenant 2" }],
  starts_on: "2026-01-01", ends_on: null,
  monthly_rent: "1500.00", owed: "50.00", status: "current" as const,
};

const PROPERTY: Property = {
  id: "p1",
  name: "Property 1",
  archived: false,
  owners: [{
    owner_id: "o1", owner: "Owner 1", account: "a1", bank_account: "Operating 1", balance: "2400.00",
    agreement: {
      starts_on: "2026-10-01", fee_percent: "8", minimum_fee: "0", flat_fee: "0", leasing_fee_percent: "0",
      reserve: "250.00", approval_limit: "500.00", bill_markup_percent: "0",
    },
    percent: null,
  }],
  ownership: [],
  units: [
    { id: "u1", name: "Unit 1", archived: false, lease: LINE },
    { id: "u2", name: "Unit 2", archived: false, lease: { ...LINE, id: "l2", status: "future", starts_on: "2026-11-01", owed: "0.00" } },
    { id: "u3", name: "Unit 3", archived: false, lease: null },
    { id: "u4", name: "Unit 4", archived: true, lease: null },
  ],
  late_fees: { starts_on: "2026-10-01", grace_days: 5, flat_fee: "50.00", percent: "0", maximum: null },
  nsf_fee: null,
  lease_break_fee: null,
};

const UNIT: Unit = {
  id: "u1",
  name: "Unit 1",
  archived: false,
  property: { id: "p1", name: "Property 1" },
  leases: [LINE, { ...LINE, id: "l0", tenants: ["Tenant 0"], people: [{ id: "t0", name: "Tenant 0" }], starts_on: "2025-01-01", ends_on: "2025-12-31", owed: "0.00", status: "ended" }],
};


const NO_FILES = {
  documents: [],
  subjects: {
    w9: [], receipt: [], move_out_photo: [], owner_document: [], work_order_photo: [], property_document: [],
    unit_document: [], tenant_document: [], lease_document: [], vendor_document: [], request_photo: [],
  },
};

// The API: `answers[path]` on GET (the paths asked are kept); a POST answers 204, or 409 with
// `refuse[path]`.
function server(answers: Record<string, unknown>, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
      return new Response(null, { status: 204 });
    }
    // Every record page's Files card reads the library; this one is empty.
    if (path === "/api/documents" && !(path in answers)) return new Response(JSON.stringify(NO_FILES), { status: 200 });
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
      "/api/properties?page=1&archived=false&group=g1": { ...ROWS, items: [ROWS.items[1]], total: 1 },
      "/api/properties?page=1&archived=true&group=g1": { ...ROWS, items: [], total: 0 },
      "/api/property-groups": GROUPS,
    });
    render(<PropertiesPage me={ME} />);

    const first = await screen.findByRole("row", { name: "Property 1" });
    expect(within(first).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Property 1", "Owner 1, Owner 2", "4 of 5 occupied",
    ]);
    expect(within(first).getByRole("link", { name: "Property 1" }).getAttribute("href")).toBe("/properties/p1");
    expect(within(screen.getByRole("row", { name: "Property 2" })).getAllByRole("cell")[2]?.textContent).toBe("Vacant");
    expect(within(screen.getByRole("row", { name: "Property 3" })).getAllByRole("cell")[2]?.textContent).toBe("No units");
    fireEvent.click(screen.getByRole("button", { name: "Add property" }));
    expect(screen.getByRole("dialog", { name: "Add property" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    const pages = screen.getByRole("navigation", { name: "Pages" });
    expect(pages.textContent).toContain("1–3 of 60");
    expect((within(pages).getByRole("button", { name: "Previous" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(pages).getByRole("button", { name: "Next" }));
    expect(await screen.findByText("51–53 of 60")).toBeTruthy();

    // The picker narrows to a group (groups first, then properties), kept in the URL.
    const picker = screen.getByRole("combobox", { name: "Properties shown" });
    await within(picker).findByRole("option", { name: "North" });
    expect(within(picker).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "All properties", "North", "Property 1", "Property 2",
    ]);
    fireEvent.change(picker, { target: { value: "group:g1" } });
    await vi.waitFor(() => expect(screen.queryByRole("row", { name: "Property 1" })).toBeNull());
    expect(screen.getByRole("row", { name: "Property 2" })).toBeTruthy();
    expect(window.location.search).toBe("?group=g1");
    fireEvent.change(screen.getByRole("combobox", { name: "Status" }), { target: { value: "archived" } });
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No archived properties.");
    expect(asked(fetch).filter((path) => path !== "/api/property-groups" && path !== "/api/setup")).toEqual([
      "/api/properties?page=1&archived=false",
      "/api/properties?page=2&archived=false",
      "/api/properties?page=1&archived=false&group=g1",
      "/api/properties?page=1&archived=true&group=g1",
    ]);
  });

  it("opens on the group last chosen in this browser", async () => {
    window.localStorage.setItem("pms.scope", JSON.stringify({ group: "g1" }));
    const fetch = server({
      "/api/properties?page=1&archived=false&group=g1": { ...ROWS, items: [ROWS.items[1]], total: 1 },
      "/api/property-groups": GROUPS,
    });
    render(<PropertiesPage me={ME} />);
    expect(await screen.findByRole("row", { name: "Property 2" })).toBeTruthy();
    expect(window.location.search).toBe("?group=g1");
    expect(asked(fetch)).toContain("/api/properties?page=1&archived=false&group=g1");
  });

  it("shows the properties of the one picked now, even when an earlier pick's answer comes back last", async () => {
    server({
      "/api/properties?page=1&archived=false": ROWS,
      "/api/properties?page=1&archived=false&property=p1": { ...ROWS, items: [ROWS.items[0]], total: 1 },
      "/api/properties?page=1&archived=false&property=p2": { ...ROWS, items: [{ ...ROWS.items[1]!, owners: ["Owner 9"] }], total: 1 },
      "/api/property-groups": GROUPS,
    });
    const release = holdBack((path) => path.includes("property=p1"));
    render(<PropertiesPage me={ME} />);
    await screen.findByRole("row", { name: "Property 1" });
    const picker = screen.getByRole("combobox", { name: "Properties shown" });
    await within(picker).findByRole("option", { name: "Property 2" });

    fireEvent.change(picker, { target: { value: "property:p1" } });
    fireEvent.change(picker, { target: { value: "property:p2" } });
    await screen.findByText("Owner 9");
    await release();

    expect(screen.getByRole("row", { name: "Property 2" })).toBeTruthy();
    expect(screen.queryByRole("row", { name: "Property 1" })).toBeNull();
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
    const owners = screen.getByRole("table", { name: "Owners" });
    expect(within(owners).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Owner 1", "Operating 1",
      "8% of rent and recurring fees collected; keeps $250.00 in reserve; owner approves bills over $500.00 (from 10/01/2026)",
      "$2,400.00", "Set terms",
    ]);
    expect(within(owners).getByRole("link", { name: "Owner 1" }).getAttribute("href")).toBe("/owners/o1");
    expect(screen.getByTestId("late-fees").textContent).toBe("After 5 days' grace: $50.00 (from 10/01/2026)");
    expect(screen.getByTestId("nsf-fee").textContent).toBe("No NSF fee");
    expect(screen.getByTestId("lease-break-fee").textContent).toBe("No lease-break fee");
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

  it("shows the property opened now, even when an earlier property's answer comes back last", async () => {
    server({ "/api/properties/p1": PROPERTY, "/api/properties/p2": { ...PROPERTY, id: "p2", name: "Property 2" } });
    const release = holdBack((path) => path === "/api/properties/p1");
    const { rerender } = render(<PropertyPage me={ME} id="p1" />);

    rerender(<PropertyPage me={ME} id="p2" />);
    await screen.findByRole("heading", { level: 1, name: "Property 2" });
    await release();

    expect(screen.getByRole("heading", { level: 1, name: "Property 2" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 1, name: "Property 1" })).toBeNull();
  });

  it("renames it, and archives it only when asked twice, saying why one is refused", async () => {
    const fetch = server(
      { "/api/properties/p1": PROPERTY },
      { "/api/properties/p1/archive": "Property 1 has a lease in effect or to come; end it before archiving" },
    );
    render(<PropertyPage me={ME} id="p1" />);
    await screen.findByRole("heading", { name: "Property 1" });

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: " Property 1A " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Renamed Property 1A.")).toBeTruthy();
    expect(screen.queryByLabelText("Name")).toBeNull();

    // Archive is in the ⋯ menu and asks first; Cancel sends nothing.
    fireEvent.click(screen.getByRole("button", { name: "Actions for Property 1" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    expect(screen.getByRole("alertdialog", { name: "Archive Property 1? It leaves the lists; its history stays." })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    choose("Property 1", "Archive");
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
      "No owner's account yet.",
      "No units yet.",
    ]);
    choose("Property 3", "Bring back");
    expect(await screen.findByText("Property 3 is back.")).toBeTruthy();
    expect(asked(fetch)).toContainEqual(["/api/properties/p3/archive", { archived: false }]);
  });

  it("sets up its units, owners, terms and fees in side panels, saying what each did and reading it again", async () => {
    const fetch = server({ "/api/properties/p1": PROPERTY });
    render(<PropertyPage me={ME} id="p1" />);
    await screen.findByRole("heading", { name: "Property 1" });

    for (const [button, title] of [
      ["Add unit", "Add unit"], ["Add owner", "Add owner"], ["Set late fees", "Set late fees"], ["Set NSF fee", "Set NSF fee"],
    ] as const) {
      fireEvent.click(screen.getByRole("button", { name: button }));
      expect(screen.getByRole("dialog", { name: title })).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: "Close" }));
    }
    fireEvent.click(within(screen.getByRole("row", { name: "Owner 1" })).getByRole("button", { name: "Set terms" }));
    const terms = screen.getByRole("dialog", { name: "Set management terms" });
    expect(within(within(terms).getByLabelText("For")).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Owner 1, Property 1",
    ]);
    fireEvent.click(within(terms).getByRole("button", { name: "Close" }));

    fireEvent.click(screen.getByRole("button", { name: "Add unit" }));
    fireEvent.change(screen.getByLabelText("Unit name"), { target: { value: "Unit 5" } });
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add unit" }));
    expect(await screen.findByText("Added Unit 5.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(asked(fetch)).toContainEqual(["/api/setup/units", { property_id: "p1", name: "Unit 5" }]);
    expect(asked(fetch).filter((path) => path === "/api/properties/p1")).toHaveLength(2);
  });

  it("shows who owns what part of it from each day, and sets new shares in a side panel", async () => {
    const owner = PROPERTY.owners[0]!;
    const shares = (one: string, two: string) => [
      { account: "a1", owner: "Owner 1", percent: one }, { account: "a2", owner: "Owner 2", percent: two },
    ];
    const coOwned: Property = {
      ...PROPERTY,
      owners: [{ ...owner, percent: "60" }, { ...owner, owner_id: "o2", owner: "Owner 2", account: "a2", percent: "40" }],
      ownership: [
        { id: "s3", starts_on: "2999-01-01", recorded_by: "Staff 1", shares: shares("50", "50") },
        { id: "s2", starts_on: "2026-01-01", recorded_by: null, shares: shares("60", "40") },
        { id: "s1", starts_on: "2025-01-01", recorded_by: "Staff 1", shares: shares("100", "0").slice(0, 1) },
      ],
    };
    const fetch = server({ "/api/properties/p1": coOwned },
      { "/api/setup/properties/p1/ownership": "Owner 2 is not an owner's account at the property" });
    render(<PropertyPage me={ME} id="p1" />);
    await screen.findByRole("heading", { name: "Property 1" });

    const table = screen.getByRole("table", { name: "Ownership" });
    expect(within(table).getAllByRole("row").slice(1).map((r) => r.textContent)).toEqual([
      "01/01/2999Owner 1 50% · Owner 2 50%Staff 1To come",
      "01/01/2026Owner 1 60% · Owner 2 40%—In force",
      "01/01/2025Owner 1 100%Staff 1Replaced",
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Set ownership" }));
    const panel = screen.getByRole("dialog", { name: "Set ownership" });
    // It starts from the shares in force today.
    expect([within(panel).getByLabelText("Owner 1, percent"), within(panel).getByLabelText("Owner 2, percent")]
      .map((f) => (f as HTMLInputElement).value)).toEqual(["60", "40"]);
    expect(within(panel).getByTestId("ownership-total").textContent).toBe("Together 100%.");
    fireEvent.change(within(panel).getByLabelText("Owner 1, percent"), { target: { value: "70.5" } });
    expect(within(panel).getByTestId("ownership-total").textContent).toBe("Together 110.5%; the shares come to 100%.");
    expect((within(panel).getByRole("button", { name: "Set ownership" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(panel).getByLabelText("Owner 2, percent"), { target: { value: "29,5" } });
    expect(within(panel).getByTestId("ownership-total").textContent)
      .toBe("A percent is above 0, at most 100, to four decimals.");
    fireEvent.change(within(panel).getByLabelText("Owner 2, percent"), { target: { value: "29.5" } });
    fireEvent.change(within(panel).getByLabelText("In force from"), { target: { value: "2027-01-01" } });

    // A refusal stays in the panel, in words.
    fireEvent.click(within(panel).getByRole("button", { name: "Set ownership" }));
    expect((await within(panel).findByRole("alert")).textContent).toBe("Owner 2 is not an owner's account at the property");
    expect(asked(fetch)).toContainEqual(["/api/setup/properties/p1/ownership", {
      starts_on: "2027-01-01", shares: [{ account: "a1", percent: "70.5" }, { account: "a2", percent: "29.5" }],
    }]);

    // A blank leaves an owner out.
    server({ "/api/properties/p1": coOwned });
    fireEvent.change(within(panel).getByLabelText("Owner 1, percent"), { target: { value: "100" } });
    fireEvent.change(within(panel).getByLabelText("Owner 2, percent"), { target: { value: "" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Set ownership" }));
    expect(await screen.findByText("Ownership of Property 1 from 01/01/2027: Owner 1 100%.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says a property owned by one owner, or by several without shares, has none", async () => {
    server({ "/api/properties/p1": PROPERTY });
    render(<PropertyPage me={ME} id="p1" />);
    await screen.findByRole("heading", { name: "Property 1" });
    expect(screen.getByText("No ownership shares.")).toBeTruthy();
    expect(screen.getByText("Set each owner's percent when another owner shares the property.")).toBeTruthy();
    cleanup();

    const owner = PROPERTY.owners[0]!;
    server({ "/api/properties/p1": { ...PROPERTY, owners: [owner, { ...owner, owner_id: "o2", owner: "Owner 2", account: "a2" }] } });
    render(<PropertyPage me={ME} id="p1" />);
    await screen.findByRole("heading", { name: "Property 1" });
    expect(screen.getByText(/Staff choose which owner's account each payment goes to/)).toBeTruthy();
  });

  it("emails every tenant living there in a side panel and says who has no email", async () => {
    const fetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        return body.subject === "Refused"
          ? new Response(JSON.stringify({ detail: "no tenant lives there today" }), { status: 422 })
          : new Response(JSON.stringify({
            emailed: [{ name: "Tenant A", to: "a@example.com" }, { name: "Tenant C", to: "c@example.com" }],
            without_email: ["Tenant B"],
          }), { status: 201 });
      }
      if (path === "/api/properties/p1") return new Response(JSON.stringify(PROPERTY), { status: 200 });
      return new Response(JSON.stringify(NO_FILES), { status: 200 });
    });
    vi.stubGlobal("fetch", fetch);
    render(<PropertyPage me={ME} id="p1" />);
    await screen.findByRole("heading", { name: "Property 1" });

    fireEvent.click(screen.getByRole("button", { name: "Email tenants" }));
    let panel = screen.getByRole("dialog", { name: "Email tenants of Property 1" });
    expect(within(panel).getByText(/Everyone on a lease in effect today/)).toBeTruthy();
    fireEvent.change(within(panel).getByLabelText("Subject"), { target: { value: "Refused" } });
    fireEvent.change(within(panel).getByLabelText("Message"), { target: { value: "x" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Send email" }));
    expect((await within(panel).findByRole("alert")).textContent).toBe("no tenant lives there today");

    fireEvent.change(within(panel).getByLabelText("Subject"), { target: { value: "Water shutoff" } });
    fireEvent.change(within(panel).getByLabelText("Message"), { target: { value: "Off Tuesday 9 to 11." } });
    fireEvent.click(within(panel).getByRole("button", { name: "Send email" }));
    expect(await screen.findByText("Emailed 2 tenants. No email on file: Tenant B.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    const posts = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts.at(-1)?.[0]).toBe("/api/emails/property");
    expect(JSON.parse(String(posts.at(-1)?.[1]?.body))).toEqual({
      property_id: "p1", subject: "Water shutoff", body: "Off Tuesday 9 to 11.",
    });
    panel = screen.queryByRole("dialog") as HTMLElement;
    expect(panel).toBeNull();
  });

  it("words what emailing the tenants did", () => {
    expect(tenantsEmailed({ emailed: [{ name: "A", to: "a@example.com" }], without_email: [] })).toBe("Emailed 1 tenant.");
    expect(tenantsEmailed({ emailed: [], without_email: ["A", "B"] })).toBe("Emailed 0 tenants. No email on file: A, B.");
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
      ["Tenant 1, Tenant 2", "01/01/2026 – Month to month", "Current", "$1,500.00", "$50.00"],
      ["Tenant 0", "01/01/2025 – 12/31/2025", "Ended", "$1,500.00", "$0.00"],
    ]);
    // Each tenant opens their own page; the term opens the lease.
    expect(within(rows[0]!).getAllByRole("link").map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["Tenant 1", "/tenants/t1"],
      ["Tenant 2", "/tenants/t2"],
      ["01/01/2026 – Month to month", "/leases/l1"],
    ]);
  });

  it("shows the unit opened now, even when an earlier unit's answer comes back last", async () => {
    server({ "/api/units/u1": UNIT, "/api/units/u2": { ...UNIT, id: "u2", name: "Unit 2" } });
    const release = holdBack((path) => path === "/api/units/u1");
    const { rerender } = render(<UnitPage me={ME} id="u1" />);

    rerender(<UnitPage me={ME} id="u2" />);
    await screen.findByRole("heading", { level: 1, name: "Unit 2, Property 1" });
    await release();

    expect(screen.getByRole("heading", { level: 1, name: "Unit 2, Property 1" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 1, name: "Unit 1, Property 1" })).toBeNull();
  });

  it("archives a unit with nothing on it, and says when it has no lease yet", async () => {
    const fetch = server({ "/api/units/u3": { ...UNIT, id: "u3", name: "Unit 3", leases: [] } });
    render(<UnitPage me={ME} id="u3" />);
    await screen.findByRole("heading", { name: "Unit 3, Property 1" });

    expect(screen.getByTestId("empty-state").textContent).toContain("No lease yet.");
    // A lease already in place is added here, for the property's tenants.
    fireEvent.click(screen.getByRole("button", { name: "Add lease in place" }));
    expect(screen.getByRole("dialog", { name: "Add lease" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    choose("Unit 3", "Archive");
    expect(await screen.findByText("Unit 3 is archived.")).toBeTruthy();
    expect(asked(fetch)).toContainEqual(["/api/units/u3/archive", { archived: true }]);
  });

  it("says when there is no such unit", async () => {
    server({});
    render(<UnitPage me={ME} id="nope" />);
    expect((await screen.findByRole("alert")).textContent).toBe("no such page");
  });
});

