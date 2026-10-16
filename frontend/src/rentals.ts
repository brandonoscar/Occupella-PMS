// Rentals as the API sends them (src/pms/api/rentals.py): pages of properties and leases, a
// property, a unit, and search results; and how they read.
import { money } from "./money";

export type Paged<T> = { items: T[]; total: number; page: number; per_page: number };
export type Ref = { id: string; name: string };

export type PropertyRow = {
  id: string;
  name: string;
  owners: string[];
  units: number;
  occupied: number;
  archived: boolean;
};

export type LeaseStatus = "current" | "future" | "ended";
export type LeaseLine = {
  id: string;
  tenants: string[];
  starts_on: string;
  ends_on: string | null;
  monthly_rent: string;
  owed: string;
  status: LeaseStatus;
};
export type UnitLine = { id: string; name: string; archived: boolean; lease: LeaseLine | null };
export type OwnerAccount = {
  owner_id: string;
  owner: string;
  account: string;
  bank_account: string;
  balance: string;
};
export type Property = {
  id: string;
  name: string;
  archived: boolean;
  owners: OwnerAccount[];
  units: UnitLine[];
};
export type Unit = { id: string; name: string; archived: boolean; property: Ref; leases: LeaseLine[] };

export type RentRollRow = {
  id: string;
  property: Ref;
  unit: Ref;
  tenants: string[];
  starts_on: string;
  ends_on: string | null;
  monthly_rent: string;
  owed: string;
};

export type Found = {
  kind: "property" | "unit" | "tenant" | "owner";
  id: string;
  label: string;
  detail: string;
  href: string;
};

// "1–50 of 120", or "None" for an empty page.
export function pageLabel(page: Paged<unknown>): string {
  if (page.total === 0) return "None";
  const first = (page.page - 1) * page.per_page + 1;
  const last = Math.min(page.total, first + page.items.length - 1);
  return `${first}–${last} of ${page.total}`;
}

export function hasNext(page: Paged<unknown>): boolean {
  return page.page * page.per_page < page.total;
}

// "4 of 5 occupied", "Vacant" for none, "No units" when there are none.
export function occupancy(row: PropertyRow): string {
  if (row.units === 0) return "No units";
  if (row.occupied === 0) return "Vacant";
  return `${row.occupied} of ${row.units} occupied`;
}

// "From 2026-01-01, month to month", "2026-01-01 to 2026-12-31".
export function termLabel(lease: { starts_on: string; ends_on: string | null }): string {
  return lease.ends_on ? `${lease.starts_on} to ${lease.ends_on}` : `From ${lease.starts_on}, month to month`;
}

// A unit's state as a badge: occupied, a lease to come, vacant, or archived.
export function unitState(unit: UnitLine): { word: string; tone: "positive" | "info" | "neutral" | "caution" } {
  if (unit.archived) return { word: "Archived", tone: "neutral" };
  if (unit.lease === null) return { word: "Vacant", tone: "caution" };
  if (unit.lease.status === "future") return { word: `Starts ${unit.lease.starts_on}`, tone: "info" };
  return { word: "Occupied", tone: "positive" };
}

export const LEASE_STATUS: Record<LeaseStatus, { word: string; tone: "positive" | "info" | "neutral" }> = {
  current: { word: "Current", tone: "positive" },
  future: { word: "Future", tone: "info" },
  ended: { word: "Ended", tone: "neutral" },
};

// "$1,500.00 a month".
export function rentLabel(monthly: string): string {
  return `${money(monthly)} a month`;
}
