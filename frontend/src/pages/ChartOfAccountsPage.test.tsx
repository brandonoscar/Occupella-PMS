import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import type { Chart, ChartAccount } from "../chart";
import { ChartOfAccountsPage } from "./ChartOfAccountsPage";

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

function account(over: Partial<ChartAccount>): ChartAccount {
  return {
    section: "income", code: "4000", name: "Rent", type: "Income line", line: "rent",
    charge_type_id: null, expense_account_id: null, schedule_e_line: null, kept_by: "owner",
    archived: false, ...over,
  };
}

const CHART: Chart = {
  accounts: [
    account({ section: "asset", code: "1000", name: "Trust bank cash", type: "Bank cash", line: "bank_cash", kept_by: null }),
    account({ section: "liability", code: "2000", name: "Owner funds", type: "Owner funds", line: "owner_property", kept_by: null }),
    account({}),
    account({ code: "4501", name: "Admin fee", type: "Charge type", line: null, charge_type_id: "t1", kept_by: "company" }),
    account({
      section: "expense", code: "6401", name: "Plumbing repairs", type: "Expense account", line: null,
      expense_account_id: "e1", schedule_e_line: 14, kept_by: null,
    }),
    account({
      section: "expense", code: "6402", name: "Roof repairs", type: "Expense account", line: null,
      expense_account_id: "e2", schedule_e_line: 14, kept_by: null, archived: true,
    }),
  ],
  schedule_e_lines: [{ line: 14, label: "Repairs" }],
};

