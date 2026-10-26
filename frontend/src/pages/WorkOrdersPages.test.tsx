import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { holdBack } from "../holdBack";
import type { Paged } from "../rentals";
import type { WorkOrder, WorkOrderRow } from "../workOrders";
import { WorkOrderPage, WorkOrdersPage, shownQuery, stepLine } from "./WorkOrdersPages";

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/work-orders");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const ROW: WorkOrderRow = {
  id: "w1", number: 7, property: { id: "p1", name: "Property 1" }, unit: { id: "u1", name: "Unit 1" },
  summary: "Leak under the sink", opened_at: "2026-10-01T15:00:00Z", status: "scheduled",
  vendor_id: "v1", vendor: "Vendor 1", billed: "80.00", priority: "urgent", due_on: "2026-10-05",
  overdue: true, scheduled_for: "2026-10-04", visit_window: "8-10 am", updated_at: "2026-10-03T09:00:00Z", days_open: 2,
};
const PAGE: Paged<WorkOrderRow> = { items: [ROW], total: 1, page: 1, per_page: 50 };
const ORDER: WorkOrder = {
  ...ROW,
  description: "Water under the cabinet.\nShut-off valve is stuck.",
  entry: "call first",
  steps: [
    { step: "assigned", vendor: "Vendor 1", taken_at: "2026-10-02T09:00:00Z", scheduled_for: null, visit_window: null, note: "Tuesday", by_vendor: false },
    { step: "scheduled", vendor: null, taken_at: "2026-10-03T09:00:00Z", scheduled_for: "2026-10-04", visit_window: "8-10 am", note: null, by_vendor: true },
  ],
  bills: [{ id: "b1", vendor: "Vendor 1", reference: "INV-1", amount: "80.00", paid: true }],
  request: {
    id: "r1", tenant: "Tenant 1", summary: "Drip", submitted_at: "2026-09-30T10:00:00Z", emergency: true, entry_allowed: false,
    photos: [{ id: "d1", title: "Cabinet" }], emailed: ["Staff 1"],
  },
  tenants: [
    { name: "Tenant 1", contact: { email: "tenant1@example.com", phone: "555-0100" } },
    { name: "Tenant 2", contact: { email: null, phone: null } },
  ],
  charges: [{
    id: "c1", lease_id: "l1", unit: "Unit 1", tenants: "Tenant 1, Tenant 2", due_on: "2026-10-06", amount: "60.00",
    memo: "Work order #7: Leak under the sink", open: "25.00",
  }],
  leases: [{ id: "l1", unit: "Unit 1", tenants: "Tenant 1, Tenant 2" }],
  recurring: "every 3 months",
};
const RECURRING = [
  {
    id: "r1", property_id: "p1", property: "Property 1", unit_id: "u1", unit: "Unit 1", summary: "Change the furnace filter",
    every_months: 3, schedule: "every 3 months on the 15th", day_of_month: 15, starts_on: "2026-01-20", priority: "high",
    vendor: "Vendor 1", ends_on: null, last_opened_on: "2026-08-15",
  },
  {
    id: "r2", property_id: "p2", property: "Property 2", unit_id: null, unit: null, summary: "Clean the gutters",
    every_months: 12, schedule: "every 12 months on the 1st", day_of_month: 1, starts_on: "2026-11-01", priority: "normal",
    vendor: null, ends_on: "2027-12-01", last_opened_on: null,
  },
];
const BOOKS = {
  bank_accounts: [], owners: [], tenants: [], expense_accounts: [], schedule_e_lines: [],
  vendors: [{ id: "v1", name: "Vendor 1" }, { id: "v2", name: "Vendor 2" }],
  accounts: [
    { account: "a2", owner: "Owner 2", property: "Property 2", property_id: "p2" },
    { account: "a1", owner: "Owner 1", property: "Property 1", property_id: "p1" },
  ],
  properties: [
    { id: "p1", name: "Property 1", late_fees: null, nsf_fee: null, units: [{ id: "u1", name: "Unit 1", leases: [] }] },
    { id: "p2", name: "Property 2", late_fees: null, nsf_fee: null, units: [] },
  ],
};

