// Work orders, a vendor's page and renewals as the API sends them (src/pms/api/work_orders.py,
// vendors.py, renewals.py), and how they read.
import type { BadgeTone } from "./components/Badge";
import type { Ref } from "./rentals";
import type { Vendor } from "./vendors";
import { usDate } from "./money";

export type WorkOrderStatus = "open" | "assigned" | "scheduled" | "completed" | "cancelled";
export type Priority = "low" | "normal" | "high" | "urgent";
// May the vendor let themselves in: yes, call the tenant first, or no.
export type Entry = "allowed" | "call first" | "not allowed";

export type WorkOrderRow = {
  id: string;
  // Its number in the company, in the order work orders were opened.
  number: number;
  property: Ref;
  unit: Ref | null;
  summary: string;
  opened_at: string;
  status: WorkOrderStatus;
  vendor_id: string | null;
  vendor: string | null;
  billed: string;
  priority: Priority;
  due_on: string | null;
  overdue: boolean;
  scheduled_for: string | null;
  // When in that day the vendor comes ("8-10 am"), if said.
  visit_window: string | null;
  updated_at: string;
  // Days from the day it was opened to the day it closed, or today.
  days_open: number;
};
export type WorkOrderStep = {
  step: "assigned" | "scheduled" | "completed" | "cancelled";
  vendor: string | null;
  taken_at: string;
  scheduled_for: string | null;
  visit_window: string | null;
  note: string | null;
  // Recorded by the vendor through their link, not by staff.
  by_vendor: boolean;
};
// The tenant's request a work order answers, with the photos they filed with it.
export type WorkOrderRequest = {
  id: string;
  tenant: string;
  summary: string;
  submitted_at: string;
  emergency: boolean;
  entry_allowed: boolean;
  photos: { id: string; title: string }[];
  // The staff members on call who were emailed of it, an emergency.
  emailed: string[];
};
// What a tenant was charged for a work order (a repair they caused), and what is open of it.
export type TenantCharge = {
  id: string;
  lease_id: string;
  unit: string;
  tenants: string;
  due_on: string;
  amount: string;
  memo: string | null;
  open: string;
};
// A lease in effect today that a work order may be charged to.
export type LeaseChoice = { id: string; unit: string; tenants: string };
export type WorkOrder = WorkOrderRow & {
  description: string | null;
  entry: Entry | null;
  steps: WorkOrderStep[];
  bills: { id: string; vendor: string; reference: string; amount: string; paid: boolean }[];
  request: WorkOrderRequest | null;
  tenants: { name: string; contact: { email: string | null; phone: string | null } }[];
  charges: TenantCharge[];
  leases: LeaseChoice[];
  // The recurring work order that opened it, in words ("every 3 months"), if one did.
  recurring: string | null;
};

// What a vendor sees through their private link (src/pms/api/vendor_links.py).
export type VendorWorkOrder = {
  id: string;
  number: number;
  property: string;
  unit: string | null;
  summary: string;
  description: string | null;
  priority: Priority;
  due_on: string | null;
  // In the words their email uses ("Call ... first to arrange entry.").
  entry: string | null;
  status: "assigned" | "scheduled";
  visit_on: string | null;
  visit_window: string | null;
};
export type VendorView = { vendor: string; company: string; work_orders: VendorWorkOrder[] };

// A job that comes back every few months (src/pms/api/recurring_work_orders.py).
export type RecurringWorkOrder = {
  id: string;
  property_id: string;
  property: string;
  unit_id: string | null;
  unit: string | null;
  summary: string;
  every_months: number;
  // "every 3 months on the 15th"
  schedule: string;
  day_of_month: number;
  starts_on: string;
  priority: Priority;
  vendor: string | null;
  ends_on: string | null;
  last_opened_on: string | null;
};

// "Change the furnace filter, Unit 1, Property 1: every 3 months on the 15th from 01/20/2026"
export function recurringLine(r: RecurringWorkOrder): string {
  const where = r.unit ? `${r.unit}, ${r.property}` : r.property;
  return `${r.summary}, ${where}: ${r.schedule} from ${usDate(r.starts_on)}`;
}

export type VendorPageData = Vendor & {
  insurance_standing: string;
  bills: { id: string; reference: string; owner: string; property: string; bill_date: string; amount: string; paid: boolean }[];
  work_orders: {
    id: string; number: number; property: string; unit: string | null; summary: string; status: WorkOrderStatus; opened_at: string;
  }[];
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
  open: { word: "New", tone: "caution" },
  assigned: { word: "Assigned", tone: "info" },
  scheduled: { word: "Scheduled", tone: "info" },
  completed: { word: "Completed", tone: "positive" },
  cancelled: { word: "Cancelled", tone: "neutral" },
};

export const PRIORITY: Record<Priority, { word: string; tone: BadgeTone }> = {
  low: { word: "Low", tone: "neutral" },
  normal: { word: "Normal", tone: "neutral" },
  high: { word: "High", tone: "caution" },
  urgent: { word: "Urgent", tone: "danger" },
};

export const ENTRY: Record<Entry, string> = {
  allowed: "The vendor may let themselves in",
  "call first": "Call the tenant before going in",
  "not allowed": "Only with the tenant home",
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

// "1 day", "12 days".
export function daysLabel(days: number): string {
  return days === 1 ? "1 day" : `${days} days`;
}

// "in 20 days", "today", "tomorrow".
export function daysLeftLabel(days: number): string {
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  return `in ${days} days`;
}
