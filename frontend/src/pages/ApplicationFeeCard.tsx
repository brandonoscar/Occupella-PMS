import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { money, usDate } from "../money";
import { useRecord } from "./useRecord";

// The application fee (migration 0160): what each applicant pays when they apply, online through
// the payment processor, by the methods Online payments takes. It is the company's own fee; no
// state's cap is checked (docs/law.md).
export type FeeSettings = { per_applicant: string; recorded_by: string | null; recorded_at: string | null };

export const APPLICATION_FEE = "/api/leasing/application-fee";

const NONE: FeeSettings = { per_applicant: "0.00", recorded_by: null, recorded_at: null };

// The answer as the card reads it; one that isn't the settings reads as no fee set.
export function readSettings(answer: unknown): FeeSettings {
  const settings = answer as Partial<FeeSettings> | null;
  return settings && typeof settings.per_applicant === "string" ? (settings as FeeSettings) : NONE;
}

export function feeLine(settings: Pick<FeeSettings, "per_applicant">): string {
  return Number(settings.per_applicant) === 0
    ? "No application fee"
    : `${money(settings.per_applicant)} from each applicant`;
}

export function ApplicationFeeCard() {
  const [settings, setSettings] = useState<FeeSettings | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    api.get<unknown>(APPLICATION_FEE).then((answer) => setSettings(readSettings(answer)))
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [runs]);

  return (
    <Card title="Application fee">
      <Said>{said}</Said>
      {settings === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <p className="text-base" data-testid="application-fee-setting">{feeLine(settings)}</p>
          <p className="mt-1 text-sm text-ink-muted">
            {`Applicants pay it online from their application's status page, by the methods Online payments takes, and it is the company's fee in trust.${settings.recorded_at ? ` Set ${usDate(settings.recorded_at)}${settings.recorded_by ? ` by ${settings.recorded_by}` : ""}.` : ""} Some states cap it: the PMS checks no cap.`}
          </p>
          <div className="mt-3">
            <Button variant="secondary" onClick={() => setEditing(true)}>Edit application fee</Button>
          </div>
          {editing && (
            <SidePanel title="Application fee" onClose={() => setEditing(false)}>
              <FeeForm start={settings.per_applicant} save={(body) =>
                record<FeeSettings>(APPLICATION_FEE, body, (saved) => `Application fee: ${feeLine(saved)}.`).then((ok) => {
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

function FeeForm({ start, save }: { start: string; save: (body: { per_applicant: string }) => Promise<void> }) {
  const [amount, setAmount] = useState(start);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({ per_applicant: amount.trim() });
    }}>
      <Field label="Each applicant pays" id="application-fee-amount" inputMode="decimal" required value={amount}
        onChange={(e) => setAmount(e.target.value)} />
      <p className="text-sm text-ink-muted">0.00 asks no fee. Applications already in keep what they were asked.</p>
      <div><Button type="submit">Save</Button></div>
    </Form>
  );
}