function server(answers: Record<string, unknown> = {}, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
      const made = path.endsWith("/details") ? { changed: !path.includes("same") } : { id: "w9" };
      const answer = `POST ${path}` in answers ? answers[`POST ${path}`] : path in answers ? answers[path] : made;
      return new Response(JSON.stringify(answer), { status: 201 });
    }
    const body =
      path in answers ? answers[path]
      : path.startsWith("/api/work-orders?") ? PAGE
      : path === "/api/setup" ? BOOKS
      : path === "/api/maintenance/vendors" ? [{ id: "v1", name: "Vendor 1" }, { id: "v2", name: "Vendor 2" }]
      : path.startsWith("/api/maintenance/requests") ? []
      : path === "/api/property-groups" ? { groups: [{ id: "g1", name: "Group 1", properties: [] }], properties: [] }
      : path === "/api/documents" ? { documents: [], subjects: {} }
      : path === "/api/work-orders/w1" ? ORDER
      : path === "/api/work-orders/recurring" ? RECURRING
      : { detail: "no such work order" };
    return new Response(JSON.stringify(body), { status: "detail" in (body as object) ? 404 : 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const asked = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method !== "POST").map(([path]) => path);
const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);
// The plain line under a button (ADR 0010, rule 1): what describes it.
const says = (button: HTMLElement) => document.getElementById(button.getAttribute("aria-describedby") ?? "")?.textContent;

describe("shownQuery and stepLine", () => {
  it("asks for the open ones, all, or one status among all", () => {
    expect(shownQuery("open")).toEqual({ show: "open" });
    expect(shownQuery("all")).toEqual({ show: "all" });
    expect(shownQuery("scheduled")).toEqual({ show: "all", status: "scheduled" });
  });

  it("reads each step as the timeline does", () => {
    expect(stepLine("assigned", "Vendor 1", null)).toBe("Assigned to Vendor 1");
    expect(stepLine("assigned", null, null)).toBe("Assigned to a vendor");
    expect(stepLine("scheduled", null, "2026-10-20")).toBe("Scheduled for 10/20/2026");
    expect(stepLine("scheduled", null, "2026-10-20", "8-10 am")).toBe("Scheduled for 10/20/2026, 8-10 am");
    expect(stepLine("completed", null, null)).toBe("Completed");
    expect(stepLine("cancelled", null, null)).toBe("Cancelled");
  });
});

