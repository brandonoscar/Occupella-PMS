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
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { latest } from "../latest";
import { money, sumMoney, usDate } from "../money";
import type { Payout } from "./BankFilesTab";
import { useRecord } from "./useRecord";

// Checks (migration 0157, the founder 2026-10-23): the payouts staff recorded as sent by check
// (owner draws and bills paid) are printed on the company's pre-printed voucher check stock. Staff
// set where the account's checks sit on the sheet and move the printing for their printer, pick
// the payouts by check of the last 30 days not yet printed, print them under the number on the
// first sheet loaded, download a run again, and void a check that was spoiled or never sent, which
// frees its payout for a new check. Printing, downloading and voiding take disburse (the server
// says so if it is missing). Someone the account allows signs each check by hand.

export type CheckSettings = {
  position: "top" | "middle" | "bottom";
  shift_right: number;
  shift_down: number;
  recorded_by: string | null;
  recorded_at: string | null;
};

export type PrintedCheck = {
  id: string;
  number: number;
  name: string;
  what: string;
  amount: string;
  voided: { reason: string; by: string | null; at: string } | null;
};

export type CheckRun = { id: string; dated_on: string; made_by: string | null; checks: PrintedCheck[] };

type Checks = { settings: CheckSettings; waiting: Payout[]; runs: CheckRun[]; next_number: number | null };

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";
const WHERE = { top: "Top", middle: "Middle", bottom: "Bottom" } as const;
const PLACE = { top: "at the top", middle: "in the middle", bottom: "at the bottom" } as const;

// "Check at the top of the page, printing moved 4 pt right and 2 pt up"
export function settingsLine(s: CheckSettings): string {
  const moved = [
    s.shift_right ? `${Math.abs(s.shift_right)} pt ${s.shift_right > 0 ? "right" : "left"}` : "",
    s.shift_down ? `${Math.abs(s.shift_down)} pt ${s.shift_down > 0 ? "down" : "up"}` : "",
  ].filter(Boolean);
  const where = `Check ${PLACE[s.position]} of the page`;
  return moved.length ? `${where}, printing moved ${moved.join(" and ")}` : where;
}

