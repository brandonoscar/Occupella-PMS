import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import type { ContentType } from "../documents";
import { MAX_BYTES, titleOf, toBase64, typeOf } from "../documents";
import { day, usDate } from "../money";

// The latest notice to move out a tenant on the lease gave (migration 20261103000100).
export type MoveOutNotice = { leaving_on: string; note: string | null; given_by: string; given_at: string };
export type Announcement = { id: string; property: string | null; title: string; body: string; at: string };

function words(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// What the company announced to the tenant (to every tenant, or at their property), newest
// first; nothing when there is none, so the Overview stays quiet.
export function AnnouncementsCard() {
  const [found, setFound] = useState<Announcement[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.get<Announcement[]>("/api/tenant/announcements").then(setFound).catch((f) => setError(words(f)));
  }, []);
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!found || found.length === 0) return null;
  return (
    <Card title="Announcements">
      <ul aria-label="Announcements" className="flex flex-col divide-y divide-line">
        {found.map((a) => (
          <li key={a.id} className="py-3">
            <p className="font-medium text-ink">{a.title}</p>
            <p className="mt-1 text-base whitespace-pre-wrap text-ink">{a.body}</p>
            <p className="mt-1 text-sm text-ink-subtle">
              {usDate(a.at)}{a.property ? ` · ${a.property}` : ""}
            </p>
          </li>
        ))}
      </ul>
    </Card>
  );
}

// A tenant tells the company the last day they will live there; a new notice replaces the one
// before. It ends nothing by itself: the company ends the lease and says what comes next.
export function MoveOutNoticeCard({
  leaseId,
  company,
  notice,
  onGiven,
}: {
  leaseId: string;
  company: string;
  notice: MoveOutNotice | null;
  onGiven: (given: MoveOutNotice) => void;
}) {
  const [leaving, setLeaving] = useState("");
  const [note, setNote] = useState("");
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");
  return (
    <Card title="Moving out">
      {notice ? (
        <p className="mb-4 text-base" data-testid="notice-given">
          You told {company} you are leaving on <strong>{usDate(notice.leaving_on)}</strong>
          {" "}(given by {notice.given_by} on {usDate(notice.given_at)}). To change the day, give notice again.
        </p>
      ) : (
        <p className="mb-4 text-base text-ink-muted">
          Leaving? Tell {company} the last day you will live here. Your lease says how much notice it asks.
        </p>
      )}
      <Form className="flex flex-col gap-3" onSubmit={async (event) => {
        event.preventDefault();
        setError("");
        setSaid("");
        try {
          const given = await api.post<MoveOutNotice>(`/api/tenant/leases/${encodeURIComponent(leaseId)}/move-out-notice`, {
            leaving_on: leaving,
            note: note.trim() || null,
          });
          setSaid(`${company} has your notice: leaving ${usDate(given.leaving_on)}.`);
          setNote("");
          onGiven(given);
        } catch (failed) {
          setError(words(failed));
        }
      }}>
        <Field label="Last day you will live here" id="leaving-on" type="date" required min={day(new Date())}
          value={leaving} onChange={(e) => setLeaving(e.target.value)} />
        <label htmlFor="leaving-note" className="flex flex-col gap-1.5 text-sm text-ink-muted">
          Anything to add (optional)
          <textarea id="leaving-note" maxLength={1000} rows={2} value={note}
            onChange={(e) => setNote(e.target.value)}
            className="rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink" />
        </label>
        <Notice tone="error">{error}</Notice>
        <div className="flex items-center gap-4">
          <Button type="submit" variant="secondary"
            says={`Sends your notice to ${company}. It doesn't end the lease: ${company} confirms the move-out and your deposit's return.`}>
            Give notice
          </Button>
          <Said>{said}</Said>
        </div>
      </Form>
    </Card>
  );
}

// A tenant files a document of their lease (an insurance certificate, a pet's records): a PDF,
// JPEG or PNG of at most 5 MB, which the company sees with the lease's files.
export function LeaseUploadForm({ leaseId, company, onFiled }: {
  leaseId: string;
  company: string;
  onFiled: () => void;
}) {
  const [file, setFile] = useState<{ title: string; type: ContentType; size: number; base64: string } | null>(null);
  const [title, setTitle] = useState("");
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");
  return (
    <Form className="mt-6 flex flex-col gap-3 border-t border-line pt-4" onSubmit={async (event) => {
      event.preventDefault();
      setError("");
      setSaid("");
      if (!file) return;
      if (file.size > MAX_BYTES) {
        setError("A file is at most 5 MB.");
        return;
      }
      try {
        await api.post(`/api/tenant/leases/${encodeURIComponent(leaseId)}/documents`, {
          title: title.trim() || file.title,
          content_type: file.type,
          content_base64: file.base64,
        });
        setSaid(`Sent to ${company}.`);
        setFile(null);
        setTitle("");
        onFiled();
      } catch (failed) {
        setError(words(failed));
      }
    }}>
      <p className="text-sm font-medium text-ink">Send a document to {company}</p>
      <label htmlFor="lease-file" className="flex flex-col gap-1.5 text-sm text-ink-muted">
        File (PDF, JPEG or PNG, up to 5 MB)
        <input id="lease-file" type="file" accept=".pdf,.jpg,.jpeg,.png" className="text-sm text-ink"
          onChange={async (e) => {
            const picked = e.target.files?.[0];
            const type = picked ? typeOf(picked) : null;
            if (!picked || !type) {
              setFile(null);
              if (picked) setError("A document is a PDF, JPEG or PNG.");
              return;
            }
            setError("");
            setFile({
              title: titleOf(picked.name),
              type,
              size: picked.size,
              base64: toBase64(new Uint8Array(await picked.arrayBuffer())),
            });
          }} />
      </label>
      <Field label="What it is (optional)" id="lease-file-title" maxLength={200} placeholder="Renters insurance certificate"
        value={title} onChange={(e) => setTitle(e.target.value)} />
      <Notice tone="error">{error}</Notice>
      <div className="flex items-center gap-4">
        <Button type="submit" variant="secondary" disabled={!file}
          says={`${company} sees it with your lease's files. Only ${company} can remove it.`}>
          Send document
        </Button>
        <Said>{said}</Said>
      </div>
    </Form>
  );
}
