import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PaymentSettings, Waiting } from "../payments";
import { OnlinePaymentsCard, OnlinePaymentsWaiting } from "./OnlinePaymentsCards";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const OFF: PaymentSettings = { online: false, ach_fee_payer: "company", partner: "fake", changed_at: null };

function server(answer: unknown) {
  let now = answer;
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (!Array.isArray(now)) now = { ...(now as object), ...JSON.parse(String(init.body)) };
      else now = [];
      return new Response(null, { status: 204 });
    }
    return new Response(JSON.stringify(now));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("OnlinePaymentsCard", () => {
  it("turns online payments on with the tenant paying the fee", async () => {
    const fetch = server(OFF);
    render(<OnlinePaymentsCard />);
    expect((await screen.findByTestId("online-payments")).textContent).toBe(
      "Off: tenants pay by check, money order or in person.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Edit online payments" }));
    const panel = screen.getByRole("dialog", { name: "Online payments" });
    fireEvent.click(within(panel).getByLabelText("Tenants may pay online"));
    fireEvent.change(within(panel).getByLabelText("Who pays the processing fee on a bank payment"), {
      target: { value: "tenant" },
    });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("On: tenants pay by bank and pay the processing fee, shown before they pay.")).toBeTruthy();
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/payments/settings");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ online: true, ach_fee_payer: "tenant" });
  });

  it("offers no change on a server without a partner", async () => {
    server({ ...OFF, partner: null });
    render(<OnlinePaymentsCard />);
    await screen.findByTestId("online-payments");
    expect(screen.queryByRole("button", { name: "Edit online payments" })).toBeNull();
  });
});

const FEE: Waiting = {
  payment_id: "p1",
  lease_id: "l1",
  memo: "Morgan Demo, Birch Flats Unit A",
  amount: "500.00",
  what: "partner fee",
  company_owes: "4.00",
  bank_account_id: "b1",
  company_holds: "0.00",
  cover_needed: "4.00",
};

describe("OnlinePaymentsWaiting", () => {
  it("shows nothing when nothing waits", async () => {
    server([]);
    const { container } = render(<OnlinePaymentsWaiting refresh={0} />);
    await vi.waitFor(() => expect(container.textContent).toBe(""));
  });

  it("records the company's deposit once staff confirm the amount", async () => {
    const fetch = server([FEE, { ...FEE, payment_id: "p2", what: "payment", company_owes: null }]);
    render(<OnlinePaymentsWaiting refresh={0} />);
    expect(await screen.findByText("The processor kept $4.00; the company's fee account holds $0.00")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Accept past the hold" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Record the company's deposit of $4.00" }));
    fireEvent.click(await screen.findByRole("button", { name: "Record the deposit" }));
    await vi.waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/payments/partner-fees/cover");
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({ bank_account_id: "b1", amount: "4.00" });
  });

  it("still says what the deposit did once nothing waits", async () => {
    server([FEE]);
    render(<OnlinePaymentsWaiting refresh={0} />);
    fireEvent.click(await screen.findByRole("button", { name: "Record the company's deposit of $4.00" }));
    fireEvent.click(await screen.findByRole("button", { name: "Record the deposit" }));
    await vi.waitFor(() => expect(screen.queryByRole("list", { name: "Online payments waiting" })).toBeNull());
    expect(screen.getByRole("status").textContent).toBe("Recorded the company's $4.00.");
  });
});
