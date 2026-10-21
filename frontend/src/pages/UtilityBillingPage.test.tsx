import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Preview, PropertyUtilities } from "../utilities";
import { UtilityBillingPage } from "./UtilityBillingPage";

beforeEach(() => window.history.replaceState(null, "", "/utility-billing"));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const PROPERTIES = {
  items: [
    { id: "p1", name: "Birch Flats", owners: [], units: 3, occupied: 2, archived: false },
    { id: "p2", name: "Cedar House", owners: [], units: 1, occupied: 1, archived: false },
  ],
  total: 2, page: 1, per_page: 200,
};

const PAGE: PropertyUtilities = {
  property: "Birch Flats",
  plans: [
    { id: "pl1", utility: "water", starts_on: "2026-01-01", method: "units", owner_percent: "10", recorded_by: "Staff 1" },
    { id: "pl2", utility: "trash", starts_on: "2026-01-01", method: "none", owner_percent: "0", recorded_by: null },
  ],
  units: [
    { unit_id: "u1", unit: "Unit A", square_feet: 750 },
    { unit_id: "u2", unit: "Unit B", square_feet: null },
  ],
  leases: [
    { lease_id: "l1", home: "Tenant 1, Birch Flats Unit A", starts_on: "2026-01-01", ends_on: null, billed: ["water"] },
    { lease_id: "l2", home: "Tenant 2, Birch Flats Unit B", starts_on: "2026-01-01", ends_on: null, billed: [] },
  ],
  bills: [{
    id: "b1", utility: "water", period_start: "2026-09-01", period_end: "2026-09-30", amount: "90.00", method: "units",
    owner_percent: "10", master_usage: null, due_on: "2026-10-05", memo: "Meter 7", charged: "27.00", owner_keeps: "63.00",
    shares: [
      { lease_id: "l1", home: "Tenant 1, Birch Flats Unit A", billed: true, weight: "30", usage: null, amount: "27.00" },
      { lease_id: "l2", home: "Tenant 2, Birch Flats Unit B", billed: false, weight: "30", usage: null, amount: "0.00" },
    ],
  }],
};

const PREVIEW: Preview = {
  method: "units", owner_percent: "10", charged: "27.00", owner_keeps: "63.00", shares: PAGE.bills[0]!.shares,
};

