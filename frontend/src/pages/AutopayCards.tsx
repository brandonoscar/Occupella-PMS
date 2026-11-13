import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { latest } from "../latest";
import { usDate } from "../money";
import type { Autopay, AutopayRule, AutopayView, Started } from "../payments";
import { STATUS, autopayLine, autopayRule, autopayRunLine, payable } from "../payments";
import { useRecord } from "./useRecord";
import { leave } from "../router";
import type { LeaseMethods, Method } from "../connect";
import { CONNECT, METHOD_NAMES } from "../connect";

// A tenant's autopay of a lease (src/pms/api/autopay.py): set up with what it pays, the bank
// account saved on the payments partner's page (the PMS never sees its number); then it pays on
// each rent due day, from the next one after the account is saved, until they turn it off.
export function TenantAutopayCard({ leaseId }: { leaseId: string }) {
  const [view, setView] = useState<AutopayView | null>(null);
  const [error, setError] = useState("");
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  const { ask, prompt } = useConfirm();
  const path = `/api/payments/leases/${encodeURIComponent(leaseId)}/autopay`;
  useEffect(() => {
    const run = latest();
    api.get<AutopayView>(path).then(run.keep(setView))
      .catch(run.keep((failed: unknown) => setError(failed instanceof ApiError ? failed.detail : String(failed))));
    return run.stop;
  }, [path, runs]);

  const on = view?.autopays.find((a) => a.stopped === null) ?? null;
  // Nothing to set up while the tenant can't pay online, unless one is on (to turn off).
  if (view !== null && !view.available && !on) return null;
  return (
    <Card title="Autopay">
      <Said>{said}</Said>
      <Notice tone="error">{error || refused}</Notice>
      {view === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : (
        <>
          {on ? (
            <div className="flex flex-col gap-3">
              <p className="text-base" data-testid="autopay">{autopayLine(on)}</p>
              {view.next_due && on.account && (
                <p className="text-sm text-ink-muted">Next: {usDate(view.next_due)}. We email you before each payment.</p>
              )}
              <div>
                <Button variant="secondary" onClick={() =>
                  ask("Turn autopay off? No new payment starts from it; one already sent to your bank isn't called back.", () =>
                    record(`${path}/stop`, {}, () => "Autopay is off."), "Turn it off")}>
                  Turn off autopay
                </Button>
              </div>
            </div>
          ) : (
            <SetUpForm path={path} leaseId={leaseId} view={view} fail={setError} />
          )}
          {on && <AutopayRuns autopay={on} />}
        </>
      )}
      {prompt}
    </Card>
  );
}

