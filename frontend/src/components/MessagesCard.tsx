import { MessagesSquare } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type { Keep } from "../latest";
import { keepAll, latest } from "../latest";
import { usDate } from "../money";
import { Button } from "./Button";
import { EmptyState } from "./EmptyState";
import { Form } from "./Form";
import { Notice } from "./Notice";
import { Said } from "./Said";
import { Card } from "./Shell";

export type Message = {
  id: string;
  by: "person" | "company";
  // The staff member who wrote it, which staff see; the portals show the company's name.
  staff: string | null;
  body: string;
  at: string;
};

// A short time for a message: the day, and the time of day in the reader's own zone.
export function whenSent(at: string): string {
  const time = new Date(at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  return `${usDate(at)} ${time}`;
}

// One conversation between the company and a tenant or an owner (migration 20261103000100), oldest
// first, with a box to write in it. In a portal, `path` is the person's own conversation and the
// company's messages read as the company's; for staff, `path` names the person and each of the
// company's messages names the staff member who wrote it.
export function MessagesCard({
  path,
  company,
  person,
  staff = false,
  title = "Messages",
}: {
  path: string;
  company: string;
  person: string;
  staff?: boolean;
  title?: string;
}) {
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [body, setBody] = useState("");
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");

  function refresh(keep: Keep = keepAll) {
    api.get<Message[] | { messages: Message[] }>(path)
      .then(keep((found) => setMessages(Array.isArray(found) ? found : found.messages)))
      .catch(keep((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed))));
  }
  useEffect(() => {
    const run = latest();
    refresh(run.keep);
    return run.stop;
  }, [path]);

  const author = (m: Message) =>
    m.by === "person" ? (staff ? person : "You") : staff ? `${m.staff ?? "Staff"} (${company})` : company;

  return (
    <Card title={title}>
      <Notice tone="error">{error}</Notice>
      {messages !== null && messages.length === 0 && (
        <EmptyState icon={MessagesSquare} title="No messages yet."
          body={staff ? `Write to ${person} below; they read it in their portal.`
            : `Write to ${company} below. Their answer shows here, and by email when they have yours.`} />
      )}
      {messages !== null && messages.length > 0 && (
        <ul aria-label={`Messages with ${staff ? person : company}`} className="flex flex-col gap-3">
          {messages.map((m) => {
            const mine = staff ? m.by === "company" : m.by === "person";
            return (
              <li key={m.id}
                className={"max-w-[85%] rounded-md border px-3 py-2 " + (mine
                  ? "self-end border-card-edge bg-raised"
                  : "self-start border-line bg-canvas")}>
                <p className="text-sm text-ink-muted">{author(m)} · {whenSent(m.at)}</p>
                <p className="mt-1 text-base whitespace-pre-wrap text-ink">{m.body}</p>
              </li>
            );
          })}
        </ul>
      )}
      <Form className="mt-6 flex flex-col gap-2" onSubmit={async (event) => {
        event.preventDefault();
        setError("");
        setSaid("");
        try {
          await api.post(path, { body });
          setBody("");
          setSaid(staff ? `Sent to ${person}.` : `Sent to ${company}.`);
          refresh();
        } catch (failed) {
          setError(failed instanceof ApiError ? failed.detail : String(failed));
        }
      }}>
        <label htmlFor="message-body" className="text-sm text-ink-muted">
          {staff ? `Write to ${person}` : `Write to ${company}`}
        </label>
        <textarea id="message-body" required maxLength={4000} rows={4} value={body}
          onChange={(event) => setBody(event.target.value)}
          className="rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink" />
        <div className="flex items-center gap-4">
          <Button type="submit"
            says={staff
              ? `${person} reads it in their portal, and by email when one is on file. It can't be taken back.`
              : `${company} reads it and answers here. It can't be taken back; for an emergency, call them.`}>
            Send message
          </Button>
          <Said>{said}</Said>
        </div>
      </Form>
    </Card>
  );
}
