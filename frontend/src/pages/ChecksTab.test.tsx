import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Payout } from "./BankFilesTab";
import type { CheckRun, CheckSettings } from "./ChecksTab";
import { ChecksTab, settingsLine } from "./ChecksTab";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const DEFAULTS: CheckSettings = { position: "top", shift_right: 0, shift_down: 0, recorded_by: null, recorded_at: null };

const WAITING: Payout[] = [
  {
    transfer_id: "t1", paid_on: "2026-10-22", payee: "owner", payee_id: "o1", name: "Demo Owner A",
    what: "Owner draw, Elm Court", memo: "Owner draw", amount: "125.50", bank_account: null,
  },
  {
    transfer_id: "t2", paid_on: "2026-10-22", payee: "vendor", payee_id: "v1", name: "Demo Roofing Co",
    what: "Bill ROOF-17", memo: "Roof repair", amount: "1000.00", bank_account: null,
  },
];

const RUN: CheckRun = {
  id: "r1", dated_on: "2026-10-23", made_by: "Staff 1",
  checks: [
    { id: "c1", number: 1001, name: "Demo Owner A", what: "Owner draw, Elm Court", amount: "125.50", voided: null },
    { id: "c2", number: 1002, name: "Demo Roofing Co", what: "Bill ROOF-17", amount: "1000.00", voided: null },
  ],
};

type Page = { settings: CheckSettings; waiting: Payout[]; runs: CheckRun[]; next_number: number | null };

function server(start: Page) {
  let now = start;
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") return new Response(JSON.stringify(now), { status: 200 });
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    if (path.endsWith("/settings")) {
      now = { ...now, settings: { ...now.settings, ...body, recorded_by: "Staff 1", recorded_at: "2026-10-23T02:00:00Z" } as CheckSettings };
      return new Response(JSON.stringify(now.settings), { status: 201 });
    }
    if (path.endsWith("/void")) {
      now = { ...now, runs: now.runs.map((r) => ({ ...r, checks: r.checks.map((c) => c.id === "c1" ? { ...c, voided: { reason: String(body.reason), by: "Staff 1", at: "" } } : c) })) };
      return new Response(null, { status: 204 });
    }
    now = { ...now, waiting: [], runs: [RUN], next_number: 1003 };
    return new Response(JSON.stringify(RUN), { status: 201 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posts = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("ChecksTab", () => {
  it("says where checks print in words", () => {
    expect(settingsLine(DEFAULTS)).toBe("Check at the top of the page");
    expect(settingsLine({ ...DEFAULTS, position: "middle", shift_right: 4, shift_down: -2 })).toBe(
      "Check in the middle of the page, printing moved 4 pt right and 2 pt up",
    );
    expect(settingsLine({ ...DEFAULTS, position: "bottom", shift_right: -3 })).toBe("Check at the bottom of the page, printing moved 3 pt left");
  });

  it("prints the payouts picked under the first sheet's number after a question", async () => {
    const fetch = server({ settings: DEFAULTS, waiting: WAITING, runs: [], next_number: null });
    render(<ChecksTab bankId="b1" />);

    const list = await screen.findByRole("list", { name: "Payouts by check waiting" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "10/22/2026 Owner draw, Elm Court, Demo Owner A: $125.50",
      "10/22/2026 Bill ROOF-17, Demo Roofing Co: $1,000.00",
    ]);
    for (const box of within(list).getAllByRole("checkbox")) fireEvent.click(box);
    expect((screen.getByRole("button", { name: "Print 2 checks of $1,125.50" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("First check number"), { target: { value: "10a01" } });
    expect((screen.getByLabelText("First check number") as HTMLInputElement).value).toBe("1001");
    fireEvent.click(screen.getByRole("button", { name: "Print 2 checks of $1,125.50" }));
    const asked = screen.getByRole("alertdialog");
    expect(asked.textContent).toContain("Print 2 checks of $1,125.50, numbered 1001 to 1002? Load the stock with check 1001 first.");
    fireEvent.click(within(asked).getByRole("button", { name: "Print checks" }));

    expect(await screen.findByText("Checks 1001 to 1002 ready to print: $1,125.50.")).toBeTruthy();
    expect(posts(fetch)).toEqual([["/api/checks/b1", { first_number: 1001, transfer_ids: ["t1", "t2"] }]]);
    const table = await screen.findByRole("table", { name: "Printed checks" });
    expect(within(table).getByRole("link", { name: "PDF of 1001–1002" }).getAttribute("href")).toBe("/api/checks/runs/r1.pdf");
    expect(within(table).getByRole("row", { name: "Check 1002" }).textContent).toContain("$1,000.00");
    // The next number is suggested once the run is printed.
    expect(await screen.findByText("No payout by check of the last 30 days waits to be printed.")).toBeTruthy();
  });

  it("moves the printing and voids a check with why", async () => {
    const fetch = server({ settings: DEFAULTS, waiting: [], runs: [RUN], next_number: 1003 });
    render(<ChecksTab bankId="b1" />);

    const group = await screen.findByRole("group", { name: "Check stock" });
    expect(group.textContent).toContain("Pre-printed voucher stock");
    fireEvent.click(within(group).getByRole("button", { name: "Change where checks print" }));
    const panel = screen.getByRole("dialog", { name: "Where checks print" });
    fireEvent.change(within(panel).getByLabelText("Check on the page"), { target: { value: "bottom" } });
    fireEvent.change(within(panel).getByLabelText("Move right (points, − for left)"), { target: { value: "5" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Check at the bottom of the page, printing moved 5 pt right.")).toBeTruthy();
    expect(posts(fetch)[0]).toEqual(["/api/checks/b1/settings", { position: "bottom", shift_right: 5, shift_down: 0 }]);

    fireEvent.click(screen.getByRole("button", { name: "Actions for check 1001" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Void" }));
    const voiding = screen.getByRole("dialog", { name: "Void check 1001" });
    fireEvent.change(within(voiding).getByLabelText("Why"), { target: { value: "Jammed in the printer" } });
    fireEvent.click(within(voiding).getByRole("button", { name: "Void check" }));
    expect(await screen.findByText("Check 1001 is void; its payout waits for a new check.")).toBeTruthy();
    expect(posts(fetch).at(-1)).toEqual(["/api/checks/c1/void", { reason: "Jammed in the printer" }]);
    expect(await screen.findByText("Void: Jammed in the printer")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Actions for check 1001" })).toBeNull();
  });

  it("says so when nothing waits and nothing was printed", async () => {
    server({ settings: DEFAULTS, waiting: [], runs: [], next_number: null });
    render(<ChecksTab bankId="b1" />);
    expect(await screen.findByText("No check printed yet.")).toBeTruthy();
  });
});
