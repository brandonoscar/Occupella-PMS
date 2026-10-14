import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Today } from "../today";
import { TodayPage } from "./TodayPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "manager@example.com", name: "Manager 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC", roles: ["admin", "approve", "disburse"],
};

const QUIET: Today = {
  on: "2026-10-13", late_rent: [], bills_due: [], unreconciled: [], applications: [], unsigned_offers: [],
};

function server(answer: Today | { detail: string }, status = 200) {
  const fetch = vi.fn(async (_path: string) => new Response(JSON.stringify(answer), { status }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const card = (title: string) => screen.getByRole("heading", { name: title }).closest("section") as HTMLElement;

describe("TodayPage", () => {
  it("shows what is late or waiting, each with the way to its page", async () => {
    const fetch = server({
      ...QUIET,
      late_rent: [{
        lease_id: "l1", property: "Property 1", unit: "Unit 1", tenants: ["Tenant A"],
        rent_past_due: "1500.00", oldest_due_on: "2026-10-03",
      }],
      unreconciled: [{
        bank_account_id: "b1", name: "Operating trust", closed_through: "2026-08-31",
        months: ["2026-09"], waiting_for_approval: null,
      }],
    });
    render(<TodayPage me={ME} />);

    expect(await screen.findByText(
      "Unit 1, Property 1 (Tenant A): $1,500.00 of rent past due since 2026-10-03 (10 days)",
    )).toBeTruthy();
    expect(within(card("Late rent (1)")).getByRole("link", { name: "Go to Rent" }).getAttribute("href")).toBe("/rent");
    expect(within(card("Month-end (1)")).getByText("Operating trust: September 2026 not reconciled")).toBeTruthy();
    expect(within(card("Bills due this week (0)")).getByText("No bill is due this week.")).toBeTruthy();
    expect(within(card("Applications waiting (0)")).getByText("No application waits for a decision.")).toBeTruthy();
    expect(within(card("Offers not yet signed (0)")).getByText("No offer waits for signatures.")).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/today");
  });

  it("says what went wrong", async () => {
    server({ detail: "the database is down" }, 503);
    render(<TodayPage me={ME} />);

    expect(await screen.findByText("the database is down")).toBeTruthy();
    expect(screen.queryByText("Loading")).toBeNull();
  });
});
