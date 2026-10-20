import { money } from "./money";

// Utility billing, RUBS and submeters (migration 0096, the founder's review q021;
// src/pms/api/utilities.py): a property's utility bill shared among its leases. Money and
// weights stay text from the API to the screen.

export type Utility = "water" | "sewer" | "trash" | "gas" | "electric" | "other";
export type Method = "units" | "square_feet" | "occupants" | "submeter";

export const UTILITIES: { value: Utility; label: string }[] = [
  { value: "water", label: "Water" },
  { value: "sewer", label: "Sewer" },
  { value: "trash", label: "Trash" },
  { value: "gas", label: "Gas" },
  { value: "electric", label: "Electric" },
  { value: "other", label: "Other" },
];

export const METHODS: { value: Method | "none"; label: string }[] = [
  { value: "units", label: "By unit" },
  { value: "square_feet", label: "By square feet" },
  { value: "occupants", label: "By occupants" },
  { value: "submeter", label: "By submeter" },
  { value: "none", label: "Not billed back" },
];

export function utilityLabel(utility: Utility): string {
  return UTILITIES.find((u) => u.value === utility)?.label ?? utility;
}

export function methodLabel(method: Method | "none"): string {
  return METHODS.find((m) => m.value === method)?.label ?? method;
}

export type Plan = {
  id: string;
  utility: Utility;
  starts_on: string;
  method: Method | "none";
  owner_percent: string;
  recorded_by: string | null;
};
export type UnitArea = { unit_id: string; unit: string; square_feet: number | null };
export type BilledLease = {
  lease_id: string;
  home: string;
  starts_on: string;
  ends_on: string | null;
  billed: Utility[];
};
export type Share = {
  lease_id: string;
  home: string;
  billed: boolean;
  weight: string;
  usage: string | null;
  amount: string;
};
export type Preview = {
  method: Method;
  owner_percent: string;
  charged: string;
  owner_keeps: string;
  shares: Share[];
};
export type UtilityBill = Preview & {
  id: string;
  utility: Utility;
  period_start: string;
  period_end: string;
  amount: string;
  master_usage: string | null;
  due_on: string;
  memo: string | null;
};
export type PropertyUtilities = {
  property: string;
  plans: Plan[];
  units: UnitArea[];
  leases: BilledLease[];
  bills: UtilityBill[];
};

// What a lease's share was worked out from, in words: "30 days", "18,000 sq ft-days",
// "60 person-days", "45.5 on its meter".
export function weightLabel(method: Method, share: Share): string {
  if (method === "submeter") return `${share.usage ?? "0"} on its meter`;
  const amount = Number(share.weight).toLocaleString("en-US");
  const unit = { units: "days", square_feet: "sq ft-days", occupants: "person-days" }[method];
  return `${amount} ${unit}`;
}

// A share as the bill's table shows it: what the lease is charged, or why nothing.
export function shareAmount(share: Share): string {
  return share.billed ? money(share.amount) : "Not billed";
}

// The plans in force today, one per utility: the latest from today or before.
export function plansInForce(plans: Plan[], today: string): Plan[] {
  const latest = new Map<Utility, Plan>();
  for (const plan of plans) {
    if (plan.starts_on > today) continue;
    const held = latest.get(plan.utility);
    if (!held || plan.starts_on > held.starts_on) latest.set(plan.utility, plan);
  }
  return UTILITIES.flatMap((u) => {
    const plan = latest.get(u.value);
    return plan && plan.method !== "none" ? [plan] : [];
  });
}
