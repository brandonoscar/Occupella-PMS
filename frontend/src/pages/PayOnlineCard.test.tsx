import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OnlinePayment } from "../onlinePayments";
import { takenLine } from "../onlinePayments";
import { FakeCheckoutPage } from "./FakeCheckoutPage";
import { PayOnlineCard, readView } from "./PayOnlineCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const PAID: OnlinePayment = {
  id: "p1", lease_id: "l1", home: "Elm Court, 1A", tenant: "Taylor Demo", amount: "1500.00", method: "card",
  started_at: "2026-10-22T10:00:00Z", status: "succeeded", reason: null,
};

function server(view: unknown, refuse = "") {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(view), { status: 200 });
    if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 422 });
    return new Response(JSON.stringify({ payment_id: "p2", url: "/pay#fake_abc" }), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("PayOnlineCard", () => {
  it("starts a payment of what the lease owes and goes to the processor's page", async () => {
    const fetch = server({ methods: ["card", "bank debit"], payments: [PAID, { ...PAID, id: "p0", lease_id: "l9" }] });
    const go = vi.fn();
    render(<PayOnlineCard leaseId="l1" owed="1500.00" go={go} />);

    expect((await screen.findByLabelText("Amount") as HTMLInputElement).value).toBe("1500.00");
    // Only this lease's online payments, with how each stands.
    const list = screen.getByRole("list", { name: "Online payments" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["10/22/2026 · $1,500.00 · CardPaid"]);
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "700.00" } });
    fireEvent.change(screen.getByLabelText("Pay by"), { target: { value: "bank debit" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue to payment" }));

    await vi.waitFor(() => expect(go).toHaveBeenCalledWith("/pay#fake_abc"));
    const posted = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posted.map(([path, init]) => [path, JSON.parse(String(init?.body))])).toEqual([
      ["/api/tenant/leases/l1/online-payments", { amount: "700.00", method: "bank debit" }],
    ]);
  });

  it("says why a payment was refused, and shows nothing when nothing is offered", async () => {
    server({ methods: ["card"], payments: [] }, "pay at most what the lease owes now, $1,500.00");
    render(<PayOnlineCard leaseId="l1" owed="1500.00" go={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Continue to payment" }));
    expect((await screen.findByRole("alert")).textContent).toContain("pay at most what the lease owes now");
    cleanup();

    server({ methods: [], payments: [] });
    const { container } = render(<PayOnlineCard leaseId="l1" owed="1500.00" go={vi.fn()} />);
    await vi.waitFor(() => expect(container.textContent).toBe(""));
    expect(readView({ name: "not a view" })).toEqual({ methods: [], payments: [] });
  });

  it("offers no form while the lease owes nothing, still listing its payments", async () => {
    server({ methods: ["card"], payments: [{ ...PAID, status: "started" }] });
    render(<PayOnlineCard leaseId="l1" owed="0.00" go={vi.fn()} />);
    expect(await screen.findByText("Processing")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Continue to payment" })).toBeNull();
  });

  it("words what a company takes", () => {
    expect([true, false].flatMap((cards) => [true, false].map((bank_debits) => takenLine({ cards, bank_debits })))).toEqual([
      "Cards and bank debits", "Cards only", "Bank debits only", "Not taken",
    ]);
  });
});

describe("FakeCheckoutPage", () => {
  it("says what the processor would and goes back home", async () => {
    window.history.replaceState(null, "", "/pay#fake_abc");
    const fetch = vi.fn(async (_path: string, _init?: RequestInit) => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    render(<FakeCheckoutPage />);

    fireEvent.click(screen.getByRole("button", { name: "Pay" }));
    await vi.waitFor(() => expect(window.location.pathname).toBe("/"));
    expect(fetch.mock.calls.map(([path, init]) => [path, JSON.parse(String(init?.body))])).toEqual([
      ["/api/tenant/online-payments/fake/fake_abc", { outcome: "succeeded" }],
    ]);
  });

  it("takes an applicant paying their application's fee back to their application", async () => {
    window.history.replaceState(null, "", "/pay#fake_fee_abc");
    const fetch = vi.fn(async (_path: string, _init?: RequestInit) => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    render(<FakeCheckoutPage />);

    expect(screen.getByRole("heading", { name: "Your application fee" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Pay" }));
    await vi.waitFor(() => expect(window.location.pathname).toBe("/application"));
    expect(fetch.mock.calls.map(([path, init]) => [path, JSON.parse(String(init?.body))])).toEqual([
      ["/api/application/fee/fake/fake_fee_abc", { outcome: "succeeded" }],
    ]);
  });
});

