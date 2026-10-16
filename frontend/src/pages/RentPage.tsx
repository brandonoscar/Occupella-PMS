import { FileText } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Pager } from "../components/Pager";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import { day, money, usDate } from "../money";
import type { Bounce, Payment, RecurringCharge, RentChange, RentLease, RentRun } from "../rent";
import type { Paged, RentRollRow } from "../rentals";
import { termLabel } from "../rentals";
import { InsuranceForms } from "./InsuranceForms";
import { GoLink } from "./NeedsAttention";
import {
  earliestRentChange,
  insuranceBadge,
  insuranceLine,
  rentChangeLine,
  bounceSummary,
  chargeLabel,
  owedLabel,
  owedTone,
  paymentSummary,
  recurringLabel,
  rentRunSummary,
  requestId,
} from "../rent";
import { STAFF_NAV } from "./staffNav";
import { ListBar } from "../components/ListBar";
import { RowMenu } from "../components/RowMenu";
import { scopeParams, useScope } from "../scope";
import { Said } from "../components/Said";
import { Form } from "../components/Form";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// Rentals → Rent roll (ADR 0005): the month's rent run and late fees for every lease, then the
// leases a page at a time (in effect, owing, or all; found by tenant, unit or property), each
// opening its own page, where its rent work is recorded. The PMS records what reached the bank;
// it takes no payments itself.
export function RentPage({ me }: { me: Me }) {
  const [page, setPage] = useState<Paged<RentRollRow> | null>(null);
  const [scope, setScope] = useScope();
  const [show, setShow] = useState<"current" | "owing" | "all">("current");
  const [number, setNumber] = useState(1);
  const [error, setError] = useState("");
  // Counts the rent runs, so the list shows what a run charged.
  const [runs, setRuns] = useState(0);

  useEffect(() => {
    const query = new URLSearchParams({ page: String(number), show, ...scopeParams(scope) });
    api
      .get<Paged<RentRollRow>>(`/api/leases?${query}`)
      .then(setPage)
      .catch((failed) => setError(detail(failed)));
  }, [scope, show, number, runs]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Rent roll" />
      <ListBar scope={scope} onScope={(next) => { setScope(next); setNumber(1); }} />
      <RunsCard refresh={() => setRuns((n) => n + 1)} />
      <Card title="Leases">
        <div className="mb-4 flex flex-wrap items-end gap-4">
          <Select label="Show" id="show-leases" value={show}
            onChange={(event) => { setShow(event.target.value as typeof show); setNumber(1); }}>
            <option value="current">In effect today</option>
            <option value="owing">Owing</option>
            <option value="all">All</option>
          </Select>
        </div>
        {page === null ? (
          <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
        ) : page.items.length === 0 ? (
          <EmptyState icon={FileText} title={scope.group || scope.property || show !== "current" ? "No leases found." : "No lease is in effect today."} />
        ) : (
          <>
            <Wide>
              <table className="w-full text-base">
                <thead>
                  <tr>
                    <th className="text-left">Property</th>
                    <th className="text-left">Unit</th>
                    <th className="text-left">Tenants</th>
                    <th className="text-right">Rent</th>
                    <th className="text-left">Term</th>
                    <th className="text-right">Owes</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((row) => (
                    <tr key={row.id} aria-label={`${row.property.name}, ${row.unit.name}`}>
                      <td>{row.property.name}</td>
                      <td>
                        <GoLink href={`/leases/${row.id}`} className="font-medium text-iris hover:text-iris-hover hover:underline">
                          {row.unit.name}
                        </GoLink>
                      </td>
                      <td className="text-ink-muted">{row.tenants.join(", ")}</td>
                      <td className="text-right">{money(row.monthly_rent)}</td>
                      <td className="text-sm text-ink-muted">{termLabel(row)}</td>
                      <td className="text-right">
                        <Badge tone={owedTone(row.owed)} data-testid="owed">{owedLabel(row.owed)}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Wide>
            <Pager page={page} onPage={setNumber} />
          </>
        )}
      </Card>
    </Shell>
  );
}

function RunsCard({ refresh }: { refresh: () => void }) {
  const [due, setDue] = useState(day(new Date()));
  const [asOf, setAsOf] = useState(day(new Date()));
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");

  async function run<T>(path: string, on: string, say: (done: T) => string) {
    setError("");
    setSaid("");
    try {
      setSaid(say(await api.post<T>(`/api/rent/${path}`, { on })));
      refresh();
    } catch (failed) {
      setError(detail(failed));
    }
  }
  const lateFees = (done: { charged: number }) =>
    `${done.charged} late fee${done.charged === 1 ? "" : "s"} charged.`;

  return (
    <Card title="Rent run and late fees">
      <div className="flex flex-wrap items-end gap-6">
        <Form className="flex items-end gap-3" onSubmit={(e) => { e.preventDefault(); run<RentRun>("charge-rent", due, rentRunSummary); }}>
          <Field label="Rent due on" id="rent-due" type="date" required value={due}
            onChange={(e) => setDue(e.target.value)} />
          <Button type="submit" variant="secondary">Charge rent</Button>
        </Form>
        <Form className="flex items-end gap-3" onSubmit={(e) => { e.preventDefault(); run("late-fees", asOf, lateFees); }}>
          <Field label="Late fees as of" id="late-as-of" type="date" required value={asOf}
            onChange={(e) => setAsOf(e.target.value)} />
          <Button type="submit" variant="secondary">Assess late fees</Button>
        </Form>
      </div>
      <p className="mt-3 text-sm text-ink-subtle">
        Each lease is charged its rent and recurring charges once per due date, and each rent at
        most one late fee.
      </p>
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}

// A lease's rent work, on its own page (pages/RentalsPages.tsx, LeasePage).
export function LeaseRow({ lease, refresh }: { lease: RentLease; refresh: () => void }) {
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");
  const names = lease.tenants.map((t) => t.name).join(", ");

  // Posts one record for this lease; true when it was recorded.
  async function record<T>(path: string, body: object, done: (answer: T) => string) {
    setError("");
    setSaid("");
    try {
      setSaid(done(await api.post<T>(path, body)));
      refresh();
      return true;
    } catch (failed) {
      setError(detail(failed));
      return false;
    }
  }

  return (
    <li className="flex flex-col gap-2 py-4" aria-label={`${lease.property}, ${lease.unit}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p>
          <span className="font-medium">{lease.property}, {lease.unit}</span>
          <span className="text-ink-muted"> · {names} · {money(lease.monthly_rent)} a month</span>
        </p>
        <Badge tone={owedTone(lease.owed)} data-testid="owed">
          {owedLabel(lease.owed)}
        </Badge>
      </div>
      {lease.on_hold && (
        <Notice tone="error">
          On a payment hold: payments are refused unless the hold allows them.
        </Notice>
      )}
      <p className="text-sm text-ink-subtle">
        {lease.tenants
          .map((t) => `${t.name}: prepaid ${money(t.prepaid)}, deposit ${money(t.deposit)}`)
          .join(" · ")}
      </p>
      {lease.open_charges.length > 0 && (
        <ul className="text-sm text-ink-muted">
          {lease.open_charges.map((c) => (
            <li key={c.id}>{chargeLabel(c)}</li>
          ))}
        </ul>
      )}
      {lease.rent_changes.length > 0 && (
        <ul className="text-sm" aria-label={`Rent changes, ${lease.unit}`}>
          {lease.rent_changes.map((c) => (
            <li key={c.id}>{rentChangeLine(c)}</li>
          ))}
        </ul>
      )}
      {lease.renters_insurance && (
        <p className="flex flex-wrap items-center gap-2 text-sm" data-testid="renters-insurance">
          <span>{insuranceLine(lease.renters_insurance)}</span>
          <Badge tone={insuranceBadge(lease.renters_insurance).tone}>
            {insuranceBadge(lease.renters_insurance).word}
          </Badge>
        </p>
      )}
      {lease.recurring.length > 0 && (
        <ul className="text-sm">
          {lease.recurring.map((r) => (
            <RecurringLine key={r.id} recurring={r} record={record} />
          ))}
        </ul>
      )}
      {lease.payments.length > 0 && (
        <table className="text-sm">
          <thead>
            <tr>
              <th className="text-left">Payment received</th>
              <th className="text-right">Amount</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {lease.payments.map((p) => (
              <PaymentRow key={p.transfer_id} payment={p} record={record} />
            ))}
          </tbody>
        </table>
      )}
      <details>
        <summary>Record for {lease.unit}</summary>
        <div className="mt-3 flex flex-col gap-4">
          <PaymentForm lease={lease} record={record} />
          <ChargeForm lease={lease} record={record} />
          <RecurringForm lease={lease} record={record} />
          <RentChangeForm lease={lease} record={record} />
          <InsuranceForms lease={lease} record={record} />
          <DepositForm lease={lease} record={record} />
        </div>
      </details>
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </li>
  );
}

type Recorder = <T>(path: string, body: object, done: (answer: T) => string) => Promise<boolean>;

// A payment as a row; It bounced is in its ⋯ menu and asks for the day it came back, naming the
// amount, before it is recorded, since it moves the money back and charges the property's NSF fee.
function PaymentRow({ payment, record }: { payment: RentLease["payments"][number]; record: Recorder }) {
  const [asking, setAsking] = useState(false);
  const [on, setOn] = useState(day(new Date()));
  return (
    <tr>
      <td>
        <span className="whitespace-nowrap">{usDate(payment.received_on)}</span>
        {payment.reference && <span className="block text-ink-muted">{payment.reference}</span>}
      </td>
      <td className="text-right">{money(payment.amount)}</td>
      <td className="text-right">
        {payment.bounced ? (
          <Badge tone="danger">Bounced</Badge>
        ) : asking ? (
          <Form
            className="flex flex-wrap items-center justify-end gap-2"
            onSubmit={async (event) => {
              event.preventDefault();
              const done = await record<Bounce>(`/api/rent/payments/${payment.transfer_id}/bounce`,
                { on }, (answer) => bounceSummary(payment.amount, answer));
              if (done) setAsking(false);
            }}
          >
            <input type="date" aria-label={`Bounced on, ${usDate(payment.received_on)} payment`} value={on}
              onChange={(e) => setOn(e.target.value)} required
              className="h-8 rounded-sm border border-line-strong bg-sunken px-2 text-sm text-ink" />
            <Button type="submit" variant="danger" size="sm">Record the {money(payment.amount)} bounce</Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setAsking(false)}>
              Cancel
            </Button>
          </Form>
        ) : (
          <RowMenu label={`${usDate(payment.received_on)} payment of ${money(payment.amount)}`}
            actions={[{ label: "It bounced", danger: true, onSelect: () => setAsking(true) }]} />
        )}
      </td>
    </tr>
  );
}

function PaymentForm({ lease, record }: { lease: RentLease; record: Recorder }) {
  const [id, setId] = useState(requestId);
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(day(new Date()));
  const [reference, setReference] = useState("");
  const hasPrepaid = lease.tenants.some((t) => Number(t.prepaid) > 0);
  return (
    <div className="flex flex-wrap items-end gap-3">
      <Form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          const body = { request_id: id, amount, received_on: on, reference: reference || null };
          if (await record<Payment>(`/api/rent/leases/${lease.id}/payments`, body, paymentSummary)) {
            setId(requestId());
            setAmount("");
            setReference("");
          }
        }}
      >
        <Field label="Payment received" id={`pay-${lease.id}`} inputMode="decimal" required
          value={amount} onChange={(e) => setAmount(e.target.value)} />
        <Field label="Received on" id={`pay-on-${lease.id}`} type="date" required value={on}
          onChange={(e) => setOn(e.target.value)} />
        <Field label="Check or reference" id={`pay-ref-${lease.id}`} maxLength={200}
          value={reference} onChange={(e) => setReference(e.target.value)} />
        <Button type="submit">Record payment</Button>
      </Form>
      {hasPrepaid && (
        <Button
          variant="secondary"
          onClick={() =>
            record<Payment>(`/api/rent/leases/${lease.id}/use-prepaid`,
              { request_id: requestId(), on: day(new Date()) }, paymentSummary)
          }
        >
          Use prepaid rent
        </Button>
      )}
    </div>
  );
}

function ChargeForm({ lease, record }: { lease: RentLease; record: Recorder }) {
  const [kind, setKind] = useState<"fee" | "credit">("fee");
  const [amount, setAmount] = useState("");
  const [due, setDue] = useState(day(new Date()));
  const [memo, setMemo] = useState("");
  return (
    <Form
      className="flex flex-wrap items-end gap-3"
      onSubmit={async (event) => {
        event.preventDefault();
        const body = { kind, amount, due_on: due, memo };
        const said = () => `${kind === "fee" ? "Fee" : "Credit"} of ${money(amount)} added.`;
        if (await record(`/api/rent/leases/${lease.id}/charges`, body, said)) {
          setAmount("");
          setMemo("");
        }
      }}
    >
      <Select label="Add a" id={`charge-kind-${lease.id}`} value={kind}
        onChange={(e) => setKind(e.target.value as "fee" | "credit")}>
        <option value="fee">Fee</option>
        <option value="credit">Credit</option>
      </Select>
      <Field label="Amount" id={`charge-amount-${lease.id}`} inputMode="decimal" required
        value={amount} onChange={(e) => setAmount(e.target.value)} />
      <Field label="Due on" id={`charge-due-${lease.id}`} type="date" required value={due}
        onChange={(e) => setDue(e.target.value)} />
      <Field label="What for" id={`charge-memo-${lease.id}`} required maxLength={200} value={memo}
        onChange={(e) => setMemo(e.target.value)} />
      <Button type="submit" variant="secondary">Add</Button>
    </Form>
  );
}

// A recurring charge; stopping it asks for the day it stops first.
function RecurringLine({ recurring, record }: { recurring: RecurringCharge; record: Recorder }) {
  const [asking, setAsking] = useState(false);
  const [on, setOn] = useState(day(new Date()));
  return (
    <li className="flex min-h-8 flex-wrap items-center gap-3">
      <span>{recurringLabel(recurring)}</span>
      {recurring.ends_on === null &&
        (asking ? (
          <Form
            className="flex items-center gap-2"
            onSubmit={async (event) => {
              event.preventDefault();
              const done = await record(`/api/rent/recurring-charges/${recurring.id}/stop`,
                { ends_on: on }, () => `${recurring.memo} stops ${usDate(on)}.`);
              if (done) setAsking(false);
            }}
          >
            <input type="date" aria-label={`Stops on, ${recurring.memo}`} value={on}
              onChange={(e) => setOn(e.target.value)} required
              className="h-8 rounded-sm border border-line-strong bg-sunken px-2 text-sm text-ink" />
            <Button type="submit" variant="secondary" size="sm">Stop it</Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setAsking(false)}>
              Cancel
            </Button>
          </Form>
        ) : (
          <Button type="button" variant="ghost" size="sm" onClick={() => setAsking(true)}>
            Stop
          </Button>
        ))}
    </li>
  );
}

function RecurringForm({ lease, record }: { lease: RentLease; record: Recorder }) {
  const [kind, setKind] = useState<"fee" | "credit">("fee");
  const [amount, setAmount] = useState("");
  const [from, setFrom] = useState(day(new Date()));
  const [memo, setMemo] = useState("");
  return (
    <Form
      className="flex flex-wrap items-end gap-3"
      onSubmit={async (event) => {
        event.preventDefault();
        const body = { kind, amount, memo, starts_on: from };
        const said = () => `${memo} of ${money(amount)} added every month.`;
        if (await record(`/api/rent/leases/${lease.id}/recurring-charges`, body, said)) {
          setAmount("");
          setMemo("");
        }
      }}
    >
      <Select label="Every month, a" id={`recurring-kind-${lease.id}`} value={kind}
        onChange={(e) => setKind(e.target.value as "fee" | "credit")}>
        <option value="fee">Fee</option>
        <option value="credit">Credit</option>
      </Select>
      <Field label="Monthly amount" id={`recurring-amount-${lease.id}`} inputMode="decimal" required
        value={amount} onChange={(e) => setAmount(e.target.value)} />
      <Field label="First due on" id={`recurring-from-${lease.id}`} type="date" required value={from}
        onChange={(e) => setFrom(e.target.value)} />
      <Field label="Monthly, for" id={`recurring-memo-${lease.id}`} required maxLength={200}
        value={memo} onChange={(e) => setMemo(e.target.value)} />
      <Button type="submit" variant="secondary">Add every month</Button>
    </Form>
  );
}

// A new monthly rent from the first of a month, after written notice of a full month. A lease
// with an end date names the term that lets its rent change during it. A state or city may ask
// for more notice, or cap the increase: that stays the company's to check.
function RentChangeForm({ lease, record }: { lease: RentLease; record: Recorder }) {
  const [from, setFrom] = useState(earliestRentChange(new Date()));
  const [amount, setAmount] = useState("");
  const [notice, setNotice] = useState(day(new Date()));
  const [served, setServed] = useState<RentChange["served_by"]>("mail");
  const [terms, setTerms] = useState("");
  return (
    <Form
      className="flex flex-wrap items-end gap-3"
      onSubmit={async (event) => {
        event.preventDefault();
        const body = {
          effective_on: from, monthly_rent: amount, notice_given_on: notice, served_by: served,
          lease_terms: terms || null,
        };
        const said = () => `Rent of ${money(amount)} a month from ${usDate(from)} recorded.`;
        if (await record(`/api/rent/leases/${lease.id}/rent-changes`, body, said)) {
          setAmount("");
          setTerms("");
        }
      }}
    >
      <Field label="New monthly rent" id={`rent-change-amount-${lease.id}`} inputMode="decimal" required
        value={amount} onChange={(e) => setAmount(e.target.value)} />
      <Field label="From (the first of a month)" id={`rent-change-from-${lease.id}`} type="date" required
        value={from} onChange={(e) => setFrom(e.target.value)} />
      <Field label="Written notice given on" id={`rent-change-notice-${lease.id}`} type="date" required
        value={notice} onChange={(e) => setNotice(e.target.value)} />
      <Select label="Served" id={`rent-change-served-${lease.id}`} value={served}
        onChange={(e) => setServed(e.target.value as RentChange["served_by"])}>
        <option value="mail">by mail</option>
        <option value="in person">in person</option>
        <option value="posted">posted at the unit</option>
        <option value="email">by email</option>
        <option value="other">another way</option>
      </Select>
      {lease.ends_on !== null && (
        <Field label="The lease term that allows it" id={`rent-change-terms-${lease.id}`} required
          maxLength={500} value={terms} onChange={(e) => setTerms(e.target.value)} />
      )}
      <Button type="submit" variant="secondary">Change rent</Button>
    </Form>
  );
}

function DepositForm({ lease, record }: { lease: RentLease; record: Recorder }) {
  const [id, setId] = useState(requestId);
  const [tenant, setTenant] = useState(lease.tenants[0]?.id ?? "");
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(day(new Date()));
  return (
    <Form
      className="flex flex-wrap items-end gap-3"
      onSubmit={async (event) => {
        event.preventDefault();
        const body = { request_id: id, tenant_id: tenant, amount, received_on: on };
        if (await record("/api/rent/deposits", body, () => `Deposit of ${money(amount)} held.`)) {
          setId(requestId());
          setAmount("");
        }
      }}
    >
      <Select label="Deposit from" id={`deposit-tenant-${lease.id}`} value={tenant}
        onChange={(e) => setTenant(e.target.value)}>
        {lease.tenants.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </Select>
      <Field label="Deposit received" id={`deposit-${lease.id}`} inputMode="decimal" required
        value={amount} onChange={(e) => setAmount(e.target.value)} />
      <Field label="Deposit received on" id={`deposit-on-${lease.id}`} type="date" required value={on}
        onChange={(e) => setOn(e.target.value)} />
      <Button type="submit" variant="secondary">Record deposit</Button>
    </Form>
  );
}
