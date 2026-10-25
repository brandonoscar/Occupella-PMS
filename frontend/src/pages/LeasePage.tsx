import { useEffect, useRef, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button, LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { NotesCard } from "../components/NotesCard";
import { Notice } from "../components/Notice";
import type { RowAction } from "../components/RowMenu";
import { RowMenu } from "../components/RowMenu";
import { DepositInterestForm } from "./DepositInterestForm";
import { Said } from "../components/Said";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Tabs } from "../components/Tabs";
import { Wide } from "../components/Wide";
import { day, money, usDate } from "../money";
import { downloadHref, FORMATS } from "../reports";
import {
  balanceDue,
  depositAskedLine,
  depositKindsLine,
  dueDayLabel,
  dueDayLine,
  holdLine,
  insuranceBadge,
  insuranceLine,
  moveHome,
  rentChangeLine,
} from "../rent";
import type { LeaseDetail, LeasePerson, Ledger } from "../rentals";
import { LEASE_STATUS, LEDGER_KIND, PORTAL, rentLabel, termLabel } from "../rentals";
import { AssistanceCard } from "./AssistanceCard";
import { LeaseAutopayCard } from "./AutopayCards";
import { TaxCreditLeaseCard } from "./TaxCreditCards";
import { FilesCard } from "./FilesCard";
import { NoticesCard } from "./NoticesCard";
import { InsuranceForms } from "./InsuranceForms";
import { LeasingFeeForm } from "./LeasingFeeForm";
import { EndLeaseForm, HouseholdCards, RemoveTenantForm } from "./LeasePeople";
import { TransferForm } from "./LeaseTransfer";
import type { Recorder } from "./LeaseForms";
import {
  ChargeForm,
  DepositForm,
  DueDayForm,
  HoldForm,
  OpenChargeRow,
  PaymentForm,
  PaymentRow,
  RecurringForm,
  RecurringLine,
  ReleaseForm,
  RentChangeForm,
} from "./LeaseForms";
import { navigate } from "../router";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

type Panel =
  | "payment" | "charge" | "rent" | "due" | "recurring" | "insurance" | "deposit" | "leasing" | "hold"
  | "release" | "end" | "remove" | "move" | "interest";