function server(page: PropertyUtilities = PAGE, refuse = "") {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 409 });
      if (path === "/api/utilities/preview") return new Response(JSON.stringify(PREVIEW), { status: 200 });
      return new Response(JSON.stringify({ id: "new", ...PREVIEW }), { status: 201 });
    }
    if (path.startsWith("/api/properties")) return new Response(JSON.stringify(PROPERTIES), { status: 200 });
    if (path === "/api/utilities/properties/p1") return new Response(JSON.stringify(page), { status: 200 });
    if (path === "/api/utilities/properties/p2") return new Response(JSON.stringify({ ...page, property: "Cedar House", bills: [] }), { status: 200 });
    return new Response(JSON.stringify({ detail: "no such page" }), { status: 404 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map((c) => c.textContent);

async function open() {
  render(<UtilityBillingPage me={ME} />);
  await screen.findByRole("table", { name: "Plans" });
}

describe("UtilityBillingPage", () => {
  it("shows a property's plans, leases, units and bills", async () => {
    server();
    await open();

    expect(cells(screen.getByRole("row", { name: "Water from 01/01/2026" }))).toEqual([
      "Water", "01/01/2026", "By unit", "10%", "Staff 1",
    ]);
    expect(cells(screen.getByRole("row", { name: "Trash from 01/01/2026" }))).toEqual([
      "Trash", "01/01/2026", "Not billed back", "", "",
    ]);
    const leases = screen.getByRole("table", { name: "Leases" });
    expect(cells(within(leases).getByRole("row", { name: "Tenant 2, Birch Flats Unit B" })).slice(0, 2)).toEqual([
      "Tenant 2, Birch Flats Unit B", "Nothing",
    ]);
    expect(cells(screen.getByRole("row", { name: "Unit B" })).slice(0, 2)).toEqual(["Unit B", "Not on file"]);
    const bill = screen.getByRole("listitem", { name: "Water, 09/01/2026 to 09/30/2026" });
    expect(within(bill).getByText("Water, 09/01/2026 to 09/30/2026: $90.00, by unit (Meter 7)")).toBeTruthy();
    expect(within(bill).getByTestId("bill-totals").textContent).toBe("Charged $27.00, due 10/05/2026; the owner keeps $63.00.");
    expect(cells(within(bill).getByRole("row", { name: "Tenant 2, Birch Flats Unit B" }))).toEqual([
      "Tenant 2, Birch Flats Unit B", "30 days", "Not billed",
    ]);
  });

  it("switches property", async () => {
    server();
    await open();
    fireEvent.change(screen.getByLabelText("Property"), { target: { value: "p2" } });
    expect(await screen.findByText("No bill billed back yet.")).toBeTruthy();
  });

  it("sets a plan, a unit's square feet and a lease's utilities", async () => {
    const fetch = server();
    await open();

    fireEvent.click(screen.getByRole("button", { name: "Set plan" }));
    let form = screen.getByRole("dialog", { name: "Set plan" });
    fireEvent.change(within(form).getByLabelText("Utility"), { target: { value: "gas" } });
    fireEvent.change(within(form).getByLabelText("How it's shared"), { target: { value: "occupants" } });
    fireEvent.change(within(form).getByLabelText("From"), { target: { value: "2026-11-01" } });
    fireEvent.change(within(form).getByLabelText("Owner keeps (percent, for the common areas)"), { target: { value: "5" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save plan" }));
    expect(await screen.findByText("Gas is billed back by occupants from 11/01/2026.")).toBeTruthy();
    // The page is hidden from everything but the panel until the panel has closed.
    const closed = () => waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await closed();

    fireEvent.click(screen.getByRole("button", { name: "Actions for Unit B" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Set square feet" }));
    form = screen.getByRole("dialog", { name: "Square feet" });
    fireEvent.change(within(form).getByLabelText("Square feet, Unit B"), { target: { value: "1200" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Unit B: 1,200 square feet.")).toBeTruthy();
    await closed();

    fireEvent.click(screen.getByRole("button", { name: "Actions for Tenant 2, Birch Flats Unit B" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Utilities billed" }));
    form = screen.getByRole("dialog", { name: "Utilities billed" });
    fireEvent.change(within(form).getByLabelText("Utility"), { target: { value: "trash" } });
    fireEvent.click(within(form).getByLabelText("Billed for it"));
    fireEvent.change(within(form).getByLabelText("From"), { target: { value: "2026-11-01" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Tenant 2, Birch Flats Unit B is not billed for trash from 11/01/2026.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/utilities/properties/p1/plans", { utility: "gas", method: "occupants", starts_on: "2026-11-01", owner_percent: "5" }],
      ["/api/utilities/units/u2/area", { square_feet: 1200 }],
      ["/api/utilities/leases/l2/billed", { utility: "trash", billed: false, starts_on: "2026-11-01" }],
    ]);
  });

  it("stops a plan", async () => {
    server();
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Set plan" }));
    const form = screen.getByRole("dialog", { name: "Set plan" });
    fireEvent.change(within(form).getByLabelText("How it's shared"), { target: { value: "none" } });
    expect(within(form).queryByLabelText(/Owner keeps/)).toBeNull();
    fireEvent.change(within(form).getByLabelText("From"), { target: { value: "2026-12-01" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save plan" }));
    expect(await screen.findByText("Water is no longer billed back from 12/01/2026.")).toBeTruthy();
  });

  it("previews a bill's shares, then charges the leases", async () => {
    const fetch = server();
    await open();

    fireEvent.click(screen.getByRole("button", { name: "Bill back a utility" }));
    const form = screen.getByRole("dialog", { name: "Bill back a utility" });
    fireEvent.change(within(form).getByLabelText("Period from"), { target: { value: "2026-09-01" } });
    fireEvent.change(within(form).getByLabelText("Period to"), { target: { value: "2026-09-30" } });
    fireEvent.change(within(form).getByLabelText("The bill's amount"), { target: { value: "90.00" } });
    fireEvent.change(within(form).getByLabelText("Due on"), { target: { value: "2026-10-05" } });
    fireEvent.change(within(form).getByLabelText("Memo (optional)"), { target: { value: " Meter 7 " } });
    fireEvent.click(within(form).getByRole("button", { name: "Preview" }));

    const preview = await within(form).findByTestId("bill-preview");
    expect(within(preview).getByText("By unit, the owner keeping 10%: $27.00 to the leases, $63.00 stays with the owner.")).toBeTruthy();
    fireEvent.click(within(form).getByRole("button", { name: "Charge the leases" }));
    expect(await screen.findByText("Water billed back: $27.00 charged to the leases; the owner keeps $63.00.")).toBeTruthy();

    const asked = { property_id: "p1", utility: "water", period_start: "2026-09-01", period_end: "2026-09-30", amount: "90.00" };
    expect(posted(fetch)).toEqual([
      ["/api/utilities/preview", asked],
      ["/api/utilities/bills", { ...asked, due_on: "2026-10-05", memo: "Meter 7" }],
    ]);
  });

  it("asks a submetered utility's readings, and a change makes the preview stale", async () => {
    const page: PropertyUtilities = {
      ...PAGE,
      plans: [{ id: "pl3", utility: "water", starts_on: "2026-01-01", method: "submeter", owner_percent: "0", recorded_by: null }],
    };
    const fetch = server(page);
    await open();

    fireEvent.click(screen.getByRole("button", { name: "Bill back a utility" }));
    const form = screen.getByRole("dialog", { name: "Bill back a utility" });
    fireEvent.change(within(form).getByLabelText("Period from"), { target: { value: "2026-09-01" } });
    fireEvent.change(within(form).getByLabelText("Period to"), { target: { value: "2026-09-30" } });
    fireEvent.change(within(form).getByLabelText("The bill's amount"), { target: { value: "100.00" } });
    fireEvent.change(within(form).getByLabelText("Master meter's reading for the period"), { target: { value: "100" } });
    fireEvent.change(within(form).getByLabelText("Reading, Tenant 1, Birch Flats Unit A"), { target: { value: " 30 " } });
    fireEvent.click(within(form).getByRole("button", { name: "Preview" }));
    await within(form).findByTestId("bill-preview");
    fireEvent.change(within(form).getByLabelText("Reading, Tenant 2, Birch Flats Unit B"), { target: { value: "45.5" } });
    expect(within(form).queryByTestId("bill-preview")).toBeNull();
    expect(within(form).getByRole("button", { name: "Preview" })).toBeTruthy();

    expect(posted(fetch)[0]?.[1]).toMatchObject({ master_usage: "100", usage: [{ lease_id: "l1", usage: "30" }] });
  });

  it("shows a refused preview in the panel", async () => {
    server(PAGE, "the property bills no gas back on 2026-09-01");
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Bill back a utility" }));
    const form = screen.getByRole("dialog", { name: "Bill back a utility" });
    fireEvent.change(within(form).getByLabelText("Period from"), { target: { value: "2026-09-01" } });
    fireEvent.change(within(form).getByLabelText("Period to"), { target: { value: "2026-09-30" } });
    fireEvent.change(within(form).getByLabelText("The bill's amount"), { target: { value: "90.00" } });
    fireEvent.click(within(form).getByRole("button", { name: "Preview" }));
    expect(await within(form).findByText("the property bills no gas back on 2026-09-01")).toBeTruthy();
  });

  it("says when there's nothing yet, or the list didn't load", async () => {
    server({ ...PAGE, plans: [], leases: [], bills: [] });
    await new Promise<void>((resolve) => { render(<UtilityBillingPage me={ME} />); resolve(); });
    expect(await screen.findByText("No utility is billed back yet.")).toBeTruthy();
    expect(screen.getByText("No lease in effect.")).toBeTruthy();

    cleanup();
    vi.stubGlobal("fetch", vi.fn(async (path: string) => path.startsWith("/api/properties")
      ? new Response(JSON.stringify({ ...PROPERTIES, items: [], total: 0 }), { status: 200 })
      : new Response("{}", { status: 404 })));
    render(<UtilityBillingPage me={ME} />);
    expect(await screen.findByText("No properties yet")).toBeTruthy();

    cleanup();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<UtilityBillingPage me={ME} />);
    expect(await screen.findByText("sign in first")).toBeTruthy();
  });
});
