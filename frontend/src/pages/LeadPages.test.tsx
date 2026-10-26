import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { holdBack } from "../holdBack";
import type { Lead } from "../leads";
import { askedAbout, showingTime, startsAt } from "../leads";
import { LeadPage } from "./LeadPage";
import { LeadsPage } from "./LeadsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  localStorage.clear();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1", pmc_name: "Synthetic PMC",
};
const SHOWING = {
  id: "sh1", unit_id: "u1", property: "Property 1", unit: "Unit 1", starts_at: "2026-10-20T15:00:00Z",
  shown_by: "Staff 1", outcome: null, note: null,
};
const LEAD: Lead = {
  id: "l1", name: "Prospect 1", email: "prospect.1@example.com", phone: "555-555-0100", property: "Property 1",
  unit: "Unit 1", source: "Website", move_in_on: "2026-11-15", added_at: "2026-10-18T12:00:00Z", showings: 1,
  next_showing: "2026-10-20T15:00:00Z", status: "open", property_id: "pr1", unit_id: "u1", note: "Asked about parking.",
  added_by: "Staff 1", showing_list: [SHOWING], closing: null,
};
const BOOKS = {
  bank_accounts: [], owners: [], accounts: [], tenants: [], vendors: [], expense_accounts: [], schedule_e_lines: [],
  properties: [
    { id: "pr1", name: "Property 1", late_fees: null, nsf_fee: null, units: [{ id: "u1", name: "Unit 1", leases: [] }, { id: "u2", name: "Unit 2", leases: [] }] },
    { id: "pr2", name: "Property 2", late_fees: null, nsf_fee: null, units: [] },
  ],
};

