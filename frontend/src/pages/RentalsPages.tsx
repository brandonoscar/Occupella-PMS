import { Building2 } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { ListBar } from "../components/ListBar";
import { RecordActions } from "../components/RecordActions";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import { money } from "../money";
import { scopeParams, useScope } from "../scope";
import type { RentLease } from "../rent";
import type { Paged, Property, PropertyRow, Unit } from "../rentals";
import { LEASE_STATUS, occupancy, rentLabel, termLabel, unitState } from "../rentals";
import { GoLink } from "./NeedsAttention";
import { LeaseRow } from "./RentPage";
import { FilesCard } from "./FilesCard";
import { STAFF_NAV } from "./staffNav";
import { Said } from "../components/Said";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// The way to a record's own page from a table: its name as a link.
function RecordLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <GoLink href={href} className="font-medium text-iris hover:text-iris-hover hover:underline">
      {children}
    </GoLink>
  );
}

// Rentals → Properties: the company's properties a page at a time, narrowed to a property group
// or one property (the picker every list page has; search is the bar's), each opening its own
// page (ADR 0005).
export function PropertiesPage({ me }: { me: Me }) {
  const [page, setPage] = useState<Paged<PropertyRow> | null>(null);
  const [scope, setScope] = useScope();
  const [archived, setArchived] = useState(false);
  const [number, setNumber] = useState(1);
  const [error, setError] = useState("");

  useEffect(() => {
    const query = new URLSearchParams({ page: String(number), archived: String(archived), ...scopeParams(scope) });
    api
      .get<Paged<PropertyRow>>(`/api/properties?${query}`)
      .then(setPage)
      .catch((failed) => setError(detail(failed)));
  }, [scope, archived, number]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Properties" actions={<GoLink href="/settings/setup" className="inline-flex h-9 items-center rounded-sm bg-iris px-3.5 text-base font-medium text-canvas hover:bg-iris-hover">Add property</GoLink>} />
      <ListBar scope={scope} onScope={(next) => { setScope(next); setNumber(1); }}
        archived={archived} onArchived={(next) => { setArchived(next); setNumber(1); }} />
      <Notice tone="error">{error}</Notice>
      {page === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : page.items.length === 0 ? (
        <EmptyState icon={Building2} title={archived ? "No archived properties." : "No properties found."}
          body="Add a property from Settings, Company setup." />
      ) : (
        <>
          <Wide>
            <table className="w-full text-base">
              <thead>
                <tr>
                  <th className="text-left">Property</th>
                  <th className="text-left">Owners</th>
                  <th className="text-left">Units</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((row) => (
                  <tr key={row.id} aria-label={row.name}>
                    <td><RecordLink href={`/properties/${row.id}`}>{row.name}</RecordLink></td>
                    <td className="text-ink-muted">{row.owners.join(", ")}</td>
                    <td>{occupancy(row)}</td>
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

// A property: what its owners' accounts hold, and its units with each one's lease.
export function PropertyPage({ me, id }: { me: Me; id: string }) {
  const [property, setProperty] = useState<Property | null>(null);
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");

  function refresh() {
    api.get<Property>(`/api/properties/${id}`).then(setProperty).catch((failed) => setError(detail(failed)));
  }
  useEffect(refresh, [id]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      {property === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <PageHeader title={property.name} actions={
            <RecordActions path={`/api/properties/${id}`} name={property.name} archived={property.archived}
              onChange={(done) => { setSaid(done); refresh(); }} />
          } />
          <Said>{said}</Said>
          <Card title="Owners">
            {property.owners.length === 0 ? (
              <EmptyState icon={Building2} title="No owner's account yet." body="Open one from Settings, Company setup." />
            ) : (
            <Wide>
              <table className="w-full text-base">
                <thead>
                  <tr>
                    <th className="text-left">Owner</th>
                    <th className="text-left">Trust bank account</th>
                    <th className="text-right">Holds</th>
                  </tr>
                </thead>
                <tbody>
                  {property.owners.map((owner) => (
                    <tr key={owner.account}>
                      <td>{owner.owner}</td>
                      <td className="text-ink-muted">{owner.bank_account}</td>
                      <td className="text-right">{money(owner.balance)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Wide>
            )}
          </Card>
          <Card title="Units">
            {property.units.length === 0 ? (
              <EmptyState icon={Building2} title="No units yet." body="Add units from Settings, Company setup." />
            ) : (
              <Wide>
                <table className="w-full text-base">
                  <thead>
                    <tr>
                      <th className="text-left">Unit</th>
                      <th className="text-left">State</th>
                      <th className="text-left">Tenants</th>
                      <th className="text-right">Rent</th>
                      <th className="text-right">Owes</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {property.units.map((unit) => (
                      <tr key={unit.id} aria-label={unit.name}>
                        <td><RecordLink href={`/units/${unit.id}`}>{unit.name}</RecordLink></td>
                        <td><Badge tone={unitState(unit).tone}>{unitState(unit).word}</Badge></td>
                        <td className="text-ink-muted">{unit.lease?.tenants.join(", ")}</td>
                        <td className="text-right">{unit.lease && money(unit.lease.monthly_rent)}</td>
                        <td className="text-right">{unit.lease && money(unit.lease.owed)}</td>
                        <td className="text-right">
                          {unit.lease && <RecordLink href={`/leases/${unit.lease.id}`}>Open lease</RecordLink>}
                        </td>
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

// A unit and every lease it has had, newest first.
export function UnitPage({ me, id }: { me: Me; id: string }) {
  const [unit, setUnit] = useState<Unit | null>(null);
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");

  function refresh() {
    api.get<Unit>(`/api/units/${id}`).then(setUnit).catch((failed) => setError(detail(failed)));
  }
  useEffect(refresh, [id]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      {unit === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <PageHeader
            title={`${unit.name}, ${unit.property.name}`}
            actions={
              <RecordActions path={`/api/units/${id}`} name={unit.name} archived={unit.archived}
                onChange={(done) => { setSaid(done); refresh(); }} />
            }
          />
          <p className="text-sm">
            <RecordLink href={`/properties/${unit.property.id}`}>{unit.property.name}</RecordLink>
          </p>
          <Said>{said}</Said>
          <Card title="Leases">
            {unit.leases.length === 0 ? (
              <EmptyState icon={Building2} title="No lease yet." body="A lease comes through Leasing, Draft leases." />
            ) : (
              <Wide>
                <table className="w-full text-base">
                  <thead>
                    <tr>
                      <th className="text-left">Tenants</th>
                      <th className="text-left">Term</th>
                      <th className="text-left">State</th>
                      <th className="text-right">Rent</th>
                      <th className="text-right">Owes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {unit.leases.map((lease) => (
                      <tr key={lease.id} aria-label={lease.tenants.join(", ")}>
                        <td><RecordLink href={`/leases/${lease.id}`}>{lease.tenants.join(", ")}</RecordLink></td>
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

type LeaseDetail = RentLease & { property_id: string; unit_id: string };

// A lease and all of its rent work (ADR 0005): its charges, payments, recurring charges, rent
// changes and renters insurance, with the forms to record each; move-out once it has ended.
export function LeasePage({ me, id }: { me: Me; id: string }) {
  const [lease, setLease] = useState<LeaseDetail | null>(null);
  const [error, setError] = useState("");

  function refresh() {
    api.get<LeaseDetail>(`/api/leases/${id}`).then(setLease).catch((failed) => setError(detail(failed)));
  }
  useEffect(refresh, [id]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      {lease === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <PageHeader title={`${lease.unit}, ${lease.property}`} />
          <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-muted">
            <RecordLink href={`/properties/${lease.property_id}`}>{lease.property}</RecordLink>
            <RecordLink href={`/units/${lease.unit_id}`}>{lease.unit}</RecordLink>
            <span>{termLabel(lease)}</span>
            <span>{rentLabel(lease.monthly_rent)}</span>
            {lease.ends_on !== null && <GoLink href="/move-out">Move-out</GoLink>}
          </p>
          <ul className="flex flex-col">
            <LeaseRow lease={lease} refresh={refresh} />
          </ul>
          <FilesCard kind="move_out_photo" aboutId={id} name={`${lease.unit}, ${lease.property}`} />
        </>
      )}
    </Shell>
  );
}
