import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button, LinkButton } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card, PageHeader } from "../components/Shell";
import { money } from "../money";
import type { OnlineStatus } from "../payments";
import { STATUS } from "../payments";

type FakeCheckout = { amount: string; tenant_fee: string; total: string; memo: string; status: OnlineStatus };
type Outcome = "paid" | "failed" | "returned" | "expired";

// Where the payments partner sends the tenant back: paid (the bank's answer still to come for a
// bank payment) or cancelled.
export function PayDonePage() {
  const cancelled = new URLSearchParams(window.location.search).has("cancelled");
  return (
    <main className="mx-auto flex max-w-xl flex-col gap-4 p-6">
      <PageHeader title={cancelled ? "Payment cancelled" : "Payment on its way"} />
      <Card title={cancelled ? "Nothing was paid" : "Thank you"}>
        <p className="mb-4 text-base" data-testid="pay-done">
          {cancelled
            ? "You left the payment page before paying. Nothing was taken from your account."
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
