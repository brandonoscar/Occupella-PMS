import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Owner, OwnerRow } from "../directory";
import { holdBack } from "../holdBack";
import type { OwnerMoney } from "../payables";
import type { Paged } from "../rentals";
import { OwnerPage, OwnersPage, agreementLine, lastTwelveMonths } from "./OwnersPage";
import { drawTotal, nextDrawOn } from "./OwnerTop";

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState(null, "", "/");
  window.localStorage.clear();
});

const ME: Me = {
  kind: "staff",
  email: "staff@example.com",
  name: "Staff 1",
  person_id: "s1",
  pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const ACCOUNTS: OwnerMoney[] = [
  {
    account: "a1", owner_id: "o1", owner: "Owner 1", property_id: "pr1", property: "Property 1",
    bank_account: "Operating", balance: "1200.00", reserve: "100.00", unpaid_bills: "300.00", available: "800.00",
    has_agreement: true, payout_method: "check",
  },
  {
    account: "a2", owner_id: "o1", owner: "Owner 1", property_id: "pr2", property: "Property 2",
    bank_account: "Operating", balance: "0.00", reserve: "0.00", unpaid_bills: "0.00", available: "0.00",
    has_agreement: false, payout_method: "check",
  },
];
const NO_CONTACT = {
  email: null, phone: null, alternate_phone: null, address_line_1: null, address_line_2: null,
  city: null, state: null, postal_code: null,
};
const TERMS = {
  account: "a1", starts_on: "2026-01-01", fee_percent: "8", minimum_fee: "25.00", flat_fee: "10.00",
  leasing_fee_percent: "50", reserve: "100.00", approval_limit: "500.00", bill_markup_percent: "0",
};
const OWNER: Owner = {
  id: "o1", name: "Owner 1", archived: false, contact: { ...NO_CONTACT, email: "owner1@example.com" },
  accounts: ACCOUNTS, agreements: [TERMS], portal: "no login", login_email: null,
};
const ROWS: Paged<OwnerRow> = {
  items: [
    { id: "o1", name: "Owner 1", properties: ["Property 1", "Property 2"], held: "1200.00", reserve: "100.00",
      available: "800.00", email: "owner1@example.com", phone: "555-555-0100", archived: false },
    { id: "o2", name: "Owner 2", properties: [], held: "0.00", reserve: "0.00", available: "0.00",
      email: null, phone: null, archived: false },
  ],
  total: 2,
  page: 1,
  per_page: 50,
};
const STATEMENT = {
  title: "Owner statement",
  columns: ["line", "property", "posted_on", "item", "detail", "amount", "balance"],
  rows: [
    ["1", null, null, "period", "2026-01-01 to 2026-10-16", null, null],
    ["2", "Property 1", null, "opening balance", null, null, "0.00"],
    ["3", "Property 1", "2026-03-02", "posting", "Owner contribution", "1200.00", "1200.00"],
    ["4", "Property 1", null, "closing balance", null, null, "1200.00"],
    ["5", null, null, "total", null, null, null],
  ],
};
const PAYEE = { method: "check", bank: null };
const NO_FILES = { documents: [], subjects: { w9: [], receipt: [], move_out_photo: [], owner_document: [] } };

function server(answers: Record<string, unknown> = {}, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      const found = Object.entries(answers).find(([prefix]) => path === prefix || path.startsWith(`${prefix}?`));
      const read = found ? found[1]
        : path === "/api/owners/o1" ? OWNER
        : path === "/api/payouts/owners/o1" ? PAYEE
        : path === "/api/tax-ids/owners/o1" ? { tax_id: null, may_reveal: false }
        : path === "/api/payouts/draw-day" ? { draw_day: null, changed_at: null, changed_by: null }
        : path.startsWith("/api/owners/o1/statement") ? STATEMENT
        : path.startsWith("/api/documents") ? NO_FILES
        : ROWS;
      return new Response(JSON.stringify(read), { status: 200 });
    }
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    return new Response(JSON.stringify(answers[path] ?? {}), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("OwnersPage", () => {
  it("lists each owner once with what they hold, keep back and may pay out, each row opening them", async () => {
    const fetch = server({ "/api/property-groups": { groups: [], properties: [{ id: "p2", name: "Property 2" }] } });
    render(<OwnersPage me={ME} />);

    const row = await screen.findByRole("row", { name: "Owner 1" });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Owner 1", "Property 1, Property 2", "$1,200.00", "$100.00", "$800.00", "owner1@example.com", "555-555-0100",
    ]);
    expect(within(screen.getByRole("main")).getByRole("link", { name: "Pay owners" }).getAttribute("href")).toBe("/owner-draws");
    fireEvent.click(within(row).getAllByRole("cell")[1]!);
    expect(window.location.pathname).toBe("/owners/o1");
    window.history.pushState(null, "", "/owners");

    const picker = screen.getByRole("combobox", { name: "Properties shown" });
    await within(picker).findByRole("option", { name: "Property 2" });
    fireEvent.change(picker, { target: { value: "property:p2" } });
    await vi.waitFor(() =>
      expect(fetch.mock.calls.at(-1)?.[0]).toBe("/api/owners?page=1&archived=false&property=p2"),
    );
  });

  it("shows the owners of the property picked now, even when an earlier property's answer comes back last", async () => {
    server({
      "/api/property-groups": { groups: [], properties: [{ id: "p1", name: "Property 1" }, { id: "p2", name: "Property 2" }] },
      "/api/owners?page=1&archived=false&property=p1": { ...ROWS, items: [ROWS.items[0]], total: 1 },
      "/api/owners?page=1&archived=false&property=p2": {
        ...ROWS, items: [{ ...ROWS.items[1]!, id: "o3", name: "Owner 3", properties: ["Property 2"] }], total: 1,
      },
    });
    const release = holdBack((path) => path.includes("property=p1"));
    render(<OwnersPage me={ME} />);
    await screen.findByRole("row", { name: "Owner 1" });
    const picker = screen.getByRole("combobox", { name: "Properties shown" });
    await within(picker).findByRole("option", { name: "Property 2" });

    fireEvent.change(picker, { target: { value: "property:p1" } });
    fireEvent.change(picker, { target: { value: "property:p2" } });
    await screen.findByRole("row", { name: "Owner 3" });
    await release();

    expect(screen.getByRole("row", { name: "Owner 3" })).toBeTruthy();
    expect(screen.queryByRole("row", { name: "Owner 1" })).toBeNull();
  });

  it("adds an owner and opens their page", async () => {
    const fetch = server({ "/api/setup/owners": { id: "o9" } });
    render(<OwnersPage me={ME} />);
    await screen.findByRole("row", { name: "Owner 1" });

    fireEvent.click(screen.getByRole("button", { name: "Add owner" }));
    const panel = screen.getByRole("dialog", { name: "Add owner" });
    fireEvent.change(within(panel).getByLabelText("Name"), { target: { value: "Owner 9" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add owner" }));

    await vi.waitFor(() => expect(window.location.pathname).toBe("/owners/o9"));
    expect(posted(fetch)).toEqual([["/api/setup/owners", { name: "Owner 9" }]]);
  });

  it("says why an owner wasn't added, and when none is found", async () => {
    server({ "/api/owners": { ...ROWS, items: [], total: 0 } }, { "/api/setup/owners": "an owner is named already" });
    render(<OwnersPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toBe("No owners found.");

    fireEvent.click(screen.getByRole("button", { name: "Add owner" }));
    const panel = screen.getByRole("dialog", { name: "Add owner" });
    fireEvent.change(within(panel).getByLabelText("Name"), { target: { value: "Owner 1" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add owner" }));
    expect((await within(panel).findByRole("alert")).textContent).toBe("an owner is named already");
  });
});

describe("OwnerPage", () => {
  it("sums up the owner: contact details, portal login and the agreements' terms", async () => {
    const fetch = server({ "/api/invites": { url: "https://pms.example/invite#token" } });
    render(<OwnerPage me={ME} id="o1" />);

    expect(await screen.findByRole("heading", { level: 1, name: "Owner 1" })).toBeTruthy();
    expect(screen.getByText("None")).toBeTruthy();
    expect(screen.getByRole("list", { name: "Management agreements" }).textContent).toBe(
      "Property 1: 8% of rent collected, at least $25.00, plus $10.00; leasing fee 50%; reserve $100.00;"
      + " the owner approves bills over $500.00; from 01/01/2026",
    );
    // One click, to the email in their contact details, named before it goes.
    expect(screen.queryByLabelText("Email to sign in with")).toBeNull();
    expect(screen.getByText("Goes to owner1@example.com, the email in their contact details. The link works once.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Invite to the portal" }));
    expect(posted(fetch)).toEqual([]);
    const ask = await screen.findByRole("alertdialog");
    expect(ask.textContent).toContain("Send Owner 1 an invite to the portal at owner1@example.com?");
    fireEvent.click(within(ask).getByRole("button", { name: "Send invite" }));

    expect(((await screen.findByLabelText("Invite link, Owner 1")) as HTMLInputElement).value).toBe(
      "https://pms.example/invite#token",
    );
    expect(posted(fetch)).toEqual([["/api/invites", { kind: "owner", person_id: "o1", email: "owner1@example.com" }]]);
  });

  it("shows the owner opened now, even when an earlier owner's answer comes back last", async () => {
    server({ "/api/owners/o2": { ...OWNER, id: "o2", name: "Owner 2" } });
    const release = holdBack((path) => path === "/api/owners/o1");
    const { rerender } = render(<OwnerPage me={ME} id="o1" />);

    rerender(<OwnerPage me={ME} id="o2" />);
    await screen.findByRole("heading", { level: 1, name: "Owner 2" });
    await release();

    expect(screen.getByRole("heading", { level: 1, name: "Owner 2" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 1, name: "Owner 1" })).toBeNull();
  });

  it("offers a new invite to an owner invited already, none to a disabled login, and says when none has terms", async () => {
    server({ "/api/owners/o1": { ...OWNER, portal: "invited", login_email: "o1@example.com", agreements: [] } });
    render(<OwnerPage me={ME} id="o1" />);
    expect(await screen.findByRole("button", { name: "Send a new invite" })).toBeTruthy();
    expect(screen.getByText("None: no reserve is kept and no fee is taken.")).toBeTruthy();
    cleanup();

    server({ "/api/owners/o1": { ...OWNER, portal: "disabled" } }, { "/api/invites": "x" });
    render(<OwnerPage me={ME} id="o1" />);
    expect(await screen.findByText("Disabled")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /invite/i })).toBeNull();
  });

  it("sets an account's management terms from a side panel", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-10-17T12:00:00Z") });
    const fetch = server({ "/api/setup/agreements": { id: "g9" } });
    render(<OwnerPage me={ME} id="o1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Set terms" }));
    const panel = screen.getByRole("dialog", { name: "Set management terms" });
    expect(within(within(panel).getByLabelText("For")).getAllByRole("option").map((o) => o.textContent)).toEqual(
      ACCOUNTS.map((a) => `Owner 1, ${a.property}`),
    );
    fireEvent.change(within(panel).getByLabelText("Fee, percent of rent and recurring fees collected"), { target: { value: "9" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Set terms" }));
    expect(await screen.findByText(`New terms for Owner 1, ${ACCOUNTS[0]!.property} from 2026-10-17.`)).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(posted(fetch)).toEqual([["/api/setup/agreements", {
      account: ACCOUNTS[0]!.account, starts_on: "2026-10-17", fee_percent: "9", minimum_fee: "0", flat_fee: "0",
      leasing_fee_percent: "0", reserve: "0", approval_limit: "0", bill_markup_percent: "0",
    }]]);
  });

  it("says why an invite was refused", async () => {
    server({}, { "/api/invites": "that email signs in for someone else" });
    render(<OwnerPage me={ME} id="o1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Invite to the portal" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Send invite" }));
    expect((await screen.findByRole("alert")).textContent).toBe("that email signs in for someone else");
  });

  it("lists each property account with what it holds, keeps back and may pay out", async () => {
    server();
    render(<OwnerPage me={ME} id="o1" />);
    fireEvent.click(await screen.findByRole("tab", { name: "Properties" }));

    const row = screen.getByRole("row", { name: "Property 1" });
    expect(within(row).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Property 1", "Operating", "$1,200.00", "$100.00", "$300.00", "$800.00",
    ]);
  });

  it("says when the owner has no property account", async () => {
    server({ "/api/owners/o1": { ...OWNER, accounts: [] } });
    render(<OwnerPage me={ME} id="o1" />);
    fireEvent.click(await screen.findByRole("tab", { name: "Properties" }));
    expect(screen.getByTestId("empty-state").textContent).toContain("No property account yet.");
    fireEvent.click(screen.getByRole("tab", { name: "Financials" }));
    expect(screen.queryByRole("button", { name: "Record contribution" })).toBeNull();
  });

  it("shows the owner's statement over days, to download, and records a contribution", async () => {
    const fetch = server();
    render(<OwnerPage me={ME} id="o1" />);
    fireEvent.click(await screen.findByRole("tab", { name: "Financials" }));

    expect(await screen.findByText("Owner contribution")).toBeTruthy();
    const year = new Date().toISOString().slice(0, 4);
    expect(String(fetch.mock.calls.at(-1)?.[0])).toContain(`/api/owners/o1/statement?start=${year}-01-01&end=`);
    expect(screen.getByRole("link", { name: "PDF" }).getAttribute("href")).toContain("/api/owners/o1/statement/download?");
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-02-01" } });
    await vi.waitFor(() => expect(String(fetch.mock.calls.at(-1)?.[0])).toContain("start=2026-02-01"));

    fireEvent.click(screen.getByRole("button", { name: "Record contribution" }));
    const panel = screen.getByRole("dialog", { name: "Record contribution" });
    fireEvent.change(within(panel).getByLabelText("Into"), { target: { value: "a2" } });
    fireEvent.change(within(panel).getByLabelText("Contribution received"), { target: { value: "75.00" } });
    fireEvent.change(within(panel).getByLabelText("For"), { target: { value: "Roof" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Record contribution" }));

    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getAllByText("$75.00 from Owner 1 recorded.").length).toBeGreaterThan(0);
    const [[path, body]] = posted(fetch) as [[string, Record<string, unknown>]];
    expect(path).toBe("/api/payables/contributions");
    expect(body).toMatchObject({ account: "a2", amount: "75.00", memo: "Roof" });
  });

  it("shows the statement from the day picked now, even when an earlier day's answer comes back last", async () => {
    const posting = (detail: string) => ({
      ...STATEMENT, rows: STATEMENT.rows.map((row) => (row[3] === "posting" ? [...row.slice(0, 4), detail, ...row.slice(5)] : row)),
    });
    server({
      "/api/owners/o1/statement?start=2026-02-01&end=2026-12-31": posting("Gutter cleaning"),
      "/api/owners/o1/statement?start=2026-03-01&end=2026-12-31": posting("Roof repair"),
    });
    const release = holdBack((path) => path.includes("start=2026-02-01"));
    render(<OwnerPage me={ME} id="o1" />);
    fireEvent.click(await screen.findByRole("tab", { name: "Financials" }));
    await screen.findByText("Owner contribution");
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-12-31" } });

    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-02-01" } });
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-03-01" } });
    await screen.findByText("Roof repair");
    await release();

    expect(screen.getByText("Roof repair")).toBeTruthy();
    expect(screen.queryByText("Gutter cleaning")).toBeNull();
  });

  it("takes an inspection fee from one of the owner's properties", async () => {
    const fetch = server();
    render(<OwnerPage me={ME} id="o1" />);
    fireEvent.click(await screen.findByRole("tab", { name: "Financials" }));
    fireEvent.click(screen.getByRole("button", { name: "Take a fee" }));
    const panel = screen.getByRole("dialog", { name: "Take a fee" });
    fireEvent.change(within(panel).getByLabelText("From"), { target: { value: "a2" } });
    fireEvent.change(within(panel).getByLabelText("Fee"), { target: { value: "lease_up" } });
    fireEvent.change(within(panel).getByLabelText("Amount"), { target: { value: "150.00" } });
    fireEvent.change(within(panel).getByLabelText("What it is for"), { target: { value: "Unit 1 leased" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Take fee" }));

    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [[path, body]] = posted(fetch) as [[string, Record<string, unknown>]];
    expect(path).toBe("/api/payables/owner-fees");
    expect(body).toMatchObject({ account: "a2", kind: "lease_up", amount: "150.00", memo: "Unit 1 leased" });
    expect(String(body.request_id)).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("keeps a refused fee's panel open with why", async () => {
    server({}, { "/api/payables/owner-fees": "not enough money: the owner's account would be $25.00 short" });
    render(<OwnerPage me={ME} id="o1" />);
    fireEvent.click(await screen.findByRole("tab", { name: "Financials" }));
    fireEvent.click(screen.getByRole("button", { name: "Take a fee" }));
    const panel = screen.getByRole("dialog", { name: "Take a fee" });
    fireEvent.change(within(panel).getByLabelText("Amount"), { target: { value: "75.00" } });
    fireEvent.change(within(panel).getByLabelText("What it is for"), { target: { value: "Inspection" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Take fee" }));
    expect((await within(panel).findByRole("alert")).textContent).toBe(
      "not enough money: the owner's account would be $25.00 short",
    );
  });

  it("keeps a refused contribution's panel open with why, and says when a statement can't be read", async () => {
    server({}, { "/api/payables/contributions": "that day is in a period already reconciled and closed" });
    render(<OwnerPage me={ME} id="o1" />);
    fireEvent.click(await screen.findByRole("tab", { name: "Financials" }));
    fireEvent.click(screen.getByRole("button", { name: "Record contribution" }));
    const panel = screen.getByRole("dialog", { name: "Record contribution" });
    fireEvent.change(within(panel).getByLabelText("Contribution received"), { target: { value: "5.00" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Record contribution" }));
    expect((await within(panel).findByRole("alert")).textContent).toBe(
      "that day is in a period already reconciled and closed",
    );
    cleanup();

    vi.stubGlobal("fetch", vi.fn(async (path: string) =>
      path.includes("/statement")
        ? new Response(JSON.stringify({ detail: "a statement runs from a day to the same day or a later one" }), { status: 422 })
        : new Response(JSON.stringify(OWNER), { status: 200 })));
    render(<OwnerPage me={ME} id="o1" />);
    fireEvent.click(await screen.findByRole("tab", { name: "Financials" }));
    expect((await screen.findByRole("alert")).textContent).toBe("a statement runs from a day to the same day or a later one");
  });

  it("links each of the last twelve months' statements and the year's income statement, and the owner's files", async () => {
    server();
    render(<OwnerPage me={ME} id="o1" />);
    fireEvent.click(await screen.findByRole("tab", { name: "Statements" }));

    const months = within(screen.getByRole("list", { name: "Monthly statements" })).getAllByRole("link");
    expect(months).toHaveLength(12);
    expect(months[0]!.getAttribute("href")).toMatch(/^\/api\/owners\/o1\/statement\/download\?start=\d{4}-\d{2}-01&end=/);
    expect(screen.getByRole("link", { name: "Income statement this year (PDF)" }).getAttribute("href")).toContain(
      "trust_report_income_statement",
    );
    fireEvent.click(screen.getByRole("tab", { name: "Files" }));
    expect(await screen.findByText(/owner document/i)).toBeTruthy();
  });

  it("says when the owner can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "no such owner" }), { status: 404 })));
    render(<OwnerPage me={ME} id="o1" />);
    expect((await screen.findByRole("alert")).textContent).toBe("no such owner");
  });
});

describe("agreementLine and lastTwelveMonths", () => {
  it("leaves out a minimum or flat fee of nothing", () => {
    expect(agreementLine({ ...TERMS, minimum_fee: "0.00", flat_fee: "0.00" })).toBe(
      "8% of rent collected; leasing fee 50%; reserve $100.00; the owner approves bills over $500.00; from 01/01/2026",
    );
    expect(agreementLine({ ...TERMS, minimum_fee: "0.00", flat_fee: "0.00", bill_markup_percent: "12.5" })).toBe(
      "8% of rent collected; leasing fee 50%; reserve $100.00; 12.5% markup on bills;" +
        " the owner approves bills over $500.00; from 01/01/2026",
    );
  });
  it("counts back twelve whole months from the one before today", () => {
    const months = lastTwelveMonths(new Date(Date.UTC(2026, 0, 15)));
    expect(months[0]).toEqual({ label: "December 2025", start: "2025-12-01", end: "2025-12-31" });
    expect(months[11]).toEqual({ label: "January 2025", start: "2025-01-01", end: "2025-01-31" });
    expect(lastTwelveMonths(new Date(Date.UTC(2024, 2, 1)))[0]?.end).toBe("2024-02-29");
  });
});

describe("the top of an owner's page", () => {
  it("shows how to reach them, how they are paid, their tax ID's last four, their balance and the next draw", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-10-17T12:00:00Z") });
    server({
      "/api/owners/o1": {
        ...OWNER,
        contact: {
          ...NO_CONTACT, email: "owner1@example.com", phone: "555-555-0100",
          address_line_1: "100 Example Street", city: "Sampletown", state: "ZZ", postal_code: "00000",
        },
      },
      "/api/payouts/owners/o1": {
        method: "EFT",
        bank: { routing_number: "001234561", account_type: "checking", last4: "6789", holder_name: "Owner 1",
          recorded_at: "2026-10-01T00:00:00Z", recorded_by: null },
      },
      "/api/tax-ids/owners/o1": {
        tax_id: { id: "t1", kind: "EIN", last4: "4321", recorded_at: "2026-10-01T00:00:00Z", recorded_by: null },
        may_reveal: false,
      },
      "/api/payouts/draw-day": { draw_day: 15, changed_at: null, changed_by: null },
    });
    render(<OwnerPage me={ME} id="o1" />);

    const about = await screen.findByTestId("owner-top");
    expect(within(about).getByRole("link", { name: "555-555-0100" }).getAttribute("href")).toBe("tel:5555550100");
    expect(within(about).getByRole("link", { name: "owner1@example.com" }).getAttribute("href")).toBe("mailto:owner1@example.com");
    expect(about.textContent).toContain("100 Example StreetSampletown, ZZ 00000");
    expect(await within(about).findByText("ACH to Checking ····6789, routing 001234561, Owner 1")).toBeTruthy();
    expect(await within(about).findByText("EIN ••-•••4321")).toBeTruthy();
    expect(screen.getByTestId("owner-balance").textContent).toBe("$1,200.00");
    // The 15th has passed this month: next month's, with what a draw would pay now.
    expect(await within(about).findByText("11/15/2026, $800.00 available now")).toBeTruthy();
  });

  it("says what is missing, and with no email opens their contact details to add one", async () => {
    server({ "/api/owners/o1": { ...OWNER, contact: NO_CONTACT } });
    render(<OwnerPage me={ME} id="o1" />);

    const about = await screen.findByTestId("owner-top");
    expect(await within(about).findByText("No draw day set")).toBeTruthy();
    expect(within(about).getAllByText("None on file")).toHaveLength(4); // phone, email, address, tax ID
    expect(screen.getByText("No email on file, so no invite can go to Owner 1.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Invite to the portal" })).toBeNull();
    expect(screen.queryByLabelText("Street address")).toBeNull();
    const portal = screen.getByText("No email on file, so no invite can go to Owner 1.").parentElement!;
    fireEvent.click(within(portal).getByRole("button", { name: "Edit contact details" }));
    expect(await screen.findByLabelText("Street address")).toBeTruthy();
  });

  it("finds the next draw day this month, or next month once it has passed", () => {
    expect(nextDrawOn(15, "2026-10-10")).toBe("2026-10-15");
    expect(nextDrawOn(15, "2026-10-15")).toBe("2026-10-15");
    expect(nextDrawOn(15, "2026-10-16")).toBe("2026-11-15");
    expect(nextDrawOn(5, "2026-12-20")).toBe("2027-01-05");
    expect(drawTotal({ accounts: [{ ...ACCOUNTS[0]!, available: "800.10" }, { ...ACCOUNTS[1]!, available: "-5.00" }] }))
      .toBe("800.10");
  });
});
