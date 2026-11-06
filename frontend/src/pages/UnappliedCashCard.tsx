import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { companyDay, money, usDate } from "../money";
import type { PaymentMethod } from "../rent";
import { PAYMENT_METHODS, requestId } from "../rent";
import type { LeaseDetail } from "../rentals";
import type { Unapplied } from "../unapplied";
import { LONG_WAIT_DAYS, UNAPPLIED, cameAs, waited } from "../unapplied";
import { LeaseFinder } from "./LeaseFinder";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

type Bank = { id: string; name: string };
type Panel = { kind: "record" } | { kind: "apply" | "refund"; item: Unapplied } | null;

// Banking: money that came into an operating trust bank account and can't be matched to a lease
// yet, held as unapplied cash, oldest first with how long each has waited. Staff record it as it
// came, apply it to a lease (it pays what the lease owes, as Receive payment does) or refund it.
// Month-end isn't approved while any is held.
export function UnappliedCash({ banks, refresh, changed }: {
  banks: Bank[];
  refresh: number;
  changed: () => void;
}) {
  const [items, setItems] = useState<Unapplied[] | null>(null);
  const [unread, setUnread] = useState("");
  const [panel, setPanel] = useState<Panel>(null);
  const [runs, setRuns] = useState(0);
  const { said, error, record } = useRecord(() => {
    setRuns((n) => n + 1);
    changed();
  });
  useEffect(() => {
    api.get<Unapplied[]>(UNAPPLIED).then((found) => {
      setItems(found);
      setUnread("");
    }).catch((failed) => {
      // Never as if nothing were held.
      setItems(null);
      setUnread(`Unapplied cash couldn't load: ${failed instanceof ApiError ? failed.detail : String(failed)}`);
    });
  }, [runs, refresh]);
  // A form in the side panel closes once what it recorded is recorded.
  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setPanel(null);
    return recorded;
  };

  return (
    <Card title="Unapplied cash">
      <Said>{said}</Said>
      <p className="text-sm text-ink-muted">
        Money that came in and can't be matched to a lease yet. Month-end isn't approved while any is held.
      </p>
      {unread !== "" && <Notice tone="error">{unread}</Notice>}
      {panel === null && <Notice tone="error">{error}</Notice>}
      {items !== null && items.length === 0 && (
        <p className="mt-2 text-base text-ink-subtle">None held.</p>
      )}
      {items !== null && items.length > 0 && (
        <Wide>
          <table className="mt-2 w-full text-base" aria-label="Unapplied cash">
            <thead>
              <tr>
                <th className="text-left">Received</th>
                <th className="text-left">From</th>
                <th className="text-left">How</th>
                <th className="text-left">Memo</th>
                <th className="text-right">Amount</th>
                <th className="text-right">Held</th>
                <th className="text-left">Waiting</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id} aria-label={`${item.payer} ${money(item.left)}`}>
                  <td>{usDate(item.received_on)}</td>
                  <td>{item.payer}</td>
                  <td>{cameAs(item)}</td>
                  <td className="text-ink-muted">{item.memo}</td>
                  <td className="text-right">{money(item.amount)}</td>
                  <td className="text-right">{money(item.left)}</td>
                  <td>
                    {item.age_days >= LONG_WAIT_DAYS
                      ? <Badge tone="caution">{waited(item.age_days)}</Badge>
                      : waited(item.age_days)}
                  </td>
                  <td className="text-right">
                    <RowMenu label={`${item.payer} ${money(item.left)}`} actions={[
                      { label: "Apply to a lease", onSelect: () => setPanel({ kind: "apply", item }) },
                      { label: "Refund", onSelect: () => setPanel({ kind: "refund", item }) },
                    ]} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
      <div className="mt-3">
        <Button variant="secondary" onClick={() => setPanel({ kind: "record" })}>Record unapplied cash</Button>
      </div>
      {panel?.kind === "record" && (
        <SidePanel title="Record unapplied cash" onClose={() => setPanel(null)}>
          <RecordForm banks={banks} record={recordThenClose} />
          <Notice tone="error">{error}</Notice>
        </SidePanel>
      )}
      {panel?.kind === "apply" && (
        <SidePanel title="Apply to a lease" onClose={() => setPanel(null)}>
          <ApplyForm item={panel.item} record={recordThenClose} />
          <Notice tone="error">{error}</Notice>
        </SidePanel>
      )}
      {panel?.kind === "refund" && (
        <SidePanel title="Refund unapplied cash" onClose={() => setPanel(null)}>
          <RefundForm item={panel.item} record={recordThenClose} />
          <Notice tone="error">{error}</Notice>
        </SidePanel>
      )}
    </Card>
  );
}

function RecordForm({ banks, record }: { banks: Bank[]; record: Recorder }) {
  const [id] = useState(requestId);
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(companyDay());
  const [payer, setPayer] = useState("");
  const [method, setMethod] = useState<PaymentMethod>("check");
  const [reference, setReference] = useState("");
  const [memo, setMemo] = useState("");
  const [bank, setBank] = useState(banks[0]?.id ?? "");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record(UNAPPLIED, {
        request_id: id,
        amount,
        received_on: on,
        payer,
        method,
        reference: reference.trim() || null,
        memo: memo.trim() || null,
        ...(banks.length > 1 ? { bank_account_id: bank } : {}),
      }, () => `Recorded ${money(amount)} from ${payer.trim()} as unapplied cash.`);
    }}>
      <Field label="Amount" id="unapplied-amount" inputMode="decimal" required value={amount}
        onChange={(e) => setAmount(e.target.value)} />
      <Field label="Received on" id="unapplied-on" type="date" required max={companyDay()} value={on}
        onChange={(e) => setOn(e.target.value)} />
      <Field label="From" id="unapplied-payer" required placeholder="The name on the check, say" value={payer}
        onChange={(e) => setPayer(e.target.value)} />
      <Select label="How it came" id="unapplied-method" value={method}
        onChange={(e) => setMethod(e.target.value as PaymentMethod)}>
        {PAYMENT_METHODS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
      </Select>
      <Field label="Check or reference number (optional)" id="unapplied-reference" value={reference}
        onChange={(e) => setReference(e.target.value)} />
      <Field label="Memo (optional)" id="unapplied-memo" value={memo} onChange={(e) => setMemo(e.target.value)} />
      {banks.length > 1 && (
        <Select label="Trust bank account" id="unapplied-bank" value={bank} onChange={(e) => setBank(e.target.value)}>
          {banks.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </Select>
      )}
      <div>
        <Button type="submit">Record unapplied cash</Button>
      </div>
    </Form>
  );
}

