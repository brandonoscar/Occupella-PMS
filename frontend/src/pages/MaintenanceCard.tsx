import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import type { StaffRequest } from "../maintenance";
import { isOpen, statusLabel, when } from "../maintenance";

type Vendor = { id: string; name: string };

// Staff's view of maintenance requests: open a work order for one, assign it to a vendor, and
// mark it done or closed. Each step is recorded as of now, and the tenant sees it.
export function MaintenanceCard() {
  const [requests, setRequests] = useState<StaffRequest[] | null>(null);
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [showClosed, setShowClosed] = useState(false);
  const [error, setError] = useState("");

  function show(failed: unknown) {
    setError(failed instanceof ApiError ? failed.detail : String(failed));
  }

  function refresh(all = showClosed) {
    api
      .get<StaffRequest[]>(`/api/maintenance/requests?show=${all ? "all" : "open"}`)
      .then(setRequests)
      .catch(show);
  }

  useEffect(() => {
    refresh();
    api.get<Vendor[]>("/api/maintenance/vendors").then(setVendors).catch(show);
  }, []);

  async function act(work: () => Promise<unknown>) {
    setError("");
    try {
      await work();
      refresh();
    } catch (failed) {
      show(failed);
    }
  }

  const openOrder = (request: StaffRequest) =>
    act(() => api.post(`/api/maintenance/requests/${request.id}/work-order`));
  const step = (request: StaffRequest, body: Record<string, string>) =>
    act(() => api.post(`/api/maintenance/work-orders/${request.work_order_id}/steps`, body));

  return (
    <Card title="Maintenance requests">
      <label className="mb-4 flex items-center gap-2 text-sm text-ink-muted">
        <input
          type="checkbox"
          checked={showClosed}
          onChange={(e) => {
            setShowClosed(e.target.checked);
            refresh(e.target.checked);
          }}
        />
        Show done and closed requests too
      </label>
      <Notice tone="error">{error}</Notice>
      {requests === null ? (
        <Notice tone="info">Loading</Notice>
      ) : requests.length === 0 ? (
        <Notice tone="info">No open requests.</Notice>
      ) : (
        <ul className="flex flex-col divide-y divide-line">
          {requests.map((request) => (
            <li key={request.id} className="flex flex-col gap-2 py-3" aria-label={request.summary}>
              <p>{request.summary}</p>
              <p className="text-sm text-ink-subtle">
                {request.property}, {request.unit}. From {request.filed_by},{" "}
                {when(request.submitted_at)}.{" "}
                {request.entry_allowed ? "May enter while they're out." : "Arrange a time to enter."}
              </p>
              <p className="text-sm text-ink-muted">
                {statusLabel(request.status)}
                {request.vendor && ` (${request.vendor})`}
              </p>
              {request.status === "received" && (
                <div>
                  <Button variant="secondary" onClick={() => openOrder(request)}>
                    Open work order
                  </Button>
                </div>
              )}
              {request.work_order_id && isOpen(request.status) && (
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    aria-label={`Vendor for ${request.summary}`}
                    className="h-9 rounded-sm border border-line-strong bg-sunken px-2 text-sm text-ink"
                    value={chosen[request.id] ?? ""}
                    onChange={(e) => setChosen({ ...chosen, [request.id]: e.target.value })}
                  >
                    <option value="">Choose a vendor</option>
                    {vendors.map((vendor) => (
                      <option key={vendor.id} value={vendor.id}>
                        {vendor.name}
                      </option>
                    ))}
                  </select>
                  <Button
                    variant="secondary"
                    disabled={!chosen[request.id]}
                    onClick={() =>
                      step(request, { step: "assigned", vendor_id: chosen[request.id] ?? "" })
                    }
                  >
                    Assign
                  </Button>
                  <Button variant="secondary" onClick={() => step(request, { step: "completed" })}>
                    Done
                  </Button>
                  <Button variant="ghost" onClick={() => step(request, { step: "cancelled" })}>
                    Close without work
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
