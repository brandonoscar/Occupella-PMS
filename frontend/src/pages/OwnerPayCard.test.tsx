import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OwnerPayView } from "../payments";
import { OwnerPayCard } from "./OwnerPayCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const VIEW: OwnerPayView = {
  available: true,
  why_not: null,
  how_to_pay: null,
  accounts: [
    { account_id: "a1", property: "Aspen Row", bank: "Operating", balance: "900.00", reserve: "500.00", short: "0.00" },
    { account_id: "a2", property: "Birch Flats", bank: "Operating", balance: "100.00", reserve: "500.00", short: "400.00" },
  ],
  payments: [{ id: "p1", started_at: "2026-10-01T12:00:00Z", property: "Birch Flats", amount: "250.00", status: "paid" }],
};

function server(view: OwnerPayView) {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") return new Response(JSON.stringify({ id: "p2", url: "/pay/fake#p2" }), { status: 201 });
    return new Response(JSON.stringify(view));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("OwnerPayCard", () => {
  it("starts with the account below its reserve and what brings it back up, then goes to the partner", async () => {
    const fetch = server(VIEW);
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    render(<OwnerPayCard />);
    expect(((await screen.findByLabelText("Property")) as HTMLSelectElement).value).toBe("a2");
    expect((screen.getByLabelText("Amount") as HTMLInputElement).value).toBe("400.00");
    expect(screen.getByTestId("owner-pay-holds").textContent).toBe(
      "Holds $100.00, with a reserve of $500.00: $400.00 brings it back up.",
    );
    // Another property starts empty when it holds its reserve.
    fireEvent.change(screen.getByLabelText("Property"), { target: { value: "a1" } });
    expect((screen.getByLabelText("Amount") as HTMLInputElement).value).toBe("");
    expect(screen.getByRole("button", { name: "Pay by bank" }).hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "75.50" } });
    fireEvent.click(screen.getByRole("button", { name: "Pay by bank" }));
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith("/pay/fake#p2"));
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/payments/owner/checkout");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ account_id: "a1", amount: "75.50" });
    // Each payment started, with what the bank did.
    const list = screen.getByRole("list", { name: "Payments online" });
    expect(within(list).getByText("10/01/2026 · Birch Flats · $250.00")).toBeTruthy();
    expect(within(list).getByText("Paid")).toBeTruthy();
  });

  it("shows the company's own words on how to send it money when it takes no payment online", async () => {
    server({ ...VIEW, available: false, why_not: "off", how_to_pay: "Wire it to the trust account on file.", payments: [] });
    render(<OwnerPayCard />);
    expect((await screen.findByTestId("owner-pay-off")).textContent).toBe("Wire it to the trust account on file.");
    expect(screen.queryByRole("button", { name: "Pay by bank" })).toBeNull();
    cleanup();
    server({ ...VIEW, available: false, why_not: "off", how_to_pay: null, payments: [] });
    render(<OwnerPayCard />);
    expect((await screen.findByTestId("owner-pay-off")).textContent).toBe("Contact your property manager to send money.");
  });

  it("says when no account is held for them", async () => {
    server({ ...VIEW, accounts: [], payments: [] });
    render(<OwnerPayCard />);
    expect(await screen.findByText("No property accounts are held for you in trust.")).toBeTruthy();
  });

  it("shows a refusal in words", async () => {
    const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return new Response(JSON.stringify({ detail: "no such property account of yours" }), { status: 404 });
      }
      return new Response(JSON.stringify(VIEW));
    });
    vi.stubGlobal("fetch", fetch);
    render(<OwnerPayCard />);
    await screen.findByLabelText("Property");
    fireEvent.click(screen.getByRole("button", { name: "Pay by bank" }));
    expect(await screen.findByText("no such property account of yours")).toBeTruthy();
  });
});
