import { Megaphone } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import type { Announcement, AnnouncementLine, Audience, Recipients, Sent } from "../announcements";
import { goesTo, sentLine, wentLine } from "../announcements";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import type { Groups } from "../components/ScopePicker";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { latest } from "../latest";
import { usDate } from "../money";
import type { Paged } from "../rentals";
import { navigate } from "../router";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

const STATUS: Record<Announcement["emails"][number]["status"], "positive" | "neutral" | "danger"> = {
  sent: "positive",
  waiting: "neutral",
  failed: "danger",
};

// Communication → Announcements (migration 20261023000203): what staff wrote once to every
// tenant living at, or every owner of, a property, a property group or the whole company, newest
// first, each with how many of its emails went. New announcement says who it will go to before it
// is sent; each opens its page with every email and whether it went.
export function AnnouncementsPage({ me }: { me: Me }) {
  const [number, setNumber] = useState(1);
  const [page, setPage] = useState<Paged<AnnouncementLine> | null>(null);
  const [loadError, setLoadError] = useState("");
  const [writing, setWriting] = useState(false);
  const [tick, setTick] = useState(0);
  const [said, setSaid] = useState("");

  useEffect(() => {
    const run = latest();
    api.get<Paged<AnnouncementLine>>(`/api/announcements?page=${number}`).then(run.keep(setPage))
      .catch(run.keep((why) => setLoadError(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
  }, [number, tick]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Announcements" actions={<Button onClick={() => setWriting(true)}>New announcement</Button>} />
      <Said>{said}</Said>
      {page === null ? (
        <Notice tone={loadError ? "error" : "info"}>{loadError || "Loading"}</Notice>
      ) : page.items.length === 0 ? (
        <EmptyState icon={Megaphone} title="No announcement yet."
          body="An announcement goes by email to every tenant living at, or every owner of, a property, a group or the whole company." />
      ) : (
        <>
          <Wide>
            <table className="w-full text-base" aria-label="Announcements">
              <thead>
                <tr>
                  <th className="text-left">Subject</th>
                  <th className="text-left">To</th>
                  <th className="text-left">By</th>
                  <th className="text-left">Sent on</th>
                  <th className="text-left">Emails</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((line) => (
                  <tr key={line.id} aria-label={line.subject} className="cursor-pointer hover:bg-raised"
                    onClick={() => navigate(`/announcements/${line.id}`)}>
                    <td>
                      <GoLink href={`/announcements/${line.id}`} className="font-medium text-iris hover:text-iris-hover hover:underline">
                        {line.subject}
                      </GoLink>
                    </td>
                    <td>{line.words}</td>
                    <td>{line.by}</td>
                    <td className="whitespace-nowrap">{usDate(line.at.slice(0, 10))}</td>
                    <td className="text-ink-muted">{wentLine(line)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
          <Pager page={page} onPage={setNumber} />
        </>
      )}
      {writing && (
        <SidePanel title="New announcement" onClose={() => setWriting(false)}>
          <AnnounceForm onSent={(words) => { setWriting(false); setSaid(words); setTick((t) => t + 1); }} />
        </SidePanel>
      )}
    </Shell>
  );
}

// Who it goes to (tenants or owners; the whole company, a group or a property), shown as it is
// picked, then the subject and the message.
function AnnounceForm({ onSent }: { onSent: (said: string) => void }) {
  const [places, setPlaces] = useState<Groups | null>(null);
  const [audience, setAudience] = useState<Audience>("tenants");
  const [where, setWhere] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [found, setFound] = useState<Recipients | null>(null);
  const { error, record } = useRecord(() => undefined);
  useEffect(() => {
    api.get<Groups>("/api/property-groups").then(setPlaces).catch(() => setPlaces(null));
  }, []);
  const place: Record<string, string> = where.startsWith("group:")
    ? { group_id: where.slice(6) }
    : where.startsWith("property:") ? { property_id: where.slice(9) } : {};
  const query = new URLSearchParams({ audience, ...place }).toString();
  useEffect(() => {
    const run = latest();
    setFound(null);
    api.get<Recipients>(`/api/announcements/recipients?${query}`).then(run.keep(setFound))
      .catch(run.keep(() => setFound(null)));
    return run.stop;
  }, [query]);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record<Sent>("/api/announcements", { audience, ...place, subject, body }, (sent) => {
        const words = sentLine(sent, audience);
        onSent(words);
        return words;
      }).then(() => undefined);
    }}>
      <Select label="To" id="announce-audience" value={audience} onChange={(e) => setAudience(e.target.value as Audience)}>
        <option value="tenants">Tenants living there</option>
        <option value="owners">Owners</option>
      </Select>
      <Select label="Of" id="announce-where" value={where} onChange={(e) => setWhere(e.target.value)}>
        <option value="">The whole company</option>
        {places?.groups.map((g) => <option key={g.id} value={`group:${g.id}`}>{`Group: ${g.name}`}</option>)}
        {places?.properties.map((p) => <option key={p.id} value={`property:${p.id}`}>{p.name}</option>)}
      </Select>
      <p className="text-sm text-ink-muted" data-testid="announce-goes-to" aria-live="polite">
        {found ? goesTo(found, audience) : ""}
      </p>
      <Field label="Subject" id="announce-subject" required maxLength={200} value={subject} onChange={(e) => setSubject(e.target.value)} />
      <label htmlFor="announce-body" className="flex flex-col gap-1.5 text-sm text-ink-muted">
        Message
        <textarea id="announce-body" rows={6} required maxLength={8000} value={body} onChange={(e) => setBody(e.target.value)}
          className="rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink" />
      </label>
      <p className="text-sm text-ink-muted">Each email is signed with your name and the company&apos;s, and shows on each person&apos;s Email tab.</p>
      <div><Button type="submit">Send announcement</Button></div>
      <Notice tone="error">{error}</Notice>
    </Form>
  );
}

// One announcement: what it said, to whom, and each email with whether it went.
export function AnnouncementPage({ me, id }: { me: Me; id: string }) {
  const [found, setFound] = useState<Announcement | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const run = latest();
    api.get<Announcement>(`/api/announcements/${id}`).then(run.keep(setFound))
      .catch(run.keep((why) => setError(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
  }, [id]);
  if (found === null) {
    return (
      <Shell me={me} nav={STAFF_NAV}>
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      </Shell>
    );
  }
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={found.subject} />
      <p className="text-sm text-ink-muted">{`${found.words} · ${found.by} · ${usDate(found.at.slice(0, 10))}`}</p>
      <Card title="Message">
        <p className="text-base whitespace-pre-wrap">{found.body}</p>
      </Card>
      <Card title="Emails">
        {found.emails.length === 0 ? (
          <EmptyState icon={Megaphone} title="No one had an email on file." />
        ) : (
          <Wide>
            <table className="w-full text-base" aria-label="Emails">
              <thead>
                <tr>
                  <th className="text-left">To</th>
                  <th className="text-left">Address</th>
                  <th className="text-left">Status</th>
                </tr>
              </thead>
              <tbody>
                {found.emails.map((e) => (
                  <tr key={`${e.name}:${e.to}`} aria-label={e.name}>
                    <td>{e.name}</td>
                    <td className="text-sm">{e.to}</td>
                    <td>
                      <Badge tone={STATUS[e.status]}>{e.status}</Badge>
                      {e.error && <span className="block text-sm text-ink-muted">{e.error}</span>}
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
