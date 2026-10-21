// The company itself as the API sends it (src/pms/api/company.py): its name, mailing address,
// the fee terms a new property starts with, and its logo.
export type Address = {
  address_line_1: string;
  address_line_2: string | null;
  city: string;
  state: string;
  postal_code: string;
};
export type DefaultLateFees = { grace_days: number; flat_fee: string; percent: string; maximum: string | null };
export type Logo = { id: string; content_type: "image/png" | "image/jpeg"; uploaded_at: string };
export type Company = {
  name: string;
  address: Address | null;
  late_fees: DefaultLateFees | null;
  nsf_fee: string | null;
  // Where replies to the company's emails go; none, to the server's From address.
  reply_to: string | null;
  logo: Logo | null;
};
// What an owner or a tenant sees of their property manager.
export type ManagerCard = { name: string; address: Address | null; logo: boolean };

// An address as it is written on an envelope: its street lines, then "City, ST 00000".
export function addressLines(address: Address): string[] {
  return [
    address.address_line_1,
    ...(address.address_line_2 ? [address.address_line_2] : []),
    `${address.city}, ${address.state} ${address.postal_code}`,
  ];
}

// The logo's address; its id in the query makes a new logo load in place of the old one.
export function logoHref(logo: Logo): string {
  return `/api/company/logo?v=${logo.id}`;
}
