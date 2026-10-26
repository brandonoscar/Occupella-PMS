import { Users } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { ContactCard } from "../components/ContactCard";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { ListBar } from "../components/ListBar";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { RecordActions } from "../components/RecordActions";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { StatementView } from "../components/StatementView";
import { Tabs } from "../components/Tabs";
import { Wide } from "../components/Wide";
import type { Agreement, Owner, OwnerRow } from "../directory";
import type { Keep } from "../latest";
import { keepAll, latest } from "../latest";
import { day, money, usDate } from "../money";
import type { OwnerFeeKind, OwnerMoney } from "../payables";
import { OWNER_FEE_KINDS, incomeStatementHref, ownerFeeSummary } from "../payables";
import { requestId } from "../rent";
import type { Paged } from "../rentals";
import { navigate } from "../router";
import { scopeParams, useScope } from "../scope";
import type { StatementData } from "../statement";
import { group } from "../statement";
import { EmailsCard } from "../components/EmailsCard";
import { FilesCard } from "./FilesCard";
import { PayeeCard } from "./PayeeCard";
import { TaxIdCard } from "./TaxIdCard";
import { OwnerTop } from "./OwnerTop";
import { PortalCard } from "./PortalCard";
import { TermsPanel } from "./SetupPanels";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";
import { HistoryCard } from "./HistoryCard";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// Rentals → Owners (ADR 0006): each owner once, a page at a time, with their properties and what
// their accounts hold, keep back and may pay out, the row opening their page. Pay owners pays
// them; Add owner adds one. Management fees, the company's fees and leasing fees are where they
// are used: Accounting, Banking and the lease.
export function OwnersPage({ me }: { me: Me }) {
  const [page, setPage] = useState<Paged<OwnerRow> | null>(null);
  const [scope, setScope] = useScope();
  const [archived, setArchived] = useState(false);
  const [number, setNumber] = useState(1);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    const run = latest();
    const query = new URLSearchParams({ page: String(number), archived: String(archived), ...scopeParams(scope) });
    api.get<Paged<OwnerRow>>(`/api/owners?${query}`).then(run.keep(setPage)).catch(run.keep((why) => setError(detail(why))));
    return run.stop;
  }, [scope, archived, number]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Owners" actions={
        <div className="flex flex-wrap items-center gap-2">
          <LinkButton href="/owner-draws">Pay owners</LinkButton>
          <Button variant="secondary" onClick={() => setAdding(true)}>Add owner</Button>
        </div>
      } />
      <ListBar scope={scope} onScope={(next) => { setScope(next); setNumber(1); }}
        archived={archived} onArchived={(next) => { setArchived(next); setNumber(1); }} />
      <Notice tone="error">{error}</Notice>
      {page === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : page.items.length === 0 ? (
        <EmptyState icon={Users} title={archived ? "No archived owners." : "No owners found."} />
      ) : (
        <>
          <Wide>
            <table className="w-full text-base">
              <thead>
                <tr>
                  <th className="text-left">Owner</th>
                  <th className="text-left">Properties</th>
                  <th className="text-right">Cash held</th>
                  <th className="text-right">Reserve</th>
                  <th className="text-right">Available</th>
                  <th className="text-left">Email</th>
                  <th className="text-left">Phone</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((row) => (
                  <tr key={row.id} aria-label={row.name} className="cursor-pointer hover:bg-raised"
                    onClick={() => navigate(`/owners/${row.id}`)}>
                    <td onClick={(event) => event.stopPropagation()}>
                      <GoLink href={`/owners/${row.id}`} className={LINK}>{row.name}</GoLink>
                    </td>
                    <td className="text-ink-muted">{row.properties.join(", ")}</td>
                    <td className="text-right">{money(row.held)}</td>
                    <td className="text-right">{money(row.reserve)}</td>
                    <td className="text-right font-medium">{money(row.available)}</td>
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
      {adding && (
        <SidePanel title="Add owner" onClose={() => setAdding(false)}>
          <AddOwner />
        </SidePanel>
      )}
    </Shell>
  );
}

// Add owner: their name; the owner's page then takes their contact details and properties.
function AddOwner() {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={async (event) => {
      event.preventDefault();
      setError("");
      try {
        const made = await api.post<{ id: string }>("/api/setup/owners", { name });
        navigate(`/owners/${made.id}`);
      } catch (failed) {
        setError(detail(failed));
      }
    }}>
      <Field label="Name" id="new-owner" required maxLength={100} value={name}
        onChange={(event) => setName(event.target.value)} />
      <div><Button type="submit" says="Adds them by name and opens their page. Nothing is sent to them.">Add owner</Button></div>
      <Notice tone="error">{error}</Notice>
    </Form>
  );
}

type OwnerTab = "summary" | "properties" | "financials" | "statements" | "email" | "files";
const OWNER_TABS: { value: OwnerTab; label: string }[] = [
  { value: "summary", label: "Summary" },
  { value: "properties", label: "Properties" },
  { value: "financials", label: "Financials" },
  { value: "statements", label: "Statements" },
  { value: "email", label: "Email" },
  { value: "files", label: "Files" },
];

// An owner (ADR 0006; ADR 0010 phase 3, part J): on top how to reach them, how they are paid,
// their tax ID's last four, their balance and the next draw; Summary (contact details, portal
// login in one click, the agreements' terms), Properties
// (what each account holds, keeps back and may pay out), Financials (their statement, and a
// contribution recorded), Statements (each month's, and the year's income statement) and Files.
export function OwnerPage({ me, id }: { me: Me; id: string }) {
  const [owner, setOwner] = useState<Owner | null>(null);
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");
  const [tab, setTab] = useState<OwnerTab>("summary");

  function refresh(keep: Keep = keepAll) {
    api.get<Owner>(`/api/owners/${id}`).then(keep(setOwner)).catch(keep((why) => setError(detail(why))));
  }
  useEffect(() => {
    const run = latest();
    refresh(run.keep);
    return run.stop;
  }, [id]);

  if (owner === null) {
    return (
      <Shell me={me} nav={STAFF_NAV}>
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      </Shell>
    );
  }
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={owner.name} actions={
        <RecordActions path={`/api/owners/${id}`} name={owner.name} archived={owner.archived}
          onChange={(done) => { setSaid(done); refresh(); }} />
      } />
      <OwnerTop owner={owner} today={day(new Date())} />
      <Said>{said}</Said>
      <div>
        <Tabs label="Owner" tabs={OWNER_TABS} value={tab} onChange={setTab} />
        {tab === "summary" && <OwnerSummary owner={owner} refresh={refresh} />}
        {tab === "properties" && <OwnerProperties accounts={owner.accounts} />}
        {tab === "financials" && <OwnerFinancials owner={owner} refresh={refresh} />}
        {tab === "statements" && (
          <div className="flex flex-col gap-6">
            <OwnerStatements owner={owner} />
            <HistoryCard about="owner" id={id} />
          </div>
        )}
        {tab === "files" && <FilesCard kind="owner_document" aboutId={id} name={owner.name} />}
        {tab === "email" && <EmailsCard about="owner" aboutId={id} name={owner.name} emailOn={me.email_on} />}
      </div>
    </Shell>
  );
}

