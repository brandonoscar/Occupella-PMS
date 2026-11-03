import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { money } from "../money";
import { useRecord } from "./useRecord";

// Over what amount an owner types their full name to approve a bill in the portal (migration
// 20261104000100): $5,000.00 until the company sets another.
export type OwnerApprovals = { sign_over: string };
const PATH = "/api/company/owner-approvals";

export function OwnerApprovalsCard() {
  const [settings, setSettings] = useState<OwnerApprovals | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    api.get<OwnerApprovals>(PATH).then(setSettings)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [runs]);

  return (
    <Card title="Owner approvals">
      <Said>{said}</Said>
      {settings === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <p className="text-base" data-testid="sign-over">
            Owners type their full name to approve bills over {money(settings.sign_over)}.
          </p>
          <div className="mt-3">
            <Button variant="secondary" onClick={() => setEditing(true)}>Edit owner approvals</Button>
          </div>
          {editing && (
            <SidePanel title="Owner approvals" onClose={() => setEditing(false)}>
              <OwnerApprovalsForm settings={settings} save={(body) =>
                record(PATH, body, () => "Saved owner approvals.").then((ok) => {
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

function OwnerApprovalsForm({ settings, save }: {
  settings: OwnerApprovals;
  save: (body: unknown) => Promise<void>;
}) {
  const [amount, setAmount] = useState(settings.sign_over);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({ sign_over: amount });
    }}>
      <Field label="Ask for the owner's typed name over" id="sign-over" type="number" min="0" step="0.01"
        required value={amount} onChange={(e) => setAmount(e.target.value)} />
      <div>
        <Button type="submit" says="Applies to bills owners approve from now on.">Save</Button>
      </div>
    </Form>
  );
}
