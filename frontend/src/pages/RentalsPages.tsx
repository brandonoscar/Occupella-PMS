import { Building2, ChartPie } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { WriteEmail } from "../components/EmailsCard";
import { EmptyState } from "../components/EmptyState";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { ListBar } from "../components/ListBar";
import { RecordActions } from "../components/RecordActions";
import { Card, PageHeader, Shell } from "../components/Shell";
import { TenantLinks } from "../components/TenantLinks";
import { Wide } from "../components/Wide";
import type { Keep } from "../latest";
import { keepAll, latest } from "../latest";
import { money } from "../money";
import { scopeParams, useScope } from "../scope";
import type { Paged, Property, PropertyRow, Unit } from "../rentals";
import { LEASE_STATUS, OWNERSHIP_STATE, occupancy, ownershipStates, sharesLine, termLabel, unitState } from "../rentals";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import { Said } from "../components/Said";
import { AddressCard } from "./AddressCard";
import { LawCard } from "./LawCard";
import { FilesCard } from "./FilesCard";
import { TaxCreditLimitsCard, TaxCreditUnitCard } from "./TaxCreditCards";
import { navigate } from "../router";
import { agreementSummary, lateFeeSummary, leaseBreakFeeSummary, nsfFeeSummary } from "../setup";
import { rateLine } from "../depositInterest";
import { day, usDate } from "../money";
import type { Panel } from "./SetupPanels";
import { MarketRent, UnitDetailsCard, UnitSize } from "./UnitDetailsCard";
import {
  AddLeasePanel,
  AddOwnerPanel,
  AddPropertyPanel,
  AddUnitPanel,
  LateFeesPanel,
  DepositInterestPanel,
  LeaseBreakFeePanel,
  NsfFeePanel,
  OwnershipPanel,
  TermsPanel,
} from "./SetupPanels";

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
  const [adding, setAdding] = useState(false);
  const [said, setSaid] = useState("");

  useEffect(() => {
    const run = latest();
    const query = new URLSearchParams({ page: String(number), archived: String(archived), ...scopeParams(scope) });
    api
      .get<Paged<PropertyRow>>(`/api/properties?${query}`)
      .then(run.keep(setPage))
      .catch(run.keep((failed) => setError(detail(failed))));
    return run.stop;
  }, [scope, archived, number]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Properties" actions={<Button onClick={() => setAdding(true)}>Add property</Button>} />
      <Said>{said}</Said>
      {adding && (
        <AddPropertyPanel panel={{ say: setSaid, refresh: () => undefined, close: () => setAdding(false) }}
          opened={(id) => navigate(`/properties/${id}`)} />
      )}
      <ListBar scope={scope} onScope={(next) => { setScope(next); setNumber(1); }}
        archived={archived} onArchived={(next) => { setArchived(next); setNumber(1); }} />
      <Notice tone="error">{error}</Notice>
      {page === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : page.items.length === 0 ? (
        <EmptyState icon={Building2} title={archived ? "No archived properties." : "No properties found."} />
      ) : (
        <>
          <Wide>
            <table className="w-full text-base">
              <thead>
                <tr>
                  <th className="text-left">Property</th>
                  <th className="text-left">Address</th>
                  <th className="text-left">Owners</th>
                  <th className="text-left">Units</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((row) => (
                  <tr key={row.id} aria-label={row.name}>
                    <td><RecordLink href={`/properties/${row.id}`}>{row.name}</RecordLink></td>
                    <td className="text-ink-muted">{row.address ?? ""}</td>
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
type EmailedTenants = { emailed: { name: string; to: string }[]; without_email: string[] };

// What emailing a property's tenants did: how many were written to, and who has no email.
export function tenantsEmailed(sent: EmailedTenants): string {
  const count = sent.emailed.length;
  const emailed = `Emailed ${count} tenant${count === 1 ? "" : "s"}.`;
  return sent.without_email.length
    ? `${emailed} No email on file: ${sent.without_email.join(", ")}.`
    : emailed;
}

export function PropertyPage({ me, id }: { me: Me; id: string }) {
  const [property, setProperty] = useState<Property | null>(null);
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState<"" | "unit" | "owner" | "terms" | "ownership" | "late" | "nsf" | "break" | "interest" | "email">("");
  const [account, setAccount] = useState("");

  function refresh(keep: Keep = keepAll) {
    api.get<Property>(`/api/properties/${id}`).then(keep(setProperty)).catch(keep((failed) => setError(detail(failed))));
  }
  useEffect(() => {
    const run = latest();
    refresh(run.keep);
    return run.stop;
  }, [id]);
  const panel: Panel = { say: setSaid, refresh, close: () => setOpen("") };

  return (
    <Shell me={me} nav={STAFF_NAV}>
      {property === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <PageHeader title={property.name} actions={
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="secondary" onClick={() => setOpen("email")}>Email tenants</Button>
              <RecordActions path={`/api/properties/${id}`} name={property.name} archived={property.archived}
                onChange={(done) => { setSaid(done); refresh(); }} />
            </div>
          } />
          <Said>{said}</Said>
          <AddressCard propertyId={id} address={property.address} location={property.location}
            canLocate={property.can_locate} onChange={refresh} />
          <LawCard propertyId={id} again={property} />
          <Card title="Owners">
            {property.owners.length === 0 ? (
              <EmptyState icon={Building2} title="No owner's account yet." />
            ) : (
            <Wide>
              <table className="w-full text-base" aria-label="Owners">
                <thead>
                  <tr>
                    <th className="text-left">Owner</th>
                    <th className="text-left">Trust bank account</th>
                    <th className="text-left">Management terms</th>
                    <th className="text-right">Holds</th>
                    <th><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {property.owners.map((owner) => (
                    <tr key={owner.account} aria-label={owner.owner}>
                      <td><RecordLink href={`/owners/${owner.owner_id}`}>{owner.owner}</RecordLink></td>
                      <td className="text-ink-muted">{owner.bank_account}</td>
                      <td className="text-sm text-ink-muted">
                        {owner.agreement
                          ? `${agreementSummary(owner.agreement)} (from ${usDate(owner.agreement.starts_on)})`
                          : "No terms in force"}
                      </td>
                      <td className="text-right">{money(owner.balance)}</td>
                      <td className="text-right">
                        <Button size="sm" variant="secondary" onClick={() => { setAccount(owner.account); setOpen("terms"); }}>
                          Set terms
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Wide>
            )}
            <div className="mt-3"><Button variant="secondary" onClick={() => setOpen("owner")}>Add owner</Button></div>
          </Card>
          {property.owners.length > 0 && <OwnershipCard property={property} onSet={() => setOpen("ownership")} />}
          <Card title="Fees">
            <dl className="grid grid-cols-1 gap-3 text-base sm:grid-cols-2">
              <div>
                <dt className="text-sm text-ink-muted">Late fees</dt>
                <dd data-testid="late-fees">
                  {property.late_fees
                    ? `${lateFeeSummary(property.late_fees)} (from ${usDate(property.late_fees.starts_on)})`
                    : "No late fee terms"}
                </dd>
                <dd><Button size="sm" variant="secondary" onClick={() => setOpen("late")}>Set late fees</Button></dd>
              </div>
              <div>
                <dt className="text-sm text-ink-muted">NSF fee</dt>
                <dd data-testid="nsf-fee">{nsfFeeSummary(property.nsf_fee)}</dd>
                <dd><Button size="sm" variant="secondary" onClick={() => setOpen("nsf")}>Set NSF fee</Button></dd>
              </div>
              <div>
                <dt className="text-sm text-ink-muted">Lease-break fee</dt>
                <dd data-testid="lease-break-fee">
                  {property.lease_break_fee
                    ? `${leaseBreakFeeSummary(property.lease_break_fee)} (leases from ${usDate(property.lease_break_fee.starts_on)})`
                    : "No lease-break fee"}
                </dd>
                <dd><Button size="sm" variant="secondary" onClick={() => setOpen("break")}>Set lease-break fee</Button></dd>
              </div>
              <div>
                <dt className="text-sm text-ink-muted">Deposit interest</dt>
                <dd data-testid="deposit-interest">{rateLine(property.deposit_interest)}</dd>
                <dd><Button size="sm" variant="secondary" onClick={() => setOpen("interest")}>Set deposit interest</Button></dd>
              </div>
            </dl>
          </Card>
          <TaxCreditLimitsCard propertyId={id} />
          <Card title="Units">
            {property.units.length === 0 ? (
              <EmptyState icon={Building2} title="No units yet." />
            ) : (
              <Wide>
                <table className="w-full text-base">
                  <thead>
                    <tr>
                      <th className="text-left">Unit</th>
                      <th className="text-left">State</th>
                      <th className="text-left">Tenants</th>
                      <th className="text-right">Rent</th>
                      <th className="text-right">Market rent</th>
                      <th className="text-right">Owes</th>
                      <th><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {property.units.map((unit) => (
                      <tr key={unit.id} aria-label={unit.name}>
                        <td>
                          <RecordLink href={`/units/${unit.id}`}>{unit.name}</RecordLink>
                          <UnitSize details={unit.details} />
                        </td>
                        <td><Badge tone={unitState(unit).tone}>{unitState(unit).word}</Badge></td>
                        <td>{unit.lease && <TenantLinks people={unit.lease.people} />}</td>
                        <td className="text-right">{unit.lease && money(unit.lease.monthly_rent)}</td>
                        <td className="text-right"><MarketRent details={unit.details} /></td>
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
            <div className="mt-3"><Button variant="secondary" onClick={() => setOpen("unit")}>Add unit</Button></div>
          </Card>
          <FilesCard kind="property_document" aboutId={id} name={property.name} />
          {open === "unit" && <AddUnitPanel panel={panel} propertyId={id} />}
          {open === "owner" && <AddOwnerPanel panel={panel} propertyId={id} />}
          {open === "ownership" && (
            <OwnershipPanel panel={panel} propertyId={id} name={property.name} owners={property.owners} />
          )}
          {open === "late" && <LateFeesPanel panel={panel} propertyId={id} />}
          {open === "nsf" && <NsfFeePanel panel={panel} propertyId={id} />}
          {open === "break" && <LeaseBreakFeePanel panel={panel} propertyId={id} />}
          {open === "interest" && <DepositInterestPanel panel={panel} propertyId={id} />}
          {open === "email" && (
            <WriteEmail title={`Email tenants of ${property.name}`} onClose={panel.close}
              note="Everyone on a lease in effect today gets their own copy, kept on their Email tab."
              onSend={async (subject, body) => {
                try {
                  const sent = await api.post<EmailedTenants>("/api/emails/property", { property_id: id, subject, body });
                  setSaid(tenantsEmailed(sent));
                  return "";
                } catch (failed) {
                  return detail(failed);
                }
              }} />
          )}
          {open === "terms" && (
            <TermsPanel panel={panel} accounts={property.owners
              .map((o) => ({ account: o.account, label: `${o.owner}, ${property.name}` }))
              .sort((a, b) => (a.account === account ? -1 : b.account === account ? 1 : 0))}
              everyOwnerOf={property.owners.length > 1 ? property.name : undefined} />
          )}
        </>
      )}
    </Shell>
  );
}

// Who owns what part of a property (migration 0094, q044): each ownership from its day, the one
// in force today marked. With one in force its payments, prepaid rent used, deposits kept and
// bills split by ownership go to the owners by their shares; without, staff choose an account.
function OwnershipCard({ property, onSet }: { property: Property; onSet: () => void }) {
  const states = ownershipStates(property.ownership, day(new Date()));
  return (
    <Card title="Ownership">
      {property.ownership.length === 0 ? (
        <EmptyState icon={ChartPie} title="No ownership shares."
          body={property.owners.length > 1
            ? "Staff choose which owner's account each payment goes to. Set each owner's percent to split them."
            : "Set each owner's percent when another owner shares the property."} />
      ) : (
        <Wide>
          <table className="w-full text-base" aria-label="Ownership">
            <thead>
              <tr>
                <th className="text-left">From</th>
                <th className="text-left">Shares</th>
                <th className="text-left">Recorded by</th>
                <th className="text-left">State</th>
              </tr>
            </thead>
            <tbody>
              {property.ownership.map((o, n) => (
                <tr key={o.id}>
                  <td className="whitespace-nowrap">{usDate(o.starts_on)}</td>
                  <td>{sharesLine(o.shares)}</td>
                  <td className="text-ink-muted">{o.recorded_by ?? "—"}</td>
                  <td><Badge tone={OWNERSHIP_STATE[states[n]!].tone}>{OWNERSHIP_STATE[states[n]!].word}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
      <div className="mt-3"><Button variant="secondary" onClick={onSet}>Set ownership</Button></div>
    </Card>
  );
}

// A unit, every lease it has had (newest first) and its files.
export function UnitPage({ me, id }: { me: Me; id: string }) {
  const [unit, setUnit] = useState<Unit | null>(null);
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);

  function refresh(keep: Keep = keepAll) {
    api.get<Unit>(`/api/units/${id}`).then(keep(setUnit)).catch(keep((failed) => setError(detail(failed))));
  }
  useEffect(() => {
    const run = latest();
    refresh(run.keep);
    return run.stop;
  }, [id]);

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
          <UnitDetailsCard unitId={id} details={unit.details} refresh={refresh} />
          <Card title="Leases">
            {unit.leases.length === 0 ? (
              <EmptyState icon={Building2} title="No lease yet." body="A new tenant's lease comes through Leasing, Draft leases." />
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
                        <td><TenantLinks people={lease.people} /></td>
                        <td><RecordLink href={`/leases/${lease.id}`}>{termLabel(lease)}</RecordLink></td>
                        <td><Badge tone={LEASE_STATUS[lease.status].tone}>{LEASE_STATUS[lease.status].word}</Badge></td>
                        <td className="text-right">{money(lease.monthly_rent)}</td>
                        <td className="text-right">{money(lease.owed)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Wide>
            )}
            <div className="mt-3"><Button variant="secondary" onClick={() => setAdding(true)}>Add lease in place</Button></div>
          </Card>
          <TaxCreditUnitCard unitId={id} />
          {adding && (
            <AddLeasePanel panel={{ say: setSaid, refresh, close: () => setAdding(false) }} unitId={id}
              propertyId={unit.property.id} propertyName={unit.property.name} />
          )}
          <FilesCard kind="unit_document" also={["listing_photo"]} aboutId={id} name={`${unit.name}, ${unit.property.name}`} />
        </>
      )}
    </Shell>
  );
}

