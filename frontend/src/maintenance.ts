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
  // No heat, a leak, no water...: its work order opens urgent and whoever is on call is emailed.
  emergency: boolean;
  submitted_at: string;
  status: Status;
  status_at: string;
  // The day the vendor comes, and when in it, as last scheduled.
  visit_on: string | null;
  visit_window: string | null;
  // How many photos came with it.
  photos: number;
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

// "Visit on 11/03/2026, 8-10 am" while a visit is scheduled, else null.
export function visitLabel(request: Pick<MaintenanceRequest, "status" | "visit_on" | "visit_window">): string | null {
  if (request.status !== "scheduled" || request.visit_on === null) return null;
  return `Visit on ${usDate(request.visit_on)}${request.visit_window ? `, ${request.visit_window}` : ""}`;
}

// "1 photo", "2 photos", or null with none.
export function photosLabel(count: number): string | null {
  if (count === 0) return null;
  return count === 1 ? "1 photo" : `${count} photos`;
}

// A tenant files at most this many photos with a request, each a JPEG, PNG or PDF of at most 5 MB.
export const PHOTOS_PER_REQUEST = 3;

// Why the photos chosen can't be sent, or "" when they can.
export function photosProblem(photos: { type: string | null; size: number }[], maxBytes: number): string {
  if (photos.length > PHOTOS_PER_REQUEST) return `At most ${PHOTOS_PER_REQUEST} photos a request.`;
  if (photos.some((p) => p.type === null)) return "A photo is a JPEG, a PNG or a PDF.";
  if (photos.some((p) => p.size > maxBytes)) return "A photo is at most 5 MB.";
  return "";
}

// Who is on call for emergencies a tenant files (src/pms/api/maintenance.py).
export type OnCall = { staff_id: string; name: string; on_call: boolean; emailed: boolean };

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
