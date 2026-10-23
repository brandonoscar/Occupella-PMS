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
import type { OnlinePayment, OnlineSettings } from "../onlinePayments";
import { METHOD_WORDS, STATUS, takenLine } from "../onlinePayments";
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
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    api.get<unknown>(PATH).then((answer) => setView(readView(answer)))
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
