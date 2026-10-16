import { Users } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { ContactCard } from "../components/ContactCard";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { RecordActions } from "../components/RecordActions";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import type { Owner, OwnerRow as OwnerLine } from "../directory";
import { day, money, usDate } from "../money";
import { Badge } from "../components/Badge";
import type { CompanyFees, Fee, OwnerMoney } from "../payables";
import {
  companyFeesDue,
  companyFeesLabel,
  feeLine,
  heldLabel,
  incomeStatementHref,
  lastMonthPeriod,
} from "../payables";
import { requestId } from "../rent";
import type { Paged } from "../rentals";
import type { Portfolio } from "../setup";
import { GoLink } from "./NeedsAttention";
import { FilesCard } from "./FilesCard";
import { STAFF_NAV } from "./staffNav";
import { ListBar } from "../components/ListBar";
import { scopeParams, useScope } from "../scope";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";
import { Said } from "../components/Said";
import { Form } from "../components/Form";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// Rentals → Owners: the company's owners a page at a time, found by their name or a property's,
// each opening their own page (ADR 0005); then the management fees the agreements take, the
// company's fees taken out of trust, and a lease's leasing fee. The PMS sends no money itself.
export function OwnersPage({ me }: { me: Me }) {
  const [page, setPage] = useState<Paged<OwnerLine> | null>(null);
  const [scope, setScope] = useScope();
  const [archived, setArchived] = useState(false);
  const [number, setNumber] = useState(1);
  const [companyFees, setCompanyFees] = useState<CompanyFees[] | null>(null);
  const [books, setBooks] = useState<Portfolio | null>(null);
  const [error, setError] = useState("");
  const [runs, setRuns] = useState(0);
  const failed = (why: unknown) => setError(why instanceof ApiError ? why.detail : String(why));

  useEffect(() => {
    const query = new URLSearchParams({ page: String(number), archived: String(archived), ...scopeParams(scope) });
    api.get<Paged<OwnerLine>>(`/api/owners?${query}`).then(setPage).catch(failed);
  }, [scope, archived, number, runs]);
  useEffect(() => {
    api.get<CompanyFees[]>("/api/payables/company-fees").then(setCompanyFees).catch(failed);
  }, [runs]);
  useEffect(() => {
    api.get<Portfolio>("/api/setup").then(setBooks).catch(failed);
  }, []);
  const refresh = () => setRuns((n) => n + 1);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Owners" />
      <ListBar scope={scope} onScope={(next) => { setScope(next); setNumber(1); }}
        archived={archived} onArchived={(next) => { setArchived(next); setNumber(1); }} />
      <Notice tone="error">{error}</Notice>
      {page === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : page.items.length === 0 ? (
        <EmptyState icon={Users} title={archived ? "No archived owners." : "No owners found."}
          body="Add an owner from Settings, Company setup." />
      ) : (
        <>
          <Wide>
            <table className="w-full text-base">
              <thead>
                <tr>
                  <th className="text-left">Owner</th>
                  <th className="text-left">Properties</th>
                  <th className="text-right">Holds</th>
                  <th className="text-left">Email</th>
                  <th className="text-left">Phone</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((row) => (
                  <tr key={row.id} aria-label={row.name}>
                    <td><GoLink href={`/owners/${row.id}`} className={LINK}>{row.name}</GoLink></td>
                    <td className="text-ink-muted">{row.properties.join(", ")}</td>
                    <td className="text-right">{money(row.held)}</td>
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
      <FeesCard refresh={refresh} />
      {companyFees !== null && <CompanyFeesCard fees={companyFees} refresh={refresh} />}
      {books && <LeasingFeeCard books={books} refresh={refresh} />}
    </Shell>
  );
}

// An owner: their contact details, and each of their property accounts with what it holds and
// may pay out, its draws and contributions as the bank recorded them, and its income statement.
export function OwnerPage({ me, id }: { me: Me; id: string }) {
  const [owner, setOwner] = useState<Owner | null>(null);
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");

  function refresh() {
    api
      .get<Owner>(`/api/owners/${id}`)
      .then(setOwner)
      .catch((why) => setError(why instanceof ApiError ? why.detail : String(why)));
  }
  useEffect(refresh, [id]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      {owner === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <PageHeader title={owner.name} actions={
            <RecordActions path={`/api/owners/${id}`} name={owner.name} archived={owner.archived}
              onChange={(done) => { setSaid(done); refresh(); }} />
          } />
          <Said>{said}</Said>
          <ContactCard path={`/api/owners/${id}`} contact={owner.contact} refresh={refresh} name={owner.name} />
          <Card title="Properties">
            {owner.accounts.length === 0 ? (
              <EmptyState icon={Users} title="No property account yet."
                body="Open one from Settings, Company setup." />
            ) : (
              <ul className="flex flex-col divide-y divide-line">
                {owner.accounts.map((account) => (
                  <OwnerRow key={account.account} owner={account} refresh={refresh} />
                ))}
              </ul>
            )}
          </Card>
          <FilesCard kind="owner_document" aboutId={id} name={owner.name} />
        </>
      )}
    </Shell>
  );
}

function OwnerRow({ owner, refresh }: { owner: OwnerMoney; refresh: () => void }) {
  const { said, error, record } = useRecord(refresh);
  return (
    <li className="flex flex-col gap-2 py-4" aria-label={`${owner.owner}, ${owner.property}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p>
          <GoLink href={`/properties/${owner.property_id}`} className={LINK}>{owner.property}</GoLink>
          <span className="text-ink-muted"> · {owner.bank_account}</span>
        </p>
        <p data-testid="available">
          <span className="font-medium">{money(owner.available)}</span>{" "}
          <span className="text-ink-muted">available</span>
        </p>
      </div>
      <p className="text-sm text-ink-subtle">{heldLabel(owner)}</p>
      {!owner.has_agreement && (
        <p className="text-sm text-ink-subtle">
          No management agreement: no reserve is kept and no fee is taken.
        </p>
      )}
      <div>
        <LinkButton href={incomeStatementHref(owner.owner_id, day(new Date()))}>
          {owner.owner}'s income statement this year (PDF)
        </LinkButton>
      </div>
      <details>
        <summary>
          Record for {owner.owner}, {owner.property}
        </summary>
        <div className="mt-3 flex flex-col gap-4">
          <DrawForm owner={owner} record={record} />
          <ContributionForm owner={owner} record={record} />
        </div>
      </details>
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </li>
  );
}

export function DrawForm({ owner, record }: { owner: OwnerMoney; record: Recorder }) {
  const [id, setId] = useState(requestId);
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(day(new Date()));
  const { ask, prompt } = useConfirm();
  return (
    <>
      <Form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          const body = { request_id: id, account: owner.account, amount: amount || null, on };
          const said = (taken: { amount: string }) => `${money(taken.amount)} paid out to ${owner.owner}.`;
          const paying = amount ? money(amount) : `all ${money(owner.available)} available`;
          ask(`Pay ${paying} out to ${owner.owner} from ${owner.bank_account} on ${usDate(on)}?`, async () => {
            if (await record("/api/payables/draws", body, said)) {
              setId(requestId());
              setAmount("");
            }
          });
        }}
      >
        <Field label="Draw amount (leave empty for all available)" id={`draw-${owner.account}`}
          inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
        <Field label="Paid out on" id={`draw-on-${owner.account}`} type="date" required value={on}
          onChange={(e) => setOn(e.target.value)} />
        <Button type="submit">Record draw</Button>
      </Form>
      {prompt}
    </>
  );
}

function ContributionForm({ owner, record }: { owner: OwnerMoney; record: Recorder }) {
  const [id, setId] = useState(requestId);
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(day(new Date()));
  const [memo, setMemo] = useState("");
  return (
    <Form
      className="flex flex-wrap items-end gap-3"
      onSubmit={async (event) => {
        event.preventDefault();
        const body = { request_id: id, account: owner.account, amount, on, memo: memo || null };
        if (await record("/api/payables/contributions", body,
          () => `${money(amount)} from ${owner.owner} recorded.`)) {
          setId(requestId());
          setAmount("");
          setMemo("");
        }
      }}
    >
      <Field label="Contribution received" id={`contribution-${owner.account}`} inputMode="decimal"
        required value={amount} onChange={(e) => setAmount(e.target.value)} />
      <Field label="Contribution received on" id={`contribution-on-${owner.account}`} type="date"
        required value={on} onChange={(e) => setOn(e.target.value)} />
      <Field label="For" id={`contribution-memo-${owner.account}`} maxLength={200} value={memo}
        onChange={(e) => setMemo(e.target.value)} />
      <Button type="submit" variant="secondary">Record contribution</Button>
    </Form>
  );
}

function FeesCard({ refresh }: { refresh: () => void }) {
  const { error, record } = useRecord(refresh);
  const month = lastMonthPeriod(new Date());
  const [start, setStart] = useState(month.start);
  const [end, setEnd] = useState(month.end);
  const [on, setOn] = useState(day(new Date()));
  const [fees, setFees] = useState<Fee[] | null>(null);
  return (
    <Card title="Management fees">
      <Form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          setFees(null);
          record<Fee[]>("/api/payables/management-fees",
            { period_start: start, period_end: end, on },
            (taken) => {
              setFees(taken);
              return "";
            });
        }}
      >
        <Field label="Period from" id="fee-start" type="date" required value={start}
          onChange={(e) => setStart(e.target.value)} />
        <Field label="Up to (not including)" id="fee-end" type="date" required value={end}
          onChange={(e) => setEnd(e.target.value)} />
        <Field label="Taken on" id="fee-on" type="date" required value={on}
          onChange={(e) => setOn(e.target.value)} />
        <Button type="submit" variant="secondary">Take fees</Button>
      </Form>
      {fees !== null && (
        <ul className="mt-4 flex flex-col gap-1 text-sm" aria-label="Fees taken">
          {fees.length === 0 ? (
            <li>No agreement was in force in that period.</li>
          ) : (
            fees.map((fee) => <li key={fee.account}>{feeLine(fee)}</li>)
          )}
        </ul>
      )}
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}

// The company's earned fees still in each operating trust bank account, and taking them out to
// the company's own account. Left in trust they mix the company's money with its clients'.
function CompanyFeesCard({ fees, refresh }: { fees: CompanyFees[]; refresh: () => void }) {
  const today = day(new Date());
  return (
    <Card title="Company fees in trust">
      {fees.length === 0 ? (
        <Notice tone="info">No operating trust bank account yet. Add one in Setup.</Notice>
      ) : (
        <ul className="flex flex-col divide-y divide-line">
          {fees.map((held) => (
            <CompanyFeesRow key={held.bank_account_id} fees={held} today={today} refresh={refresh} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function CompanyFeesRow({ fees, today, refresh }: { fees: CompanyFees; today: string; refresh: () => void }) {
  const { said, error, record } = useRecord(refresh);
  const [id, setId] = useState(requestId);
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(today);
  const due = companyFeesDue(fees, today);
  const { ask, prompt } = useConfirm();
  return (
    <li className="flex flex-col gap-2 py-4" aria-label={fees.bank_account}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-medium">{fees.bank_account}</p>
        {due && <Badge tone={due.late ? "danger" : "caution"}>{due.word}</Badge>}
      </div>
      <p className="text-sm text-ink-subtle" data-testid="company-fees">{companyFeesLabel(fees)}</p>
      {fees.since !== null && (
        <Form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            const body = { request_id: id, bank_account_id: fees.bank_account_id, amount: amount || null, on };
            const taken = (out: { amount: string }) => `${money(out.amount)} of the company's fees taken out.`;
            const paying = amount ? money(amount) : `all ${money(fees.held)}`;
            ask(`Take ${paying} of the company's fees out of ${fees.bank_account} on ${usDate(on)}?`, async () => {
              if (await record("/api/payables/company-fees", body, taken)) {
                setId(requestId());
                setAmount("");
              }
            });
          }}
        >
          <Field label="Amount (leave empty for all)" id={`company-fees-${fees.bank_account_id}`}
            inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
          <Field label="Paid out on" id={`company-fees-on-${fees.bank_account_id}`} type="date"
            required value={on} onChange={(e) => setOn(e.target.value)} />
          <Button type="submit">Take out company fees</Button>
        </Form>
      )}
      {prompt}
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </li>
  );
}

