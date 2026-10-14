import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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

const group = (title: string) => screen.getByRole("region", { name: title });

describe("TodayPage", () => {
  it("lists what is late or waiting, a group for each kind with its count and the way to its page", async () => {
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
    expect(screen.getByText("2 items need someone today.")).toBeTruthy();
    const late = group("Late rent");
    expect(within(late).getByRole("heading").textContent).toBe("Late rent1");
    expect(within(late).getByRole("link", { name: "Open Rent" }).getAttribute("href")).toBe("/rent");
    const monthEnd = group("Month-end");
    expect(within(monthEnd).getByText("Operating trust: September 2026 not reconciled")).toBeTruthy();
    expect(within(monthEnd).getByRole("link", { name: "Open Month-end" }).getAttribute("href")).toBe("/reconcile");
    // A kind with nothing in it is left out.
    expect(screen.getAllByRole("region").map((region) => region.getAttribute("aria-label"))).toEqual([
      "Late rent", "Month-end",
    ]);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0]?.[0]).toBe("/api/today");
  });

  it("goes to a group's page in place", async () => {
    window.history.pushState(null, "", "/today");
    server({ ...QUIET, unreconciled: [{
      bank_account_id: "b1", name: "Operating trust", closed_through: null,
      months: [], waiting_for_approval: "2026-09-30",
    }] });
    render(<TodayPage me={ME} />);

    fireEvent.click(within(await screen.findByRole("region", { name: "Month-end" })).getByRole("link"));
    expect(window.location.pathname).toBe("/reconcile");
  });

  it("says so in one line on a quiet day", async () => {
    server(QUIET);
    render(<TodayPage me={ME} />);

    expect(await screen.findByText("Nothing is late or waiting.")).toBeTruthy();
    expect(screen.getByTestId("empty-state").textContent).toContain("Nothing is late or waiting.");
    expect(screen.queryAllByRole("region")).toEqual([]);
  });

  it("says what went wrong", async () => {
    server({ detail: "the database is down" }, 503);
    render(<TodayPage me={ME} />);

    expect(await screen.findByText("the database is down")).toBeTruthy();
    expect(screen.queryByText("Loading")).toBeNull();
  });
});
