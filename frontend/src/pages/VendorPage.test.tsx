import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { VendorPageData } from "../workOrders";
import { VendorPage } from "./VendorPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const NO_CONTACT = {
  email: null, phone: null, alternate_phone: null, address_line_1: null, address_line_2: null,
  city: null, state: null, postal_code: null,
};

const VENDOR: VendorPageData = {
  id: "v1", name: "Vendor 1", w9_on_file: true, contact: { ...NO_CONTACT, phone: "555-555-0100" },
  insurance: [
    { coverage: "general liability", carrier: "Carrier A", policy_number: "GL-1", starts_on: "2026-01-01",
      expires_on: "2026-12-31", limit_amount: null, standing: "current" },
  ],
  bills: [
    { id: "b1", reference: "INV-1", owner: "Owner 1", property: "Property 1", bill_date: "2026-10-01",
      amount: "80.00", paid: false },
  ],
  work_orders: [{ id: "w1", property: "Property 1", summary: "Leak", status: "assigned" }],
};

const NO_FILES = { documents: [], subjects: { w9: [], receipt: [], move_out_photo: [], owner_document: [] } };

function server(vendor: unknown = VENDOR, status = 200) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) =>
    init?.method === "POST" ? new Response(null, { status: 204 })
    : path === "/api/documents" ? new Response(JSON.stringify(NO_FILES), { status: 200 })
    : new Response(JSON.stringify(vendor), { status }),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("VendorPage", () => {
  it("shows their W-9, contact details, insurance, bills and work orders", async () => {
    server();
    render(<VendorPage me={ME} id="v1" />);

    expect(await screen.findByRole("heading", { level: 1, name: "Vendor 1" })).toBeTruthy();
    expect(screen.getByText("W-9 on file")).toBeTruthy();
    expect(screen.getByRole("group", { name: "Contact details, Vendor 1" }).textContent).toContain("555-555-0100");
    expect(screen.getByRole("list", { name: "Insurance" }).textContent).toContain("Carrier A GL-1, 01/01/2026 to 12/31/2026");
    expect(within(screen.getByRole("row", { name: "INV-1" })).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "INV-1", "Owner 1, Property 1", "10/01/2026", "$80.00", "Unpaid",
    ]);
    const orders = screen.getByRole("list", { name: "Work orders" });
    expect(within(orders).getByRole("link", { name: "Leak" }).getAttribute("href")).toBe("/work-orders/w1");
    // Their W-9s are on their Files card.
    expect(await screen.findByLabelText("W-9 file")).toBeTruthy();
    // A vendor is renamed, never archived.
    expect(screen.queryByRole("button", { name: "Archive" })).toBeNull();
  });

  it("renames them", async () => {
    const fetch = server();
    render(<VendorPage me={ME} id="v1" />);
    await screen.findByRole("heading", { level: 1 });

    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    fireEvent.change(screen.getByLabelText("New name"), { target: { value: "Vendor One" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));

    expect(await screen.findByText("Renamed Vendor One.")).toBeTruthy();
    const [path, init] = fetch.mock.calls.find(([, i]) => i?.method === "POST") ?? [];
    expect([path, JSON.parse(String(init?.body))]).toEqual(["/api/vendors/v1/name", { name: "Vendor One" }]);
  });

  it("says when they have nothing on file yet, and when there is no such vendor", async () => {
    server({ ...VENDOR, w9_on_file: false, insurance: [], bills: [], work_orders: [] });
    render(<VendorPage me={ME} id="v1" />);
    expect(await screen.findByText("no W-9")).toBeTruthy();
    expect(screen.getByText("No insurance on file. Record a policy on Vendors.")).toBeTruthy();
    expect(screen.getByText("No bills yet.")).toBeTruthy();
    expect(screen.getByText("No work order is assigned to them.")).toBeTruthy();
    cleanup();

    server({ detail: "no such vendor" }, 404);
    render(<VendorPage me={ME} id="nope" />);
    expect((await screen.findByRole("alert")).textContent).toBe("no such vendor");
  });
});
