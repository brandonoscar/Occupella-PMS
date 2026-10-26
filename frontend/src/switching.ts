// The switching tool as the API sends it (src/pms/api/switching.py), and how it reads.

export type RosterKind =
  | "owners"
  | "vendors"
  | "properties"
  | "units"
  | "tenants"
  | "leases"
  | "charges"
  | "bills"
  | "tenant history"
  | "owner history"
  | "vendor history";
export type TieOutRow = { line: number; item: string; detail: string | null; amount: string | null };
export type Cutover = {
  bank_account_id: string;
  name: string;
  kind: "operating" | "security_deposit";
  can_open: boolean;
  opened_on: string | null;
  signed_off: boolean;
  tie_out: TieOutRow[];
};
export type Counted = { added: number; kept: number };

// Each roster file, in the order staff bring them in, with the columns it needs.
// The contact columns the owners, vendors and tenants files may add, each optional (q127).
const CONTACT_COLUMNS = "and, if the old system has them, Email, Phone, Other phone, Address, Address line 2, City, State, ZIP";

export const ROSTER: { kind: RosterKind; label: string; columns: string }[] = [
  { kind: "owners", label: "Owners", columns: `Name, ${CONTACT_COLUMNS}` },
  { kind: "vendors", label: "Vendors", columns: `Name, ${CONTACT_COLUMNS}` },
  { kind: "properties", label: "Properties and their owners", columns: "Property, Owner (and Bank account if there are several)" },
  {
    kind: "units",
    label: "Units",
    columns: "Property, Unit, and, if the old system has them, Bedrooms, Bathrooms, Square feet, Market rent",
  },
  { kind: "tenants", label: "Tenants", columns: `Property, Tenant, ${CONTACT_COLUMNS}` },
  { kind: "leases", label: "Leases in place", columns: "Property, Unit, Tenants (separated by ;), Starts on, Ends on, Monthly rent" },
  { kind: "charges", label: "Unpaid charges", columns: "Property, Unit, Due on, Amount, Kind (rent or fee), Memo" },
  {
    kind: "bills",
    label: "Unpaid bills",
    columns:
      "Vendor, Owner, Property, Reference, Bill date, Due on, Amount, Memo, and Approved by and " +
      "Approved on for a bill the owner approved there (Bank account if there are several). A bill " +
      "the old system already took from the owner's balance but didn't pay: carry its money over " +
      "on the owner's line and the bill here",
  },
  // The years before the switch, as history: never posted, read under Before the switch (q129).
  {
    kind: "tenant history",
    label: "Tenants' history before the switch",
    columns: "Property, Tenant, Date, Type, Description, Amount (as the old system printed it), and Unit and Reference if it has them",
  },
  {
    kind: "owner history",
    label: "Owners' history before the switch",
    columns: "Owner, Property, Date, Type, Description, Amount, and Reference (Bank account if there are several)",
  },
  {
    kind: "vendor history",
    label: "Vendors' history before the switch",
    columns: "Vendor, Date, Type, Description, Amount, and Reference",
  },
];

export const BALANCE_COLUMNS =
  "Kind (owner, prepaid rent, deposit, vendor or fees), Owner, Property, Tenant, Vendor, Amount";
export const ITEM_COLUMNS = "Date, Amount (a check out is negative), Description, Reference";
export const VENDOR_PAYMENT_COLUMNS = "Vendor, Paid (what the old system paid it in the year)";

export function countedLabel(counted: Counted): string {
  const kept = counted.kept ? `; ${counted.kept} already here` : "";
  return `${counted.added} added${kept}.`;
}

// The tie-out's line staff look for first: the bank balance to expect at the cutover.
export function expectedBank(rows: TieOutRow[]): string | null {
  return rows.find((r) => r.item === "bank balance to expect at the cutover")?.amount ?? null;
}
