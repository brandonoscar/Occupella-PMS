// Money arrives from the API as text ("1234.50"), so no cent is lost on the way. It is shown as
// US dollars, a negative amount in parentheses, as accounting writes it (ADR 0005): ($75.00).
const DOLLARS = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  currencySign: "accounting",
});

const DECIMAL = /^-?\d+(\.\d+)?$/;

export function money(amount: string | null): string {
  if (amount === null || amount === "") return "";
  if (!DECIMAL.test(amount)) return amount;
  // The text itself, never a float: Intl formats a decimal string exactly (ECMA-402 2023).
  return DOLLARS.format(amount as `${number}`);
}

// The first and last day of the month before `today`, as YYYY-MM-DD in UTC.
export function lastMonth(today: Date): { start: string; end: string } {
  const first = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - 1, 1));
  const last = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 0));
  return { start: day(first), end: day(last) };
}

export function day(date: Date): string {
  return date.toISOString().slice(0, 10);
}

// A day as people in the US write it (ADR 0005): "2026-10-16", or a time on that day (UTC),
// reads "10/16/2026". The API, the downloads and the reports keep ISO days.
export function usDate(iso: string | null): string {
  if (!iso) return "";
  const found = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return found ? `${found[2]}/${found[3]}/${found[1]}` : iso;
}

// Days written into free text (a charge's memo "Rent due 2026-08-01", a fee's "2026-09-01 to
// 2026-09-30") read as MM/DD/YYYY on screen too; the rest of the text is as it came, and a day
// inside a longer token (a reference like "INV-2026-10-05") is left alone.
export function usDatesIn(text: string): string {
  return text.replace(/(?<![\w-])(\d{4})-(\d{2})-(\d{2})(?![\w-])/g, "$2/$3/$1");
}

// Amounts added up exactly, in whole cents, never as floats: "12.30" + "0.75" is "13.05".
export function sumMoney(amounts: string[]): string {
  let cents = 0n;
  for (const amount of amounts) {
    const negative = amount.trim().startsWith("-");
    const [whole = "0", part = ""] = amount.trim().replace(/^-/, "").split(".");
    const value = BigInt(whole || "0") * 100n + BigInt((part + "00").slice(0, 2) || "0");
    cents += negative ? -value : value;
  }
  const sign = cents < 0n ? "-" : "";
  const abs = cents < 0n ? -cents : cents;
  return `${sign}${abs / 100n}.${String(abs % 100n).padStart(2, "0")}`;
}
