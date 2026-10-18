// Lease offers and renewals as the API sends them (src/pms/api/offers.py), and how they read.
import type { BadgeTone } from "./components/Badge";
import { usDate } from "./money";

// A signer is a tenant to be, or the lease's guarantor, who signs without becoming a tenant.
export type SignerRole = "tenant" | "guarantor";
export type Signer = { position: number; name: string; email: string; role: SignerRole; signed_at: string | null };

// Where an offer stands: a draft, sent and signed by none, some or all of its signers, made the
// lease, or voided.
export type OfferStatus = "draft" | "sent" | "partially signed" | "signed" | "leased" | "void";

// A fee the lease will owe every month besides rent, or a credit.
export type MonthlyCharge = { kind: "fee" | "credit"; memo: string; amount: string };

export type Offer = {
  id: string;
  kind: "new lease" | "renewal";
  application_id: string | null;
  renews_lease_id: string | null;
  property_id: string;
  property: string;
  unit_id: string;
  unit: string;
  starts_on: string;
  ends_on: string | null;
  monthly_rent: string;
  security_deposit: string | null;
  recurring_charges: MonthlyCharge[];
  document_title: string;
  offered_at: string;
  sent: boolean;
  signers: Signer[];
  lease_id: string | null;
  status: OfferStatus;
  void: { reason: string | null; recorded_at: string; recorded_by: string | null } | null;
};

export type SigningLink = { position: number; name: string; email: string; url: string };

// Whom the PMS emailed their link (migration 0090), while the server sends email.
export type Offered = { offer: Offer; links: SigningLink[]; emailed?: string[] };

// What Send says: whom it went to, and whom the PMS emailed their own link.
export function sentLine(sent: Offered): string {
  const count = sent.offer.signers.length;
  const emailed = sent.emailed ?? [];
  return `Sent to ${count} signer${count === 1 ? "" : "s"}.` +
    (emailed.length > 0 ? ` Each was emailed their link: ${emailed.join(", ")}.` : "");
}

export type RenewableLease = {
  lease_id: string;
  property: string;
  unit: string;
  starts_on: string;
  ends_on: string | null;
  monthly_rent: string;
  tenants: { name: string; email: string | null }[];
};

export type Person = { name: string; email: string };

// The database's limit on a lease document.
export const MAX_DOCUMENT = 5_000_000;

export function term(startsOn: string, endsOn: string | null): string {
  return `${usDate(startsOn)} – ${endsOn ? usDate(endsOn) : "Month to month"}`;
}

// Where an offer stands, for staff, in a badge.
export function offerBadge(status: OfferStatus): { word: string; tone: BadgeTone } {
  return {
    draft: { word: "Draft", tone: "neutral" as const },
    sent: { word: "Sent", tone: "info" as const },
    "partially signed": { word: "Partially signed", tone: "caution" as const },
    signed: { word: "Signed", tone: "positive" as const },
    leased: { word: "Lease active", tone: "positive" as const },
    void: { word: "Void", tone: "danger" as const },
  }[status];
}

// How many of its signers have signed: "1 of 2 signed".
export function signedCount(offer: Offer): string {
  return `${offer.signers.filter((s) => s.signed_at).length} of ${offer.signers.length} signed`;
}

// Signers typed one a line as "Name, email"; the email is what follows the last comma.
export function parseSigners(text: string): { signers: Person[] } | { error: string } {
  const signers: Person[] = [];
  for (const [n, line] of text.split("\n").entries()) {
    if (!line.trim()) continue;
    const comma = line.lastIndexOf(",");
    const name = line.slice(0, Math.max(comma, 0)).trim();
    const email = line.slice(comma + 1).trim();
    if (comma < 0 || !name || !email) {
      return { error: `line ${n + 1}: write each signer as "Name, email"` };
    }
    signers.push({ name, email });
  }
  return signers.length ? { signers } : { error: "name who signs" };
}

// People as the signers box shows them; someone without an email gets an empty one to fill in.
export function signersText(people: { name: string; email: string | null }[]): string {
  return people.map((p) => `${p.name}, ${p.email ?? ""}`).join("\n");
}

// The day after a date ("2027-06-30" -> "2027-07-01"), in UTC like the server's dates.
export function dayAfter(isoDay: string): string {
  const next = new Date(`${isoDay}T00:00:00Z`);
  next.setUTCDate(next.getUTCDate() + 1);
  return next.toISOString().slice(0, 10);
}

const PDF_START = [...new TextEncoder().encode("%PDF-")];

export function isPdf(bytes: Uint8Array): boolean {
  return PDF_START.every((byte, n) => bytes[n] === byte);
}

// Bytes as base64, in chunks so a large document doesn't overflow the call stack.
export function base64Of(bytes: Uint8Array): string {
  let text = "";
  for (let at = 0; at < bytes.length; at += 0x8000) {
    text += String.fromCharCode(...bytes.subarray(at, at + 0x8000));
  }
  return btoa(text);
}
