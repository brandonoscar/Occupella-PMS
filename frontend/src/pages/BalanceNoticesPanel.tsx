import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type { Owing, Run } from "../balanceNotices";
import { paperStates, sentLine } from "../balanceNotices";
import { Button, LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { day, money } from "../money";
import type { NoticeTemplate, Templates } from "../notices";
import type { Scope } from "../scope";
import { scopeParams } from "../scope";

// Send balance notices (migration 20261023000201), from the rent roll or the delinquency report:
// the leases that owe in the page's scope, all picked to start, one of the company's notice
// templates and the day the notices are dated. Each lease gets a notice listing what is open by
// charge, emailed to each tenant with an email on file; then a PDF of them all, to print. A
// reminder, not the legal notice for nonpayment: the panel says how that notice goes where each
// home is.
export function BalanceNoticesPanel({ scope, onClose, onSent }: {
  scope: Scope;
  onClose: () => void;
  onSent?: (said: string) => void;
}) {
  const [owing, setOwing] = useState<Owing[] | null>(null);
  const [templates, setTemplates] = useState<NoticeTemplate[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [template, setTemplate] = useState("");
  const [on, setOn] = useState(() => day(new Date()));
  const [error, setError] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  useEffect(() => {
    const query = new URLSearchParams(scopeParams(scope));
    api.get<Owing[]>(`/api/balance-notices/owing?${query}`).then((found) => {
      setOwing(found);
      setPicked(new Set(found.map((o) => o.lease_id)));
    }).catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
    api.get<Templates>("/api/notices/templates").then((found) => {
      const notices = found.templates.filter((t) => t.used_for === "notice");
      setTemplates(notices);
      setTemplate(notices[0]?.id ?? "");
    }).catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [scope.group, scope.property]);

  function toggle(lease: string, on: boolean) {
    const next = new Set(picked);
    if (on) next.add(lease);
    else next.delete(lease);
    setPicked(next);
  }

  if (run !== null) {
    const states = paperStates(run.sent);
    return (
      <SidePanel title="Balance notices sent" onClose={onClose}>
        <p className="text-base" data-testid="balance-notices-sent">{sentLine(run)}</p>
        {run.sent.some((s) => s.without_email.length > 0) && (
          <ul className="text-sm text-ink-muted" aria-label="No email on file">
            {run.sent.filter((s) => s.without_email.length > 0).map((s) => (
              <li key={s.lease_id}>{`${s.property}, ${s.unit}: ${s.without_email.join(", ")}`}</li>
            ))}
          </ul>
        )}
        <DeliveryWords states={states} />
        <div><LinkButton href={`/api/balance-notices/runs/${run.id}/pdf`}>Print the notices (PDF)</LinkButton></div>
      </SidePanel>
    );
  }

  return (
    <SidePanel title="Send balance notices" onClose={onClose}>
      <p className="text-sm text-ink-muted">
        Each lease picked gets a notice from your template listing what is open by charge, emailed to each tenant with an
        email on file. It is a reminder, not the legal notice for nonpayment, and it says so.
      </p>
      {owing === null || templates === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : owing.length === 0 ? (
        <p className="text-base">No lease owes anything now.</p>
      ) : templates.length === 0 ? (
        <p className="text-base">
          Add a notice template first, at <a className="text-iris underline" href="/settings/notices">Settings, Notice templates</a>.
        </p>
      ) : (
        <Form className="flex flex-col gap-3" onSubmit={async (event) => {
          event.preventDefault();
          setError("");
          try {
            const answer = await api.post<Run>("/api/balance-notices", {
              template_id: template,
              dated_on: on,
              lease_ids: owing.filter((o) => picked.has(o.lease_id)).map((o) => o.lease_id),
            });
            setRun(answer);
            onSent?.(sentLine(answer));
          } catch (failed) {
            setError(failed instanceof ApiError ? failed.detail : String(failed));
          }
        }}>
          <Select label="Notice template" id="balance-template" value={template} onChange={(e) => setTemplate(e.target.value)}>
            {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </Select>
          <Field label="Dated" id="balance-dated" type="date" required value={on} onChange={(e) => setOn(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" variant="secondary" onClick={() => setPicked(new Set(owing.map((o) => o.lease_id)))}>Pick all</Button>
            <Button type="button" size="sm" variant="secondary" onClick={() => setPicked(new Set())}>Pick none</Button>
          </div>
          <Wide>
            <table className="w-full text-base" aria-label="Leases that owe">
              <thead>
                <tr>
                  <th><span className="sr-only">Send</span></th>
                  <th className="text-left">Home</th>
                  <th className="text-left">Tenants</th>
                  <th className="text-right">Owes</th>
                </tr>
              </thead>
              <tbody>
                {owing.map((o) => (
                  <tr key={o.lease_id}>
                    <td>
                      <input type="checkbox" aria-label={`Send to ${o.property}, ${o.unit}`} checked={picked.has(o.lease_id)}
                        onChange={(e) => toggle(o.lease_id, e.target.checked)} />
                    </td>
                    <td>{`${o.property}, ${o.unit}`}</td>
                    <td className="text-sm">
                      {o.tenants.map((t) => `${t.name}${t.email ? "" : " (no email on file)"}`).join(", ")}
                    </td>
                    <td className="text-right">{money(o.owed)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
          <DeliveryWords states={paperStates(owing.filter((o) => picked.has(o.lease_id)))} />
          {owing.filter((o) => picked.has(o.lease_id)).some((o) => o.delivery.email_may_count !== false) && (
            <ul className="text-sm text-ink-muted" aria-label="How the legal notice goes">
              {[...new Map(owing.filter((o) => picked.has(o.lease_id) && o.delivery.email_may_count !== false)
                .map((o) => [o.delivery.words, o.delivery.state])).keys()].map((words) => <li key={words}>{words}</li>)}
            </ul>
          )}
          <Notice tone="error">{error}</Notice>
          <div><Button type="submit" disabled={picked.size === 0}>{`Send ${picked.size} balance notice${picked.size === 1 ? "" : "s"}`}</Button></div>
        </Form>
      )}
    </SidePanel>
  );
}

function DeliveryWords({ states }: { states: string[] }) {
  if (states.length === 0) return null;
  return (
    <p className="text-sm text-ink" data-testid="paper-states">
      {`In ${states.join(", ")} the legal notice for nonpayment must be delivered by mail, posting or hand, not by email. These notices are reminders: print them to deliver, or make your legal notice from your own template on each lease's page.`}
    </p>
  );
}
