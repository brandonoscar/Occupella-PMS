import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import type { LeaseDetail, Paged, RentRollRow } from "../rentals";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// Finds a lease by its tenant, unit or property, then reads it whole: Receive payment and
// recording a payment file's waiting line both start here.
export function LeaseFinder({ id, onLease }: { id: string; onLease: (lease: LeaseDetail) => void }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<RentRollRow[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (q.trim().length < 2) {
      setFound([]);
      return;
    }
    let live = true;
    const query = new URLSearchParams({ q: q.trim(), show: "all", per_page: "10" });
    api.get<Paged<RentRollRow>>(`/api/leases?${query}`)
      .then((answer) => { if (live) setFound(answer.items); })
      .catch((failed) => { if (live) setError(detail(failed)); });
    return () => { live = false; };
  }, [q]);

  function choose(lease: string) {
    setError("");
    api.get<LeaseDetail>(`/api/leases/${lease}`).then(onLease).catch((failed) => setError(detail(failed)));
  }

  return (
    <div className="flex flex-col gap-3">
      <Field label="Lease" id={id} placeholder="Tenant, unit or property" value={q}
        onChange={(event) => setQ(event.target.value)} />
      <Notice tone="error">{error}</Notice>
      {found.length > 0 && (
        <ul aria-label="Leases found" className="flex flex-col divide-y divide-line">
          {found.map((row) => (
            <li key={row.id}>
              <button type="button" onClick={() => choose(row.id)}
                className="w-full py-2 text-left text-base text-ink hover:bg-raised">
                {row.property.name} · {row.unit.name} · {row.tenants.join(", ")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
