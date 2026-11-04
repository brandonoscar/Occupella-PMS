import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { usDate } from "../money";
import { useRecord } from "./useRecord";

const PAYMENT_KEYS = "/api/payments/keys";

export type PaymentKeys = {
  using: "own" | "server" | "none" | "unreadable";
  server_partner: string | null;
  mode: "test" | "live" | null;
  ends_in: string | null;
  webhook_url: string;
  changed_at: string | null;
  changed_by: string | null;
  can_keep: boolean;
  may_change: boolean;
};

// What the company's payments go through now, in a line.
export function keysLine(keys: PaymentKeys): string {
  switch (keys.using) {
    case "own":
      return `Your Stripe account, key ending in ${keys.ends_in}`;
    case "server":
      return keys.server_partner === "fake" ? "Using the test payments partner." : "Using this server's Stripe account.";
    case "unreadable":
      return "Your keys can't be read on this server. Enter them again.";
    default:
      return "Not set. Tenants and owners can't pay online.";
  }
}

// Company setup: the company's own Stripe keys (the founder's review of 11/03, item 4). An admin
// enters them; the server keeps them sealed and only ever shows the last four.
export function PaymentKeysCard() {
  const [keys, setKeys] = useState<PaymentKeys | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [runs, setRuns] = useState(0);
  const refresh = () => setRuns((n) => n + 1);
  const { said, error: refused, record } = useRecord(refresh);
  useEffect(() => {
    api.get<PaymentKeys>(PAYMENT_KEYS).then(setKeys)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [runs]);

  return (
    <Card title="Stripe keys">
      <Said>{said}</Said>
      {keys === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          {keys.using === "unreadable" && <Notice tone="error">{keysLine(keys)}</Notice>}
          <div className="flex flex-wrap items-center gap-2">
            {keys.using !== "unreadable" && <p className="text-base text-ink" data-testid="payment-keys">{keysLine(keys)}</p>}
            {keys.using === "own" && keys.mode !== null && (
              <Badge tone={keys.mode === "live" ? "positive" : "caution"}>{keys.mode === "live" ? "Live" : "Test"}</Badge>
            )}
            {(keys.using === "own" || keys.using === "unreadable") && keys.may_change && (
              <RowMenu label="Stripe keys" actions={[{
                label: "Remove keys",
                danger: true,
                confirm: keys.server_partner
                  ? "Remove your Stripe keys? Payments go back to this server's account."
                  : "Remove your Stripe keys? Tenants and owners can't pay online until you add them again.",
                onSelect: () => record(`${PAYMENT_KEYS}/remove`, {}, () => "Removed your Stripe keys."),
              }]} />
            )}
          </div>
          {keys.changed_at && (
            <p className="mt-1 text-sm text-ink-muted">
              Changed {usDate(keys.changed_at.slice(0, 10))} by {keys.changed_by}.
            </p>
          )}
          <dl className="mt-3 text-sm">
            <dt className="text-ink-muted">Webhook URL</dt>
            <dd className="break-all font-mono text-ink" data-testid="webhook-url">{keys.webhook_url}</dd>
          </dl>
          <p className="mt-1 text-sm text-ink-muted">In Stripe, add this as a webhook endpoint.</p>
          {!keys.can_keep && <Notice tone="info">This server can't keep keys. Ask whoever runs it to set PMS_DATA_KEY.</Notice>}
          {keys.may_change && keys.can_keep && (
            <div className="mt-3">
              <Button variant="secondary" onClick={() => setEditing(true)}>
                {keys.using === "own" ? "Replace keys" : "Enter keys"}
              </Button>
            </div>
          )}
          {editing && (
            <SidePanel title="Stripe keys" onClose={() => setEditing(false)}>
              <KeysForm save={(body) =>
                record(PAYMENT_KEYS, body, () => "Saved your Stripe keys.").then((ok) => {
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

function KeysForm({ save }: { save: (body: unknown) => Promise<void> }) {
  const [secretKey, setSecretKey] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({ secret_key: secretKey, webhook_secret: webhookSecret });
    }}>
      <Field label="Secret key" id="stripe-secret-key" type="password" autoComplete="off" required
        placeholder="sk_live_…" value={secretKey} onChange={(e) => setSecretKey(e.target.value)} />
      <Field label="Webhook signing secret" id="stripe-webhook-secret" type="password" autoComplete="off" required
        placeholder="whsec_…" value={webhookSecret} onChange={(e) => setWebhookSecret(e.target.value)} />
      <div>
        <Button type="submit">Save keys</Button>
      </div>
    </Form>
  );
}
