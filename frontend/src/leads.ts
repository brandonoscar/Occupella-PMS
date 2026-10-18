// Leads and showings as the API sends them (src/pms/api/leads.py, migration 0088), and how they read.
import type { BadgeTone } from "./components/Badge";

export type LeadStatus = "open" | "applied" | "lost";
export type ShowingOutcome = "shown" | "no_show" | "cancelled";

export type LeadRow = {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  property: string | null;
  unit: string | null;
  source: string | null;
  move_in_on: string | null;
  added_at: string;
  showings: number;
  next_showing: string | null;
  status: LeadStatus;
};

export type Showing = {
  id: string;
  unit_id: string;
  property: string;
  unit: string;
  starts_at: string;
  shown_by: string | null;
  outcome: ShowingOutcome | null;
  note: string | null;
};

export type Lead = LeadRow & {
  property_id: string | null;
  unit_id: string | null;
  note: string | null;
  added_by: string | null;
  showing_list: Showing[];
  closing: { outcome: "applied" | "lost"; reason: string | null; closed_at: string; closed_by: string | null } | null;
};

export const LEAD_STATUS: Record<LeadStatus, { word: string; tone: BadgeTone }> = {
  open: { word: "Open", tone: "info" },
  applied: { word: "Applied", tone: "positive" },
  lost: { word: "Lost", tone: "neutral" },
};

export const OUTCOME: Record<ShowingOutcome, { word: string; tone: BadgeTone }> = {
  shown: { word: "Shown", tone: "positive" },
  no_show: { word: "No-show", tone: "caution" },
  cancelled: { word: "Cancelled", tone: "neutral" },
};

// What a lead asked about: "Unit 1, Cedar House", "Cedar House", or nothing.
export function askedAbout(lead: { property: string | null; unit: string | null }): string {
  if (lead.property === null) return "";
  return lead.unit ? `${lead.unit}, ${lead.property}` : lead.property;
}

// A showing's time as the viewer's clock reads it: "10/20/2026 3:00 PM".
export function showingTime(iso: string, timeZone?: string): string {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return iso;
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "numeric", minute: "2-digit", hour12: true, timeZone,
  }).formatToParts(moment);
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${part("month")}/${part("day")}/${part("year")} ${part("hour")}:${part("minute")} ${part("dayPeriod")}`;
}

// A day and a time typed on the viewer's clock, as the moment the API keeps.
export function startsAt(day: string, time: string): string {
  return new Date(`${day}T${time}`).toISOString();
}