function OwnerSummary({ owner, refresh }: { owner: Owner; refresh: () => void }) {
  const [setting, setSetting] = useState(false);
  const [said, setSaid] = useState("");
  const [editContact, setEditContact] = useState(0);
  const property = (account: string) => owner.accounts.find((a) => a.account === account)?.property ?? account;
  return (
    <div className="flex flex-col gap-6">
      <ContactCard path={`/api/owners/${owner.id}`} contact={owner.contact} refresh={refresh} name={owner.name}
        editRequest={editContact} />
      <PortalCard kind="owner" personId={owner.id} name={owner.name} portal={owner.portal}
        loginEmail={owner.login_email} contactEmail={owner.contact.email} refresh={refresh}
        onEditContact={() => setEditContact((n) => n + 1)} />
      <PayeeCard kind="owner" id={owner.id} name={owner.name} />
      <TaxIdCard kind="owner" id={owner.id} name={owner.name} />
      <Card title="Management agreements">
        {owner.agreements.length === 0 ? (
          <p className="text-sm text-ink-subtle">None: no reserve is kept and no fee is taken.</p>
        ) : (
          <ul className="flex flex-col gap-2 text-base" aria-label="Management agreements">
            {owner.agreements.map((terms) => (
              <li key={terms.account}>
                <span className="font-medium">{property(terms.account)}</span>: {agreementLine(terms)}
              </li>
            ))}
          </ul>
        )}
        {owner.accounts.length > 0 && (
          <div className="mt-3"><Button variant="secondary" onClick={() => setSetting(true)}>Set terms</Button></div>
        )}
      </Card>
      <Said>{said}</Said>
      {setting && (
        <TermsPanel panel={{ say: setSaid, refresh, close: () => setSetting(false) }}
          accounts={owner.accounts.map((a) => ({ account: a.account, label: `${owner.name}, ${a.property}` }))} />
      )}
    </div>
  );
}

