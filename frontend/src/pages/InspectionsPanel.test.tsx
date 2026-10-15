import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Inspection } from "../inspections";
import { InspectionsPanel } from "./InspectionsPanel";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const FOUND: Inspection[] = [
  {
    id: "i1", kind: "move-in", inspected_on: "2026-01-01", notes: null, recorded_by: "Staff 1",
    recorded_at: "2026-01-01T12:00:00Z",
    items: [{ room: "Kitchen", condition: "good", note: null }],
  },
];

function server(found: Inspection[] = FOUND) {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) =>
    init?.method === "POST"
      ? new Response(JSON.stringify({ id: "i2" }), { status: 201 })
      : new Response(JSON.stringify(found), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("InspectionsPanel", () => {
  it("opens on the lease's inspections and the report, and records another room by room", async () => {
    const fetch = server();
    render(<InspectionsPanel leaseId="l1" label="Property 1 / Unit 1" />);
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Inspections" }));

    expect(await screen.findByText("Move-in, 2026-01-01: 1 room")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Inspection report (PDF)" }).getAttribute("href")).toBe(
      "/api/reports/trust_report_inspection?arg=l1&format=pdf",
    );
    fireEvent.change(screen.getByLabelText("Inspection"), { target: { value: "move-out" } });
    fireEvent.change(screen.getByLabelText("Inspected on"), { target: { value: "2026-06-30" } });
    fireEvent.change(screen.getByLabelText("Room 1 condition"), { target: { value: "damaged" } });
    fireEvent.change(screen.getByLabelText("Room 1 note"), { target: { value: "Burn on the counter" } });
    fireEvent.change(screen.getByLabelText("Room 4"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Add a room" }));
    fireEvent.change(screen.getByLabelText("Room 5"), { target: { value: "Garage" } });
    fireEvent.click(screen.getByRole("button", { name: "Record inspection" }));

    expect(await screen.findByText("The move-out inspection of 2026-06-30 is recorded.")).toBeTruthy();
    const [path, init] = fetch.mock.calls.find(([, i]) => i?.method === "POST") ?? [];
    expect(path).toBe("/api/inspections");
    expect(JSON.parse(String(init?.body))).toEqual({
      lease_id: "l1", kind: "move-out", inspected_on: "2026-06-30",
      items: [
        { room: "Kitchen", condition: "damaged", note: "Burn on the counter" },
        { room: "Living room", condition: "good" },
        { room: "Bedroom", condition: "good" },
        { room: "Garage", condition: "good" },
      ],
    });
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/inspections?lease_id=l1");
  });

  it("says when none is recorded yet", async () => {
    server([]);
    render(<InspectionsPanel leaseId="l1" label="Property 1 / Unit 1" />);
    fireEvent.click(screen.getByRole("button", { name: "Inspections" }));
    expect(await screen.findByText("No inspection recorded yet.")).toBeTruthy();
  });
});
