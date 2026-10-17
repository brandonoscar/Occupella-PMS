// Maintenance requests as the API sends them, and how their status reads. A request's status is
// its work order's latest step (src/pms/api/maintenance.py).
import { usDate } from "./money";

export type Status = "received" | "opened" | "assigned" | "scheduled" | "completed" | "cancelled";

export type MaintenanceRequest = {
  id: string;
  lease_id: string;
  property: string;
  unit: string;
  filed_by: string;
  summary: string;
  entry_allowed: boolean;
  submitted_at: string;
  status: Status;
  status_at: string;
};

export type StaffRequest = MaintenanceRequest & {
  work_order_id: string | null;
  vendor: string | null;
};

const LABELS: Record<Status, string> = {
  received: "Received",
  opened: "Work order opened",
  assigned: "Someone is assigned",
  scheduled: "A visit is scheduled",
  completed: "Done",
  cancelled: "Closed without work",
};

export function statusLabel(status: Status): string {
  return LABELS[status];
}

export function isOpen(status: Status): boolean {
  return status !== "completed" && status !== "cancelled";
}

// A time from the API (ISO 8601, UTC) as a day and minute in UTC, the same on every screen.
export function when(iso: string): string {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return iso;
  const at = moment.toISOString();
  return `${usDate(at)} ${at.slice(11, 16)} UTC`;
}
