import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { QuickBooksPage, journalHref } from "./QuickBooksPage";
import type { QuickBooksAccount } from "./QuickBooksPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff",
  email: "staff@example.com",
  name: "Staff 1",
  person_id: "s1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const CASH: QuickBooksAccount = {
  ledger_account_id: "L1",
  kind: "bank_cash",
  bank: "Operating trust",
  holder: "Operating trust",
  default_name: "Trust cash:Operating trust",
  name: null,
};

function server(accounts: QuickBooksAccount[]) {
  let now = accounts;
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const { name } = JSON.parse(String(init.body)) as { name: string | null };
      now = now.map((a) => (path.endsWith(`/${a.ledger_account_id}`) ? { ...a, name } : a));
      return new Response(null, { status: 204 });
    }
    return new Response(JSON.stringify(now), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("QuickBooksPage", () => {
  it("links each QuickBooks' journal for the days", () => {
    expect(journalHref("2026-01-01", "2026-01-31", "iif")).toBe(
      "/api/quickbooks/journal?from=2026-01-01&to=2026-01-31&format=iif",
    );
  });

  it("names an account for QuickBooks in a side panel, then puts the PMS's name back", async () => {
    const fetch = server([CASH]);
    render(<QuickBooksPage me={ME} />);
    const table = await screen.findByRole("table", { name: "Account names" });
    expect(within(table).getByRole("row", { name: "Trust cash:Operating trust" })).toBeTruthy();
    const exports = screen.getByRole("link", { name: "For QuickBooks Online (CSV)" });
    expect(exports.getAttribute("href")).toContain("format=csv");

    fireEvent.click(screen.getByRole("button", { name: "Rename Trust cash:Operating trust" }));
    const panel = screen.getByRole("dialog", { name: "Name in QuickBooks" });
    fireEvent.change(within(panel).getByLabelText(/Name in QuickBooks/), { target: { value: "Trust checking" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("row", { name: "Trust checking" })).toBeTruthy();
    const posts = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts[0]?.[0]).toBe("/api/quickbooks/accounts/L1");
    expect(JSON.parse(String(posts[0]?.[1]?.body))).toEqual({ name: "Trust checking" });

    fireEvent.click(screen.getByRole("button", { name: "Rename Trust checking" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Use the PMS's name" }));
    expect(await screen.findByRole("row", { name: "Trust cash:Operating trust" })).toBeTruthy();
  });

  it("says when there is no ledger account yet", async () => {
    server([]);
    render(<QuickBooksPage me={ME} />);
    expect(await screen.findByText("No ledger accounts yet")).toBeTruthy();
  });
});
