// Lease offers and renewals as the API sends them (src/pms/api/offers.py), and how they read.

export type Signer = { position: number; name: string; email: string; signed_at: string | null };

export type Offer = {
  id: string;
  kind: "new lease" | "renewal";
  application_id: string | null;
  renews_lease_id: string | null;
  property: string;
  unit: string;
  starts_on: string;
  ends_on: string | null;
  monthly_rent: string;
  document_title: string;
  offered_at: string;
  sent: boolean;
  signers: Signer[];
  lease_id: string | null;
};

export type SigningLink = { position: number; name: string; email: string; url: string };

export type Offered = { offer: Offer; links: SigningLink[] };

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
  return `${startsOn} to ${endsOn ?? "month to month"}`;
}

// Where an offer stands, for staff.
export function offerStatus(offer: Offer): string {
  if (offer.lease_id) return "It is the lease now.";
  if (!offer.sent) return "Not sent for signing.";
  const signed = offer.signers.filter((s) => s.signed_at).length;
  if (signed === offer.signers.length) return "Signed by everyone: it can be made the lease.";
  return `Signed by ${signed} of ${offer.signers.length}.`;
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