function server(found: Lead = LEAD, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
      return new Response(JSON.stringify(path === "/api/leasing/leads" ? { id: "l9" } : found), { status: 201 });
    }
    if (path === "/api/leasing/leads/l1") return new Response(JSON.stringify(found));
    if (path.startsWith("/api/leasing/leads?")) {
      return new Response(JSON.stringify({ items: path.includes("status=closed") ? [] : [found], total: 1, page: 1, per_page: 50 }));
    }
    if (path === "/api/setup") return new Response(JSON.stringify(BOOKS));
    if (path === "/api/staff") return new Response(JSON.stringify([{ staff_id: "s1", name: "Staff 1", roles: [] }]));
    if (path === "/api/property-groups") return new Response("[]");
    if (path.startsWith("/api/properties")) return new Response(JSON.stringify({ items: [], total: 0, page: 1, per_page: 200 }));
    return new Response(JSON.stringify({ detail: path }), { status: 404 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("leads", () => {
  it("reads what a lead asked about and a showing's time", () => {
    expect([askedAbout(LEAD), askedAbout({ property: "Property 1", unit: null }), askedAbout({ property: null, unit: null })])
      .toEqual(["Unit 1, Property 1", "Property 1", ""]);
    expect(showingTime("2026-10-20T15:00:00Z", "UTC")).toBe("10/20/2026 3:00 PM");
    expect(showingTime("2026-10-20T15:00:00Z", "America/New_York")).toBe("10/20/2026 11:00 AM");
    expect(showingTime("not a time")).toBe("not a time");
    expect(startsAt("2026-10-20", "15:00")).toBe(new Date("2026-10-20T15:00").toISOString());
  });
});

describe("LeadsPage", () => {
  it("lists open leads with what they asked about and their next showing, and opens a lead", async () => {
    const fetch = server();
    render(<LeadsPage me={ME} />);

    const row = await screen.findByRole("row", { name: "Lead Prospect 1" });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Prospect 1", "Unit 1, Property 1", "Website", "10/18/2026", showingTime("2026-10-20T15:00:00Z"), "1", "Open",
    ]);
    fireEvent.click(screen.getByRole("tab", { name: "Closed" }));
    expect(await screen.findByText("No lead is closed yet.")).toBeTruthy();
    expect(fetch.mock.calls.some(([path]) => path.startsWith("/api/leasing/leads?status=closed&page=1"))).toBe(true);
  });

  it("adds a lead with what they asked about, then opens its page", async () => {
    const fetch = server();
    render(<LeadsPage me={ME} />);
    await screen.findByRole("row", { name: "Lead Prospect 1" });

    fireEvent.click(screen.getByRole("button", { name: "Add lead" }));
    const panel = screen.getByRole("dialog", { name: "Add lead" });
    fireEvent.change(within(panel).getByLabelText("Name"), { target: { value: "Prospect 2" } });
    fireEvent.change(within(panel).getByLabelText("Phone"), { target: { value: " 555-555-0101 " } });
    await within(panel).findByRole("option", { name: "Property 1" });
    fireEvent.change(within(panel).getByRole("combobox", { name: /^Property asked about/ }), { target: { value: "pr1" } });
    fireEvent.change(within(panel).getByRole("combobox", { name: /^Unit asked about/ }), { target: { value: "u2" } });
    fireEvent.change(within(panel).getByLabelText("Source (optional)"), { target: { value: "Walk-in" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add lead" }));
    await vi.waitFor(() => expect(window.location.pathname).toBe("/leads/l9"));

    expect(posted(fetch)).toEqual([["/api/leasing/leads", {
      name: "Prospect 2", email: null, phone: "555-555-0101", property_id: "pr1", unit_id: "u2", source: "Walk-in",
      move_in_on: null, note: null,
    }]]);
  });

  it("shows the leads of the tab picked now, even when an earlier tab's answer comes back last", async () => {
    server();
    const release = holdBack((path) => path.startsWith("/api/leasing/leads?status=open"));
    render(<LeadsPage me={ME} />);

    fireEvent.click(screen.getByRole("tab", { name: "Closed" }));
    await screen.findByText("No lead is closed yet.");
    await release();

    expect(screen.getByText("No lead is closed yet.")).toBeTruthy();
    expect(screen.queryByRole("row", { name: "Lead Prospect 1" })).toBeNull();
  });
});

describe("LeadPage", () => {
  it("shows the lead and its showings, records how one went, and schedules another", async () => {
    const fetch = server();
    render(<LeadPage me={ME} id="l1" />);

    expect(await screen.findByRole("heading", { name: "Prospect 1" })).toBeTruthy();
    const details = screen.getByLabelText("Lead details");
    expect(details.textContent).toContain("prospect.1@example.com");
    expect(details.textContent).toContain("Unit 1, Property 1");
    expect(screen.getByText("Asked about parking.")).toBeTruthy();
    const when = showingTime(SHOWING.starts_at);
    const row = screen.getByRole("row", { name: `Showing ${when}` });
    fireEvent.click(within(row).getByRole("button", { name: `Actions for Showing ${when}` }));
    // Recording how it went asks first; Cancel sends nothing.
    const question = `Mark the ${when} showing no-show? It can't be changed after.`;
    fireEvent.click(screen.getByRole("menuitem", { name: "No-show" }));
    expect(screen.getByRole("alertdialog", { name: question }).textContent).toContain(question);
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(posted(fetch)).toEqual([]);
    fireEvent.click(within(row).getByRole("button", { name: `Actions for Showing ${when}` }));
    fireEvent.click(screen.getByRole("menuitem", { name: "No-show" }));
    expect(posted(fetch)).toEqual([]);
    fireEvent.click(within(screen.getByRole("alertdialog", { name: question })).getByRole("button", { name: "No-show" }));
    await screen.findByText(`The ${when} showing: no-show.`);

    fireEvent.click(screen.getByRole("button", { name: "Schedule showing" }));
    const panel = screen.getByRole("dialog", { name: "Schedule showing" });
    await within(panel).findByRole("option", { name: "Staff 1" });
    await within(panel).findByRole("option", { name: "Unit 1, Property 1" });
    // The unit the lead asked about comes first.
    expect((within(panel).getByRole("combobox", { name: "Unit" }) as HTMLSelectElement).value).toBe("u1");
    fireEvent.change(within(panel).getByRole("combobox", { name: "Unit" }), { target: { value: "u2" } });
    fireEvent.change(within(panel).getByLabelText("Day"), { target: { value: "2026-10-22" } });
    fireEvent.change(within(panel).getByLabelText("Time"), { target: { value: "09:30" } });
    fireEvent.change(within(panel).getByRole("combobox", { name: /^Shown by/ }), { target: { value: "s1" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Schedule" }));
    const at = startsAt("2026-10-22", "09:30");
    await screen.findByText(`Showing on ${showingTime(at)}.`);

    expect(posted(fetch)).toEqual([
      ["/api/leasing/showings/sh1/outcome", { outcome: "no_show" }],
      ["/api/leasing/leads/l1/showings", { unit_id: "u2", starts_at: at, shown_by: "s1" }],
    ]);
  });

  it("closes a lead, and a closed one says how and takes no showing", async () => {
    const fetch = server();
    render(<LeadPage me={ME} id="l1" />);
    await screen.findByRole("heading", { name: "Prospect 1" });

    fireEvent.click(screen.getByRole("button", { name: "Close lead" }));
    const panel = screen.getByRole("dialog", { name: "Close lead" });
    fireEvent.change(within(panel).getByRole("combobox", { name: /^Outcome/ }), { target: { value: "lost" } });
    fireEvent.change(within(panel).getByLabelText("Why (optional)"), { target: { value: " Moved away " } });
    fireEvent.click(within(panel).getByRole("button", { name: "Close lead" }));
    await screen.findByText("Closed Prospect 1: lost.");
    expect(posted(fetch)).toEqual([["/api/leasing/leads/l1/close", { outcome: "lost", reason: "Moved away" }]]);
    cleanup();

    server({
      ...LEAD, status: "lost", next_showing: null,
      showing_list: [{ ...SHOWING, outcome: "shown", note: "Liked the kitchen" }],
      closing: { outcome: "lost", reason: "Moved away", closed_at: "2026-10-21T10:00:00Z", closed_by: "Staff 1" },
    });
    render(<LeadPage me={ME} id="l1" />);
    expect((await screen.findByTestId("status")).textContent).toBe("Lost");
    expect(screen.getByText("Lost: Moved away. Closed 10/21/2026 by Staff 1.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Schedule showing" })).toBeNull();
    const row = screen.getByRole("row", { name: `Showing ${showingTime(SHOWING.starts_at)}` });
    expect(row.textContent).toContain("ShownLiked the kitchen");
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("says why a showing was refused, and when a lead can't be read", async () => {
    server(LEAD, { "/api/leasing/showings/sh1/outcome": "how this showing went is recorded already" });
    render(<LeadPage me={ME} id="l1" />);
    const when = showingTime(SHOWING.starts_at);
    const row = await screen.findByRole("row", { name: `Showing ${when}` });
    fireEvent.click(within(row).getByRole("button", { name: `Actions for Showing ${when}` }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Shown" }));
    fireEvent.click(within(screen.getByRole("alertdialog", { name: `Mark the ${when} showing shown? It can't be changed after.` }))
      .getByRole("button", { name: "Shown" }));
    expect(await screen.findByText("how this showing went is recorded already")).toBeTruthy();
    cleanup();

    server();
    render(<LeadPage me={ME} id="l2" />);
    expect(await screen.findByText("/api/leasing/leads/l2")).toBeTruthy();
  });

  it("shows the lead opened now, even when an earlier lead's answer comes back last", async () => {
    const reads = server();
    vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) =>
      path === "/api/leasing/leads/l2" ? new Response(JSON.stringify({ ...LEAD, id: "l2", name: "Prospect 2" })) : reads(path, init)));
    const release = holdBack((path) => path === "/api/leasing/leads/l1");
    const { rerender } = render(<LeadPage me={ME} id="l1" />);
    rerender(<LeadPage me={ME} id="l2" />);
    await screen.findByRole("heading", { level: 1, name: "Prospect 2" });
    await release();

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Prospect 2");
    expect(screen.queryByRole("heading", { level: 1, name: "Prospect 1" })).toBeNull();
  });
});
