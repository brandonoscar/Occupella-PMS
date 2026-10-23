import { useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card, PageHeader, Shell } from "../components/Shell";
import { navigate } from "../router";

// The fake payment processor's checkout (migration 0158): on a server whose processor is the fake
// one (development and CI), no money moves. The tenant who started the payment says what happened,
// as the processor would, and goes back to their page.
export function FakeCheckoutPage({ me }: { me: Me }) {
  const reference = window.location.hash.slice(1);
  const [error, setError] = useState("");
  async function say(outcome: "succeeded" | "failed") {
    setError("");
    try {
      await api.post(`/api/tenant/online-payments/fake/${encodeURIComponent(reference)}`, { outcome });
      navigate("/");
    } catch (failed) {
      setError(failed instanceof ApiError ? failed.detail : String(failed));
    }
  }
  return (
    <Shell me={me}>
      <PageHeader title="Test checkout" description="The fake payment processor: no money moves." />
      <Card title="Your payment">
        <p className="text-base">Say what the payment processor would say of this payment.</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button onClick={() => void say("succeeded")}>Pay</Button>
          <Button variant="secondary" onClick={() => void say("failed")}>It didn't go through</Button>
        </div>
        <Notice tone="error">{error}</Notice>
      </Card>
    </Shell>
  );
}
