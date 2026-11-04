import { useState } from "react";
import { api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Form } from "../components/Form";
import { RowMenu } from "../components/RowMenu";
import type { MaintenanceRequest } from "../maintenance";
import { isOpen, kindAndPlace, photosLabel, statusLabel, visitLabel, when } from "../maintenance";

// One of the tenant's maintenance requests: what it is, where it stands, its notes; while it is
// still received it can be cancelled from its ⋯ menu, and while it is open a note added
// (src/pms/api/tenant.py). `onChange` gets the request as the API answers it.
export function TenantRequest({
  request,
  canChange,
  onChange,
}: {
  request: MaintenanceRequest;
  // The lease is in effect and the tenant still on it.
  canChange: boolean;
  onChange: (changed: MaintenanceRequest) => void;
}) {
  const [note, setNote] = useState("");
  const said = kindAndPlace(request);
  const open = isOpen(request.status) && !request.taken_back;

  async function addNote(event: React.FormEvent) {
    event.preventDefault();
    const changed = await api.post<MaintenanceRequest>(
      `/api/tenant/requests/${encodeURIComponent(request.id)}/notes`, { body: note },
    );
    setNote("");
    onChange(changed);
  }

  return (
    <li className="flex flex-col gap-1 py-3">
      <div className="flex items-start justify-between gap-3">
        <p className="flex flex-wrap items-center gap-2">
          {request.summary}
          {request.emergency && <Badge tone="danger">Emergency</Badge>}
        </p>
        {canChange && request.status === "received" && !request.taken_back && (
          <RowMenu label={request.summary} actions={[{
            label: "Cancel request",
            confirm: "Cancel this request? Your manager won't act on it.",
            danger: true,
            onSelect: async () => onChange(await api.post<MaintenanceRequest>(
              `/api/tenant/requests/${encodeURIComponent(request.id)}/cancel`, {},
            )),
          }]} />
        )}
      </div>
      {said && <p className="text-sm text-ink" data-testid="kind-and-place">{said}</p>}
      <p className="text-sm text-ink-subtle">
        {request.unit}, sent by {request.filed_by} {when(request.submitted_at)}.{" "}
        {photosLabel(request.photos) && `${photosLabel(request.photos)}. `}
        <span className="text-ink-muted">{statusLabel(request.status, request.taken_back)}</span>
        {request.status !== "received" && ` (${when(request.status_at)})`}
      </p>
      {visitLabel(request) && <p className="text-sm text-ink" data-testid="visit">{visitLabel(request)}</p>}
      {(request.notes ?? []).length > 0 && (
        <ul className="mt-1 flex flex-col gap-1 border-l-2 border-line pl-3 text-sm" aria-label={`Notes on ${request.summary}`}>
          {(request.notes ?? []).map((n, i) => (
            <li key={i}>
              <span className="text-ink">{n.body}</span>
              <span className="text-ink-subtle"> · {n.by}, {when(n.at)}</span>
            </li>
          ))}
        </ul>
      )}
      {canChange && open && (
        <Form className="mt-2 flex flex-wrap items-end gap-2" onSubmit={addNote}>
          <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm text-ink-muted">
            Add a note
            <input
              value={note}
              required
              maxLength={2000}
              onChange={(e) => setNote(e.target.value)}
              className="h-9 rounded-sm border border-line-strong bg-sunken px-3 text-base text-ink"
            />
          </label>
          <Button type="submit" variant="secondary">Add note</Button>
        </Form>
      )}
    </li>
  );
}
