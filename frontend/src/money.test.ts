import { describe, expect, it } from "vitest";
import { day, lastMonth, money, sumMoney } from "./money";

describe("money", () => {
  it("shows dollars and cents from the API's text, negatives in parentheses", () => {
    expect(money("1234.5")).toBe("$1,234.50");
    expect(money("-150.00")).toBe("($150.00)");
    expect(money("0.00")).toBe("$0.00");
    expect(money("999999999999.99")).toBe("$999,999,999,999.99");
  });

  it("formats the text itself, so no amount passes through a float", () => {
    // As a float this is 12345678901234568, and its cents are gone.
    expect(money("12345678901234567.89")).toBe("$12,345,678,901,234,567.89");
    expect(money("-0.10")).toBe("($0.10)");
  });

  it("shows nothing for a missing amount, and odd text as it came", () => {
    expect(money(null)).toBe("");
    expect(money("")).toBe("");
    expect(money("n/a")).toBe("n/a");
    expect(money("1e3")).toBe("1e3");
  });
});

describe("lastMonth", () => {
  it("is the whole month before today, in UTC", () => {
    expect(lastMonth(new Date(Date.UTC(2026, 2, 15)))).toEqual({
      start: "2026-02-01",
      end: "2026-02-28",
    });
    expect(lastMonth(new Date(Date.UTC(2026, 0, 1)))).toEqual({
      start: "2025-12-01",
      end: "2025-12-31",
    });
  });

  it("writes days as YYYY-MM-DD", () => {
    expect(day(new Date(Date.UTC(2024, 1, 29)))).toBe("2024-02-29");
  });
});

describe("sumMoney", () => {
  it("adds amounts exactly, in whole cents", () => {
    expect(sumMoney(["0.10", "0.20"])).toBe("0.30");
    expect(sumMoney(["1200.5", "-0.75", "3"])).toBe("1202.75");
    expect(sumMoney(["-1.00", "0.25"])).toBe("-0.75");
    expect(sumMoney([])).toBe("0.00");
  });
});
