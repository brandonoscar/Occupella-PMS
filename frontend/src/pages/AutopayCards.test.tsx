import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Autopay, AutopayView } from "../payments";
import { LeaseAutopayCard, TenantAutopayCard } from "./AutopayCards";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ON: Autopay = {
  id: "a1",
  tenant: "Taylor Tenant",
  rule: "balance",
  amount: "1500.00",
  account: "Test bank ••••0000",
  started_at: "2026-10-01T12:00:00Z",
  stopped_at: null,
  stopped: null,
  runs: [
    { due_on: "2026-10-01", amount: "1200.00", tenant_fee: "0.00", status: "paid", skipped: null },
    { due_on: "2026-09-01", amount: null, tenant_fee: null, status: "skipped", skipped: "nothing owed" },
  ],
};

const VIEW: AutopayView = { available: true, why_not: null, fee_payer: "company", next_due: "2026-11-01", autopays: [] };

function server(answer: unknown) {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return _path.endsWith("/stop")
        ? new Response(null, { status: 204 })
        : new Response(JSON.stringify({ id: "a2", url: "/pay/fake-save#a2" }), { status: 201 });
    }
    return new Response(JSON.stringify(answer));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("TenantAutopayCard", () => {
  it("sets up autopay of the balance up to an amount and sends the tenant to save a bank account", async () => {
    const fetch = server(VIEW);
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    render(<TenantAutopayCard leaseId="l1" />);
    expect(await screen.findByText(/next: 11\/01\/2026/)).toBeTruthy();
    expect(screen.getByText(/No fee\./)).toBeTruthy();
    const save = screen.getByRole("button", { name: "Save a bank account and turn on autopay" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Up to"), { target: { value: "1500" } });
    fireEvent.click(save);
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith("/pay/fake-save#a2"));
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/payments/leases/l1/autopay");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ rule: "balance", amount: "1500" });
  });

  it("shows the autopay that is on, its payments, and turns it off once confirmed", async () => {
    const fetch = server({ ...VIEW, autopays: [ON] });
    render(<TenantAutopayCard leaseId="l1" />);
    expect((await screen.findByTestId("autopay")).textContent).toBe(
      "On: pays the balance due, up to $1,500.00, on each rent due day, from Test bank ••••0000",
    );
    expect(screen.getByText("10/01/2026 · $1,200.00")).toBeTruthy();
    expect(screen.getByText("Paid")).toBeTruthy();
    expect(screen.getByText("09/01/2026 · Nothing taken: nothing owed")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Turn off autopay" }));
    fireEvent.click(await screen.findByRole("button", { name: "Turn it off" }));
    await vi.waitFor(() =>
      expect(fetch.mock.calls.some(([path, init]) => path === "/api/payments/leases/l1/autopay/stop" && init?.method === "POST")).toBe(true),
    );
  });

  it("says why autopay can't be set up", async () => {
    server({ ...VIEW, available: false, why_not: "your manager doesn't take payments online" });
    render(<TenantAutopayCard leaseId="l1" />);
    expect((await screen.findByTestId("autopay-off")).textContent).toBe("your manager doesn't take payments online");
  });
});

describe("LeaseAutopayCard", () => {
  it("lists each tenant's autopay and staff turn one off once confirmed", async () => {
    const fetch = server([ON, { ...ON, id: "a0", stopped: "replaced", stopped_at: "2026-09-30T00:00:00Z", runs: [] }]);
    render(<LeaseAutopayCard leaseId="l1" />);
    expect(await screen.findByText(/Taylor Tenant: On: pays the balance due/)).toBeTruthy();
    expect(screen.getByText("Taylor Tenant: Replaced by a newer autopay")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Turn off" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    fireEvent.click(await screen.findByRole("button", { name: "Turn it off" }));
    await vi.waitFor(() =>
      expect(fetch.mock.calls.some(([path, init]) => path === "/api/payments/autopay/a1/stop" && init?.method === "POST")).toBe(true),
    );
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/payments/autopay?lease=l1");
  });

  it("says when no tenant set up autopay", async () => {
    server([]);
    render(<LeaseAutopayCard leaseId="l1" />);
    expect(await screen.findByText("No tenant set up autopay.")).toBeTruthy();
  });
});
