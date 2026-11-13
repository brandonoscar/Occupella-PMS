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
import { NO_HOW_TO_PAY, PAUSED, STATUS, feeLine, payable } from "../payments";
import type { LeaseMethods, Method, MethodTerms } from "../connect";
import { CONNECT, METHOD_NAMES, methodLine } from "../connect";
import { Select } from "../components/Select";
import { leave } from "../router";

// A tenant pays a lease online (src/pms/api/online_payments.py): what they choose, the lease's
// balance to start with, on the payments partner's page, which takes their bank account; the PMS
// never sees it. The fee, when the tenant pays it, shows before they go. Each payment started is
// listed with what the bank did; it reaches the lease's payments once the bank paid it. Pay
// online waits for the fee's quote, and stays off when the quote can't be had (P-058, H-142): the
// tenant always sees the fee before going to the partner's page.
const NO_QUOTE =
  "We couldn't work out the processing fee, so you can't pay online just now. Try again in a few minutes.";

// Through Stripe Connect (docs/adr/0014-stripe-connect.md) the tenant picks a bank account or a card,
// each with its own fee shown before they go (a card fee only by credit card, capped, where the
// state allows it); the company's own keys take a bank payment alone, as before.
export function PayOnlineCard({ leaseId }: { leaseId: string }) {
  const [view, setView] = useState<PayView | null>(null);
  const [amount, setAmount] = useState("");
  // The fee's quote for the amount: none yet (or none needed), or "failed".
  const [quote, setQuote] = useState<(FeeQuote & { terms?: MethodTerms }) | "failed" | null>(null);
  // How the lease's payments go through Connect, if they do, and the way the tenant picked.
  const [connect, setConnect] = useState<LeaseMethods | null>(null);
  const [method, setMethod] = useState<Method>("ach");
  const [error, setError] = useState("");
  const show = (failed: unknown) => setError(failed instanceof ApiError ? failed.detail : String(failed));
  const viaConnect = connect?.connect === true;
  const tooMuch = viaConnect && method === "card" && payable(amount) && Number(amount) > Number(connect.card_max);

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
    api
      .get<LeaseMethods>(`${CONNECT}/leases/${encodeURIComponent(leaseId)}`)
      .then(run.keep(setConnect))
      .catch(run.keep(() => setConnect(null)));
    return run.stop;
  }, [leaseId]);

  useEffect(() => {
    const run = latest();
    if (!view?.available || !payable(amount)) {
      setQuote(null);
      return run.stop;
    }
    const query = new URLSearchParams({ amount: amount.trim() });
    setQuote(null); // a quote for another amount is no quote for this one
    const asked = viaConnect
      ? api.get<LeaseMethods>(`${CONNECT}/leases/${encodeURIComponent(leaseId)}?${query}`).then((found) => {
        const terms = found.methods.find((m) => m.method === method);
        if (!terms || terms.tenant_fee === null || terms.total === null) throw new Error("no quote");
        return { amount: amount.trim(), tenant_fee: terms.tenant_fee, total: terms.total, terms };
      })
      : api.get<FeeQuote>(`/api/payments/leases/${encodeURIComponent(leaseId)}/fee?${query}`);
    asked.then(run.keep(setQuote)).catch(run.keep(() => setQuote("failed")));
    return run.stop;
  }, [leaseId, amount, view?.available, viaConnect, method]);

  async function pay(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const started = viaConnect
        ? await api.post<Started>(`${CONNECT}/leases/${encodeURIComponent(leaseId)}/checkout`, {
          amount: amount.trim(),
          method,
        })
        : await api.post<Started>(`/api/payments/leases/${encodeURIComponent(leaseId)}/checkout`, {
          amount: amount.trim(),
        });
      leave(started.url);
    } catch (failed) {
      show(failed);
    }
  }

  // Off: the company's own words on how to pay it. Paused: a payment hold on the lease.
  const off = view !== null && !view.available && view.why_not !== "paused";
  return (
    <Card title={off ? "How to pay" : "Pay online"}>
      <Notice tone="error">{error}</Notice>
      {view === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : !view.available ? (
        <p className="whitespace-pre-line text-base text-ink" data-testid="pay-online-off">
          {off ? (view.how_to_pay ?? NO_HOW_TO_PAY) : PAUSED}
        </p>
      ) : (
        <Form className="flex flex-col gap-3" onSubmit={pay}>
          <p className="text-sm text-ink-muted">
            You owe {money(view.owed)}. You pay from your bank account on our payments partner's page; we never see
            your account number.
          </p>
          <Field label="Amount" id="pay-amount" inputMode="decimal" value={amount}
            onChange={(e) => setAmount(e.target.value)} />
          {viaConnect && (
            <Select label="Pay by" id="pay-method" value={method} onChange={(e) => setMethod(e.target.value as Method)}>
              {connect.methods.map((m) => (
                <option key={m.method} value={m.method}>{METHOD_NAMES[m.method]}</option>
              ))}
            </Select>
          )}
          <p className="text-base" data-testid="pay-fee">
            {quote === "failed"
              ? NO_QUOTE
              : tooMuch
                ? `A card payment is ${money(connect.card_max)} at most: pay more by bank.`
                : quote
                  ? quote.terms ? methodLine(quote.terms, amount.trim()) : feeLine(quote)
                  : " "}
          </p>
          {viaConnect && method === "card" && quote !== null && quote !== "failed" && quote.tenant_fee !== "0.00" && (
            <p className="text-sm text-ink-muted">Credit cards only: a debit or prepaid card can't carry a fee.</p>
          )}
          <div>
            <Button type="submit" disabled={!payable(amount) || quote === null || quote === "failed" || tooMuch}
              says={viaConnect && method === "card"
                ? "Takes you to our payments partner's page to pay by card. Nothing is paid until you finish there."
                : "Takes you to our payments partner's page to pay by bank. Nothing is paid until you finish there."}>
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
