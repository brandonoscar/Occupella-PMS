import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BankAccount } from "./BankAccountCard";
import { BankAccountCard, bankAccountLine } from "./BankAccountCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ACCOUNT: BankAccount = {
  holder_name: "Demo Roofing Co",
  routing_number: "001000009",
  account_type: "checking",
  account_last4: "6789",
  recorded_by: "Staff 1",
  recorded_at: "2026-10-23T01:00:00Z",
};

function server(start: BankAccount | null, refuse: string | null = null) {
  let now = start;
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(now), { status: 200 });
    if (refuse) return new Response(JSON.stringify({ detail: refuse }), { status: 403 });
    if (path.endsWith("/remove")) {
      now = null;
      return new Response(null, { status: 204 });
    }
    const body = JSON.parse(String(init.body)) as { account_number: string; account_type: "checking" | "savings" };
    now = { ...ACCOUNT, account_type: body.account_type, account_last4: body.account_number.slice(-4) };
    return new Response(JSON.stringify(now), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const PATH = "/api/vendors/v1/bank-account";

describe("BankAccountCard", () => {
  it("says the account by its last four digits", () => {
    expect(bankAccountLine(ACCOUNT)).toBe("Checking ••••6789, routing 001000009, in the name of Demo Roofing Co");
  });

  it("adds an account typed twice, and the number never shows again", async () => {
    const fetch = server(null);
    render(<BankAccountCard path={PATH} name="Demo Roofing Co" />);

    const card = await screen.findByRole("group", { name: "Bank account, Demo Roofing Co" });
    expect(card.textContent).toContain("No bank account on file.");
    fireEvent.click(within(card).getByRole("button", { name: "Add bank account" }));
    const panel = screen.getByRole("dialog", { name: "Add bank account" });
    expect((within(panel).getByLabelText("Name on the account") as HTMLInputElement).value).toBe("Demo Roofing Co");
    fireEvent.change(within(panel).getByLabelText("Routing number"), { target: { value: "001000009" } });
    fireEvent.change(within(panel).getByLabelText("Account type"), { target: { value: "savings" } });
    fireEvent.change(within(panel).getByLabelText("Account number"), { target: { value: "000123456789" } });
    fireEvent.change(within(panel).getByLabelText("Account number again"), { target: { value: "000123456789" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add bank account" }));

    expect(await screen.findByText("Demo Roofing Co's bank account is set: ••••6789.")).toBeTruthy();
    expect((await screen.findByTestId("bank-account")).textContent).toBe(
      "Savings ••••6789, routing 001000009, in the name of Demo Roofing Co",
    );
    expect(document.body.textContent).not.toContain("000123456789");
    const posted = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posted.map(([path, init]) => [path, JSON.parse(String(init?.body))])).toEqual([[PATH, {
      holder_name: "Demo Roofing Co", routing_number: "001000009", account_type: "savings",
      account_number: "000123456789", account_number_again: "000123456789",
    }]]);
  });

  it("removes one after a question, and shows a refusal in the panel", async () => {
    server(ACCOUNT);
    render(<BankAccountCard path={PATH} name="Demo Roofing Co" />);

    expect((await screen.findByTestId("bank-account")).textContent).toContain("••••6789");
    expect(screen.getByText("Set 10/23/2026 by Staff 1.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }));
    expect(await screen.findByText("Demo Roofing Co's bank account is removed.")).toBeTruthy();
    expect(await screen.findByText(/No bank account on file/)).toBeTruthy();
    cleanup();

    server(ACCOUNT, "only staff who may disburse change where money is paid");
    render(<BankAccountCard path={PATH} name="Demo Roofing Co" />);
    fireEvent.click(await screen.findByRole("button", { name: "Change bank account" }));
    const panel = screen.getByRole("dialog", { name: "Change bank account" });
    fireEvent.change(within(panel).getByLabelText("Routing number"), { target: { value: "001000009" } });
    fireEvent.change(within(panel).getByLabelText("Account number"), { target: { value: "0000555501" } });
    fireEvent.change(within(panel).getByLabelText("Account number again"), { target: { value: "0000555501" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Change bank account" }));
    expect((await within(panel).findByRole("alert")).textContent).toContain("only staff who may disburse");
  });

  it("shows none for an answer that isn't an account", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ name: "Demo Roofing Co" }), { status: 200 })));
    render(<BankAccountCard path={PATH} name="Demo Roofing Co" />);
    expect(await screen.findByText(/No bank account on file/)).toBeTruthy();
  });
});
