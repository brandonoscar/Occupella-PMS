import { useState } from "react";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { RowMenu } from "../components/RowMenu";
import { Select } from "../components/Select";
import { Badge } from "../components/Badge";
import { day, money, usDate } from "../money";
import type { Bounce, Hold, Payment, PaymentMethod, RecurringCharge, RentChange, RentLease } from "../rent";
import {
  PAYMENT_METHODS,
  bounceSummary,
  earliestRentChange,
  methodLabel,
  paymentSummary,
  recurringLabel,
  requestId,
} from "../rent";

// The forms of a lease's rent work (pages/LeasePage.tsx), each one thing recorded: a payment, a
// fee or credit, one every month, a new rent, a deposit; and a payment's row, which can bounce,
// and a monthly charge's, which can stop.

export type Recorder = <T>(path: string, body: object, done: (answer: T) => string) => Promise<boolean>;

// A payment as a row; It bounced is in its ⋯ menu and asks for the day it came back, naming the
// amount, before it is recorded, since it moves the money back and charges the property's NSF fee.
export function PaymentRow({ payment, record }: { payment: RentLease["payments"][number]; record: Recorder }) {
  const [asking, setAsking] = useState(false);
  const [on, setOn] = useState(day(new Date()));
  return (
    <tr>
      <td>
        <span className="whitespace-nowrap">{usDate(payment.received_on)}</span>
        {(payment.reference || payment.method) && (
          <span className="block text-ink-muted">
            {[payment.method && methodLabel(payment.method), payment.reference].filter(Boolean).join(" · ")}
          </span>
        )}
        <a href={`/api/rent/payments/${encodeURIComponent(payment.transfer_id)}/receipt`}
          className="text-sm text-iris hover:text-iris-hover hover:underline">Receipt (PDF)</a>
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

export function PaymentForm({ lease, record }: { lease: RentLease; record: Recorder }) {
  const [id, setId] = useState(requestId);
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(day(new Date()));
  const [reference, setReference] = useState("");
  const [method, setMethod] = useState<PaymentMethod>("check");
  const [pastHold, setPastHold] = useState(false);
  const hasPrepaid = lease.tenants.some((t) => Number(t.prepaid) > 0);
  return (
    <div className="flex flex-wrap items-end gap-3">
      <Form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          const body = {
            request_id: id, amount, received_on: on, method, reference: reference || null,
            ...(lease.on_hold && pastHold ? { past_hold: true } : {}),
          };
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
        <Select label="Method" id={`pay-method-${lease.id}`} value={method}
          onChange={(e) => setMethod(e.target.value as PaymentMethod)}>
          {PAYMENT_METHODS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </Select>
        <Field label="Check or reference" id={`pay-ref-${lease.id}`} maxLength={200}
          value={reference} onChange={(e) => setReference(e.target.value)} />
        {lease.on_hold && (
          <label className="flex basis-full items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={pastHold} onChange={(e) => setPastHold(e.target.checked)} />
            Accept this payment past the payment hold
          </label>
        )}
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

export function ChargeForm({ lease, record }: { lease: RentLease; record: Recorder }) {
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

// A recurring charge; Stop is in its ⋯ menu and asks for the day it stops first.
export function RecurringLine({ recurring, record }: { recurring: RecurringCharge; record: Recorder }) {
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
          <RowMenu label={recurring.memo} actions={[{ label: "Stop", danger: true, onSelect: () => setAsking(true) }]} />
        ))}
    </li>
  );
}

export function RecurringForm({ lease, record }: { lease: RentLease; record: Recorder }) {
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
export function RentChangeForm({ lease, record }: { lease: RentLease; record: Recorder }) {
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

export function DepositForm({ lease, record }: { lease: RentLease; record: Recorder }) {
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

// A payment hold placed when an eviction is filed: from its first day a payment is refused unless
// staff accept it past the hold.
export function HoldForm({ lease, record }: { lease: RentLease; record: Recorder }) {
  const [since, setSince] = useState(day(new Date()));
  const [reason, setReason] = useState("Eviction filed");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record<Hold>(`/api/rent/leases/${lease.id}/holds`, { starts_on: since, reason },
        (hold) => `On a payment hold since ${usDate(hold.starts_on)}.`).then(() => undefined);
    }}>
      <Field label="Hold payments from" id={`hold-since-${lease.id}`} type="date" required value={since}
        onChange={(e) => setSince(e.target.value)} />
      <Field label="Why" id={`hold-why-${lease.id}`} required maxLength={500} value={reason}
        onChange={(e) => setReason(e.target.value)} />
      <p className="text-sm text-ink-muted">From then on a payment is refused unless you accept it past the hold.</p>
      <div><Button type="submit">Place hold</Button></div>
    </Form>
  );
}

export function ReleaseForm({ hold, record }: { hold: Hold; record: Recorder }) {
  const [ends, setEnds] = useState(day(new Date()));
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record<Hold>(`/api/rent/holds/${hold.id}/release`, { ends_on: ends },
        (released) => `The payment hold is released from ${usDate(released.ends_on ?? ends)}.`).then(() => undefined);
    }}>
      <p className="text-base text-ink">On hold since {usDate(hold.starts_on)}: {hold.reason}</p>
      <Field label="Accept payments again from" id={`release-${hold.id}`} type="date" required value={ends}
        onChange={(e) => setEnds(e.target.value)} />
      <div><Button type="submit">Release hold</Button></div>
    </Form>
  );
}
