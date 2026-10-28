import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { holdBack } from "../holdBack";
import type { RentRunLine } from "../rent";
import type { LeaseDetail, Paged, RentRollRow } from "../rentals";
import { RentPage } from "./RentPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState(null, "", "/");
  window.localStorage.clear();
});

const ME: Me = {
  kind: "staff",
  email: "staff@example.com",
  name: "Staff 1",
  person_id: "s1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
  roles: ["approve"],
};
const ADMIN: Me = { ...ME, roles: ["admin", "approve", "disburse"] };

const ROW: RentRollRow = {
  id: "l1",
  property: { id: "p1", name: "Property 1" },
  unit: { id: "u1", name: "Unit 1" },
  tenants: ["Tenant 1", "Tenant 2"],
  people: [{ id: "t1", name: "Tenant 1" }, { id: "t2", name: "Tenant 2" }],
  starts_on: "2026-01-01",
  ends_on: "2026-12-31",
  status: "current",
  days_left: 76,
  monthly_rent: "1500.00",
  deposit: "1500.00",
  owed: "1550.00",
  details: { bedrooms: 2, bathrooms: "1", square_feet: 900, market_rent: "1575.00" },
};
const ROLL: Paged<RentRollRow> = { items: [ROW], total: 1, page: 1, per_page: 50 };
const LEASE = {
  id: "l1",
  property: "Property 1",
  unit: "Unit 1",
  tenants: [{ id: "t1", name: "Tenant 1", prepaid: "0.00", deposit: "1500.00", deposits: [{ kind: "security", amount: "1500.00" }] }],
  owners: [{ account: "a1", owner: "Owner 1", percent: null }],
  owed: "1550.00",
  open_charges: [],
  moved_from: null,
} as unknown as LeaseDetail;
const RUNS: RentRunLine[] = [
  { at: "2026-10-16T06:00:00Z", due_on: "2026-10-01", late_fees_as_of: "2026-10-16", rents: 6, recurring: 1, late_fees: 0, by: null },
];

