import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { SidePanel } from "../components/SidePanel";

// Who an email to a property's tenants goes to, as the API previews it.
export type Living = { tenant_id: string; name: string; to: string | null };
export type Recipients = { property_id: string; tenants: Living[] };
export type EmailedTenants = { emailed: { name: string; to: string }[]; without_email: string[] };

const plural = (n: number) => `${n} tenant${n === 1 ? "" : "s"}`;

// Email tenants (ADR 0010 phase 3, part K; rule 3): before it is written, exactly who gets it,
// with their emails and a box each (all ticked), how many, and who has no email on file; current
// tenants only. Each one chosen gets their own copy, kept on their Email tab.
export function EmailTenantsPanel({ propertyId, name, onSent, onClose }: {
  propertyId: string;
  name: string;
  onSent: (sent: EmailedTenants) => void;
  onClose: () => void;
}) {
  const [recipients, setRecipients] = useState<Recipients | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    api.get<Recipients>(`/api/emails/property/${propertyId}`)
      .then((found) => {
        setRecipients(found);
        setChosen(new Set(found.tenants.filter((t) => t.to !== null).map((t) => t.tenant_id)));
      })
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [propertyId]);
  const reachable = recipients?.tenants.filter((t) => t.to !== null) ?? [];
  const missing = recipients?.tenants.filter((t) => t.to === null) ?? [];
  const n = chosen.size;
  return (
    <SidePanel title={`Email tenants of ${name}`} onClose={onClose}>
      {recipients === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <Form className="flex flex-col gap-3" onSubmit={async (event) => {
          event.preventDefault();
          setError("");
          try {
            onSent(await api.post<EmailedTenants>("/api/emails/property", {
              property_id: propertyId, subject: subject.trim(), body: body.trim(), tenant_ids: [...chosen],
            }));
            onClose();
          } catch (failed) {
            setError(failed instanceof ApiError ? failed.detail : String(failed));
          }
        }}>
          <p className="text-sm text-ink-muted">Current tenants only, not past or future.</p>
          <fieldset className="flex flex-col gap-1">
            <legend className="mb-1 text-xs font-medium tracking-wider text-ink-subtle uppercase">
              To {plural(n)} of {plural(reachable.length)} with an email
            </legend>
            {reachable.length === 0 && <p className="text-sm text-ink-subtle">No one living there has an email on file.</p>}
            {reachable.map((t) => (
              <label key={t.tenant_id} className="flex items-center gap-2 text-base">
                <input type="checkbox" checked={chosen.has(t.tenant_id)} onChange={(e) => setChosen((was) => {
                  const next = new Set(was);
                  if (e.target.checked) next.add(t.tenant_id);
                  else next.delete(t.tenant_id);
                  return next;
                })} />
                {t.name}, {t.to}
              </label>
            ))}
          </fieldset>
          {missing.length > 0 && (
            <p className="text-sm text-ink-muted" data-testid="no-email">
              No email on file, so not written to: {missing.map((t) => t.name).join(", ")}.
            </p>
          )}
          <Field label="Subject" id="email-subject" required maxLength={200} value={subject}
            onChange={(event) => setSubject(event.target.value)} />
          <label htmlFor="email-body" className="text-sm text-ink-muted">Message</label>
          <textarea id="email-body" required maxLength={8000} rows={8} value={body}
            onChange={(event) => setBody(event.target.value)}
            className="rounded-sm border border-line-strong bg-sunken px-2 py-1.5 text-base text-ink" />
          <p className="text-sm text-ink-subtle">
            Each gets their own copy, kept on their Email tab. Your name and the company's are added below it.
          </p>
          <Notice tone="error">{error}</Notice>
          <div><Button type="submit" disabled={n === 0}>{n === 0 ? "Choose who gets it" : `Send to ${plural(n)}`}</Button></div>
        </Form>
      )}
    </SidePanel>
  );
}
