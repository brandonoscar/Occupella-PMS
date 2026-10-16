import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import type { ContentType, Kind, Library } from "../documents";
import { MAX_BYTES, kindLabel, sizeLabel, titleOf, toBase64, typeOf } from "../documents";
import { usDate } from "../money";
import { useRecord } from "./useRecord";

type Chosen = { type: ContentType | null; size: number; base64: string };

// The Files card of an owner's, a vendor's or a lease's page (ADR 0005): the documents about that
// record, each to download or remove, and one more to upload. It reads and writes what Files
// does (/api/documents), kept to this record's one kind of document.
export function FilesCard({ kind, aboutId, name }: { kind: Kind; aboutId: string; name: string }) {
  const [library, setLibrary] = useState<Library | null>(null);
  const [error, setError] = useState("");
  const { said, error: refused, record } = useRecord(refresh);
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<Chosen | null>(null);
  const [picker, setPicker] = useState(0);

  function refresh() {
    api
      .get<Library>("/api/documents")
      .then(setLibrary)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);

  const files = library?.documents.filter((d) => d.kind === kind && d.about_id === aboutId) ?? [];
  const problem =
    file === null ? "" : file.type === null ? "A file is a PDF, a JPEG or a PNG." : file.size > MAX_BYTES ? "A file is at most 5 MB." : "";
  return (
    <Card title="Files">
      {library === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : files.length === 0 ? (
        <p className="text-sm text-ink-subtle">No file yet.</p>
      ) : (
        <ul className="flex flex-col gap-2 text-base" aria-label={`Files, ${name}`}>
          {files.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-3">
              <a className="font-medium text-iris hover:text-iris-hover hover:underline" href={`/api/documents/${d.id}/file`}>
                {d.title}
              </a>
              <span className="text-ink-muted">
                {kindLabel(d.kind)}, {sizeLabel(d.size)}, uploaded {usDate(d.uploaded_at)}
              </span>
              <Button variant="secondary" aria-label={`Remove ${d.title}`}
                onClick={() => record(`/api/documents/${d.id}/remove`, {}, () => `Removed ${d.title}.`)}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
      {library !== null && (
        <form
          className="mt-4 flex flex-wrap items-end gap-3"
          onSubmit={async (event) => {
            event.preventDefault();
            if (file === null || problem) return;
            const body = { kind, about_id: aboutId, title, content_type: file.type, content_base64: file.base64 };
            if (await record<{ id: string }>("/api/documents", body, () => `Uploaded ${title.trim()}.`)) {
              setTitle("");
              setFile(null);
              setPicker((n) => n + 1);
            }
          }}
        >
          <label htmlFor={`file-${aboutId}`} className="flex flex-col gap-1.5 text-sm text-ink-muted">
            {kindLabel(kind)} file
            <input key={picker} id={`file-${aboutId}`} type="file" accept=".pdf,.jpg,.jpeg,.png" className="text-sm text-ink"
              onChange={async (e) => {
                const picked = e.target.files?.[0];
                if (!picked) return setFile(null);
                const bytes = new Uint8Array(await picked.arrayBuffer());
                setFile({ type: typeOf(picked), size: picked.size, base64: toBase64(bytes) });
                setTitle((current) => current || titleOf(picked.name));
              }} />
          </label>
          <Field label="Title" id={`file-title-${aboutId}`} required value={title} onChange={(e) => setTitle(e.target.value)} />
          <Button type="submit" disabled={file === null || problem !== ""}>Upload</Button>
        </form>
      )}
      <Notice tone="error">{problem}</Notice>
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{refused}</Notice>
    </Card>
  );
}
