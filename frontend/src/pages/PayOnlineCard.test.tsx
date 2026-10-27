import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PayView } from "../payments";
import { PayOnlineCard } from "./PayOnlineCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const VIEW: PayView = {
  available: true,
  why_not: null,
  owed: "1200.00",
  fee_payer: "tenant",
  payments: [{ id: "p1", started_at: "2026-10-01T12:00:00Z", amount: "300.00", tenant_fee: "2.42", status: "paid" }],
};

function server(view: PayView) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") return new Response(JSON.stringify({ id: "p2", url: "/pay/fake#p2" }), { status: 201 });
    if (path.includes("/fee?")) {
      const amount = new URL(path, "http://x").searchParams.get("amount");
      return new Response(JSON.stringify({ amount, tenant_fee: "5.00", total: "1205.00" }));
    }
    return new Response(JSON.stringify(view));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("PayOnlineCard", () => {
  it("starts with the balance, shows the fee and sends the tenant to the partner's page", async () => {
    const fetch = server(VIEW);
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    render(<PayOnlineCard leaseId="l1" />);
    expect(((await screen.findByLabelText("Amount")) as HTMLInputElement).value).toBe("1200.00");
    // The fee line is there at once, blank until its quote comes back.
    await vi.waitFor(() => expect(screen.getByTestId("pay-fee").textContent).toBe("$1,200.00 + $5.00 processing fee = $1,205.00"));
    expect(screen.getByText("Paid")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Pay online" }));
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith("/pay/fake#p2"));
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/payments/leases/l1/checkout");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ amount: "1200.00" });
  });

  it("says why a lease takes no payment online", async () => {
    server({ ...VIEW, available: false, why_not: "your manager doesn't take payments online", payments: [] });
    render(<PayOnlineCard leaseId="l1" />);
    expect((await screen.findByTestId("pay-online-off")).textContent).toBe("your manager doesn't take payments online");
    expect(screen.queryByRole("button", { name: "Pay online" })).toBeNull();
  });

  it("won't pay an amount that isn't one", async () => {
    server(VIEW);
    render(<PayOnlineCard leaseId="l1" />);
    fireEvent.change(await screen.findByLabelText("Amount"), { target: { value: "12.345" } });
    expect((screen.getByRole("button", { name: "Pay online" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
