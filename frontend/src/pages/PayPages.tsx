import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button, LinkButton } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card, PageHeader } from "../components/Shell";
import { money, usDate } from "../money";
import type { OnlineStatus } from "../payments";
import { STATUS } from "../payments";

type FakeCheckout = { amount: string; tenant_fee: string; total: string; memo: string; status: OnlineStatus };
type FakeSave = { tenant: string; memo: string; saved: boolean };
type FakeQueued = { id: string; memo: string; amount: string; tenant_fee: string; started_at: string };
type Outcome = "paid" | "failed" | "returned" | "expired";

// Where the payments partner sends the tenant back: paid (the bank's answer still to come for a
// bank payment) or cancelled.
export function PayDonePage() {
  const query = new URLSearchParams(window.location.search);
  const cancelled = query.has("cancelled");
  const autopay = query.has("autopay");
  const title = cancelled ? "Payment cancelled" : autopay ? "Autopay is set up" : "Payment on its way";
  return (
    <main className="mx-auto flex max-w-xl flex-col gap-4 p-6">
      <PageHeader title={title} />
      <Card title={cancelled ? "Nothing was paid" : "Thank you"}>
        <p className="mb-4 text-base" data-testid="pay-done">
          {cancelled
            ? "You left the payment page before paying. Nothing was taken from your account."
            : autopay
              ? "Your bank account is saved. Autopay pays on your rent's due day, from the next one, and emails you before each payment."
              : "Your bank takes a few business days with a bank payment. It shows on your lease once your bank pays it."}
        </p>
        <LinkButton href="/">Back to your lease</LinkButton>
      </Card>
    </main>
  );
}

// The fake payments partner's page (development and tests only, PMS_PAYMENTS=fake): what the
// tenant is paying, and buttons that say what the bank did, which the PMS reads as the partner's
// report. A real partner's page takes a bank account; this one takes nothing.
export function PayFakePage() {
  const payment = window.location.hash.slice(1);
  if (!payment) return <PayFakeQueue />;
  return <PayFakeCheckout payment={payment} />;
}

function PayFakeCheckout({ payment }: { payment: string }) {
  const [checkout, setCheckout] = useState<FakeCheckout | null>(null);
  const [error, setError] = useState("");
  const [runs, setRuns] = useState(0);
  const show = (failed: unknown) => setError(failed instanceof ApiError ? failed.detail : String(failed));
  useEffect(() => {
    api.get<FakeCheckout>(`/api/payments/fake/${encodeURIComponent(payment)}`).then(setCheckout).catch(show);
  }, [payment, runs]);

  async function bankSays(outcome: Outcome) {
    setError("");
    try {
      await api.post(`/api/payments/fake/${encodeURIComponent(payment)}`, { outcome });
      setRuns((n) => n + 1);
    } catch (failed) {
      show(failed);
    }
  }

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-4 p-6">
      <PageHeader title="Test payments partner" description="For development and tests only: no money moves." />
      <Notice tone="error">{error}</Notice>
      {checkout !== null && (
        <Card title={checkout.memo}>
          <p className="mb-2 text-base" data-testid="fake-total">
            {money(checkout.total)}
            {checkout.tenant_fee !== "0.00" && ` (${money(checkout.amount)} + ${money(checkout.tenant_fee)} fee)`}
          </p>
          <p className="mb-4">
            <Badge tone={STATUS[checkout.status].tone}>{STATUS[checkout.status].label}</Badge>
          </p>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => bankSays("paid")}>The bank paid it</Button>
            <Button variant="secondary" onClick={() => bankSays("failed")}>It didn't go through</Button>
            <Button variant="secondary" onClick={() => bankSays("returned")}>It came back</Button>
            <Button variant="secondary" onClick={() => bankSays("expired")}>Left without paying</Button>
            <LinkButton href="/pay/done?paid=1">Back to the portal</LinkButton>
          </div>
        </Card>
      )}
    </main>
  );
}

// The fake partner's payments nothing was reported of yet (an autopay's has no page of its own):
// each opens its page above.
function PayFakeQueue() {
  const [queued, setQueued] = useState<FakeQueued[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.get<FakeQueued[]>("/api/payments/fake").then(setQueued)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, []);
  return (
    <main className="mx-auto flex max-w-xl flex-col gap-4 p-6">
      <PageHeader title="Test payments partner" description="For development and tests only: no money moves." />
      <Notice tone="error">{error}</Notice>
      <Card title="Payments waiting for the bank">
        {queued !== null && queued.length === 0 && <p className="text-sm text-ink-subtle">None.</p>}
        <ul className="flex flex-col divide-y divide-line text-base" aria-label="Payments waiting for the bank">
          {(queued ?? []).map((q) => (
            <li key={q.id} className="py-2">
              <a className="font-medium text-iris hover:text-iris-hover hover:underline" href={`/pay/fake#${q.id}`}
                onClick={() => window.setTimeout(() => window.location.reload(), 0)}>
                {q.memo} · {money(q.amount)} · {usDate(q.started_at.slice(0, 10))}
              </a>
            </li>
          ))}
        </ul>
      </Card>
    </main>
  );
}

// The fake partner's page that saves a bank account for an autopay (development and tests only,
// PMS_PAYMENTS=fake): it saves a made-up one.
export function PayFakeSavePage() {
  const autopay = window.location.hash.slice(1);
  const [page, setPage] = useState<FakeSave | null>(null);
  const [error, setError] = useState("");
  const [runs, setRuns] = useState(0);
  const path = `/api/payments/fake-save/${encodeURIComponent(autopay)}`;
  useEffect(() => {
    api.get<FakeSave>(path).then(setPage)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [path, runs]);

  async function save() {
    setError("");
    try {
      await api.post(path, {});
      setRuns((n) => n + 1);
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
    }
  }

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-4 p-6">
      <PageHeader title="Test payments partner" description="For development and tests only: no money moves." />
      <Notice tone="error">{error}</Notice>
      {page !== null && (
        <Card title={`Autopay for ${page.memo}`}>
          <p className="mb-4 text-base" data-testid="fake-save">
            {page.saved ? "Saved: Test bank ••••0000." : `${page.tenant} saves a bank account to pay from.`}
          </p>
          <div className="flex flex-wrap gap-2">
            {!page.saved && <Button onClick={save}>Save this bank account</Button>}
            <LinkButton href="/pay/done?autopay=1">Back to the portal</LinkButton>
          </div>
        </Card>
      )}
    </main>
  );
}
