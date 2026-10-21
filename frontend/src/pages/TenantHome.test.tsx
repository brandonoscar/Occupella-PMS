import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Lease } from "./TenantHome";
import { TenantHome, comingDueLabel, firstLease, leaseLabel, monthlyLabel } from "./TenantHome";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "tenant",
  email: "tenant@example.com",
  name: "Tenant 1",
  person_id: "t1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const lease = (id: string, inEffect: boolean): Lease => ({
  lease_id: id,
  property: "Property 1",
  unit: `Unit ${id}`,
  starts_on: "2026-01-01",
  ends_on: inEffect ? null : "2026-06-30",
  monthly_rent: "1500.00",
  rent_changes: [],
  rent_due_day: 1,
  renters_insurance: null,
  tenants: ["Tenant 1", "Tenant 2"],
  balance_due: "1500.00",
  in_effect: inEffect,
  each_month: [],
  coming_due: [],
  deposit_returned: false,
  payments: [],
  files: [],
});

const LEDGER = {
  title: "Tenant ledger through 2026-10-11",
  columns: ["line", "on_date", "item", "detail", "charged", "paid", "balance"],
  rows: [
    ["1", null, "lease", "Unit 1, 2026-01-01, month to month", null, null, null],
    ["2", "2026-01-01", "rent", null, "1500.00", "0.00", "1500.00"],
    ["3", "2026-10-11", "balance", "end of day UTC", "1500.00", "0.00", "1500.00"],
  ],
};

const REQUEST = {
  id: "r1",
  lease_id: "1",
  property: "Property 1",
  unit: "Unit 1",
  filed_by: "Tenant 1",
  summary: "Tap drips",
  entry_allowed: true,
  emergency: false,
  submitted_at: "2026-10-11T18:05:00Z",
  status: "received",
  status_at: "2026-10-11T18:05:00Z",
  visit_on: null,
  visit_window: null,
  photos: 0,
};

