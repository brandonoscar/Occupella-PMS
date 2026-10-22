// Interest on a lease's security deposits (migration 0122; the founder's review q041): what they
// earned since the last payment through a day (GET /api/rent/leases/{id}/deposit-interest), and
// paying it as a credit against the rent or added to the deposits (POST the same path).
import { money, usDate } from "./money";

export type PaidAs = "rent_credit" | "deposit";

export type DepositInterest = {
  annual_percent: string; // the property's yearly rate today, "0" without terms
  rate_from: string | null;
  from_on: string;
  through: string;
  deposits: { tenant: string; held: string; interest: string }[];
  due: string;
  in_effect: boolean; // may take it as a rent credit
  returned: boolean; // the deposit went back: nothing more is paid
  paid: { from_on: string; through_on: string; amount: string; paid_as: PaidAs; paid_on: string }[];
  owner_accounts: { id: string; owner: string }[];
};

export type InterestPaid = { from_on: string; through_on: string; amount: string; paid_as: PaidAs };

// A property's terms (GET /api/properties/{id}), set with POST /api/setup/deposit-interest.
export type DepositInterestTerms = { starts_on: string; annual_percent: string };

export const PAID_AS: { value: PaidAs; label: string }[] = [
  { value: "rent_credit", label: "A credit against the rent" },
  { value: "deposit", label: "Added to the deposit" },
];

export function rateLine(terms: DepositInterestTerms | null): string {
  if (terms === null) return "No deposit interest";
  if (Number(terms.annual_percent) === 0) return `None from ${usDate(terms.starts_on)}`;
  return `${terms.annual_percent}% a year (from ${usDate(terms.starts_on)})`;
}

export function paidLine(paid: InterestPaid): string {
  const how = paid.paid_as === "rent_credit" ? "credited against the rent" : "added to the deposit";
  return `${money(paid.amount)} ${how}, ${usDate(paid.from_on)} to ${usDate(paid.through_on)}`;
}
