import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConnectView } from "../connect";
import { ConnectCard } from "./ConnectCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const VIEW: ConnectView = {
  offered: true,
  platform: "fake_connect",
  live: false,
  connected: false,
  account_ends: null,
  charges_enabled: false,
  payouts_enabled: false,
  details_submitted: false,
  requirements_due: [],
  using: false,
  connected_at: null,
  connected_by: null,
  may_change: true,
  fees: [
    { method: "ach", words: "$0.50" },
    { method: "card", words: "$0.50" },
  ],
  card_fee_payer: "company",
  card_fee_changed_at: null,
  rules: [],
  card_max: "10000.00",
};

function server(...views: ConnectView[]) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (path.endsWith("/start")) return new Response(JSON.stringify({ url: "/pay/fake-connect#fake_acct_a1b2c3" }));
    if (path.endsWith("/card-fees")) return new Response(null, { status: 204 });
    if (init?.method === "POST" && !path.endsWith("/refresh")) return new Response(null, { status: 204 });
    return new Response(JSON.stringify(views.length > 1 ? views.shift() : views[0]));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("ConnectCard", () => {
  it("shows nothing on a server that doesn't offer Connect", async () => {
    const fetch = server({ ...VIEW, offered: false });
    const { container } = render(<ConnectCard />);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    await vi.waitFor(() => expect(container.textContent).toBe(""));
  });

  it("lets an admin connect the company's Stripe account on Stripe's own pages", async () => {
    const fetch = server(VIEW);
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    render(<ConnectCard />);
    expect((await screen.findByTestId("connect-status")).textContent).toBe(
      "Not connected. Connect your Stripe account to take rent by bank and card.",
    );
    expect(screen.getByTestId("connect-fees").textContent).toContain("bank $0.50, card $0.50");
    fireEvent.click(screen.getByRole("button", { name: "Connect Stripe" }));
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith("/pay/fake-connect#fake_acct_a1b2c3"));
    expect(fetch.mock.calls.some(([path, init]) => path === "/api/payments/connect/start" && init?.method === "POST")).toBe(true);
  });

  it("says what Stripe still needs, and only an admin connects", async () => {
    server({ ...VIEW, connected: true, account_ends: "c3d4", requirements_due: ["external_account"], may_change: false });
    render(<ConnectCard />);
    expect((await screen.findByTestId("connect-status")).textContent).toBe(
      "Connected, but Stripe needs more before it takes payments. Continue setup.",
    );
    expect(screen.getByTestId("connect-requirements").textContent).toBe("Stripe still needs: external_account.");
    expect(screen.getByText("Setup not finished")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Continue setup" })).toBeNull();
  });

  it("saves who pays the card fee and shows each state's rule", async () => {
    const fetch = server({
      ...VIEW,
      rules: [{ state: "CT", says: "Connecticut General Statutes 42-133ff bans a card surcharge", card: "banned" }],
    });
    render(<ConnectCard />);
    const rules = await screen.findByRole("list", { name: "State rules on card fees" });
    expect(within(rules).getByText(/CT: Connecticut General Statutes 42-133ff bans a card surcharge/)).toBeTruthy();
    expect(within(rules).getByText(/Card fees there are the company's/)).toBeTruthy();
    const save = screen.getByRole("button", { name: "Save card fee" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Who pays the card fee"), { target: { value: "tenant" } });
    fireEvent.click(save);
    await vi.waitFor(() => expect(screen.getByText("Saved who pays the card fee.")).toBeTruthy());
    const post = fetch.mock.calls.find(([path]) => path === "/api/payments/connect/card-fees");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ card_fee_payer: "tenant" });
  });

  it("asks Stripe on the admin's return from onboarding", async () => {
    vi.stubGlobal("location", { ...window.location, search: "?connect=back" });
    const fetch = server({ ...VIEW, connected: true, charges_enabled: true, using: true, account_ends: "a1b2" });
    render(<ConnectCard />);
    expect((await screen.findByTestId("connect-status")).textContent).toContain("Payments go through it");
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/payments/connect/refresh");
  });
});
