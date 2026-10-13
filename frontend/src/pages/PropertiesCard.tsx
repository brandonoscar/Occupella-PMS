import { useState } from "react";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import type { Portfolio, Property } from "../setup";
import { lateFeeSummary, leaseSummary, nsfFeeSummary } from "../setup";
import type { CardProps } from "./useAdd";
import { useAdd } from "./useAdd";

function operatingAccounts(books: Portfolio) {
  return books.bank_accounts.filter((bank) => bank.kind === "operating");
}

// A choice of operating trust bank account, made for staff when there is only one.
function useBank(books: Portfolio): [string, (id: string) => void] {
  const [bank, setBank] = useState("");
  const operating = operatingAccounts(books);
  return [bank || (operating.length === 1 ? (operating[0]?.id ?? "") : ""), setBank];
}

function BankSelect({ books, id, value, onChange }: {
  books: Portfolio;
  id: string;
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <Select label="Held in" id={id} required value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Choose an operating account</option>
      {operatingAccounts(books).map((bank) => (
        <option key={bank.id} value={bank.id}>
          {bank.name}
        </option>
      ))}
    </Select>
  );
}

function OwnerSelect({ books, id, value, onChange }: {
  books: Portfolio;
  id: string;
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <Select label="Owner" id={id} required value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Choose an owner</option>
      {books.owners.map((owner) => (
        <option key={owner.id} value={owner.id}>
          {owner.name}
        </option>
      ))}
    </Select>
  );
}

// Properties: each with its owners, units (and their leases), late fee and NSF fee terms, and
// the forms to add a unit, another owner or new late fee or NSF fee terms.
export function PropertiesCard({ books, refresh }: CardProps) {
  const { error, add } = useAdd(refresh);
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [bank, setBank] = useBank(books);
  return (
    <Card title="Properties">
      <ul className="mb-6 flex flex-col divide-y divide-line">
        {books.properties.map((property) => (
          <PropertyRow key={property.id} property={property} books={books} refresh={refresh} />
        ))}
      </ul>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          if (await add("properties", { name, owner_id: owner, bank_account_id: bank })) {
            setName("");
          }
        }}
      >
        <Field label="Property name" id="property-name" required maxLength={200} value={name}
          onChange={(e) => setName(e.target.value)} />
        <OwnerSelect books={books} id="property-owner" value={owner} onChange={setOwner} />
        <BankSelect books={books} id="property-bank" value={bank} onChange={setBank} />
        <Button type="submit" variant="secondary">Add property</Button>
      </form>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}

function PropertyRow({ property, books, refresh }: CardProps & { property: Property }) {
  const { error, add } = useAdd(refresh);
  const [unit, setUnit] = useState("");
  const [owner, setOwner] = useState("");
  const [bank, setBank] = useBank(books);
  const [fees, setFees] = useState({
    starts_on: "",
    grace_days: "5",
    flat_fee: "",
    percent: "",
    maximum: "",
  });
  const [nsf, setNsf] = useState({ starts_on: "", fee: "" });
  const owners = books.accounts.filter((a) => a.property_id === property.id).map((a) => a.owner);
  const at = (field: string) => `${field}-${property.id}`;
  return (
    <li className="flex flex-col gap-2 py-4" aria-label={property.name}>
      <p className="text-md font-medium">{property.name}</p>
      <p className="text-sm text-ink-subtle">
        Owned by {owners.join(", ") || "nobody yet"}.{" "}
        {property.late_fees ? lateFeeSummary(property.late_fees) : "No late fee terms"}.{" "}
        {nsfFeeSummary(property.nsf_fee)}.
      </p>
      <ul className="flex flex-col gap-1 text-sm">
        {property.units.map((u) => (
          <li key={u.id}>
            <span className="text-ink">{u.name}</span>
            <span className="text-ink-muted">
              {": "}
              {u.leases.length ? u.leases.map(leaseSummary).join("; then ") : "vacant"}
            </span>
          </li>
        ))}
      </ul>
      <details>
        <summary className="cursor-pointer text-sm text-ink-muted">Add to {property.name}</summary>
        <div className="mt-3 flex flex-col gap-4">
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={async (event) => {
              event.preventDefault();
              if (await add("units", { property_id: property.id, name: unit })) setUnit("");
            }}
          >
            <Field label="Unit name" id={at("unit")} required maxLength={200} value={unit}
              onChange={(e) => setUnit(e.target.value)} />
            <Button type="submit" variant="secondary">Add unit</Button>
          </form>
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              add(`properties/${property.id}/owners`, { owner_id: owner, bank_account_id: bank });
            }}
          >
            <OwnerSelect books={books} id={at("owner")} value={owner} onChange={setOwner} />
            <BankSelect books={books} id={at("bank")} value={bank} onChange={setBank} />
            <Button type="submit" variant="secondary">Add owner</Button>
          </form>
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              add("late-fees", {
                property_id: property.id,
                starts_on: fees.starts_on,
                grace_days: Number(fees.grace_days),
                flat_fee: fees.flat_fee || "0",
                percent: fees.percent || "0",
                maximum: fees.maximum || null,
              });
            }}
          >
            <Field label="Late fees from" id={at("fees-from")} type="date" required
              value={fees.starts_on} onChange={(e) => setFees({ ...fees, starts_on: e.target.value })} />
            <Field label="Grace days" id={at("grace")} inputMode="numeric" required
              value={fees.grace_days} onChange={(e) => setFees({ ...fees, grace_days: e.target.value })} />
            <Field label="Flat fee" id={at("flat")} inputMode="decimal" value={fees.flat_fee}
              onChange={(e) => setFees({ ...fees, flat_fee: e.target.value })} />
            <Field label="Percent of unpaid" id={at("percent")} inputMode="decimal"
              value={fees.percent} onChange={(e) => setFees({ ...fees, percent: e.target.value })} />
            <Field label="Up to" id={at("maximum")} inputMode="decimal" value={fees.maximum}
              onChange={(e) => setFees({ ...fees, maximum: e.target.value })} />
            <Button type="submit" variant="secondary">Set late fees</Button>
          </form>
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              add("nsf-fees", { property_id: property.id, starts_on: nsf.starts_on, fee: nsf.fee });
            }}
          >
            <Field label="NSF fee from" id={at("nsf-from")} type="date" required
              value={nsf.starts_on} onChange={(e) => setNsf({ ...nsf, starts_on: e.target.value })} />
            <Field label="NSF fee" id={at("nsf-fee")} inputMode="decimal" required value={nsf.fee}
              onChange={(e) => setNsf({ ...nsf, fee: e.target.value })} />
            <Button type="submit" variant="secondary">Set NSF fee</Button>
          </form>
          <Notice tone="error">{error}</Notice>
        </div>
      </details>
    </li>
  );
}
