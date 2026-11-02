import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import type { Application } from "../leasing";
import { applicantNames } from "../leasing";
import type { NoticeTemplate, Templates } from "../notices";
import type { MonthlyCharge, Offered, RenewableLease } from "../offers";
import { MAX_DOCUMENT, base64Of, dayAfter, isPdf, parseSigners, signersText } from "../offers";
import type { Paged } from "../rentals";
import type { Recorder } from "./useRecord";

const TEXTAREA = "rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink";
const NO_CHARGE: MonthlyCharge = { kind: "fee", memo: "", amount: "" };

// New draft lease (ADR 0006): a lease for an approved application or the renewal of a lease, its
// days, rent, the security deposit it asks and the monthly fees and credits besides rent, the
// company's own lease PDF (or one of its lease templates, filled in from these terms; migration
// 0121) and who signs. Saved as a draft to send later, or sent for signing now.
// `from` starts it on an application ("application:<id>") or a lease ("lease:<id>").
export function DraftLeaseForm({ from = "", record, onMade }: {
  from?: string;
  record: Recorder;
  onMade: (made: Offered) => void;
}) {
  const [approved, setApproved] = useState<Application[]>([]);
  const [leases, setLeases] = useState<RenewableLease[]>([]);
  const [target, setTarget] = useState("");
  const [startsOn, setStartsOn] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [rent, setRent] = useState("");
  const [deposit, setDeposit] = useState("");
  const [charges, setCharges] = useState<MonthlyCharge[]>([]);
  const [title, setTitle] = useState("");
  const [signers, setSigners] = useState("");
  // Guarantors who sign too, one a line; they become the lease's guarantors, not tenants.
  const [guarantors, setGuarantors] = useState("");
  const [document, setDocument] = useState("");
  // The company's own PDF, or a lease template of its own the PMS fills in.
  const [from_, setFrom] = useState<"pdf" | "template">("pdf");
  const [leaseTemplates, setLeaseTemplates] = useState<NoticeTemplate[]>([]);
  const [templateId, setTemplateId] = useState("");
  const [problem, setProblem] = useState("");
  // Which of the two submit buttons was pressed: Send for signing, or Save draft.
  const sending = useRef(true);

  useEffect(() => {
    api.get<Paged<Application>>("/api/leasing/applications?show=open&per_page=200")
      .then((open) => setApproved(open.items.filter((a) => a.status === "approved" || a.status === "conditional")))
      .catch(() => setApproved([]));
    api.get<RenewableLease[]>("/api/leasing/leases").then(setLeases).catch(() => setLeases([]));
    api.get<Templates>("/api/notices/templates")
      .then((found) => {
        const leasesOnly = found.templates.filter((t) => t.used_for === "lease");
        setLeaseTemplates(leasesOnly);
        setTemplateId(leasesOnly[0]?.id ?? "");
      })
      .catch(() => setLeaseTemplates([]));
  }, []);
  // Started from an application's or a lease's page: that one, once it is loaded.
  const [started, setStarted] = useState(false);
  useEffect(() => {
    if (started || !from) return;
    const [kind, id] = from.split(":");
    const known = kind === "application" ? approved.some((a) => a.id === id) : leases.some((l) => l.lease_id === id);
    if (!known) return;
    setStarted(true);
    choose(from);
  }, [approved, leases, from, started]);

  function choose(value: string) {
    setTarget(value);
    const [kind, id] = value.split(":");
    const application = approved.find((a) => kind === "application" && a.id === id);
    const lease = leases.find((l) => kind === "lease" && l.lease_id === id);
    if (application) {
      setSigners(signersText(application.applicants));
      setRent(application.monthly_rent);
      setStartsOn(application.move_in_on);
      setTitle("Lease");
    } else if (lease) {
      setSigners(signersText(lease.tenants));
      setRent(lease.monthly_rent);
      setStartsOn(lease.ends_on ? dayAfter(lease.ends_on) : "");
      setTitle("Lease renewal");
    }
    setEndsOn("");
  }

  async function pick(file: File | undefined) {
    setDocument("");
    if (!file) return;
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (!isPdf(bytes)) return setProblem("the lease document is a PDF");
    if (bytes.length > MAX_DOCUMENT) return setProblem("the lease document is at most 5 MB");
    setProblem("");
    setDocument(base64Of(bytes));
  }

  const ready = from_ === "pdf" ? document !== "" : templateId !== "";

  function change(n: number, part: Partial<MonthlyCharge>) {
    setCharges(charges.map((c, at) => (at === n ? { ...c, ...part } : c)));
  }

  function save(send: boolean) {
    const people = parseSigners(signers);
    if ("error" in people) {
      setProblem(people.error);
      return;
    }
    const backing = guarantors.trim() ? parseSigners(guarantors) : { signers: [] };
    if ("error" in backing) {
      setProblem(`Guarantors: ${backing.error}`);
      return;
    }
    setProblem("");
    const [kind, id] = target.split(":");
    const path = kind === "application" ? `/api/leasing/applications/${id}/offer` : `/api/leasing/leases/${id}/renewal`;
    return record<Offered>(path, {
      starts_on: startsOn,
      ends_on: endsOn || null,
      monthly_rent: rent,
      security_deposit: deposit || null,
      recurring_charges: charges,
      ...(from_ === "template"
        ? { template_id: templateId }
        : { document_title: title, document_base64: document }),
      signers: [...people.signers, ...backing.signers.map((g) => ({ ...g, role: "guarantor" }))],
      send,
    }, (made) => {
      onMade(made);
      return send
        ? `Sent the lease for ${made.offer.property}, ${made.offer.unit} to its signers.`
        : `Saved a draft lease for ${made.offer.property}, ${made.offer.unit}.`;
    });
  }

  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save(sending.current);
    }}>
      <Select label="For" id="draft-for" required value={target} onChange={(e) => choose(e.target.value)}>
        <option value="">Choose an approved applicant or a lease to renew</option>
        <optgroup label="A new lease for an approved applicant">
          {approved.map((a) => (
            <option key={a.id} value={`application:${a.id}`}>{applicantNames(a)} for {a.property}, {a.unit}</option>
          ))}
        </optgroup>
        <optgroup label="The renewal of a lease">
          {leases.map((l) => (
            <option key={l.lease_id} value={`lease:${l.lease_id}`}>
              {l.property}, {l.unit}: {l.tenants.map((t) => t.name).join(", ")}
            </option>
          ))}
        </optgroup>
      </Select>
      {target && (
        <>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Starts" id="draft-starts" type="date" required value={startsOn}
              onChange={(e) => setStartsOn(e.target.value)} />
            <Field label="Ends (empty for month to month)" id="draft-ends" type="date" value={endsOn}
              onChange={(e) => setEndsOn(e.target.value)} />
            <Field label="Rent a month" id="draft-rent" inputMode="decimal" required value={rent}
              onChange={(e) => setRent(e.target.value)} />
            <Field label="Security deposit (optional)" id="draft-deposit" inputMode="decimal" value={deposit}
              onChange={(e) => setDeposit(e.target.value)} />
          </div>
          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm text-ink-muted">Monthly charges besides rent</legend>
            {charges.map((c, n) => (
              <div key={n} role="group" className="flex flex-wrap items-end gap-2" aria-label={`Monthly charge ${n + 1}`}>
                <Select label="Kind" id={`charge-kind-${n}`} value={c.kind}
                  onChange={(e) => change(n, { kind: e.target.value as MonthlyCharge["kind"] })}>
                  <option value="fee">Fee</option>
                  <option value="credit">Credit</option>
                </Select>
                <Field label="What for" id={`charge-memo-${n}`} required maxLength={200} value={c.memo}
                  onChange={(e) => change(n, { memo: e.target.value })} />
                <Field label="Amount" id={`charge-amount-${n}`} inputMode="decimal" required value={c.amount}
                  onChange={(e) => change(n, { amount: e.target.value })} />
                <Button type="button" variant="ghost" size="sm" onClick={() => setCharges(charges.filter((_, at) => at !== n))}>
                  Remove
                </Button>
              </div>
            ))}
            {charges.length < 10 && (
              <div>
                <Button type="button" variant="secondary" size="sm" onClick={() => setCharges([...charges, NO_CHARGE])}>
                  Add a monthly charge
                </Button>
              </div>
            )}
          </fieldset>
          <Select label="The lease" id="draft-from" value={from_}
            onChange={(e) => setFrom(e.target.value as "pdf" | "template")}>
            <option value="pdf">Your own PDF</option>
            <option value="template">One of your lease templates, filled in</option>
          </Select>
          {from_ === "pdf" ? (
            <>
              <Field label="Document title" id="draft-title" required maxLength={200} value={title}
                onChange={(e) => setTitle(e.target.value)} />
              <Field label="Lease document (PDF, your own)" id="draft-document" type="file" accept="application/pdf"
                onChange={(e) => void pick(e.target.files?.[0])} />
            </>
          ) : leaseTemplates.length === 0 ? (
            <Notice tone="info">No lease template yet: add one at Settings, Notice templates, used for a lease to sign.</Notice>
          ) : (
            <Select label="Lease template" id="draft-template" value={templateId}
              onChange={(e) => setTemplateId(e.target.value)}>
              {leaseTemplates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </Select>
          )}
          <label htmlFor="draft-signers" className="flex flex-col gap-1.5 text-sm text-ink-muted">
            Who signs, one a line: Name, email
            <textarea id="draft-signers" rows={3} required value={signers} onChange={(e) => setSigners(e.target.value)}
              className={TEXTAREA} />
          </label>
          <label htmlFor="draft-guarantors" className="flex flex-col gap-1.5 text-sm text-ink-muted">
            Guarantors who sign too (optional), one a line: Name, email
            <textarea id="draft-guarantors" rows={2} value={guarantors} onChange={(e) => setGuarantors(e.target.value)}
              className={TEXTAREA} />
          </label>
          <Notice tone="error">{problem}</Notice>
          {from_ === "pdf" && !document && <Notice tone="info">Choose the lease document first.</Notice>}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={!ready} onClick={() => { sending.current = true; }}
              says="Makes each signer above a signing link, emailed to them if this server sends email. No lease is made yet.">
              Send for signing
            </Button>
            <Button type="submit" variant="secondary" disabled={!ready} onClick={() => { sending.current = false; }}
              says="Keeps it as a draft to send later. Nothing goes to the signers, and no lease is made.">
              Save draft
            </Button>
          </div>
        </>
      )}
    </Form>
  );
}
