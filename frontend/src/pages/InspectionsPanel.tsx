import { useState } from "react";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import type { Condition, Inspection, NewRoom } from "../inspections";
import { CONDITIONS, ROOMS, inspectionReportHref, inspectionSummary, itemsToSend } from "../inspections";
import { day } from "../money";
import { useRecord } from "./useRecord";

const fresh = (): NewRoom[] => ROOMS.map((room) => ({ room, condition: "good", note: "" }));

// A lease's move-in and move-out inspections, room by room, behind a button: the inspections so
// far, the report that lays the latest two side by side, and recording another.
export function InspectionsPanel({ leaseId, label }: { leaseId: string; label: string }) {
  const [open, setOpen] = useState(false);
  const [found, setFound] = useState<Inspection[] | null>(null);
  const [error, setError] = useState("");
  const { said, error: refused, record } = useRecord(load);
  const [kind, setKind] = useState<"move-in" | "move-out">("move-in");
  const [on, setOn] = useState(day(new Date()));
  const [notes, setNotes] = useState("");
  const [rooms, setRooms] = useState<NewRoom[]>(fresh());

  function load() {
    api
      .get<Inspection[]>(`/api/inspections?${new URLSearchParams({ lease_id: leaseId })}`)
      .then(setFound)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  const change = (n: number, to: Partial<NewRoom>) =>
    setRooms(rooms.map((room, i) => (i === n ? { ...room, ...to } : room)));

  if (!open) {
    return (
      <div>
        <Button variant="secondary" size="sm" onClick={() => { setOpen(true); load(); }}>
          Inspections
        </Button>
      </div>
    );
  }
  return (
    <section aria-label={`Inspections, ${label}`} className="flex flex-col gap-3 rounded-md border border-card-edge p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">Inspections</p>
        <LinkButton href={inspectionReportHref(leaseId)}>Inspection report (PDF)</LinkButton>
      </div>
      {found === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : found.length === 0 ? (
        <p className="text-sm text-ink-subtle">No inspection recorded yet.</p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm">
          {found.map((inspection) => (
            <li key={inspection.id}>
              {inspectionSummary(inspection)}
              {inspection.recorded_by ? <span className="text-ink-muted"> · by {inspection.recorded_by}</span> : null}
            </li>
          ))}
        </ul>
      )}
      <form
        className="flex flex-col gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          const body = {
            lease_id: leaseId, kind, inspected_on: on, items: itemsToSend(rooms),
            ...(notes.trim() ? { notes: notes.trim() } : {}),
          };
          if (await record("/api/inspections", body, () => `The ${kind} inspection of ${on} is recorded.`)) {
            setNotes("");
            setRooms(fresh());
          }
        }}
      >
        <div className="flex flex-wrap items-end gap-3">
          <Select label="Inspection" id={`inspection-kind-${leaseId}`} value={kind}
            onChange={(e) => setKind(e.target.value as "move-in" | "move-out")}>
            <option value="move-in">Move-in</option>
            <option value="move-out">Move-out</option>
          </Select>
          <Field label="Inspected on" id={`inspection-on-${leaseId}`} type="date" required value={on}
            onChange={(e) => setOn(e.target.value)} />
          <Field label="Notes" id={`inspection-notes-${leaseId}`} maxLength={2000} value={notes}
            onChange={(e) => setNotes(e.target.value)} />
        </div>
        {rooms.map((room, n) => (
          <fieldset key={n} className="flex flex-wrap items-end gap-3">
            <Field label={`Room ${n + 1}`} id={`inspection-room-${leaseId}-${n}`} maxLength={100} value={room.room}
              onChange={(e) => change(n, { room: e.target.value })} />
            <Select label={`Room ${n + 1} condition`} id={`inspection-condition-${leaseId}-${n}`} value={room.condition}
              onChange={(e) => change(n, { condition: e.target.value as Condition })}>
              {CONDITIONS.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </Select>
            <Field label={`Room ${n + 1} note`} id={`inspection-note-${leaseId}-${n}`} maxLength={500} value={room.note}
              onChange={(e) => change(n, { note: e.target.value })} />
          </fieldset>
        ))}
        <div className="flex flex-wrap gap-3">
          <Button type="button" variant="secondary"
            onClick={() => setRooms([...rooms, { room: "", condition: "good", note: "" }])}>
            Add a room
          </Button>
          <Button type="submit">Record inspection</Button>
        </div>
      </form>
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{refused}</Notice>
    </section>
  );
}
