import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { companyDay } from "../money";
import type { LeaseDetail, RentRollRow } from "../rentals";
import type { Unapplied } from "../unapplied";
import { cameAs, waited } from "../unapplied";
import { UnappliedCash } from "./UnappliedCashCard";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const HELD: Unapplied = {
  id: "u1",
  bank_account_id: "b1",
  bank_account: "Operating trust",
  received_on: "2026-10-01",
  payer: "J. Sample",
  memo: "Check with no unit on it",
  method: "check",
  reference: "4410",
  amount: "500.00",
  left: "500.00",
  age_days: 35,
};
const ROW: RentRollRow = {
  id: "l2", property: { id: "p1", name: "Birch Flats" }, unit: { id: "u1", name: "3C" }, tenants: ["Rowan Demo"],
  people: [{ id: "t1", name: "Rowan Demo" }], starts_on: "2026-01-01", ends_on: null, status: "current",
  days_left: null, monthly_rent: "900.00", deposit: "0.00", owed: "900.00",
  details: { bedrooms: null, bathrooms: null, square_feet: null, market_rent: null },
};
const LEASE = {
  id: "l2", property: "Birch Flats", unit: "3C", tenants: [{ id: "t1", name: "Rowan Demo" }],
  owners: [{ account: "a1", owner: "Owner 1", percent: null }, { account: "a2", owner: "Owner 2", percent: null }],
  owed: "900.00", on_hold: false,
} as unknown as LeaseDetail;
const BANKS = [{ id: "b1", name: "Operating trust" }];