// An agreement's terms in a line: "8% of rent collected, at least $25.00, plus $10.00; leasing
// fee 50%; reserve $100.00; the owner approves bills over $500.00; from 01/01/2026".
export function agreementLine(terms: Agreement): string {
  const fee = [`${terms.fee_percent}% of rent collected`];
  if (Number(terms.minimum_fee) > 0) fee.push(`at least ${money(terms.minimum_fee)}`);
  if (Number(terms.flat_fee) > 0) fee.push(`plus ${money(terms.flat_fee)}`);
  return [
    fee.join(", "),
    `leasing fee ${terms.leasing_fee_percent}%`,
    `reserve ${money(terms.reserve)}`,
    ...(Number(terms.bill_markup_percent) > 0 ? [`${terms.bill_markup_percent}% markup on bills`] : []),
    `the owner approves bills over ${money(terms.approval_limit)}`,
    `from ${usDate(terms.starts_on)}`,
  ].join("; ");
}

function OwnerProperties({ accounts }: { accounts: OwnerMoney[] }) {
  if (accounts.length === 0) {
    return <EmptyState icon={Users} title="No property account yet." body="Open one from a property's page, Add owner." />;
  }
  return (
    <Wide>
      <table className="w-full text-base" aria-label="Properties">
        <thead>
          <tr>
            <th className="text-left">Property</th>
            <th className="text-left">Trust account</th>
            <th className="text-right">Cash held</th>
            <th className="text-right">Reserve</th>
            <th className="text-right">Unpaid bills</th>
            <th className="text-right">Available</th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((account) => (
            <tr key={account.account} aria-label={account.property}>
              <td><GoLink href={`/properties/${account.property_id}`} className={LINK}>{account.property}</GoLink></td>
              <td className="text-ink-muted">{account.bank_account}</td>
              <td className="text-right">{money(account.balance)}</td>
              <td className="text-right">{money(account.reserve)}</td>
              <td className="text-right">{money(account.unpaid_bills)}</td>
              <td className="text-right font-medium">{money(account.available)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Wide>
  );
}

// Financials: the owner's statement over days (this year to today, at first), and in side panels
// Record contribution, money the owner put in, and Take a fee (an inspection, a lease-up, another;
// migration 0082), from the owner's account into the company's fee account.
function OwnerFinancials({ owner, refresh }: { owner: Owner; refresh: () => void }) {
  const today = day(new Date());
  const [start, setStart] = useState(`${today.slice(0, 4)}-01-01`);
  const [end, setEnd] = useState(today);
  const [statement, setStatement] = useState<StatementData | null>(null);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState<"contribution" | "fee" | null>(null);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => { setRuns((n) => n + 1); refresh(); });

  useEffect(() => {
    const run = latest();
    setError("");
    const query = new URLSearchParams({ start, end });
    api.get<StatementData>(`/api/owners/${owner.id}/statement?${query}`)
      .then(run.keep(setStatement))
      .catch(run.keep((why) => { setStatement(null); setError(detail(why)); }));
    return run.stop;
  }, [owner.id, start, end, runs]);

  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setAdding(null);
    return recorded;
  };
  const download = (format: string) =>
    `/api/owners/${owner.id}/statement/download?${new URLSearchParams({ start, end, format })}`;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="From" id="owner-start" type="date" required value={start}
            onChange={(event) => setStart(event.target.value)} />
          <Field label="To" id="owner-end" type="date" required value={end}
            onChange={(event) => setEnd(event.target.value)} />
          <LinkButton href={download("pdf")}>PDF</LinkButton>
          <LinkButton href={download("csv")}>CSV</LinkButton>
        </div>
        {owner.accounts.length > 0 && (
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setAdding("contribution")}>Record contribution</Button>
            <Button variant="secondary" onClick={() => setAdding("fee")}>Take a fee</Button>
          </div>
        )}
      </div>
      <Said>{said}</Said>
      {!adding && <Notice tone="error">{refused}</Notice>}
      <Notice tone="error">{error}</Notice>
      {statement && <StatementView grouped={group(statement)} />}
      {adding === "contribution" && (
        <SidePanel title="Record contribution" onClose={() => setAdding(null)}>
          <ContributionForm owner={owner} record={recordThenClose} />
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
      {adding === "fee" && (
        <SidePanel title="Take a fee" onClose={() => setAdding(null)}>
          <OwnerFeeForm owner={owner} record={recordThenClose} />
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </div>
  );
}

function ContributionForm({ owner, record }: { owner: Owner; record: Recorder }) {
  const [id, setId] = useState(requestId);
  const [account, setAccount] = useState(owner.accounts[0]?.account ?? "");
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(day(new Date()));
  const [memo, setMemo] = useState("");
  return (
    <Form
      className="flex flex-col gap-3"
      onSubmit={async (event) => {
        event.preventDefault();
        const body = { request_id: id, account, amount, on, memo: memo || null };
        if (await record("/api/payables/contributions", body, () => `${money(amount)} from ${owner.name} recorded.`)) {
          setId(requestId());
          setAmount("");
          setMemo("");
        }
      }}
    >
      <Select label="Into" id="contribution-account" value={account} onChange={(event) => setAccount(event.target.value)}>
        {owner.accounts.map((a) => <option key={a.account} value={a.account}>{a.property}</option>)}
      </Select>
      <Field label="Contribution received" id="contribution-amount" inputMode="decimal" required value={amount}
        onChange={(event) => setAmount(event.target.value)} />
      <Field label="Received on" id="contribution-on" type="date" required value={on}
        onChange={(event) => setOn(event.target.value)} />
      <Field label="For" id="contribution-memo" maxLength={200} value={memo}
        onChange={(event) => setMemo(event.target.value)} />
      <div>
        <Button type="submit" says="Adds it to the owner's balance, as money already received. The PMS collects nothing from them.">
          Record contribution
        </Button>
      </div>
    </Form>
  );
}

// A fee from the owner besides the management and leasing fees (q051): it moves from their
// account into the company's fee account, inside trust, once per request id; staff who hold
// disburse take it, and the owner's statement shows it with what it was for.
function OwnerFeeForm({ owner, record }: { owner: Owner; record: Recorder }) {
  const [id, setId] = useState(requestId);
  const [account, setAccount] = useState(owner.accounts[0]?.account ?? "");
  const [kind, setKind] = useState<OwnerFeeKind>("inspection");
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState("");
  const [on, setOn] = useState(day(new Date()));
  const property = owner.accounts.find((a) => a.account === account)?.property ?? "";
  return (
    <Form
      className="flex flex-col gap-3"
      onSubmit={async (event) => {
        event.preventDefault();
        const body = { request_id: id, account, kind, amount, memo, on };
        if (await record("/api/payables/owner-fees", body, () => ownerFeeSummary(kind, amount, property))) {
          setId(requestId());
          setAmount("");
          setMemo("");
        }
      }}
    >
      <Select label="From" id="owner-fee-account" value={account} onChange={(event) => setAccount(event.target.value)}>
        {owner.accounts.map((a) => <option key={a.account} value={a.account}>{a.property}</option>)}
      </Select>
      <Select label="Fee" id="owner-fee-kind" value={kind} onChange={(event) => setKind(event.target.value as OwnerFeeKind)}>
        {OWNER_FEE_KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </Select>
      <Field label="Amount" id="owner-fee-amount" inputMode="decimal" required value={amount}
        onChange={(event) => setAmount(event.target.value)} />
      <Field label="What it is for" id="owner-fee-memo" required maxLength={200} value={memo}
        onChange={(event) => setMemo(event.target.value)} />
      <Field label="Taken on" id="owner-fee-on" type="date" required value={on}
        onChange={(event) => setOn(event.target.value)} />
      <div>
        <Button type="submit" says="Moves it from the owner's balance to the company's fees and shows on their statement. Nothing leaves trust.">
          Take fee
        </Button>
      </div>
    </Form>
  );
}

// The last twelve months, newest first, each from its first day to its last (UTC).
export function lastTwelveMonths(today: Date): { label: string; start: string; end: string }[] {
  const months = [];
  for (let back = 1; back <= 12; back++) {
    const first = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - back, 1));
    const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0));
    const label = first.toLocaleString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
    months.push({ label, start: day(first), end: day(last) });
  }
  return months;
}

function OwnerStatements({ owner }: { owner: Owner }) {
  const today = new Date();
  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-1 text-base" aria-label="Monthly statements">
        {lastTwelveMonths(today).map((month) => (
          <li key={month.start}>
            <a className={LINK} href={`/api/owners/${owner.id}/statement/download?${new URLSearchParams({ start: month.start, end: month.end, format: "pdf" })}`}>
              {month.label} statement (PDF)
            </a>
          </li>
        ))}
      </ul>
      <div>
        <LinkButton href={incomeStatementHref(owner.id, day(today))}>Income statement this year (PDF)</LinkButton>
      </div>
    </div>
  );
}