// What taking a lease's leasing fee did. An agreement that takes no leasing fee charges none:
// the lease's one leasing fee is then $0.00, not a fee to take later.
export function leasingFeeSaid(amount: string): string {
  return Number(amount) > 0
    ? `A leasing fee of ${money(amount)} is taken.`
    : "No leasing fee: the owner's agreement in force takes none, so none is due for this lease.";
}

function LeasingFeeCard({ books, refresh }: { books: Portfolio; refresh: () => void }) {
  const { said, error, record } = useRecord(refresh);
  const leases = books.properties.flatMap((p) =>
    p.units.flatMap((u) =>
      u.leases.map((l) => ({ id: l.id, property: p.id, label: `${p.name}, ${u.name} from ${usDate(l.starts_on)}` })),
    ),
  );
  const [lease, setLease] = useState(leases[0]?.id ?? "");
  const property = leases.find((l) => l.id === lease)?.property;
  const accounts = books.accounts.filter((a) => a.property_id === property);
  const [account, setAccount] = useState("");
  const chosen = accounts.some((a) => a.account === account) ? account : (accounts[0]?.account ?? "");
  const [on, setOn] = useState(day(new Date()));
  if (leases.length === 0) return null;
  return (
    <Card title="Leasing fee">
      <Form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          record<{ amount: string }>("/api/payables/leasing-fees",
            { lease_id: lease, account: chosen, on },
            (taken) => leasingFeeSaid(taken.amount));
        }}
      >
        <Select label="Lease" id="leasing-lease" value={lease} onChange={(e) => setLease(e.target.value)}>
          {leases.map((l) => (
            <option key={l.id} value={l.id}>
              {l.label}
            </option>
          ))}
        </Select>
        <Select label="From owner" id="leasing-account" value={chosen}
          onChange={(e) => setAccount(e.target.value)}>
          {accounts.map((a) => (
            <option key={a.account} value={a.account}>
              {a.owner}
            </option>
          ))}
        </Select>
        <Field label="Taken on" id="leasing-on" type="date" required value={on}
          onChange={(e) => setOn(e.target.value)} />
        <Button type="submit" variant="secondary">Take leasing fee</Button>
      </Form>
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}
