import { describe, expect, it } from "vitest";
import { itemLabel, readLedger } from "./ledger";

// The golden tenant ledger (tests/golden/cases/trust_report_tenant_ledger/april), as the API
// sends it.
const COLUMNS = ["line", "on_date", "item", "detail", "charged", "paid", "balance"];
const ROWS: (string | null)[][] = [
  ["1", null, "lease", "Unit 1, 2026-01-01, month to month", null, null, null],
  ["2", "2026-01-01", "rent", "January rent", "1500.00", "0.00", "1500.00"],
  ["3", "2026-01-03", "payment", "Check 101", "0.00", "1500.00", "0.00"],
  ["4", "2026-03-05", "payment", null, "0.00", "1000.00", "500.00"],
  ["5", "2026-04-30", "balance", "end of day UTC", "6000.00", "4000.00", "2000.00"],
];

describe("readLedger", () => {
  it("splits the lease, the entries and the closing balance", () => {
    const ledger = readLedger({ title: "t", columns: COLUMNS, rows: ROWS });

    expect(ledger.lease).toBe("Unit 1, 2026-01-01, month to month");
    expect(ledger.through).toBe("2026-04-30");
    expect(ledger.totals).toEqual({ charged: "6000.00", paid: "4000.00", balance: "2000.00" });
    expect(ledger.entries).toEqual([
      { on: "2026-01-01", item: "rent", detail: "January rent", charged: "1500.00", paid: "0.00", balance: "1500.00" },
      { on: "2026-01-03", item: "payment", detail: "Check 101", charged: "0.00", paid: "1500.00", balance: "0.00" },
      { on: "2026-03-05", item: "payment", detail: "", charged: "0.00", paid: "1000.00", balance: "500.00" },
    ]);
  });

  it("finds its columns by name, and says which is missing", () => {
    const reordered = COLUMNS.slice().reverse();
    const rows = ROWS.map((row) => row.slice().reverse());

    expect(readLedger({ title: "t", columns: reordered, rows }).totals.balance).toBe("2000.00");
    expect(() => readLedger({ title: "t", columns: ["line"], rows: [] })).toThrow(
      "the ledger has no on_date column",
    );
  });
});

describe("itemLabel", () => {
  it("names each kind of line", () => {
    expect(["rent", "fee", "credit", "payment", "bounced payment", "other"].map(itemLabel)).toEqual(
      ["Rent", "Fee", "Credit", "Payment", "Payment returned", "other"],
    );
  });
});
