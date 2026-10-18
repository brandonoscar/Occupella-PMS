import { describe, expect, it } from "vitest";
import type { Me } from "./api";
import type { Prepared, ReportRow } from "./reconcile";
import {
  approver,
  byDay,
  difference,
  figures,
  formatOf,
  fromCents,
  itemLabel,
  items,
  lastDayOf,
  lineLabel,
  sum,
  toCents,
  toggleTransfer,
  transferLabel,
} from "./reconcile";

const ROWS: ReportRow[] = [
  { line: 1, item: "PMC", detail: "Synthetic PMC", amount: null },
  { line: 4, item: "bank balance (statements)", detail: null, amount: "4075.00" },
  { line: 9, item: "difference: adjusted bank balance - trust journal", detail: null, amount: "65.00" },
  { line: 10, item: "deposit in transit", detail: "2026-01-31 Late fee", amount: "50.00" },
];

describe("reconcile", () => {
  it("adds money exactly, in cents", () => {
    expect(toCents("1,234.5")).toBe(123450);
    expect(toCents("-0.05")).toBe(-5);
    expect(toCents("7")).toBe(700);
    expect(fromCents(-5)).toBe("-0.05");
    expect(fromCents(123450)).toBe("1234.50");
    expect(sum(["0.10", "0.20"])).toBe("0.30");
    expect(sum(["2750.00", "-240.00", "-0.01"])).toBe("2509.99");
    expect(sum([])).toBe("0.00");
    expect(() => toCents("1.234")).toThrow("not an amount");
  });

  it("reads the report's figures, items and difference", () => {
    expect(figures(ROWS)).toEqual({
      "bank balance (statements)": "4075.00",
      "difference: adjusted bank balance - trust journal": "65.00",
    });
    expect(items(ROWS).map((r) => r.item)).toEqual(["deposit in transit"]);
    expect(difference(ROWS)).toBe("65.00");
    expect(difference([])).toBeNull();
  });

  it("tells a statement's format by its file name", () => {
    expect(formatOf("january.OFX")).toBe("ofx");
    expect(formatOf("january.qfx")).toBe("ofx");
    expect(formatOf("january.csv")).toBe("csv");
    expect(formatOf("january.txt")).toBe("csv");
  });

  it("reads lines, transfers and a period's last day", () => {
    expect(lineLabel({ id: "l", posted_on: "2026-01-05", amount: "2750.00", description: "Deposit", reference: "D1" }))
      .toBe("01/05/2026 Deposit (D1): $2,750.00");
    expect(lineLabel({ id: "l", posted_on: "2026-01-05", amount: "-5.00", description: "", reference: null }))
      .toBe("01/05/2026 No description: ($5.00)");
    expect(transferLabel({ id: "t", on: "2026-01-04", amount: "1500.00", memo: "Rent" }))
      .toBe("01/04/2026 Rent: $1,500.00");
    expect(transferLabel({ id: "t", on: "2026-01-04", amount: "1.00", memo: null }))
      .toBe("01/04/2026 No memo: $1.00");
    expect(transferLabel({ id: "t", on: "2026-01-04", amount: "200.00", memo: "Rent", deposit_id: "d1", deposited_on: "2026-01-05" }))
      .toBe("01/04/2026 Rent: $200.00 · deposit of 01/05/2026");
    expect(lastDayOf("2026-02-01T00:00:00Z")).toBe("2026-01-31");
    expect(itemLabel({ id: "i", issued_on: "2025-12-30", amount: "-200.00", description: "Check", reference: "1041" }))
      .toBe("12/30/2025 Check (1041), from before the cutover: ($200.00)");
    expect(itemLabel({ id: "i", issued_on: "2025-12-31", amount: "75.00", description: "Deposit", reference: null }))
      .toBe("12/31/2025 Deposit, from before the cutover: $75.00");
  });

  it("lets anyone but the preparer approve, holding the approve role", () => {
    const me: Me = { kind: "staff", email: "s@example.com", name: "Staff 1", person_id: "s1", pmc_id: "p1", pmc_name: "PMC" };
    const prepared: Prepared = {
      id: "pr1", through: "2026-01-31", bank_balance: "1.00", book_cash: "1.00", prepared_by: "Staff 2",
      preparer_id: "s2", prepared_at: "2026-02-01T00:00:00Z",
    };
    expect(approver({ ...me, roles: ["approve"] }, prepared)).toBe("may");
    expect(approver({ ...me, roles: ["admin", "disburse"] }, prepared)).toBe("may not");
    expect(approver(me, prepared)).toBe("may not");
    expect(approver({ ...me, person_id: "s2", roles: ["approve"] }, prepared)).toBe("preparer");
  });
});

describe("byDay", () => {
  it("lists the matches newest first, by their bank line's day, else their transfer's or item's", () => {
    const line = (posted_on: string) => ({ id: posted_on, posted_on, amount: "1.00", description: "", reference: null });
    const matches = [
      { id: "a", lines: [line("2026-09-03")], transfers: [], items: [] },
      { id: "b", lines: [], transfers: [{ id: "t", on: "2026-09-20", amount: "1.00", memo: null }], items: [] },
      { id: "c", lines: [line("2026-09-10")], transfers: [], items: [] },
      { id: "d", lines: [], transfers: [], items: [{ id: "i", issued_on: "2025-12-29", amount: "1.00", description: "Old", reference: null }] },
    ];
    expect(byDay(matches).map((m) => m.id)).toEqual(["b", "c", "a", "d"]);
    expect(matches.map((m) => m.id)).toEqual(["a", "b", "c", "d"]); // the API's order is left alone
  });
});

describe("toggleTransfer", () => {
  const all = [
    { id: "a", on: "2026-01-04", amount: "100.00", memo: null, deposit_id: "d1", deposited_on: "2026-01-05" },
    { id: "b", on: "2026-01-04", amount: "50.00", memo: null, deposit_id: "d1", deposited_on: "2026-01-05" },
    { id: "c", on: "2026-01-04", amount: "20.00", memo: null, deposit_id: null, deposited_on: null },
  ];
  it("ticks and unticks a whole deposit, and a lone transfer by itself", () => {
    expect(toggleTransfer(all, [], "a")).toEqual(["a", "b"]);
    expect(toggleTransfer(all, ["a", "b", "c"], "b")).toEqual(["c"]);
    expect(toggleTransfer(all, ["a", "b"], "c")).toEqual(["a", "b", "c"]);
    expect(toggleTransfer(all, ["c"], "c")).toEqual([]);
  });
});
