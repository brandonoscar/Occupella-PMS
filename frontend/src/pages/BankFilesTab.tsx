import { useEffect, useState } from "react";
import { FileText } from "lucide-react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { ConfirmDialog } from "../components/Confirm";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { latest } from "../latest";
import { day, money, sumMoney, usDate } from "../money";
import { GoLink } from "./NeedsAttention";
import { useRecord } from "./useRecord";

// Bank files (migration 0156, the founder 2026-10-23): the payouts staff recorded as sent by EFT
// (owner draws and bills paid) go to the bank in an ACH file. Staff keep the account's ACH settings
// from its bank, pick the EFT payouts of the last 30 days that wait for a file, make one for a
// weekday, download it to upload to the bank, and void one the bank didn't take, which frees its
// payouts. Making, downloading and voiding take disburse (the server says so if it is missing).

export type AchSettings = {
  bank_routing: string;
  bank_name: string;
  origin: string;
  company_name: string;
  company_id: string;
  recorded_by: string | null;
  recorded_at: string;
};

export type Payout = {
  transfer_id: string;
  paid_on: string;
  payee: "vendor" | "owner";
  payee_id: string;
  name: string;
  what: string;
  amount: string;
  bank_account: string | null;
};

export type BankFile = {
  id: string;
  made_at: string;
  made_by: string | null;
  modifier: string;
  effective_on: string;
  entries: number;
  total: string;
  voided: { reason: string; by: string | null; at: string } | null;
  items: { name: string; what: string; amount: string; bank_account: string }[];
};

type BankFiles = { settings: AchSettings | null; waiting: Payout[]; files: BankFile[] };

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// The first weekday after a day: the soonest a file's payees' banks settle it.
export function nextWeekday(from: Date): string {
  const next = new Date(Date.UTC(from.getUTCFullYear(), from.getUTCMonth(), from.getUTCDate() + 1));
  while (next.getUTCDay() === 0 || next.getUTCDay() === 6) next.setUTCDate(next.getUTCDate() + 1);
  return day(next);
}

// "Demo Trust Bank, routing 001000009; Demo Property Co, company ID 1000000001, origin 1000000001"
export function settingsLine(s: AchSettings): string {
  return `${s.bank_name}, routing ${s.bank_routing}; ${s.company_name}, company ID ${s.company_id}, origin ${s.origin}`;
}

