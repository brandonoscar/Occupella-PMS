// The PMC's books as staff set them up (src/pms/api/setup.py), and how they read.
import { money } from "./money";

export type BankKind = "operating" | "security_deposit";
export type Named = { id: string; name: string };
export type BankAccount = Named & {
  kind: BankKind;
  opened_at: string | null;
  closed_through: string | null;
};
export type Agreement = {
  starts_on: string;
  fee_percent: string;
  minimum_fee: string;
  flat_fee: string;
  leasing_fee_percent: string;
  reserve: string;
  approval_limit: string;
};
export type OwnerAccount = {
  account: string;
  owner_id: string;
  owner: string;
  property_id: string;
  property: string;
  bank_account: string;
  agreement: Agreement | null;
};
export type Lease = {
  id: string;
  starts_on: string;
  ends_on: string | null;
  monthly_rent: string;
  tenants: string[];
};
export type Unit = Named & { leases: Lease[] };
export type LateFees = {
  starts_on: string;
  grace_days: number;
  flat_fee: string;
  percent: string;
  maximum: string | null;
};
export type Property = Named & { units: Unit[]; late_fees: LateFees | null };
export type Tenant = Named & { property_id: string; property: string };
export type Portfolio = {
  bank_accounts: BankAccount[];
  owners: Named[];
  properties: Property[];
  accounts: OwnerAccount[];
  tenants: Tenant[];
  vendors: Named[];
};

export function kindLabel(kind: BankKind): string {
  return kind === "operating" ? "Operating" : "Security deposit";
}

const positive = (amount: string) => Number(amount) > 0;

// Management terms in a line, as fees per period: "8.5% of rent collected (at least $50.00);
// ...", saying so when they take no management fee.
export function agreementSummary(terms: Agreement): string {
  const parts: string[] = [];
  if (positive(terms.fee_percent)) {
    parts.push(
      `${terms.fee_percent}% of rent collected` +
        (positive(terms.minimum_fee) ? ` (at least ${money(terms.minimum_fee)})` : ""),
    );
  } else if (positive(terms.minimum_fee)) {
    parts.push(`a ${money(terms.minimum_fee)} minimum fee`);
  }
  if (positive(terms.flat_fee)) parts.push(`a ${money(terms.flat_fee)} flat fee`);
  if (positive(terms.leasing_fee_percent)) {
    parts.push(`${terms.leasing_fee_percent}% of a new lease's monthly rent`);
  }
  if (positive(terms.reserve)) parts.push(`keeps ${money(terms.reserve)} in reserve`);
  parts.push(
    positive(terms.approval_limit)
      ? `owner approves bills over ${money(terms.approval_limit)}`
      : "owner approves every bill",
  );
  const charged = [terms.fee_percent, terms.minimum_fee, terms.flat_fee].some(positive);
  return (charged ? "" : "No management fee; ") + parts.join("; ");
}

// Late fee terms in a line: "After 5 days' grace: $50.00 and 5% of what is unpaid, up to $100.00".
export function lateFeeSummary(terms: LateFees): string {
  const fees = [
    positive(terms.flat_fee) ? money(terms.flat_fee) : "",
    positive(terms.percent) ? `${terms.percent}% of what is unpaid` : "",
  ].filter(Boolean);
  if (fees.length === 0) return "No late fee";
  const grace = terms.grace_days === 1 ? "1 day's" : `${terms.grace_days} days'`;
  const cap = terms.maximum === null ? "" : `, up to ${money(terms.maximum)}`;
  return `After ${grace} grace: ${fees.join(" and ")}${cap}`;
}

export function leaseSummary(lease: Lease): string {
  const term = lease.ends_on ? `${lease.starts_on} to ${lease.ends_on}` : `from ${lease.starts_on}, month to month`;
  return `${lease.tenants.join(", ")}: ${term}, ${money(lease.monthly_rent)} a month`;
}
