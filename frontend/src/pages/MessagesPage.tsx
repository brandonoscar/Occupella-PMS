import { MessagesSquare } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { MessagesCard, whenSent } from "../components/MessagesCard";
import { Notice } from "../components/Notice";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import { STAFF_NAV } from "./staffNav";

// One conversation in the inbox: its latest message, and whether it waits for an answer.
export type Thread = {
  about: "tenant" | "owner";
  person_id: string;
  name: string;
  last_at: string;
  last_by: "person" | "company";
  preview: string;
  waiting: boolean;
  count: number;
};

const CELL = "text-left";

// Every conversation with a tenant or an owner (the portals' second version), those waiting for
// an answer first; each opens at /messages/<tenant|owner>/<id>.
export function MessagesPage({ me }: { me: Me }) {
  const [threads, setThreads] = useState<Thread[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.get<Thread[]>("/api/messages").then(setThreads)
      .catch((f) => setError(f instanceof ApiError ? f.detail : String(f)));
  }, []);
  const waiting = threads?.filter((t) => t.waiting).length ?? 0;
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Messages"
        description="What tenants and owners write from their portals, and your answers. Each answer is emailed to them too." />
      <Notice tone="error">{error}</Notice>
      <Card title={waiting ? `Conversations (${waiting} waiting for an answer)` : "Conversations"}>
        {threads !== null && threads.length === 0 && (
          <EmptyState icon={MessagesSquare} title="No messages yet."
            body="Tenants and owners write from their portal's Messages; you can also write first from here once they have." />
        )}
        {threads !== null && threads.length > 0 && (
          <Wide>
            <table className="w-full text-base" aria-label="Conversations">
              <thead className="text-sm text-ink-subtle">
                <tr>
                  <th className={CELL}>With</th>
                  <th className={CELL}>Latest</th>
                  <th className={CELL}>When</th>
                  <th className={CELL}>Status</th>
                </tr>
              </thead>
              <tbody>
                {threads.map((t) => (
                  <tr key={`${t.about}-${t.person_id}`} className="border-t border-line">
                    <td className={CELL}>
                      <a className="font-medium text-iris hover:text-iris-hover hover:underline"
                        href={`/messages/${t.about}/${t.person_id}`}>{t.name}</a>
                      <span className="block text-sm text-ink-subtle">{t.about === "tenant" ? "Tenant" : "Owner"}</span>
                    </td>
                    <td className={`${CELL} text-ink-muted`}>
                      {t.last_by === "company" ? "You: " : ""}{t.preview}
                    </td>
                    <td className={`${CELL} whitespace-nowrap text-ink-muted`}>{whenSent(t.last_at)}</td>
                    <td className={CELL}>
                      {t.waiting ? <Badge tone="caution">Waiting</Badge> : <Badge tone="positive">Answered</Badge>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
        )}
      </Card>
    </Shell>
  );
}

// One conversation, for staff: a tenant's or an owner's messages and the company's answers, with
// a box to answer.
export function ConversationPage({ me, about, id }: { me: Me; about: "tenant" | "owner"; id: string }) {
  const [name, setName] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.get<{ name: string }>(`/api/messages/${about}/${id}`).then((c) => setName(c.name))
      .catch((f) => setError(f instanceof ApiError ? f.detail : String(f)));
  }, [about, id]);
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={name ?? "Messages"}
        description={about === "tenant" ? "A tenant's conversation with the company." : "An owner's conversation with the company."} />
      <p className="mb-4 flex gap-4 text-sm">
        <a className="text-iris hover:text-iris-hover hover:underline" href="/messages">All messages</a>
        <a className="text-iris hover:text-iris-hover hover:underline" href={`/${about}s/${id}`}>
          {about === "tenant" ? "Tenant's page" : "Owner's page"}
        </a>
      </p>
      <Notice tone="error">{error}</Notice>
      {name !== null && (
        <MessagesCard path={`/api/messages/${about}/${id}`} company={me.pmc_name} person={name} staff
          title="Conversation" />
      )}
    </Shell>
  );
}
