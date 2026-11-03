// The Dashboard's figures as the API sends them (src/pms/api/dashboard.py), and its tiles: only
// numbers that change, each beside what it was the same day a month before (ADR 0010 phase 3).
import { money, sumMoney, usDate } from "./money";

export type Dashboard = {
  on: string;
  month_ago: string;
  // Units with a lease in effect, of all units not archived, a percent to one place; null with
  // no unit.
  occupancy: { percent: string | null; then_percent: string | null };
  // Rent due from the month's first day through the day, and what of it was paid by then.
  rent: { collected: string; due: string; then_collected: string; then_due: string };
  past_due: { total: string; tenants: number; then_total: string; then_tenants: number };
  leases_ending: { within_60: number; then_within_60: number };
  vacant: { units: number; average_days: number | null; then_units: number; then_average_days: number | null };
  // The next draw day and what a draw of every owner's account would pay now; the last draws.
  next_draw: { on: string | null; total: string | null; last_total: string | null };
  unreconciled: { accounts: number; then_accounts: number };
};

export type Tile = { label: string; value: string; detail: string; href: string };

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

// "Up 2 from 09/25/2026", "Down 1 from 09/25/2026" or "Same as 09/25/2026".
export function countChange(now: number, then: number, on: string): string {
  if (now === then) return `Same as ${usDate(on)}`;
  return `${now > then ? "Up" : "Down"} ${Math.abs(now - then)} from ${usDate(on)}`;
}

// "Up $500.00 from 09/25/2026": money's change, worked out in cents, never as a float.
export function moneyChange(now: string, then: string, on: string): string {
  const change = sumMoney([now, `-${then}`]);
  if (/^-?0\.00$/.test(change)) return `Same as ${usDate(on)}`;
  const down = change.startsWith("-");
  return `${down ? "Down" : "Up"} ${money(change.replace(/^-/, ""))} from ${usDate(on)}`;
}

// "Up 2.5 points from 09/25/2026": a percent's change, both to one place.
export function pointsChange(now: string, then: string | null, on: string): string {
  if (then === null) return `No units on ${usDate(on)}`;
  const tenths = Math.round(Number(now) * 10) - Math.round(Number(then) * 10);
  if (tenths === 0) return `Same as ${usDate(on)}`;
  const points = (Math.abs(tenths) / 10).toFixed(1);
  return `${tenths > 0 ? "Up" : "Down"} ${points} point${points === "1.0" ? "" : "s"} from ${usDate(on)}`;
}

// One tile for each figure, each opening the page where that work is done.
export function tiles(d: Dashboard): Tile[] {
  const then = d.month_ago;
  const draw = d.next_draw;
  const average = (days: number | null) => (days === null ? "" : `, ${count(days, "day", "days")} vacant on average`);
  return [
    {
      label: "Occupancy",
      value: d.occupancy.percent === null ? "No units" : `${d.occupancy.percent}%`,
      detail: d.occupancy.percent === null ? "Add a property and its units" : pointsChange(d.occupancy.percent, d.occupancy.then_percent, then),
      href: "/properties",
    },
    {
      label: "Rent collected this month",
      value: `${money(d.rent.collected)} of ${money(d.rent.due)}`,
      detail: `${money(d.rent.then_collected)} of ${money(d.rent.then_due)} by ${usDate(then)}`,
      href: "/rent-roll",
    },
    {
      label: "Past due",
      value: money(d.past_due.total),
      detail: `${count(d.past_due.tenants, "tenant", "tenants")}; ${moneyChange(d.past_due.total, d.past_due.then_total, then)}`,
      href: "/rent-roll",
    },
    {
      label: "Leases ending in 60 days",
      value: String(d.leases_ending.within_60),
      detail: countChange(d.leases_ending.within_60, d.leases_ending.then_within_60, then),
      href: "/renewals",
    },
    {
      label: "Vacant units",
      value: String(d.vacant.units) + average(d.vacant.average_days),
      detail: countChange(d.vacant.units, d.vacant.then_units, then),
      href: "/rental-listings",
    },
    {
      label: "Next draw",
      value: draw.on === null ? "No draw day" : usDate(draw.on),
      detail: draw.on === null
        ? "Set the draw day in Company setup"
        : `${money(draw.total)} available to pay now` + (draw.last_total === null ? "" : `; the last draws prepared came to ${money(draw.last_total)}`),
      href: draw.on === null ? "/settings/setup" : "/owner-draws",
    },
    {
      label: "Accounts not reconciled",
      value: String(d.unreconciled.accounts),
      detail: countChange(d.unreconciled.accounts, d.unreconciled.then_accounts, then),
      href: "/banking",
    },
  ];
}
