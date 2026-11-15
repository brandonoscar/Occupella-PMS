import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectOnboardingPage, ConnectPayPage, ConnectSavePage } from "./ConnectPages";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function server(...answers: unknown[]) {
  const fetch = vi.fn(async (_path: string, init?: RequestInit) =>
    init?.method === "POST"
      ? new Response(JSON.stringify({ recorded: true }))
      : new Response(JSON.stringify(answers.length > 1 ? answers.shift() : answers[0])));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const PAYMENT = {
  method: "card",
  amount: "1000.00",
  tenant_fee: "30.00",
  total: "1030.00",
  platform_fee: "0.50",
  memo: "Cedar House",
  status: "started",
  dispute: null,
  done: "/pay/done",
};

describe("the fake Connect platform's pages", () => {
  it("finishes onboarding", async () => {
    vi.stubGlobal("location", { ...window.location, hash: "#fake_acct_a1b2c3" });
    const fetch = server({ company: "Demo Co", charges_enabled: false }, { company: "Demo Co", charges_enabled: true });
    render(<ConnectOnboardingPage />);
    expect(await screen.findByRole("heading", { name: "Onboarding for Demo Co" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Finish onboarding" }));
    await vi.waitFor(() => expect(screen.getByTestId("fake-onboarding").textContent).toContain("Finished"));
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/payments/connect/fake/accounts/fake_acct_a1b2c3");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ outcome: "finished" });
  });

  it("takes a card payment, then a dispute and its outcome", async () => {
    vi.stubGlobal("location", { ...window.location, hash: "#p1" });
    const fetch = server(
      PAYMENT,
      { ...PAYMENT, status: "paid" },
      { ...PAYMENT, status: "returned", dispute: "opened" },
      { ...PAYMENT, status: "returned", dispute: "won" },
    );
    render(<ConnectPayPage />);
    expect((await screen.findByTestId("fake-total")).textContent).toBe("$1,030.00 by card ($1,000.00 + $30.00 fee)");
    expect(screen.getByTestId("fake-platform-fee").textContent).toBe("Occupella's fee in it: $0.50");
    fireEvent.click(screen.getByRole("button", { name: "The card paid it" }));
    fireEvent.click(await screen.findByRole("button", { name: "The cardholder disputed it" }));
    fireEvent.click(await screen.findByRole("button", { name: "The company won the dispute" }));
    expect(await screen.findByText("Dispute won")).toBeTruthy();
    const outcomes = fetch.mock.calls.filter(([, init]) => init?.method === "POST")
      .map(([, init]) => JSON.parse(String(init?.body)).outcome);
    expect(outcomes).toEqual(["paid", "disputed", "won"]);
  });

  it("saves a card for autopay", async () => {
    vi.stubGlobal("location", { ...window.location, hash: "#a1" });
    server(
      { tenant: "Taylor Demo", memo: "Cedar House", method: "card", saved: false },
      { tenant: "Taylor Demo", memo: "Cedar House", method: "card", saved: true },
    );
    render(<ConnectSavePage />);
    expect((await screen.findByTestId("fake-save")).textContent).toBe("Taylor Demo saves a card to pay from.");
    fireEvent.click(screen.getByRole("button", { name: "Save this card" }));
    await vi.waitFor(() => expect(screen.getByTestId("fake-save").textContent).toBe("Saved: Test card ••••4242."));
  });
});
