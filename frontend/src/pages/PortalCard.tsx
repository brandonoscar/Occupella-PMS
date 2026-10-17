import { useState } from "react";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
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

// An invite to sign in (ADR 0006: invites are on the person's own page): the link is shown once,
// to copy and send yourself, since the PMS sends no invite email, and a new one resets the
// password. A disabled login takes no invite.
export function InviteForm({ kind, personId, name, portal, email, refresh }: Invitee) {
  const [address, setAddress] = useState(email ?? "");
  const [link, setLink] = useState("");
  const { said, error, record } = useRecord(refresh);
  if (portal === "disabled") return <p className="text-sm text-ink-subtle">This login is disabled for good.</p>;
  return (
    <div className="flex flex-col gap-3">
      <Form className="flex flex-wrap items-end gap-3" onSubmit={(event) => {
        event.preventDefault();
        return record<{ url: string }>("/api/invites", { kind, person_id: personId, email: address.trim() }, (made) => {
          setLink(made.url);
          return `Invite link made for ${address.trim()}; give it to ${name}.`;
        });
      }}>
        <Field label="Email to sign in with" id={`login-email-${personId}`} type="email" required value={address}
          onChange={(event) => setAddress(event.target.value)} />
        <Button type="submit" variant="secondary">
          {portal !== "no login" ? "Send a new invite" : kind === "staff" ? "Invite to sign in" : "Invite to the portal"}
        </Button>
      </Form>
      {link && <input readOnly aria-label={`Invite link, ${name}`} value={link}
        className="w-full rounded-sm border border-line-strong bg-sunken px-2 py-1.5 font-mono text-sm break-all text-ink" />}
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </div>
  );
}

// A person's portal login on their page: its state, its email and the invite.
export function PortalCard(invitee: Invitee & { loginEmail: string | null }) {
  return (
    <Card title="Portal">
      <p className="mb-3 flex flex-wrap items-center gap-2 text-base">
        <Badge tone={PORTAL[invitee.portal].tone}>{PORTAL[invitee.portal].word}</Badge>
        {invitee.loginEmail && <span className="text-ink-muted">{invitee.loginEmail}</span>}
      </p>
      <InviteForm {...invitee} email={invitee.loginEmail ?? invitee.email} />
    </Card>
  );
}
