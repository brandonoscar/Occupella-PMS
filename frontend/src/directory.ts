// Tenants, owners and contact details as the API sends them (src/pms/api/directory.py), and how
// they read.
import type { OwnerMoney } from "./payables";
import type { LeaseLine, Portal, Ref } from "./rentals";

export type Contact = {
  email: string | null;
  phone: string | null;
  alternate_phone: string | null;
  address_line_1: string | null;
  address_line_2: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
};

export const CONTACT_FIELDS = [
  "email",
  "phone",
  "alternate_phone",
  "address_line_1",
  "address_line_2",
  "city",
  "state",
  "postal_code",
] as const;

export type TenantRow = {
  id: string;
  name: string;
  property: Ref;
  lease: { id: string; unit: Ref; status: LeaseLine["status"] } | null;
  email: string | null;
  phone: string | null;
  archived: boolean;
  // What the lease shown owes today, and whether they can sign in to the portal.
  owed: string;
  portal: Portal;
};
export type Tenant = {
  id: string;
  name: string;
  archived: boolean;
  property: Ref;
  contact: Contact;
  prepaid: string;
  deposit: string;
  leases: (LeaseLine & { unit: Ref; property: Ref })[];
  portal: Portal;
  login_email: string | null;
};

export type OwnerRow = {
  id: string;
  name: string;
  properties: string[];
  // What their property accounts hold, keep back and may pay out today, together.
  held: string;
  reserve: string;
  available: string;
  email: string | null;
  phone: string | null;
  archived: boolean;
};
// A management agreement's terms, in force today on one of the owner's property accounts.
export type Agreement = {
  account: string;
  starts_on: string;
  fee_percent: string;
  minimum_fee: string;
  flat_fee: string;
  leasing_fee_percent: string;
  reserve: string;
  approval_limit: string;
};
export type Owner = {
  id: string;
  name: string;
  archived: boolean;
  contact: Contact;
  accounts: OwnerMoney[];
  agreements: Agreement[];
  // Whether they can sign in to the portal, and their login's email.
  portal: Portal;
  login_email: string | null;
};

// The mailing address on its lines: "100 Example Street, Suite 1" then "Sampletown, ZZ 00000";
// none recorded, no lines.
export function addressLines(contact: Contact): string[] {
  if (!contact.address_line_1) return [];
  const street = [contact.address_line_1, contact.address_line_2].filter(Boolean).join(", ");
  return [street, `${contact.city}, ${contact.state} ${contact.postal_code}`];
}

// What a form's fields send: each one trimmed, an empty one as nothing.
export function contactBody(draft: Record<(typeof CONTACT_FIELDS)[number], string>): Contact {
  const body = {} as Contact;
  for (const field of CONTACT_FIELDS) {
    const value = draft[field].trim();
    body[field] = value === "" ? null : value;
  }
  return body;
}

export function hasContact(contact: Contact): boolean {
  return CONTACT_FIELDS.some((field) => contact[field] !== null);
}
