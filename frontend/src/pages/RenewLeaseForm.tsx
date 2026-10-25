import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { latest } from "../latest";
import { money, usDate } from "../money";
import type { NoticeTemplate, Templates } from "../notices";
import type { Offered, RenewalPlan } from "../offers";
import { MAX_DOCUMENT, base64Of, isPdf, sentLine, whoSigns, yearFrom } from "../offers";
import type { Recorder } from "./LeaseForms";

const MONEY = /^\d{1,7}(\.\d{1,2})?$/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;
const OWN_PDF = "pdf";

// Renew lease (ADR 0010 phase 3, part L), from the tenant's or the lease's page: one offer. The
// new rent and term, starting the day after the lease ends; the lease filled in from one of the
// company's lease templates, or its own PDF; sent to the tenants on the lease to sign on the PMS's
// signing page. Before it goes, the confirm names who gets it, and says so when the new rent starts
// sooner than the written notice the property's state asks (docs/law.md). Once everyone has signed,
// it becomes the lease.
export function RenewLeaseForm({ leaseId, record }: { leaseId: string; record: Recorder }) {
  const [plan, setPlan] = useState<RenewalPlan | null>(null);
  const [error, setError] = useState("");
  const [rent, setRent] = useState("");
  const [startsOn, setStartsOn] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [templates, setTemplates] = useState<NoticeTemplate[]>([]);
  const [lease, setLease] = useState(OWN_PDF);
  const [document, setDocument] = useState("");
  const [problem, setProblem] = useState("");
  const { ask, prompt } = useConfirm();

  // First the plan as it stands, which fills in the form; then, as the rent or the first day
  // changes, the notice the state asks of those.
  const asked = plan !== null && MONEY.test(rent) && DAY.test(startsOn)
    ? `?monthly_rent=${encodeURIComponent(rent)}&starts_on=${startsOn}`
    : "";
  useEffect(() => {
    const run = latest();
    api.get<RenewalPlan>(`/api/leasing/renewals/${leaseId}/plan${asked}`)
      .then(run.keep((found: RenewalPlan) => {
        setPlan(found);
        if (asked) return;
        setRent(found.new_rent);
        setStartsOn(found.new_starts_on);
        setEndsOn(found.ends_on ? yearFrom(found.new_starts_on) : "");
      }))
      .catch(run.keep((why: unknown) => setError(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
  }, [leaseId, asked]);
  useEffect(() => {
    api.get<Templates>("/api/notices/templates")
      .then((found) => {
        const leases = found.templates.filter((t) => t.used_for === "lease");
        setTemplates(leases);
        const first = leases.at(0);
        if (first) setLease(first.id);
      })
      .catch(() => setTemplates([]));
  }, []);

  async function pick(file: File | undefined) {
    setDocument("");
    if (!file) return;
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (!isPdf(bytes)) return setProblem("the lease document is a PDF");
    if (bytes.length > MAX_DOCUMENT) return setProblem("the lease document is at most 5 MB");
    setProblem("");
    setDocument(base64Of(bytes));
  }

  if (plan === null) return <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>;
  const noEmail = plan.signers.filter((s) => s.email === null);
  const ready = noEmail.length === 0 && plan.signers.length > 0 && (lease !== OWN_PDF || document !== "");
  const where = `${plan.unit}, ${plan.property}`;

  function send() {
    if (plan === null) return;
    const warning = plan.notice?.warning;
    const question = `Send the renewal of ${where}, ${money(rent)} a month from ${usDate(startsOn)}, to sign to `
      + `${whoSigns(plan.signers)}?${warning ? ` ${warning}` : ""}`;
    ask(question, () => record<Offered>(`/api/leasing/leases/${leaseId}/renewal`, {
      starts_on: startsOn,
      ends_on: endsOn || null,
      monthly_rent: rent,
      recurring_charges: [],
      ...(lease === OWN_PDF
        ? { document_title: "Lease renewal", document_base64: document }
        : { template_id: lease }),
      signers: plan.signers.map((s) => ({ name: s.name, email: s.email, role: "tenant" })),
      send: true,
    }, (made) => `Sent the renewal of ${where}. ${sentLine(made)}`), warning ? "Send anyway" : "Send");
  }

  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => { event.preventDefault(); send(); }}>
      <p className="text-base">
        {where}: {money(plan.monthly_rent)} a month now,{" "}
        {plan.ends_on ? `ending ${usDate(plan.ends_on)}` : "month to month"}.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <Field label="New rent a month" id="renew-rent" inputMode="decimal" required value={rent}
          onChange={(e) => setRent(e.target.value)} />
        <span />
        <Field label="Starts" id="renew-starts" type="date" required value={startsOn}
          onChange={(e) => setStartsOn(e.target.value)} />
        <Field label="Ends (empty for month to month)" id="renew-ends" type="date" value={endsOn}
          onChange={(e) => setEndsOn(e.target.value)} />
      </div>
      {plan.notice?.warning && <Notice tone="error">{plan.notice.warning}</Notice>}
      {plan.notice && !plan.notice.warning && (
        <p className="text-sm text-ink-subtle" data-testid="renew-notice">
          {plan.notice.days} days' written notice asked ({plan.notice.law}); this start gives it.
        </p>
      )}
      <Select label="The lease" id="renew-document" value={lease} onChange={(e) => setLease(e.target.value)}>
        {templates.map((t) => <option key={t.id} value={t.id}>{t.name}, filled in</option>)}
        <option value={OWN_PDF}>Your own PDF</option>
      </Select>
      {lease === OWN_PDF && (
        <Field label="Lease document (PDF, your own)" id="renew-file" type="file" accept="application/pdf"
          onChange={(e) => void pick(e.target.files?.[0])} />
      )}
      <div className="flex flex-col gap-1">
        <p className="text-sm text-ink-muted">Who signs</p>
        <ul aria-label="Who signs" className="flex flex-col gap-0.5 text-base">
          {plan.signers.map((s) => (
            <li key={s.tenant_id}>
              {s.name}{s.email ? `, ${s.email}` : <span className="text-danger">: no email on file</span>}
            </li>
          ))}
        </ul>
      </div>
      {noEmail.length > 0 && (
        <Notice tone="error">
          Add an email for {noEmail.map((s) => s.name).join(", ")} on their page (Edit contact details) to send this.
        </Notice>
      )}
      <Notice tone="error">{problem}</Notice>
      <div>
        <Button type="submit" disabled={!ready}>Send for signing</Button>
      </div>
      <p className="text-sm text-ink-subtle">
        Each tenant gets their own link to sign. Once everyone has signed, it becomes the lease from its first day,
        and this one ends the day before.
      </p>
      {prompt}
    </Form>
  );
}
