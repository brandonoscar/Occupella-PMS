import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Payee } from "../payouts";
import { PayeeCard } from "./PayeeCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const BANK = {
  routing_number: "001234561",
  account_type: "checking" as const,
  last4: "6789",
  holder_name: "Owner 1",
  recorded_at: "2026-10-24T00:00:00Z",
  recorded_by: "Staff 1",
};

function server(payee: Payee, refuse?: string) {
  let now = payee;
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 409 });
      const body = JSON.parse(String(init.body));
      if (path.endsWith("/method")) now = { ...now, method: body.method };
      else now = { ...now, bank: { ...BANK, last4: body.account_number.slice(-4), holder_name: body.holder_name } };
      return new Response(JSON.stringify(now), { status: path.endsWith("/method") ? 200 : 201 });
    }
    return new Response(JSON.stringify(now), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posts = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("PayeeCard", () => {
  it("shows an owner paid by check with no bank account, then by ACH", async () => {
    const fetch = server({ method: "check", bank: null });
    render(<PayeeCard kind="owner" id="o1" name="Owner 1" />);
    expect((await screen.findByTestId("payout-method")).textContent).toBe("Check");
    expect(screen.getByTestId("payout-bank").textContent).toBe("None on file");

    fireEvent.change(screen.getByLabelText("Paid by"), { target: { value: "EFT" } });

    expect(await screen.findByText("Owner 1 is paid by ACH.")).toBeTruthy();
    expect((await screen.findByTestId("payout-method")).textContent).toBe("ACH");
    // ACH with no bank account can't go into a file yet, and the card says so.
    expect(screen.getByText(/an ACH file can't pay Owner 1 yet/)).toBeTruthy();
    expect(posts(fetch)).toEqual([["/api/payouts/owners/o1/method", { method: "EFT" }]]);
  });

  it("adds a bank account and shows only its last four", async () => {
    const fetch = server({ method: "EFT", bank: null });
    render(<PayeeCard kind="owner" id="o1" name="Owner 1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Add bank account" }));
    const panel = screen.getByRole("dialog", { name: "Bank account, Owner 1" });
    fireEvent.change(within(panel).getByLabelText("Routing number"), { target: { value: "001234561" } });
    fireEvent.change(within(panel).getByLabelText("Account number"), { target: { value: "123456789" } });
    fireEvent.change(within(panel).getByLabelText("Account type"), { target: { value: "savings" } });
    fireEvent.change(within(panel).getByLabelText("Name on the account"), { target: { value: "Owner 1" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved Owner 1's bank account.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect((await screen.findByTestId("payout-bank")).textContent).toBe("Checking ····6789, routing 001234561, Owner 1");
    expect(screen.getByRole("button", { name: "Change bank account" })).toBeTruthy();
    expect(posts(fetch)).toEqual([["/api/payouts/owners/o1/bank-account", {
      routing_number: "001234561", account_number: "123456789", account_type: "savings", holder_name: "Owner 1",
    }]]);
  });

  it("asks a vendor only for their bank account", async () => {
    const fetch = server({ method: null, bank: BANK });
    render(<PayeeCard kind="vendor" id="v1" name="Vendor 1" />);
    expect((await screen.findByTestId("payout-bank")).textContent).toContain("····6789");
    expect(screen.queryByLabelText("Paid by")).toBeNull();
    expect(screen.queryByTestId("payout-method")).toBeNull();
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/payouts/vendors/v1");
  });

  it("keeps the panel open with why a bank account was refused", async () => {
    server({ method: "check", bank: null }, "this server keeps no account numbers: no PMS_DATA_KEY; ask whoever runs it to set one");
    render(<PayeeCard kind="owner" id="o1" name="Owner 1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Add bank account" }));
    const panel = screen.getByRole("dialog", { name: "Bank account, Owner 1" });
    fireEvent.change(within(panel).getByLabelText("Routing number"), { target: { value: "001234561" } });
    fireEvent.change(within(panel).getByLabelText("Account number"), { target: { value: "123456789" } });
    fireEvent.change(within(panel).getByLabelText("Name on the account"), { target: { value: "Owner 1" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect((await within(panel).findByRole("alert")).textContent).toContain("no PMS_DATA_KEY");
  });
});
