import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { LedgerAccount } from "../generalLedger";
import { holdBack } from "../holdBack";
import type { Paged } from "../rentals";
import { GeneralLedgerPage } from "./GeneralLedgerPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const OWNER: LedgerAccount = {
  bank: "Operating (operating)",
  account: "owner_property: Owner 1 / Property 1",
  prior_balance: "1000.00",
  entries: [
    { posted_at: "2026-10-03 15:00:00", description: "pmc_income: Synthetic PMC - Fee", debit: "80.00",
      credit: "0.00", balance: "920.00" },
  ],
  debits: "80.00",
  credits: "0.00",
  balance: "920.00",
};
const PAGE: Paged<LedgerAccount> = { items: [OWNER], total: 30, page: 1, per_page: 25 };

function server(answer: unknown = PAGE, status = 200) {
  const fetch = vi.fn(async (_path: string) => new Response(JSON.stringify(answer), { status }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("GeneralLedgerPage", () => {
  it("shows each account's prior balance, entries with their running balance and totals", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-10-16T12:00:00Z") });
    const fetch = server();
    render(<GeneralLedgerPage me={ME} />);

    const table = await screen.findByRole("table", { name: "owner_property: Owner 1 / Property 1, Operating (operating)" });
    expect(within(table).getAllByRole("row").slice(1).map((row) =>
      within(row).getAllByRole("cell").map((c) => c.textContent))).toEqual([
      ["Prior balance", "$1,000.00"],
      ["10/03/2026", "pmc_income: Synthetic PMC - Fee", "$80.00", "", "$920.00"],
      ["Total", "$80.00", "$0.00", "$920.00"],
    ]);
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/general-ledger?from=2026-10-01&to=2026-10-16&page=1");
    expect(screen.getByRole("link", { name: "Journal entry" }).getAttribute("href")).toBe("/journal");
    expect(screen.getByRole("link", { name: "CSV" }).getAttribute("href")).toBe(
      "/api/reports/trust_report_general_ledger?arg=2026-10-01&arg=2026-10-16&format=csv",
    );
    expect(screen.getByRole("navigation", { name: "Pages" }).textContent).toContain("1–1 of 30");
  });

  it("asks again for other days, words or the next page", async () => {
    const fetch = server();
    render(<GeneralLedgerPage me={ME} />);
    await screen.findByRole("table");

    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-01-01" } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-01-31" } });
    fireEvent.change(screen.getByLabelText("Find an account"), { target: { value: " Owner 1 " } });
    await vi.waitFor(() =>
      expect(fetch.mock.calls.at(-1)?.[0]).toBe("/api/general-ledger?from=2026-01-01&to=2026-01-31&page=1&q=Owner+1"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await vi.waitFor(() =>
      expect(fetch.mock.calls.at(-1)?.[0]).toBe("/api/general-ledger?from=2026-01-01&to=2026-01-31&page=2&q=Owner+1"),
    );
  });

  it("says when no account is found, and why days were refused", async () => {
    server({ ...PAGE, items: [], total: 0 });
    render(<GeneralLedgerPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No accounts found.");
    cleanup();

    server({ detail: "a general ledger runs from a day to the same day or a later one" }, 422);
    render(<GeneralLedgerPage me={ME} />);
    expect((await screen.findByRole("alert")).textContent).toBe(
      "a general ledger runs from a day to the same day or a later one",
    );
  });

  it("shows the accounts of the search typed now, even when an earlier search's answer comes back last", async () => {
    const OTHER: LedgerAccount = { ...OWNER, account: "owner_property: Owner 2 / Property 2" };
    vi.stubGlobal("fetch", vi.fn(async (path: string) => {
      const items = path.includes("q=Owner+2") ? [OTHER] : path.includes("q=Owner+1") ? [OWNER] : [];
      return new Response(JSON.stringify({ ...PAGE, items }), { status: 200 });
    }));
    const release = holdBack((path) => path.includes("q=Owner+1"));
    render(<GeneralLedgerPage me={ME} />);
    await screen.findByTestId("empty-state");

    fireEvent.change(screen.getByLabelText("Find an account"), { target: { value: "Owner 1" } });
    fireEvent.change(screen.getByLabelText("Find an account"), { target: { value: "Owner 2" } });
    await screen.findByRole("table", { name: "owner_property: Owner 2 / Property 2, Operating (operating)" });
    await release();

    expect(screen.getByRole("table", { name: "owner_property: Owner 2 / Property 2, Operating (operating)" })).toBeTruthy();
    expect(screen.queryByRole("table", { name: "owner_property: Owner 1 / Property 1, Operating (operating)" })).toBeNull();
  });
});
