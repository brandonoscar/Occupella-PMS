import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { usDate } from "../money";
import { Button } from "./Button";
import { Form } from "./Form";
import { Notice } from "./Notice";
import { Said } from "./Said";
import { Card } from "./Shell";

export type NoteAbout = "property" | "unit" | "lease" | "tenant" | "owner" | "vendor";
type Note = { id: string; body: string; by: string | null; at: string };

// A record's Notes tab (ADR 0006): what staff wrote about it, newest first with who and when, and
// a box to add one. A note is kept for good; a correction is a new note. The AI never reads them.
export function NotesCard({ about, aboutId, name }: { about: NoteAbout; aboutId: string; name: string }) {
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [body, setBody] = useState("");
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");
  const query = new URLSearchParams({ about, about_id: aboutId });

  function refresh() {
    api.get<Note[]>(`/api/notes?${query}`).then(setNotes)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, [about, aboutId]);

  return (
    <Card title="Notes">
      <Form className="flex flex-col gap-2" onSubmit={async (event) => {
        event.preventDefault();
        setError("");
        try {
          await api.post("/api/notes", { about, about_id: aboutId, body });
          setBody("");
          setSaid("Note added.");
          refresh();
        } catch (failed) {
          setError(failed instanceof ApiError ? failed.detail : String(failed));
        }
      }}>
        <label htmlFor={`note-${aboutId}`} className="text-sm text-ink-muted">New note</label>
        <textarea id={`note-${aboutId}`} required maxLength={5000} rows={3} value={body}
          onChange={(event) => setBody(event.target.value)}
          className="rounded-sm border border-line-strong bg-sunken px-2 py-1.5 text-base text-ink" />
        <div><Button type="submit" variant="secondary">Add note</Button></div>
      </Form>
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
      {notes !== null && notes.length === 0 && <p className="mt-4 text-sm text-ink-subtle">No notes yet.</p>}
      {notes !== null && notes.length > 0 && (
        <ul aria-label={`Notes, ${name}`} className="mt-4 flex flex-col divide-y divide-line">
          {notes.map((note) => (
            <li key={note.id} className="py-3">
              <p className="text-base whitespace-pre-wrap text-ink">{note.body}</p>
              <p className="mt-1 text-sm text-ink-subtle">{note.by ?? "Someone"} · {usDate(note.at)}</p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
