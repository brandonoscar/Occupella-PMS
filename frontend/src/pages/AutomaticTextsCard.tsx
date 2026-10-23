import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import type { TextSettings } from "../texts";
import { useRecord } from "./useRecord";

// The texts the PMS writes by itself (migration 0161; src/pms/texts.py), each one the company turns
// on: a rent reminder some days before rent is due and a late notice when a late fee is charged,
// each only to a tenant who agreed to texts, in the afternoon. None while the server sends no texts.
export function textSettingsLines(settings: Pick<TextSettings, "rent_reminder_days" | "late_notices">): [string, string][] {
  const days = settings.rent_reminder_days;
  return [
    ["Rent reminders by text", days === null ? "Off" : `${days} day${days === 1 ? "" : "s"} before rent is due`],
    ["Late fee notices by text", settings.late_notices ? "On" : "Off"],
  ];
}

export function AutomaticTextsCard() {
  const [settings, setSettings] = useState<TextSettings | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    api.get<TextSettings>("/api/texts/settings").then(setSettings)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [runs]);

  return (
    <Card title="Automatic texts">
      <Said>{said}</Said>
      {settings === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          {!settings.sending && (
            <Notice tone="info">This server sends no texts yet: none of these is written until it does.</Notice>
          )}
          <dl className="grid grid-cols-1 gap-3 text-base sm:grid-cols-2" aria-label="Automatic texts">
            {textSettingsLines(settings).map(([what, how]) => (
              <div key={what}>
                <dt className="text-sm text-ink-muted">{what}</dt>
                <dd>{how}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-sm text-ink-muted">
            Only to tenants who agreed to texts, each with the company's name and how to stop, held until the afternoon (US time).
          </p>
          <div className="mt-3"><Button variant="secondary" onClick={() => setEditing(true)}>Edit automatic texts</Button></div>
          {editing && (
            <SidePanel title="Automatic texts" onClose={() => setEditing(false)}>
              <TextSettingsForm settings={settings} save={(body) =>
                record("/api/texts/settings", body, () => "Saved the automatic texts.").then((ok) => { if (ok) setEditing(false); })} />
              <Notice tone="error">{refused}</Notice>
            </SidePanel>
          )}
        </>
      )}
    </Card>
  );
}

function TextSettingsForm({ settings, save }: { settings: TextSettings; save: (body: unknown) => Promise<void> }) {
  const [reminders, setReminders] = useState(settings.rent_reminder_days !== null);
  const [days, setDays] = useState(settings.rent_reminder_days ?? 3);
  const [late, setLate] = useState(settings.late_notices);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({ rent_reminder_days: reminders ? days : null, late_notices: late });
    }}>
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={reminders} onChange={(e) => setReminders(e.target.checked)} />
        Rent reminders by text
      </label>
      {reminders && (
        <Select label="Days before rent is due" id="text-reminder-days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {Array.from({ length: 15 }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{n}</option>)}
        </Select>
      )}
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={late} onChange={(e) => setLate(e.target.checked)} />
        Late fee notices by text
      </label>
      <div><Button type="submit">Save</Button></div>
    </Form>
  );
}
