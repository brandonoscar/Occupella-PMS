// The company's vendors as the API sends them (src/pms/api/vendors.py): W-9 on file, each
// coverage as trust_report_vendor_insurance has it on the day, and their profile (trade, 1099,
// how they are paid; no tax ID).
import type { BadgeTone } from "./components/Badge";
import type { Contact } from "./directory";

export type Policy = {
  coverage: string;
  carrier: string;
  policy_number: string;
  starts_on: string;
  expires_on: string;
  limit_amount: string | null;
  standing: string; // current, expires today, expires in N days, expired
};
export type PaymentMethod = "check" | "EFT";
export type Profile = { trade: string | null; gets_1099: boolean; payment_method: PaymentMethod | null };
export type Vendor = {
  id: string;
  name: string;
  w9_on_file: boolean;
  insurance: Policy[];
  contact: Contact;
  profile: Profile | null;
};
// A row of the Vendors list; insurance is how it stands by the coverage that runs out first.
export type VendorRow = {
  id: string;
  name: string;
  trade: string | null;
  phone: string | null;
  email: string | null;
  w9_on_file: boolean;
  insurance: string;
};

export const PAYMENT: Record<PaymentMethod, string> = { check: "Check", EFT: "EFT" };

export const COVERAGES = [
  "general liability",
  "workers compensation",
  "commercial auto",
  "professional liability",
  "other",
] as const;

export function standingTone(standing: string): BadgeTone {
  if (standing === "current") return "positive";
  if (standing === "expired") return "danger";
  if (standing === "no insurance on file") return "neutral";
  return "caution";
}

// "Current", "Expires in 12 days", "Expired", "None on file": a standing as a list reads it.
export function standingWord(standing: string): string {
  if (standing === "no insurance on file") return "None on file";
  return standing.charAt(0).toUpperCase() + standing.slice(1);
}
