// Property tests (fast-check, MIT; the portal's side of what Hypothesis does for the server):
// for any amount, day or scope fast-check makes up, money reads back as the cents it came as,
// sums are exact, days keep their parts, and a list's scope survives the round trip through its
// URL.
import { test } from "@fast-check/vitest";
import fc from "fast-check";
import { describe, expect } from "vitest";
import { day, money, sumMoney, usDate } from "./money";
import { scopeOf, scopeParams } from "./scope";

// An amount as the API sends it: text, whole cents, maybe negative, past what a float holds.
const CENTS = fc.bigInt({ min: -(10n ** 20n), max: 10n ** 20n });
const text = (cents: bigint) => {
  const abs = cents < 0n ? -cents : cents;
  return `${cents < 0n ? "-" : ""}${abs / 100n}.${String(abs % 100n).padStart(2, "0")}`;
};

describe("money", () => {
  test.prop([CENTS])("reads back as the cents it came as, a negative in parentheses", (cents) => {
    const shown = money(text(cents));
    const digits = shown.replace(/[$,()]/g, "");
    expect(BigInt(digits.replace(".", ""))).toBe(cents < 0n ? -cents : cents);
    expect(shown.startsWith("(")).toBe(cents < 0n);
  });

  test.prop([fc.array(CENTS, { maxLength: 30 })])("sums to the exact cents, in any order", (all) => {
    const total = all.reduce((sum, cents) => sum + cents, 0n);
    expect(sumMoney(all.map(text))).toBe(text(total));
    expect(sumMoney([...all].reverse().map(text))).toBe(text(total));
  });
});

describe("days", () => {
  const DAYS = fc.date({ min: new Date(Date.UTC(1900, 0, 1)), max: new Date(Date.UTC(2999, 11, 31)), noInvalidDate: true });

  test.prop([DAYS])("a day keeps its year, month and day from YYYY-MM-DD to MM/DD/YYYY", (when) => {
    const iso = day(when);
    expect(iso).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    const [year, month, date] = iso.split("-");
    expect(usDate(iso)).toBe(`${month}/${date}/${year}`);
    expect(new Date(`${iso}T00:00:00Z`).getUTCDate()).toBe(when.getUTCDate());
  });
});

describe("scope", () => {
  const ID = fc.uuid();
  const SCOPES = fc.oneof(
    fc.constant({}),
    ID.map((group) => ({ group })),
    ID.map((property) => ({ property })),
  );

  test.prop([SCOPES])("a list's scope survives its URL", (scope) => {
    expect(scopeOf(`?${new URLSearchParams(scopeParams(scope))}`)).toEqual(scope);
  });

  test.prop([ID, ID])("a group wins over a property, never both", (group, property) => {
    expect(scopeOf(`?${new URLSearchParams({ group, property })}`)).toEqual({ group });
  });
});
