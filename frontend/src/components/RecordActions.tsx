import { useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "./Badge";
import { Button } from "./Button";
import { Field } from "./Field";
import { Notice } from "./Notice";
import { Form } from "./Form";
import { RowMenu } from "./RowMenu";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// A record's name and whether it is archived, changed in place: Edit opens its name, and
// Archive (or Bring back) is in the ⋯ menu and asks before it does anything. The database refuses
// archiving while something is open on it, and says what in words.
export function RecordActions({
  path,
  name,
  archived,
  archivable = true,
  onChange,
}: {
  path: string;
  name: string;
  // Undefined: the record is renamed, never archived.
  archived?: boolean;
  // False hides Archive while it doesn't apply (ADR 0010, rule 4); Bring back always shows.
  archivable?: boolean;
  onChange: (said: string) => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(name);
  const [error, setError] = useState("");

  async function post(to: string, body: object, said: string) {
    setError("");
    try {
      await api.post(`${path}/${to}`, body);
      setRenaming(false);
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
          Edit
        </Button>
        {archived !== undefined && (archived || archivable) && (
          <RowMenu label={name} actions={[
            archived
              ? { label: "Bring back", confirm: `Bring back ${name}? It comes back into lists.`,
                  onSelect: () => post("archive", { archived: false }, `${name} is back.`) }
              : { label: "Archive", danger: true, confirm: `Archive ${name}? It leaves the lists; its history stays.`,
                  onSelect: () => post("archive", { archived: true }, `${name} is archived.`) },
          ]} />
        )}
      </div>
      {renaming && (
        <Form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            return post("name", { name: draft }, `Renamed ${draft.trim()}.`);
          }}
        >
          <Field label="Name" id={`rename-${path}`} required maxLength={200} value={draft}
            onChange={(event) => setDraft(event.target.value)} />
          <Button type="submit" size="sm">Save</Button>
        </Form>
      )}
      <Notice tone="error">{error}</Notice>
    </div>
  );
}
