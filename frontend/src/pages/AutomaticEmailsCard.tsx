import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { useRecord } from "./useRecord";

// The emails the PMS writes by itself (ADR 0007, its second step; src/pms/notices.py), each one
// the company turns on: rent reminders, late fee notices, monthly owner statements, work orders
// to vendors and adverse action notices to applicants. None is written while the server sends no
// email.
export type EmailSettings = {
  rent_reminder_days: number | null;
  late_notices: boolean;
  owner_statement_day: number | null;
  work_order_notices: boolean;
  adverse_action_notices: boolean;
  sending: boolean;
};

const ON_OFF = (on: boolean) => (on ? "On" : "Off");

export function ordinal(n: number): string {
  const teen = n % 100 >= 11 && n % 100 <= 13;
  const end = teen ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th";
  return `${n}${end}`;
}

// Each email the settings turn on, in words.
export function emailSettingsLines(settings: EmailSettings): [string, string][] {
  const days = settings.rent_reminder_days;
  const day = settings.owner_statement_day;
  return [
    ["Rent reminders to tenants", days === null ? "Off" : `${days} day${days === 1 ? "" : "s"} before the first of the month`],
    ["Late fee notices to tenants", ON_OFF(settings.late_notices)],
    ["Monthly statements to owners", day === null ? "Off" : `Last month's, from the ${ordinal(day)} of each month`],
    ["Work orders to vendors", ON_OFF(settings.work_order_notices)],
    ["Adverse action notices to applicants", ON_OFF(settings.adverse_action_notices)],
  ];
}

export function AutomaticEmailsCard() {
  const [settings, setSettings] = useState<EmailSettings | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    api.get<EmailSettings>("/api/emails/settings").then(setSettings)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [runs]);

  return (
    <Card title="Automatic emails">
      <Said>{said}</Said>
      {settings === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          {!settings.sending && (
            <Notice tone="info">This server sends no email yet: none of these is written until it does.</Notice>
          )}
          <dl className="grid grid-cols-1 gap-3 text-base sm:grid-cols-2" aria-label="Automatic emails">
            {emailSettingsLines(settings).map(([what, how]) => (
              <div key={what}>
                <dt className="text-sm text-ink-muted">{what}</dt>
                <dd>{how}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-3"><Button variant="secondary" onClick={() => setEditing(true)}>Edit automatic emails</Button></div>
          {editing && (
            <SidePanel title="Automatic emails" onClose={() => setEditing(false)}>
              <EmailSettingsForm settings={settings} save={(body) =>
                record("/api/emails/settings", body, () => "Saved the automatic emails.").then((ok) => { if (ok) setEditing(false); })} />
              <Notice tone="error">{refused}</Notice>
            </SidePanel>
          )}
        </>
      )}
    </Card>
  );
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (on: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm text-ink">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

const range = (from: number, to: number) => Array.from({ length: to - from + 1 }, (_, i) => from + i);

function EmailSettingsForm({ settings, save }: { settings: EmailSettings; save: (body: unknown) => Promise<void> }) {
  const [reminders, setReminders] = useState(settings.rent_reminder_days !== null);
  const [days, setDays] = useState(settings.rent_reminder_days ?? 5);
  const [late, setLate] = useState(settings.late_notices);
  const [statements, setStatements] = useState(settings.owner_statement_day !== null);
  const [day, setDay] = useState(settings.owner_statement_day ?? 5);
  const [workOrders, setWorkOrders] = useState(settings.work_order_notices);
  const [notices, setNotices] = useState(settings.adverse_action_notices);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({
        rent_reminder_days: reminders ? days : null,
        late_notices: late,
        owner_statement_day: statements ? day : null,
        work_order_notices: workOrders,
        adverse_action_notices: notices,
      });
    }}>
      <Check label="Rent reminders to tenants" checked={reminders} onChange={setReminders} />
      {reminders && (
        <Select label="Days before the first of the month" id="reminder-days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {range(1, 15).map((n) => <option key={n} value={n}>{n}</option>)}
        </Select>
      )}
      <Check label="Late fee notices to tenants" checked={late} onChange={setLate} />
      <Check label="Monthly statements to owners" checked={statements} onChange={setStatements} />
      {statements && (
        <Select label="Day of the month they go" id="statement-day" value={day} onChange={(e) => setDay(Number(e.target.value))}>
          {range(1, 28).map((n) => <option key={n} value={n}>{ordinal(n)}</option>)}
        </Select>
      )}
      <Check label="Work orders to vendors" checked={workOrders} onChange={setWorkOrders} />
      <Check label="Adverse action notices to applicants" checked={notices} onChange={setNotices} />
      <p className="text-sm text-ink-muted">
        A tenant or owner gets theirs at their contact email, else their portal login's; a vendor at their contact
        email; an applicant at the email they applied with. A lease under a payment hold gets no reminder or notice.
      </p>
      <div><Button type="submit">Save</Button></div>
    </Form>
  );
}