export function ChecksTab({ bankId }: { bankId: string }) {
  const [found, setFound] = useState<Checks | null>(null);
  const [unread, setUnread] = useState("");
  const [runs, setRuns] = useState(0);
  const [chosen, setChosen] = useState<string[]>([]);
  const [first, setFirst] = useState("");
  const [asking, setAsking] = useState(false);
  const [editing, setEditing] = useState(false);
  const [voiding, setVoiding] = useState<PrintedCheck | null>(null);
  const { said, error, record } = useRecord(() => setRuns((n) => n + 1));

  useEffect(() => {
    const run = latest();
    api.get<Checks>(`/api/checks/${bankId}`)
      .then(run.keep((answer: Checks) => {
        setFound(answer);
        setFirst((typed) => typed || (answer.next_number === null ? "" : String(answer.next_number)));
      }))
      .catch(run.keep((why) => setUnread(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
  }, [bankId, runs]);

  if (found === null) return <Notice tone={unread ? "error" : "info"}>{unread || "Loading"}</Notice>;
  const picked = found.waiting.filter((p) => chosen.includes(p.transfer_id));
  const total = sumMoney(picked.map((p) => p.amount));
  const toggle = (id: string) => setChosen(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id]);
  const last = Number(first) + picked.length - 1;

  return (
    <div className="flex flex-col gap-6">
      <Said>{said}</Said>
      {!editing && !voiding && <Notice tone="error">{error}</Notice>}
      <Card title="Check stock">
        <div role="group" aria-label="Check stock" className="flex flex-col gap-3">
          <p className="text-base" data-testid="check-settings">{settingsLine(found.settings)}</p>
          <p className="text-sm text-ink-muted">
            {found.settings.recorded_at
              ? `Set ${usDate(found.settings.recorded_at.slice(0, 10))}${found.settings.recorded_by ? ` by ${found.settings.recorded_by}` : ""}.`
              : "Pre-printed voucher stock: a check and two stubs a page."}
          </p>
          <div>
            <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>Change where checks print</Button>
          </div>
        </div>
      </Card>
      <Card title="Payouts by check waiting to be printed">
        {found.waiting.length === 0 ? (
          <p className="text-sm text-ink-subtle">No payout by check of the last 30 days waits to be printed.</p>
        ) : (
          <Form className="flex flex-col gap-3" onSubmit={(event) => {
            event.preventDefault();
            setAsking(true);
          }}>
            <ul className="flex flex-col divide-y divide-line rounded-md border border-card-edge text-sm" aria-label="Payouts by check waiting">
              {found.waiting.map((p) => (
                <li key={p.transfer_id}>
                  <label className="flex items-center gap-2.5 px-3 py-2 hover:bg-raised">
                    <input type="checkbox" checked={chosen.includes(p.transfer_id)} onChange={() => toggle(p.transfer_id)} />
                    {`${usDate(p.paid_on)} ${p.what}, ${p.name}: ${money(p.amount)}`}
                  </label>
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap items-end gap-3">
              <Field label="First check number" id={`first-check-${bankId}`} required inputMode="numeric" value={first}
                onChange={(event) => setFirst(event.target.value.replace(/[^0-9]/g, ""))} />
              <Button type="submit" disabled={picked.length === 0 || !first}>
                {picked.length === 0 ? "Print checks" : `Print ${picked.length} ${picked.length === 1 ? "check" : "checks"} of ${money(total)}`}
              </Button>
            </div>
            <p className="text-sm text-ink-muted">The number on the first sheet of stock in the printer.</p>
          </Form>
        )}
      </Card>
      <Card title="Printed checks">
        {found.runs.length === 0 ? (
          <EmptyState icon={FileText} title="No check printed yet." />
        ) : (
          <Wide>
            <table className="w-full text-base" aria-label="Printed checks">
              <thead>
                <tr>
                  <th className="text-left">Check</th>
                  <th className="text-left">Dated</th>
                  <th className="text-left">Payee</th>
                  <th className="text-left">For</th>
                  <th className="text-right">Amount</th>
                  <th className="text-left">Status</th>
                  <th><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {found.runs.flatMap((r) => r.checks.map((c, n) => (
                  <tr key={c.id} aria-label={`Check ${c.number}`}>
                    <td>{c.number}</td>
                    <td className="whitespace-nowrap">{usDate(r.dated_on)}</td>
                    <td>{c.name}</td>
                    <td className="text-ink-muted">{c.what}</td>
                    <td className="text-right">{money(c.amount)}</td>
                    <td>{c.voided ? <Badge tone="neutral">Void: {c.voided.reason}</Badge> : <Badge tone="positive">Printed</Badge>}</td>
                    <td className="text-right whitespace-nowrap">
                      <span className="inline-flex items-center gap-2">
                        {n === 0 && r.checks.some((x) => !x.voided) && (
                          <a href={`/api/checks/runs/${r.id}.pdf`} className={LINK}>
                            {r.checks.length === 1 ? "PDF" : `PDF of ${r.checks[0]!.number}–${r.checks.at(-1)!.number}`}
                          </a>
                        )}
                        {!c.voided && (
                          <RowMenu label={`check ${c.number}`} actions={[{ label: "Void", danger: true, onSelect: () => setVoiding(c) }]} />
                        )}
                      </span>
                    </td>
                  </tr>
                )))}
              </tbody>
            </table>
          </Wide>
        )}
      </Card>
      {asking && (
        <ConfirmDialog
          question={`Print ${picked.length} ${picked.length === 1 ? "check" : "checks"} of ${money(total)}, numbered ${first}${picked.length > 1 ? ` to ${last}` : ""}? Load the stock with check ${first} first.`}
          confirmLabel="Print checks"
          onCancel={() => setAsking(false)}
          onConfirm={() => {
            setAsking(false);
            void record<CheckRun>(`/api/checks/${bankId}`, { first_number: Number(first), transfer_ids: picked.map((p) => p.transfer_id) },
              (run) => `Checks ${run.checks[0]!.number} to ${run.checks.at(-1)!.number} ready to print: ${money(sumMoney(run.checks.map((c) => c.amount)))}.`)
              .then((done) => {
                if (done) {
                  setChosen([]);
                  setFirst("");
                }
              });
          }}
        />
      )}
      {editing && (
        <SettingsPanel start={found.settings} error={error} onClose={() => setEditing(false)}
          save={async (body) => {
            if (await record<CheckSettings>(`/api/checks/${bankId}/settings`, body, (kept) => `${settingsLine(kept)}.`)) setEditing(false);
          }} />
      )}
      {voiding && (
        <SidePanel title={`Void check ${voiding.number}`} onClose={() => setVoiding(null)}>
          <VoidForm error={error} save={async (reason) => {
            if (await record(`/api/checks/${voiding.id}/void`, { reason }, () => `Check ${voiding.number} is void; its payout waits for a new check.`)) {
              setVoiding(null);
            }
          }} />
        </SidePanel>
      )}
    </div>
  );
}

type NewSettings = Pick<CheckSettings, "position" | "shift_right" | "shift_down">;

function SettingsPanel({ start, error, onClose, save }: {
  start: CheckSettings;
  error: string;
  onClose: () => void;
  save: (body: NewSettings) => Promise<void>;
}) {
  const [form, setForm] = useState({ position: start.position, right: String(start.shift_right), down: String(start.shift_down) });
  return (
    <SidePanel title="Where checks print" onClose={onClose}>
      <Form className="flex flex-col gap-3" onSubmit={(event) => {
        event.preventDefault();
        return save({ position: form.position, shift_right: Number(form.right), shift_down: Number(form.down) });
      }}>
        <Select label="Check on the page" id="check-position" value={form.position}
          onChange={(event) => setForm({ ...form, position: event.target.value as CheckSettings["position"] })}>
          {(["top", "middle", "bottom"] as const).map((p) => <option key={p} value={p}>{WHERE[p]}</option>)}
        </Select>
        <Field label="Move right (points, − for left)" id="check-right" type="number" min={-36} max={36} required value={form.right}
          onChange={(event) => setForm({ ...form, right: event.target.value })} />
        <Field label="Move down (points, − for up)" id="check-down" type="number" min={-36} max={36} required value={form.down}
          onChange={(event) => setForm({ ...form, down: event.target.value })} />
        <p className="text-sm text-ink-muted">A point is 1/72 inch. Print one check on plain paper and hold it over the stock to see.</p>
        <div><Button type="submit">Save</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}

function VoidForm({ error, save }: { error: string; save: (reason: string) => Promise<void> }) {
  const [reason, setReason] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save(reason);
    }}>
      <p className="text-sm text-ink-muted">Its number stays used. Its payout waits for a new check under a new number.</p>
      <Field label="Why" id="check-void-reason" required maxLength={500} value={reason} onChange={(event) => setReason(event.target.value)} />
      <div><Button type="submit" variant="danger">Void check</Button></div>
      <Notice tone="error">{error}</Notice>
    </Form>
  );
}
