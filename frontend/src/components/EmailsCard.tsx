import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type { Keep } from "../latest";
import { keepAll, latest } from "../latest";
import { usDate } from "../money";
import type { BadgeTone } from "./Badge";
import { Badge } from "./Badge";
import { Button } from "./Button";
import { Field } from "./Field";
import { Form } from "./Form";
import { Notice } from "./Notice";
import { Said } from "./Said";
import { Card } from "./Shell";
import { SidePanel } from "./SidePanel";

export type EmailAbout = "tenant" | "owner" | "vendor";
export type EmailLine = {
  id: string;
  kind:
    | "message" | "invite" | "password_reset" | "bill_approval"
    | "rent_reminder" | "late_notice" | "owner_statement" | "work_order" | "adverse_action"
    | "payment_receipt" | "announcement";
  to: string;
  subject: string;
  body: string;
  at: string;
  by: string | null;
  status: "sent" | "waiting" | "failed";
  error: string | null;
};

const KIND: Record<EmailLine["kind"], string> = {
  message: "Email",
  invite: "Portal invite",
  password_reset: "Password reset",
  bill_approval: "Bill waiting for approval",
  rent_reminder: "Rent reminder",
  late_notice: "Late fee notice",
  owner_statement: "Monthly statement",
  work_order: "Work order",
  adverse_action: "Adverse action notice",
  payment_receipt: "Payment receipt",
  announcement: "Announcement",
};
// The PDF an email carries, made as it is sent (src/pms/attachments.py).
const ATTACHED: Partial<Record<EmailLine["kind"], string>> = {
  owner_statement: "The month's statement attached (PDF)",
  payment_receipt: "The receipt attached (PDF)",
};
const STATUS: Record<EmailLine["status"], { word: string; tone: BadgeTone }> = {
  sent: { word: "Sent", tone: "positive" },
  waiting: { word: "Waiting to send", tone: "info" },
  failed: { word: "Not sent", tone: "danger" },
};

// Writing an email, in a side panel (ADR 0007): a subject and the text, signed with the staff
// member's name and the company's when it goes. `onSend` answers why it was refused, or "".
export function WriteEmail({ title, onSend, onClose, note }: {
  title: string;
  onSend: (subject: string, body: string) => Promise<string>;
  onClose: () => void;
  note?: string;
}) {
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState("");
  return (
    <SidePanel title={title} onClose={onClose}>
      {note && <p className="text-sm text-ink-muted">{note}</p>}
      <Form className="flex flex-col gap-3" onSubmit={async (event) => {
        event.preventDefault();
        setError("");
        const refused = await onSend(subject.trim(), body.trim());
        if (refused) setError(refused);
        else onClose();
      }}>
        <Field label="Subject" id="email-subject" required maxLength={200} value={subject}
          onChange={(event) => setSubject(event.target.value)} />
        <label htmlFor="email-body" className="text-sm text-ink-muted">Message</label>
        <textarea id="email-body" required maxLength={8000} rows={8} value={body}
          onChange={(event) => setBody(event.target.value)}
          className="rounded-sm border border-line-strong bg-sunken px-2 py-1.5 text-base text-ink" />
        <p className="text-sm text-ink-subtle">Your name and the company's are added below it.</p>
        <Notice tone="error">{error}</Notice>
        <div><Button type="submit">Send email</Button></div>
      </Form>
    </SidePanel>
  );
}

// A person's Email tab (ADR 0007): every email the PMS wrote them, newest first (what staff
// wrote, portal invites, password resets, an owner's bills waiting for approval, and what the PMS
// writes by itself), each with whether it went, and Write email. An invite's link is never kept,
// so it isn't shown. An application's applicants are written to only by the PMS itself (their
// adverse action notice), so theirs has no Write email.
export function EmailsCard({ about, aboutId, name, emailOn }: {
  about: EmailAbout | "application";
  aboutId: string;
  name: string;
  emailOn?: boolean;
}) {
  const [emails, setEmails] = useState<EmailLine[] | null>(null);
  const [writing, setWriting] = useState(false);
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");
  const query = new URLSearchParams({ about, about_id: aboutId });

  function refresh(keep: Keep = keepAll) {
    api.get<EmailLine[]>(`/api/emails?${query}`).then(keep(setEmails))
      .catch(keep((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed))));
  }
  useEffect(() => {
    const run = latest();
    refresh(run.keep);
    return run.stop;
  }, [about, aboutId]);

  async function send(subject: string, body: string) {
    setSaid("");
    try {
      const sent = await api.post<{ to: string }>("/api/emails", { about, about_id: aboutId, subject, body });
      setSaid(emailOn === false ? `Email to ${sent.to} waits to send.` : `Email sent to ${sent.to}.`);
      refresh();
      return "";
    } catch (failed) {
      return failed instanceof ApiError ? failed.detail : String(failed);
    }
  }

  return (
    <Card title="Email">
      {about !== "application" && (
        <div className="mb-3"><Button type="button" variant="secondary" onClick={() => setWriting(true)}>Write email</Button></div>
      )}
      {emailOn === false && about !== "application" && (
        <Notice tone="info">This server sends no email yet: what you write waits until it does.</Notice>
      )}
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
      {emails !== null && emails.length === 0 && <p className="text-sm text-ink-subtle">No email to {name} yet.</p>}
      {emails !== null && emails.length > 0 && (
        <ul aria-label={`Email, ${name}`} className="flex flex-col divide-y divide-line">
          {emails.map((line) => (
            <li key={line.id} className="flex flex-col gap-1 py-3">
              <p className="flex flex-wrap items-center gap-2 text-base text-ink">
                <span className="font-medium">{line.subject}</span>
                <Badge tone={STATUS[line.status].tone}>{STATUS[line.status].word}</Badge>
              </p>
              <p className="text-sm text-ink-subtle">
                {KIND[line.kind]} to {line.to} · {line.by ?? "Sent by the PMS"} · {usDate(line.at)}
              </p>
              {ATTACHED[line.kind] && <p className="text-sm text-ink-muted">{ATTACHED[line.kind]}</p>}
              {line.status === "failed" && line.error && <p className="text-sm text-danger">{line.error}</p>}
              <details className="text-sm text-ink-muted">
                <summary className="cursor-pointer">Show the email</summary>
                <p className="mt-2 whitespace-pre-wrap text-ink">{line.body}</p>
              </details>
            </li>
          ))}
        </ul>
      )}
      {writing && <WriteEmail title={`Email ${name}`} onSend={send} onClose={() => setWriting(false)} />}
    </Card>
  );
}
