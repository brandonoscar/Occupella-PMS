// The company's vendors as the API sends them (src/pms/api/vendors.py): W-9 on file and each
// coverage as trust_report_vendor_insurance has it on the day.
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
export type Vendor = {
  id: string;
  name: string;
  w9_on_file: boolean;
  insurance: Policy[];
  contact: Contact;
};

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
  return "caution";
}

export function vendorInsuranceHref(on: string, format: "pdf" | "xlsx" = "pdf"): string {
  return `/api/reports/trust_report_vendor_insurance?${new URLSearchParams({ arg: on, format })}`;
}
