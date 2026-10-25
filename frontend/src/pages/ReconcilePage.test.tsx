import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { holdBack } from "../holdBack";
import type { BankBooks, Worksheet } from "../reconcile";
import { BankingPage, ReconcilePage } from "./ReconcilePage";

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
    book_cash: "4075.00",
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
    prepared: null,
  },
  {
    id: "b2", name: "Deposit trust account", kind: "security_deposit", book_cash: "0.00", closed_through: null,
    next_start: null, statements: [], reconciliations: [], prepared: null,
  },
];

const PREPARED: BankBooks[] = [
  {
    ...BANKS[0]!,
    prepared: {
      id: "pr1", through: "2026-01-31", bank_balance: "4075.00", book_cash: "4075.00",
      prepared_by: "Staff 2", preparer_id: "s2", prepared_at: "2026-02-03T09:00:00Z",
    },
  },
  BANKS[1]!,
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

const NO_MOVES = { items: [], total: 0, page: 1, per_page: 50 };
const BREAKDOWN = { book_cash: "4075.00", held: [{ kind: "owner_property", label: "Owners' money", amount: "4075.00" }], owners: [] };

// Opens one of the account's tabs.
const open = async (tab: string) => fireEvent.click(await screen.findByRole("tab", { name: tab }));

// Banking's Year close card (YearCloseCard.test.tsx tests it): last year closed already.
const NOTHING_TO_CLOSE = { closed: [], closed_until: null, next: null };

function server(
  sheet: Worksheet = SHEET,
  answers: Record<string, unknown> = {},
  refuse: Record<string, string> = {},
  banks: BankBooks[] = BANKS,
) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method !== "POST") {
      const body = path === "/api/reconcile" ? banks
        : path === "/api/payables/company-fees" ? (answers[path] ?? [])
        : path === "/api/payments/waiting" ? (answers[path] ?? [])
        : path === "/api/year-close" ? (answers[path] ?? NOTHING_TO_CLOSE)
        : path.endsWith("/bill-suggestions") ? (answers.suggestions ?? [])
        : path.startsWith("/api/bank-accounts/") && path.includes("/register") ? (answers.register ?? NO_MOVES)
        : path.startsWith("/api/bank-accounts/") ? (answers.breakdown ?? BREAKDOWN)
        : path.startsWith("/api/reconcile/b2") ? { ...sheet, through: null, rows: [], open_lines: [], open_transfers: [], matches: [] } : sheet;
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

// A table's body rows, each as its cells' text.
const cells = (table: HTMLElement) =>
  within(table)
    .getAllByRole("row")
    .filter((row) => within(row).queryAllByRole("cell").length > 0)
    .map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent));

