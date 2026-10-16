import { Users } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { ContactCard } from "../components/ContactCard";
import { EmptyState } from "../components/EmptyState";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { RecordActions } from "../components/RecordActions";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import type { Tenant, TenantRow } from "../directory";
import { money } from "../money";
import type { Paged } from "../rentals";
import { LEASE_STATUS, termLabel } from "../rentals";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import { ListBar } from "../components/ListBar";
import { scopeParams, useScope } from "../scope";
import { Said } from "../components/Said";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// Rentals → Tenants: the company's tenants a page at a time, found by their name, property or
// unit, each opening their own page (ADR 0005).
export function TenantsPage({ me }: { me: Me }) {
  const [page, setPage] = useState<Paged<TenantRow> | null>(null);
  const [scope, setScope] = useScope();
  const [archived, setArchived] = useState(false);
  const [number, setNumber] = useState(1);
  const [error, setError] = useState("");

  useEffect(() => {
    const query = new URLSearchParams({ page: String(number), archived: String(archived), ...scopeParams(scope) });
    api
      .get<Paged<TenantRow>>(`/api/tenants?${query}`)
      .then(setPage)
      .catch((failed) => setError(detail(failed)));
  }, [scope, archived, number]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Tenants" />
      <ListBar scope={scope} onScope={(next) => { setScope(next); setNumber(1); }}
        archived={archived} onArchived={(next) => { setArchived(next); setNumber(1); }} />
      <Notice tone="error">{error}</Notice>
      {page === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : page.items.length === 0 ? (
        <EmptyState icon={Users} title={archived ? "No archived tenants." : "No tenants found."}
          body="Add a tenant from Settings, Company setup." />
      ) : (
        <>
          <Wide>
            <table className="w-full text-base">
              <thead>
                <tr>
                  <th className="text-left">Tenant</th>
                  <th className="text-left">Property</th>
                  <th className="text-left">Unit</th>
                  <th className="text-left">Lease</th>
                  <th className="text-left">Email</th>
                  <th className="text-left">Phone</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((row) => (
                  <tr key={row.id} aria-label={row.name}>
                    <td><GoLink href={`/tenants/${row.id}`} className={LINK}>{row.name}</GoLink></td>
                    <td className="text-ink-muted">{row.property.name}</td>
                    <td className="text-ink-muted">{row.lease?.unit.name}</td>
                    <td>
                      {row.lease ? (
                        <Badge tone={LEASE_STATUS[row.lease.status].tone}>{LEASE_STATUS[row.lease.status].word}</Badge>
                      ) : (
                        <span className="text-sm text-ink-subtle">No lease</span>
                      )}
                    </td>
                    <td className="text-ink-muted">{row.email}</td>
                    <td className="text-ink-muted">{row.phone}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
          <Pager page={page} onPage={setNumber} />
        </>
      )}
    </Shell>
  );
}

// A tenant: their contact details, what is held for them, and every lease they have been on.
export function TenantPage({ me, id }: { me: Me; id: string }) {
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");

  function refresh() {
    api.get<Tenant>(`/api/tenants/${id}`).then(setTenant).catch((failed) => setError(detail(failed)));
  }
  useEffect(refresh, [id]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      {tenant === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <PageHeader title={tenant.name} actions={
            <RecordActions path={`/api/tenants/${id}`} name={tenant.name} archived={tenant.archived}
              onChange={(done) => { setSaid(done); refresh(); }} />
          } />
          <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-muted">
            <GoLink href={`/properties/${tenant.property.id}`} className={LINK}>{tenant.property.name}</GoLink>
            <span data-testid="held">Prepaid rent {money(tenant.prepaid)} · Deposit held {money(tenant.deposit)}</span>
          </p>
          <Said>{said}</Said>
          <ContactCard path={`/api/tenants/${id}`} contact={tenant.contact} refresh={refresh} name={tenant.name} />
          <Card title="Leases">
            {tenant.leases.length === 0 ? (
              <EmptyState icon={Users} title="No lease yet." body="A lease comes through Leasing, Draft leases." />
            ) : (
              <Wide>
                <table className="w-full text-base">
                  <thead>
                    <tr>
                      <th className="text-left">Unit</th>
                      <th className="text-left">Term</th>
                      <th className="text-left">State</th>
                      <th className="text-right">Rent</th>
                      <th className="text-right">Owes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tenant.leases.map((lease) => (
                      <tr key={lease.id} aria-label={`${lease.property.name}, ${lease.unit.name}`}>
                        <td>
                          <GoLink href={`/leases/${lease.id}`} className={LINK}>
                            {lease.unit.name}, {lease.property.name}
                          </GoLink>
                        </td>
                        <td>{termLabel(lease)}</td>
                        <td><Badge tone={LEASE_STATUS[lease.status].tone}>{LEASE_STATUS[lease.status].word}</Badge></td>
                        <td className="text-right">{money(lease.monthly_rent)}</td>
                        <td className="text-right">{money(lease.owed)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Wide>
            )}
          </Card>
        </>
      )}
    </Shell>
  );
}
