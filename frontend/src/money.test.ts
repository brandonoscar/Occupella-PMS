import { describe, expect, it } from "vitest";
import { day, lastMonth, money } from "./money";

describe("money", () => {
  it("shows dollars and cents from the API's text, negatives in parentheses", () => {
    expect(money("1234.5")).toBe("$1,234.50");
    expect(money("-150.00")).toBe("($150.00)");
    expect(money("0.00")).toBe("$0.00");
    expect(money("999999999999.99")).toBe("$999,999,999,999.99");
  });

  it("shows nothing for a missing amount, and odd text as it came", () => {
    expect(money(null)).toBe("");
    expect(money("")).toBe("");
    expect(money("n/a")).toBe("n/a");
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
