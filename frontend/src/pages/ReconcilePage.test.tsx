import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { BankBooks, Worksheet } from "../reconcile";
import { ReconcilePage } from "./ReconcilePage";

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

const BANKS: BankBooks[] = [
  {
    id: "b1",
    name: "Operating trust account",
    kind: "operating",
    closed_through: "2025-12-31",
    next_start: "2026-01-01",
    statements: [
      {
        id: "s1", first_day: "2026-01-01", last_day: "2026-01-31", opening_balance: "0.00",
        closing_balance: "4075.00", lines: 5, source: "csv", imported_at: "2026-02-02T10:00:00Z",
      },
    ],
    reconciliations: [
      {
        id: "r1", period_start: "2025-12-01T00:00:00Z", period_end: "2026-01-01T00:00:00Z",
        statement_balance: "0.00", book_balance: "0.00", prepared_by: "Staff 1",
        approved_by: "Staff 1", approved_at: "2026-01-02T10:00:00Z",
      },
    ],
  },
  {
    id: "b2", name: "Deposit trust account", kind: "security_deposit", closed_through: null,
    next_start: null, statements: [], reconciliations: [],
  },
];

const SHEET: Worksheet = {
  through: "2026-01-31",
  rows: [
    { line: 1, item: "PMC", detail: "Synthetic PMC", amount: null },
    { line: 4, item: "bank balance (statements)", detail: null, amount: "4075.00" },
    { line: 8, item: "trust journal (book cash)", detail: null, amount: "3750.00" },
    { line: 9, item: "difference: adjusted bank balance - trust journal", detail: null, amount: "65.00" },
    { line: 10, item: "bank line not in the books", detail: "2026-01-28 Service fee", amount: "-15.00" },
  ],
  open_lines: [
    { id: "l1", posted_on: "2026-01-05", amount: "2750.00", description: "Deposit", reference: "D1" },
    { id: "l2", posted_on: "2026-01-28", amount: "-15.00", description: "Service fee", reference: null },
  ],
  open_transfers: [
    { id: "t1", on: "2026-01-03", amount: "1500.00", memo: "Rent, Property 1" },
    { id: "t2", on: "2026-01-04", amount: "1250.00", memo: "Rent, Property 2" },
  ],
  matches: [
    {
      id: "m1",
      lines: [{ id: "l0", posted_on: "2026-01-23", amount: "-240.00", description: "Check 101", reference: "101" }],
      transfers: [{ id: "t0", on: "2026-01-20", amount: "-240.00", memo: "Check 101, Vendor 1" }],
      items: [],
    },
  ],
  open_items: [],
  opening: null,
};

const TIED: Worksheet = {
  ...SHEET,
  rows: SHEET.rows.map((r) => (r.line === 9 ? { ...r, amount: "0.00" } : r)).filter((r) => r.line <= 9),
};

