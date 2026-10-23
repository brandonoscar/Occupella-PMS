// Text messages as the API sends them (src/pms/api/texts.py, migration 0161): a person's word on
// being texted at a US mobile number, and the texts the PMS wrote them.
import type { BadgeTone } from "./components/Badge";
import { usDate } from "./money";

export type TextAbout = "tenant" | "owner" | "vendor";
export type TextHow = "portal" | "in writing" | "verbally" | "stop reply" | "start reply";

export type Consent = { phone: string; consented: boolean; how: TextHow; at: string; by: string | null };

export type TextLine = {
  id: string;
  kind: "message" | "rent_reminder" | "late_notice";
  to: string;
  body: string;
  at: string;
  by: string | null;
  status: "sent" | "waiting" | "failed";
  error: string | null;
};

export type PersonTexts = { sending: boolean; consent: Consent | null; phone_on_file: string | null; texts: TextLine[] };
export type MyTexts = Omit<PersonTexts, "texts">;
export type TextSettings = { rent_reminder_days: number | null; late_notices: boolean; sending: boolean };

// +15555550123 as (555) 555-0123.
export function shownPhone(phone: string): string {
  return `(${phone.slice(2, 5)}) ${phone.slice(5, 8)}-${phone.slice(8)}`;
}

const HOW: Record<TextHow, string> = {
  portal: "in the portal",
  "in writing": "in writing",
  verbally: "by word of mouth",
  "stop reply": "by replying STOP",
  "start reply": "by replying START",
};

// "Agreed to texts at (555) 555-0123 in writing, 10/23/2026 (recorded by Staff 1)".
export function consentLine(consent: Consent | null): string {
  if (consent === null) return "No word on texts yet";
  const said = consent.consented ? "Agreed to texts at" : "Said no to texts at";
  const by = consent.by ? ` (recorded by ${consent.by})` : "";
  return `${said} ${shownPhone(consent.phone)} ${HOW[consent.how]}, ${usDate(consent.at)}${by}`;
}

export const TEXT_KIND: Record<TextLine["kind"], string> = {
  message: "Text",
  rent_reminder: "Rent reminder",
  late_notice: "Late fee notice",
};

export const TEXT_STATUS: Record<TextLine["status"], { word: string; tone: BadgeTone }> = {
  sent: { word: "Sent", tone: "positive" },
  waiting: { word: "Waiting to send", tone: "info" },
  failed: { word: "Not sent", tone: "danger" },
};
