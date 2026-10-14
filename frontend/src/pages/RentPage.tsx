import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { day, money } from "../money";
import type { Bounce, Payment, RecurringCharge, RentLease, RentRun } from "../rent";
import {
  bounceSummary,
  chargeLabel,
  owedLabel,
  paymentSummary,
  recurringLabel,
  rentRunSummary,
  requestId,
} from "../rent";
import { STAFF_NAV } from "./staffNav";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// Staff's rent work: the month's rent (with each lease's recurring charges) and late fees for
// every lease, and per lease its open charges, payments and recurring charges, with forms to
// record a payment, a deposit, a fee or a credit once or every month, to use prepaid rent, to
// stop a recurring charge and to undo a payment that bounced. The PMS records what reached the bank; it
// takes no payments itself.
export function RentPage({ me }: { me: Me }) {
  const [leases, setLeases] = useState<RentLease[] | null>(null);
  const [error, setError] = useState("");

  function refresh() {
    api.get<RentLease[]>("/api/rent").then(setLeases).catch((failed) => setError(detail(failed)));
  }
  useEffect(refresh, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Rent" description="Each lease's charges and payments, the month's rent run and late fees." />
      <RunsCard refresh={refresh} />
      <Card title="Leases">
        {leases === null ? (
          <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
        ) : leases.length === 0 ? (
          <Notice tone="info">No lease is in effect or owes anything.</Notice>
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {leases.map((lease) => (
              <LeaseRow key={lease.id} lease={lease} refresh={refresh} />
            ))}
          </ul>
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
        <form className="flex items-end gap-3" onSubmit={(e) => { e.preventDefault(); run<RentRun>("charge-rent", due, rentRunSummary); }}>
          <Field label="Rent due on" id="rent-due" type="date" required value={due}
            onChange={(e) => setDue(e.target.value)} />
          <Button type="submit" variant="secondary">Charge rent</Button>
        </form>
        <form className="flex items-end gap-3" onSubmit={(e) => { e.preventDefault(); run("late-fees", asOf, lateFees); }}>
          <Field label="Late fees as of" id="late-as-of" type="date" required value={asOf}
            onChange={(e) => setAsOf(e.target.value)} />
          <Button type="submit" variant="secondary">Assess late fees</Button>
        </form>
      </div>
      <p className="mt-3 text-sm text-ink-subtle">
        Each lease is charged its rent and recurring charges once per due date, and each rent at
        most one late fee.
      </p>
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}

function LeaseRow({ lease, refresh }: { lease: RentLease; refresh: () => void }) {
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
        <p className="font-mono" data-testid="owed">
          {owedLabel(lease.owed)}
        </p>
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
      {lease.recurring.length > 0 && (
        <ul className="text-sm">
          {lease.recurring.map((r) => (
            <RecurringLine key={r.id} recurring={r} record={record} />
          ))}
        </ul>
      )}
      {lease.payments.length > 0 && (
        <ul className="text-sm">
          {lease.payments.map((p) => (
            <PaymentLine key={p.transfer_id} payment={p} record={record} />
          ))}
        </ul>
      )}
      <details>
        <summary className="cursor-pointer text-sm text-ink-muted">Record for {lease.unit}</summary>
        <div className="mt-3 flex flex-col gap-4">
          <PaymentForm lease={lease} record={record} />
          <ChargeForm lease={lease} record={record} />
          <RecurringForm lease={lease} record={record} />
          <DepositForm lease={lease} record={record} />
        </div>
      </details>
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </li>
  );
}

type Recorder = <T>(path: string, body: object, done: (answer: T) => string) => Promise<boolean>;

function PaymentLine({ payment, record }: { payment: RentLease["payments"][number]; record: Recorder }) {
  const [on, setOn] = useState(day(new Date()));
  return (
    <li className="flex flex-wrap items-center gap-3 py-1">
      <span>
        {payment.received_on} {money(payment.amount)}
        {payment.reference && <span className="text-ink-muted"> ({payment.reference})</span>}
      </span>
      {payment.bounced ? (
        <span className="text-danger">Bounced</span>
      ) : (
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            record<Bounce>(`/api/rent/payments/${payment.transfer_id}/bounce`, { on },
              (answer) => bounceSummary(payment.amount, answer));
          }}
        >
          <input type="date" aria-label={`Bounced on, ${payment.received_on} payment`} value={on}
            onChange={(e) => setOn(e.target.value)}
            className="h-8 rounded-sm border border-line-strong bg-sunken px-2 text-sm text-ink" />
          <Button type="submit" variant="ghost">It bounced</Button>
        </form>
      )}
    </li>
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
      <form
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
      </form>
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
    <form
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
    </form>
  );
}

function RecurringLine({ recurring, record }: { recurring: RecurringCharge; record: Recorder }) {
  const [on, setOn] = useState(day(new Date()));
  return (
    <li className="flex flex-wrap items-center gap-3 py-1">
      <span>{recurringLabel(recurring)}</span>
      {recurring.ends_on === null && (
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            record(`/api/rent/recurring-charges/${recurring.id}/stop`, { ends_on: on },
              () => `${recurring.memo} stops ${on}.`);
          }}
        >
          <input type="date" aria-label={`Stops on, ${recurring.memo}`} value={on}
            onChange={(e) => setOn(e.target.value)}
            className="h-8 rounded-sm border border-line-strong bg-sunken px-2 text-sm text-ink" />
          <Button type="submit" variant="ghost">Stop</Button>
        </form>
      )}
    </li>
  );
}

function RecurringForm({ lease, record }: { lease: RentLease; record: Recorder }) {
  const [kind, setKind] = useState<"fee" | "credit">("fee");
  const [amount, setAmount] = useState("");
  const [from, setFrom] = useState(day(new Date()));
  const [memo, setMemo] = useState("");
  return (
    <form
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
    </form>
  );
}

function DepositForm({ lease, record }: { lease: RentLease; record: Recorder }) {
  const [id, setId] = useState(requestId);
  const [tenant, setTenant] = useState(lease.tenants[0]?.id ?? "");
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(day(new Date()));
  return (
    <form
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
    </form>
  );
}
