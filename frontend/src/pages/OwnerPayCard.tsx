import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { money, usDate } from "../money";
import type { OwnerPayView, Started } from "../payments";
import { STATUS, payable } from "../payments";

// An owner adds money to one of their property accounts online (src/pms/api/owner_payments.py):
// what they choose, starting with what brings the account back up to its reserve, on the payments
// partner's page, which takes their bank account; the PMS never sees it. No fee: the company pays
// the processing fee (the founder, 2026-11-02). Each payment started is listed with what the bank
// did; it reaches their statement once the bank paid it.

export function OwnerPayCard() {
  const [view, setView] = useState<OwnerPayView | null>(null);
  const [account, setAccount] = useState("");
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  const show = (failed: unknown) => setError(failed instanceof ApiError ? failed.detail : String(failed));

  useEffect(() => {
    api
      .get<OwnerPayView>("/api/payments/owner")
      .then((found) => {
        setView(found);
        const first = found.accounts.find((a) => a.short !== "0.00") ?? found.accounts[0];
        if (first) choose(first.account_id, found);
      })
      .catch(show);
  }, []);

  // A property chosen starts the amount at what brings its account back up to its reserve.
  function choose(id: string, from: OwnerPayView | null = view) {
    setAccount(id);
    const chosen = from?.accounts.find((a) => a.account_id === id);
    setAmount(chosen && chosen.short !== "0.00" ? chosen.short : "");
  }

  async function pay(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const started = await api.post<Started>("/api/payments/owner/checkout", {
        account_id: account,
        amount: amount.trim(),
      });
      window.location.assign(started.url);
    } catch (failed) {
      show(failed);
    }
  }

  const chosen = view?.accounts.find((a) => a.account_id === account);
  return (
    <Card title="Add money">
      <Notice tone="error">{error}</Notice>
      {view === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : !view.available ? (
        <p className="whitespace-pre-line text-base text-ink" data-testid="owner-pay-off">
          {view.how_to_pay ?? "Contact your property manager to send money."}
        </p>
      ) : view.accounts.length === 0 ? (
        <p className="text-base text-ink-muted">No property accounts are held for you in trust.</p>
      ) : (
        <Form className="flex flex-col gap-3" onSubmit={pay}>
          <p className="text-sm text-ink-muted">
            Pay into a property's account from your bank account on our payments partner's page; we never see your
            account number. There is no fee.
          </p>
          <Select label="Property" id="owner-pay-account" value={account} onChange={(e) => choose(e.target.value)}>
            {view.accounts.map((a) => (
              <option key={a.account_id} value={a.account_id}>{a.property}</option>
            ))}
          </Select>
          {chosen && (
            <p className="text-sm text-ink-muted" data-testid="owner-pay-holds">
              Holds {money(chosen.balance)}
              {chosen.reserve !== "0.00" && `, with a reserve of ${money(chosen.reserve)}`}
              {chosen.short !== "0.00" && `: ${money(chosen.short)} brings it back up`}.
            </p>
          )}
          <Field label="Amount" id="owner-pay-amount" inputMode="decimal" value={amount}
            onChange={(e) => setAmount(e.target.value)} />
          <div>
            <Button type="submit" disabled={!account || !payable(amount)}
              says="Takes you to our payments partner's page to pay by bank. Nothing is paid until you finish there.">
              Pay by bank
            </Button>
          </div>
        </Form>
      )}
      {view !== null && view.payments.length > 0 && (
        <ul className="mt-4 flex flex-col divide-y divide-line text-base" aria-label="Payments online">
          {view.payments.map((payment) => (
            <li key={payment.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span>
                {usDate(payment.started_at.slice(0, 10))} · {payment.property} · {money(payment.amount)}
              </span>
              <Badge tone={STATUS[payment.status].tone}>{STATUS[payment.status].label}</Badge>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
