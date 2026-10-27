// Owners' and vendors' tax IDs (ADR 0010 phase 2, part F; src/pms/api/tax_ids.py). The server
// seals the number before it keeps it: every staff member sees the last four; only an admin sees
// it whole (Reveal, or the year's 1099 export), and each time is recorded naming them.

export type TaxKind = "EIN" | "SSN";
export type TaxId = { id: string; kind: TaxKind; last4: string; recorded_at: string; recorded_by: string | null };
export type OnFile = { tax_id: TaxId | null; may_reveal: boolean };
export type Whole = { kind: TaxKind; number: string };

/** A tax ID by its last four, written as its kind is: EIN ••-•••6789, SSN •••-••-6789. */
export function taxIdLine(taxId: Pick<TaxId, "kind" | "last4">): string {
  return taxId.kind === "EIN" ? `EIN ••-•••${taxId.last4}` : `SSN •••-••-${taxId.last4}`;
}

/** The nine digits typed, dashes and spaces aside, or null when it isn't nine digits. */
export function nineDigits(typed: string): string | null {
  const digits = typed.replace(/[\s-]/g, "");
  return /^[0-9]{9}$/.test(digits) ? digits : null;
}
