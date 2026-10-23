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
                  ask("Turn autopay off? Nothing more is taken from your bank account.", () =>
                    record(`${path}/stop`, {}, () => "Autopay is off."), "Turn it off")}>
                  Turn off autopay
                </Button>
              </div>
            </div>
          ) : !view.available ? (
            <p className="text-base text-ink-muted" data-testid="autopay-off">{view.why_not}</p>
          ) : (
            <SetUpForm path={path} view={view} fail={setError} />
          )}
          {on && <AutopayRuns autopay={on} />}
        </>
      )}
      {prompt}
    </Card>
  );
}

function SetUpForm({ path, view, fail }: { path: string; view: AutopayView; fail: (why: string) => void }) {
  const [rule, setRule] = useState<AutopayRule>("balance");
  const [amount, setAmount] = useState("");
  async function start(event: React.FormEvent) {
    event.preventDefault();
    fail("");
    try {
      const started = await api.post<Started>(path, { rule, amount: amount.trim() });
      window.location.assign(started.url);
    } catch (failed) {
      fail(failed instanceof ApiError ? failed.detail : String(failed));
    }
  }
  return (
    <Form className="flex flex-col gap-3" onSubmit={start}>
      <p className="text-sm text-ink-muted">
        Pay each month by itself, on your rent's due day{view.next_due ? ` (next: ${usDate(view.next_due)})` : ""},
        from a bank account you save on our payments partner's page.{" "}
        {view.fee_payer === "tenant" ? "A processing fee of at most $5.00 is added to each payment." : "No fee."}
      </p>
      <Select label="Pay" id="autopay-rule" value={rule} onChange={(e) => setRule(e.target.value as AutopayRule)}>
        <option value="balance">My balance due, up to an amount</option>
        <option value="fixed">The same amount each month</option>
      </Select>
      <Field label={rule === "balance" ? "Up to" : "Amount"} id="autopay-amount" inputMode="decimal" value={amount}
        onChange={(e) => setAmount(e.target.value)} />
      <div>
        <Button type="submit" disabled={!payable(amount)}>Save a bank account and turn on autopay</Button>
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
                  ask(`Turn off ${a.tenant}'s autopay (${autopayRule(a)})? Nothing more is taken from their bank.`, () =>
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