function server(sheet: Worksheet = SHEET, answers: Record<string, unknown> = {}, refuse: Record<string, string> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      const body = path === "/api/reconcile" ? BANKS : path.startsWith("/api/reconcile/b2") ? { ...sheet, through: null, rows: [], open_lines: [], open_transfers: [], matches: [] } : sheet;
      return new Response(JSON.stringify(body), { status: 200 });
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

describe("ReconcilePage", () => {
  it("shows the statements, the figures, the items and the approved periods", async () => {
    server();
    render(<ReconcilePage me={ME} />);

    const statements = await screen.findByRole("list", { name: "Statements" });
    expect(statements.textContent).toBe("2026-01-01 to 2026-01-31: $0.00 to $4,075.00, 5 lines (CSV)");
    const shown = screen.getByLabelText("Figures");
    expect(shown.textContent).toContain("bank balance (statements)$4,075.00");
    expect(screen.getByRole("list", { name: "Reconciling items" }).textContent).toBe(
      "bank line not in the books: 2026-01-28 Service fee -$15.00",
    );
    expect(screen.getByRole("list", { name: "Approved" }).textContent).toBe(
      "2025-12-01 to 2025-12-31: bank $0.00, books $0.00, approved by Staff 1",
    );
    expect(screen.getByText(/Closed through 2025-12-31/)).toBeTruthy();
    const approve = screen.getByRole("button", { name: "Approve through 2026-01-31" });
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Lines the books lack come to $65.00: record them and match them first.")).toBeTruthy();
  });

  it("imports a statement file with its days and balances", async () => {
    const fetch = server(SHEET, { "/api/reconcile/statements": { lines: 2, first_day: "2026-02-01", last_day: "2026-02-28" } });
    render(<ReconcilePage me={ME} />);
    await screen.findByRole("list", { name: "Statements" });
    const importing = screen.getByRole("button", { name: "Import statement" }) as HTMLButtonElement;
    expect(importing.disabled).toBe(true);

    const file = new File(["Date,Amount\n2026-02-03,-5.00\n"], "february.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("Statement file (CSV or OFX)"), { target: { files: [file] } });
    await screen.findByText(/Ready: february.csv./);
    fireEvent.change(screen.getByLabelText("First day"), { target: { value: "2026-02-01" } });
    fireEvent.change(screen.getByLabelText("Last day"), { target: { value: "2026-02-28" } });
    fireEvent.change(screen.getByLabelText("Closing balance"), { target: { value: "4070.00" } });
    fireEvent.click(importing);

    expect(await screen.findByText("Imported 2 lines, 2026-02-01 to 2026-02-28.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      [
        "/api/reconcile/statements",
        {
          bank_account_id: "b1", format: "csv", content: "Date,Amount\n2026-02-03,-5.00\n",
          first_day: "2026-02-01", last_day: "2026-02-28", opening_balance: null,
          closing_balance: "4070.00",
        },
      ],
    ]);
  });

  it("matches the lines and transfers picked once they come to the same", async () => {
    const fetch = server(SHEET, { "/api/reconcile/b1/auto-match": { matched: 1 } });
    render(<ReconcilePage me={ME} />);
    await screen.findByRole("list", { name: "Statements" });
    const matching = screen.getByRole("button", { name: "Match these" }) as HTMLButtonElement;

    fireEvent.click(screen.getByLabelText("2026-01-05 Deposit (D1): $2,750.00"));
    fireEvent.click(screen.getByLabelText("2026-01-03 Rent, Property 1: $1,500.00"));
    expect(screen.getByTestId("picked").textContent).toBe("Lines $2,750.00 · Transfers $1,500.00");
    expect(matching.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("2026-01-04 Rent, Property 2: $1,250.00"));
    expect(matching.disabled).toBe(false);
    fireEvent.click(matching);
    expect(await screen.findByText("Matched.")).toBeTruthy();
    expect(screen.getByTestId("picked").textContent).toBe("Lines $0.00 · Transfers $0.00");

    fireEvent.click(screen.getByRole("button", { name: "Match the sure ones" }));
    expect(await screen.findByText("1 line matched.")).toBeTruthy();
    const matches = screen.getByRole("list", { name: "Matches" });
    fireEvent.click(within(matches).getByRole("button", { name: "Undo" }));
    expect(await screen.findByText("Match undone.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/reconcile/b1/matches", { line_ids: ["l1"], transfer_ids: ["t1", "t2"], cutover_item_ids: [] }],
      ["/api/reconcile/b1/auto-match", {}],
      ["/api/reconcile/matches/m1/undo", {}],
    ]);
  });

  it("approves a period whose books lack no line, and shows a refusal", async () => {
    const fetch = server(TIED, {}, {});
    render(<ReconcilePage me={ME} />);
    const approve = await screen.findByRole("button", { name: "Approve through 2026-01-31" });
    expect(screen.queryByText(/Lines the books lack/)).toBeNull();

    fireEvent.click(approve);

    expect(await screen.findByText("Approved through 2026-01-31; the account is closed through it.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/reconcile/b1/approve", { through: "2026-01-31" }]]);
    cleanup();

    server(TIED, {}, { "/api/reconcile/b1/approve": "the next period starts on 2026-02-01" });
    render(<ReconcilePage me={ME} />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve through 2026-01-31" }));
    expect(await screen.findByText("the next period starts on 2026-02-01")).toBeTruthy();
  });

  it("reads the worksheet at another day, and another account", async () => {
    const fetch = server();
    render(<ReconcilePage me={ME} />);
    await screen.findByRole("list", { name: "Statements" });

    fireEvent.change(screen.getByLabelText("At the end of"), { target: { value: "2026-01-15" } });
    await vi.waitFor(() =>
      expect(fetch.mock.calls.map(([path]) => path)).toContain("/api/reconcile/b1?through=2026-01-15"),
    );
    // Choosing the account already shown keeps its worksheet.
    fireEvent.change(screen.getByLabelText("Trust bank account"), { target: { value: "b1" } });
    expect(await screen.findByLabelText("Figures")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Trust bank account"), { target: { value: "b2" } });

    expect(await screen.findByText("No statement yet. The first one sets where the bank record starts.")).toBeTruthy();
    expect(screen.getByText("Import a statement to see the reconciliation.")).toBeTruthy();
    expect(screen.getAllByText("None.")).toHaveLength(2);
    expect(screen.getByText("None yet.")).toBeTruthy();
  });

  it("says when there is no trust bank account, and when the page can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
    render(<ReconcilePage me={ME} />);
    expect(await screen.findByText("Add a trust bank account in Setup first.")).toBeTruthy();
    cleanup();

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })),
    );
    render(<ReconcilePage me={ME} />);
    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("sign in first");
  });

  it("matches a migrated account's opening, then clears an item carried over", async () => {
    const migrated: Worksheet = {
      ...SHEET,
      open_lines: [
        { id: "f1", posted_on: "2026-01-01", amount: "1200.00", description: "Balance brought forward", reference: null },
        { id: "c1", posted_on: "2026-01-09", amount: "-200.00", description: "Check 1041", reference: "1041" },
      ],
      open_transfers: [],
      open_items: [
        { id: "i1", issued_on: "2025-12-30", amount: "-200.00", description: "Check to Vendor 1", reference: "1041" },
      ],
      opening: { book_cash: "1000.00", carried: "-200.00", brought_forward: "1200.00", signed_off: true, matched: false },
      matches: [{ ...SHEET.matches[0]!, items: [{ id: "i0", issued_on: "2025-12-29", amount: "-10.00", description: "Old check", reference: null }] }],
    };
    const fetch = server(migrated);
    render(<ReconcilePage me={ME} />);

    expect(await screen.findByText(/the first statement's balance brought forward should be \$1,200.00/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Match the opening" }));
    expect(await screen.findByText("The opening is matched.")).toBeTruthy();
    expect(screen.getByRole("list", { name: "Matches" }).textContent).toContain("2025-12-29 Old check, from before the cutover: -$10.00");

    fireEvent.click(screen.getByLabelText("2026-01-09 Check 1041 (1041): -$200.00"));
    fireEvent.click(screen.getByLabelText("2025-12-30 Check to Vendor 1 (1041), from before the cutover: -$200.00"));
    expect(screen.getByTestId("picked").textContent).toBe("Lines -$200.00 · Transfers -$200.00");
    fireEvent.click(screen.getByRole("button", { name: "Match these" }));
    expect(await screen.findByText("Matched.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/reconcile/b1/opening-match", { line_id: "f1" }],
      ["/api/reconcile/b1/matches", { line_ids: ["c1"], transfer_ids: [], cutover_item_ids: ["i1"] }],
    ]);
  });

  it("says what the opening needs before it can be matched", async () => {
    const opening = { book_cash: "1000.00", carried: "0.00", brought_forward: "1000.00", signed_off: false, matched: false };
    server({ ...SHEET, opening });
    render(<ReconcilePage me={ME} />);
    expect(await screen.findByText("Sign off the cutover at Switching first.")).toBeTruthy();
    cleanup();

    server({ ...SHEET, opening: { ...opening, signed_off: true } });
    render(<ReconcilePage me={ME} />);
    expect(await screen.findByText("No open bank line comes to $1,000.00.")).toBeTruthy();
    cleanup();

    server({ ...SHEET, opening: { ...opening, signed_off: true, matched: true } });
    render(<ReconcilePage me={ME} />);
    await screen.findByRole("list", { name: "Statements" });
    expect(screen.queryByText("Opening from the old system")).toBeNull();
  });
});