// Through Stripe Connect (docs/adr/0014-stripe-connect.md) autopay pays from a bank account or a
// card the tenant saves; a card fee on autopay is always the company's.
function SetUpForm({ path, leaseId, view, fail }: {
  path: string;
  leaseId: string;
  view: AutopayView;
  fail: (why: string) => void;
}) {
  const [rule, setRule] = useState<AutopayRule>("balance");
  const [amount, setAmount] = useState("");
  const [connect, setConnect] = useState<LeaseMethods | null>(null);
  const [method, setMethod] = useState<Method>("ach");
  useEffect(() => {
    const run = latest();
    api.get<LeaseMethods>(`${CONNECT}/leases/${encodeURIComponent(leaseId)}`).then(run.keep(setConnect))
      .catch(run.keep(() => setConnect(null)));
    return run.stop;
  }, [leaseId]);
  const viaConnect = connect?.connect === true;
  const card = viaConnect && method === "card";
  async function start(event: React.FormEvent) {
    event.preventDefault();
    fail("");
    try {
      const started = viaConnect
        ? await api.post<Started>(`${CONNECT}/leases/${encodeURIComponent(leaseId)}/autopay`, {
          rule,
          amount: amount.trim(),
          method,
        })
        : await api.post<Started>(path, { rule, amount: amount.trim() });
      leave(started.url);
    } catch (failed) {
      fail(failed instanceof ApiError ? failed.detail : String(failed));
    }
  }
  return (
    <Form className="flex flex-col gap-3" onSubmit={start}>
      <p className="text-sm text-ink-muted">
        Pay each month by itself, on your rent's due day{view.next_due ? ` (next: ${usDate(view.next_due)})` : ""},
        from {viaConnect ? "a bank account or card" : "a bank account"} you save on our payments partner's page.{" "}
        {card
          ? "No fee by card."
          : view.fee_payer === "tenant"
            ? viaConnect
              ? "A processing fee is added to each bank payment, shown in the email before it is taken."
              : "A processing fee of at most $5.00 is added to each payment."
            : "No fee."}
      </p>
      {viaConnect && (
        <Select label="Pay from" id="autopay-method" value={method} onChange={(e) => setMethod(e.target.value as Method)}>
          {connect.autopay.map((m) => <option key={m.method} value={m.method}>{METHOD_NAMES[m.method]}</option>)}
        </Select>
      )}
      <Select label="Pay" id="autopay-rule" value={rule} onChange={(e) => setRule(e.target.value as AutopayRule)}>
        <option value="balance">My balance due, up to an amount</option>
        <option value="fixed">The same amount each month</option>
      </Select>
      <Field label={rule === "balance" ? "Up to" : "Amount"} id="autopay-amount" inputMode="decimal" value={amount}
        onChange={(e) => setAmount(e.target.value)} />
      <div>
        <Button type="submit" disabled={!payable(amount)}
          says={card
            ? "Takes you to our payments partner's page to save your card. Nothing is paid before a rent due day."
            : "Takes you to our payments partner's page to save your bank account. Nothing is paid before a rent due day."}>
          {card ? "Save a card and turn on autopay" : "Save a bank account and turn on autopay"}
        </Button>
      </div>
    </Form>
  );
}

function AutopayRuns({ autopay }: { autopay: Autopay }) {
  if (autopay.runs.length === 0) return null;
  return (
    <ul className="mt-4 flex flex-col divide-y divide-line text-base" aria-label="Autopay payments">
      {autopay.runs.map((run) => (
        <li key={run.due_on} className="flex flex-wrap items-center justify-between gap-2 py-2">
          <span>{autopayRunLine(run)}</span>
          {run.status !== "skipped" && <Badge tone={STATUS[run.status].tone}>{STATUS[run.status].label}</Badge>}
        </li>
      ))}
    </ul>
  );
}

// Staff's view of a lease's autopays (the lease page's Summary): each tenant's, what it pays and
// from what account, its payments, and Turn off, which names who did.
export function LeaseAutopayCard({ leaseId }: { leaseId: string }) {
  const [autopays, setAutopays] = useState<Autopay[] | null>(null);
  const [runs, setRuns] = useState(0);
  const { said, error, record } = useRecord(() => setRuns((n) => n + 1));
  const { ask, prompt } = useConfirm();
  useEffect(() => {
    const run = latest();
    api.get<Autopay[]>(`/api/payments/autopay?lease=${encodeURIComponent(leaseId)}`).then(run.keep(setAutopays))
      .catch(run.keep(() => setAutopays([])));
    return run.stop;
  }, [leaseId, runs]);

  return (
    <Card title="Autopay">
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
      {autopays === null ? null : autopays.length === 0 ? (
        <p className="text-sm text-ink-subtle">No tenant set up autopay.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-line text-base" aria-label="Autopays">
          {autopays.map((a) => (
            <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span>
                {a.tenant}: {autopayLine(a)}
                {a.runs[0] && <span className="block text-sm text-ink-muted">Last: {autopayRunLine(a.runs[0])}</span>}
              </span>
              {a.stopped === null && (
                <Button variant="secondary" onClick={() =>
                  ask(`Turn off ${a.tenant}'s autopay (${autopayRule(a)})? No new payment starts from it; one already sent to their bank isn't called back.`, () =>
                    record(`/api/payments/autopay/${a.id}/stop`, {}, () => `Turned off ${a.tenant}'s autopay.`),
                    "Turn it off")}>
                  Turn off
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {prompt}
    </Card>
  );
}
