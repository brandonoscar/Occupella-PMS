import { useState } from "react";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import type { Portal } from "../rentals";
import { PORTAL } from "../rentals";
import { useRecord } from "./useRecord";

type Invitee = {
  kind: "staff" | "owner" | "tenant";
  personId: string;
  name: string;
  portal: Portal;
  // The login's email, or else the contact email to start from.
  email: string | null;
  refresh: () => void;
};

type Made = { url: string; emailed: boolean };

function said(made: Made, address: string, name: string): string {
  return made.emailed
    ? `Invite emailed to ${address}. The link is below too, shown once.`
    : `Invite link made for ${address}; give it to ${name}.`;
}

function InviteLink({ link, name }: { link: string; name: string }) {
  return link ? (
    <input readOnly aria-label={`Invite link, ${name}`} value={link}
      className="w-full rounded-sm border border-line-strong bg-sunken px-2 py-1.5 font-mono text-sm break-all text-ink" />
  ) : null;
}

// An invite to sign in, typed to an email (staff, from the staff page): emailed when the server
// sends email (ADR 0007), and its link shown once too, to copy and send yourself if the email
// doesn't arrive. A new one resets the password. A disabled login takes no invite.
export function InviteForm({ kind, personId, name, portal, email, refresh }: Invitee) {
  const [address, setAddress] = useState(email ?? "");
  const [link, setLink] = useState("");
  const { said: done, error, record } = useRecord(refresh);
  if (portal === "disabled") return <p className="text-sm text-ink-subtle">This login is disabled for good.</p>;
  return (
    <div className="flex flex-col gap-3">
      <Form className="flex flex-wrap items-end gap-3" onSubmit={(event) => {
        event.preventDefault();
        return record<Made>("/api/invites", { kind, person_id: personId, email: address.trim() }, (made) => {
          setLink(made.url);
          return said(made, address.trim(), name);
        });
      }}>
        <Field label="Email to sign in with" id={`login-email-${personId}`} type="email" required value={address}
          onChange={(event) => setAddress(event.target.value)} />
        <Button type="submit" variant="secondary">
          {portal !== "no login" ? "Send a new invite" : kind === "staff" ? "Invite to sign in" : "Invite to the portal"}
        </Button>
      </Form>
      <InviteLink link={link} name={name} />
      <Said>{done}</Said>
      <Notice tone="error">{error}</Notice>
    </div>
  );
}

// An owner's or a tenant's portal login on their page (ADR 0010 phase 3): its state and, in one
// click, an invite to the email in their contact details, named before it goes (rule 3). With no
// email on file it says so and opens their contact details to add one.
export function PortalCard({ kind, personId, name, portal, loginEmail, contactEmail, refresh, onEditContact }: {
  kind: "owner" | "tenant";
  personId: string;
  name: string;
  portal: Portal;
  loginEmail: string | null;
  contactEmail: string | null;
  refresh: () => void;
  onEditContact: () => void;
}) {
  const [link, setLink] = useState("");
  const { said: done, error, record } = useRecord(refresh);
  const { ask, prompt } = useConfirm();
  const again = portal !== "no login";
  const label = again ? "Send a new invite" : "Invite to the portal";
  return (
    <Card title="Portal">
      <div className="flex flex-col gap-3">
        <p className="flex flex-wrap items-center gap-2 text-base">
          <Badge tone={PORTAL[portal].tone}>{PORTAL[portal].word}</Badge>
          {loginEmail && <span className="text-ink-muted">Signs in as {loginEmail}</span>}
        </p>
        {portal === "disabled" ? (
          <p className="text-sm text-ink-subtle">This login is disabled for good.</p>
        ) : contactEmail === null ? (
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm text-ink-muted">No email on file, so no invite can go to {name}.</p>
            <Button variant="secondary" size="sm" onClick={onEditContact}>Edit contact details</Button>
          </div>
        ) : (
          <>
            <div>
              <Button variant="secondary" onClick={() => ask(
                `Send ${name} an invite to the portal at ${contactEmail}?`
                  + (again ? " Using it sets a new password; the one they had stops working." : ""),
                () => record<Made>("/api/invites", { kind, person_id: personId, email: contactEmail }, (made) => {
                  setLink(made.url);
                  return said(made, contactEmail, name);
                }), "Send invite")}>
                {label}
              </Button>
            </div>
            <p className="text-sm text-ink-subtle">
              Goes to {contactEmail}, the email in their contact details. The link works once.
            </p>
          </>
        )}
        <InviteLink link={link} name={name} />
        <Said>{done}</Said>
        <Notice tone="error">{error}</Notice>
        {prompt}
      </div>
    </Card>
  );
}
