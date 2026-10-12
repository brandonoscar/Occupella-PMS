import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card, Shell } from "../components/Shell";
import type { ContentType, Kind, Library } from "../documents";
import { KINDS, MAX_BYTES, kindLabel, sizeLabel, titleOf, toBase64, typeOf } from "../documents";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

// Staff's documents: a vendor's W-9, a bill's receipt, a lease's move-out photos and an owner's
// documents (owners read their own in the portal). Files are PDF, JPEG or PNG, at most 5 MB.
// The PMS reads nothing out of them.
export function DocumentsPage({ me }: { me: Me }) {
  const [library, setLibrary] = useState<Library | null>(null);
  const [error, setError] = useState("");
  const { said, error: refused, record } = useRecord(refresh);

  function refresh() {
    api
      .get<Library>("/api/documents")
      .then(setLibrary)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <h1 className="font-display text-xl font-medium">Documents</h1>
      {library === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <UploadCard library={library} refresh={refresh} />
          <Card title="Kept documents">
            {library.documents.length === 0 ? (
              <p className="text-sm text-ink-muted">No document yet.</p>
            ) : (
              <ul className="flex flex-col gap-2 text-sm" aria-label="Documents">
                {library.documents.map((d) => (
                  <li key={d.id} className="flex flex-wrap items-center gap-3">
                    <a className="text-ink underline underline-offset-2" href={`/api/documents/${d.id}/file`}>
                      {d.title}
                    </a>
                    <span className="text-ink-muted">
                      {kindLabel(d.kind)}, {d.about}, {sizeLabel(d.size)}, by {d.uploaded_by ?? "the server"}{" "}
                      on {d.uploaded_at.slice(0, 10)}
                    </span>
                    <Button variant="secondary" aria-label={`Remove ${d.title}`}
                      onClick={() => record(`/api/documents/${d.id}/remove`, {}, () => `Removed ${d.title}.`)}>
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            <Notice tone="info">{said}</Notice>
            <Notice tone="error">{refused}</Notice>
          </Card>
        </>
      )}
    </Shell>
  );
}

type Chosen = { title: string; type: ContentType | null; size: number; base64: string };

function UploadCard({ library, refresh }: { library: Library; refresh: () => void }) {
  const { said, error, record } = useRecord(refresh);
  const [kind, setKind] = useState<Kind>("w9");
  const [about, setAbout] = useState("");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<Chosen | null>(null);
  const subjects = library.subjects[kind];
  const chosen = subjects.some((s) => s.id === about) ? about : (subjects[0]?.id ?? "");
  const problem =
    file === null ? "" : file.type === null ? "A document is a PDF, a JPEG or a PNG." : file.size > MAX_BYTES ? "A document is at most 5 MB." : "";
  return (
    <Card title="Add a document">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          if (file === null || problem) return;
          const body = { kind, about_id: chosen, title, content_type: file.type, content_base64: file.base64 };
          if (await record<{ id: string }>("/api/documents", body, () => `Kept ${title.trim()}.`)) {
            setTitle("");
            setFile(null);
          }
        }}
      >
        <Select label="Kind" id="document-kind" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
          {KINDS.map((k) => (
            <option key={k.kind} value={k.kind}>
              {k.label}
            </option>
          ))}
        </Select>
        <Select label={KINDS.find((k) => k.kind === kind)?.about ?? "About"} id="document-about" value={chosen}
          onChange={(e) => setAbout(e.target.value)}>
          {subjects.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </Select>
        <label htmlFor="document-file" className="flex flex-col gap-1.5 text-sm text-ink-muted">
          File
          <input id="document-file" type="file" accept=".pdf,.jpg,.jpeg,.png" className="text-sm text-ink"
            onChange={async (e) => {
              const picked = e.target.files?.[0];
              if (!picked) return setFile(null);
              const bytes = new Uint8Array(await picked.arrayBuffer());
              setFile({ title: picked.name, type: typeOf(picked), size: picked.size, base64: toBase64(bytes) });
              setTitle((current) => current || titleOf(picked.name));
            }} />
        </label>
        <Field label="Title" id="document-title" required value={title} onChange={(e) => setTitle(e.target.value)} />
        <Button type="submit" disabled={file === null || chosen === "" || problem !== ""}>Keep it</Button>
      </form>
      {subjects.length === 0 && (
        <p className="mt-3 text-sm text-ink-subtle">Nothing to keep a {kindLabel(kind)} for yet.</p>
      )}
      <Notice tone="error">{problem}</Notice>
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}
