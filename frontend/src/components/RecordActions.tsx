import { useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "./Badge";
import { Button } from "./Button";
import { Field } from "./Field";
import { Notice } from "./Notice";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// A record's name and whether it is archived, changed in place: Rename opens a field, and
// Archive (or Bring back) asks before it does anything. The database refuses archiving while
// something is open on it, and says what in words.
export function RecordActions({
  path,
  name,
  archived,
  onChange,
}: {
  path: string;
  name: string;
  archived: boolean;
  onChange: (said: string) => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [asking, setAsking] = useState(false);
  const [draft, setDraft] = useState(name);
  const [error, setError] = useState("");

  async function post(to: string, body: object, said: string) {
    setError("");
    try {
      await api.post(`${path}/${to}`, body);
      setRenaming(false);
      setAsking(false);
      onChange(said);
    } catch (failed) {
      setError(detail(failed));
    }
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex flex-wrap items-center justify-end gap-2">
        {archived && <Badge tone="neutral">Archived</Badge>}
        <Button variant="secondary" size="sm" onClick={() => { setDraft(name); setRenaming(!renaming); }}>
          Rename
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setAsking(!asking)}>
          {archived ? "Bring back" : "Archive"}
        </Button>
      </div>
      {renaming && (
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            post("name", { name: draft }, `Renamed ${draft.trim()}.`);
          }}
        >
          <Field label="New name" id={`rename-${path}`} required maxLength={200} value={draft}
            onChange={(event) => setDraft(event.target.value)} />
          <Button type="submit" size="sm">Save name</Button>
        </form>
      )}
      {asking && (
        <div className="flex flex-wrap items-center justify-end gap-2 text-sm text-ink-muted">
          <span>
            {archived
              ? `${name} comes back into lists.`
              : `${name} leaves the lists; its history stays.`}
          </span>
          <Button size="sm" onClick={() => post("archive", { archived: !archived }, archived ? `${name} is back.` : `${name} is archived.`)}>
            {archived ? `Bring back ${name}` : `Archive ${name}`}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setAsking(false)}>Cancel</Button>
        </div>
      )}
      <Notice tone="error">{error}</Notice>
    </div>
  );
}
