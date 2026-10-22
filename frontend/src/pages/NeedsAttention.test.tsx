import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Today } from "../today";
import type { Dashboard } from "../dashboard";
import { DashboardPage } from "./DashboardPage";

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
  journal_waiting: [], vendor_insurance: [], company_fees: [], signers: [], renters_insurance: [],
  deposits_to_receive: [],
};

const FIGURES: Dashboard = {
  on: "2026-10-13", outstanding: "1750.50", leases_owing: 2, open_work_orders: 3,
  leases_ending: { within_30: 1, within_60: 0, within_90: 2 }, vacant_units: 1, applications_waiting: 4,
};

// The Dashboard asks for its figures and for what needs someone today.
function server(answer: Today | { detail: string }, status = 200) {
  const fetch = vi.fn(async (path: string) =>
    path === "/api/dashboard" && status === 200
      ? new Response(JSON.stringify(FIGURES), { status })
      : new Response(JSON.stringify(answer), { status }),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const group = (title: string) => screen.getByRole("region", { name: title });

describe("Dashboard", () => {
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
    render(<DashboardPage me={ME} />);

    expect(await screen.findByText(
      "Unit 1, Property 1 (Tenant A): $1,500.00 of rent past due since 10/03/2026 (10 days)",
    )).toBeTruthy();
    const late = group("Late rent");
    expect(within(late).getByRole("heading").textContent).toBe("Late rent1");
    expect(within(late).getByRole("link", { name: "Open Rent roll" }).getAttribute("href")).toBe("/rent-roll");
    const monthEnd = group("Month-end");
    expect(within(monthEnd).getByText("Operating trust: September 2026 not reconciled")).toBeTruthy();
    expect(within(monthEnd).getByRole("link", { name: "Open Banking" }).getAttribute("href")).toBe("/banking");
    // A kind with nothing in it is left out.
    expect(screen.getAllByRole("region").map((region) => region.getAttribute("aria-label"))).toEqual([
      "Late rent", "Month-end",
    ]);
    expect(fetch.mock.calls.map(([path]) => path).sort()).toEqual(["/api/dashboard", "/api/today"]);
  });

  it("goes to a group's page in place", async () => {
    window.history.pushState(null, "", "/");
    server({ ...QUIET, unreconciled: [{
      bank_account_id: "b1", name: "Operating trust", closed_through: null,
      months: [], waiting_for_approval: "2026-09-30",
    }] });
    render(<DashboardPage me={ME} />);

    fireEvent.click(within(await screen.findByRole("region", { name: "Month-end" })).getByRole("link"));
    expect(window.location.pathname).toBe("/banking");
  });

  it("lists journal entries waiting to be posted, with the way to the journal", async () => {
    server({ ...QUIET, journal_waiting: [{
      id: "e1", event_on: "2026-10-10", memo: "Rent deposited to the wrong property",
      proposed_by: "Bookkeeper 1", proposed_by_me: false, proposed_at: "2026-10-10T15:00:00Z",
      lines: [{ from_account: "a", to_account: "b", amount: "25.00" }],
      status: "waiting", decided_by: null, decided_at: null, reason: null,
    }] });
    render(<DashboardPage me={ME} />);

    const journal = await screen.findByRole("region", { name: "Journal entries waiting" });
    expect(within(journal).getByText(
      "Rent deposited to the wrong property: $25.00, dated 10/10/2026, proposed by Bookkeeper 1",
    )).toBeTruthy();
    expect(within(journal).getByRole("link", { name: "Open Journal entries" }).getAttribute("href")).toBe("/journal");
  });

  it("lists vendor insurance running out, with the way to the vendors page", async () => {
    server({ ...QUIET, vendor_insurance: [{
      vendor_id: "v1", vendor: "Vendor 1", coverage: "commercial auto", expires_on: "2026-05-31",
      standing: "expired",
    }] });
    render(<DashboardPage me={ME} />);

    const insurance = await screen.findByRole("region", { name: "Vendor insurance" });
    expect(within(insurance).getByText("Vendor 1, commercial auto: expired (05/31/2026)")).toBeTruthy();
    expect(within(insurance).getByRole("link", { name: "Open Vendors" }).getAttribute("href")).toBe("/vendors");
  });

  it("lists the company's fees left in trust, with the day to take them out by", async () => {
    server({ ...QUIET, company_fees: [
      { bank_account_id: "b1", bank_account: "Operating trust", held: "150.00", since: "2026-09-10",
        days_held: 33, take_by: "2026-10-05" },
      { bank_account_id: "b2", bank_account: "Second trust", held: "20.00", since: "2026-10-01",
        days_held: 12, take_by: "2026-10-26" },
    ] });
    render(<DashboardPage me={ME} />);

    const fees = await screen.findByRole("region", { name: "Company fees in trust" });
    expect(within(fees).getByText(
      "Operating trust: $150.00 of the company's fees, the oldest since 09/10/2026 (33 days); past 10/05/2026 to take them out",
    )).toBeTruthy();
    expect(within(fees).getByText(
      "Second trust: $20.00 of the company's fees, the oldest since 10/01/2026 (12 days); take them out by 10/26/2026",
    )).toBeTruthy();
    expect(within(fees).getByRole("link", { name: "Open Banking" }).getAttribute("href")).toBe("/banking");
  });

  it("lists trust accounts with no broker on file and signers' bonds running out", async () => {
    server({ ...QUIET, signers: [
      { bank_account_id: "b1", bank_account: "Operating trust", signer: "Clerk 1", standing: "bond runs out in 5 days" },
      { bank_account_id: "b2", bank_account: "Deposit trust", signer: null, standing: "no broker on file" },
    ] });
    render(<DashboardPage me={ME} />);

    const signers = await screen.findByRole("region", { name: "Trust account signers" });
    expect(within(signers).getByText("Operating trust, Clerk 1: bond runs out in 5 days")).toBeTruthy();
    expect(within(signers).getByText("Deposit trust: no broker on file")).toBeTruthy();
    expect(within(signers).getByRole("link", { name: "Open Staff and roles" }).getAttribute("href")).toBe("/settings/staff");
  });

  it("lists leases whose required renters insurance isn't current", async () => {
    server({ ...QUIET, renters_insurance: [
      { lease_id: "l1", property: "Property 1", unit: "Unit 1", tenants: "Tenant A", standing: "none on file" },
    ] });
    render(<DashboardPage me={ME} />);

    const insurance = await screen.findByRole("region", { name: "Renters insurance" });
    expect(within(insurance).getByText("Unit 1, Property 1 (Tenant A): renters insurance none on file")).toBeTruthy();
    expect(within(insurance).getByRole("link", { name: "Open Rent roll" }).getAttribute("href")).toBe("/rent-roll");
  });

  it("lists leases whose signed offer asked a security deposit not all held yet", async () => {
    server({ ...QUIET, deposits_to_receive: [
      { lease_id: "l1", property: "Property 1", unit: "Unit 1", tenants: ["Tenant A", "Tenant B"], asked: "1500.00", to_receive: "500.00" },
    ] });
    render(<DashboardPage me={ME} />);

    const deposits = await screen.findByRole("region", { name: "Deposits to receive" });
    expect(within(deposits).getByText(
      "Unit 1, Property 1 (Tenant A, Tenant B): $500.00 of the $1,500.00 security deposit the signed lease asked still to receive",
    )).toBeTruthy();
    expect(within(deposits).getByRole("link", { name: "Open Rent roll" }).getAttribute("href")).toBe("/rent-roll");
  });

  it("shows the company's figures, each opening the page where that work is done", async () => {
    window.history.pushState(null, "", "/");
    server(QUIET);
    render(<DashboardPage me={ME} />);

    const figures = await screen.findByRole("list", { name: "Figures" });
    expect(within(figures).getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Outstanding balances$1,750.502 leases owe", "/rent-roll"],
      ["Open work orders3not completed or cancelled", "/work-orders"],
      ["Leases ending31 in 30 days, 0 in 60, 2 in 90", "/draft-leases"],
      ["Vacant units1no lease in effect today", "/applicants"],
      ["Applications waiting4not decided yet", "/applicants"],
    ]);
    fireEvent.click(within(figures).getByRole("link", { name: /Open work orders/ }));
    expect(window.location.pathname).toBe("/work-orders");
  });

  it("says so in one line on a quiet day", async () => {
    server(QUIET);
    render(<DashboardPage me={ME} />);

    expect(await screen.findByText("Nothing is late or waiting.")).toBeTruthy();
    expect(screen.getByTestId("empty-state").textContent).toContain("Nothing is late or waiting.");
    expect(screen.queryAllByRole("region")).toEqual([]);
  });

  it("says what went wrong", async () => {
    server({ detail: "the database is down" }, 503);
    render(<DashboardPage me={ME} />);

    expect(await screen.findByText("the database is down")).toBeTruthy();
    expect(screen.queryByText("Loading")).toBeNull();
  });
});
