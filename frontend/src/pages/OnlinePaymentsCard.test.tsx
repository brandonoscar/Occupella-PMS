import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OnlinePaymentsCard, readView } from "./OnlinePaymentsCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const VIEW = {
  processor: "fake",
  settings: { cards: false, bank_debits: false, recorded_by: null, recorded_at: null },
  payments: [{
    id: "p1", lease_id: "l1", home: "Elm Court, 1A", tenant: "Taylor Demo", amount: "200.00", method: "card",
    started_at: "2026-10-22T10:00:00Z", status: "refused", reason: "a payment hold: refund it at the payment processor",
  }],
};

describe("OnlinePaymentsCard", () => {
  it("turns cards and bank debits on and lists the online payments", async () => {
    let now = VIEW;
    const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        now = { ...now, settings: { ...now.settings, ...JSON.parse(String(init.body)) } };
        return new Response(JSON.stringify(now.settings), { status: 201 });
      }
      return new Response(JSON.stringify(now), { status: 200 });
    });
    vi.stubGlobal("fetch", fetch);
    render(<OnlinePaymentsCard />);

    expect((await screen.findByTestId("online-payments")).textContent).toBe("Not taken");
    expect(screen.getByText(/the test processor \(no money moves\)/)).toBeTruthy();
    const table = screen.getByRole("table", { name: "Online payments" });
    expect(within(table).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "10/22/2026", "Taylor Demo", "Elm Court, 1A", "Card", "$200.00",
      "Refused: being refundeda payment hold: refund it at the payment processor",
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Edit online payments" }));
    const panel = screen.getByRole("dialog", { name: "Online payments" });
    fireEvent.click(within(panel).getByLabelText("Tenants may pay by card"));
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Online payments: Cards only.")).toBeTruthy();
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]))
      .toEqual([["/api/rent/online-payments/settings", { cards: true, bank_debits: false }]]);
    expect((await screen.findByTestId("online-payments")).textContent).toBe("Cards only");
  });

  it("lists each lease's autopay and stops one with why", async () => {
    let autopay = [{
      id: "a1", lease_id: "l1", home: "Elm Court, 1A", tenant: "Taylor Demo", method: "card",
      since: "2026-10-22T10:00:00Z", status: "on", label: "Card ••••4242", reason: null as string | null,
    }];
    const fetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        autopay = autopay.map((a) => ({ ...a, status: "stopped", reason: JSON.parse(String(init.body)).reason }));
        return new Response(null, { status: 204 });
      }
      return new Response(JSON.stringify(path.endsWith("/autopay") ? autopay : VIEW), { status: 200 });
    });
    vi.stubGlobal("fetch", fetch);
    render(<OnlinePaymentsCard />);

    const table = await screen.findByRole("table", { name: "Autopay" });
    expect(within(table).getAllByRole("cell").map((c) => c.textContent).slice(0, 5)).toEqual([
      "10/22/2026", "Taylor Demo", "Elm Court, 1A", "Card ••••4242", "On",
    ]);
    fireEvent.click(within(table).getByRole("button", { name: "Actions for autopay of Taylor Demo" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Stop autopay" }));
    const panel = screen.getByRole("dialog", { name: "Stop autopay" });
    fireEvent.change(within(panel).getByLabelText("Why"), { target: { value: "Card expired" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Stop autopay" }));

    expect(await screen.findByText("Autopay of Taylor Demo is stopped.")).toBeTruthy();
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]))
      .toEqual([["/api/rent/autopay/a1/stop", { reason: "Card expired" }]]);
    expect(await screen.findByText("Card expired")).toBeTruthy();
  });

  it("says when the server has no processor, and reads another answer as nothing set up", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...VIEW, processor: null, payments: [] }), { status: 200 })));
    render(<OnlinePaymentsCard />);
    expect(await screen.findByText(/no payment processor yet/)).toBeTruthy();
    expect(readView({ name: "a company" }).processor).toBeNull();
  });
});
