import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { day, money } from "../money";
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
import type { Portfolio } from "../setup";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

// Staff's work on owners' money: what each owner's property account holds and may pay out,
// owner draws and contributions as the bank recorded them, the management and leasing fees the
// agreements take, and those fees taken out of trust to the company. The PMS sends no money
// itself.
export function OwnersPage({ me }: { me: Me }) {
  const [owners, setOwners] = useState<OwnerMoney[] | null>(null);
  const [companyFees, setCompanyFees] = useState<CompanyFees[] | null>(null);
  const [books, setBooks] = useState<Portfolio | null>(null);
  const [error, setError] = useState("");
  const failed = (why: unknown) => setError(why instanceof ApiError ? why.detail : String(why));

  function refresh() {
    api.get<OwnerMoney[]>("/api/payables/owners").then(setOwners).catch(failed);
    api.get<CompanyFees[]>("/api/payables/company-fees").then(setCompanyFees).catch(failed);
  }
  useEffect(refresh, []);
  useEffect(() => {
    api.get<Portfolio>("/api/setup").then(setBooks).catch(failed);
  }, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Owners" />
      <Card title="Owners' money">
        {owners === null ? (
          <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
        ) : owners.length === 0 ? (
          <Notice tone="info">No owner has a property account yet. Add one in Setup.</Notice>
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {owners.map((owner) => (
              <OwnerRow key={owner.account} owner={owner} refresh={refresh} />
            ))}
          </ul>
        )}
      </Card>
      <FeesCard refresh={refresh} />
      {companyFees !== null && <CompanyFeesCard fees={companyFees} refresh={refresh} />}
      {books && <LeasingFeeCard books={books} refresh={refresh} />}
    </Shell>
  );
}

function OwnerRow({ owner, refresh }: { owner: OwnerMoney; refresh: () => void }) {
  const { said, error, record } = useRecord(refresh);
  return (
    <li className="flex flex-col gap-2 py-4" aria-label={`${owner.owner}, ${owner.property}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p>
          <span className="font-medium">{owner.owner}, {owner.property}</span>
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
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </li>
  );
}

function DrawForm({ owner, record }: { owner: OwnerMoney; record: Recorder }) {
  const [id, setId] = useState(requestId);
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(day(new Date()));
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={async (event) => {
        event.preventDefault();
        const body = { request_id: id, account: owner.account, amount: amount || null, on };
        const said = (taken: { amount: string }) => `${money(taken.amount)} paid out to ${owner.owner}.`;
        if (await record("/api/payables/draws", body, said)) {
          setId(requestId());
          setAmount("");
        }
      }}
    >
      <Field label="Draw amount (leave empty for all available)" id={`draw-${owner.account}`}
        inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
      <Field label="Paid out on" id={`draw-on-${owner.account}`} type="date" required value={on}
        onChange={(e) => setOn(e.target.value)} />
      <Button type="submit">Record draw</Button>
    </form>
  );
}

function ContributionForm({ owner, record }: { owner: OwnerMoney; record: Recorder }) {
  const [id, setId] = useState(requestId);
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(day(new Date()));
  const [memo, setMemo] = useState("");
  return (
    <form
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
    </form>
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
      <p className="mb-4 text-sm text-ink-muted">
        Each agreement's fee for the period, from the rent and recurring fees collected in it, once
        per period.
      </p>
      <form
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
      </form>
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
      <p className="mb-4 text-sm text-ink-muted">
        Fees the company has earned are its own money: take them out of trust, by 25 days after
        the oldest came in, so they don't mix with the money held for owners and tenants.
      </p>
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
  return (
    <li className="flex flex-col gap-2 py-4" aria-label={fees.bank_account}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-medium">{fees.bank_account}</p>
        {due && <Badge tone={due.late ? "danger" : "caution"}>{due.word}</Badge>}
      </div>
      <p className="text-sm text-ink-subtle" data-testid="company-fees">{companyFeesLabel(fees)}</p>
      {fees.since !== null && (
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={async (event) => {
            event.preventDefault();
            const body = { request_id: id, bank_account_id: fees.bank_account_id, amount: amount || null, on };
            const taken = (out: { amount: string }) => `${money(out.amount)} of the company's fees taken out.`;
            if (await record("/api/payables/company-fees", body, taken)) {
              setId(requestId());
              setAmount("");
            }
          }}
        >
          <Field label="Amount (leave empty for all)" id={`company-fees-${fees.bank_account_id}`}
            inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
          <Field label="Paid out on" id={`company-fees-on-${fees.bank_account_id}`} type="date"
            required value={on} onChange={(e) => setOn(e.target.value)} />
          <Button type="submit">Take out company fees</Button>
        </form>
      )}
      <Notice tone="info">{said}</Notice>
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
      u.leases.map((l) => ({ id: l.id, property: p.id, label: `${p.name}, ${u.name} from ${l.starts_on}` })),
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
      <form
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
      </form>
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}
