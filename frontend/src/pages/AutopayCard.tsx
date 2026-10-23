import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { ConfirmDialog } from "../components/Confirm";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { latest } from "../latest";
import { usDate } from "../money";
import type { Autopay, OnlineMethod } from "../onlinePayments";
import { AUTOPAY_STATUS, METHOD_WORDS } from "../onlinePayments";

type View = { methods: OnlineMethod[]; autopay: Autopay[] };

// The answer as the card reads it; one that isn't the tenant's view offers nothing.
export function readView(answer: unknown): View {
  const view = answer as Partial<View> | null;
  return view && Array.isArray(view.methods) && Array.isArray(view.autopay)
    ? (view as View)
    : { methods: [], autopay: [] };
}

// Autopay (migration 0159): the tenant saves a card or bank account with the payment processor
// for this lease, and what the lease owes is paid from it once its rent is due each month. One at
// a time; they stop it here. It shows once the company takes a method online.
export function AutopayCard({ leaseId, go = (url: string) => window.location.assign(url) }: {
  leaseId: string;
  go?: (url: string) => void;
}) {
  const [view, setView] = useState<View | null>(null);
  const [method, setMethod] = useState<OnlineMethod | "">("");
  const [error, setError] = useState("");
  const [said, setSaid] = useState("");
  const [asking, setAsking] = useState<Autopay | null>(null);
  const [runs, setRuns] = useState(0);
  useEffect(() => {
    const run = latest();
    api.get<unknown>("/api/tenant/autopay")
      .then(run.keep((answer: unknown) => {
        const found = readView(answer);
        setView(found);
        setMethod((chosen) => chosen || found.methods[0] || "");
      }))
      .catch(run.keep((why) => setError(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
  }, [leaseId, runs]);

  if (view === null) return error ? <Notice tone="error">{error}</Notice> : null;
  const mine = view.autopay.filter((a) => a.lease_id === leaseId);
  const current = mine.find((a) => a.status === "on" || a.status === "started") ?? null;
  if (view.methods.length === 0 && mine.length === 0) return null;
  return (
    <Card title="Autopay">
      <Said>{said}</Said>
      {current?.status === "on" ? (
        <div className="flex flex-col gap-2">
          <p className="text-base" data-testid="autopay">
            On: {current.label}. What the lease owes is paid from it once rent is due each month.
          </p>
          <div><Button variant="secondary" onClick={() => setAsking(current)}>Stop autopay</Button></div>
        </div>
      ) : view.methods.length > 0 ? (
        <Form className="flex flex-col gap-3" onSubmit={async (event) => {
          event.preventDefault();
          setError("");
          try {
            const started = await api.post<{ autopay_id: string; url: string }>(`/api/tenant/leases/${leaseId}/autopay`, { method });
            go(started.url);
          } catch (failed) {
            setError(failed instanceof ApiError ? failed.detail : String(failed));
          }
        }}>
          <div className="flex flex-wrap items-end gap-3">
            <Select label="Pay automatically by" id="autopay-method" value={method}
              onChange={(event) => setMethod(event.target.value as OnlineMethod)}>
              {view.methods.map((m) => <option key={m} value={m}>{METHOD_WORDS[m]}</option>)}
            </Select>
            <Button type="submit">Set up autopay</Button>
          </div>
          <p className="text-sm text-ink-muted">You save it on the payment processor's page. No fee; stop it here any time.</p>
        </Form>
      ) : null}
      <Notice tone="error">{error}</Notice>
      {mine.length > 0 && (
        <ul className="mt-4 flex flex-col divide-y divide-line text-sm" aria-label="Autopay history">
          {mine.map((a) => (
            <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span>{usDate(a.since.slice(0, 10))} · {a.label ?? METHOD_WORDS[a.method]}</span>
              <span className="flex items-center gap-2">
                <Badge tone={AUTOPAY_STATUS[a.status].tone}>{AUTOPAY_STATUS[a.status].label}</Badge>
                {a.reason && <span className="text-ink-muted">{a.reason}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      {asking && (
        <ConfirmDialog
          question={`Stop autopay from ${asking.label ?? METHOD_WORDS[asking.method]}? Nothing more is paid from it; you pay what the lease owes yourself.`}
          confirmLabel="Stop autopay"
          danger
          onCancel={() => setAsking(null)}
          onConfirm={async () => {
            const stopping = asking;
            setAsking(null);
            setError("");
            try {
              await api.post(`/api/tenant/autopay/${stopping.id}/stop`, {});
              setSaid("Autopay is stopped.");
              setRuns((n) => n + 1);
            } catch (failed) {
              setError(failed instanceof ApiError ? failed.detail : String(failed));
            }
          }}
        />
      )}
    </Card>
  );
}