// Applied to a lease staff find: it pays what the lease owes as Receive payment does, rent first,
// and the rest is the first tenant's prepaid rent.
function ApplyForm({ item, record }: { item: Unapplied; record: Recorder }) {
  const [id] = useState(requestId);
  const [lease, setLease] = useState<LeaseDetail | null>(null);
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(companyDay());
  const [account, setAccount] = useState("");
  const shares = lease?.owners.filter((o) => o.percent !== null) ?? [];
  const choose = lease !== null && shares.length === 0 && lease.owners.length > 1;
  const paying = amount || item.left;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-base text-ink">
        {money(item.left)} from {item.payer} · {cameAs(item)} · received {usDate(item.received_on)}
      </p>
      {lease === null ? (
        <LeaseFinder id={`unapplied-lease-${item.id}`} onLease={setLease} />
      ) : (
        <Form className="flex flex-col gap-3" onSubmit={(event) => {
          event.preventDefault();
          return record(`${UNAPPLIED}/${item.id}/apply`, {
            request_id: id,
            lease_id: lease.id,
            amount: amount || null,
            on,
            ...(choose ? { account: account || lease.owners[0]?.account } : {}),
          }, () => `Applied ${money(paying)} of unapplied cash to ${lease.property} · ${lease.unit}.`);
        }}>
          <span className="flex flex-wrap items-center justify-between gap-2 text-base">
            <span>{lease.property} · {lease.unit} · {lease.tenants.map((t) => t.name).join(", ")}</span>
            <Button variant="ghost" size="sm" onClick={() => setLease(null)}>Another lease</Button>
          </span>
          <p className="text-sm text-ink-muted">Balance due {money(lease.owed)}</p>
          {choose && (
            <Select label="Owner's account" id={`unapplied-account-${item.id}`}
              value={account || lease.owners[0]?.account} onChange={(e) => setAccount(e.target.value)}>
              {lease.owners.map((o) => <option key={o.account} value={o.account}>{o.owner}</option>)}
            </Select>
          )}
          <Field label={`Amount (leave empty for all ${money(item.left)})`} id={`unapplied-apply-${item.id}`}
            inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
          <Field label="Applied on" id={`unapplied-apply-on-${item.id}`} type="date" required max={companyDay()}
            value={on} onChange={(e) => setOn(e.target.value)} />
          <div>
            <Button type="submit"
              says="Pays what the lease owes, rent first, as Receive payment does; the rest is the first tenant's prepaid rent. Each tenant with an email on file gets a receipt.">
              Apply {money(paying)} to this lease
            </Button>
          </div>
        </Form>
      )}
    </div>
  );
}

// Refunded: paid back out of the trust bank account's cash to whom it is paid (a payout: the
// disburse role and the account's signers). The PMS records what the bank did.
function RefundForm({ item, record }: { item: Unapplied; record: Recorder }) {
  const [id] = useState(requestId);
  const [paidTo, setPaidTo] = useState(item.payer);
  const [method, setMethod] = useState<"check" | "EFT">("check");
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(companyDay());
  const { ask, prompt } = useConfirm();
  const paying = amount || item.left;
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      ask(`Refund ${money(paying)} of unapplied cash to ${paidTo.trim()} by ${method === "EFT" ? "EFT" : "check"}`
        + ` on ${usDate(on)}? It leaves ${item.bank_account}.`, () =>
        record(`${UNAPPLIED}/${item.id}/refund`, {
          request_id: id,
          paid_to: paidTo,
          method,
          amount: amount || null,
          on,
        }, (out: { amount: string }) => `Refunded ${money(out.amount)} to ${paidTo.trim()}.`), "Refund");
    }}>
      <p className="text-base text-ink">
        {money(item.left)} from {item.payer} · {cameAs(item)} · received {usDate(item.received_on)}
      </p>
      <Field label="Paid to" id={`unapplied-paid-to-${item.id}`} required value={paidTo}
        onChange={(e) => setPaidTo(e.target.value)} />
      <Select label="How" id={`unapplied-refund-method-${item.id}`} value={method}
        onChange={(e) => setMethod(e.target.value as "check" | "EFT")}>
        <option value="check">Check</option>
        <option value="EFT">EFT</option>
      </Select>
      <Field label={`Amount (leave empty for all ${money(item.left)})`} id={`unapplied-refund-${item.id}`}
        inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
      <Field label="Paid out on" id={`unapplied-refund-on-${item.id}`} type="date" required max={companyDay()}
        value={on} onChange={(e) => setOn(e.target.value)} />
      <div>
        <Button type="submit"
          says="Records the money paid back out of trust to whom it is paid. The PMS sends no money.">
          Refund {money(paying)}
        </Button>
      </div>
      {prompt}
    </Form>
  );
}