describe("WorkOrdersPage", () => {
  it("lists the work orders with their number, priority, status and due day; a row opens its page", async () => {
    const fetch = server();
    render(<WorkOrdersPage me={ME} />);

    const row = await screen.findByRole("row", { name: "Leak under the sink" });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "7", "Unit 1, Property 1", "Leak under the sink", "Vendor 1", "Urgent", "Scheduled", "10/05/2026", "2", "10/03/2026",
    ]);
    // Overdue: due before today and not closed.
    expect(within(row).getByText("10/05/2026").className).toContain("text-danger");
    expect(asked(fetch)).toContain("/api/work-orders?show=open&page=1");
    fireEvent.click(row);
    expect(window.location.pathname).toBe("/work-orders/w1");
  });

  it("narrows by status, vendor and property group, from the first page", async () => {
    const fetch = server({
      "/api/work-orders?show=open&page=1": { ...PAGE, total: 51 },
      "/api/work-orders?show=open&page=2": { ...PAGE, page: 2, total: 51 },
    });
    render(<WorkOrdersPage me={ME} />);
    await screen.findByRole("row", { name: "Leak under the sink" });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await vi.waitFor(() => expect(asked(fetch)).toContain("/api/work-orders?show=open&page=2"));

    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "completed" } });
    await vi.waitFor(() => expect(asked(fetch)).toContain("/api/work-orders?show=all&status=completed&page=1"));
    fireEvent.change(await screen.findByLabelText("Vendor"), { target: { value: "v2" } });
    await vi.waitFor(() =>
      expect(asked(fetch)).toContain("/api/work-orders?show=all&status=completed&page=1&vendor=v2"),
    );
    await screen.findByRole("option", { name: "Group 1" });
    fireEvent.change(screen.getByLabelText("Properties shown"), { target: { value: "group:g1" } });
    await vi.waitFor(() =>
      expect(asked(fetch)).toContain("/api/work-orders?show=all&status=completed&page=1&group=g1&vendor=v2"),
    );
  });

  it("opens a work order with its details in a panel, then goes to it", async () => {
    const fetch = server();
    render(<WorkOrdersPage me={ME} />);
    await screen.findByRole("row", { name: "Leak under the sink" });

    fireEvent.click(screen.getByRole("button", { name: "New work order" }));
    const panel = screen.getByRole("dialog", { name: "New work order" });
    await within(panel).findByLabelText("Property");
    fireEvent.change(within(panel).getByLabelText("Unit"), { target: { value: "u1" } });
    // Another property clears the unit: the work order is the whole property's.
    fireEvent.change(within(panel).getByLabelText("Property"), { target: { value: "p2" } });
    expect((within(panel).getByLabelText("Unit") as HTMLSelectElement).value).toBe("");
    fireEvent.change(within(panel).getByLabelText("Property"), { target: { value: "p1" } });
    fireEvent.change(within(panel).getByLabelText("Unit"), { target: { value: "u1" } });
    fireEvent.change(within(panel).getByLabelText("Title"), { target: { value: " Leak " } });
    fireEvent.change(within(panel).getByLabelText("Priority"), { target: { value: "high" } });
    fireEvent.change(within(panel).getByLabelText("Due (optional)"), { target: { value: "2026-10-09" } });
    fireEvent.change(within(panel).getByLabelText("What is wrong (optional)"), { target: { value: " Drips " } });
    fireEvent.change(within(panel).getByLabelText("Entry"), { target: { value: "allowed" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Open work order" }));
    await vi.waitFor(() => expect(window.location.pathname).toBe("/work-orders/w9"));

    expect(posted(fetch)).toEqual([[
      "/api/work-orders",
      { property_id: "p1", unit_id: "u1", summary: " Leak ", priority: "high", description: "Drips", due_on: "2026-10-09", entry: "allowed" },
    ]]);
  });

  it("puts staff on call and off it in the On call panel, a change saved at once", async () => {
    const before = [
      { staff_id: "s1", name: "Staff 1", on_call: false, emailed: true },
      { staff_id: "s2", name: "Staff 2", on_call: false, emailed: false },
    ];
    server({ "/api/maintenance/on-call": before });
    render(<WorkOrdersPage me={ME} />);
    await screen.findByRole("row", { name: "Leak under the sink" });

    fireEvent.click(screen.getByRole("button", { name: "On call" }));
    const panel = screen.getByRole("dialog", { name: "On call" });
    const list = await within(panel).findByRole("list", { name: "On call" });
    expect(list.textContent).toBe("Staff 1" + "Staff 2(no login to email)");
    server({ "/api/maintenance/on-call": [{ ...before[0], on_call: true }, before[1]] });
    fireEvent.click(within(panel).getByLabelText("Staff 1"));
    expect(await within(panel).findByText("Staff 1 is on call.")).toBeTruthy();
    expect((within(panel).getByLabelText("Staff 1") as HTMLInputElement).checked).toBe(true);
  });

  it("says why a change to who is on call was refused, or that they can't be read", async () => {
    const fetch = server(
      { "/api/maintenance/on-call": [{ staff_id: "s1", name: "Staff 1", on_call: true, emailed: true }] },
      { "/api/maintenance/on-call": "no such staff member" },
    );
    render(<WorkOrdersPage me={ME} />);
    await screen.findByRole("row", { name: "Leak under the sink" });
    fireEvent.click(screen.getByRole("button", { name: "On call" }));
    const panel = screen.getByRole("dialog", { name: "On call" });
    fireEvent.click(await within(panel).findByLabelText("Staff 1"));
    expect((await within(panel).findByRole("alert")).textContent).toBe("no such staff member");
    // The box goes back to what it was.
    expect((within(panel).getByLabelText("Staff 1") as HTMLInputElement).checked).toBe(true);
    expect(posted(fetch)).toEqual([["/api/maintenance/on-call", { staff_id: "s1", on_call: false }]]);
    cleanup();

    server({ "/api/maintenance/on-call": { detail: "sign in first" } });
    render(<WorkOrdersPage me={ME} />);
    fireEvent.click(await screen.findByRole("button", { name: "On call" }));
    expect((await within(screen.getByRole("dialog")).findByRole("alert")).textContent).toBe("sign in first");
  });

  it("lists the recurring work orders, stops one from a day and adds one", async () => {
    const fetch = server({ "POST /api/work-orders/recurring": { id: "r3", opened: 2 } });
    render(<WorkOrdersPage me={ME} />);
    await screen.findByRole("row", { name: "Leak under the sink" });
    fireEvent.click(screen.getByRole("button", { name: "Recurring" }));
    const panel = screen.getByRole("dialog", { name: "Recurring work orders" });
    const list = await within(panel).findByRole("list", { name: "Recurring work orders" });
    const filter = within(list).getByRole("listitem", { name: "Change the furnace filter" });
    expect(filter.textContent).toContain("Change the furnace filter, Unit 1, Property 1: every 3 months on the 15th from 01/20/2026");
    expect(filter.textContent).toContain("High priority, Vendor 1. Last opened 08/15/2026.");
    const gutters = within(list).getByRole("listitem", { name: "Clean the gutters" });
    expect(gutters.textContent).toContain("Normal priority, no vendor. None opened yet. Ends 12/01/2027.");
    // An ended one has nothing to stop.
    expect(within(gutters).queryByRole("button", { name: "Stop it" })).toBeNull();

    fireEvent.change(within(filter).getByLabelText('Stop "Change the furnace filter" from'), { target: { value: "2026-12-01" } });
    fireEvent.click(within(filter).getByRole("button", { name: "Stop it" }));
    expect(await within(panel).findByText('"Change the furnace filter" stops from 12/01/2026.')).toBeTruthy();

    fireEvent.change(within(panel).getByLabelText("Property"), { target: { value: "p1" } });
    fireEvent.change(within(panel).getByLabelText("Unit"), { target: { value: "u1" } });
    fireEvent.change(within(panel).getByLabelText("What it is"), { target: { value: " Test the smoke alarms " } });
    fireEvent.change(within(panel).getByLabelText("Every"), { target: { value: "6" } });
    fireEvent.change(within(panel).getByLabelText("On day (1 to 28)"), { target: { value: "10" } });
    fireEvent.change(within(panel).getByLabelText("From"), { target: { value: "2026-11-01" } });
    fireEvent.change(within(panel).getByLabelText("Priority"), { target: { value: "high" } });
    fireEvent.change(within(panel).getByLabelText("Vendor (optional)"), { target: { value: "v2" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add recurring work order" }));
    expect(await within(panel).findByText('"Test the smoke alarms" comes back by itself. 2 already due opened now.')).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/work-orders/recurring/r1/end", { ends_on: "2026-12-01" }],
      ["/api/work-orders/recurring", {
        property_id: "p1", unit_id: "u1", summary: " Test the smoke alarms ", every_months: 6, day_of_month: 10,
        starts_on: "2026-11-01", priority: "high", vendor_id: "v2",
      }],
    ]);
  });

  it("says when no work order comes back yet, or the list can't be read", async () => {
    server({ "/api/work-orders/recurring": [] });
    render(<WorkOrdersPage me={ME} />);
    await screen.findByRole("row", { name: "Leak under the sink" });
    fireEvent.click(screen.getByRole("button", { name: "Recurring" }));
    expect(await screen.findByText("No work order comes back by itself yet.")).toBeTruthy();
    cleanup();

    server({ "/api/work-orders/recurring": { detail: "sign in first" } });
    render(<WorkOrdersPage me={ME} />);
    fireEvent.click(await screen.findByRole("button", { name: "Recurring" }));
    expect((await within(screen.getByRole("dialog")).findByRole("alert")).textContent).toBe("sign in first");
  });

  it("says when none is open or found, and when there is no property or they can't be read", async () => {
    server({
      "/api/work-orders?show=open&page=1": { ...PAGE, items: [], total: 0 },
      "/api/work-orders?show=all&page=1": { ...PAGE, items: [], total: 0 },
      "/api/setup": { ...BOOKS, properties: [] },
    });
    render(<WorkOrdersPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No work order is open.");
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "all" } });
    await vi.waitFor(() => expect(screen.getByTestId("empty-state").textContent).toContain("No work orders found."));
    fireEvent.click(screen.getByRole("button", { name: "New work order" }));
    expect(await screen.findByText("Add a property from Properties first.")).toBeTruthy();
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<WorkOrdersPage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
    fireEvent.click(screen.getByRole("button", { name: "New work order" }));
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toBeTruthy();
  });

  it("shows the work orders of the vendor picked now, even when an earlier vendor's answer comes back last", async () => {
    server({
      "/api/work-orders?show=open&page=1&vendor=v1": PAGE,
      "/api/work-orders?show=open&page=1&vendor=v2": { ...PAGE, items: [{ ...ROW, id: "w9", vendor_id: "v2", vendor: "Vendor 2", summary: "Broken window" }] },
    });
    const release = holdBack((path) => path.includes("vendor=v1"));
    render(<WorkOrdersPage me={ME} />);
    const vendor = screen.getByRole("combobox", { name: "Vendor" });
    await within(vendor).findByRole("option", { name: "Vendor 2" });

    fireEvent.change(vendor, { target: { value: "v1" } });
    fireEvent.change(vendor, { target: { value: "v2" } });
    await screen.findByRole("row", { name: "Broken window" });
    await release();

    expect(screen.getByRole("row", { name: "Broken window" })).toBeTruthy();
    expect(screen.queryByRole("row", { name: "Leak under the sink" })).toBeNull();
  });
});

