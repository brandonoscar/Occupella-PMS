import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { holdBack } from "../holdBack";
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
  profile: { trade: "Plumbing", gets_1099: true, payment_method: "EFT" },
  insurance: [
    { coverage: "general liability", carrier: "Carrier A", policy_number: "GL-1", starts_on: "2026-01-01",
      expires_on: "2026-12-31", limit_amount: "1000000.00", standing: "current" },
    { coverage: "workers compensation", carrier: "Carrier B", policy_number: "WC-1", starts_on: "2025-01-01",
      expires_on: "2025-12-31", limit_amount: null, standing: "expired" },
  ],
  insurance_standing: "expired",
  bills: [
    { id: "b1", reference: "INV-1", owner: "Owner 1", property: "Property 1", bill_date: "2026-10-01",
      amount: "80.00", paid: false },
  ],
  work_orders: [
    { id: "w1", number: 4, property: "Property 1", unit: "Unit 1", summary: "Leak", status: "scheduled",
      opened_at: "2026-10-02T09:00:00Z" },
  ],
};

const NO_FILES = { documents: [], subjects: { w9: [], receipt: [], move_out_photo: [], owner_document: [], work_order_photo: [] } };

function server(vendor: unknown = VENDOR, status = 200, answers: Record<string, unknown> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) =>
    init?.method === "POST"
      ? path in answers ? new Response(JSON.stringify(answers[path]), { status: 200 })
        : path.endsWith("/insurance") ? new Response(JSON.stringify({ id: "i1" }), { status: 201 })
          : new Response(null, { status: 204 })
    : path === "/api/documents" ? new Response(JSON.stringify(NO_FILES), { status: 200 })
    : new Response(JSON.stringify(vendor), { status }),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("VendorPage", () => {
  it("opens on their summary: trade, 1099, how they are paid, W-9 and contact details", async () => {
    server();
    render(<VendorPage me={ME} id="v1" />);

    expect(await screen.findByRole("heading", { level: 1, name: "Vendor 1" })).toBeTruthy();
    expect(screen.getByText("W-9 on file")).toBeTruthy();
    // Their insurance by the coverage that runs out first.
    expect(screen.getByTestId("standing").textContent).toBe("Insurance: Expired");
    expect(screen.getByRole("tab", { name: "Summary" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByLabelText("Summary").textContent).toBe("TradePlumbing1099 vendorYesPaid byEFTW-9On file");
    expect(screen.getByRole("group", { name: "Contact details, Vendor 1" }).textContent).toContain("555-555-0100");
    // A vendor is renamed, never archived.
    expect(screen.queryByRole("button", { name: "Archive" })).toBeNull();
  });

  it("has their policies, bills, work orders and W-9s on tabs of their own", async () => {
    server();
    render(<VendorPage me={ME} id="v1" />);
    await screen.findByRole("heading", { level: 1 });

    fireEvent.click(screen.getByRole("tab", { name: "Insurance" }));
    expect(within(screen.getByRole("row", { name: "general liability" })).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "general liability", "Carrier A", "GL-1", "01/01/2026", "12/31/2026", "$1,000,000.00", "Current",
    ]);
    expect(within(screen.getByRole("row", { name: "workers compensation" })).getAllByRole("cell").at(-1)?.textContent).toBe("Expired");

    fireEvent.click(screen.getByRole("tab", { name: "Bills" }));
    expect(within(screen.getByRole("row", { name: "INV-1" })).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "INV-1", "Owner 1, Property 1", "10/01/2026", "$80.00", "Unpaid",
    ]);
    expect(screen.getByRole("link", { name: "INV-1" }).getAttribute("href")).toBe("/bills/b1");

    fireEvent.click(screen.getByRole("tab", { name: "Work orders" }));
    expect(within(screen.getByRole("row", { name: "Leak" })).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "4", "Leak", "Unit 1, Property 1", "10/02/2026", "Scheduled",
    ]);
    expect(screen.getByRole("link", { name: "Leak" }).getAttribute("href")).toBe("/work-orders/w1");

    fireEvent.click(screen.getByRole("tab", { name: "Files" }));
    expect(await screen.findByLabelText("W-9 file")).toBeTruthy();
  });

  it("edits their details in a side panel, and says when nothing changed", async () => {
    const fetch = server(VENDOR, 200, { "/api/vendors/v1/profile": { changed: false } });
    render(<VendorPage me={ME} id="v1" />);
    await screen.findByRole("heading", { level: 1 });

    fireEvent.click(screen.getByRole("button", { name: "Edit details" }));
    const panel = screen.getByRole("dialog", { name: "Edit details" });
    expect((within(panel).getByLabelText("Trade (optional)") as HTMLInputElement).value).toBe("Plumbing");
    expect((within(panel).getByLabelText("1099 vendor") as HTMLInputElement).checked).toBe(true);
    fireEvent.change(within(panel).getByLabelText("Trade (optional)"), { target: { value: " " } });
    fireEvent.change(within(panel).getByLabelText("Paid by"), { target: { value: "check" } });
    fireEvent.click(within(panel).getByLabelText("1099 vendor"));
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Nothing changed.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();

    expect(posted(fetch)).toEqual([
      ["/api/vendors/v1/profile", { trade: null, gets_1099: false, payment_method: "check" }],
    ]);
  });

  it("adds a policy on the Insurance tab", async () => {
    const fetch = server();
    render(<VendorPage me={ME} id="v1" />);
    await screen.findByRole("heading", { level: 1 });

    fireEvent.click(screen.getByRole("tab", { name: "Insurance" }));
    fireEvent.click(screen.getByRole("button", { name: "Add policy" }));
    const panel = screen.getByRole("dialog", { name: "Add policy" });
    fireEvent.change(within(panel).getByLabelText("Coverage"), { target: { value: "workers compensation" } });
    fireEvent.change(within(panel).getByLabelText("Carrier"), { target: { value: "Carrier B" } });
    fireEvent.change(within(panel).getByLabelText("Policy number"), { target: { value: "WC-2" } });
    fireEvent.change(within(panel).getByLabelText("Starts"), { target: { value: "2026-01-01" } });
    fireEvent.change(within(panel).getByLabelText("Expires"), { target: { value: "2026-12-31" } });
    fireEvent.change(within(panel).getByLabelText("Limit (optional)"), { target: { value: " 500000 " } });
    fireEvent.click(within(panel).getByRole("button", { name: "Record policy" }));
    expect(await screen.findByText("Vendor 1's workers compensation policy WC-2 is recorded.")).toBeTruthy();

    expect(posted(fetch)).toEqual([[
      "/api/vendors/v1/insurance",
      { coverage: "workers compensation", carrier: "Carrier B", policy_number: "WC-2", starts_on: "2026-01-01",
        expires_on: "2026-12-31", limit_amount: "500000" },
    ]]);
  });

  it("renames them", async () => {
    const fetch = server();
    render(<VendorPage me={ME} id="v1" />);
    await screen.findByRole("heading", { level: 1 });

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Vendor One" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Renamed Vendor One.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/vendors/v1/name", { name: "Vendor One" }]]);
  });

  it("shows the vendor opened now, even when an earlier vendor's answer comes back last", async () => {
    const reads = server();
    vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) =>
      path === "/api/vendors/v2" ? new Response(JSON.stringify({ ...VENDOR, id: "v2", name: "Vendor 2" }), { status: 200 })
      : reads(path, init)));
    const release = holdBack((path) => path === "/api/vendors/v1");
    const { rerender } = render(<VendorPage me={ME} id="v1" />);
    rerender(<VendorPage me={ME} id="v2" />);
    await screen.findByRole("heading", { level: 1, name: "Vendor 2" });
    await release();

    expect(screen.getByRole("heading", { level: 1, name: "Vendor 2" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 1, name: "Vendor 1" })).toBeNull();
  });

  it("says when they have nothing on file yet, and when there is no such vendor", async () => {
    server({ ...VENDOR, w9_on_file: false, profile: null, insurance: [], insurance_standing: "no insurance on file", bills: [], work_orders: [] });
    render(<VendorPage me={ME} id="v1" />);
    expect(await screen.findByText("W-9 missing")).toBeTruthy();
    expect(screen.getByTestId("standing").textContent).toBe("Insurance: None on file");
    expect(screen.getByLabelText("Summary").textContent).toBe("TradeNot set1099 vendorNot setPaid byNot setW-9Missing");
    fireEvent.click(screen.getByRole("tab", { name: "Insurance" }));
    expect(screen.getByText("No insurance on file.")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Bills" }));
    expect(screen.getByText("No bills yet.")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Work orders" }));
    expect(screen.getByText("No work order is assigned to them.")).toBeTruthy();
    cleanup();

    server({ detail: "no such vendor" }, 404);
    render(<VendorPage me={ME} id="nope" />);
    expect((await screen.findByRole("alert")).textContent).toBe("no such vendor");
  });
});
