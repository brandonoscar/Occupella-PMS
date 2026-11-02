import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { day, money, sumMoney, usDate } from "../money";
import type { OwnerPaid } from "../payables";
import { paidSummary } from "../payables";
import type { DrawRun } from "../payouts";
import { drawChoices, methodWord } from "../payouts";
import { requestId } from "../rent";
import type { Prepared } from "../reconcile";
import type { Unreconciled } from "../today";
import type { Output } from "./PayOwnersPage";
import type { Recorder } from "./useRecord";

const EMAIL = "text-iris hover:text-iris-hover hover:underline";

// What Send reminder writes about a lease's rent past due, as the API previews it.
export type Reminder = {
  lease_id: string;
  subject: string;
  body: string;
  to: { tenant_id: string; name: string; to: string }[];
  left_out: { name: string; why: string }[];
  sent_today: boolean;
};

// Send reminder (rule 3): before anything is sent, exactly who gets it, their emails, how many,
// anyone left out and why, and what it says. One email to each; it moves no money.
export function ReminderForm({ leaseId, record }: { leaseId: string; record: Recorder }) {
  const [reminder, setReminder] = useState<Reminder | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.get<Reminder>(`/api/emails/rent-reminders/${leaseId}`).then(setReminder)
      .catch((why) => setError(why instanceof ApiError ? why.detail : String(why)));
  }, [leaseId]);
  if (reminder === null) return <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>;
  const n = reminder.to.length;
  return (
    <div className="flex flex-col gap-4">
      <section aria-label="Who gets it" className="flex flex-col gap-1">
        <h3 className="text-xs font-medium tracking-wider text-ink-subtle uppercase">
          To {n} tenant{n === 1 ? "" : "s"}
        </h3>
        <ul className="flex flex-col gap-1 text-base">
          {reminder.to.map((r) => (
            <li key={r.tenant_id}>{r.name}, <a className={EMAIL} href={`mailto:${r.to}`}>{r.to}</a></li>
          ))}
        </ul>
        {reminder.left_out.length > 0 && (
          <ul aria-label="Left out" className="flex flex-col gap-1 text-sm text-ink-muted">
            {reminder.left_out.map((l) => <li key={l.name}>Not {l.name}: {l.why}</li>)}
          </ul>
        )}
      </section>
      <section aria-label="What it says" className="flex flex-col gap-1">
        <h3 className="text-xs font-medium tracking-wider text-ink-subtle uppercase">{reminder.subject}</h3>
        <p className="text-base whitespace-pre-line">{reminder.body}</p>
        <p className="text-sm text-ink-subtle">Signed with your name and the company's.</p>
      </section>
      {reminder.sent_today && <Notice tone="info">A reminder of this rent went to them today already.</Notice>}
      <div>
        <Button disabled={n === 0 || reminder.sent_today}
          onClick={() => record(`/api/emails/rent-reminders/${leaseId}`, {},
            () => `Sent the reminder to ${n} tenant${n === 1 ? "" : "s"}.`)}>
          {n === 0 ? "No one to send it to" : `Send to ${n} tenant${n === 1 ? "" : "s"}`}
        </Button>
      </div>
      <p className="text-sm text-ink-subtle">
        Writes one email to each, listed on their Email tab. Nothing else changes; no fee is charged.
      </p>
    </div>
  );
}

// Approve draws: pays the draws the nightly run prepared on the draw day, each owner the way they
// are paid, every one or none; recorded as paid outside the PMS, or with its ACH file and checks
// written too. Money leaves trust, so staff confirm the total first.
export function ApproveDrawsForm({ run, record }: { run: DrawRun; record: Recorder }) {
  const chosen = drawChoices(run);
  const lines = run.lines.filter((l) => l.account in chosen);
  const [output, setOutput] = useState<Output>("record");
  const [on, setOn] = useState(day(new Date()));
  const [request] = useState(requestId);
  const { ask, prompt } = useConfirm();
  const total = sumMoney(Object.values(chosen));
  const payouts = lines.map((l) => ({ account: l.account, amount: chosen[l.account] ?? "0", method: l.method }));
  const skipped = run.lines.length - lines.length;
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const files = output === "file" ? " The PMS writes the ACH file and prints the checks; the bank moves the money." : "";
      ask(`Pay ${money(total)} to ${lines.length} owner account${lines.length === 1 ? "" : "s"} on ${usDate(on)}, each the way they are paid?${files}`,
        () => record<OwnerPaid[]>("/api/payables/draws/batch", {
          request_id: request, on, method: "check", payouts, output, draw_run_id: run.id,
        }, (paid) => paidSummary(paid, [...new Set(payouts.map((p) => p.method))])
          + (output === "file" ? " Their files are on Pay owners." : "")), "Pay");
    }}>
      <ul aria-label="Owner draws ready" className="flex flex-col gap-1 text-base">
        {lines.map((l) => (
          <li key={l.account}>
            {l.owner}, {l.property}: {money(chosen[l.account] ?? "0")} by {methodWord(l.method)}
            {chosen[l.account] !== l.amount && <span className="text-ink-muted"> (prepared {money(l.amount)}; has less now)</span>}
          </li>
        ))}
      </ul>
      {skipped > 0 && <p className="text-sm text-ink-muted">{skipped} with nothing available now {skipped === 1 ? "is" : "are"} left out.</p>}
      <Select label="Payout file" id="draws-output" value={output} onChange={(e) => setOutput(e.target.value as Output)}>
        <option value="record">None: record payouts made outside the PMS</option>
        <option value="file">ACH file and checks to print</option>
      </Select>
      <Field label="Paid out on" id="draws-on" type="date" required value={on} onChange={(e) => setOn(e.target.value)} />
      <div><Button type="submit" disabled={lines.length === 0}>Pay {money(total)}</Button></div>
      <p className="text-sm text-ink-subtle">
        Records the payouts, all of them or none. To change an amount or an owner, use Pay owners.
      </p>
      {prompt}
    </Form>
  );
}

// The question Approve asks before a month-end is approved: the figures, who prepared it and
// what approving does.
export function approveQuestion(account: Unreconciled, prepared: Prepared): string {
  return `Approve ${account.name}'s month-end through ${usDate(prepared.through)}, prepared by ${prepared.prepared_by}?`
    + ` The bank shows ${money(prepared.bank_balance)} and the books ${money(prepared.book_cash)}.`
    + ` The account closes through ${usDate(prepared.through)}: nothing dated on or before it can be added after.`;
}
