// The Dashboard's figures as the API sends them (src/pms/api/dashboard.py), and its tiles.
import { money } from "./money";

export type Dashboard = {
  on: string;
  outstanding: string;
  leases_owing: number;
  open_work_orders: number;
  leases_ending: { within_30: number; within_60: number; within_90: number };
  vacant_units: number;
  applications_waiting: number;
};

export type Tile = { label: string; value: string; detail: string; href: string };

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

// One tile for each figure, each opening the page where that work is done.
export function tiles(d: Dashboard): Tile[] {
  const ending = d.leases_ending;
  return [
    {
      label: "Outstanding balances",
      value: money(d.outstanding),
      detail: count(d.leases_owing, "lease owes", "leases owe"),
      href: "/rent-roll",
    },
    {
      label: "Open work orders",
      value: String(d.open_work_orders),
      detail: "not completed or cancelled",
      href: "/work-orders",
    },
    {
      label: "Leases ending",
      value: String(ending.within_30 + ending.within_60 + ending.within_90),
      detail: `${ending.within_30} in 30 days, ${ending.within_60} in 60, ${ending.within_90} in 90`,
      href: "/draft-leases",
    },
    {
      label: "Vacant units",
      value: String(d.vacant_units),
      detail: "no lease in effect today",
      href: "/applicants",
    },
    {
      label: "Applications waiting",
      value: String(d.applications_waiting),
      detail: "not decided yet",
      href: "/applicants",
    },
  ];
}
