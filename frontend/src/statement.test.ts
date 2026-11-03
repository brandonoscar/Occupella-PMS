import { describe, expect, it } from "vitest";
import { describePosting, group, periodLabel } from "./statement";

// The golden owner statement (tests/golden/cases/trust_report_owner_statement/january), as
// the API sends it.
const COLUMNS = ["line", "property", "posted_on", "item", "detail", "amount", "balance"];
const ROWS: (string | null)[][] = [
  ["1", null, null, "PMC", "Golden PMC", null, null],
  ["2", null, null, "owner", "Owner 1", null, null],
  ["3", null, null, "period", "2026-01-01 00:00:00 to 2026-02-01 00:00:00 UTC", null, null],
  ["4", "Property 1", null, "opening balance", null, null, "1000.00"],
  ["5", "Property 1", "2026-01-03", "posting", "bank_cash: rent", "1500.00", "2500.00"],
  ["6", "Property 1", "2026-01-05", "posting", "pmc_income: fee", "-150.00", "2350.00"],
  ["7", "Property 1", null, "closing balance", null, null, "2350.00"],
  ["8", "Property 2", null, "opening balance", null, null, "0.00"],
  ["9", "Property 2", null, "closing balance", null, null, "0.00"],
  ["10", null, null, "all properties: opening balance", null, null, "1000.00"],
  ["11", null, null, "all properties: money in", null, "1500.00", null],
  ["12", null, null, "all properties: money out", null, "-150.00", null],
  ["13", null, null, "all properties: closing balance", null, null, "2350.00"],
];

describe("group", () => {
  it("puts each property's postings between its opening and closing balance", () => {
    const grouped = group({ title: "t", columns: COLUMNS, rows: ROWS });

    expect(grouped.period).toBe("2026-01-01 00:00:00 to 2026-02-01 00:00:00 UTC");
    expect(grouped.properties).toEqual([
      {
        name: "Property 1",
        opening: "1000.00",
        postings: [
          { postedOn: "2026-01-03", detail: "bank_cash: rent", amount: "1500.00", balance: "2500.00" },
          { postedOn: "2026-01-05", detail: "pmc_income: fee", amount: "-150.00", balance: "2350.00" },
        ],
        closing: "2350.00",
      },
      { name: "Property 2", opening: "0.00", postings: [], closing: "0.00" },
    ]);
    expect(grouped.totals).toEqual({
      opening: "1000.00",
      moneyIn: "1500.00",
      moneyOut: "-150.00",
      closing: "2350.00",
    });
  });

  it("finds the columns by name, whatever their order", () => {
    const order = [6, 5, 4, 3, 2, 1, 0];
    const columns = order.map((i) => COLUMNS[i] as string);
    const rows = ROWS.map((row) => order.map((i) => row[i] ?? null));

    expect(group({ title: "t", columns, rows }).totals.closing).toBe("2350.00");
  });

  it("refuses a table that isn't a statement", () => {
    expect(() => group({ title: "t", columns: ["line"], rows: [] })).toThrow("no property column");
  });
});

describe("describePosting", () => {
  it("says what each posting was and with whom, not the ledger's kind", () => {
    const cases: [string, string, { what: string; with: string }][] = [
      ["bank_cash: Operating trust account", "1430.00", { what: "Received", with: "Operating trust account" }],
      ["bank_cash: Operating trust account", "-20.00", { what: "Paid out", with: "Operating trust account" }],
      ["bank_cash: Operating trust account - Owner draw", "-2796.80", { what: "Owner draw", with: "Operating trust account" }],
      [
        "pmc_income: Synthetic PMC - Management fee 2026-09-01 to 2026-09-30",
        "-243.20",
        { what: "Management fee 2026-09-01 to 2026-09-30", with: "Synthetic PMC" },
      ],
      ["vendor_payable: Vendor 1 - Bill RF-1 set aside", "-650.00", { what: "Bill RF-1", with: "Vendor 1" }],
      ["pmc_income: Synthetic PMC", "-10.00", { what: "Management company", with: "Synthetic PMC" }],
      ["vendor_payable: Vendor 1", "-10.00", { what: "Vendor", with: "Vendor 1" }],
      ["owner_property: Owner 2 / Property 2", "10.00", { what: "Transfer", with: "Owner 2 / Property 2" }],
      ["prepaid_rent: Tenant 1", "10.00", { what: "Prepaid rent", with: "Tenant 1" }],
      ["tenant_deposit: Tenant 1", "10.00", { what: "Security deposit", with: "Tenant 1" }],
    ];
    for (const [detail, amount, said] of cases) expect(describePosting(detail, amount)).toEqual(said);
  });

  it("shows anything it doesn't know as it came", () => {
    expect(describePosting("Opening balance", "1.00")).toEqual({ what: "Opening balance", with: "" });
    expect(describePosting("something_else: X", "1.00")).toEqual({ what: "something_else: X", with: "" });
  });
});

describe("periodLabel", () => {
  it("reads the report's period, its end not included, in words", () => {
    expect(periodLabel("2026-09-01 00:00:00 to 2026-10-01 00:00:00 UTC")).toBe("September 1 to September 30, 2026");
    expect(periodLabel("2025-12-01 00:00:00 to 2026-02-01 00:00:00 UTC")).toBe(
      "December 1, 2025 to January 31, 2026",
    );
    expect(periodLabel("2026-03-05 00:00:00 to 2026-03-06 00:00:00 UTC")).toBe("March 5 to March 5, 2026");
  });

  it("shows any other period as it came", () => {
    expect(periodLabel("2026-09-01 12:00:00 to 2026-10-01 00:00:00 UTC")).toBe(
      "2026-09-01 12:00:00 to 2026-10-01 00:00:00 UTC",
    );
    expect(periodLabel("2026-10-01 00:00:00 to 2026-10-01 00:00:00 UTC")).toBe(
      "2026-10-01 00:00:00 to 2026-10-01 00:00:00 UTC",
    );
  });
});
