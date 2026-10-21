import { FileText } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { ListBar } from "../components/ListBar";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import type { ContentType, FileRow, Kind, Library } from "../documents";
import { ATTACHED, KINDS, MAX_BYTES, kindLabel, sizeLabel, titleOf, toBase64, typeOf } from "../documents";
import { latest } from "../latest";
import { usDate } from "../money";
import type { Paged } from "../rentals";
import { scopeParams, useScope } from "../scope";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";
const FILTER = "h-9 rounded-sm border border-line-strong bg-canvas px-2 text-sm text-ink";

// Files (ADR 0006, step 12): every file of the company in one table, newest first, a page at a
// time: Name (to download), Attached to (the record's page), Category, Uploaded and By; narrowed
// by category and by property group (by where each file's record is). Upload opens a side panel:
// the file, what it is attached to (a property, unit, lease, tenant, owner, vendor, work order or
// bill) and its category. A file is a PDF, JPEG or PNG of at most 5 MB; the PMS reads nothing out
// of it, and an owner reads their owner documents in the portal.
export function DocumentsPage({ me }: { me: Me }) {
  const [scope, setScope] = useScope();
  const [kind, setKind] = useState<Kind | "">("");
  const [number, setNumber] = useState(1);
  const [page, setPage] = useState<Paged<FileRow> | null>(null);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));

  useEffect(() => {
    const run = latest();
    const query = new URLSearchParams({ page: String(number), ...scopeParams(scope) });
    if (kind) query.set("kind", kind);
    api
      .get<Paged<FileRow>>(`/api/documents/files?${query}`)
      .then(run.keep(setPage))
      .catch(run.keep((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed))));
    return run.stop;
  }, [scope, kind, number, runs]);
  const restart = <T,>(set: (value: T) => void) => (value: T) => { set(value); setNumber(1); };
  const uploadThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setUploading(false);
    return recorded;
  };

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Files" actions={<Button onClick={() => setUploading(true)}>Upload</Button>} />
      <ListBar scope={scope} onScope={restart(setScope)}>
        <select aria-label="Category" value={kind} onChange={(e) => restart(setKind)(e.target.value as Kind | "")} className={FILTER}>
          <option value="">All categories</option>
          {KINDS.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
        </select>
      </ListBar>
      <Said>{said}</Said>
      {!uploading && <Notice tone="error">{refused || error}</Notice>}
      {page === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : page.items.length === 0 ? (
        <EmptyState icon={FileText} title="No files yet" />
      ) : (
        <>
          <Wide>
            <table className="w-full text-base" aria-label="Files">
              <thead>
                <tr>
                  <th className="text-left">Name</th>
                  <th className="text-left">Attached to</th>
                  <th className="text-left">Category</th>
                  <th className="text-left">Uploaded</th>
                  <th className="text-left">By</th>
                  <th><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((d) => (
                  <tr key={d.id} aria-label={d.title}>
                    <td>
                      <a className={LINK} href={`/api/documents/${d.id}/file`}>{d.title}</a>
                      <span className="ml-2 text-sm text-ink-muted">{sizeLabel(d.size)}</span>
                    </td>
                    <td><GoLink href={d.about_path} className={LINK}>{d.about}</GoLink></td>
                    <td>{kindLabel(d.kind)}</td>
                    <td className="whitespace-nowrap">{usDate(d.uploaded_at)}</td>
                    <td className="text-ink-muted">{d.uploaded_by ?? "the server"}</td>
                    <td className="text-right">
                      <RowMenu label={d.title} actions={[{
                        label: "Remove", danger: true,
                        confirm: `Remove ${d.title}? It is no longer listed or downloaded; its record stays.`,
                        onSelect: () => record(`/api/documents/${d.id}/remove`, {}, () => `Removed ${d.title}.`),
                      }]} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
          <Pager page={page} onPage={setNumber} />
        </>
      )}
      {uploading && (
        <SidePanel title="Upload" onClose={() => setUploading(false)}>
          <UploadForm record={uploadThenClose} />
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

type Chosen = { type: ContentType | null; size: number; base64: string };

// The file, then what it is attached to and its category.
function UploadForm({ record }: { record: Recorder }) {
  const [library, setLibrary] = useState<Library | null>(null);
  const [unread, setUnread] = useState("");
  const [about, setAbout] = useState(ATTACHED[0]!.about);
  const [kind, setKind] = useState<Kind>(ATTACHED[0]!.kinds[0]!);
  const [record_, setRecord] = useState("");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<Chosen | null>(null);
  useEffect(() => {
    api.get<Library>("/api/documents").then(setLibrary)
      .catch((failed) => setUnread(failed instanceof ApiError ? failed.detail : String(failed)));
  }, []);
  if (library === null) return <Notice tone={unread ? "error" : "info"}>{unread || "Loading"}</Notice>;
  const kinds = ATTACHED.find((a) => a.about === about)?.kinds ?? [];
  const subjects = library.subjects[kind] ?? [];
  const chosen = subjects.some((s) => s.id === record_) ? record_ : (subjects[0]?.id ?? "");
  const problem =
    file === null ? "" : file.type === null ? "A file is a PDF, a JPEG or a PNG." : file.size > MAX_BYTES ? "A file is at most 5 MB." : "";
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      if (file === null || problem || !chosen) return;
      const body = { kind, about_id: chosen, title, content_type: file.type, content_base64: file.base64 };
      return record<{ id: string }>("/api/documents", body, () => `Uploaded ${title.trim()}.`);
    }}>
      <label htmlFor="upload-file" className="flex flex-col gap-1.5 text-sm text-ink-muted">
        File
        <input id="upload-file" type="file" accept=".pdf,.jpg,.jpeg,.png" className="text-sm text-ink"
          onChange={async (e) => {
            const picked = e.target.files?.[0];
            if (!picked) return setFile(null);
            const bytes = new Uint8Array(await picked.arrayBuffer());
            setFile({ type: typeOf(picked), size: picked.size, base64: toBase64(bytes) });
            setTitle((current) => current || titleOf(picked.name));
          }} />
      </label>
      <Field label="Name" id="upload-title" required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
      <Select label="Attached to" id="upload-about" value={about} onChange={(e) => {
        const next = e.target.value;
        setAbout(next);
        setKind(ATTACHED.find((a) => a.about === next)?.kinds[0] ?? kind);
        setRecord("");
      }}>
        {ATTACHED.map((a) => <option key={a.about} value={a.about}>{a.about}</option>)}
      </Select>
      <Select label={about} id="upload-record" value={chosen} onChange={(e) => setRecord(e.target.value)}>
        {subjects.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
      </Select>
      <Select label="Category" id="upload-category" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
        {kinds.map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}
      </Select>
      {subjects.length === 0 && <Notice tone="info">{`No ${about.toLowerCase()} to attach a file to yet.`}</Notice>}
      <Notice tone="error">{problem}</Notice>
      <div><Button type="submit" disabled={file === null || !chosen || problem !== ""}>Upload</Button></div>
    </Form>
  );
}