export function BankFilesTab({ bankId }: { bankId: string }) {
  const [found, setFound] = useState<BankFiles | null>(null);
  const [unread, setUnread] = useState("");
  const [runs, setRuns] = useState(0);
  const [chosen, setChosen] = useState<string[]>([]);
  const [effective, setEffective] = useState(nextWeekday(new Date()));
  const [asking, setAsking] = useState(false);
  const [editing, setEditing] = useState(false);
  const [voiding, setVoiding] = useState<BankFile | null>(null);
  const { said, error, record } = useRecord(() => setRuns((n) => n + 1));

  useEffect(() => {
    const run = latest();
    api.get<BankFiles>(`/api/bank-files/${bankId}`)
      .then(run.keep(setFound))
      .catch(run.keep((why) => setUnread(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
  }, [bankId, runs]);

  if (found === null) return <Notice tone={unread ? "error" : "info"}>{unread || "Loading"}</Notice>;
  const picked = found.waiting.filter((p) => chosen.includes(p.transfer_id));
  const total = sumMoney(picked.map((p) => p.amount));
  const toggle = (id: string) => setChosen(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id]);
  const payees = new Set(picked.map((p) => `${p.payee}:${p.payee_id}`)).size;

  return (
    <div className="flex flex-col gap-6">
      <Said>{said}</Said>
      {!editing && !voiding && <Notice tone="error">{error}</Notice>}
      <Card title="ACH settings">
        <div role="group" aria-label="ACH settings" className="flex flex-col gap-3">
          {found.settings === null ? (
            <p className="text-base text-ink-muted">No ACH settings yet. Add the details your bank gives for ACH files to make one.</p>
          ) : (
            <>
              <p className="text-base" data-testid="ach-settings">{settingsLine(found.settings)}</p>
              <p className="text-sm text-ink-muted">
                Set {usDate(found.settings.recorded_at.slice(0, 10))}
                {found.settings.recorded_by ? ` by ${found.settings.recorded_by}` : ""}.
              </p>
            </>
          )}
          <div>
            <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
              {found.settings === null ? "Add ACH settings" : "Change ACH settings"}
            </Button>
          </div>
        </div>
      </Card>
      <Card title="EFT payouts waiting for a bank file">
        {found.waiting.length === 0 ? (
          <p className="text-sm text-ink-subtle">No EFT payout of the last 30 days waits for a bank file.</p>
        ) : (
          <Form className="flex flex-col gap-3" onSubmit={(event) => {
            event.preventDefault();
            setAsking(true);
          }}>
            <ul className="flex flex-col divide-y divide-line rounded-md border border-card-edge text-sm" aria-label="EFT payouts waiting">
              {found.waiting.map((p) => (
                <li key={p.transfer_id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                  <label className="flex items-center gap-2.5">
                    <input type="checkbox" checked={chosen.includes(p.transfer_id)} disabled={p.bank_account === null}
                      onChange={() => toggle(p.transfer_id)} />
                    {`${usDate(p.paid_on)} ${p.what}, ${p.name}: ${money(p.amount)}`}
                  </label>
                  {p.bank_account === null ? (
                    <GoLink href={`/${p.payee === "vendor" ? "vendors" : "owners"}/${p.payee_id}`} className={LINK}>
                      No bank account on file: add one
                    </GoLink>
                  ) : (
                    <span className="text-ink-muted">{p.bank_account}</span>
                  )}
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Effective day" id={`effective-${bankId}`} type="date" required value={effective}
                onChange={(event) => setEffective(event.target.value)} />
              <Button type="submit" disabled={picked.length === 0 || found.settings === null}>
                {picked.length === 0 ? "Make bank file" : `Make bank file of ${money(total)}`}
              </Button>
            </div>
            {found.settings === null && <p className="text-sm text-ink-muted">Add the ACH settings first.</p>}
          </Form>
        )}
      </Card>
      <Card title="Bank files">
        {found.files.length === 0 ? (
          <EmptyState icon={FileText} title="No bank file made yet." />
        ) : (
          <Wide>
            <table className="w-full text-base" aria-label="Bank files">
              <thead>
                <tr>
                  <th className="text-left">Made</th>
                  <th className="text-left">File</th>
                  <th className="text-left">Effective</th>
                  <th className="text-right">Payouts</th>
                  <th className="text-right">Total</th>
                  <th className="text-left">Made by</th>
                  <th className="text-left">Status</th>
                  <th><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {found.files.map((f) => (
                  <tr key={f.id} aria-label={`File ${f.modifier} of ${usDate(f.made_at.slice(0, 10))}`}>
                    <td className="whitespace-nowrap">{usDate(f.made_at.slice(0, 10))}</td>
                    <td>{f.modifier}</td>
                    <td className="whitespace-nowrap">{usDate(f.effective_on)}</td>
                    <td className="text-right">{f.entries}</td>
                    <td className="text-right">{money(f.total)}</td>
                    <td className="text-ink-muted">{f.made_by}</td>
                    <td>
                      {f.voided ? <Badge tone="neutral">Void: {f.voided.reason}</Badge> : <Badge tone="positive">Made</Badge>}
                    </td>
                    <td className="text-right whitespace-nowrap">
                      {!f.voided && (
                        <span className="inline-flex items-center gap-2">
                          <a href={`/api/bank-files/files/${f.id}.ach`} className={LINK}>Download</a>
                          <RowMenu label={`file ${f.modifier}`} actions={[
                            { label: "Void", danger: true, onSelect: () => setVoiding(f) },
                          ]} />
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
        )}
      </Card>
      {asking && (
        <ConfirmDialog
          question={`Make a bank file paying ${money(total)} to ${payees} ${payees === 1 ? "payee" : "payees"}, effective ${usDate(effective)}? Upload it to your bank to send the money.`}
          confirmLabel="Make bank file"
          onCancel={() => setAsking(false)}
          onConfirm={() => {
            setAsking(false);
            void record<BankFile>(`/api/bank-files/${bankId}`, { effective_on: effective, transfer_ids: picked.map((p) => p.transfer_id) },
              (made) => `Bank file ${made.modifier} made: ${made.entries} ${made.entries === 1 ? "payout" : "payouts"}, ${money(made.total)}.`)
              .then((done) => done && setChosen([]));
          }}
        />
      )}
      {editing && (
        <SettingsPanel start={found.settings} error={error} onClose={() => setEditing(false)}
          save={async (body) => {
            if (await record<AchSettings>(`/api/bank-files/${bankId}/settings`, body, () => "ACH settings saved.")) setEditing(false);
          }} />
      )}
      {voiding && (
        <SidePanel title={`Void bank file ${voiding.modifier}`} onClose={() => setVoiding(null)}>
          <VoidForm file={voiding} error={error} save={async (reason) => {
            if (await record(`/api/bank-files/files/${voiding.id}/void`, { reason }, () => `Bank file ${voiding.modifier} is void; its payouts wait for another.`)) {
              setVoiding(null);
            }
          }} />
        </SidePanel>
      )}
    </div>
  );
}

type NewSettings = Omit<AchSettings, "recorded_by" | "recorded_at">;

function SettingsPanel({ start, error, onClose, save }: {
  start: AchSettings | null;
  error: string;
  onClose: () => void;
  save: (body: NewSettings) => Promise<void>;
}) {
  const [form, setForm] = useState<NewSettings>({
    bank_routing: start?.bank_routing ?? "",
    bank_name: start?.bank_name ?? "",
    origin: start?.origin ?? "",
    company_name: start?.company_name ?? "",
    company_id: start?.company_id ?? "",
  });
  const set = (key: keyof NewSettings) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });
  return (
    <SidePanel title="ACH settings" onClose={onClose}>
      <Form className="flex flex-col gap-3" onSubmit={(event) => {
        event.preventDefault();
        return save(form);
      }}>
        <p className="text-sm text-ink-muted">As your bank gives them for the company&apos;s ACH files.</p>
        <Field label="Bank's routing number" id="ach-routing" required inputMode="numeric" value={form.bank_routing} onChange={set("bank_routing")} />
        <Field label="Bank's name" id="ach-bank" required maxLength={23} value={form.bank_name} onChange={set("bank_name")} />
        <Field label="Immediate origin" id="ach-origin" required maxLength={10} value={form.origin} onChange={set("origin")} />
        <Field label="Company name" id="ach-company" required maxLength={16} value={form.company_name} onChange={set("company_name")} />
        <Field label="Company ID" id="ach-company-id" required maxLength={10} value={form.company_id} onChange={set("company_id")} />
        <div><Button type="submit">Save ACH settings</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}

function VoidForm({ file, error, save }: { file: BankFile; error: string; save: (reason: string) => Promise<void> }) {
  const [reason, setReason] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save(reason);
    }}>
      <p className="text-sm text-ink-muted">
        {`Only a file the bank didn't take: its ${file.entries} ${file.entries === 1 ? "payout waits" : "payouts wait"} for another file again.`}
      </p>
      <Field label="Why" id="void-reason" required maxLength={500} value={reason} onChange={(event) => setReason(event.target.value)} />
      <div><Button type="submit" variant="danger">Void bank file</Button></div>
      <Notice tone="error">{error}</Notice>
    </Form>
  );
}
