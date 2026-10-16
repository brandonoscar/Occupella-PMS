import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
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
};
const ROLL: Paged<RentRollRow> = { items: [ROW], total: 1, page: 1, per_page: 50 };
const LEASE = {
  id: "l1",
  property: "Property 1",
  unit: "Unit 1",
  tenants: [{ id: "t1", name: "Tenant 1", prepaid: "0.00", deposit: "1500.00" }],
  owed: "1550.00",
} as LeaseDetail;
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
      "Property 1", "Unit 1", "Tenant 1, Tenant 2", "01/01/2026", "12/31/2026", "76",
      "$1,500.00", "$1,500.00", "$1,550.00",
    ]);
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
