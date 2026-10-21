import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import type { OwnerDocument } from "../documents";
import { sizeLabel } from "../documents";
import { usDate } from "../money";

// What the signed-in owner sees of the files, to download: their own documents, their
// properties' and units' files, and their bills' receipts (q046), each saying what it is about.
export function OwnerDocuments() {
  const [documents, setDocuments] = useState<OwnerDocument[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api
      .get<OwnerDocument[]>("/api/owner/documents")
      .then(setDocuments)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, []);
  return (
    <Card title="Documents">
      {documents === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : documents.length === 0 ? (
        <p className="text-sm text-ink-muted">No documents yet.</p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm" aria-label="Your documents">
          {documents.map((d) => (
            <li key={d.id}>
              <a className="text-ink underline underline-offset-2" href={`/api/owner/documents/${d.id}/file`}>
                {d.title}
              </a>{" "}
              <span className="text-ink-muted">
                ({d.about_name ? `${d.about_name}, ` : ""}{sizeLabel(d.size)}, {usDate(d.uploaded_at)})
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
