// Money arrives from the API as text ("1234.50"), so no cent is lost on the way. It is shown as
// US dollars, a negative amount in parentheses, as accounting writes it (ADR 0005): ($75.00).
const DOLLARS = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  currencySign: "accounting",
});

export function money(amount: string | null): string {
  if (amount === null || amount === "") return "";
  const value = Number(amount);
  if (!Number.isFinite(value)) return amount;
  return DOLLARS.format(value);
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
