import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { money, usDate } from "../money";
import type { Autopay, OnlinePayment, OnlineSettings } from "../onlinePayments";
import { AUTOPAY_STATUS, METHOD_WORDS, STATUS, takenLine } from "../onlinePayments";
import { Field } from "../components/Field";
import { RowMenu } from "../components/RowMenu";
import { useRecord } from "./useRecord";

type StaffView = { processor: "fake" | "stripe" | null; settings: OnlineSettings; payments: OnlinePayment[] };

const PATH = "/api/rent/online-payments";
const NONE: StaffView = {
  processor: null,
  settings: { cards: false, bank_debits: false, recorded_by: null, recorded_at: null },
  payments: [],
};

// The answer as the card reads it; one that isn't the staff view reads as nothing set up.
export function readView(answer: unknown): StaffView {
  const view = answer as Partial<StaffView> | null;
  return view && typeof view.settings === "object" && view.settings !== null && Array.isArray(view.payments)
    ? (view as StaffView)
    : NONE;
}

// Rent paid online (migration 0158): whether tenants may pay by card and by bank debit in the
// portal, and the latest online payments with how each stands. Offered only while the server has a
// payment processor; the processor bills its fees to the company, never to the money in trust.
export function OnlinePaymentsCard() {
  const [view, setView] = useState<StaffView | null>(null);
  const [autopay, setAutopay] = useState<Autopay[]>([]);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [stopping, setStopping] = useState<Autopay | null>(null);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    api.get<unknown>(PATH).then((answer) => setView(readView(answer)))
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
    api.get<unknown>("/api/rent/autopay").then((answer) => setAutopay(Array.isArray(answer) ? answer as Autopay[] : []))
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [runs]);

  return (
    <Card title="Online payments">
      <Said>{said}</Said>
      {view === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <p className="text-base" data-testid="online-payments">{takenLine(view.settings)}</p>
          <p className="mt-1 text-sm text-ink-muted">
            {view.processor === null
              ? "This server has no payment processor yet, so tenants aren't offered paying online."
              : `Tenants pay what their lease owes in the portal, through ${view.processor === "fake" ? "the test processor (no money moves)" : "Stripe"}. The processor bills its fees to the company; the payment is recorded once the money comes.`}
          </p>
          <div className="mt-3">
            <Button variant="secondary" onClick={() => setEditing(true)}>Edit online payments</Button>
          </div>
          {view.payments.length > 0 && (
            <Wide>
              <table className="mt-4 w-full text-base" aria-label="Online payments">
                <thead>
                  <tr>
                    <th className="text-left">Started</th>
                    <th className="text-left">Tenant</th>
                    <th className="text-left">Home</th>
                    <th className="text-left">By</th>
                    <th className="text-right">Amount</th>
                    <th className="text-left">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {view.payments.map((p) => (
                    <tr key={p.id}>
                      <td className="whitespace-nowrap">{usDate(p.started_at.slice(0, 10))}</td>
                      <td>{p.tenant}</td>
                      <td className="text-ink-muted">{p.home}</td>
                      <td>{METHOD_WORDS[p.method]}</td>
                      <td className="text-right">{money(p.amount)}</td>
                      <td>
                        <Badge tone={STATUS[p.status].tone}>{STATUS[p.status].label}</Badge>
                        {p.reason && <span className="ml-2 text-sm text-ink-muted">{p.reason}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Wide>
          )}
          {autopay.length > 0 && (
            <Wide>
              <table className="mt-4 w-full text-base" aria-label="Autopay">
                <thead>
                  <tr>
                    <th className="text-left">Since</th>
                    <th className="text-left">Tenant</th>
                    <th className="text-left">Home</th>
                    <th className="text-left">From</th>
                    <th className="text-left">Autopay</th>
                    <th><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {autopay.map((a) => (
                    <tr key={a.id}>
                      <td className="whitespace-nowrap">{usDate(a.since.slice(0, 10))}</td>
                      <td>{a.tenant}</td>
                      <td className="text-ink-muted">{a.home}</td>
                      <td>{a.label ?? METHOD_WORDS[a.method]}</td>
                      <td>
                        <Badge tone={AUTOPAY_STATUS[a.status].tone}>{AUTOPAY_STATUS[a.status].label}</Badge>
                        {a.reason && <span className="ml-2 text-sm text-ink-muted">{a.reason}</span>}
                      </td>
                      <td className="text-right">
                        {(a.status === "on" || a.status === "started") && (
                          <RowMenu label={`autopay of ${a.tenant}`} actions={[{ label: "Stop autopay", danger: true, onSelect: () => setStopping(a) }]} />
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Wide>
          )}
          {stopping && (
            <SidePanel title="Stop autopay" onClose={() => setStopping(null)}>
              <StopForm who={stopping.tenant} save={(reason) =>
                record(`/api/rent/autopay/${stopping.id}/stop`, { reason }, () => `Autopay of ${stopping.tenant} is stopped.`).then((ok) => {
                  if (ok) setStopping(null);
                })} />
              <Notice tone="error">{refused}</Notice>
            </SidePanel>
          )}
          {editing && (
            <SidePanel title="Online payments" onClose={() => setEditing(false)}>
              <SettingsForm start={view.settings} save={(body) =>
                record(`${PATH}/settings`, body, () => `Online payments: ${takenLine(body)}.`).then((ok) => {
                  if (ok) setEditing(false);
                })} />
              <Notice tone="error">{refused}</Notice>
            </SidePanel>
          )}
        </>
      )}
    </Card>
  );
}

function SettingsForm({ start, save }: {
  start: OnlineSettings;
  save: (body: { cards: boolean; bank_debits: boolean }) => Promise<void>;
}) {
  const [cards, setCards] = useState(start.cards);
  const [banks, setBanks] = useState(start.bank_debits);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({ cards, bank_debits: banks });
    }}>
      <label className="flex items-center gap-2 text-base">
        <input type="checkbox" checked={cards} onChange={(event) => setCards(event.target.checked)} />
        Tenants may pay by card
      </label>
      <label className="flex items-center gap-2 text-base">
        <input type="checkbox" checked={banks} onChange={(event) => setBanks(event.target.checked)} />
        Tenants may pay from a bank account (ACH debit)
      </label>
      <div><Button type="submit">Save</Button></div>
    </Form>
  );
}

function StopForm({ who, save }: { who: string; save: (reason: string) => Promise<void> }) {
  const [reason, setReason] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save(reason);
    }}>
      <p className="text-sm text-ink-muted">{`Nothing more is charged to ${who}'s saved card or account; they pay what the lease owes themselves.`}</p>
      <Field label="Why" id="autopay-stop-reason" required maxLength={500} value={reason} onChange={(event) => setReason(event.target.value)} />
      <div><Button type="submit" variant="danger">Stop autopay</Button></div>
    </Form>
  );
}
