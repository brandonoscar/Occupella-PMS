import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { NotesCard } from "../components/NotesCard";
import { Notice } from "../components/Notice";
import type { RowAction } from "../components/RowMenu";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Tabs } from "../components/Tabs";
import { Wide } from "../components/Wide";
import { money, usDate } from "../money";
import { chargeLabel, insuranceBadge, insuranceLine, rentChangeLine } from "../rent";
import type { LeaseDetail, LeasePerson, Ledger } from "../rentals";
import { LEASE_STATUS, LEDGER_KIND, PORTAL, rentLabel, termLabel } from "../rentals";
import { FilesCard } from "./FilesCard";
import { InsuranceForms } from "./InsuranceForms";
import { LeasingFeeForm } from "./LeasingFeeForm";
import type { Recorder } from "./LeaseForms";
import {
  ChargeForm,
  DepositForm,
  PaymentForm,
  PaymentRow,
  RecurringForm,
  RecurringLine,
  RentChangeForm,
} from "./LeaseForms";
import { navigate } from "../router";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

type Panel = "payment" | "charge" | "rent" | "recurring" | "insurance" | "deposit" | "leasing";
const PANELS: Record<Panel, string> = {
  payment: "Receive payment",
  charge: "Enter charge",
  rent: "Change rent",
  recurring: "Add recurring charge",
  insurance: "Renters insurance",
  deposit: "Record deposit",
  leasing: "Take leasing fee",
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
  const { said, error: refused, record } = useRecord(refresh);

  function refresh() {
    api.get<LeaseDetail>(`/api/leases/${id}`).then(setLease).catch((failed) => setError(detail(failed)));
    api.get<Ledger>(`/api/leases/${id}/ledger`).then(setLedger).catch((failed) => setError(detail(failed)));
  }
  useEffect(refresh, [id]);

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
  const owes = Number(lease.owed) > 0;
  const actions: RowAction[] = [
    { label: "Change rent", onSelect: () => setPanel("rent") },
    { label: "Add recurring charge", onSelect: () => setPanel("recurring") },
    { label: "Renters insurance", onSelect: () => setPanel("insurance") },
    { label: "Record deposit", onSelect: () => setPanel("deposit") },
    { label: "Take leasing fee", onSelect: () => setPanel("leasing") },
    { label: "Record NSF", onSelect: () => setTab("ledger") },
    { label: "Renew lease", onSelect: () => navigate(`/draft-leases?lease=${id}`) },
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
            {money(lease.owed)}
          </span>
        </p>
      </div>
      {lease.on_hold && <Notice tone="error">On a payment hold: payments are refused unless the hold allows them.</Notice>}
      <Said>{said}</Said>
      {/* A side panel's refusal shows in the panel, once. */}
      {panel === null && <Notice tone="error">{refused}</Notice>}
      <div>
        <Tabs label="Lease" tabs={TABS} value={tab} onChange={setTab} />
        {tab === "summary" && <Summary lease={lease} record={record} />}
        {tab === "ledger" && <LedgerTab lease={lease} ledger={ledger} record={record} />}
        {tab === "tenants" && <TenantsTab people={lease.people} refresh={refresh} />}
        {tab === "files" && <FilesCard kind="move_out_photo" also={["lease_document"]} aboutId={id} name={`${lease.unit}, ${lease.property}`} />}
        {tab === "notes" && <NotesCard about="lease" aboutId={id} name={`${lease.unit}, ${lease.property}`} />}
      </div>
      {panel !== null && (
        <SidePanel title={PANELS[panel]} onClose={() => setPanel(null)}>
          {panel === "payment" && <PaymentForm lease={lease} record={recordThenClose} />}
          {panel === "charge" && <ChargeForm lease={lease} record={recordThenClose} />}
          {panel === "rent" && <RentChangeForm lease={lease} record={recordThenClose} />}
          {panel === "recurring" && <RecurringForm lease={lease} record={recordThenClose} />}
          {panel === "insurance" && <InsuranceForms lease={lease} record={recordThenClose} />}
          {panel === "deposit" && <DepositForm lease={lease} record={recordThenClose} />}
          {panel === "leasing" && <LeasingFeeForm lease={lease} record={recordThenClose} />}
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

// Summary: the term and rent (and its changes), what is held for the tenants, what is still
// open, the monthly charges (each stopped from its ⋯ menu) and renters insurance.
function Summary({ lease, record }: { lease: LeaseDetail; record: Recorder }) {
  const deposit = lease.tenants.reduce((sum, t) => sum + Number(t.deposit), 0).toFixed(2);
  const prepaid = lease.tenants.reduce((sum, t) => sum + Number(t.prepaid), 0).toFixed(2);
  return (
    <div className="flex flex-col gap-6">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-base md:grid-cols-4">
        <div><dt className="text-sm text-ink-muted">Term</dt><dd>{termLabel(lease)}</dd></div>
        <div><dt className="text-sm text-ink-muted">Rent</dt><dd>{rentLabel(lease.monthly_rent)}</dd></div>
        <div><dt className="text-sm text-ink-muted">Deposit held</dt><dd data-testid="deposit-held">{money(deposit)}</dd></div>
        <div><dt className="text-sm text-ink-muted">Prepaid rent</dt><dd data-testid="prepaid">{money(prepaid)}</dd></div>
      </dl>
      {lease.rent_changes.length > 0 && (
        <Card title="Rent changes">
          <ul className="text-base" aria-label={`Rent changes, ${lease.unit}`}>
            {lease.rent_changes.map((c) => <li key={c.id}>{rentChangeLine(c)}</li>)}
          </ul>
        </Card>
      )}
      {lease.open_charges.length > 0 && (
        <Card title="Open charges">
          <ul className="text-base text-ink-muted">
            {lease.open_charges.map((c) => <li key={c.id}>{chargeLabel(c)}</li>)}
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
  return (
    <div className="flex flex-col gap-6">
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
                <th />
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
function TenantsTab({ people, refresh }: { people: LeasePerson[]; refresh: () => void }) {
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
              <th />
            </tr>
          </thead>
          <tbody>
            {people.map((person) => (
              <tr key={person.id} aria-label={person.name}>
                <td><GoLink href={`/tenants/${person.id}`} className={LINK}>{person.name}</GoLink></td>
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
