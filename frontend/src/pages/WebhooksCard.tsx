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
import { Wide } from "../components/Wide";
import { usDate } from "../money";
import { useRecord } from "./useRecord";

// A webhook as the list shows it (src/pms/api/events.py): never its secret.
type Webhook = {
  id: string;
  url: string;
  last4: string;
  made_at: string;
  made_by: string;
  revoked_at: string | null;
  last_attempt_at: string | null;
  last_error: string | null;
};
type MadeWebhook = { id: string; url: string; secret: string };

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// How a webhook's deliveries go: none yet, the latest went, or why the latest failed.
function delivery(hook: Webhook): React.ReactNode {
  if (hook.last_attempt_at === null) return <span className="text-ink-muted">None yet</span>;
  if (hook.last_error === null) return <Badge tone="positive">Delivering</Badge>;
  return (
    <span className="flex flex-col gap-1">
      <Badge tone="danger">Failing</Badge>
      <span className="text-sm text-ink-muted">{hook.last_error}</span>
    </span>
  );
}

// Settings → AI, Webhooks (ADR 0011): the addresses each event of the company goes to, as it
// happens, signed with each webhook's secret. An event is its kind and ids only (a payment
// recorded, a work order opened, a draft decided); the receiver reads the rest with its API key.
// An admin adds one in a side panel and sees its secret once, and takes one back from its ⋯ menu.
export function WebhooksCard() {
  const [hooks, setHooks] = useState<Webhook[] | null>(null);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);
  const [said, setSaid] = useState("");
  const { error: refused, record } = useRecord(refresh, setSaid);

  function refresh() {
    api.get<Webhook[]>("/api/ai/webhooks").then(setHooks).catch((failed) => setError(detail(failed)));
  }
  useEffect(refresh, []);

  return (
    <Card title="Webhooks">
      <Said>{said}</Said>
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <p className="max-w-prose text-sm text-ink-muted">
            Each event in the company&apos;s books, such as a payment recorded, a work order opened
            or a draft decided, is sent to these addresses as it happens, signed with the
            webhook&apos;s secret. An event carries its kind and ids only.
          </p>
          <Button variant="secondary" onClick={() => setAdding(true)}>Add webhook</Button>
        </div>
        <Notice tone="error">{refused || error}</Notice>
        {hooks !== null && hooks.length === 0 && (
          <p className="text-sm text-ink-muted">No webhooks yet. Software can also read events with its key.</p>
        )}
        {hooks !== null && hooks.length > 0 && (
          <Wide>
            <table className="w-full text-base" aria-label="Webhooks">
              <thead>
                <tr>
                  <th className="text-left">Address</th>
                  <th className="text-left">Secret</th>
                  <th className="text-left">Added</th>
                  <th className="text-left">Status</th>
                  <th className="text-left">Deliveries</th>
                  <th><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {hooks.map((hook) => (
                  <tr key={hook.id} aria-label={hook.url}>
                    <td><span className="font-mono text-sm break-all text-ink">{hook.url}</span></td>
                    <td><span className="font-mono text-sm break-all text-ink-muted">…{hook.last4}</span></td>
                    <td className="text-ink-muted">{usDate(hook.made_at)} by {hook.made_by}</td>
                    <td>
                      {hook.revoked_at ? <Badge tone="neutral">Taken back</Badge> : <Badge tone="positive">Active</Badge>}
                    </td>
                    <td>{hook.revoked_at ? "" : delivery(hook)}</td>
                    <td className="text-right">
                      {!hook.revoked_at && (
                        <RowMenu label={hook.url} actions={[{
                          label: "Take back", danger: true,
                          confirm: `Take back the webhook to ${hook.url}? No event is sent to it from now on.`,
                          onSelect: () => record(`/api/ai/webhooks/${hook.id}/revoke`, {}, () => `Took back the webhook to ${hook.url}.`),
                        }]} />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
        )}
      </div>
      {adding && (
        <AddWebhookPanel onClose={() => setAdding(false)}
          onMade={(hook) => { setSaid(`Added the webhook to ${hook.url}.`); refresh(); }} />
      )}
    </Card>
  );
}

// Add a webhook: its address, then its secret, shown this once (the PMS keeps it sealed).
function AddWebhookPanel({ onClose, onMade }: { onClose: () => void; onMade: (hook: MadeWebhook) => void }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [made, setMade] = useState<MadeWebhook | null>(null);

  async function add() {
    setError("");
    try {
      const hook = await api.post<MadeWebhook>("/api/ai/webhooks", { url });
      setMade(hook);
      onMade(hook);
    } catch (failed) {
      setError(detail(failed));
    }
  }

  return (
    <SidePanel title="Add webhook" onClose={onClose}>
      {made === null ? (
        <Form className="flex flex-col gap-4" onSubmit={(event) => { event.preventDefault(); return add(); }}>
          <Field label="Address" id="webhook-url" type="url" required maxLength={500} value={url}
            placeholder="https://hooks.example.com/occupella" onChange={(event) => setUrl(event.target.value)} />
          <p className="text-sm text-ink-subtle">
            An https address on the public internet. Events go to it from now on, each signed with
            a secret shown once.
          </p>
          <Notice tone="error">{error}</Notice>
          <div className="flex gap-2">
            <Button type="submit">Add webhook</Button>
            <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          </div>
        </Form>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-base text-ink">The webhook to {made.url} is added.</p>
          <p className="text-sm text-ink-subtle">
            Shown once: copy it now into the receiving software, which checks each event&apos;s
            Occupella-Signature with it. The PMS keeps it sealed and never shows it again.
          </p>
          <input readOnly aria-label="Signing secret" value={made.secret}
            className="w-full rounded-sm border border-line-strong bg-sunken px-2 py-1.5 font-mono text-sm break-all text-ink" />
          <div className="flex gap-2">
            <Button type="button" onClick={onClose}>Done</Button>
          </div>
        </div>
      )}
    </SidePanel>
  );
}
