import type { BadgeTone } from "./components/Badge";
import { money, usDate } from "./money";

// Tax credit (LIHTC) compliance (migration 0123, the founder's review q143;
// src/pms/api/tax_credit.py): a property's area income limits at 50% of median from a day, a
// unit's designation from a day, and where a tax credit unit's lease stands in the compliance
// report's words. Money stays text from the API to the screen.

// The 50% of median limits for households of one to eight, as the area's table gives them.
export type TaxCreditLimits = { starts_on: string; limits: string[] };

export type Designation = {
  starts_on: string;
  // null: not a tax credit unit from that day (bedrooms and the allowance are null too).
  income_percent: number | null;
  bedrooms: number | null;
  utility_allowance: string | null;
};

export type UnitTaxCredit = {
  in_force: Designation | null;
  // The most the rent and the utility allowance may come to today, in whole dollars.
  max_gross_rent: string | null;
  designations: Designation[];
};

export type LeaseTaxCredit = {
  income_percent: number;
  bedrooms: number;
  certification: string;
  income_test: string;
  max_gross_rent: string | null;
  gross_rent: string | null;
  rent_test: string;
};

export const INCOME_PERCENTS = [20, 30, 40, 50, 60, 70, 80] as const;

// "1 person", "2 people"... for the table's eight household sizes.
export const HOUSEHOLDS = Array.from({ length: 8 }, (_, n) => (n === 0 ? "1 person" : `${n + 1} people`));

export function bedroomsLabel(bedrooms: number): string {
  if (bedrooms === 0) return "no bedroom";
  return bedrooms === 1 ? "1 bedroom" : `${bedrooms} bedrooms`;
}

// "From 01/01/2026: 60% of median, 1 bedroom, utility allowance $50.00", or the day it ended.
export function designationLine(d: Designation): string {
  const from = `From ${usDate(d.starts_on)}: `;
  if (d.income_percent === null || d.bedrooms === null) return `${from}not a tax credit unit`;
  return `${from}${d.income_percent}% of median, ${bedroomsLabel(d.bedrooms)}, utility allowance ${money(d.utility_allowance)}`;
}

// The income test's badge: qualified is fine; over the limit at move-in is a household that
// never qualified; anything else (over 140%, nothing on file) asks for staff's attention.
export function incomeTone(test: string): BadgeTone {
  if (test === "qualified") return "positive";
  if (test === "over the limit at move-in") return "danger";
  if (test === "vacant") return "neutral";
  return "caution";
}

export function rentTone(test: string): BadgeTone {
  if (test === "within the limit") return "positive";
  if (test.startsWith("over by")) return "danger";
  return "caution";
}

// "$950.00, the rent and the utility allowance; at most $964.00", or what is missing.
export function grossLine(lease: LeaseTaxCredit): string {
  const gross = `${money(lease.gross_rent)}, the rent and the utility allowance`;
  return lease.max_gross_rent === null ? gross : `${gross}; at most ${money(lease.max_gross_rent)}`;
}
