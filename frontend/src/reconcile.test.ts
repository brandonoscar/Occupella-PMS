import { describe, expect, it } from "vitest";
import type { Me } from "./api";
import type { Prepared, ReportRow, Unmatched } from "./reconcile";
import {
  approver,
  byDay,
  countedTwice,
  difference,
  figures,
  formatOf,
  fromCents,
  itemLabel,
  itemWord,
  items,
  lastDayOf,
  lineLabel,
  nextSteps,
  standing,
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

  it("names an item that may only wait to be matched as not matched yet", () => {
    const row = (item: string, pending?: boolean): ReportRow => ({ line: 10, item, detail: "d", amount: "1.00", pending });
    expect(itemWord(row("bank line not in the books", true))).toBe("bank line not matched yet");
    expect(itemWord(row("deposit in transit", true))).toBe("deposit not matched yet");
    expect(itemWord(row("outstanding withdrawal", true))).toBe("withdrawal not matched yet");
    expect(itemWord(row("bank line not in the books", false))).toBe("bank line not in the books");
    expect(itemWord(row("deposit in transit"))).toBe("deposit in transit");
  });

  const unmatched = (u: Partial<Unmatched>): Unmatched => ({
    lines: 0, total: "0.00", sure: 0, likely: 0, likely_total: "0.00", alone: 0, alone_total: "0.00", ...u,
  });

  it("is never off by a double count before matching", () => {
    expect(standing("0.00", unmatched({ lines: 3 }))).toEqual({ tone: "positive", word: "Ties out" });
    expect(standing("54625.00", unmatched({ lines: 41 }))).toEqual({ tone: "caution", word: "41 bank lines not matched" });
    expect(standing("10.00", unmatched({ lines: 1 }))).toEqual({ tone: "caution", word: "1 bank line not matched" });
    // Every line matched, and still a difference: off by it.
    expect(standing("10.00", unmatched({}))).toEqual({ tone: "caution", word: "Off by $10.00" });
    expect(standing("-10.00", null)).toEqual({ tone: "caution", word: "Off by ($10.00)" });
  });

  it("says to match first, and to record only a line with nothing of its amount in the books", () => {
    // The walkthrough: every line has its transfer in the books.
    expect(nextSteps("54625.00", unmatched({ lines: 41, sure: 41, likely: 41, likely_total: "54625.00" }))).toEqual([
      "41 bank lines of $54,625.00 have a transfer of the same amount in the books, so they are likely there"
        + " already: Match the sure ones pairs all of them.",
    ]);
    expect(nextSteps("1500.00", unmatched({ lines: 1, sure: 1, likely: 1, likely_total: "1500.00" }))).toEqual([
      "1 bank line of $1,500.00 has a transfer of the same amount in the books, so it is likely there already:"
        + " Match the sure ones pairs it.",
    ]);
    expect(nextSteps("1400.00", unmatched({ lines: 2, likely: 2, likely_total: "1400.00" }))).toEqual([
      "2 bank lines of $1,400.00 have a transfer of the same amount in the books, so they are likely there"
        + " already: match them by hand.",
    ]);
    expect(nextSteps("-15.00", unmatched({ lines: 1, alone: 1, alone_total: "-15.00" }))).toEqual([
      "1 bank line of ($15.00) has nothing of its amount in the books: match it to the transfers it adds up to,"
        + " or, if the books lack it, record it and then match it.",
    ]);
    expect(nextSteps("0.00", unmatched({ lines: 2, likely: 2 }))).toEqual([]);
    expect(nextSteps("10.00", unmatched({}))).toEqual(["Off by $10.00: the reconciling items say where."]);
  });

  it("says the figures count money twice only while a line has its transfer to match", () => {
    expect(countedTwice(unmatched({ lines: 2, likely: 1 }))).toMatch(/^Until they are matched, money both the bank/);
    expect(countedTwice(unmatched({ lines: 2, alone: 2 }))).toBeNull();
    expect(countedTwice(null)).toBeNull();
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
