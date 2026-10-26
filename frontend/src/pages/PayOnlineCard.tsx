import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import { latest } from "../latest";
import { money, usDate } from "../money";
import type { FeeQuote, PayView, Started } from "../payments";
import { STATUS, feeLine, payable } from "../payments";

// A tenant pays a lease online (src/pms/api/online_payments.py): what they choose, the lease's
// balance to start with, on the payments partner's page, which takes their bank account; the PMS
// never sees it. The fee, when the tenant pays it, shows before they go. Each payment started is
// listed with what the bank did; it reaches the lease's payments once the bank paid it.
export function PayOnlineCard({ leaseId }: { leaseId: string }) {
  const [view, setView] = useState<PayView | null>(null);
  const [amount, setAmount] = useState("");
  const [quote, setQuote] = useState<FeeQuote | null>(null);
  const [error, setError] = useState("");
  const show = (failed: unknown) => setError(failed instanceof ApiError ? failed.detail : String(failed));

  useEffect(() => {
    const run = latest();
    setView(null);
    api
      .get<PayView>(`/api/payments/leases/${encodeURIComponent(leaseId)}`)
      .then(
        run.keep((found: PayView) => {
          setView(found);
          setAmount(found.owed === "0.00" ? "" : found.owed);
        }),
      )
      .catch(run.keep(show));
    return run.stop;
  }, [leaseId]);

  useEffect(() => {
    const run = latest();
    if (!view?.available || !payable(amount)) {
      setQuote(null);
      return run.stop;
    }
    const query = new URLSearchParams({ amount: amount.trim() });
    api
      .get<FeeQuote>(`/api/payments/leases/${encodeURIComponent(leaseId)}/fee?${query}`)
      .then(run.keep(setQuote))
      .catch(run.keep(() => setQuote(null)));
    return run.stop;
  }, [leaseId, amount, view?.available]);

  async function pay(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const started = await api.post<Started>(`/api/payments/leases/${encodeURIComponent(leaseId)}/checkout`, {
        amount: amount.trim(),
      });
      window.location.assign(started.url);
    } catch (failed) {
      show(failed);
    }
  }

  return (
    <Card title="Pay online">
      <Notice tone="error">{error}</Notice>
      {view === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : !view.available ? (
        <p className="text-base text-ink-muted" data-testid="pay-online-off">{view.why_not}</p>
      ) : (
        <Form className="flex flex-col gap-3" onSubmit={pay}>
          <p className="text-sm text-ink-muted">
            You owe {money(view.owed)}. You pay from your bank account on our payments partner's page; we never see
            your account number.
          </p>
          <Field label="Amount" id="pay-amount" inputMode="decimal" value={amount}
            onChange={(e) => setAmount(e.target.value)} />
          <p className="text-base" data-testid="pay-fee">{quote ? feeLine(quote) : " "}</p>
          <div>
            <Button type="submit" disabled={!payable(amount)}
              says="Takes you to our payments partner's page to pay by bank. Nothing is paid until you finish there.">
              Pay online
            </Button>
          </div>
        </Form>
      )}
      {view !== null && view.payments.length > 0 && (
        <ul className="mt-4 flex flex-col divide-y divide-line text-base" aria-label="Online payments">
          {view.payments.map((payment) => (
            <li key={payment.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span>
                {usDate(payment.started_at.slice(0, 10))} · {money(payment.amount)}
                {payment.tenant_fee !== "0.00" && (
                  <span className="text-ink-muted"> + {money(payment.tenant_fee)} fee</span>
                )}
              </span>
              <Badge tone={STATUS[payment.status].tone}>{STATUS[payment.status].label}</Badge>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