// Answers each API path with its canned body, and records what was posted.
function server(answers: Record<string, unknown>) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${path}`;
    if (!(key in answers)) return new Response(JSON.stringify({ detail: key }), { status: 404 });
    return new Response(JSON.stringify(answers[key]), { status: key.startsWith("POST") ? 201 : 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("leaseLabel", () => {
  it("names a lease by its home and first day, and says one ended or is to come", () => {
    expect(leaseLabel(lease("1", true), "2026-10-20")).toBe("Property 1, Unit 1, from 01/01/2026");
    expect(leaseLabel(lease("2", false), "2026-10-20")).toBe("Property 1, Unit 2, from 01/01/2026 (ended)");
    expect(leaseLabel({ ...lease("3", false), starts_on: "2027-01-01", ends_on: null }, "2026-10-20")).toBe(
      "Property 1, Unit 3, from 01/01/2027 (to come)",
    );
  });
});

describe("firstLease", () => {
  it("shows the lease in effect first, else the newest, else none", () => {
    expect(firstLease([lease("old", false), lease("now", true)])?.lease_id).toBe("now");
    expect(firstLease([lease("newer", false), lease("older", false)])?.lease_id).toBe("newer");
    expect(firstLease([])).toBeNull();
  });
});

describe("what comes up", () => {
  it("reads what each month charges, and a charge due later", () => {
    const pet = { what: "Pet rent", kind: "fee" as const, amount: "35.00", starts_on: "2026-02-01", ends_on: null };
    expect(monthlyLabel(pet, "2026-10-13")).toBe("Pet rent: $35.00");
    expect(monthlyLabel({ ...pet, what: "Parking", kind: "credit", ends_on: "2026-12-01" }, "2026-10-13")).toBe(
      "Parking: $35.00 off, until 12/01/2026",
    );
    expect(monthlyLabel({ ...pet, starts_on: "2026-11-01" }, "2026-10-13")).toBe("Pet rent: $35.00, from 11/01/2026");
    const key = { due_on: "2026-10-23", kind: "fee" as const, memo: "Key replacement", open: "50.00" };
    expect(comingDueLabel(key)).toBe("10/23/2026 Key replacement: $50.00");
    expect(comingDueLabel({ ...key, kind: "rent", memo: null, open: "1500.00" })).toBe("10/23/2026 Rent: $1,500.00");
  });
});

describe("TenantHome", () => {
  it("shows what comes up and, once returned, the deposit statement", async () => {
    server({
      "GET /api/tenant/leases": [{
        ...lease("1", true),
        each_month: [
          { what: "Rent", kind: "rent", amount: "1500.00", starts_on: "2026-01-01", ends_on: null },
          { what: "Pet rent", kind: "fee", amount: "35.00", starts_on: "2026-02-01", ends_on: null },
        ],
        coming_due: [{ due_on: "2099-01-01", kind: "fee", memo: "Key replacement", open: "50.00" }],
        deposit_returned: true,
      }],
      "GET /api/tenant/requests": [],
      "GET /api/tenant/renewals": [],
      "GET /api/tenant/leases/1/ledger": LEDGER,
    });
    render(<TenantHome me={ME} />);

    const card = (await screen.findByRole("heading", { name: "Coming up" })).closest("section") as HTMLElement;
    expect(within(card).getByText("Rent: $1,500.00")).toBeTruthy();
    expect(within(card).getByText("Pet rent: $35.00")).toBeTruthy();
    expect(within(card).getByText("01/01/2099 Key replacement: $50.00")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Deposit statement (PDF)" }).getAttribute("href")).toBe(
      "/api/tenant/leases/1/deposit-return/download?format=pdf",
    );
  });

  it("lists the lease as signed, its signing certificate and its documents to download", async () => {
    server({
      "GET /api/tenant/leases": [{
        ...lease("1", true),
        files: [
          { kind: "signed lease", title: "Residential lease", url: "/api/tenant/leases/1/signed-lease" },
          { kind: "signing certificate", title: "Signing certificate", url: "/api/tenant/leases/1/signing-certificate" },
          { kind: "lease document", title: "Pet addendum", url: "/api/tenant/leases/1/documents/d1" },
        ],
      }],
      "GET /api/tenant/requests": [],
      "GET /api/tenant/renewals": [],
      "GET /api/tenant/leases/1/ledger": LEDGER,
    });
    render(<TenantHome me={ME} />);

    const files = await screen.findByRole("list", { name: "Your lease's files" });
    expect(within(files).getAllByRole("link").map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["Residential lease (as signed, PDF)", "/api/tenant/leases/1/signed-lease"],
      ["Signing certificate", "/api/tenant/leases/1/signing-certificate"],
      ["Pet addendum", "/api/tenant/leases/1/documents/d1"],
    ]);
  });

  it("lists each payment with how it came and its receipt, and says one bounced", async () => {
    server({
      "GET /api/tenant/leases": [{
        ...lease("1", true),
        payments: [
          { transfer_id: "tr2", received_on: "2026-10-03", amount: "1500.00", reference: "No. 8", method: "cashier's check", bounced: false },
          { transfer_id: "tr1", received_on: "2026-09-02", amount: "1500.00", reference: null, method: "EFT", bounced: true },
        ],
      }],
      "GET /api/tenant/requests": [],
      "GET /api/tenant/renewals": [],
      "GET /api/tenant/leases/1/ledger": LEDGER,
    });
    render(<TenantHome me={ME} />);

    const list = await screen.findByRole("list", { name: "Payments" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      "10/03/2026 · $1,500.00 · Cashier's check No. 8Receipt (PDF)",
      "09/02/2026 · $1,500.00 · EFTBouncedReceipt (PDF)",
    ]);
    expect(within(items[0] as HTMLElement).getByRole("link", { name: "Receipt (PDF)" }).getAttribute("href")).toBe(
      "/api/tenant/payments/tr2/receipt",
    );
  });

  it("shows neither when nothing comes up and no deposit was returned", async () => {
    server({
      "GET /api/tenant/leases": [lease("1", false)],
      "GET /api/tenant/requests": [],
      "GET /api/tenant/renewals": [],
      "GET /api/tenant/leases/1/ledger": LEDGER,
    });
    render(<TenantHome me={ME} />);

    expect(await screen.findByTestId("balance-due")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Coming up" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Deposit statement (PDF)" })).toBeNull();
  });

  it("shows the lease, what is due, its ledger, and sends a request", async () => {
    const fetch = server({
      "GET /api/tenant/leases": [lease("1", true)],
      "GET /api/tenant/requests": [],
      "GET /api/tenant/renewals": [],
      "GET /api/tenant/leases/1/ledger": LEDGER,
      "POST /api/tenant/requests": REQUEST,
    });
    render(<TenantHome me={ME} />);

    expect((await screen.findByTestId("balance-due")).textContent).toBe("$1,500.00");
    // The balance is a figure in the page's own face, 28px, not code.
    expect(screen.getByTestId("balance-due").className).toContain("text-xl");
    expect(screen.getByTestId("balance-due").className).not.toContain("font-mono");
    expect(screen.getByText("Tenant 1, Tenant 2")).toBeTruthy();
    expect(await screen.findByText("Balance at the end of 10/11/2026")).toBeTruthy();
    const rent = screen.getByRole("row", { name: /^01\/01\/2026 Rent/ });
    expect(within(rent).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "01/01/2026", "Rent", "$1,500.00", "", "$1,500.00",
    ]);
    expect(screen.queryByText("Lease renewal")).toBeNull();
    expect(screen.getByRole("link", { name: "PDF" }).getAttribute("href")).toBe(
      "/api/tenant/leases/1/ledger/download?format=pdf",
    );

    fireEvent.change(screen.getByLabelText("What needs fixing?"), {
      target: { value: "Tap drips" },
    });
    fireEvent.click(screen.getByLabelText("Someone may come in while I'm out"));
    fireEvent.click(screen.getByRole("button", { name: "Send request" }));

    expect(await screen.findByText("Sent to Synthetic PMC.")).toBeTruthy();
    const posted = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(posted?.[1]?.body))).toEqual({
      lease_id: "1",
      summary: "Tap drips",
      entry_allowed: true,
      emergency: false,
      photos: [],
    });
    const list = screen.getByText("Tap drips").closest("li");
    expect(within(list as HTMLElement).getByText("Received")).toBeTruthy();
  });

  it("sends an emergency with its photos, and shows a scheduled visit's day and window", async () => {
    const urgent = { ...REQUEST, id: "r2", summary: "No heat", emergency: true, photos: 2 };
    const scheduled = {
      ...REQUEST, summary: "Tap drips", status: "scheduled", visit_on: "2026-11-03", visit_window: "8-10 am", photos: 1,
    };
    const fetch = server({
      "GET /api/tenant/leases": [lease("1", true)],
      "GET /api/tenant/requests": [scheduled],
      "GET /api/tenant/renewals": [],
      "GET /api/tenant/leases/1/ledger": LEDGER,
      "POST /api/tenant/requests": urgent,
    });
    render(<TenantHome me={ME} />);
    expect((await screen.findByTestId("visit")).textContent).toBe("Visit on 11/03/2026, 8-10 am");
    expect(screen.getByText(/1 photo\./)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("What needs fixing?"), { target: { value: "No heat" } });
    fireEvent.click(screen.getByLabelText("This is an emergency (no heat, no water, a leak, a gas smell)"));
    const png = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], "radiator.png", { type: "image/png" });
    const jpeg = new File([new Uint8Array([0xff, 0xd8, 0xff])], "boiler.jpg", { type: "image/jpeg" });
    fireEvent.change(screen.getByLabelText("Photos (up to 3, JPEG, PNG or PDF)"), { target: { files: [png, jpeg] } });
    expect((await screen.findByTestId("photos-chosen")).textContent).toBe("Photos: radiator, boiler");
    fireEvent.click(screen.getByRole("button", { name: "Send request" }));

    expect(await screen.findByText("Sent to Synthetic PMC as an emergency. If anyone is in danger, call 911.")).toBeTruthy();
    const posted = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(posted?.[1]?.body))).toEqual({
      lease_id: "1",
      summary: "No heat",
      entry_allowed: false,
      emergency: true,
      photos: [
        { title: "radiator", content_type: "image/png", content_base64: "iVBORw==" },
        { title: "boiler", content_type: "image/jpeg", content_base64: "/9j/" },
      ],
    });
    const filed = screen.getByText("No heat").closest("li") as HTMLElement;
    expect(within(filed).getByText("Emergency")).toBeTruthy();
    expect(within(filed).getByText(/2 photos\./)).toBeTruthy();
  });

  it("sends no request with a photo it can't take", async () => {
    const fetch = server({
      "GET /api/tenant/leases": [lease("1", true)],
      "GET /api/tenant/requests": [],
      "GET /api/tenant/renewals": [],
      "GET /api/tenant/leases/1/ledger": LEDGER,
    });
    render(<TenantHome me={ME} />);
    fireEvent.change(await screen.findByLabelText("What needs fixing?"), { target: { value: "Tap drips" } });
    const page = new File(["<html>"], "tap.html", { type: "text/html" });
    fireEvent.change(screen.getByLabelText("Photos (up to 3, JPEG, PNG or PDF)"), { target: { files: [page] } });
    await screen.findByTestId("photos-chosen");
    fireEvent.click(screen.getByRole("button", { name: "Send request" }));

    expect(await screen.findByText("A photo is a JPEG, a PNG or a PDF.")).toBeTruthy();
    expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("says the day of the month rent is due", async () => {
    server({
      "GET /api/tenant/leases": [{ ...lease("1", true), rent_due_day: 10 }],
      "GET /api/tenant/requests": [],
      "GET /api/tenant/renewals": [],
      "GET /api/tenant/leases/1/ledger": LEDGER,
    });
    render(<TenantHome me={ME} />);

    expect(await screen.findByText("Due on the 10th")).toBeTruthy();
  });

  it("shows a rent change to come, with the day notice was given", async () => {
    server({
      "GET /api/tenant/leases": [{
        ...lease("1", true),
        rent_changes: [{ effective_on: "2026-12-01", monthly_rent: "1575.00", notice_given_on: "2026-10-15" }],
      }],
      "GET /api/tenant/requests": [],
      "GET /api/tenant/renewals": [],
      "GET /api/tenant/leases/1/ledger": LEDGER,
    });
    render(<TenantHome me={ME} />);

    expect(await screen.findByText(
      "$1,500.00 a month; $1,575.00 from 12/01/2026 (notice given 10/15/2026)",
    )).toBeTruthy();
  });

  it("shows the lease's renters insurance and records the tenant's own policy", async () => {
    const fetch = server({
      "GET /api/tenant/leases": [{
        ...lease("1", true),
        renters_insurance: {
          required: true, minimum_liability: null, carrier: "Carrier A", policy_number: "RI-1",
          expires_on: "2026-11-30", liability_limit: null, standing: "expires in 20 days", below_minimum: false,
        },
      }],
      "GET /api/tenant/requests": [],
      "GET /api/tenant/renewals": [],
      "GET /api/tenant/leases/1/ledger": LEDGER,
      "POST /api/tenant/leases/1/renters-insurance": { id: "ri2" },
    });
    render(<TenantHome me={ME} />);

    expect((await screen.findByTestId("renters-insurance")).textContent).toBe(
      "Renters insurance required: Carrier A RI-1 through 11/30/2026 (expires in 20 days)",
    );
    fireEvent.change(screen.getByLabelText("Insurer"), { target: { value: "Carrier B" } });
    fireEvent.change(screen.getByLabelText("Policy number"), { target: { value: "RI-2" } });
    fireEvent.change(screen.getByLabelText("Policy starts"), { target: { value: "2026-12-01" } });
    fireEvent.change(screen.getByLabelText("Policy runs out"), { target: { value: "2027-11-30" } });
    fireEvent.change(screen.getByLabelText("Liability coverage (optional)"), { target: { value: "100000.00" } });
    fireEvent.click(screen.getByRole("button", { name: "Record policy" }));

    expect(await screen.findByText("Policy RI-2 from Carrier B recorded.")).toBeTruthy();
    const posted = fetch.mock.calls.find(([path, init]) =>
      init?.method === "POST" && String(path).endsWith("/renters-insurance"));
    expect(JSON.parse(String(posted?.[1]?.body))).toEqual({
      carrier: "Carrier B", policy_number: "RI-2", starts_on: "2026-12-01", expires_on: "2027-11-30",
      liability_limit: "100000.00", landlord_interested_party: false,
    });
  });

  it("takes no request under a lease that has ended", async () => {
    server({
      "GET /api/tenant/leases": [lease("1", false)],
      "GET /api/tenant/requests": [],
      "GET /api/tenant/renewals": [],
      "GET /api/tenant/leases/1/ledger": LEDGER,
    });
    render(<TenantHome me={ME} />);

    expect(await screen.findByText(/This lease has ended/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send request" })).toBeNull();
  });

  it("says when no lease is on file", async () => {
    server({
      "GET /api/tenant/leases": [],
      "GET /api/tenant/requests": [],
      "GET /api/tenant/renewals": [],
    });
    render(<TenantHome me={ME} />);

    expect(await screen.findByText("No lease is on file for you. Ask Synthetic PMC.")).toBeTruthy();
  });
});