// The API: GETs answer `answers[path]` (the rent roll by default); each POST answers with
// `answers[path]` (or 201 {}), or 409 for a path in `refuse`.
function server(answers: Record<string, unknown> = {}, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      return new Response(JSON.stringify(answers[path] ?? ROLL), { status: 200 });
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

describe("RentPage", () => {
  async function roll() {
    return screen.findByRole("row", { name: "Property 1, Unit 1" });
  }

  it("lists the leases a page at a time with their days left, rent, deposit and balance", async () => {
    const fetch = server({
      "/api/leases?page=1&show=current": { ...ROLL, total: 51 },
      "/api/property-groups": { groups: [], properties: [{ id: "p1", name: "Property 1" }] },
    });
    render(<RentPage me={ME} />);

    const row = await roll();
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Property 1", "Unit 12 beds, 1 bath, 900 sq ft", "Tenant 1, Tenant 2", "01/01/2026", "12/31/2026", "76",
      "$1,500.00", "$1,575.00", "$1,500.00", "$1,550.00",
    ]);
    // The unit's size under its name, and the market rent beside the lease's rent (migration 0124).
    expect(within(row).getByTestId("unit-size").textContent).toBe("2 beds, 1 bath, 900 sq ft");
    expect(within(row).getByRole("link", { name: "Unit 1" }).getAttribute("href")).toBe("/leases/l1");
    expect(within(row).getByTestId("balance").className).toContain("text-danger");
    const pages = screen.getByRole("navigation", { name: "Pages" });
    expect(pages.textContent).toContain("1–1 of 51");

    // The next page, one property and each status ask again, from the first page.
    fireEvent.click(within(pages).getByRole("button", { name: "Next" }));
    await vi.waitFor(() => expect(fetch.mock.calls.at(-1)?.[0]).toBe("/api/leases?page=2&show=current"));
    const picker = screen.getByRole("combobox", { name: "Properties shown" });
    await within(picker).findByRole("option", { name: "Property 1" });
    fireEvent.change(picker, { target: { value: "property:p1" } });
    await vi.waitFor(() =>
      expect(fetch.mock.calls.at(-1)?.[0]).toBe("/api/leases?page=1&show=current&property=p1"),
    );
    for (const [label, show] of [["Future", "future"], ["Past", "past"], ["Owing", "owing"]]) {
      fireEvent.change(screen.getByLabelText("Status"), {
        target: { value: within(screen.getByLabelText("Status")).getByRole("option", { name: label }).getAttribute("value") },
      });
      await vi.waitFor(() =>
        expect(fetch.mock.calls.at(-1)?.[0]).toBe(`/api/leases?page=1&show=${show}&property=p1`),
      );
    }
  });

  it("shows the leases of the status picked now, even when an earlier status's answer comes back last", async () => {
    server({
      "/api/leases?page=1&show=future": { ...ROLL, items: [{ ...ROW, id: "l2", property: { id: "p2", name: "Property 2" }, unit: { id: "u2", name: "Unit 2" } }] },
      "/api/leases?page=1&show=past": { ...ROLL, items: [{ ...ROW, id: "l3", property: { id: "p3", name: "Property 3" }, unit: { id: "u3", name: "Unit 3" } }] },
    });
    const release = holdBack((path) => path.includes("show=future"));
    render(<RentPage me={ME} />);
    await roll();
    const status = screen.getByRole("combobox", { name: "Status" });

    fireEvent.change(status, { target: { value: "future" } });
    fireEvent.change(status, { target: { value: "past" } });
    await screen.findByRole("row", { name: "Property 3, Unit 3" });
    await release();

    expect(screen.getByRole("row", { name: "Property 3, Unit 3" })).toBeTruthy();
    expect(screen.queryByRole("row", { name: "Property 2, Unit 2" })).toBeNull();
  });

  it("opens the lease from anywhere on its row, and a tenant from their name", async () => {
    server();
    render(<RentPage me={ME} />);
    const row = await roll();

    fireEvent.click(within(row).getByRole("link", { name: "Tenant 2" }));
    expect(window.location.pathname).toBe("/tenants/t2");
    fireEvent.click(within(row).getAllByRole("cell")[0]!);
    expect(window.location.pathname).toBe("/leases/l1");
  });

  it("shows a lease with no end as month to month, with no days left, and paid up in plain ink", async () => {
    server({ "/api/leases?page=1&show=current": { ...ROLL, items: [{ ...ROW, ends_on: null, days_left: null, owed: "0.00" }] } });
    render(<RentPage me={ME} />);
    const row = await roll();

    expect(within(row).getAllByRole("cell").map((c) => c.textContent).slice(4, 6)).toEqual(["Month to month", ""]);
    expect(within(row).getByTestId("balance").className).not.toContain("text-danger");
  });

  it("receives a payment to a lease found by its tenant, unit or property", async () => {
    const fetch = server({
      "/api/leases?q=te&show=all&per_page=10": ROLL,
      "/api/leases/l1": LEASE,
      "/api/rent/leases/l1/payments": { transfers: ["tr1"], to_owner: "1550.00", to_prepaid: "0.00" },
    });
    render(<RentPage me={ME} />);
    await roll();

    fireEvent.click(screen.getByRole("button", { name: "Receive payment" }));
    const panel = screen.getByRole("dialog", { name: "Receive payment" });
    fireEvent.change(within(panel).getByLabelText("Lease"), { target: { value: "t" } });
    expect(within(panel).queryByRole("list", { name: "Leases found" })).toBeNull();  // two letters first
    fireEvent.change(within(panel).getByLabelText("Lease"), { target: { value: "te" } });
    const found = await within(panel).findByRole("list", { name: "Leases found" });
    fireEvent.click(within(found).getByRole("button", { name: "Property 1 · Unit 1 · Tenant 1, Tenant 2" }));
    expect(await within(panel).findByText("Balance due $1,550.00")).toBeTruthy();
    fireEvent.change(within(panel).getByLabelText("Payment received"), { target: { value: "1550.00" } });
    fireEvent.change(within(panel).getByLabelText("Method"), { target: { value: "money order" } });
    fireEvent.change(within(panel).getByLabelText("Check or reference"), { target: { value: "MO 12" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Record payment" }));

    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posted(fetch)).toEqual([[
      "/api/rent/leases/l1/payments",
      expect.objectContaining({ amount: "1550.00", method: "money order", reference: "MO 12" }),
    ]]);
  });

  it("goes back to the search for another lease, and says when one can't be read", async () => {
    server({ "/api/leases?q=unit&show=all&per_page=10": ROLL, "/api/leases/l1": LEASE });
    render(<RentPage me={ME} />);
    await roll();
    fireEvent.click(screen.getByRole("button", { name: "Receive payment" }));
    const panel = screen.getByRole("dialog", { name: "Receive payment" });
    fireEvent.change(within(panel).getByLabelText("Lease"), { target: { value: "unit" } });
    fireEvent.click(await within(panel).findByRole("button", { name: /^Property 1 · Unit 1/ }));
    fireEvent.click(await within(panel).findByRole("button", { name: "Another lease" }));
    expect(within(panel).getByLabelText("Lease")).toBeTruthy();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async (path: string) =>
      path.startsWith("/api/leases?q=")
        ? new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })
        : new Response(JSON.stringify(ROLL), { status: 200 })));
    render(<RentPage me={ME} />);
    await roll();
    fireEvent.click(screen.getByRole("button", { name: "Receive payment" }));
    fireEvent.change(screen.getByLabelText("Lease"), { target: { value: "unit" } });
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });

  it("receives a housing authority's check split across its leases, and starts the next with Save and add another", async () => {
    const authority = {
      id: "h1", name: "Synthetic Housing Authority", owed: "800.00", late_fee: null, payments: [],
      leases: [{
        lease_id: "l1", home: "Tenant 1, Property 1 Unit 1", monthly: "800.00", contract: null, owed: "800.00",
        open_charges: [], hold_since: null, hold_reason: null,
      }],
    };
    const fetch = server({
      "/api/assistance/authorities": [{ id: "h1", name: "Synthetic Housing Authority", leases: 1, owed: "800.00" }],
      "/api/assistance/authorities/h1": authority,
      "/api/assistance/payments": { transfers: ["t1"], paid: "800.00" },
    });
    render(<RentPage me={ME} />);
    await roll();

    fireEvent.click(screen.getByRole("button", { name: "Receive payment" }));
    const panel = screen.getByRole("dialog", { name: "Receive payment" });
    fireEvent.change(within(panel).getByLabelText("From"), { target: { value: "authority" } });
    fireEvent.click(await within(panel).findByRole("button", { name: "Fill in what it owes" }));
    fireEvent.change(within(panel).getByLabelText("Check or reference"), { target: { value: "HAP 1001" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save and add another" }));

    expect(await screen.findByText("$800.00 from Synthetic Housing Authority recorded.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/assistance/payments", expect.objectContaining({
      housing_authority_id: "h1", method: "check", reference: "HAP 1001", lines: [{ lease_id: "l1", amount: "800.00" }],
    })]]);
    // The panel stays open on a fresh check.
    await vi.waitFor(() => expect((within(panel).getByLabelText("Check or reference") as HTMLInputElement).value).toBe(""));
    expect(within(panel).getByTestId("authority-pay-total").textContent).toBe("Total $0.00");
    fireEvent.change(within(panel).getByLabelText("Tenant 1, Property 1 Unit 1 (owes $800.00)"), { target: { value: "10.00" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Record payment" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [first, second] = posted(fetch).map(([, body]) => body as Record<string, unknown>);
    expect(second?.request_id).not.toBe(first?.request_id);
  });

  it("lets an admin run rent now, saying what it charged, beside the latest runs", async () => {
    const fetch = server({ "/api/rent/runs": RUNS, "/api/rent/run": { rents: 3, recurring: 1, late_fees: 2 } });
    render(<RentPage me={ADMIN} />);
    await roll();

    fireEvent.click(screen.getByRole("button", { name: "Actions for the rent roll" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Run rent now" }));
    const panel = screen.getByRole("dialog", { name: "Run rent now" });
    expect((await within(panel).findByRole("list", { name: "Latest rent runs" })).textContent).toBe(
      "10/16/2026 nightly: 6 rents, 1 recurring charge and 0 late fees charged.",
    );
    fireEvent.change(within(panel).getByLabelText("Rent due on"), { target: { value: "2026-09-01" } });
    fireEvent.change(within(panel).getByLabelText("Late fees as of"), { target: { value: "2026-10-16" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Run rent now" }));

    expect(await screen.findByText("3 rents, 1 recurring charge and 2 late fees charged.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(posted(fetch)).toEqual([["/api/rent/run", { due_on: "2026-09-01", late_fees_as_of: "2026-10-16" }]]);
  });

  it("keeps Run rent now from staff who aren't admins", async () => {
    server();
    render(<RentPage me={ME} />);
    await roll();
    expect(screen.queryByRole("button", { name: "Actions for the rent roll" })).toBeNull();
  });

  it("shows why a run was refused, in its panel, once", async () => {
    server({ "/api/rent/runs": [] }, { "/api/rent/run": "late fees are assessed as of today or an earlier day" });
    render(<RentPage me={ADMIN} />);
    await roll();
    fireEvent.click(screen.getByRole("button", { name: "Actions for the rent roll" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Run rent now" }));
    const panel = screen.getByRole("dialog", { name: "Run rent now" });

    fireEvent.click(within(panel).getByRole("button", { name: "Run rent now" }));

    expect((await within(panel).findByRole("alert")).textContent).toBe(
      "late fees are assessed as of today or an earlier day",
    );
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(within(panel).queryByRole("list", { name: "Latest rent runs" })).toBeNull();
  });

  it("says when no lease is listed, and when the leases can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...ROLL, items: [], total: 0 }), { status: 200 })));
    render(<RentPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toBe("No lease is in effect today.");
    cleanup();

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })),
    );
    render(<RentPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });
});