describe("WorkOrderPage", () => {
  it("shows its details, the tenants to call, its timeline, bills and the request it answers", async () => {
    server();
    render(<WorkOrderPage me={ME} id="w1" />);

    expect(await screen.findByRole("heading", { level: 1, name: "#7 Leak under the sink" })).toBeTruthy();
    expect(screen.getByTestId("status").textContent).toBe("Scheduled");
    expect(screen.getByTestId("priority").textContent).toBe("Urgent");
    const summary = screen.getByLabelText("Summary");
    expect(within(summary).getByRole("link", { name: "Unit 1" }).getAttribute("href")).toBe("/units/u1");
    expect(within(summary).getByRole("link", { name: "Vendor 1" }).getAttribute("href")).toBe("/vendors/v1");
    expect(within(summary).getByText("10/05/2026").className).toContain("text-danger");
    expect(within(summary).getByText("10/04/2026, 8-10 am")).toBeTruthy();
    expect(within(summary).getByText("2 days")).toBeTruthy();
    expect(within(summary).getByText("Call the tenant before going in")).toBeTruthy();
    expect(screen.getByTestId("description").textContent).toBe("Water under the cabinet.\nShut-off valve is stuck.");
    expect(screen.getByTestId("request").textContent).toBe("EmergencyRequested by Tenant 1 on 09/30/2026: Drip");
    expect(screen.getByText("The tenant asked to be called first.")).toBeTruthy();
    expect(screen.getByTestId("emailed").textContent).toBe("Emailed to who was on call: Staff 1.");
    expect(within(screen.getByRole("list", { name: "Request photos" })).getByRole("link", { name: "Cabinet" })
      .getAttribute("href")).toBe("/api/documents/d1/file");
    expect(screen.getByRole("list", { name: "Tenants" }).textContent).toBe(
      "Tenant 1, 555-0100, tenant1@example.com" + "Tenant 2",
    );
    expect(screen.getByRole("link", { name: "555-0100" }).getAttribute("href")).toBe("tel:555-0100");
    expect(screen.getByRole("list", { name: "Timeline" }).textContent).toBe(
      "10/01/2026 Opened" + "10/02/2026 Assigned to Vendor 1: Tuesday" + "10/03/2026 Scheduled for 10/04/2026, 8-10 am (by the vendor)",
    );
    expect(within(screen.getByRole("row", { name: "INV-1" })).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Vendor 1", "INV-1", "$80.00", "Paid",
    ]);
    expect(screen.getByRole("link", { name: "INV-1" }).getAttribute("href")).toBe("/bills/b1");
  });

  it("assigns a vendor in the Next step panel, and says why one was refused", async () => {
    const fetch = server(
      { "/api/work-orders/w1": { ...ORDER, vendor_id: null, vendor: null, status: "open", steps: [] } },
      { "/api/maintenance/work-orders/w1/steps": "the vendor's insurance expired on 09/30/2026; record its renewal before assigning them" },
    );
    render(<WorkOrderPage me={ME} id="w1" />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByText("None yet")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Next step" }));
    const panel = screen.getByRole("dialog", { name: "Next step" });
    // Nothing to schedule before a vendor is assigned.
    expect(within(panel).queryByRole("option", { name: "Schedule the visit" })).toBeNull();
    fireEvent.change(await within(panel).findByLabelText("Vendor"), { target: { value: "v2" } });
    fireEvent.change(within(panel).getByLabelText("Note (optional)"), { target: { value: " Friday " } });
    expect(says(within(panel).getByRole("button", { name: "Record" }))).toBe(
      "Assigns them; it stays in the timeline. They're emailed the work order only if work order emails are on.",
    );
    fireEvent.click(within(panel).getByRole("button", { name: "Record" }));
    expect((await within(panel).findByRole("alert")).textContent).toBe(
      "the vendor's insurance expired on 09/30/2026; record its renewal before assigning them",
    );
    fireEvent.change(within(panel).getByLabelText("What happens next"), { target: { value: "completed" } });
    expect(within(panel).queryByLabelText("Vendor")).toBeNull();
    expect(says(within(panel).getByRole("button", { name: "Record" })))
      .toBe("Closes the work order; no step can follow it. No one is emailed.");

    expect(posted(fetch)).toEqual([
      ["/api/maintenance/work-orders/w1/steps", { step: "assigned", vendor_id: "v2", scheduled_for: null, visit_window: null, note: "Friday" }],
    ]);
  });

  it("schedules the visit once a vendor is assigned, and closes the panel", async () => {
    const fetch = server();
    render(<WorkOrderPage me={ME} id="w1" />);
    await screen.findByRole("heading", { level: 1 });

    fireEvent.click(screen.getByRole("button", { name: "Next step" }));
    const panel = screen.getByRole("dialog", { name: "Next step" });
    expect((within(panel).getByLabelText("What happens next") as HTMLSelectElement).value).toBe("scheduled");
    fireEvent.change(within(panel).getByLabelText("Visit on"), { target: { value: "2026-10-20" } });
    fireEvent.change(within(panel).getByLabelText("Window (optional, like 8-10 am)"), { target: { value: " 9-11 am " } });
    expect(says(within(panel).getByRole("button", { name: "Record" })))
      .toBe("Adds the visit to the timeline. The vendor is emailed the day only if work order emails are on.");
    fireEvent.click(within(panel).getByRole("button", { name: "Record" }));
    expect(await screen.findByText("Scheduled for 10/20/2026.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();

    expect(posted(fetch)).toEqual([
      ["/api/maintenance/work-orders/w1/steps", {
        step: "scheduled", vendor_id: null, scheduled_for: "2026-10-20", visit_window: "9-11 am", note: null,
      }],
    ]);
  });

  it("lists what tenants were charged for it, and charges the tenant in a panel", async () => {
    const fetch = server({ "/api/rent/leases/l1/charges": { id: "c2" } });
    render(<WorkOrderPage me={ME} id="w1" />);
    await screen.findByRole("heading", { level: 1 });
    expect(within(screen.getByLabelText("Summary")).getByText("Opened by itself, every 3 months")).toBeTruthy();
    const charged = screen.getByRole("table", { name: "Charged to tenants" });
    expect(within(charged).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Unit 1", "Tenant 1, Tenant 2", "10/06/2026", "Work order #7: Leak under the sink", "$60.00", "$25.00",
    ]);
    expect(within(charged).getByRole("link", { name: "Unit 1" }).getAttribute("href")).toBe("/leases/l1");

    fireEvent.click(screen.getByRole("button", { name: "Charge the tenant" }));
    const panel = screen.getByRole("dialog", { name: "Charge the tenant" });
    expect((within(panel).getByLabelText("Memo") as HTMLInputElement).value).toBe("Work order #7: Leak under the sink");
    fireEvent.change(within(panel).getByLabelText("Amount"), { target: { value: "85.00" } });
    fireEvent.change(within(panel).getByLabelText("Due"), { target: { value: "2026-10-20" } });
    expect(says(within(panel).getByRole("button", { name: "Charge" }))).toBe(
      "Adds a $85.00 fee to the lease, due 10/20/2026. Nothing is paid now; the owner still pays the vendor's bill.",
    );
    fireEvent.click(within(panel).getByRole("button", { name: "Charge" }));
    expect(await screen.findByText("$85.00 charged to Tenant 1, Tenant 2.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();

    expect(posted(fetch)).toEqual([[
      "/api/rent/leases/l1/charges",
      { kind: "fee", amount: "85.00", due_on: "2026-10-20", memo: "Work order #7: Leak under the sink", work_order_id: "w1" },
    ]]);
  });

  it("edits its details, and says when nothing changed", async () => {
    const fetch = server({ "/api/work-orders/w1/details": { changed: false } });
    render(<WorkOrderPage me={ME} id="w1" />);
    await screen.findByRole("heading", { level: 1 });

    fireEvent.click(screen.getByRole("button", { name: "Edit details" }));
    const panel = screen.getByRole("dialog", { name: "Edit details" });
    expect((within(panel).getByLabelText("Priority") as HTMLSelectElement).value).toBe("urgent");
    expect((within(panel).getByLabelText("Entry") as HTMLSelectElement).value).toBe("call first");
    fireEvent.change(within(panel).getByLabelText("Due (optional)"), { target: { value: "" } });
    fireEvent.change(within(panel).getByLabelText("Entry"), { target: { value: "" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Nothing changed.")).toBeTruthy();

    expect(posted(fetch)).toEqual([[
      "/api/work-orders/w1/details",
      { priority: "urgent", description: "Water under the cabinet.\nShut-off valve is stuck.", due_on: null, entry: null },
    ]]);
  });

  it("creates the vendor's bill naming it, its vendor, property and memo filled in", async () => {
    const fetch = server({ "/api/payables/bills": { id: "b2" } });
    render(<WorkOrderPage me={ME} id="w1" />);
    await screen.findByRole("heading", { level: 1 });

    fireEvent.click(screen.getByRole("button", { name: "Create bill" }));
    const panel = screen.getByRole("dialog", { name: "Create bill" });
    expect((await within(panel).findByLabelText("Vendor") as HTMLSelectElement).value).toBe("v1");
    expect((within(panel).getByLabelText("Owner and property") as HTMLSelectElement).value).toBe("a1");
    expect((within(panel).getByLabelText("Memo") as HTMLInputElement).value).toBe("Leak under the sink");
    fireEvent.change(within(panel).getByLabelText("Ref #"), { target: { value: "INV-2" } });
    fireEvent.change(within(panel).getByLabelText("Amount"), { target: { value: "45.00" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Record bill" }));
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(1));

    expect(posted(fetch)[0]?.[0]).toBe("/api/payables/bills");
    expect(posted(fetch)[0]?.[1]).toMatchObject({
      vendor_id: "v1", account: "a1", reference: "INV-2", amount: "45.00", memo: "Leak under the sink", work_order_id: "w1",
    });
  });

  it("has no Next step once closed, and says when there is no such work order", async () => {
    server({ "/api/work-orders/w2": { ...ORDER, id: "w2", status: "completed", unit: null, vendor_id: null, vendor: null, due_on: null, overdue: false, scheduled_for: null, description: null, entry: null, steps: [], bills: [], request: null, tenants: [], charges: [], leases: [], recurring: null } });
    render(<WorkOrderPage me={ME} id="w2" />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("button", { name: "Next step" })).toBeNull();
    expect(screen.getByText("The whole property")).toBeTruthy();
    expect(screen.getByText("No day set")).toBeTruthy();
    expect(screen.getByText("Not scheduled")).toBeTruthy();
    expect(screen.getByText("Not asked")).toBeTruthy();
    expect(screen.queryByTestId("description")).toBeNull();
    expect(screen.queryByRole("list", { name: "Tenants" })).toBeNull();
    expect(screen.getByRole("list", { name: "Timeline" }).textContent).toBe("10/01/2026 Opened");
    expect(screen.getByText("No bill names this work order.")).toBeTruthy();
    // No lease to charge, nothing charged, and not opened by a recurring one.
    expect(screen.queryByRole("button", { name: "Charge the tenant" })).toBeNull();
    expect(screen.queryByRole("table", { name: "Charged to tenants" })).toBeNull();
    expect(screen.queryByText(/Opened by itself/)).toBeNull();
    cleanup();

    render(<WorkOrderPage me={ME} id="nope" />);
    expect((await screen.findByRole("alert")).textContent).toBe("no such work order");
  });

  it("shows the work order opened now, even when an earlier work order's answer comes back last", async () => {
    server({ "/api/work-orders/w2": { ...ORDER, id: "w2", number: 9, summary: "Broken window" } });
    const release = holdBack((path) => path === "/api/work-orders/w1");
    const { rerender } = render(<WorkOrderPage me={ME} id="w1" />);
    rerender(<WorkOrderPage me={ME} id="w2" />);
    await screen.findByRole("heading", { level: 1, name: "#9 Broken window" });
    await release();

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("#9 Broken window");
    expect(screen.queryByRole("heading", { level: 1, name: "#7 Leak under the sink" })).toBeNull();
  });
});
