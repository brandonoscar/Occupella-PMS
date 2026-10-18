// Rentals as the API sends them (src/pms/api/rentals.py): pages of properties and leases, a
// property, a unit, and search results; and how they read.
import { money, usDate } from "./money";
import type { Hold, RentLease } from "./rent";
import type { Agreement, LateFees, LeaseBreakFee, NsfFee } from "./setup";

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
  // The same tenants with their ids, each opening their own page.
  people: Ref[];
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
  // The management terms in force today, if any.
  agreement: Agreement | null;
};
export type Property = {
  id: string;
  name: string;
  archived: boolean;
  owners: OwnerAccount[];
  units: UnitLine[];
  // The late fee, NSF fee and lease-break fee terms in force today, if any.
  late_fees: LateFees | null;
  nsf_fee: NsfFee | null;
  lease_break_fee: LeaseBreakFee | null;
};
export type Unit = { id: string; name: string; archived: boolean; property: Ref; leases: LeaseLine[] };

export type RentRollRow = {
  id: string;
  property: Ref;
  unit: Ref;
  tenants: string[];
  people: Ref[];
  starts_on: string;
  ends_on: string | null;
  status: LeaseLine["status"];
  // Days from today to its last day, for a lease in effect with an end date.
  days_left: number | null;
  monthly_rent: string;
  // The security deposits held for its tenants.
  deposit: string;
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
// A lease's term as Buildium reads it: "11/01/2025 – 10/31/2026", or "11/01/2025 – Month to month".
export function termLabel(lease: { starts_on: string; ends_on: string | null }): string {
  return `${usDate(lease.starts_on)} – ${lease.ends_on ? usDate(lease.ends_on) : "Month to month"}`;
}

// A unit's state as a badge: occupied, a lease to come, vacant, or archived.
export function unitState(unit: UnitLine): { word: string; tone: "positive" | "info" | "neutral" | "caution" } {
  if (unit.archived) return { word: "Archived", tone: "neutral" };
  if (unit.lease === null) return { word: "Vacant", tone: "caution" };
  if (unit.lease.status === "future") return { word: `Starts ${usDate(unit.lease.starts_on)}`, tone: "info" };
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

// Whether a tenant can sign in to the portal (src/pms/api/rentals.py PORTAL_STATUS).
export type Portal = "no login" | "invited" | "active" | "disabled";
export const PORTAL: Record<Portal, { word: string; tone: "positive" | "info" | "neutral" }> = {
  active: { word: "Active", tone: "positive" },
  invited: { word: "Invited", tone: "info" },
  "no login": { word: "None", tone: "neutral" },
  disabled: { word: "Disabled", tone: "neutral" },
};

export type LeasePerson = {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  portal: Portal;
  login_email: string | null;
  // The day they left while others stayed on the lease, and who took over their deposit then.
  left_on: string | null;
  deposit_to: string | null;
};

// Who else lives there and who guarantees the lease (GET /api/leases/{id}/household).
export type Occupant = { id: string; name: string; relationship: string | null };
export type Pet = {
  id: string;
  name: string;
  kind: string;
  breed: string | null;
  weight_lbs: string | null;
  // Not a pet under the Fair Housing Act: no pet rent, fee or deposit is charged for it.
  assistance_animal: boolean;
};
export type Vehicle = {
  id: string;
  make: string;
  model: string | null;
  color: string | null;
  plate: string;
  plate_state: string | null;
};
export type Guarantor = { id: string; name: string; email: string | null; phone: string | null; signed: boolean };
export type Household = { occupants: Occupant[]; pets: Pet[]; vehicles: Vehicle[]; guarantors: Guarantor[] };

// What ending a lease on a day would charge as its lease-break fee (GET /api/move-out/{id}/break-fee).
export type BreakFee = { early: boolean; fee: string | null; flat_fee: string | null; rent_months: string | null };

// A pet as a line: "Pet 1, Dog (Breed 1), 42.5 lb".
export function petLine(pet: Pet): string {
  const kind = pet.breed ? `${pet.kind} (${pet.breed})` : pet.kind;
  return [pet.name, kind, pet.weight_lbs ? `${pet.weight_lbs} lb` : ""].filter(Boolean).join(", ");
}

// A vehicle as a line: "Make 1 Model 1, Blue, SYN-001 (State 1)".
export function vehicleLine(vehicle: Vehicle): string {
  const plate = vehicle.plate_state ? `${vehicle.plate} (${vehicle.plate_state})` : vehicle.plate;
  return [[vehicle.make, vehicle.model].filter(Boolean).join(" "), vehicle.color ?? "", plate]
    .filter(Boolean).join(", ");
}

// A lease's own page (GET /api/leases/{id}): its rent work, where it stands and its people.
export type LeaseDetail = RentLease & {
  property_id: string;
  unit_id: string;
  status: LeaseStatus;
  people: LeasePerson[];
  // Its payment holds (an eviction filed), newest first.
  holds: Hold[];
};

export type LedgerKind =
  | "rent" | "recurring" | "late fee" | "NSF fee" | "fee" | "credit" | "payment" | "bounced payment";
export const LEDGER_KIND: Record<LedgerKind, string> = {
  rent: "Rent",
  recurring: "Monthly charge",
  "late fee": "Late fee",
  "NSF fee": "NSF fee",
  fee: "Charge",
  credit: "Credit",
  payment: "Payment",
  "bounced payment": "Bounced payment",
};
export type LedgerLine = {
  on: string;
  kind: LedgerKind;
  memo: string | null;
  charged: string | null;
  paid: string | null;
  balance: string;
  bounced: boolean;
};
// A lease's ledger through today (GET /api/leases/{id}/ledger).
export type Ledger = { lines: LedgerLine[]; balance: string };