function server(answers: Record<string, unknown> = {}) {
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      if (path in answers) {
        const [status, body] = answers[path] as [number, unknown];
        return new Response(JSON.stringify(body), { status });
      }
      return new Response(JSON.stringify({ changed: true }), { status: 201 });
    }
    return new Response(JSON.stringify(CHART), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const posted = (fetch: ReturnType<typeof server>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe("ChartOfAccountsPage", () => {
  it("lists every account by section with its code, type, line, who keeps it and status", async () => {
    server();
    render(<ChartOfAccountsPage me={ME} />);

    const income = await screen.findByRole("table", { name: "Income" });
    expect(within(income).getByRole("row", { name: "Admin fee" }).textContent).toBe(
      "4501Admin feeCharge typeCompanyactive",
    );
    const expenses = screen.getByRole("table", { name: "Expenses" });
    expect(within(expenses).getByRole("row", { name: "Plumbing repairs" }).textContent).toContain("14: Repairs");
    expect(within(expenses).getByRole("row", { name: "Roof repairs" }).textContent).toContain("archived");
    expect(screen.getByRole("table", { name: "Assets" })).toBeTruthy();
    expect(screen.getByRole("table", { name: "Liabilities" })).toBeTruthy();
    expect(screen.queryByRole("table", { name: "Equity" })).toBeNull();
    expect(screen.getByRole("link", { name: "Download CSV" }).getAttribute("href")).toBe(
      "/api/reports/trust_report_chart_of_accounts?format=csv",
    );
  });

  it("gives an account another code in a side panel, and says why one is refused", async () => {
    const fetch = server({ "/api/setup/codes": [409, { detail: "code 4501 is Admin fee's already" }] });
    render(<ChartOfAccountsPage me={ME} />);
    await screen.findByRole("table", { name: "Income" });

    fireEvent.click(screen.getByRole("button", { name: "Actions for Rent" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Change code" }));
    const panel = screen.getByRole("dialog", { name: "Change code" });
    const code = within(panel).getByLabelText(/Code/);
    fireEvent.change(code, { target: { value: "40" } });
    expect((within(panel).getByRole("button", { name: "Save code" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(code, { target: { value: "4501" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save code" }));

    expect(await within(panel).findByText("code 4501 is Admin fee's already")).toBeTruthy();
    expect(posted(fetch)).toEqual([["/api/setup/codes", { line: "rent", code: "4501" }]]);
  });

  it("offers a fixed line only a new code: it keeps its name and is never archived", async () => {
    server();
    render(<ChartOfAccountsPage me={ME} />);
    await screen.findByRole("table", { name: "Income" });

    fireEvent.click(screen.getByRole("button", { name: "Actions for Rent" }));

    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Change code"]);
  });

  it("renames, archives after asking, and brings back an expense account", async () => {
    const fetch = server();
    render(<ChartOfAccountsPage me={ME} />);
    await screen.findByRole("table", { name: "Expenses" });

    fireEvent.click(screen.getByRole("button", { name: "Actions for Plumbing repairs" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const panel = screen.getByRole("dialog", { name: "Rename" });
    fireEvent.change(within(panel).getByLabelText(/Name/), { target: { value: "Plumbing" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save name" }));
    expect(await screen.findByText("Renamed to Plumbing.")).toBeTruthy();

    // A panel closes a tick after its toast shows, and hides the page until it has: each click
    // after one waits for its button (e2e's vitest run of 11/06 clicked too soon and failed).
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Plumbing repairs" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    const asked = screen.getByRole("alertdialog");
    expect(asked.textContent).toContain("Archive Plumbing repairs?");
    fireEvent.click(within(asked).getByRole("button", { name: "Archive" }));
    expect(await screen.findByText("Plumbing repairs is archived.")).toBeTruthy();

    fireEvent.click(await screen.findByRole("button", { name: "Actions for Roof repairs" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Bring back" }));
    expect(await screen.findByText("Roof repairs is back.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/setup/expense-accounts/e1/name", { name: "Plumbing" }],
      ["/api/setup/expense-accounts/e1/archive", { archived: true }],
      ["/api/setup/expense-accounts/e2/archive", { archived: false }],
    ]);
  });

  it("says who keeps a charge type's fees, and adds a type and an account", async () => {
    const fetch = server({
      "/api/setup/charge-types": [201, { id: "t2" }],
      "/api/setup/expense-accounts": [201, { id: "e3" }],
    });
    render(<ChartOfAccountsPage me={ME} />);
    await screen.findByRole("table", { name: "Income" });

    fireEvent.click(screen.getByRole("button", { name: "Actions for Admin fee" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Change who keeps it" }));
    const payee = screen.getByRole("dialog", { name: "Who keeps its fees" });
    fireEvent.change(within(payee).getByLabelText("Who keeps its fees"), { target: { value: "owner" } });
    fireEvent.click(within(payee).getByRole("button", { name: /Save who keeps it/ }));
    expect(await screen.findByText("Admin fee's fees go to the owner from now on.")).toBeTruthy();

    fireEvent.click(await screen.findByRole("button", { name: "Add charge type" }));
    const kind = screen.getByRole("dialog", { name: "Add charge type" });
    fireEvent.change(within(kind).getByLabelText(/Charge type/), { target: { value: "Pool key" } });
    fireEvent.change(within(kind).getByLabelText("Who keeps its fees"), { target: { value: "company" } });
    fireEvent.click(within(kind).getByRole("button", { name: "Add charge type" }));
    expect(await screen.findByText("Added Pool key.")).toBeTruthy();

    fireEvent.click(await screen.findByRole("button", { name: "Add expense account" }));
    const bills = screen.getByRole("dialog", { name: "Add expense account" });
    fireEvent.change(within(bills).getByLabelText(/Expense account/), { target: { value: "Gutters" } });
    fireEvent.change(within(bills).getByLabelText(/Schedule E line/), { target: { value: "14" } });
    fireEvent.click(within(bills).getByRole("button", { name: "Add expense account" }));
    expect(await screen.findByText("Added Gutters.")).toBeTruthy();

    expect(posted(fetch)).toEqual([
      ["/api/setup/charge-types/t1/payee", { payee: "owner" }],
      ["/api/setup/charge-types", { name: "Pool key", payee: "company" }],
      ["/api/setup/expense-accounts", { name: "Gutters", schedule_e_line: 14 }],
    ]);
  });

  it("says when the company has no charge type or expense account yet", async () => {
    const bare = {
      ...CHART,
      accounts: [
        ...CHART.accounts.filter((a) => a.line !== null),
        account({
          section: "expense", code: "5000", name: "Bills not in an expense account", type: "Expense line",
          line: "unassigned_bills", kept_by: null,
        }),
      ],
    };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(bare), { status: 200 })));
    render(<ChartOfAccountsPage me={ME} />);

    expect(await screen.findByText("No charge types yet")).toBeTruthy();
    expect(screen.getByText("No expense accounts yet")).toBeTruthy();
    // The fixed lines are listed all the same.
    expect(within(screen.getByRole("table", { name: "Income" })).getByRole("row", { name: "Rent" })).toBeTruthy();
  });
});
