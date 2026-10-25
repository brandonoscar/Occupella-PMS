import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { day, usDate } from "../money";
import type { Filled, MadeNotice, Templates } from "../notices";
import { GoLink } from "./NeedsAttention";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// A lease's Notices (the founder's review q095): the notices made for it from the company's
// templates, newest first, each to download as a PDF, and Write a notice: pick a template and the
// day it is dated, read it filled in from the lease, then make it. The words are the company's.
export function NoticesCard({ leaseId }: { leaseId: string }) {
  const [made, setMade] = useState<MadeNotice[] | null>(null);
  const [templates, setTemplates] = useState<Templates | null>(null);
  const [writing, setWriting] = useState(false);
  const [template, setTemplate] = useState("");
  const [on, setOn] = useState(day(new Date()));
  const [filled, setFilled] = useState<Filled | null>(null);
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");

  function refresh() {
    api.get<MadeNotice[]>(`/api/notices?lease_id=${leaseId}`).then(setMade).catch((failed) => setError(detail(failed)));
  }
  useEffect(refresh, [leaseId]);

  useEffect(() => {
    if (!writing || templates !== null) return;
    api.get<Templates>("/api/notices/templates")
      .then((answer) => {
        // A notice comes from a notice template; a lease template makes a draft lease.
        const notices = answer.templates.filter((t) => t.used_for === "notice");
        setTemplates({ ...answer, templates: notices });
        setTemplate(notices[0]?.id ?? "");
      })
      .catch((failed) => setError(detail(failed)));
  }, [writing, templates]);

  // What the chosen template reads for this lease on the chosen day.
  useEffect(() => {
    setFilled(null);
    if (!writing || template === "" || on === "") return;
    let current = true;
    api.post<Filled>("/api/notices/preview", { lease_id: leaseId, template_id: template, dated_on: on })
      .then((answer) => { if (current) setFilled(answer); })
      .catch((failed) => { if (current) setError(detail(failed)); });
    return () => { current = false; };
  }, [writing, template, on, leaseId]);

  return (
    <Card title="Notices">
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
      {made === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : made.length === 0 ? (
        <p className="text-sm text-ink-subtle">No notice made for this lease yet.</p>
      ) : (
        <ul className="flex flex-col gap-2 text-base" aria-label="Notices made">
          {made.map((n) => (
            <li key={n.id} className="flex flex-wrap items-center gap-3">
              <a className="inline-flex min-h-6 items-center font-medium text-iris hover:text-iris-hover hover:underline" href={`/api/notices/${n.id}/pdf`}>
                {n.title} (PDF)
              </a>
              <span className="text-sm text-ink-muted">
                dated {usDate(n.dated_on)}, made {usDate(n.made_at.slice(0, 10))}{n.made_by ? ` by ${n.made_by}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
      {!writing ? (
        <div><Button variant="secondary" onClick={() => setWriting(true)}>Write a notice</Button></div>
      ) : templates !== null && templates.templates.length === 0 ? (
        <p className="text-sm text-ink-muted">
          No notice template yet. Write the company's own in{" "}
          <GoLink href="/settings/notices" className="text-iris hover:underline">Settings, Notice templates</GoLink>.
        </p>
      ) : (
        <Form className="flex flex-col gap-3" onSubmit={async (event) => {
          event.preventDefault();
          setError("");
          try {
            const answer = await api.post<MadeNotice>("/api/notices", { lease_id: leaseId, template_id: template, dated_on: on });
            setSaid(`${answer.title}, dated ${usDate(answer.dated_on)}, is made.`);
            setWriting(false);
            refresh();
          } catch (failed) {
            setError(detail(failed));
          }
        }}>
          <div className="flex flex-wrap items-end gap-3">
            <Select label="Template" id={`notice-template-${leaseId}`} value={template}
              onChange={(event) => setTemplate(event.target.value)}>
              {(templates?.templates ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </Select>
            <Field label="Dated" id={`notice-on-${leaseId}`} type="date" required value={on}
              onChange={(event) => setOn(event.target.value)} />
          </div>
          {filled !== null && (
            <article aria-label="The notice as it will read" className="rounded-md border border-line bg-raised p-4 text-sm text-ink">
              <p className="whitespace-pre-line font-medium">{filled.letterhead}</p>
              <p className="mt-3 font-medium">{filled.title}</p>
              <p className="mt-2 whitespace-pre-line">{filled.body}</p>
            </article>
          )}
          <div className="flex gap-2">
            <Button type="submit" disabled={filled === null}>Make the notice</Button>
            <Button variant="ghost" onClick={() => setWriting(false)}>Cancel</Button>
          </div>
        </Form>
      )}
    </Card>
  );
}
