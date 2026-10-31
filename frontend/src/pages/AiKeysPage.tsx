import { Bot } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { usDate } from "../money";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

// An API key as the list shows it (src/pms/api/ai_keys.py): never its secret.
type ApiKey = {
  id: string;
  name: string;
  prefix: string;
  made_at: string;
  made_by: string;
  revoked_at: string | null;
};
type MadeKey = { id: string; name: string; prefix: string; secret: string };

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// Settings → AI (ADR 0011): the company's API keys. Software with a key, such as an AI assistant,
// reads the company's books through the PMS's API, as the AI's role, which can't move money or
// change a record. An admin makes a key in a side panel and sees its secret once; each standing
// key is taken back from its row's ⋯ menu, for good.
export function AiKeysPage({ me }: { me: Me }) {
  const admin = me.roles?.includes("admin") ?? false;
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [error, setError] = useState("");
  const [making, setMaking] = useState(false);
  const [said, setSaid] = useState("");
  const { error: refused, record } = useRecord(refresh, setSaid);

  function refresh() {
    if (!admin) return;
    api.get<ApiKey[]>("/api/ai/keys").then(setKeys).catch((failed) => setError(detail(failed)));
  }
  useEffect(refresh, [admin]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="AI"
        description="A key lets software, such as an AI assistant, read the company's books through the PMS's API. It reads only: it never moves money or changes a record."
        actions={admin && <Button onClick={() => setMaking(true)}>Make a key</Button>} />
      <Said>{said}</Said>
      <Notice tone="error">{refused || error}</Notice>
      {!admin ? (
        <Notice tone="info">Only an admin sees, makes and takes back API keys.</Notice>
      ) : keys === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : keys.length === 0 ? (
        <EmptyState icon={Bot} title="No API keys yet."
          body="Make a key for the software that should read the company's books; take it back when it should stop." />
      ) : (
        <Wide>
          <table className="w-full text-base" aria-label="API keys">
            <thead>
              <tr>
                <th className="text-left">Name</th>
                <th className="text-left">Key</th>
                <th className="text-left">Made</th>
                <th className="text-left">Made by</th>
                <th className="text-left">Status</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => (
                <tr key={key.id} aria-label={key.name}>
                  <td className="font-medium text-ink">{key.name}</td>
                  <td><span className="font-mono text-sm break-all text-ink-muted">{key.prefix}…</span></td>
                  <td className="text-ink-muted">{usDate(key.made_at)}</td>
                  <td className="text-ink-muted">{key.made_by}</td>
                  <td>
                    {key.revoked_at ? (
                      <span className="flex flex-wrap items-center gap-2">
                        <Badge tone="neutral">Taken back</Badge>
                        <span className="text-sm text-ink-muted">{usDate(key.revoked_at)}</span>
                      </span>
                    ) : (
                      <Badge tone="positive">Active</Badge>
                    )}
                  </td>
                  <td className="text-right">
                    {!key.revoked_at && (
                      <RowMenu label={key.name} actions={[{
                        label: "Take back", danger: true,
                        confirm: `Take back the key ${key.name}? Software using it is refused from its next request, and the key never works again.`,
                        onSelect: () => record(`/api/ai/keys/${key.id}/revoke`, {}, () => `Took back the key ${key.name}.`),
                      }]} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
      {making && (
        <MakeKeyPanel onClose={() => setMaking(false)}
          onMade={(key) => { setSaid(`Made the key ${key.name}.`); refresh(); }} />
      )}
    </Shell>
  );
}

// Make a key: its name, then its secret, shown this once (the PMS keeps only its SHA-256).
function MakeKeyPanel({ onClose, onMade }: { onClose: () => void; onMade: (key: MadeKey) => void }) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [made, setMade] = useState<MadeKey | null>(null);

  async function make() {
    setError("");
    try {
      const key = await api.post<MadeKey>("/api/ai/keys", { name });
      setMade(key);
      onMade(key);
    } catch (failed) {
      setError(detail(failed));
    }
  }

  return (
    <SidePanel title="Make a key" onClose={onClose}>
      {made === null ? (
        <Form className="flex flex-col gap-4" onSubmit={(event) => { event.preventDefault(); return make(); }}>
          <Field label="Name" id="ai-key-name" required maxLength={60} value={name}
            placeholder="Occupella AI" onChange={(event) => setName(event.target.value)} />
          <p className="text-sm text-ink-subtle">
            Name it after the software that will use it, so you know which key to take back.
          </p>
          <Notice tone="error">{error}</Notice>
          <div className="flex gap-2">
            <Button type="submit">Make key</Button>
            <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          </div>
        </Form>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-base text-ink">The key {made.name} is made.</p>
          <p className="text-sm text-ink-subtle">
            Shown once: copy it now and keep it where the software keeps its secrets. The PMS keeps
            only a fingerprint of it, so no one can show it again. Anyone holding it reads the
            company&apos;s books until it is taken back.
          </p>
          <input readOnly aria-label="Secret key" value={made.secret}
            className="w-full rounded-sm border border-line-strong bg-sunken px-2 py-1.5 font-mono text-sm break-all text-ink" />
          <div className="flex gap-2">
            <Button type="button" onClick={onClose}>Done</Button>
          </div>
        </div>
      )}
    </SidePanel>
  );
}