const PANELS: Record<Panel, string> = {
  payment: "Receive payment",
  charge: "Enter charge",
  rent: "Change rent",
  due: "Change rent due day",
  recurring: "Add recurring charge",
  insurance: "Renters insurance",
  deposit: "Record deposit",
  leasing: "Take leasing fee",
  hold: "Place payment hold",
  release: "Release payment hold",
  end: "End lease",
  remove: "Take a tenant off the lease",
  move: "Move to another unit",
  interest: "Deposit interest",
};
type Tab = "summary" | "ledger" | "tenants" | "files" | "notes";
const TABS: { value: Tab; label: string }[] = [
  { value: "summary", label: "Summary" },
  { value: "ledger", label: "Ledger" },
  { value: "tenants", label: "Tenants" },
  { value: "files", label: "Files" },
  { value: "notes", label: "Notes" },
];

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// A lease (the founder's punch list, ADR 0006: "the most important page"): property, unit and
// tenants, where it stands and the balance due at the top; Receive payment and Enter charge, and
// the rest of its rent work in the ⋯ menu, each in a side panel; then Summary, Ledger, Tenants,
// Files and Notes. The PMS records what reached the bank; it takes no payments itself.
export function LeasePage({ me, id }: { me: Me; id: string }) {
  const [lease, setLease] = useState<LeaseDetail | null>(null);
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("summary");
  const [panel, setPanel] = useState<Panel | null>(null);
  // The tenant the remove panel takes off the lease.
  const [leaving, setLeaving] = useState<LeasePerson | null>(null);
  const { said, error: refused, record } = useRecord(refresh);

  // The lease the page shows. An answer is kept only while it is still that lease's: a move to
  // another unit refreshes the lease it left as it opens the new one, and that answer can come
  // back last.
  const shown = useRef(id);
  function refresh() {
    const asked = id;
    const keep = <T,>(set: (value: T) => void) => (value: T) => {
      if (shown.current === asked) set(value);
    };
    api.get<LeaseDetail>(`/api/leases/${asked}`).then(keep(setLease)).catch((failed) => keep(setError)(detail(failed)));
    api.get<Ledger>(`/api/leases/${asked}/ledger`).then(keep(setLedger)).catch((failed) => keep(setError)(detail(failed)));
  }
  useEffect(() => {
    shown.current = id;
    refresh();
  }, [id]);

  // A form in a side panel closes once what it recorded is recorded.
  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setPanel(null);
    return recorded;
  };

  if (lease === null) {
    return (
      <Shell me={me} nav={STAFF_NAV}>
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      </Shell>
    );
  }
  const names = lease.tenants.map((t) => t.name).join(", ");
  // A payment pays what the leases its tenants moved from still owe too (migration 0091).
  const due = balanceDue(lease);
  const owes = Number(due) > 0;
  // The hold not yet released, if any: one at a time.
  const open = lease.holds.find((h) => h.ends_on === null) ?? null;
  const actions: RowAction[] = [
    { label: "Change rent", onSelect: () => setPanel("rent") },
    { label: "Change rent due day", onSelect: () => setPanel("due") },
    { label: "Add recurring charge", onSelect: () => setPanel("recurring") },
    { label: "Renters insurance", onSelect: () => setPanel("insurance") },
    { label: "Record deposit", onSelect: () => setPanel("deposit") },
    { label: "Deposit interest", onSelect: () => setPanel("interest") },
    { label: "Take leasing fee", onSelect: () => setPanel("leasing") },
    open === null
      ? { label: "Place payment hold", onSelect: () => setPanel("hold") }
      : { label: "Release payment hold", onSelect: () => setPanel("release") },
    { label: "Record NSF", onSelect: () => setTab("ledger") },
    { label: "Write a notice", onSelect: () => setTab("files") },
    { label: "Renew lease", onSelect: () => navigate(`/draft-leases?lease=${id}`) },
    ...(lease.moved_to === null ? [{ label: "Move to another unit", onSelect: () => setPanel("move") }] : []),
    { label: "End lease", onSelect: () => setPanel("end") },
    { label: "Move out", onSelect: () => navigate("/move-out") },
  ];

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={`${lease.property} · ${lease.unit} · ${names}`} actions={
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => setPanel("payment")}>Receive payment</Button>
          <Button variant="secondary" onClick={() => setPanel("charge")}>Enter charge</Button>
          <RowMenu label="this lease" actions={actions} />
        </div>
      } />
      <div className="flex flex-wrap items-end justify-between gap-4">
        <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-muted">
          <Badge tone={LEASE_STATUS[lease.status].tone}>{LEASE_STATUS[lease.status].word}</Badge>
          <GoLink href={`/properties/${lease.property_id}`} className={LINK}>{lease.property}</GoLink>
          <GoLink href={`/units/${lease.unit_id}`} className={LINK}>{lease.unit}</GoLink>
          <span>{termLabel(lease)}</span>
        </p>
        <p className="flex flex-col items-end">
          <span className="text-sm text-ink-muted">Balance due</span>
          <span data-testid="balance-due" className={"font-display text-xl " + (owes ? "text-danger" : "text-ink")}>
            {money(due)}
          </span>
          {Number(lease.carried) > 0 && lease.moved_from && (
            <span className="text-sm text-ink-muted" data-testid="carried">
              {`Includes ${money(lease.carried)} still owed at ${moveHome(lease.moved_from)}`}
            </span>
          )}
        </p>
      </div>
      {lease.moved_from && (
        <p className="text-sm text-ink-muted" data-testid="moved-from">
          {`Moved here on ${usDate(lease.moved_from.moved_on)} from `}
          <GoLink href={`/leases/${lease.moved_from.lease_id}`} className={LINK}>{moveHome(lease.moved_from)}</GoLink>
          {". What that lease still owes is its owner's, and a payment here pays it first."}
        </p>
      )}
      {lease.moved_to && (
        <Notice tone="info">
          {`The tenants moved to ${moveHome(lease.moved_to)} on ${usDate(lease.moved_to.moved_on)}; their deposits went with them.`}
        </Notice>
      )}
      {lease.moved_to && (
        <p className="text-sm" data-testid="moved-to">
          <GoLink href={`/leases/${lease.moved_to.lease_id}`} className={LINK}>Open the new lease</GoLink>
        </p>
      )}
      {lease.on_hold && (
        <Notice tone="error">
          {`On a payment hold. ${lease.holds.filter((h) => h.in_force).map(holdLine).join(". ")}. A payment is refused unless you accept it past the hold.`}
        </Notice>
      )}
      <Said>{said}</Said>
      {/* A side panel's refusal shows in the panel, once. */}
      {panel === null && <Notice tone="error">{refused}</Notice>}
      <div>
        <Tabs label="Lease" tabs={TABS} value={tab} onChange={setTab} />
        {tab === "summary" && <Summary lease={lease} record={record} recordDeposit={() => setPanel("deposit")} />}
        {tab === "ledger" && <LedgerTab lease={lease} ledger={ledger} record={record} />}
        {tab === "tenants" && (
          <div className="flex flex-col gap-6">
            <TenantsTab people={lease.people} refresh={refresh}
              onRemove={(person) => { setLeaving(person); setPanel("remove"); }} />
            <HouseholdCards leaseId={id} />
          </div>
        )}
        {tab === "files" && (
          <FilesCard kind="move_out_photo" also={["lease_document"]} listed={["payment_attachment"]} aboutId={id} name={`${lease.unit}, ${lease.property}`}
            note="Its tenants download its lease documents in the portal, with the lease as signed; move-out photos stay here." />
        )}
        {tab === "files" && <NoticesCard leaseId={id} />}
        {tab === "notes" && <NotesCard about="lease" aboutId={id} name={`${lease.unit}, ${lease.property}`} />}
      </div>
      {panel !== null && (
        <SidePanel title={PANELS[panel]} onClose={() => setPanel(null)}>
          {panel === "payment" && (
            <PaymentForm lease={lease} record={record} onRecorded={(another) => { if (!another) setPanel(null); }} />
          )}
          {panel === "charge" && <ChargeForm lease={lease} record={recordThenClose} />}
          {panel === "rent" && <RentChangeForm lease={lease} record={recordThenClose} />}
          {panel === "due" && <DueDayForm lease={lease} record={recordThenClose} />}
          {panel === "recurring" && <RecurringForm lease={lease} record={recordThenClose} />}
          {panel === "insurance" && <InsuranceForms lease={lease} record={recordThenClose} />}
          {panel === "deposit" && <DepositForm lease={lease} record={recordThenClose} />}
          {panel === "leasing" && <LeasingFeeForm lease={lease} record={recordThenClose} />}
          {panel === "hold" && <HoldForm lease={lease} record={recordThenClose} />}
          {panel === "release" && open !== null && <ReleaseForm hold={open} record={recordThenClose} />}
          {panel === "end" && <EndLeaseForm lease={lease} record={recordThenClose} />}
          {panel === "move" && <TransferForm lease={lease} record={recordThenClose} />}
          {panel === "interest" && <DepositInterestForm lease={lease} record={recordThenClose} />}
          {panel === "remove" && leaving !== null && (
            <RemoveTenantForm lease={lease} person={leaving} record={recordThenClose} />
          )}
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

// Summary: the term and rent (and its changes), what is held for the tenants, what is still
// open, the monthly charges (each stopped from its ⋯ menu), housing assistance and renters
// insurance.
function Summary({ lease, record, recordDeposit }: { lease: LeaseDetail; record: Recorder; recordDeposit: () => void }) {
  const deposit = lease.tenants.reduce((sum, t) => sum + Number(t.deposit), 0).toFixed(2);
  const prepaid = lease.tenants.reduce((sum, t) => sum + Number(t.prepaid), 0).toFixed(2);
  const kinds = depositKindsLine(lease.tenants);
  return (
    <div className="flex flex-col gap-6">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-base md:grid-cols-5">
        <div><dt className="text-sm text-ink-muted">Term</dt><dd>{termLabel(lease)}</dd></div>
        <div><dt className="text-sm text-ink-muted">Rent</dt><dd>{rentLabel(lease.monthly_rent)}</dd></div>
        <div><dt className="text-sm text-ink-muted">Rent due</dt><dd data-testid="rent-due-day">{dueDayLabel(lease.rent_due_day)}</dd></div>
        <div><dt className="text-sm text-ink-muted">Deposit held</dt><dd data-testid="deposit-held">{money(deposit)}</dd>
          {kinds && <dd className="text-sm text-ink-muted" data-testid="deposit-kinds">{kinds}</dd>}</div>
        <div><dt className="text-sm text-ink-muted">Prepaid rent</dt><dd data-testid="prepaid">{money(prepaid)}</dd></div>
      </dl>
      {lease.deposit_asked && (
        <Card title="Security deposit asked">
          <p className="flex flex-wrap items-center gap-2 text-base" data-testid="deposit-asked">
            <span>{depositAskedLine(lease.deposit_asked)}</span>
            {Number(lease.deposit_asked.to_receive) > 0
              ? <Badge tone="caution">To receive</Badge>
              : <Badge tone="positive">Held</Badge>}
          </p>
          {Number(lease.deposit_asked.to_receive) > 0 && (
            <div className="mt-3"><Button variant="secondary" onClick={recordDeposit}>Record deposit</Button></div>
          )}
        </Card>
      )}
      {lease.rent_changes.length > 0 && (
        <Card title="Rent changes">
          <ul className="text-base" aria-label={`Rent changes, ${lease.unit}`}>
            {lease.rent_changes.map((c) => <li key={c.id}>{rentChangeLine(c)}</li>)}
          </ul>
        </Card>
      )}
      {lease.due_days.length > 0 && (
        <Card title="Rent due day">
          <ul className="text-base" aria-label={`Rent due day, ${lease.unit}`}>
            {lease.due_days.map((d) => <li key={d.id}>{dueDayLine(d)}</li>)}
          </ul>
        </Card>
      )}
      {lease.open_charges.length > 0 && (
        <Card title="Open charges">
          <ul className="flex flex-col divide-y divide-line text-base text-ink-muted" aria-label="Open charges">
            {lease.open_charges.map((c) => <OpenChargeRow key={c.id} charge={c} record={record} />)}
          </ul>
        </Card>
      )}
      <Card title="Monthly charges">
        {lease.recurring.length === 0 ? (
          <p className="text-sm text-ink-subtle">None.</p>
        ) : (
          <ul className="text-base">
            {lease.recurring.map((r) => <RecurringLine key={r.id} recurring={r} record={record} />)}
          </ul>
        )}
      </Card>
      <LeaseAutopayCard leaseId={lease.id} />
      <AssistanceCard leaseId={lease.id} unit={lease.unit} />
      <TaxCreditLeaseCard leaseId={lease.id} />
      <Card title="Renters insurance">
        {lease.renters_insurance ? (
          <p className="flex flex-wrap items-center gap-2 text-base" data-testid="renters-insurance">
            <span>{insuranceLine(lease.renters_insurance)}</span>
            <Badge tone={insuranceBadge(lease.renters_insurance).tone}>{insuranceBadge(lease.renters_insurance).word}</Badge>
          </p>
        ) : (
          <p className="text-sm text-ink-subtle">Not asked for.</p>
        )}
      </Card>
    </div>
  );
}

// Ledger: each charge, payment and bounce with the balance after it (the lease ledger report's
// own lines), then each payment as it came in, whole, where It bounced is.
function LedgerTab({ lease, ledger, record }: { lease: LeaseDetail; ledger: Ledger | null; record: Recorder }) {
  if (ledger === null) return <Notice tone="info">Loading</Notice>;
  const today = day(new Date());
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Download the ledger">
        <span className="text-sm text-ink-muted">Download the ledger:</span>
        {FORMATS.map(([format, label]) => (
          <LinkButton key={format} href={downloadHref("tenant_ledger", [lease.id, today], format)}>{label}</LinkButton>
        ))}
      </div>
      {ledger.lines.length === 0 ? (
        <p className="text-sm text-ink-subtle">Nothing charged or paid yet.</p>
      ) : (
        <Wide>
          <table className="w-full text-base" aria-label="Ledger">
            <thead>
              <tr>
                <th className="text-left">Date</th>
                <th className="text-left">Type</th>
                <th className="text-left">Memo</th>
                <th className="text-right">Charges</th>
                <th className="text-right">Payments</th>
                <th className="text-right">Balance</th>
              </tr>
            </thead>
            <tbody>
              {ledger.lines.map((line, n) => (
                <tr key={n}>
                  <td className="whitespace-nowrap">{usDate(line.on)}</td>
                  <td>
                    {LEDGER_KIND[line.kind]}
                    {line.bounced && <> <Badge tone="danger">Bounced</Badge></>}
                  </td>
                  <td className="text-ink-muted">{line.memo ?? ""}</td>
                  <td className="text-right">{line.charged ? money(line.charged) : ""}</td>
                  <td className="text-right">{line.paid ? money(line.paid) : ""}</td>
                  <td className="text-right">{money(line.balance)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
      <Card title="Payments received">
        {lease.payments.length === 0 ? (
          <p className="text-sm text-ink-subtle">No payment yet.</p>
        ) : (
          <table className="w-full text-base" aria-label="Payments received">
            <thead>
              <tr>
                <th className="text-left">Received</th>
                <th className="text-right">Amount</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {lease.payments.map((p) => <PaymentRow key={p.transfer_id} payment={p} record={record} />)}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

// Tenants: each one's page, phone, email and whether they can sign in to the portal; an invite
// (or a new one, which resets their password) gives a link to hand them. The PMS sends it nowhere.
// A tenant leaving while others stay is taken off from their ⋯ menu, and marked once they left.
function TenantsTab({ people, refresh, onRemove }: {
  people: LeasePerson[];
  refresh: () => void;
  onRemove: (person: LeasePerson) => void;
}) {
  const staying = people.filter((p) => p.left_on === null).length;
  const [links, setLinks] = useState<Record<string, string>>({});
  const [emails, setEmails] = useState<Record<string, string>>({});
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");

  async function invite(person: LeasePerson) {
    setError("");
    const email = (emails[person.id] ?? person.login_email ?? person.email ?? "").trim();
    try {
      const link = await api.post<{ url: string }>("/api/invites", { kind: "tenant", person_id: person.id, email });
      setLinks({ ...links, [person.id]: link.url });
      setSaid(`Invite link made for ${email}; give it to ${person.name}.`);
      refresh();
    } catch (failed) {
      setError(detail(failed));
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <Wide>
        <table className="w-full text-base" aria-label="Tenants">
          <thead>
            <tr>
              <th className="text-left">Tenant</th>
              <th className="text-left">Phone</th>
              <th className="text-left">Email</th>
              <th className="text-left">Portal</th>
              <th><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {people.map((person) => (
              <tr key={person.id} aria-label={person.name}>
                <td>
                  <GoLink href={`/tenants/${person.id}`} className={LINK}>{person.name}</GoLink>
                  {person.left_on && <> <Badge tone="neutral">{`Left ${usDate(person.left_on)}`}</Badge></>}
                </td>
                <td>{person.phone ?? ""}</td>
                <td>{person.email ?? ""}</td>
                <td><Badge tone={PORTAL[person.portal].tone}>{PORTAL[person.portal].word}</Badge></td>
                <td className="text-right">
                  {person.portal !== "disabled" && (
                    <Form className="flex flex-wrap items-end justify-end gap-2"
                      onSubmit={(event) => { event.preventDefault(); return invite(person); }}>
                      {!person.login_email && !person.email && (
                        <Field label={`Email, ${person.name}`} id={`invite-${person.id}`} type="email" required
                          value={emails[person.id] ?? ""}
                          onChange={(event) => setEmails({ ...emails, [person.id]: event.target.value })} />
                      )}
                      <Button type="submit" size="sm" variant="secondary">
                        {person.portal === "no login" ? "Invite" : "Resend invite"}
                      </Button>
                    </Form>
                  )}
                  {person.left_on === null && staying > 1 && (
                    <RowMenu label={person.name} actions={[
                      { label: "Take off the lease", onSelect: () => onRemove(person) },
                    ]} />
                  )}
                  {links[person.id] && (
                    <input readOnly aria-label={`Invite link, ${person.name}`} value={links[person.id]}
                      className="mt-2 h-8 w-full rounded-sm border border-line-strong bg-sunken px-2 font-mono text-sm break-all text-ink" />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Wide>
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </div>
  );
}
