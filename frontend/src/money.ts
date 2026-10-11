// Money arrives from the API as text ("1234.50"), so no cent is lost on the way. It is shown as
// US dollars; a negative amount reads with a minus sign.
const DOLLARS = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

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
