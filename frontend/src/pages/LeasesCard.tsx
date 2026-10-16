import { useState } from "react";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import type { CardProps } from "./useAdd";
import { useAdd } from "./useAdd";
import { Form } from "../components/Form";

const BLANK = { unit: "", starts_on: "", ends_on: "", rent: "", tenants: [] as string[] };

// Tenants already in place and their leases: a new tenant's lease comes through a lease offer
// (Leasing, Draft leases) instead.
export function LeasesCard({ books, refresh }: CardProps) {
  const tenants = useAdd(refresh);
  const leases = useAdd(refresh);
  const [property, setProperty] = useState("");
  const [name, setName] = useState("");
  const [lease, setLease] = useState(BLANK);
  const units = books.properties.flatMap((p) => p.units.map((u) => ({ ...u, property: p })));
  const unit = units.find((u) => u.id === lease.unit);
  const onLease = books.tenants.filter((t) => t.property_id === unit?.property.id);

  function toggle(tenant: string, on: boolean) {
    const others = lease.tenants.filter((t) => t !== tenant);
    setLease({ ...lease, tenants: on ? [...others, tenant] : others });
  }

  return (
    <Card title="Tenants and leases in place">
      <ul className="mb-4 flex flex-col gap-1">
        {books.tenants.map((t) => (
          <li key={t.id}>
            {t.name} <span className="text-sm text-ink-subtle">({t.property})</span>
          </li>
        ))}
      </ul>
      <Form
        className="mb-8 flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          if (await tenants.add("tenants", { property_id: property, name })) setName("");
        }}
      >
        <Select label="Tenant of" id="tenant-property" required value={property}
          onChange={(e) => setProperty(e.target.value)}>
          <option value="">Choose a property</option>
          {books.properties.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </Select>
        <Field label="Tenant's name" id="tenant-name" required maxLength={200} value={name}
          onChange={(e) => setName(e.target.value)} />
        <Button type="submit" variant="secondary">Add tenant</Button>
      </Form>
      <Notice tone="error">{tenants.error}</Notice>

      <h3 className="mb-3 text-md font-medium">A lease in place</h3>
      <Form
        className="flex flex-col gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          const added = await leases.add("leases", {
            unit_id: lease.unit,
            starts_on: lease.starts_on,
            ends_on: lease.ends_on || null,
            monthly_rent: lease.rent,
            tenant_ids: lease.tenants,
          });
          if (added) setLease(BLANK);
        }}
      >
        <div className="flex flex-wrap items-end gap-3">
          <Select label="Unit" id="lease-unit" required value={lease.unit}
            onChange={(e) => setLease({ ...BLANK, unit: e.target.value })}>
            <option value="">Choose a unit</option>
            {units.map((u) => (
              <option key={u.id} value={u.id}>
                {u.property.name}, {u.name}
              </option>
            ))}
          </Select>
          <Field label="Starts" id="lease-starts" type="date" required value={lease.starts_on}
            onChange={(e) => setLease({ ...lease, starts_on: e.target.value })} />
          <Field label="Ends (empty for month to month)" id="lease-ends" type="date"
            value={lease.ends_on} onChange={(e) => setLease({ ...lease, ends_on: e.target.value })} />
          <Field label="Rent a month" id="lease-rent" inputMode="decimal" required value={lease.rent}
            onChange={(e) => setLease({ ...lease, rent: e.target.value })} />
        </div>
        {unit && (
          <fieldset className="flex flex-wrap gap-4 text-sm text-ink-muted">
            <legend className="mb-2">On the lease</legend>
            {onLease.length === 0 && <span>Add the tenants of {unit.property.name} first.</span>}
            {onLease.map((t) => (
              <label key={t.id} className="flex items-center gap-2">
                <input type="checkbox" checked={lease.tenants.includes(t.id)}
                  onChange={(e) => toggle(t.id, e.target.checked)} />
                {t.name}
              </label>
            ))}
          </fieldset>
        )}
        <div>
          <Button type="submit" variant="secondary" disabled={lease.tenants.length === 0}>
            Add lease
          </Button>
        </div>
      </Form>
      <Notice tone="error">{leases.error}</Notice>
    </Card>
  );
}
