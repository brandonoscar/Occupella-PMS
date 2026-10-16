import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Paged } from "../rentals";
import type { WorkOrder, WorkOrderRow } from "../workOrders";
import { WorkOrderPage, WorkOrdersPage } from "./WorkOrdersPages";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const ROW: WorkOrderRow = {
  id: "w1", property: { id: "p1", name: "Property 1" }, unit: { id: "u1", name: "Unit 1" },
  summary: "Leak under the sink", opened_at: "2026-10-01T15:00:00Z", status: "assigned",
  vendor: "Vendor 1", billed: "80.00",
};
const PAGE: Paged<WorkOrderRow> = { items: [ROW], total: 1, page: 1, per_page: 50 };
const ORDER: WorkOrder = {
  ...ROW,
  steps: [{ step: "assigned", vendor: "Vendor 1", taken_at: "2026-10-02T09:00:00Z", note: "Tuesday" }],
  bills: [{ id: "b1", vendor: "Vendor 1", reference: "INV-1", amount: "80.00", paid: true }],
  request: { id: "r1", tenant: "Tenant 1", summary: "Drip", submitted_at: "2026-09-30T10:00:00Z" },
};
const BOOKS = {
  bank_accounts: [], owners: [], accounts: [], tenants: [], vendors: [], expense_accounts: [],
  schedule_e_lines: [],
  properties: [
    { id: "p1", name: "Property 1", late_fees: null, nsf_fee: null, units: [{ id: "u1", name: "Unit 1", leases: [] }] },
    { id: "p2", name: "Property 2", late_fees: null, nsf_fee: null, units: [] },
  ],
};

