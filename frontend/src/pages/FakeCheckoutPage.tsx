import { useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card, Wordmark } from "../components/Shell";
import { navigate } from "../router";

// The fake payment processor's checkout (migration 0158): on a server whose processor is the fake
// one (development and CI), no money moves. Whoever started the payment says what happened, as the
// processor would, and goes back: a tenant to their page, an applicant to their application's
// (migration 0160), whom no login lets in, so the page asks none.
export function FakeCheckoutPage() {
  const reference = window.location.hash.slice(1);
  // A reference from setting autopay up saves a card or account (migration 0159), one from an
  // application pays its fee; any other pays rent.
  const saving = reference.startsWith("fake_setup_");
  const fee = reference.startsWith("fake_fee_");
  const [error, setError] = useState("");
  async function say(outcome: string) {
    setError("");
    const path = saving
      ? "/api/tenant/autopay/fake/"
      : fee
        ? "/api/application/fee/fake/"
        : "/api/tenant/online-payments/fake/";
    try {
      await api.post(`${path}${encodeURIComponent(reference)}`, { outcome });
      navigate(fee ? "/application" : "/");
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
    }
  }
  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-10">
      <Wordmark />
      <div>
        <h1 className="font-display text-xl text-ink">Test checkout</h1>
        <p className="mt-1 text-base text-ink-muted">The fake payment processor: no money moves.</p>
      </div>
      <Card title={saving ? "Your autopay" : fee ? "Your application fee" : "Your payment"}>
        <p className="text-base">Say what the payment processor would say of this {saving ? "card or account" : "payment"}.</p>
        <div className="mt-3 flex flex-wrap gap-2">
          {saving ? (
            <>
              <Button onClick={() => say("saved")}>Save</Button>
              <Button variant="secondary" onClick={() => say("not saved")}>It wasn't saved</Button>
            </>
          ) : (
            <>
              <Button onClick={() => say("succeeded")}>Pay</Button>
              <Button variant="secondary" onClick={() => say("failed")}>It didn't go through</Button>
            </>
          )}
        </div>
        <Notice tone="error">{error}</Notice>
      </Card>
    </div>
  );
}
