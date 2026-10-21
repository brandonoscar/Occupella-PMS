import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import type { Tenant } from "../directory";
import type { Paged, PropertyRow } from "../rentals";
import { navigate } from "../router";
import { GoLink } from "./NeedsAttention";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

type Found = { kind: string; id: string; label: string; detail: string };

// A tenant renting at two properties at once (migration 0105, q009): a record at each, linked to
// their first. Each record keeps its own leases and money; they sign in once, as their first.
export function SamePersonCard({ tenant, refresh, say }: {
  tenant: Tenant;
  refresh: () => void;
  say: (said: string) => void;
}) {
  const [open, setOpen] = useState<"elsewhere" | "same" | null>(null);
  return (
    <Card title="Other properties">
      {tenant.same_person.length === 0 ? (
        <p className="mb-3 text-base text-ink-muted">
          Renting at another property too? Make {tenant.name} a tenant there, or say a tenant there is them.
        </p>
      ) : (
        <ul className="mb-3 flex flex-col gap-1 text-base" aria-label={`${tenant.name} at other properties`}>
          {tenant.same_person.map((other) => (
            <li key={other.id} className="flex flex-wrap items-center gap-2">
              <GoLink href={`/tenants/${other.id}`} className={LINK}>Tenant at {other.property.name}</GoLink>
              {other.first && <Badge tone="info">Signs in here</Badge>}
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => setOpen("elsewhere")}>Rent at another property</Button>
        <Button variant="secondary" onClick={() => setOpen("same")}>Same person as…</Button>
      </div>
      {open === "elsewhere" && <Elsewhere tenant={tenant} say={say} onClose={() => setOpen(null)} />}
      {open === "same" && <SamePerson tenant={tenant} refresh={refresh} say={say} onClose={() => setOpen(null)} />}
    </Card>
  );
}

function Elsewhere({ tenant, say, onClose }: { tenant: Tenant; say: (said: string) => void; onClose: () => void }) {
  const [properties, setProperties] = useState<PropertyRow[] | null>(null);
  const [property, setProperty] = useState("");
  const { error, record } = useRecord(() => {}, say);
  const taken = new Set([tenant.property.id, ...tenant.same_person.map((other) => other.property.id)]);
  useEffect(() => {
    api.get<Paged<PropertyRow>>("/api/properties?page=1&per_page=200")
      .then((page) => setProperties(page.items))
      .catch(() => setProperties([]));
  }, []);
  const choices = (properties ?? []).filter((p) => !taken.has(p.id));
  return (
    <SidePanel title="Rent at another property" onClose={onClose}>
      <p className="text-base text-ink-muted">
        {tenant.name} becomes a tenant there too, with the same contact details, to put on the lease there.
        What they hold at each property stays its own; they sign in once.
      </p>
      <Form className="flex flex-col gap-3" onSubmit={async (event) => {
        event.preventDefault();
        const name = choices.find((p) => p.id === property)?.name ?? "";
        let made = "";
        const done = await record<{ id: string }>(`/api/tenants/${tenant.id}/elsewhere`, { property_id: property }, (answer) => {
          made = answer.id;
          return `${tenant.name} is a tenant at ${name} too: put them on the lease there.`;
        });
        if (done) {
          onClose();
          navigate(`/tenants/${made}`);
        }
      }}>
        <Select label="Property" id="elsewhere-property" required value={property}
          onChange={(event) => setProperty(event.target.value)}>
          <option value="">Pick a property</option>
          {choices.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </Select>
        <Button type="submit">Make them a tenant there</Button>
      </Form>
      <Notice tone="error">{error}</Notice>
    </SidePanel>
  );
}

function SamePerson({ tenant, refresh, say, onClose }: {
  tenant: Tenant;
  refresh: () => void;
  say: (said: string) => void;
  onClose: () => void;
}) {
  const [words, setWords] = useState("");
  const [found, setFound] = useState<Found[] | null>(null);
  const { error, record } = useRecord(refresh, say);
  return (
    <SidePanel title="Same person as…" onClose={onClose}>
      <p className="text-base text-ink-muted">
        A tenant at another property who is {tenant.name} (an applicant who rents here already, say).
        They sign in once, as their first.
      </p>
      <Form className="flex flex-wrap items-end gap-3" onSubmit={async (event) => {
        event.preventDefault();
        const hits = await api.get<Found[]>(`/api/search?${new URLSearchParams({ q: words.trim() })}`);
        setFound(hits.filter((hit) => hit.kind === "tenant" && hit.id !== tenant.id));
      }}>
        <Field label="Name, email or phone" id="same-person-words" required value={words}
          onChange={(event) => setWords(event.target.value)} />
        <Button type="submit" variant="secondary">Find</Button>
      </Form>
      {found !== null && (found.length === 0 ? (
        <p className="text-base text-ink-muted">No other tenant holds those words.</p>
      ) : (
        <ul className="flex flex-col gap-2" aria-label="Tenants found">
          {found.map((hit) => (
            <li key={hit.id} className="flex flex-wrap items-center justify-between gap-2 text-base">
              <span>{hit.label} <span className="text-ink-muted">{hit.detail}</span></span>
              <Button variant="secondary" aria-label={`Same person: ${hit.label}, ${hit.detail}`} onClick={async () => {
                const done = await record(`/api/tenants/${tenant.id}/same-person`, { tenant_id: hit.id },
                  () => `${tenant.name} and ${hit.label} (${hit.detail}) are the same person now.`);
                if (done) onClose();
              }}>This is them</Button>
            </li>
          ))}
        </ul>
      ))}
      <Notice tone="error">{error}</Notice>
    </SidePanel>
  );
}

// A record linked to another signs in through the person's first: its Portal card says where.
export function SignsInElsewhere({ tenant }: { tenant: Tenant }) {
  const first = tenant.same_person.find((other) => other.first);
  if (first === undefined) return null;
  return (
    <Card title="Portal">
      <p className="text-base text-ink-muted">
        {tenant.name} signs in once, as their record at{" "}
        <GoLink href={`/tenants/${first.id}`} className={LINK}>{first.property.name}</GoLink>, and sees
        their leases at every property there. Invite them from that page.
      </p>
    </Card>
  );
}
