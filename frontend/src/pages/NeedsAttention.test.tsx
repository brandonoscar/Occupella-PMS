import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Dashboard } from "../dashboard";
import type { Bill } from "../payables";
import type { DrawRun } from "../payouts";
import type { Today } from "../today";
import type { Reminder } from "./AttentionActions";
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
  deposits_to_receive: [], deposits_to_return: [],
};

const FIGURES: Dashboard = {
  on: "2026-10-13", month_ago: "2026-09-13",
  occupancy: { percent: "92.5", then_percent: "90.0" },
  rent: { collected: "12000.00", due: "15000.00", then_collected: "11500.00", then_due: "14000.00" },
  past_due: { total: "1750.50", tenants: 2, then_total: "2000.50", then_tenants: 3 },
  leases_ending: { within_60: 3, then_within_60: 3 },
  vacant: { units: 2, average_days: 38, then_units: 1, then_average_days: 20 },
  next_draw: { on: "2026-10-15", total: "8400.00", last_total: "7900.00" },
  unreconciled: { accounts: 1, then_accounts: 0 },
};

const LATE = {
  lease_id: "l1", property: "Property 1", unit: "Unit 1", tenants: ["Tenant A"],
  rent_past_due: "1500.00", oldest_due_on: "2026-10-03",
};

const BILL = {
  id: "bill1", vendor: "Vendor 1", reference: "INV-1", property: "Property 1", owner: "Owner 1",
  amount: "120.00", left: "120.00", due_on: "2026-10-16", status: "ready to pay",
} as unknown as Bill;

const RUN: DrawRun = {
  id: "dr1", month: "2026-10-01", prepared_on: "2026-10-15", total: "1200.00",
  lines: [
    { account: "a1", owner_id: "o1", owner: "Owner 1", property: "Property 1", amount: "700.00", available_now: "700.00", method: "EFT" },
    { account: "a2", owner_id: "o2", owner: "Owner 2", property: "Property 2", amount: "500.00", available_now: "450.00", method: "check" },
  ],
};

const PREPARED = {
  id: "prep1", through: "2026-09-30", bank_balance: "5000.00", book_cash: "5000.00",
  prepared_by: "Clerk 1", preparer_id: "s2", prepared_at: "2026-10-02T15:00:00Z",
};

const REMINDER: Reminder = {
  lease_id: "l1", subject: "Rent past due at Unit 1, Property 1",
  body: "Our records show $1,500.00 of rent at Unit 1, Property 1 not paid yet, the oldest due 10/03/2026.",
  to: [{ tenant_id: "t1", name: "Tenant A", to: "tenant.a@example.com" }],
  left_out: [{ name: "Tenant B", why: "no email on file" }],
  sent_today: false,
};

type Call = { method: string; path: string; body: unknown };

