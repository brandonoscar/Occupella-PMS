import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { day } from "../money";
import { StaffHome } from "./StaffHome";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff",
  email: "manager@example.com",
  name: "Manager 1",
  person_id: "s1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
  roles: ["admin", "approve", "disburse"],
};

describe("StaffHome", () => {
  it("downloads today's rent roll, trial balance and leases ending in the next 90 days", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
    render(<StaffHome me={ME} />);
    const today = day(new Date());

    const href = (name: string) => screen.getByRole("link", { name }).getAttribute("href");
    expect(await screen.findByRole("heading", { name: "Synthetic PMC" })).toBeTruthy();
    expect(href("Rent roll (PDF)")).toBe(`/api/reports/trust_report_rent_roll?arg=${today}&format=pdf`);
    expect(href("Trial balance (Excel)")).toBe(
      `/api/reports/trust_report_trial_balance?arg=${today}&format=xlsx`,
    );
    expect(href("Leases ending in 90 days (Excel)")).toBe(
      `/api/reports/trust_report_lease_expirations?arg=${today}&format=xlsx`,
    );
    expect(href("Whole ledger (CSV, zip)")).toBe("/api/ledger.zip");
  });

  it("shows each person's role and login as Occupella shows a state: a badge", async () => {
    const people = [
      { kind: "staff", person_id: "s1", name: "Manager 1", email: "manager@example.com", login_id: "l1", status: "active" },
      { kind: "owner", person_id: "o1", name: "Owner 1", email: "owner@example.com", login_id: "l2", status: "invited" },
      { kind: "tenant", person_id: "t1", name: "Tenant 1", email: null, login_id: null, status: "no login" },
      { kind: "tenant", person_id: "t2", name: "Tenant 2", email: "t2@example.com", login_id: "l3", status: "disabled" },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => new Response(JSON.stringify(path === "/api/people" ? people : []), { status: 200 })),
    );
    render(<StaffHome me={ME} />);

    const row = async (name: string) => screen.findByRole("row", { name: new RegExp(`^${name}`) });
    const badge = async (name: string) => {
      const cells = within(await row(name)).getAllByRole("cell");
      const status = cells[3]!.querySelector("[data-tone]");
      return [cells[1]!.textContent, status?.textContent, status?.getAttribute("data-tone")];
    };
    expect(await badge("Manager 1")).toEqual(["Staff", "Active", "positive"]);
    expect(await badge("Owner 1")).toEqual(["Owner", "Invited", "info"]);
    expect(await badge("Tenant 1")).toEqual(["Tenant", "No login", "neutral"]);
    expect(await badge("Tenant 2")).toEqual(["Tenant", "Disabled", "caution"]);
    // Someone with no email yet is asked for one; nobody invites themself or a disabled login.
    expect(within(await row("Tenant 1")).getByPlaceholderText("Email to invite")).toBeTruthy();
    expect(within(await row("Manager 1")).queryByRole("button")).toBeNull();
    expect(within(await row("Tenant 2")).queryByRole("button")).toBeNull();
    expect(within(await row("Tenant 1")).getByRole("button", { name: "Invite" })).toBeTruthy();
    expect(within(await row("Owner 1")).getByRole("button", { name: "New invite" })).toBeTruthy();
  });

  it("disables a login for good only once staff say yes, and never their own or one not made", async () => {
    const people = [
      { kind: "staff", person_id: "s1", name: "Manager 1", email: "manager@example.com", login_id: "l1", status: "active" },
      { kind: "staff", person_id: "s2", name: "Bookkeeper 1", email: "books@example.com", login_id: "l2", status: "active" },
      { kind: "tenant", person_id: "t1", name: "Tenant 1", email: null, login_id: null, status: "no login" },
    ];
    const fetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") return new Response(null, { status: 204 });
      return new Response(JSON.stringify(path === "/api/people" ? people : []), { status: 200 });
    });
    vi.stubGlobal("fetch", fetch);
    render(<StaffHome me={ME} />);
    const row = async (name: string) => screen.findByRole("row", { name: new RegExp(`^${name}`) });
    const posts = () => fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path]) => path);

    expect(within(await row("Manager 1")).queryByRole("button", { name: "Disable" })).toBeNull();
    expect(within(await row("Tenant 1")).queryByRole("button", { name: "Disable" })).toBeNull();

    const bookkeeper = await row("Bookkeeper 1");
    fireEvent.click(within(bookkeeper).getByRole("button", { name: "Disable" }));
    expect(within(bookkeeper).getByText("Bookkeeper 1 can't sign in again, and is signed out now.")).toBeTruthy();
    fireEvent.click(within(bookkeeper).getByRole("button", { name: "Cancel" }));
    expect(within(bookkeeper).queryByRole("button", { name: "Disable login" })).toBeNull();
    expect(posts()).toEqual([]);

    fireEvent.click(within(bookkeeper).getByRole("button", { name: "Disable" }));
    fireEvent.click(within(bookkeeper).getByRole("button", { name: "Disable login" }));
    expect(await screen.findByText("Bookkeeper 1's login is disabled; they are signed out.")).toBeTruthy();
    expect(posts()).toEqual(["/api/logins/l2/disable"]);
  });
});
