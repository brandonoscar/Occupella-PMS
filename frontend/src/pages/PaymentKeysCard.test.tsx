import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type PaymentKeys, PaymentKeysCard, keysLine } from "./PaymentKeysCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const WEBHOOK = "http://localhost:8000/api/payments/webhook/stripe/00000000-0000-0000-0000-000000000001";
const NONE: PaymentKeys = {
  using: "server",
  server_partner: "fake",
  mode: null,
  ends_in: null,
  webhook_url: WEBHOOK,
  webhook_events: ["checkout.session.async_payment_succeeded", "setup_intent.succeeded"],
  changed_at: null,
  changed_by: null,
  can_keep: true,
  may_change: true,
};
const OWN: PaymentKeys = {
  ...NONE,
  using: "own",
  mode: "live",
  ends_in: "A1b2",
  changed_at: "2026-11-04T15:00:00Z",
  changed_by: "Staff 1",
};
// Made up, spelled in pieces so no secret scanner mistakes it for a real key.
const SECRET = "sk_" + "live_" + "Synthetic000000000000A1b2";
const SIGNING = "whsec_" + "Synthetic0000000000000001";

function server(...answers: unknown[]) {
  const fetch = vi.fn(async (_path: string, _init?: RequestInit) =>
    answers.length > 0 && answers[0] === 204
      ? (answers.shift(), new Response(null, { status: 204 }))
      : new Response(JSON.stringify(answers.shift() ?? {}), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("PaymentKeysCard", () => {
  it("says whose account payments go through", () => {
    expect(keysLine(NONE)).toBe("Using the test payments partner.");
    expect(keysLine({ ...NONE, server_partner: "stripe" })).toBe("Using this server's Stripe account.");
    expect(keysLine({ ...NONE, using: "none", server_partner: null })).toBe("Not set. Tenants and owners can't pay online.");
    expect(keysLine(OWN)).toBe("Your Stripe account, key ending in A1b2");
  });

  it("lets an admin enter the keys, which never come back", async () => {
    const fetch = server(NONE, OWN, OWN);
    render(<PaymentKeysCard />);
    expect((await screen.findByTestId("payment-keys")).textContent).toBe("Using the test payments partner.");
    expect(screen.getByTestId("webhook-url").textContent).toBe(WEBHOOK);
    expect((screen.getByRole("textbox", { name: "Webhook events to send" }) as HTMLTextAreaElement).value)
      .toBe("checkout.session.async_payment_succeeded\nsetup_intent.succeeded");

    fireEvent.click(screen.getByRole("button", { name: "Enter keys" }));
    const panel = screen.getByRole("dialog", { name: "Stripe keys" });
    const secret = within(panel).getByLabelText("Secret key");
    expect(secret.getAttribute("type")).toBe("password");
    fireEvent.change(secret, { target: { value: SECRET } });
    fireEvent.change(within(panel).getByLabelText("Webhook signing secret"), { target: { value: SIGNING } });
    fireEvent.click(within(panel).getByRole("button", { name: /^Save keys/ }));

    expect(await screen.findByText("Saved your Stripe keys.")).toBeTruthy();
    expect(fetch.mock.calls[1]?.[0]).toBe("/api/payments/keys");
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ secret_key: SECRET, webhook_secret: SIGNING });
    await vi.waitFor(() =>
      expect(screen.getByTestId("payment-keys").textContent).toBe("Your Stripe account, key ending in A1b2"));
    expect(screen.getByText("Live")).toBeTruthy();
    expect(screen.getByText("Changed 11/04/2026 by Staff 1.")).toBeTruthy();
    expect(document.body.textContent).not.toContain(SECRET);
  });

  it("removes them from the menu, after asking", async () => {
    const fetch = server(OWN, 204, NONE);
    render(<PaymentKeysCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Stripe keys" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Remove keys" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toContain("Remove your Stripe keys? Payments go back to this server's account.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove keys" }));

    expect(await screen.findByText("Removed your Stripe keys.")).toBeTruthy();
    expect(fetch.mock.calls[1]?.[0]).toBe("/api/payments/keys/remove");
  });

  it("shows staff who aren't admins the keys but nothing to change, and a server that can't keep them says so", async () => {
    server({ ...OWN, may_change: false, can_keep: false });
    render(<PaymentKeysCard />);
    expect((await screen.findByTestId("payment-keys")).textContent).toBe("Your Stripe account, key ending in A1b2");
    expect(screen.queryByRole("button", { name: "Replace keys" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Actions for Stripe keys" })).toBeNull();
    expect(screen.getByText("This server can't keep keys. Ask whoever runs it to set PMS_DATA_KEY.")).toBeTruthy();
  });

  it("says keys that can't be read here need entering again", async () => {
    server({ ...OWN, using: "unreadable" });
    render(<PaymentKeysCard />);
    expect(await screen.findByText("Your keys can't be read on this server. Enter them again.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Enter keys" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Actions for Stripe keys" })).toBeTruthy();
  });

  it("says test keys take no payments on a live server, and offers live ones (P-048)", async () => {
    server({ ...OWN, using: "test_refused", mode: "test" });
    render(<PaymentKeysCard />);
    expect(await screen.findByText("Your keys are test keys, which take no payments here. Enter your live keys.")).toBeTruthy();
    expect(screen.queryByText("Test")).toBeNull();
    expect(screen.getByRole("button", { name: "Enter keys" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Actions for Stripe keys" })).toBeTruthy();
  });
});
