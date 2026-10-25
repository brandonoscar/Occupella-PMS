import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type { FromApplication } from "../applying";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { when } from "../maintenance";
import { DetailsList } from "./ApplicantDetails";
import { GoLink } from "./NeedsAttention";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// A tenant's emergency contact, which staff change, and the application they became a tenant
// through with what they gave on it (ADR 0010 phase 2, part G): the address they left, their
// previous landlord, employer and proof of income.
export function TenantApplicationCards({ id, name }: { id: string; name: string }) {
  const [found, setFound] = useState<FromApplication | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    api.get<FromApplication>(`/api/tenants/${id}/application`).then(setFound)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [id, runs]);

  if (found === null) return <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>;
  const contact = found.emergency_contact;
  return (
    <>
      <Card title="Emergency contact">
        <Said>{said}</Said>
        <div className="flex flex-col gap-3">
          <p className="text-base" data-testid="emergency-contact">
            {contact ? `${contact.name}, ${contact.phone}` : "None on file"}
          </p>
          <div>
            <Button variant="secondary" onClick={() => setEditing(true)}>
              {contact ? "Change emergency contact" : "Add emergency contact"}
            </Button>
          </div>
        </div>
        {editing && (
          <SidePanel title={`Emergency contact, ${name}`} onClose={() => setEditing(false)}>
            <EmergencyForm held={contact} save={(body) =>
              record(`/api/tenants/${id}/emergency-contact`, body, () => `Saved ${name}'s emergency contact.`)
                .then((ok) => { if (ok) setEditing(false); })} />
            <Notice tone="error">{refused}</Notice>
          </SidePanel>
        )}
      </Card>
      {found.application && (
        <Card title="From their application">
          <p className="mb-3 text-base text-ink-muted">
            Applied {when(found.application.submitted_at)}.{" "}
            <GoLink href={`/applicants/${found.application.application_id}`} className={LINK}>Open the application</GoLink>
          </p>
          <DetailsList details={found.application.details} label={`What ${name} gave when applying`} />
        </Card>
      )}
    </>
  );
}

function EmergencyForm({ held, save }: {
  held: FromApplication["emergency_contact"];
  save: (body: unknown) => Promise<void>;
}) {
  const [contactName, setContactName] = useState(held?.name ?? "");
  const [phone, setPhone] = useState(held?.phone ?? "");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({ name: contactName, phone });
    }}>
      <Field label="Name" id="emergency-name" required maxLength={200} value={contactName}
        onChange={(e) => setContactName(e.target.value)} />
      <Field label="Phone" id="emergency-phone" type="tel" required value={phone}
        onChange={(e) => setPhone(e.target.value)} />
      <div><Button type="submit">Save</Button></div>
    </Form>
  );
}