describe("ReconcilePage", () => {
  it("shows the statements, the figures, the items and the approved periods", async () => {
    server();
    render(<ReconcilePage me={ME} id="b1" />);

    // Nothing waits for approval: it opens on the register.
    expect((await screen.findByRole("tab", { name: "Transactions" })).getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByText("No money moved in these days.")).toBeTruthy();
    await open("Statements");
    const statements = await screen.findByRole("table", { name: "Statements" });
    expect(cells(statements)).toEqual([["01/01/2026 to 01/31/2026", "$0.00", "$4,075.00", "5", "CSV"]]);
    await open("Reconciliations");
    const shown = screen.getByRole("table", { name: "Figures" });
    expect(shown.textContent).toContain("bank balance (statements)$4,075.00");
    // Off by the difference, until the books lack no line.
    expect(screen.getByText("Off by $65.00").closest("[data-tone]")?.getAttribute("data-tone")).toBe("caution");
    expect(cells(screen.getByRole("table", { name: "Reconciling items" }))).toEqual([
      ["bank line not in the books", "2026-01-28 Service fee", "($15.00)"],
    ]);
    expect(cells(screen.getByRole("table", { name: "Approved" }))).toEqual([
      ["12/01/2025 to 12/31/2025", "$0.00", "$0.00", "Staff 1", "PDF"],
    ]);
    // Each approved period's three-way report, and the reconciliation on screen, download.
    const threeWay = screen.getByRole("link", { name: "Three-way reconciliation, 12/01/2025 to 12/31/2025, PDF" });
    expect(threeWay.getAttribute("href")).toMatch(/^\/api\/reports\/trust_report_three_way_reconciliation\?arg=[^&]+&format=pdf$/);
    const downloads = within(screen.getByRole("group", { name: "Download the reconciliation" })).getAllByRole("link");
    expect(downloads.map((link) => [link.textContent, link.getAttribute("href")?.replace(/arg=[^&]+&arg=/, "arg=…&arg=")])).toEqual([
      ["PDF", "/api/reports/trust_report_bank_reconciliation?arg=…&arg=2026-01-31&format=pdf"],
      ["CSV", "/api/reports/trust_report_bank_reconciliation?arg=…&arg=2026-01-31&format=csv"],
      ["Excel", "/api/reports/trust_report_bank_reconciliation?arg=…&arg=2026-01-31&format=xlsx"],
    ]);
    expect(screen.getByText(/Closed through 12\/31\/2025/)).toBeTruthy();
    const prepare = screen.getByRole("button", { name: "Prepare through 01/31/2026" });
    expect((prepare as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: /^Approve/ })).toBeNull();
    expect(screen.getByText("Lines the books lack come to $65.00: record them and match them first.")).toBeTruthy();
  });

  it("imports a statement file with its days and balances", async () => {
    const fetch = server(SHEET, { "/api/reconcile/statements": { lines: 2, first_day: "2026-02-01", last_day: "2026-02-28" } });
    render(<ReconcilePage me={ME} id="b1" />);
    await open("Statements");
    await screen.findByRole("table", { name: "Statements" });
    const importing = screen.getByRole("button", { name: "Import statement" }) as HTMLButtonElement;
    expect(importing.disabled).toBe(true);

    const file = new File(["Date,Amount\n2026-02-03,-5.00\n"], "february.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("Statement file (CSV or OFX)"), { target: { files: [file] } });
    await screen.findByText(/Ready: february.csv./);
    fireEvent.change(screen.getByLabelText("First day"), { target: { value: "2026-02-01" } });
    fireEvent.change(screen.getByLabelText("Last day"), { target: { value: "2026-02-28" } });
    fireEvent.change(screen.getByLabelText("Closing balance"), { target: { value: "4070.00" } });
    fireEvent.click(importing);

    expect(await screen.findByText("Imported 2 lines, 02/01/2026 to 02/28/2026.")).toBeTruthy();
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
    render(<ReconcilePage me={ME} id="b1" />);
    await open("Reconciliations");
    const matching = screen.getByRole("button", { name: "Match these" }) as HTMLButtonElement;

    fireEvent.click(screen.getByLabelText("01/05/2026 Deposit (D1): $2,750.00"));
    fireEvent.click(screen.getByLabelText("01/03/2026 Rent, Property 1: $1,500.00"));
    expect(screen.getByTestId("picked").textContent).toBe("Lines $2,750.00 · Transfers $1,500.00");
    expect(matching.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("01/04/2026 Rent, Property 2: $1,250.00"));
    expect(matching.disabled).toBe(false);
    fireEvent.click(matching);
    expect(await screen.findByText("Matched.")).toBeTruthy();
    expect(screen.getByTestId("picked").textContent).toBe("Lines $0.00 · Transfers $0.00");

    fireEvent.click(screen.getByRole("button", { name: "Match the sure ones" }));
    expect(await screen.findByText("1 line matched.")).toBeTruthy();
    const matches = screen.getByRole("table", { name: "Matches" });
    fireEvent.click(within(matches).getByRole("button", { name: "Undo" }));
    expect(await screen.findByText("Match undone.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/reconcile/b1/matches", { line_ids: ["l1"], transfer_ids: ["t1", "t2"], cutover_item_ids: [] }],
      ["/api/reconcile/b1/auto-match", {}],
      ["/api/reconcile/matches/m1/undo", {}],
    ]);
  });

  it("prepares a period whose books lack no line, and shows a refusal", async () => {
    const fetch = server(TIED, {}, {});
    render(<ReconcilePage me={ME} id="b1" />);
    await open("Reconciliations");
    const prepare = await screen.findByRole("button", { name: "Prepare through 01/31/2026" });
    expect(screen.queryByText(/Lines the books lack/)).toBeNull();
    expect(screen.getByText("Ties out").closest("[data-tone]")?.getAttribute("data-tone")).toBe("positive");

    fireEvent.click(prepare);

    expect(
      await screen.findByText("Prepared through 01/31/2026; another staff member who may approve approves it."),
    ).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/reconcile/b1/prepare", { through: "2026-01-31" }]]);
    cleanup();

    server(TIED, {}, { "/api/reconcile/b1/prepare": "the next period starts on 2026-02-01" });
    render(<ReconcilePage me={ME} id="b1" />);
    await open("Reconciliations");
    fireEvent.click(await screen.findByRole("button", { name: "Prepare through 01/31/2026" }));
    expect(await screen.findByText("the next period starts on 2026-02-01")).toBeTruthy();
  });

  it("approves a period another staff member prepared, if this one may", async () => {
    const fetch = server(TIED, {}, {}, PREPARED);
    render(<ReconcilePage me={{ ...ME, roles: ["approve"] }} id="b1" />);
    // A month waiting for approval opens on Reconciliations.
    const prepared = await screen.findByLabelText("Prepared");
    expect(screen.getByRole("tab", { name: "Reconciliations" }).getAttribute("aria-selected")).toBe("true");
    expect(prepared.textContent).toContain("Prepared through 01/31/2026 by Staff 2: bank $4,075.00, books $4,075.00.");
    fireEvent.click(screen.getByRole("button", { name: "Approve through 01/31/2026" }));

    expect(await screen.findByText("Approved through 01/31/2026; the account is closed through it.")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/reconcile/b1/approve", { preparation_id: "pr1" }]]);
    cleanup();

    server(TIED, {}, {}, PREPARED);
    render(<ReconcilePage me={ME} id="b1" />);
    expect((await screen.findByLabelText("Prepared")).textContent).toContain("A staff member who may approve approves it.");
    expect(screen.queryByRole("button", { name: /^Approve/ })).toBeNull();
    cleanup();

    server(TIED, {}, {}, PREPARED);
    render(<ReconcilePage me={{ ...ME, person_id: "s2", roles: ["approve"] }} id="b1" />);
    expect((await screen.findByLabelText("Prepared")).textContent).toContain(
      "Another staff member who may approve approves it.",
    );
    expect(screen.getByRole("button", { name: "Prepare through 01/31/2026" })).toBeTruthy();
  });

  it("stays on Reconciliations once the month it opened on is approved", async () => {
    let banks: BankBooks[] = PREPARED;
    vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        // Approved: the month no longer waits, and the account is closed through it.
        banks = [{ ...BANKS[0]!, closed_through: "2026-01-31" }, BANKS[1]!];
        return new Response("{}", { status: 201 });
      }
      const read = path === "/api/reconcile" ? banks : path.startsWith("/api/bank-accounts/") ? NO_MOVES : TIED;
      return new Response(JSON.stringify(read), { status: 200 });
    }));
    render(<ReconcilePage me={{ ...ME, roles: ["approve"] }} id="b1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve through 01/31/2026" }));

    expect(await screen.findByText("Approved through 01/31/2026; the account is closed through it.")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Reconciliations" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("table", { name: "Approved" })).toBeTruthy();
  });

  it("reads the worksheet at another day, and an account with nothing yet", async () => {
    const fetch = server();
    render(<ReconcilePage me={ME} id="b1" />);
    await open("Reconciliations");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Operating trust account");
    expect(screen.getByTestId("book-cash").textContent).toBe("Book balance $4,075.00");
    expect(within(screen.getByRole("main")).getByRole("link", { name: "Banking" }).getAttribute("href")).toBe("/banking");

    fireEvent.change(screen.getByLabelText("At the end of"), { target: { value: "2026-01-15" } });
    await vi.waitFor(() =>
      expect(fetch.mock.calls.map(([path]) => path)).toContain("/api/reconcile/b1?through=2026-01-15"),
    );
    cleanup();
    render(<ReconcilePage me={ME} id="b2" />);

    await open("Statements");
    expect(await screen.findByText("No statement yet. The first one sets where the bank record starts.")).toBeTruthy();
    await open("Reconciliations");
    expect(screen.getByText("Import a statement to see the reconciliation.")).toBeTruthy();
    expect(screen.getAllByText("None.")).toHaveLength(2);
    expect(screen.getByText("None yet.")).toBeTruthy();
  });

  it("shows the worksheet at the day picked now, even when an earlier day's answer comes back last", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => {
      const read = path === "/api/reconcile" ? BANKS : path.startsWith("/api/bank-accounts/") ? NO_MOVES
        : path === "/api/reconcile/b1?through=2026-01-31" ? TIED : SHEET;
      return new Response(JSON.stringify(read), { status: 200 });
    }));
    const release = holdBack((path) => path === "/api/reconcile/b1?through=2026-01-15");
    render(<ReconcilePage me={ME} id="b1" />);
    await open("Reconciliations");
    expect(screen.getByText("Off by $65.00")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("At the end of"), { target: { value: "2026-01-15" } });
    fireEvent.change(screen.getByLabelText("At the end of"), { target: { value: "2026-01-31" } });
    await screen.findByText("Ties out");
    await release();

    expect(screen.getByText("Ties out")).toBeTruthy();
    expect(screen.queryByText("Off by $65.00")).toBeNull();
  });

  it("says when there is no such trust bank account, and when the page can't be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
    render(<ReconcilePage me={ME} id="b1" />);
    expect((await screen.findByRole("alert")).textContent).toBe("no such trust bank account");
    cleanup();

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })),
    );
    render(<ReconcilePage me={ME} id="b1" />);
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
    render(<ReconcilePage me={ME} id="b1" />);
    await open("Reconciliations");

    expect(await screen.findByText(/the first statement's balance brought forward should be \$1,200.00/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Match the opening" }));
    expect(await screen.findByText("The opening is matched.")).toBeTruthy();
    expect(screen.getByRole("table", { name: "Matches" }).textContent).toContain("12/29/2025 Old check, from before the cutover: ($10.00)");

    fireEvent.click(screen.getByLabelText("01/09/2026 Check 1041 (1041): ($200.00)"));
    fireEvent.click(screen.getByLabelText("12/30/2025 Check to Vendor 1 (1041), from before the cutover: ($200.00)"));
    expect(screen.getByTestId("picked").textContent).toBe("Lines ($200.00) · Transfers ($200.00)");
    fireEvent.click(screen.getByRole("button", { name: "Match these" }));
    expect(await screen.findByText("Matched.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/reconcile/b1/opening-match", { line_id: "f1" }],
      ["/api/reconcile/b1/matches", { line_ids: ["c1"], transfer_ids: [], cutover_item_ids: ["i1"] }],
    ]);
  });

  it("still says the opening is matched once the page refreshes to the matched opening", async () => {
    const opening = { book_cash: "1000.00", carried: "0.00", brought_forward: "1000.00", signed_off: true, matched: false };
    let sheet: Worksheet = {
      ...SHEET,
      open_lines: [{ id: "f1", posted_on: "2026-01-01", amount: "1000.00", description: "Balance brought forward", reference: null }],
      opening,
    };
    const fetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        // The server matches it: from now on the worksheet says so.
        sheet = { ...sheet, open_lines: [], opening: { ...opening, matched: true } };
        return new Response(JSON.stringify({ id: "m1" }), { status: 201 });
      }
      const read = path === "/api/reconcile" ? BANKS : path.startsWith("/api/bank-accounts/") ? NO_MOVES : sheet;
      return new Response(JSON.stringify(read), { status: 200 });
    });
    vi.stubGlobal("fetch", fetch);
    render(<ReconcilePage me={ME} id="b1" />);
    await open("Reconciliations");

    fireEvent.click(await screen.findByRole("button", { name: "Match the opening" }));
    // The refresh brings the matched opening; the confirmation stays, the form goes.
    await waitFor(() => expect(screen.queryByRole("button", { name: "Match the opening" })).toBeNull());
    expect(screen.getByText("The opening is matched.")).toBeTruthy();
    expect(screen.getAllByText("The opening is matched.")).toHaveLength(1);
  });

  it("says what the opening needs before it can be matched", async () => {
    const opening = { book_cash: "1000.00", carried: "0.00", brought_forward: "1000.00", signed_off: false, matched: false };
    server({ ...SHEET, opening });
    render(<ReconcilePage me={ME} id="b1" />);
    await open("Reconciliations");
    expect(await screen.findByText("Sign off the cutover at Switching first.")).toBeTruthy();
    cleanup();

    server({ ...SHEET, opening: { ...opening, signed_off: true } });
    render(<ReconcilePage me={ME} id="b1" />);
    await open("Reconciliations");
    expect(await screen.findByText("No open bank line comes to $1,000.00.")).toBeTruthy();
    cleanup();

    // Matched, the card says so for good, with nothing left to do.
    server({ ...SHEET, opening: { ...opening, signed_off: true, matched: true } });
    render(<ReconcilePage me={ME} id="b1" />);
    await open("Reconciliations");
    expect(await screen.findByText("The opening is matched.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Match the opening" })).toBeNull();
  });
});

