// Work orders, a vendor's page and renewals as the API sends them (src/pms/api/work_orders.py,
// vendors.py, renewals.py), and how they read.
import type { BadgeTone } from "./components/Badge";
import type { Ref } from "./rentals";
import type { Vendor } from "./vendors";

export type WorkOrderStatus = "open" | "assigned" | "completed" | "cancelled";

export type WorkOrderRow = {
  id: string;
  property: Ref;
  unit: Ref | null;
  summary: string;
  opened_at: string;
  status: WorkOrderStatus;
  vendor: string | null;
  billed: string;
};
export type WorkOrder = WorkOrderRow & {
  steps: { step: "assigned" | "completed" | "cancelled"; vendor: string | null; taken_at: string; note: string | null }[];
  bills: { id: string; vendor: string; reference: string; amount: string; paid: boolean }[];
  request: { id: string; tenant: string; summary: string; submitted_at: string } | null;
};

export type VendorPageData = Vendor & {
  bills: { id: string; reference: string; owner: string; property: string; bill_date: string; amount: string; paid: boolean }[];
  work_orders: { id: string; property: string; summary: string; status: WorkOrderStatus }[];
};

export type Renewal = "none" | "drafted" | "sent" | "signed" | "renewed";
export type Expiring = {
  lease_id: string | null;
  property: string;
  unit: string;
  tenants: string;
  ends_on: string;
  days_left: number;
  bucket: "0-30" | "31-60" | "61-90";
  monthly_rent: string;
  renewal: Renewal;
};

export const STATUS: Record<WorkOrderStatus, { word: string; tone: BadgeTone }> = {
  open: { word: "Open", tone: "caution" },
  assigned: { word: "Assigned", tone: "info" },
  completed: { word: "Completed", tone: "positive" },
  cancelled: { word: "Cancelled", tone: "neutral" },
};

export const RENEWAL: Record<Renewal, { word: string; tone: BadgeTone }> = {
  none: { word: "No renewal", tone: "caution" },
  drafted: { word: "Drafted", tone: "info" },
  sent: { word: "Sent", tone: "info" },
  signed: { word: "Signed", tone: "positive" },
  renewed: { word: "Renewed", tone: "positive" },
};

// "Unit 1, Property 1", or the property alone for a work order on the whole property.
export function whereLabel(row: Pick<WorkOrderRow, "property" | "unit">): string {
  return row.unit ? `${row.unit.name}, ${row.property.name}` : row.property.name;
}

// "in 20 days", "today", "tomorrow".
export function daysLeftLabel(days: number): string {
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  return `in ${days} days`;
}
