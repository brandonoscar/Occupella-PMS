import { useState } from "react";
import type { Contact } from "../directory";
import { addressLines, CONTACT_FIELDS, contactBody, hasContact } from "../directory";
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
}: {
  path: string;
  contact: Contact;
  refresh: () => void;
  name: string;
}) {
  const { said, error, record } = useRecord(refresh);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(() => draftOf(contact));
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
            <Button type="submit" size="sm">Save contact details</Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </Form>
      )}
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </div>
  );
}

// The same, in a card of its own under its label.
export function ContactCard(props: { path: string; contact: Contact; refresh: () => void; name: string }) {
  return (
    <Card title="Contact details">
      <ContactDetails {...props} />
    </Card>
  );
}
