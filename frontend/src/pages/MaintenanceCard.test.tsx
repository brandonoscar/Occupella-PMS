import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MaintenanceCard } from "./MaintenanceCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const BASE = {
  lease_id: "l1",
  property: "Property 1",
  unit: "Unit 1",
  filed_by: "Tenant 1",
  entry_allowed: false,
  submitted_at: "2026-10-11T18:05:00Z",
  status_at: "2026-10-11T18:05:00Z",
  vendor: null,
};
const RECEIVED = { ...BASE, id: "r1", summary: "Tap drips", status: "received", work_order_id: null };
const OPENED = { ...BASE, id: "r2", summary: "Door sticks", status: "opened", work_order_id: "w2" };

function server() {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (path.startsWith("/api/maintenance/requests?")) {
      return new Response(JSON.stringify([RECEIVED, OPENED]));
    }
    if (path === "/api/maintenance/vendors") {
      return new Response(JSON.stringify([{ id: "v1", name: "Vendor 1" }]));
    }
    if (init?.method === "POST") return new Response(JSON.stringify({}), { status: 201 });
    return new Response(JSON.stringify({ detail: path }), { status: 404 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posts = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("MaintenanceCard", () => {
  it("opens a work order for a new request", async () => {
    const fetch = server();
    render(<MaintenanceCard />);

    const received = await screen.findByRole("listitem", { name: "Tap drips" });
    expect(within(received).getByText(/Arrange a time to enter\./)).toBeTruthy();
    fireEvent.click(within(received).getByRole("button", { name: "Open work order" }));
    expect(await screen.findByText('Work order opened for "Tap drips".')).toBeTruthy();

    await vi.waitFor(() =>
      expect(posts(fetch)).toEqual([["/api/maintenance/requests/r1/work-order", {}]]),
    );
  });

  it("assigns a vendor only once one is chosen, then records each step", async () => {
    const fetch = server();
    render(<MaintenanceCard />);

    const opened = await screen.findByRole("listitem", { name: "Door sticks" });
    const assign = within(opened).getByRole("button", { name: "Assign" }) as HTMLButtonElement;
    expect(assign.disabled).toBe(true);
    await screen.findByRole("option", { name: "Vendor 1" });
    fireEvent.change(within(opened).getByLabelText("Vendor for Door sticks"), {
      target: { value: "v1" },
    });
    fireEvent.click(assign);
    fireEvent.click(within(opened).getByRole("button", { name: "Done" }));
    expect(await screen.findByText('"Door sticks" updated.')).toBeTruthy();

    await vi.waitFor(() =>
      expect(posts(fetch)).toEqual([
        ["/api/maintenance/work-orders/w2/steps", { step: "assigned", vendor_id: "v1" }],
        ["/api/maintenance/work-orders/w2/steps", { step: "completed" }],
      ]),
    );
  });

  it("asks for closed requests too when told to", async () => {
    const fetch = server();
    render(<MaintenanceCard />);
    await screen.findByRole("listitem", { name: "Tap drips" });

    fireEvent.click(screen.getByLabelText("Show done and closed requests too"));

    await vi.waitFor(() =>
      expect(fetch.mock.calls.map(([path]) => path)).toContain(
        "/api/maintenance/requests?show=all",
      ),
    );
  });
});