// The Dashboard asks for its figures and for what needs someone today; a row's action posts.
function server(today: Today | { detail: string }, status = 200, answers: Record<string, unknown> = {}) {
  const calls: Call[] = [];
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (path === "/api/dashboard" || path === "/api/today") {
      const answer = path === "/api/dashboard" && status === 200 ? FIGURES : today;
      return new Response(JSON.stringify(answer), { status });
    }
    const answer = answers[`${method} ${path}`];
    return answer === undefined
      ? new Response(JSON.stringify({ detail: `no ${method} ${path}` }), { status: 404 })
      : new Response(JSON.stringify(answer), { status: method === "POST" ? 201 : 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return calls;
}

const list = () => screen.findByRole("list", { name: "Needs attention" });
const row = async (what: string) => within(await list()).getByRole("listitem", { name: what });

describe("Dashboard", () => {
  it("lists everything as one list, the longest overdue first, then what comes due soonest", async () => {
    server({
      ...QUIET,
      late_rent: [LATE],
      bills_due: [BILL],
      unreconciled: [{ bank_account_id: "b1", name: "Operating trust", closed_through: "2026-08-31",
        months: ["2026-09"], waiting_for_approval: null }],
      deposits_to_return: [{ lease_id: "l2", property: "Property 1", unit: "Unit 2", tenants: ["Tenant 1"],
        held: "1200.00", return_by: "2026-11-04", standing: "due in 22 days" }],
    });
    render(<DashboardPage me={ME} />);

    const rows = within(await list()).getAllByRole("listitem");
    expect(rows.map((r) => r.getAttribute("aria-label"))).toEqual([
      "Month-end", "Late rent", "Bill due", "Deposit to return",
    ]);
    expect(rows[0]?.textContent).toContain("Operating trust: September 2026 not reconciled");
    expect(rows[1]?.textContent).toContain(
      "Unit 1, Property 1 (Tenant A): $1,500.00 of rent past due since 10/03/2026 (10 days)");
    expect(rows[2]?.textContent).toContain("Vendor 1 INV-1 for Property 1: $120.00, due in 3 days (ready to pay)");
    // Each row carries its main action.
    expect(within(rows[0]!).getByRole("link", { name: "Reconcile" }).getAttribute("href")).toBe("/bank-accounts/b1");
    expect(within(rows[1]!).getByRole("button", { name: "Send reminder" })).toBeTruthy();
    expect(within(rows[2]!).getByRole("button", { name: "Pay bill" })).toBeTruthy();
    expect(within(rows[3]!).getByRole("link", { name: "Open Move-outs" }).getAttribute("href")).toBe("/move-out");
  });

  it("each kind reads as before and opens its own page", async () => {
    server({
      ...QUIET,
      applications: [{ id: "app1", unit: "Unit 3", property: "Property 1", submitted_at: "2026-10-12T10:00:00Z",
        applicants: [{ name: "Applicant 1" }], status: "new" } as unknown as Today["applications"][number]],
      journal_waiting: [{
        id: "e1", event_on: "2026-10-10", memo: "Rent deposited to the wrong property",
        proposed_by: "Bookkeeper 1", proposed_by_me: false, proposed_at: "2026-10-10T15:00:00Z",
        lines: [{ from_account: "a", to_account: "b", amount: "25.00" }],
        status: "waiting", decided_by: null, decided_at: null, reason: null,
      }] as unknown as Today["journal_waiting"],
      vendor_insurance: [{ vendor_id: "v1", vendor: "Vendor 1", coverage: "commercial auto", expires_on: "2026-05-31", standing: "expired" }],
      company_fees: [{ bank_account_id: "b1", bank_account: "Operating trust", held: "150.00", since: "2026-09-10",
        days_held: 33, take_by: "2026-10-05" }],
      signers: [{ bank_account_id: "b2", bank_account: "Deposit trust", signer: null, standing: "no broker on file" }],
      renters_insurance: [{ lease_id: "l3", property: "Property 1", unit: "Unit 1", tenants: "Tenant A", standing: "none on file" }],
      deposits_to_receive: [{ lease_id: "l4", property: "Property 1", unit: "Unit 5", tenants: ["Tenant A", "Tenant B"],
        asked: "1500.00", to_receive: "500.00" }],
      month_to_month: [{ lease_id: "l5", property: "Property 1", unit: "Unit 4", tenants: ["Tenant 1"],
        term_ended_on: "2026-10-10", rolled_on: "2026-10-11" }],
    });
    render(<DashboardPage me={ME} />);

    const items = within(await list()).getAllByRole("listitem");
    const shown = Object.fromEntries(items.map((item) => {
      const link = within(item).getByRole("link");
      return [item.getAttribute("aria-label"), [link.textContent, link.getAttribute("href")]];
    }));
    expect(shown).toEqual({
      "Application": ["Review application", "/applicants/app1"],
      "Journal entry": ["Open journal", "/journal"],
      "Vendor insurance": ["Open vendor", "/vendors/v1"],
      "Company fees in trust": ["Open Banking", "/banking"],
      "Trust account signers": ["Open Staff and roles", "/settings/staff"],
      "Renters insurance": ["Open lease", "/leases/l3"],
      "Deposit to receive": ["Open lease", "/leases/l4"],
      "Month to month": ["Open lease", "/leases/l5"],
    });
    // Insurance expired in May comes before the fees past their day, before the rest of today.
    expect(items.slice(0, 2).map((item) => item.getAttribute("aria-label"))).toEqual([
      "Vendor insurance", "Company fees in trust",
    ]);
    expect(screen.getByText("Rent deposited to the wrong property: $25.00, dated 10/10/2026, proposed by Bookkeeper 1")).toBeTruthy();
    expect(screen.getByText(
      "Unit 4, Property 1 (Tenant 1): its term ended 10/10/2026 with no renewal signed; month to month at the same rent since 10/11/2026",
    )).toBeTruthy();
  });

  it("Send reminder shows who gets it, their emails, the count and who is left out, then sends", async () => {
    const calls = server({ ...QUIET, late_rent: [LATE] }, 200, {
      "GET /api/emails/rent-reminders/l1": REMINDER,
      "POST /api/emails/rent-reminders/l1": [{ id: "m1", name: "Tenant A", to: "tenant.a@example.com" }],
    });
    render(<DashboardPage me={ME} />);

    fireEvent.click(within(await row("Late rent")).getByRole("button", { name: "Send reminder" }));
    const panel = await screen.findByRole("dialog", { name: "Send reminder" });
    const who = await within(panel).findByRole("region", { name: "Who gets it" });
    expect(within(who).getByRole("heading").textContent).toBe("To 1 tenant");
    expect(within(who).getByRole("link", { name: "tenant.a@example.com" }).getAttribute("href")).toBe("mailto:tenant.a@example.com");
    expect(within(who).getByRole("list", { name: "Left out" }).textContent).toBe("Not Tenant B: no email on file");
    expect(within(panel).getByText(REMINDER.body)).toBeTruthy();

    fireEvent.click(within(panel).getByRole("button", { name: "Send to 1 tenant" }));
    expect(await screen.findByRole("status")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Sent the reminder to 1 tenant.");
    expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/api/emails/rent-reminders/l1"]);
    // The list is asked again after it.
    expect(calls.filter((c) => c.path === "/api/today")).toHaveLength(2);
    expect(screen.queryByRole("dialog", { name: "Send reminder" })).toBeNull();
  });

  it("sends no second reminder the same day", async () => {
    server({ ...QUIET, late_rent: [LATE] }, 200, {
      "GET /api/emails/rent-reminders/l1": { ...REMINDER, sent_today: true },
    });
    render(<DashboardPage me={ME} />);

    fireEvent.click(within(await row("Late rent")).getByRole("button", { name: "Send reminder" }));
    const panel = await screen.findByRole("dialog", { name: "Send reminder" });
    expect(await within(panel).findByText("A reminder of this rent went to them today already.")).toBeTruthy();
    expect((within(panel).getByRole("button", { name: "Send to 1 tenant" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("Pay bill asks before money leaves trust, naming the amount, then records it", async () => {
    const calls = server({ ...QUIET, bills_due: [BILL] }, 200, {
      "POST /api/payables/bills/bill1/pay": { transfer_id: "t1" },
    });
    render(<DashboardPage me={ME} />);

    fireEvent.click(within(await row("Bill due")).getByRole("button", { name: "Pay bill" }));
    const panel = await screen.findByRole("dialog", { name: "Pay bill" });
    fireEvent.change(within(panel).getByLabelText("Check number"), { target: { value: "1042" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Pay $120.00" }));
    const question = await screen.findByRole("alertdialog");
    expect(question.textContent).toContain("Pay $120.00 to Vendor 1 for INV-1 by check 1042 on");
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    fireEvent.click(within(question).getByRole("button", { name: "Pay" }));
    expect((await screen.findByRole("status")).textContent).toBe("INV-1 is paid.");
    const paid = calls.find((c) => c.method === "POST");
    expect(paid?.path).toBe("/api/payables/bills/bill1/pay");
    expect(paid?.body).toMatchObject({ method: "check", check_number: "1042", amount: "120.00" });
  });

  it("a bill waiting for its owner opens its page instead", async () => {
    server({ ...QUIET, bills_due: [{ ...BILL, status: "waiting for owner" }] });
    render(<DashboardPage me={ME} />);

    const bill = await row("Bill due");
    expect(within(bill).queryByRole("button")).toBeNull();
    expect(within(bill).getByRole("link", { name: "Open bill" }).getAttribute("href")).toBe("/bills/bill1");
  });

  it("Approve draws pays the prepared draws each owner's way, at most what each has now, after asking", async () => {
    const calls = server({ ...QUIET, owner_draws: RUN }, 200, {
      "POST /api/payables/draws/batch": [
        { account: "a1", owner: "Owner 1", property: "Property 1", amount: "700.00", transfer_id: "x1" },
        { account: "a2", owner: "Owner 2", property: "Property 2", amount: "450.00", transfer_id: "x2" },
      ],
    });
    render(<DashboardPage me={ME} />);

    const draws = await row("Owner draws ready");
    expect(draws.textContent).toContain("Prepared 10/15/2026 for 2 owner accounts, $1,200.00.");
    fireEvent.click(within(draws).getByRole("button", { name: "Approve draws" }));
    const panel = await screen.findByRole("dialog", { name: "Approve draws" });
    expect(within(panel).getByRole("list", { name: "Owner draws ready" }).textContent).toContain(
      "Owner 2, Property 2: $450.00 by Check (prepared $500.00; has less now)");
    fireEvent.click(within(panel).getByRole("button", { name: "Pay $1,150.00" }));
    const question = await screen.findByRole("alertdialog");
    expect(question.textContent).toContain("Pay $1,150.00 to 2 owner accounts on");
    fireEvent.click(within(question).getByRole("button", { name: "Pay" }));
    await screen.findByRole("status");

    const paid = calls.find((c) => c.method === "POST");
    expect(paid?.path).toBe("/api/payables/draws/batch");
    expect(paid?.body).toMatchObject({
      draw_run_id: "dr1", output: "record",
      payouts: [
        { account: "a1", amount: "700.00", method: "EFT" },
        { account: "a2", amount: "450.00", method: "check" },
      ],
    });
  });

  it("Approve reconciliation asks with the figures, and only someone who may approve sees it", async () => {
    const monthEnd = { bank_account_id: "b1", name: "Operating trust", closed_through: "2026-08-31",
      months: [], waiting_for_approval: "2026-09-30", prepared: PREPARED };
    const calls = server({ ...QUIET, unreconciled: [monthEnd] }, 200, {
      "POST /api/reconcile/b1/approve": { id: "r1" },
    });
    render(<DashboardPage me={ME} />);

    fireEvent.click(within(await row("Month-end")).getByRole("button", { name: "Approve reconciliation" }));
    const question = await screen.findByRole("alertdialog");
    expect(question.textContent).toContain(
      "Approve Operating trust's month-end through 09/30/2026, prepared by Clerk 1? The bank shows $5,000.00 and the books $5,000.00.");
    fireEvent.click(within(question).getByRole("button", { name: "Approve" }));
    expect((await screen.findByRole("status")).textContent).toBe("Approved Operating trust's month-end through 09/30/2026.");
    expect(calls.find((c) => c.method === "POST")).toMatchObject({
      path: "/api/reconcile/b1/approve", body: { preparation_id: "prep1" },
    });
    cleanup();

    // Whoever prepared it doesn't approve it; nor does someone without approve.
    server({ ...QUIET, unreconciled: [monthEnd] });
    render(<DashboardPage me={{ ...ME, person_id: "s2" }} />);
    expect((await row("Month-end")).textContent).toContain("You prepared it: someone else approves");
    cleanup();
    server({ ...QUIET, unreconciled: [monthEnd] });
    render(<DashboardPage me={{ ...ME, roles: ["disburse"] }} />);
    expect((await row("Month-end")).textContent).toContain("Needs someone who holds approve");
  });

  it("shows a refusal in words and keeps the panel open", async () => {
    server({ ...QUIET, late_rent: [LATE] }, 200, { "GET /api/emails/rent-reminders/l1": REMINDER });
    render(<DashboardPage me={ME} />);

    fireEvent.click(within(await row("Late rent")).getByRole("button", { name: "Send reminder" }));
    const panel = await screen.findByRole("dialog", { name: "Send reminder" });
    fireEvent.click(await within(panel).findByRole("button", { name: "Send to 1 tenant" }));
    expect(await within(panel).findByText("no POST /api/emails/rent-reminders/l1")).toBeTruthy();
  });

  it("shows the figures that change, each beside a month before and opening its page", async () => {
    window.history.pushState(null, "", "/");
    server(QUIET);
    render(<DashboardPage me={ME} />);

    const figures = await screen.findByRole("list", { name: "Figures" });
    expect(within(figures).getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Occupancy92.5%Up 2.5 points from 09/13/2026", "/properties"],
      ["Rent collected this month$12,000.00 of $15,000.00$11,500.00 of $14,000.00 by 09/13/2026", "/rent-roll"],
      ["Past due$1,750.502 tenants; Down $250.00 from 09/13/2026", "/rent-roll"],
      ["Leases ending in 60 days3Same as 09/13/2026", "/renewals"],
      ["Vacant units2, 38 days vacant on averageUp 1 from 09/13/2026", "/rental-listings"],
      ["Next draw10/15/2026$8,400.00 available to pay now; the last draws prepared came to $7,900.00", "/owner-draws"],
      ["Accounts not reconciled1Up 1 from 09/13/2026", "/banking"],
    ]);
    fireEvent.click(within(figures).getByRole("link", { name: /Next draw/ }));
    expect(window.location.pathname).toBe("/owner-draws");
  });

  it("says so in one line on a quiet day", async () => {
    server(QUIET);
    render(<DashboardPage me={ME} />);

    expect(await screen.findByText("Nothing is late or waiting.")).toBeTruthy();
    expect(screen.getByTestId("empty-state").textContent).toContain("owner draws ready to pay");
    expect(screen.queryByRole("list", { name: "Needs attention" })).toBeNull();
  });

  it("says what went wrong", async () => {
    server({ detail: "the database is down" }, 503);
    render(<DashboardPage me={ME} />);

    expect(await screen.findByText("the database is down")).toBeTruthy();
    expect(screen.queryByText("Loading")).toBeNull();
  });
});
