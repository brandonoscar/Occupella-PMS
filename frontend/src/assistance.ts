import { money, sumMoney, usDate } from "./money";

// A housing authority's part of a lease's rent (migration 0095, the founder's review q034;
// src/pms/api/assistance.py): the authority pays its part of each month's rent, the tenant the
// rest. Money stays text from the API to the screen.

export type Authority = { id: string; name: string; leases: number; owed: string };

export type AssistanceTerms = {
  id: string;
  authority_id: string;
  authority: string;
  starts_on: string;
  // What the authority pays of each month's rent; "0.00" ended the assistance.
  amount: string;
  contract: string | null;
  recorded_by: string | null;
};

export type AuthorityCharge = {
  id: string;
  lease_id: string;
  authority_id: string;
  due_on: string;
  memo: string;
  source: "rent" | "staff";
  charged: string;
  // What a lease's end or staff's credits added to it or took off it (signed).
  adjusted: string;
  paid: string;
  // Below 0 when the authority paid more than it now owes.
  open: string;
};

export type LeaseAssistance = {
  in_force: AssistanceTerms | null;
  monthly_rent: string;
  tenant_part: string;
  terms: AssistanceTerms[];
  charges: AuthorityCharge[];
  owed: string;
};

export type AssistedLease = {
  lease_id: string;
  home: string;
  monthly: string | null;
  contract: string | null;
  owed: string;
  open_charges: AuthorityCharge[];
};

export type AuthorityPage = { id: string; name: string; leases: AssistedLease[]; owed: string };

// "From 11/01/2026: Synthetic Housing Authority pays $800.00 a month (contract HAP-0001)".
export function termsLine(terms: AssistanceTerms): string {
  const from = `From ${usDate(terms.starts_on)}: `;
  if (Number(terms.amount) === 0) return `${from}no assistance (${terms.authority})`;
  const contract = terms.contract ? ` (contract ${terms.contract})` : "";
  return `${from}${terms.authority} pays ${money(terms.amount)} a month${contract}`;
}

// How today's rent splits: "Synthetic Housing Authority pays $800.00 of the $1,000.00 rent; the
// tenant pays $200.00".
export function splitLine(assistance: LeaseAssistance): string {
  const terms = assistance.in_force;
  if (terms === null) return `The tenant pays all of the ${money(assistance.monthly_rent)} rent`;
  const paid = sumMoney([assistance.monthly_rent, `-${assistance.tenant_part}`]);
  return `${terms.authority} pays ${money(paid)} of the ${money(assistance.monthly_rent)} rent; the tenant pays ${money(assistance.tenant_part)}`;
}

// The first of next month (UTC), as YYYY-MM-DD: the first month assistance can usually start.
export function nextFirst(today: Date): string {
  const first = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() + 1, 1));
  return first.toISOString().slice(0, 10);
}

// A payment's lines: each lease given an amount above nothing.
export function paymentLines(amounts: Record<string, string>): { lease_id: string; amount: string }[] {
  return Object.entries(amounts)
    .map(([lease_id, amount]) => ({ lease_id, amount: amount.trim() }))
    .filter((line) => line.amount !== "" && Number(line.amount) > 0);
}

export function paymentTotal(amounts: Record<string, string>): string {
  return sumMoney(paymentLines(amounts).map((line) => line.amount));
}
