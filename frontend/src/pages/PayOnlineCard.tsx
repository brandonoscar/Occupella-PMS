import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { latest } from "../latest";
import { money, usDate } from "../money";
import type { OnlineMethod, OnlinePayment } from "../onlinePayments";
import { METHOD_WORDS, STATUS } from "../onlinePayments";

type View = { methods: OnlineMethod[]; payments: OnlinePayment[] };

// The answer as the card reads it; one that isn't the tenant's view offers nothing.
export function readView(answer: unknown): View {
  const view = answer as Partial<View> | null;
  return view && Array.isArray(view.methods) && Array.isArray(view.payments)
    ? (view as View)
    : { methods: [], payments: [] };
}

// A tenant pays what their lease owes online (migration 0158): the amount, by card or bank
// account, on the payment processor's page. It shows once the company takes a method and the lease
// owes something; each online payment is listed with how it stands until the processor says.
export function PayOnlineCard({ leaseId, owed, go = (url: string) => window.location.assign(url) }: {
  leaseId: string;
  owed: string;
  go?: (url: string) => void;
}) {
  const [view, setView] = useState<View | null>(null);
  const [amount, setAmount] = useState(owed);
  const [method, setMethod] = useState<OnlineMethod | "">("");
  const [error, setError] = useState("");
  useEffect(() => {
    const run = latest();
    api.get<unknown>("/api/tenant/online-payments")
      .then(run.keep((answer: unknown) => {
        const found = readView(answer);
        setView(found);
        setMethod((chosen) => chosen || found.methods[0] || "");
      }))
      .catch(run.keep((why) => setError(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
  }, [leaseId]);
  useEffect(() => setAmount(owed), [owed]);

  if (view === null) return error ? <Notice tone="error">{error}</Notice> : null;
  const mine = view.payments.filter((p) => p.lease_id === leaseId);
  if (view.methods.length === 0 && mine.length === 0) return null;
  return (
    <Card title="Pay online">
      {view.methods.length > 0 && Number(owed) > 0 && (
        <Form className="flex flex-col gap-3" onSubmit={async (event) => {
          event.preventDefault();
          setError("");
          try {
            const started = await api.post<{ payment_id: string; url: string }>(
              `/api/tenant/leases/${leaseId}/online-payments`, { amount, method },
            );
            go(started.url);
          } catch (failed) {
            setError(failed instanceof ApiError ? failed.detail : String(failed));
          }
        }}>
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Amount" id="pay-online-amount" required inputMode="decimal" value={amount}
              onChange={(event) => setAmount(event.target.value)} />
            <Select label="Pay by" id="pay-online-method" value={method}
              onChange={(event) => setMethod(event.target.value as OnlineMethod)}>
              {view.methods.map((m) => <option key={m} value={m}>{METHOD_WORDS[m]}</option>)}
            </Select>
            <Button type="submit">Continue to payment</Button>
          </div>
          <p className="text-sm text-ink-muted">
            You pay on the payment processor's page, with no fee. It shows here as paid once the money comes.
          </p>
        </Form>
      )}
      <Notice tone="error">{error}</Notice>
      {mine.length > 0 && (
        <ul className="mt-4 flex flex-col divide-y divide-line text-base" aria-label="Online payments">
          {mine.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span>{usDate(p.started_at.slice(0, 10))} · {money(p.amount)} · {METHOD_WORDS[p.method]}</span>
              <span className="flex items-center gap-2">
                <Badge tone={STATUS[p.status].tone}>{STATUS[p.status].label}</Badge>
                {p.reason && <span className="text-sm text-ink-muted">{p.reason}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