// GETs answer `answers[path]`; POSTs answer `answers[path]` (201), or `refuse[path]` (409).
function server(answers: Record<string, unknown>, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (path in refuse) return new Response(JSON.stringify({ detail: refuse[path] }), { status: 409 });
    if (!(path in answers)) return new Response(JSON.stringify({ detail: `no ${path}` }), { status: 404 });
    return new Response(JSON.stringify(answers[path]), { status: init?.method === "POST" ? 201 : 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("unapplied cash words", () => {
  it("says how it came and how long it has waited", () => {
    expect(cameAs(HELD)).toBe("Check 4410");
    expect(cameAs({ method: "cash", reference: null })).toBe("Cash");
    expect([waited(0), waited(1), waited(12)]).toEqual(["Today", "1 day", "12 days"]);
  });
});

describe("UnappliedCash", () => {
  it("lists what is held with how long it has waited, a month or more marked", async () => {
    server({ "/api/unapplied": [HELD, { ...HELD, id: "u2", age_days: 2, payer: "K. Sample", memo: null }] });
    render(<UnappliedCash banks={BANKS} refresh={0} changed={() => undefined} />);

    const table = await screen.findByRole("table", { name: "Unapplied cash" });
    const old = within(table).getByRole("row", { name: "J. Sample $500.00" });
    expect(within(old).getAllByRole("cell").map((c) => c.textContent).slice(0, 7)).toEqual([
      "10/01/2026", "J. Sample", "Check 4410", "Check with no unit on it", "$500.00", "$500.00", "35 days",
    ]);
    expect(within(old).getByText("35 days").closest("[data-tone]")?.getAttribute("data-tone")).toBe("caution");
    const recent = within(table).getByRole("row", { name: "K. Sample $500.00" });
    expect(within(recent).getByText("2 days").closest("[data-tone]")).toBeNull();
  });

  it("says none is held, and never that none is when the list can't load", async () => {
    server({ "/api/unapplied": [] });
    render(<UnappliedCash banks={BANKS} refresh={0} changed={() => undefined} />);
    expect(await screen.findByText("None held.")).toBeTruthy();
    cleanup();

    server({});
    render(<UnappliedCash banks={BANKS} refresh={0} changed={() => undefined} />);
    expect((await screen.findByRole("alert")).textContent).toBe("Unapplied cash couldn't load: no /api/unapplied");
    expect(screen.queryByText("None held.")).toBeNull();
  });

  it("records money that came in as it came", async () => {
    const changed = vi.fn();
    const fetch = server({ "/api/unapplied": [] });
    render(<UnappliedCash banks={BANKS} refresh={0} changed={changed} />);
    await screen.findByText("None held.");

    fireEvent.click(screen.getByRole("button", { name: "Record unapplied cash" }));
    const panel = screen.getByRole("dialog", { name: "Record unapplied cash" });
    expect(within(panel).queryByLabelText("Trust bank account")).toBeNull();  // the only one
    fireEvent.change(within(panel).getByLabelText("Amount"), { target: { value: "500.00" } });
    fireEvent.change(within(panel).getByLabelText("From"), { target: { value: "J. Sample" } });
    fireEvent.change(within(panel).getByLabelText("How it came"), { target: { value: "money order" } });
    fireEvent.change(within(panel).getByLabelText("Check or reference number (optional)"), { target: { value: "MO 7" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Record unapplied cash" }));

    expect(await screen.findByText("Recorded $500.00 from J. Sample as unapplied cash.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(changed).toHaveBeenCalled();
    expect(posted(fetch)).toEqual([["/api/unapplied", {
      request_id: expect.any(String), amount: "500.00", received_on: companyDay(), payer: "J. Sample",
      method: "money order", reference: "MO 7", memo: null,
    }]]);
  });

  it("asks which operating account when the company has more than one", async () => {
    const fetch = server({ "/api/unapplied": [] });
    render(<UnappliedCash banks={[...BANKS, { id: "b2", name: "Second trust" }]} refresh={0} changed={() => undefined} />);
    await screen.findByText("None held.");
    fireEvent.click(screen.getByRole("button", { name: "Record unapplied cash" }));
    const panel = screen.getByRole("dialog", { name: "Record unapplied cash" });
    fireEvent.change(within(panel).getByLabelText("Amount"), { target: { value: "5.00" } });
    fireEvent.change(within(panel).getByLabelText("From"), { target: { value: "X" } });
    fireEvent.change(within(panel).getByLabelText("Trust bank account"), { target: { value: "b2" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Record unapplied cash" }));
    await vi.waitFor(() => expect(posted(fetch)).toHaveLength(1));
    expect(posted(fetch)[0]?.[1]).toMatchObject({ bank_account_id: "b2" });
  });

  it("applies it to a lease staff find, its owner's account chosen", async () => {
    const fetch = server({
      "/api/unapplied": [HELD],
      "/api/leases?q=rowan&show=all&per_page=10": { items: [ROW], total: 1, page: 1, per_page: 10 },
      "/api/leases/l2": LEASE,
      "/api/unapplied/u1/apply": { transfers: ["tr1"], to_owner: "500.00", to_prepaid: "0.00" },
    });
    render(<UnappliedCash banks={BANKS} refresh={0} changed={() => undefined} />);
    const row = await screen.findByRole("row", { name: "J. Sample $500.00" });
    fireEvent.click(within(row).getByRole("button", { name: "Actions for J. Sample $500.00" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Apply to a lease" }));
    const panel = screen.getByRole("dialog", { name: "Apply to a lease" });
    expect(within(panel).getByText("$500.00 from J. Sample · Check 4410 · received 10/01/2026")).toBeTruthy();
    fireEvent.change(within(panel).getByLabelText("Lease"), { target: { value: "rowan" } });
    fireEvent.click(await within(panel).findByRole("button", { name: "Birch Flats · 3C · Rowan Demo" }));
    expect(await within(panel).findByText("Balance due $900.00")).toBeTruthy();
    fireEvent.change(within(panel).getByLabelText("Owner's account"), { target: { value: "a2" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Apply $500.00 to this lease" }));

    expect(await screen.findByText("Applied $500.00 of unapplied cash to Birch Flats · 3C.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(posted(fetch)).toEqual([["/api/unapplied/u1/apply", {
      request_id: expect.any(String), lease_id: "l2", amount: null, on: companyDay(), account: "a2",
    }]]);
  });

  it("refunds part of it once staff confirm the amount, and says why the books refuse more", async () => {
    const refusal = "only $500.00 of this unapplied cash is left; $600.00 is more";
    const fetch = server({ "/api/unapplied": [HELD] }, { "/api/unapplied/u1/refund": refusal });
    render(<UnappliedCash banks={BANKS} refresh={0} changed={() => undefined} />);
    const row = await screen.findByRole("row", { name: "J. Sample $500.00" });
    fireEvent.click(within(row).getByRole("button", { name: "Actions for J. Sample $500.00" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Refund" }));
    const panel = screen.getByRole("dialog", { name: "Refund unapplied cash" });
    expect((within(panel).getByLabelText("Paid to") as HTMLInputElement).value).toBe("J. Sample");
    fireEvent.change(within(panel).getByLabelText("How"), { target: { value: "EFT" } });
    fireEvent.change(within(panel).getByLabelText("Amount (leave empty for all $500.00)"), { target: { value: "600.00" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Refund $600.00" }));
    const question = screen.getByRole("alertdialog");
    expect(question.textContent).toContain("Refund $600.00 of unapplied cash to J. Sample by EFT");
    expect(question.textContent).toContain("It leaves Operating trust.");
    fireEvent.click(within(question).getByRole("button", { name: "Refund" }));

    expect((await within(screen.getByRole("dialog", { name: "Refund unapplied cash" })).findByRole("alert")).textContent)
      .toBe(refusal);
    expect(posted(fetch)).toEqual([["/api/unapplied/u1/refund", {
      request_id: expect.any(String), paid_to: "J. Sample", method: "EFT", amount: "600.00", on: companyDay(),
    }]]);
  });
});
