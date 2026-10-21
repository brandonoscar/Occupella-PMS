import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../api";
import { day } from "../money";
import type { Preview } from "../reports";
import { ReportPage } from "./ReportPage";

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/reports/rent_roll");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ME: Me = {
  kind: "staff", email: "staff@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1",
  pmc_name: "Synthetic PMC",
};

const ROLL: Preview = {
  title: "rent roll",
  columns: ["line", "item", "property", "unit", "monthly_rent"],
  rows: [
    ["1", "PMC", "", "", ""],
    ["2", "lease", "Property 1", "Unit A", "1000.00"],
  ],
  total_rows: 2,
};
const BOOKS = {
  bank_accounts: [], properties: [], accounts: [], tenants: [], vendors: [], expense_accounts: [], schedule_e_lines: [],
  owners: [{ id: "o1", name: "Owner 1" }, { id: "o2", name: "Owner 2" }],
};

function server(answers: Record<string, unknown> = {}) {
  const fetch = vi.fn(async (path: string) => {
    const body =
      path in answers ? answers[path]
      : path === "/api/setup" ? BOOKS
      : path === "/api/property-groups" ? { groups: [{ id: "g1", name: "North", properties: [] }], properties: [] }
      : path.startsWith("/api/reports/") ? ROLL
      : { detail: "not found" };
    return new Response(JSON.stringify(body), { status: "detail" in (body as object) ? 422 : 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const asked = (fetch: ReturnType<typeof server>) => fetch.mock.calls.map(([p]) => p as string);

describe("ReportPage", () => {
  it("previews a report as of today, narrows it to a group, and exports it the same way", async () => {
    const fetch = server();
    render(<ReportPage me={ME} name="rent_roll" />);
    const today = day(new Date());

    expect(await screen.findByRole("heading", { level: 1, name: "Rent roll" })).toBeTruthy();
    const table = await screen.findByRole("table", { name: "Rent roll" });
    // Line numbers are for joining rows up, not for reading.
    expect(within(table).getAllByRole("columnheader").map((c) => c.textContent)).toEqual([
      "Item", "Property", "Unit", "Monthly rent",
    ]);
    expect(within(table).getAllByRole("row")[2]?.textContent).toBe("leaseProperty 1Unit A1000.00");
    expect(asked(fetch)).toContain(`/api/reports/trust_report_rent_roll?arg=${today}&format=json`);

    await screen.findByRole("option", { name: "North" });
    fireEvent.change(screen.getByLabelText("Properties shown"), { target: { value: "group:g1" } });
    fireEvent.change(screen.getByLabelText("As of"), { target: { value: "2026-03-31" } });
    await vi.waitFor(() =>
      expect(asked(fetch)).toContain("/api/reports/trust_report_rent_roll?arg=2026-03-31&format=json&group=g1"),
    );
    const exports = screen.getByLabelText("Export");
    expect(within(exports).getByRole("link", { name: "PDF" }).getAttribute("href")).toBe(
      "/api/reports/trust_report_rent_roll?arg=2026-03-31&format=pdf&group=g1",
    );
    expect(within(exports).getByRole("link", { name: "Excel" }).getAttribute("href")).toContain("format=xlsx");
    expect(within(exports).getByRole("link", { name: "CSV" }).getAttribute("href")).toContain("format=csv");

    // One owner's properties (q109): All owners until one is picked, then the preview and the
    // exports name them.
    const owner = screen.getByLabelText("Owner") as HTMLSelectElement;
    expect(owner.value).toBe("");
    await screen.findByRole("option", { name: "Owner 2" });
    fireEvent.change(owner, { target: { value: "o2" } });
    await vi.waitFor(() =>
      expect(asked(fetch)).toContain("/api/reports/trust_report_rent_roll?arg=2026-03-31&format=json&group=g1&owner=o2"),
    );
    expect(within(exports).getByRole("link", { name: "PDF" }).getAttribute("href")).toBe(
      "/api/reports/trust_report_rent_roll?arg=2026-03-31&format=pdf&group=g1&owner=o2",
    );
  });

  it("takes an owner and days with presets for an owner's report", async () => {
    window.history.replaceState(null, "", "/reports/income_statement");
    const fetch = server();
    render(<ReportPage me={ME} name="income_statement" />);

    await vi.waitFor(() => expect((screen.getByLabelText("Owner") as HTMLSelectElement).value).toBe("o1"));
    // An owner's report is not narrowed by property.
    expect(screen.queryByLabelText("Properties shown")).toBeNull();
    fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "o2" } });
    fireEvent.change(screen.getByLabelText("Days"), { target: { value: "last-year" } });
    const year = new Date().getUTCFullYear() - 1;
    await vi.waitFor(() =>
      expect(asked(fetch)).toContain(
        `/api/reports/trust_report_income_statement?arg=o2&arg=${year}-01-01&arg=${year}-12-31&format=json`,
      ),
    );
  });

  it("shows the report for what is picked now, even when an earlier answer comes back last", async () => {
    window.history.replaceState(null, "", "/reports/income_statement");
    let releaseFirst = () => {};
    const first = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    const of = (who: string): Preview => ({ ...ROLL, rows: [["1", "owner", who, "", ""]], total_rows: 1 });
    const fetch = vi.fn(async (path: string) => {
      if (path === "/api/setup") return new Response(JSON.stringify(BOOKS));
      if (path.includes("arg=o1&")) {
        await first; // Owner 1's answer is slow: it comes back after Owner 2's
        return new Response(JSON.stringify(of("Owner 1")));
      }
      return new Response(JSON.stringify(of("Owner 2")));
    });
    vi.stubGlobal("fetch", fetch);
    render(<ReportPage me={ME} name="income_statement" />);

    await vi.waitFor(() => expect(asked(fetch).some((path) => path.includes("arg=o1&"))).toBe(true));
    fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "o2" } });
    expect((await screen.findByRole("table", { name: "Income statement" })).textContent).toContain("Owner 2");
    releaseFirst();
    await new Promise((resolve) => setTimeout(resolve, 20));

    const shown = screen.getByRole("table", { name: "Income statement" }).textContent;
    expect(shown).toContain("Owner 2");
    expect(shown).not.toContain("Owner 1");
  });

  it("asks for what is missing, says how many lines are left out, and says why it was refused", async () => {
    window.history.replaceState(null, "", "/reports/vendor_payments");
    server({
      [`/api/reports/trust_report_vendor_payments?arg=${new Date().getUTCFullYear()}&format=json`]: { ...ROLL, total_rows: 900 },
      "/api/reports/trust_report_vendor_payments?arg=20x6&format=json": { detail: "invalid input syntax for type integer" },
    });
    render(<ReportPage me={ME} name="vendor_payments" />);
    expect((await screen.findByTestId("more")).textContent).toBe("The first 2 of 900 lines; Export has them all.");
    fireEvent.change(screen.getByLabelText("Year"), { target: { value: "" } });
    expect(await screen.findByText("Choose what the report covers.")).toBeTruthy();
    expect(screen.queryByLabelText("Export")).toBeNull();
    fireEvent.change(screen.getByLabelText("Year"), { target: { value: "20x6" } });
    expect((await screen.findByRole("alert")).textContent).toBe("invalid input syntax for type integer");
    cleanup();

    render(<ReportPage me={ME} name="nope" />);
    expect(screen.getByRole("alert").textContent).toBe("No report by that name.");
  });
});
