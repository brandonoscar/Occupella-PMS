import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { NO_HOW_TO_PAY } from "../payments";
import { useRecord } from "./useRecord";

// How to pay us (migration 20261104000400): the company's own words to tenants and owners about
// paying it, shown on their Pay online and Add money when it takes no payment online.
export type HowToPay = { body: string | null };
const PATH = "/api/company/how-to-pay";

export function HowToPayCard() {
  const [found, setFound] = useState<HowToPay | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    api.get<HowToPay>(PATH).then(setFound)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [runs]);

  return (
    <Card title="How to pay us">
      <Said>{said}</Said>
      {found === null ? (
        <Notice tone="info">{error || "Loading"}</Notice>
      ) : (
        <>
          {found.body === null ? (
            <p className="text-base text-ink-muted" data-testid="how-to-pay">
              Not set. Tenants and owners see "{NO_HOW_TO_PAY}"
            </p>
          ) : (
            <p className="whitespace-pre-line text-base text-ink" data-testid="how-to-pay">{found.body}</p>
          )}
          <div className="mt-3">
            <Button variant="secondary" onClick={() => setEditing(true)}>Edit how to pay us</Button>
          </div>
          {editing && (
            <SidePanel title="How to pay us" onClose={() => setEditing(false)}>
              <HowToPayForm body={found.body ?? ""} save={(body) =>
                record(PATH, body, () => "Saved how to pay us.").then((ok) => {
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

function HowToPayForm({ body, save }: { body: string; save: (body: unknown) => Promise<void> }) {
  const [text, setText] = useState(body);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({ body: text });
    }}>
      <label htmlFor="how-to-pay-body" className="flex flex-col gap-1.5 text-sm text-ink-muted">
        How to pay us
        <textarea id="how-to-pay-body" maxLength={2000} rows={5} value={text}
          placeholder="Checks payable to… Mail or drop off at…"
          onChange={(e) => setText(e.target.value)}
          className="rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink" />
      </label>
      <div>
        <Button type="submit" says="Tenants and owners see this when they can't pay online.">Save</Button>
      </div>
    </Form>
  );
}