function server(answers: Record<string, unknown> = {}, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
      return new Response(JSON.stringify({ id: "w9" }), { status: 201 });
    }
    const body =
      path in answers ? answers[path]
      : path.startsWith("/api/work-orders?") ? PAGE
      : path === "/api/setup" ? BOOKS
      : path === "/api/maintenance/vendors" ? [{ id: "v1", name: "Vendor 1" }, { id: "v2", name: "Vendor 2" }]
      : path.startsWith("/api/maintenance/requests") ? []
      : path === "/api/work-orders/w1" ? ORDER
      : { detail: "no such work order" };
    return new Response(JSON.stringify(body), { status: "detail" in (body as object) ? 404 : 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("WorkOrdersPage", () => {
  it("lists the open work orders, and the closed ones or all on their tabs", async () => {
    const fetch = server();
    render(<WorkOrdersPage me={ME} />);

    const row = await screen.findByRole("row", { name: "Leak under the sink" });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Leak under the sink", "Unit 1, Property 1", "10/01/2026", "Assigned", "Vendor 1", "$80.00",
    ]);
    expect(within(row).getByRole("link").getAttribute("href")).toBe("/work-orders/w1");
    fireEvent.click(screen.getByRole("tab", { name: "Closed" }));
    fireEvent.change(screen.getByLabelText("Find a work order, property, unit or vendor"), { target: { value: "sink" } });
    await vi.waitFor(() =>
      expect(fetch.mock.calls.map(([p]) => p)).toContain("/api/work-orders?show=closed&page=1&q=sink"),
    );
  });

  it("opens a work order for a property or one of its units in a panel", async () => {
    const fetch = server();
    render(<WorkOrdersPage me={ME} />);
    await screen.findByRole("row", { name: "Leak under the sink" });

    fireEvent.click(screen.getByRole("button", { name: "New work order" }));
    const panel = screen.getByRole("dialog", { name: "New work order" });
    await within(panel).findByLabelText("Property");
    fireEvent.change(within(panel).getByLabelText("Unit"), { target: { value: "u1" } });
    fireEvent.change(within(panel).getByLabelText("What needs doing"), { target: { value: " Leak " } });
    fireEvent.click(within(panel).getByRole("button", { name: "Open work order" }));
    expect(await within(panel).findByText('Work order "Leak" opened.')).toBeTruthy();
    // Another property clears the unit: the work order is the whole property's.
    fireEvent.change(within(panel).getByLabelText("Property"), { target: { value: "p2" } });
    expect((within(panel).getByLabelText("Unit") as HTMLSelectElement).value).toBe("");
    fireEvent.change(within(panel).getByLabelText("What needs doing"), { target: { value: "Gutters" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Open work order" }));
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(2));

    expect(posted(fetch)).toEqual([
      ["/api/work-orders", { property_id: "p1", unit_id: "u1", summary: " Leak " }],
      ["/api/work-orders", { property_id: "p2", summary: "Gutters" }],
    ]);
  });

  it("says when none is open, and when there is no property or they can't be read", async () => {
    server({ "/api/work-orders?show=open&page=1": { ...PAGE, items: [], total: 0 }, "/api/setup": { ...BOOKS, properties: [] } });
    render(<WorkOrdersPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No work order is open.");
    fireEvent.click(screen.getByRole("button", { name: "New work order" }));
    expect(await screen.findByText("Add a property from Settings, Company setup first.")).toBeTruthy();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<WorkOrdersPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
    fireEvent.click(screen.getByRole("button", { name: "New work order" }));
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toBeTruthy();
  });
});

describe("WorkOrderPage", () => {
  it("shows where it stands, its steps, bills and the request it answers", async () => {
    server();
    render(<WorkOrderPage me={ME} id="w1" />);

    expect(await screen.findByRole("heading", { level: 1, name: "Leak under the sink" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Unit 1" }).getAttribute("href")).toBe("/units/u1");
    expect(screen.getByTestId("request").textContent).toBe("Requested by Tenant 1 on 09/30/2026: Drip");
    expect(screen.getByRole("list", { name: "Steps" }).textContent).toBe("assigned 10/02/2026, to Vendor 1: Tuesday");
    expect(within(screen.getByRole("row", { name: "INV-1" })).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Vendor 1", "INV-1", "$80.00", "Paid",
    ]);
  });

  it("records the next step, and says why one was refused", async () => {
    const fetch = server({}, { "/api/maintenance/work-orders/w1/steps": "the work order is closed" });
    render(<WorkOrderPage me={ME} id="w1" />);
    await screen.findByRole("heading", { level: 1 });

    fireEvent.change(await screen.findByLabelText("Vendor"), { target: { value: "v2" } });
    fireEvent.change(screen.getByLabelText("Note (optional)"), { target: { value: " Friday " } });
    fireEvent.click(screen.getByRole("button", { name: "Record" }));
    expect((await screen.findByRole("alert")).textContent).toBe("the work order is closed");
    fireEvent.change(screen.getByLabelText("What happened"), { target: { value: "completed" } });
    expect(screen.queryByLabelText("Vendor")).toBeNull();

    expect(posted(fetch)).toEqual([
      ["/api/maintenance/work-orders/w1/steps", { step: "assigned", vendor_id: "v2", note: "Friday" }],
    ]);
  });

  it("records a completion, has no form once closed, and says when there is no such work order", async () => {
    const fetch = server({ "/api/work-orders/w2": { ...ORDER, id: "w2", status: "completed", steps: [], bills: [], request: null } });
    render(<WorkOrderPage me={ME} id="w1" />);
    await screen.findByRole("heading", { level: 1 });
    fireEvent.change(screen.getByLabelText("What happened"), { target: { value: "completed" } });
    fireEvent.click(screen.getByRole("button", { name: "Record" }));
    expect(await screen.findByText("Completed.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/maintenance/work-orders/w1/steps", { step: "completed", vendor_id: null, note: null }]]);
    cleanup();

    render(<WorkOrderPage me={ME} id="w2" />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByLabelText("What happened")).toBeNull();
    expect(screen.getByText("No step yet.")).toBeTruthy();
    expect(screen.getByText("No bill names this work order.")).toBeTruthy();
    cleanup();

    render(<WorkOrderPage me={ME} id="nope" />);
    expect((await screen.findByRole("alert")).textContent).toBe("no such work order");
  });
});
