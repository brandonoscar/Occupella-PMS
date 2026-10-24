import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PayDonePage, PayFakePage, PayFakeSavePage } from "./PayPages";

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

describe("the fake partner's autopay pages", () => {
  it("saves a made-up bank account for an autopay", async () => {
    let saved = false;
    const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        saved = true;
        return new Response(null, { status: 204 });
      }
      return new Response(JSON.stringify({ tenant: "Taylor Tenant", memo: "Cedar House, House", saved }));
    });
    vi.stubGlobal("fetch", fetch);
    vi.stubGlobal("location", { ...window.location, hash: "#a1" });
    render(<PayFakeSavePage />);
    expect((await screen.findByTestId("fake-save")).textContent).toBe("Taylor Tenant saves a bank account to pay from.");
    fireEvent.click(screen.getByRole("button", { name: "Save this bank account" }));
    await vi.waitFor(() => expect(screen.getByTestId("fake-save").textContent).toBe("Saved: Test bank ••••0000."));
    expect(fetch.mock.calls.find(([, init]) => init?.method === "POST")?.[0]).toBe("/api/payments/fake-save/a1");
  });

  it("lists the payments waiting for the bank when it has none to show", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
      { id: "p9", memo: "Cedar House, House", amount: "1200.00", tenant_fee: "0.00", started_at: "2026-11-01T06:00:00Z" },
    ]))));
    vi.stubGlobal("location", { ...window.location, hash: "" });
    render(<PayFakePage />);
    const link = await screen.findByRole("link", { name: "Cedar House, House · $1,200.00 · 11/01/2026" });
    expect(link.getAttribute("href")).toBe("/pay/fake#p9");
  });

  it("says autopay is set up when the partner sends the tenant back", () => {
    vi.stubGlobal("location", { ...window.location, search: "?autopay=1" });
    render(<PayDonePage />);
    expect(screen.getByRole("heading", { name: "Autopay is set up" })).toBeTruthy();
  });
});
