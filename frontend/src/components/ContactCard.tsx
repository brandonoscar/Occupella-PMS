import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type { Contact } from "../directory";
import { addressLines, CONTACT_FIELDS, contactBody, hasContact } from "../directory";
import { usDate } from "../money";
import { useRecord } from "../pages/useRecord";
import { Button } from "./Button";
import { Field } from "./Field";
import { Notice } from "./Notice";
import { Card } from "./Shell";
import { Said } from "./Said";
import { Form } from "./Form";

const LABELS: Record<(typeof CONTACT_FIELDS)[number], string> = {
  email: "Email",
  phone: "Phone",
  alternate_phone: "Other phone",
  address_line_1: "Street address",
  address_line_2: "Apartment, suite (optional)",
  city: "City",
  state: "State",
  postal_code: "ZIP code",
};

function draftOf(contact: Contact) {
  return Object.fromEntries(CONTACT_FIELDS.map((field) => [field, contact[field] ?? ""])) as Record<
    (typeof CONTACT_FIELDS)[number],
    string
  >;
}

// A tenant's, owner's or vendor's email, phones and mailing address, and Edit to change them.
// Saving sends them all, so a field left empty is cleared; the ones before stay in the history.
function ContactDetails({
  path,
  contact,
  refresh,
  name,
  note,
  editRequest = 0,
}: {
  path: string;
  contact: Contact;
  refresh: () => void;
  name: string;
  note?: string;
  // Raised by another part of the page (the portal's "Edit contact details") to open Edit.
  editRequest?: number;
}) {
  const { said, error, record } = useRecord(refresh);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(() => draftOf(contact));
  useEffect(() => {
    if (editRequest > 0) {
      setDraft(draftOf(contact));
      setEditing(true);
    }
    // Only a new request opens it, not a refreshed contact.
  }, [editRequest]);
  const address = addressLines(contact);
  return (
    <div className="flex flex-col gap-3" aria-label={`Contact details, ${name}`} role="group">
      {hasContact(contact) ? (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-base">
          {contact.email && (
            <>
              <dt className="text-ink-muted">Email</dt>
              <dd><a className="text-iris hover:underline" href={`mailto:${contact.email}`}>{contact.email}</a></dd>
            </>
          )}
          {contact.phone && (
            <>
              <dt className="text-ink-muted">Phone</dt>
              <dd>{contact.phone}</dd>
            </>
          )}
          {contact.alternate_phone && (
            <>
              <dt className="text-ink-muted">Other phone</dt>
              <dd>{contact.alternate_phone}</dd>
            </>
          )}
          {address.length > 0 && (
            <>
              <dt className="text-ink-muted">Mailing address</dt>
              <dd>{address.map((line) => <span key={line} className="block">{line}</span>)}</dd>
            </>
          )}
        </dl>
      ) : (
        <p className="text-sm text-ink-subtle">No contact details yet.</p>
      )}
      {note && <p className="text-sm text-ink-muted">{note}</p>}
      {!editing ? (
        <div>
          <Button variant="secondary" size="sm" onClick={() => { setDraft(draftOf(contact)); setEditing(true); }}>
            Edit contact details
          </Button>
        </div>
      ) : (
        <Form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={async (event) => {
            event.preventDefault();
            if (await record(`${path}/contact`, contactBody(draft), () => "Contact details saved.")) {
              setEditing(false);
            }
          }}
        >
          {CONTACT_FIELDS.map((field) => (
            <Field key={field} label={LABELS[field]} id={`${path}-${field}`}
              type={field === "email" ? "email" : field.endsWith("phone") ? "tel" : "text"}
              maxLength={field === "email" ? 254 : 200} value={draft[field]}
              onChange={(event) => setDraft({ ...draft, [field]: event.target.value })} />
          ))}
          <div className="flex gap-2 sm:col-span-2">
            <Button type="submit" size="sm" says="Saves these details; the old ones stay in the history. Nothing is sent to them.">Save contact details</Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </Form>
      )}
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </div>
  );
}

// The same, in a card of its own under its label. A tenant's page says when the tenant changed
// them themselves, in the portal.
export function ContactCard(props: {
  path: string;
  contact: Contact;
  refresh: () => void;
  name: string;
  byTenantAt?: string | null;
  editRequest?: number;
}) {
  const { byTenantAt, ...details } = props;
  return (
    <Card title="Contact details">
      <ContactDetails {...details}
        note={byTenantAt ? `${props.name} changed these in the portal on ${usDate(byTenantAt)}.` : undefined} />
    </Card>
  );
}

// A tenant's own contact details in the portal (q121): theirs to change. The company writes to
// this email from then on; signing in keeps the email they sign in with.
export function MyContactCard({ name }: { name: string }) {
  const [contact, setContact] = useState<Contact | null>(null);
  const [error, setError] = useState("");
  function refresh() {
    api.get<Contact>("/api/tenant/contact").then(setContact)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);
  return (
    <Card title="Your contact details">
      {contact === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <ContactDetails path="/api/tenant" contact={contact} refresh={refresh} name={name}
          note="Your manager writes to you at this email and calls you at these numbers. You still sign in with the email you signed up with." />
      )}
    </Card>
  );
}