describe("ReconcilePage bank lines that look like bills", () => {
  const SUGGESTED = [
    {
      line: { id: "l2", posted_on: "2026-01-28", amount: "-125.00", description: "CHECK 4412", reference: null },
      bill_id: "bill-2", vendor: "Vendor 1", reference: "INV-2", owner: "Owner 1", property: "Property 1",
      kind: "record", transfer_id: null, amount: "125.00", check_number: "4412",
    },
    {
      line: { id: "l3", posted_on: "2026-01-29", amount: "-60.00", description: "ACH VENDOR 2", reference: null },
      bill_id: "bill-3", vendor: "Vendor 2", reference: "INV-3", owner: "Owner 2", property: "Property 2",
      kind: "match", transfer_id: "t9", amount: "60.00", check_number: null,
    },
  ];

  it("says how many checks a statement matched by number and amount", async () => {
    server(SHEET, {
      "/api/reconcile/statements": { lines: 2, first_day: "2026-02-01", last_day: "2026-02-28", checks_matched: 1 },
    });
    render(<ReconcilePage me={ME} id="b1" />);
    await open("Statements");
    const file = new File(["Date,Amount\n2026-02-03,-5.00\n"], "february.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("Statement file (CSV or OFX)"), { target: { files: [file] } });
    await screen.findByText(/Ready: february.csv./);
    fireEvent.change(screen.getByLabelText("First day"), { target: { value: "2026-02-01" } });
    fireEvent.change(screen.getByLabelText("Last day"), { target: { value: "2026-02-28" } });
    fireEvent.change(screen.getByLabelText("Closing balance"), { target: { value: "4070.00" } });
    fireEvent.click(screen.getByRole("button", { name: "Import statement" }));
    expect(await screen.findByText(
      "Imported 2 lines, 02/01/2026 to 02/28/2026. 1 check matched to bill payments by number and amount.",
    )).toBeTruthy();
  });

  it("lists them, asks before recording a bill paid or matching a payment, and sends nothing until confirmed", async () => {
    const fetch = server(SHEET, { suggestions: SUGGESTED });
    render(<ReconcilePage me={ME} id="b1" />);
    await open("Reconciliations");
    const table = await screen.findByRole("table", { name: "Bank lines that look like bills" });
    expect(cells(table)).toEqual([
      ["01/28/2026 CHECK 4412", "$125.00", "Vendor 1 INV-2, Owner 1, Property 1 (not paid yet)", "Record paid"],
      ["01/29/2026 ACH VENDOR 2", "$60.00", "Vendor 2 INV-3, Owner 2, Property 2 (payment recorded)", "Match"],
    ]);

    fireEvent.click(within(table).getByRole("button", { name: "Record paid" }));
    const asking = await screen.findByRole("alertdialog");
    expect(asking.textContent).toContain(
      "Record Vendor 1 INV-2 paid $125.00 by check 4412 on 01/28/2026, and match it to this bank line? It comes out of Owner 1's money in trust for Property 1.",
    );
    expect(posted(fetch)).toEqual([]);
    fireEvent.click(within(asking).getByRole("button", { name: "Record paid" }));
    expect(await screen.findByText("Vendor 1 INV-2 is paid and matched.")).toBeTruthy();

    fireEvent.click(within(table).getByRole("button", { name: "Match" }));
    const matching = await screen.findByRole("alertdialog");
    expect(matching.textContent).toContain("to the payment of Vendor 2 INV-3? No money moves.");
    fireEvent.click(within(matching).getByRole("button", { name: "Match" }));
    expect(await screen.findByText("Matched to Vendor 2 INV-3.")).toBeTruthy();
    expect(posted(fetch)).toEqual([
      ["/api/reconcile/b1/bill-suggestions", { line_id: "l2", bill_id: "bill-2", transfer_id: null }],
      ["/api/reconcile/b1/bill-suggestions", { line_id: "l3", bill_id: "bill-3", transfer_id: "t9" }],
    ]);
  });

  it("shows no card when no bank line looks like a bill", async () => {
    server();
    render(<ReconcilePage me={ME} id="b1" />);
    await open("Reconciliations");
    await screen.findByRole("table", { name: "Figures" });
    expect(screen.queryByRole("table", { name: "Bank lines that look like bills" })).toBeNull();
  });
});

describe("BankingPage", () => {
  it("lists each trust bank account with its book balance, how far it is reconciled and what waits", async () => {
    server(SHEET, {}, {}, [{ ...BANKS[0]!, prepared: PREPARED[0]!.prepared }, BANKS[1]!]);
    render(<BankingPage me={ME} />);

    const operating = await screen.findByRole("row", { name: "Operating trust account" });
    expect(within(operating).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Operating trust account", "Operating", "$4,075.00", "12/31/2025", "Waiting for approval", "", "",
    ]);
    expect(within(operating).getByRole("link").getAttribute("href")).toBe("/bank-accounts/b1");
    expect(within(screen.getByRole("row", { name: "Deposit trust account" })).getAllByRole("cell").map((c) => c.textContent)).toEqual([
      "Deposit trust account", "Security deposits", "$0.00", "Not yet", "", "", "",
    ]);
  });

  it("shows the company's fees held in an operating account and transfers them out after asking", async () => {
    const fees = [{ bank_account_id: "b1", bank_account: "Operating trust account", held: "150.00",
      since: "2000-01-01", days_held: 40, take_by: "2000-01-26" }];
    const fetch = server(SHEET, { "/api/payables/company-fees": fees }, {}, BANKS);
    render(<BankingPage me={ME} />);

    const operating = await screen.findByRole("row", { name: "Operating trust account" });
    await vi.waitFor(() => expect(within(operating).getAllByRole("cell")[5]?.textContent).toBe("$150.00take out: past 01/26/2000"));
    fireEvent.click(within(operating).getByRole("button", { name: "Actions for Operating trust account" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Transfer company fees" }));
    const panel = screen.getByRole("dialog", { name: "Transfer company fees" });
    expect(within(panel).getByTestId("company-fees").textContent).toBe(
      "$150.00 of the company's fees, the oldest since 01/01/2000 (40 days)",
    );
    fireEvent.change(within(panel).getByLabelText("Amount (leave empty for all)"), { target: { value: "100.00" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Take out company fees" }));
    const confirm = screen.getByRole("alertdialog");
    expect(confirm.textContent).toContain("Take $100.00 of the company's fees out of Operating trust account on");
    fireEvent.click(within(confirm).getByRole("button", { name: "Confirm" }));

    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posted(fetch)).toEqual([["/api/payables/company-fees", expect.objectContaining({ bank_account_id: "b1", amount: "100.00" })]]);
  });

  it("shows a panel opened while the page reloads after a transfer what the reload brings", async () => {
    const before = [{ bank_account_id: "b1", bank_account: "Operating trust account", held: "150.00",
      since: "2000-01-01", days_held: 40, take_by: "2000-01-26" }];
    const after = [{ ...before[0]!, held: "0.00", since: null, days_held: null, take_by: null }];
    // The second read of the fees waits until the test lets it through.
    const gate: { open?: () => void } = {};
    let reads = 0;
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
    vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST") return json({ amount: "150.00" }, 201);
      if (path === "/api/payables/company-fees") {
        reads += 1;
        if (reads > 1) await new Promise<void>((resolve) => { gate.open = resolve; });
        return json(reads > 1 ? after : before);
      }
      return json(path === "/api/reconcile" ? BANKS : path === "/api/year-close" ? NOTHING_TO_CLOSE
        : path === "/api/payments/waiting" ? [] : {});
    }));
    render(<BankingPage me={ME} />);
    const operating = await screen.findByRole("row", { name: "Operating trust account" });
    const openPanel = async () => {
      fireEvent.click(await within(operating).findByRole("button", { name: "Actions for Operating trust account" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Transfer company fees" }));
      return screen.getByRole("dialog", { name: "Transfer company fees" });
    };
    fireEvent.click(within(await openPanel()).getByRole("button", { name: "Take out company fees" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Confirm" }));
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    // Opened again before the reload lands: what it knew, then what the reload says.
    await vi.waitFor(() => expect(gate.open).toBeDefined());
    const panel = await openPanel();
    expect(within(panel).getByTestId("company-fees").textContent).toBe(
      "$150.00 of the company's fees, the oldest since 01/01/2000 (40 days)",
    );
    gate.open!();
    await vi.waitFor(() =>
      expect(within(panel).getByTestId("company-fees").textContent).toBe("None of the company's fees is held here."),
    );
    expect(within(panel).queryByRole("button", { name: "Take out company fees" })).toBeNull();
  });

  it("keeps a refused transfer's panel open with why", async () => {
    const fees = [{ bank_account_id: "b1", bank_account: "Operating trust account", held: "150.00",
      since: "2000-01-01", days_held: 40, take_by: "2000-01-26" }];
    server(SHEET, { "/api/payables/company-fees": fees },
      { "/api/payables/company-fees": "only $150.00 of the company's fees is in this trust bank account" }, BANKS);
    render(<BankingPage me={ME} />);
    const operating = await screen.findByRole("row", { name: "Operating trust account" });
    fireEvent.click(await within(operating).findByRole("button", { name: "Actions for Operating trust account" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Transfer company fees" }));
    const panel = screen.getByRole("dialog", { name: "Transfer company fees" });
    fireEvent.click(within(panel).getByRole("button", { name: "Take out company fees" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Confirm" }));

    expect((await within(panel).findByRole("alert")).textContent).toBe(
      "only $150.00 of the company's fees is in this trust bank account",
    );
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  it("offers no transfer where none of the company's fees was ever held", async () => {
    const fees = [
      { bank_account_id: "b1", bank_account: "Operating trust account", held: "0.00", since: null, days_held: null, take_by: null },
    ];
    server(SHEET, { "/api/payables/company-fees": fees }, {}, BANKS);
    render(<BankingPage me={ME} />);
    const operating = await screen.findByRole("row", { name: "Operating trust account" });
    fireEvent.click(await within(operating).findByRole("button", { name: "Actions for Operating trust account" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Transfer company fees" }));
    const panel = screen.getByRole("dialog", { name: "Transfer company fees" });
    expect(within(panel).getByTestId("company-fees").textContent).toBe("None of the company's fees is held here.");
    expect(within(panel).queryByRole("button", { name: "Take out company fees" })).toBeNull();
    fireEvent.click(within(panel).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("adds a trust bank account of either kind and opens its page", async () => {
    const fetch = server(SHEET, { "/api/setup/bank-accounts": { id: "b9" } });
    render(<BankingPage me={ME} />);
    await screen.findByRole("row", { name: "Operating trust account" });
    fireEvent.click(screen.getByRole("button", { name: "Add bank account" }));
    const panel = screen.getByRole("dialog", { name: "Add bank account" });
    fireEvent.change(within(panel).getByLabelText("Holds"), { target: { value: "security_deposit" } });
    fireEvent.change(within(panel).getByLabelText("Bank account name"), { target: { value: "Second deposit trust" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add bank account" }));

    await vi.waitFor(() => expect(window.location.pathname).toBe("/bank-accounts/b9"));
    expect(posted(fetch)).toEqual([["/api/setup/bank-accounts", { kind: "security_deposit", name: "Second deposit trust" }]]);
    window.history.pushState(null, "", "/");
  });

  it("keeps the panel open with why a bank account was refused", async () => {
    server(SHEET, {}, { "/api/setup/bank-accounts": "sign in first" });
    render(<BankingPage me={ME} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add bank account" }));
    const panel = screen.getByRole("dialog", { name: "Add bank account" });
    fireEvent.change(within(panel).getByLabelText("Bank account name"), { target: { value: "Operating trust account" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add bank account" }));
    expect((await within(panel).findByRole("alert")).textContent).toBe("sign in first");
  });

  it("says when there is no trust bank account, and when they can't be read", async () => {
    server(SHEET, {}, {}, []);
    render(<BankingPage me={ME} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("No trust bank account yet.");
    cleanup();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "sign in first" }), { status: 401 })));
    render(<BankingPage me={ME} />);
    expect((await screen.findByRole("alert")).textContent).toBe("sign in first");
  });
});
