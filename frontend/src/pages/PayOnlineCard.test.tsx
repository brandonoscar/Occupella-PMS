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
  how_to_pay: null,
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

  it("shows the company's own words on how to pay it when it takes no payment online", async () => {
    server({ ...VIEW, available: false, why_not: "off", how_to_pay: "Checks to Example Co.\nMail them to the office.", payments: [] });
    render(<PayOnlineCard leaseId="l1" />);
    expect((await screen.findByTestId("pay-online-off")).textContent).toBe("Checks to Example Co.\nMail them to the office.");
    expect(screen.getByRole("heading", { name: "How to pay" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Pay online" })).toBeNull();
  });

  it("says to contact the manager when the company wrote nothing, and why a lease on hold can't pay", async () => {
    server({ ...VIEW, available: false, why_not: "off", how_to_pay: null, payments: [] });
    render(<PayOnlineCard leaseId="l1" />);
    expect((await screen.findByTestId("pay-online-off")).textContent).toBe("Contact your property manager to pay.");
    cleanup();
    server({ ...VIEW, available: false, why_not: "paused", how_to_pay: "Checks to Example Co.", payments: [] });
    render(<PayOnlineCard leaseId="l1" />);
    expect((await screen.findByTestId("pay-online-off")).textContent).toBe(
      "Online payments are paused for this lease. Contact your property manager.",
    );
    expect(screen.getByRole("heading", { name: "Pay online" })).toBeTruthy();
  });

  it("won't pay before the fee is shown, nor when it can't be (P-058, H-142)", async () => {
    let answer: (response: Response) => void = () => {};
    const fetch = vi.fn((path: string) => {
      if (path.includes("/fee?")) return new Promise<Response>((resolve) => (answer = resolve));
      return Promise.resolve(new Response(JSON.stringify(VIEW)));
    });
    vi.stubGlobal("fetch", fetch);
    render(<PayOnlineCard leaseId="l1" />);
    await screen.findByLabelText("Amount");
    const pay = () => screen.getByRole("button", { name: "Pay online" }) as HTMLButtonElement;
    // While the quote is on its way the tenant hasn't seen the fee yet.
    await vi.waitFor(() => expect(fetch.mock.calls.some(([path]) => String(path).includes("/fee?"))).toBe(true));
    expect(pay().disabled).toBe(true);
    // The quote failed: the card says so, and Pay online stays off.
    answer(new Response(JSON.stringify({ detail: "the payments partner is not answering" }), { status: 503 }));
    await vi.waitFor(() =>
      expect(screen.getByTestId("pay-fee").textContent).toBe(
        "We couldn't work out the processing fee, so you can't pay online just now. Try again in a few minutes.",
      ),
    );
    expect(pay().disabled).toBe(true);
  });

  it("won't pay an amount that isn't one", async () => {
    server(VIEW);
    render(<PayOnlineCard leaseId="l1" />);
    fireEvent.change(await screen.findByLabelText("Amount"), { target: { value: "12.345" } });
    expect((screen.getByRole("button", { name: "Pay online" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("PayOnlineCard through Stripe Connect", () => {
  const terms = (amount: string) => ({
    connect: true,
    card_max: "10000.00",
    methods: [
      { method: "ach", fee_payer: "company", why_company: null, tenant_fee: "0.00", total: amount },
      { method: "card", fee_payer: "tenant", why_company: null, tenant_fee: "30.00", total: "1230.00" },
    ],
    autopay: [],
  });

  it("lets the tenant pick a card, shows its fee before they go, and pays by card", async () => {
    const fetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(JSON.stringify({ id: "p3", url: "/pay/fake-connect-pay#p3" }), { status: 201 });
      if (path.startsWith("/api/payments/connect/leases/l1")) {
        const amount = new URL(path, "http://x").searchParams.get("amount") ?? "100.00";
        return new Response(JSON.stringify(terms(amount)));
      }
      return new Response(JSON.stringify(VIEW));
    });
    vi.stubGlobal("fetch", fetch);
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    render(<PayOnlineCard leaseId="l1" />);
    const method = (await screen.findByLabelText("Pay by")) as HTMLSelectElement;
    await vi.waitFor(() => expect(screen.getByTestId("pay-fee").textContent).toBe("$1,200.00, no fee"));
    fireEvent.change(method, { target: { value: "card" } });
    await vi.waitFor(() => expect(screen.getByTestId("pay-fee").textContent).toBe("$1,200.00 + $30.00 card fee = $1,230.00"));
    expect(screen.getByText("Credit cards only: a debit or prepaid card can't carry a fee.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Pay online" }));
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith("/pay/fake-connect-pay#p3"));
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/payments/connect/leases/l1/checkout");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ amount: "1200.00", method: "card" });
  });

  it("won't take more than a card payment may be", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) =>
      new Response(JSON.stringify(path.startsWith("/api/payments/connect/") ? terms("20000.00") : VIEW))));
    render(<PayOnlineCard leaseId="l1" />);
    fireEvent.change(await screen.findByLabelText("Pay by"), { target: { value: "card" } });
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "20000.00" } });
    await vi.waitFor(() =>
      expect(screen.getByTestId("pay-fee").textContent).toBe("A card payment is $10,000.00 at most: pay more by bank."));
    expect((screen.getByRole("button", { name: "Pay online" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
