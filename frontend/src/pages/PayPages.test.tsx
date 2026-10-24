import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PayDonePage, PayFakePage } from "./PayPages";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("PayDonePage", () => {
  it("says a bank payment takes a few days, or that nothing was paid", () => {
    window.history.replaceState(null, "", "/pay/done?paid=1");
    render(<PayDonePage />);
    expect(screen.getByTestId("pay-done").textContent).toContain("a few business days");
    cleanup();
    window.history.replaceState(null, "", "/pay/done?cancelled=1");
    render(<PayDonePage />);
    expect(screen.getByTestId("pay-done").textContent).toContain("Nothing was taken");
  });
});

describe("PayFakePage", () => {
  it("says what the bank did for the payment in its link", async () => {
    window.history.replaceState(null, "", "/pay/fake#p1");
    let status = "started";
    const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        status = "paid";
        return new Response(JSON.stringify({ recorded: true }));
      }
      return new Response(JSON.stringify({
        amount: "500.00", tenant_fee: "4.03", total: "504.03", memo: "Morgan Demo, Birch Flats Unit A", status,
      }));
    });
    vi.stubGlobal("fetch", fetch);
    render(<PayFakePage />);
    expect((await screen.findByTestId("fake-total")).textContent).toBe("$504.03 ($500.00 + $4.03 fee)");
    fireEvent.click(screen.getByRole("button", { name: "The bank paid it" }));
    expect(await screen.findByText("Paid")).toBeTruthy();
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/payments/fake/p1");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ outcome: "paid" });
  });

  it("can say the tenant left without paying, which reads not finished", async () => {
    window.history.replaceState(null, "", "/pay/fake#p1");
    let status = "started";
    const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        status = JSON.parse(String(init.body)).outcome;
        return new Response(JSON.stringify({ recorded: true }));
      }
      return new Response(JSON.stringify({
        amount: "500.00", tenant_fee: "0.00", total: "500.00", memo: "Morgan Demo, Birch Flats Unit A", status,
      }));
    });
    vi.stubGlobal("fetch", fetch);
    render(<PayFakePage />);
    expect(await screen.findByText("Waiting for your bank")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Left without paying" }));
    expect(await screen.findByText("Not finished")).toBeTruthy();
    expect(status).toBe("expired");
  });
});
