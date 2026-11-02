import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Select } from "../components/Select";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import type { ContentType, Kind, Library } from "../documents";
import { MAX_BYTES, kindLabel, sizeLabel, titleOf, toBase64, typeOf } from "../documents";
import { usDate } from "../money";
import { useRecord } from "./useRecord";
import { Said } from "../components/Said";
import { Form } from "../components/Form";
import { AskFirst } from "../components/Confirm";

type Chosen = { type: ContentType | null; size: number; base64: string };

// Who sees a file once it is uploaded, said under Upload (rule 1): the portal or public page that
// shows its kind (src/pms/api/owner.py's OWNER_SEES, tenant.py's lease files, a deposit
// statement's photos, listings.py), else staff alone. An upload writes no email.
export function uploadSays(kind: Kind): string {
  switch (kind) {
    case "owner_document":
      return "Keeps it on file; the owner sees it in their portal. Nothing is emailed.";
    case "property_document":
    case "unit_document":
      return "Keeps it on file; the property's owners see it in their portal. Nothing is emailed.";
    case "receipt":
      return "Keeps it with the bill; its owner sees it in their portal. Nothing is emailed.";
    case "lease_document":
      return "Keeps it with the lease; its tenants see it in their portal. Nothing is emailed.";
    case "listing_photo":
      return "Shows on the unit's public listing while the unit is listed. Nothing is emailed.";
    case "move_out_photo":
      return "Keeps it with the lease; it goes with the deposit statement the tenants get. Nothing is emailed.";
    case "tenant_document":
    case "vendor_document":
    case "w9":
    case "work_order_photo":
      return "Keeps it on file; only staff see it. Nothing is emailed.";
    default:
      return "Keeps it on file. Nothing is emailed.";
  }
}

// The Files card of a record's page (ADR 0005, ADR 0006): the documents about that record, each
// to download or remove, and one more to upload. It reads and writes what Files does
// (/api/documents), kept to this record's kind of document and any `also` its record takes
// (a lease's move-out photos beside its documents, a vendor's other documents beside their W-9s),
// chosen as the upload's category, and any `listed` kind that comes some other way (a lease's
// payments' files, kept as each payment is received). `note` says who else sees them.
export function FilesCard({ kind, also = [], listed = [], aboutId, name, note }: {
  kind: Kind;
  also?: Kind[];
  listed?: Kind[];
  aboutId: string;
  name: string;
  note?: string;
}) {
  const kinds = [kind, ...also];
  const [category, setCategory] = useState<Kind>(kind);
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

  const files = library?.documents.filter((d) => [...kinds, ...listed].includes(d.kind) && d.about_id === aboutId) ?? [];
  const problem =
    file === null ? "" : file.type === null ? "A file is a PDF, a JPEG or a PNG." : file.size > MAX_BYTES ? "A file is at most 5 MB." : "";
  return (
    <Card title="Files">
      {note && <p className="mb-3 text-sm text-ink-subtle">{note}</p>}
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
              <AskFirst variant="secondary" aria-label={`Remove ${d.title}`} danger
                question={`Remove ${d.title}? It leaves every list here and in the portals, and its download stops. Its record stays.`}
                says="Takes it off every list, here and in the portals, and stops its download. Its record stays."
                onConfirm={() => record(`/api/documents/${d.id}/remove`, {}, () => `Removed ${d.title}.`)}>
                Remove
              </AskFirst>
            </li>
          ))}
        </ul>
      )}
      {library !== null && (
        <Form
          className="mt-4 flex flex-wrap items-end gap-3"
          onSubmit={async (event) => {
            event.preventDefault();
            if (file === null || problem) return;
            const body = { kind: category, about_id: aboutId, title, content_type: file.type, content_base64: file.base64 };
            if (await record<{ id: string }>("/api/documents", body, () => `Uploaded ${title.trim()}.`)) {
              setTitle("");
              setFile(null);
              setPicker((n) => n + 1);
            }
          }}
        >
          {also.length > 0 && (
            <Select label="Category" id={`file-category-${aboutId}`} value={category}
              onChange={(e) => setCategory(e.target.value as Kind)}>
              {kinds.map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}
            </Select>
          )}
          <label htmlFor={`file-${aboutId}`} className="flex flex-col gap-1.5 text-sm text-ink-muted">
            {kindLabel(category)} file
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
          <Button type="submit" disabled={file === null || problem !== ""} says={uploadSays(category)}>Upload</Button>
        </Form>
      )}
      <Notice tone="error">{problem}</Notice>
      <Said>{said}</Said>
      <Notice tone="error">{refused}</Notice>
    </Card>
  );
}
