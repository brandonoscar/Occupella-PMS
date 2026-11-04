import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MaintenanceRequest } from "../maintenance";
import { TenantRequest } from "./TenantRequest";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const REQUEST: MaintenanceRequest = {
  id: "r1",
  lease_id: "1",
  property: "Property 1",
  unit: "Unit 1",
  filed_by: "Tenant 1",
  summary: "Tap drips",
  entry_allowed: true,
  emergency: false,
  submitted_at: "2026-11-02T15:00:00Z",
  status: "received",
  status_at: "2026-11-02T15:00:00Z",
  visit_on: null,
  visit_window: null,
  photos: 0,
  category: "plumbing",
  location: "Kitchen sink",
  taken_back: false,
  notes: [{ by: "Tenant 2", body: "It drips faster now.", at: "2026-11-03T09:30:00Z" }],
};

function server(answer: MaintenanceRequest) {
  const fetch = vi.fn(async () => new Response(JSON.stringify(answer), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function show(request: MaintenanceRequest, canChange = true) {
  const onChange = vi.fn();
  render(<ul><TenantRequest request={request} canChange={canChange} onChange={onChange} /></ul>);
  return onChange;
}

describe("TenantRequest", () => {
  it("says what kind of repair, where, and its notes", () => {
    show(REQUEST);

    expect(screen.getByTestId("kind-and-place").textContent).toBe("Plumbing · Kitchen sink");
    const notes = screen.getByRole("list", { name: "Notes on Tap drips" });
    expect(within(notes).getByRole("listitem").textContent).toBe(
      "It drips faster now. · Tenant 2, 11/03/2026 09:30 UTC",
    );
  });

  it("cancels a request still received from its menu, after asking", async () => {
    const fetch = server({ ...REQUEST, status: "cancelled", taken_back: true });
    const onChange = show(REQUEST);

    fireEvent.click(screen.getByRole("button", { name: "Actions for Tap drips" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Cancel request" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toContain("Cancel this request? Your manager won't act on it.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel request" }));

    await vi.waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ taken_back: true })));
    expect(fetch).toHaveBeenCalledWith("/api/tenant/requests/r1/cancel", expect.objectContaining({ method: "POST" }));
  });

  it("adds a note to an open request", async () => {
    const fetch = server(REQUEST);
    const onChange = show({ ...REQUEST, status: "scheduled" });

    fireEvent.change(screen.getByLabelText("Add a note"), { target: { value: "Monday works." } });
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));

    await vi.waitFor(() => expect(onChange).toHaveBeenCalled());
    const [path, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect([path, JSON.parse(String(init.body))]).toEqual(["/api/tenant/requests/r1/notes", { body: "Monday works." }]);
  });

  it("offers no cancel once it's being worked on, and nothing once it's closed or taken back", () => {
    show({ ...REQUEST, status: "assigned" });
    expect(screen.queryByRole("button", { name: "Actions for Tap drips" })).toBeNull();
    expect(screen.getByLabelText("Add a note")).toBeTruthy();
    cleanup();

    show({ ...REQUEST, status: "completed" });
    expect(screen.queryByLabelText("Add a note")).toBeNull();
    cleanup();

    show({ ...REQUEST, status: "cancelled", taken_back: true });
    expect(screen.getByText("Cancelled")).toBeTruthy();
    expect(screen.queryByLabelText("Add a note")).toBeNull();
    expect(screen.queryByRole("button", { name: "Actions for Tap drips" })).toBeNull();
  });

  it("changes nothing on a lease the tenant can't file under", () => {
    show(REQUEST, false);

    expect(screen.queryByRole("button", { name: "Actions for Tap drips" })).toBeNull();
    expect(screen.queryByLabelText("Add a note")).toBeNull();
  });
});
