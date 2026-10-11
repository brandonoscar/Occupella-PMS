import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Lease } from "./TenantHome";
import { TenantHome, firstLease } from "./TenantHome";

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
  tenants: ["Tenant 1", "Tenant 2"],
  balance_due: "1500.00",
  in_effect: inEffect,
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
  submitted_at: "2026-10-11T18:05:00Z",
  status: "received",
  status_at: "2026-10-11T18:05:00Z",
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

describe("firstLease", () => {
  it("shows the lease in effect first, else the newest, else none", () => {
    expect(firstLease([lease("old", false), lease("now", true)])?.lease_id).toBe("now");
    expect(firstLease([lease("newer", false), lease("older", false)])?.lease_id).toBe("newer");
    expect(firstLease([])).toBeNull();
  });
});

describe("TenantHome", () => {
  it("shows the lease, what is due, its ledger, and sends a request", async () => {
    const fetch = server({
      "GET /api/tenant/leases": [lease("1", true)],
      "GET /api/tenant/requests": [],
      "GET /api/tenant/leases/1/ledger": LEDGER,
      "POST /api/tenant/requests": REQUEST,
    });
    render(<TenantHome me={ME} />);

    expect((await screen.findByTestId("balance-due")).textContent).toBe("$1,500.00");
    expect(screen.getByText("Tenant 1, Tenant 2")).toBeTruthy();
    expect(await screen.findByText("Balance at the end of 2026-10-11")).toBeTruthy();
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
    });
    const list = screen.getByText("Tap drips").closest("li");
    expect(within(list as HTMLElement).getByText("Received")).toBeTruthy();
  });

  it("takes no request under a lease that has ended", async () => {
    server({
      "GET /api/tenant/leases": [lease("1", false)],
      "GET /api/tenant/requests": [],
      "GET /api/tenant/leases/1/ledger": LEDGER,
    });
    render(<TenantHome me={ME} />);

    expect(await screen.findByText(/This lease has ended/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send request" })).toBeNull();
  });

  it("says when no lease is on file", async () => {
    server({ "GET /api/tenant/leases": [], "GET /api/tenant/requests": [] });
    render(<TenantHome me={ME} />);

    expect(await screen.findByText("No lease is on file for you. Ask Synthetic PMC.")).toBeTruthy();
  });
});
