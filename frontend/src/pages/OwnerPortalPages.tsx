import { Building2, Wrench } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import { Wide } from "../components/Wide";
import { money, usDate } from "../money";

// One of the owner's properties as the portal shows it (src/pms/api/owner_portal.py): its units,
// who rents each (names only) and for how much, what they owe, and what is vacant since when.
export type OwnedLease = {
  tenants: string[];
  starts_on: string;
  ends_on: string | null;
  monthly_rent: string;
  owed: string;
  status: "current" | "future" | "ended";
  leaving_on: string | null;
};
export type OwnedUnit = { id: string; name: string; lease: OwnedLease | null; vacant_since: string | null };
export type OwnedProperty = {
  property_id: string;
  name: string;
  address: string | null;
  balance: string;
  units: OwnedUnit[];
  occupied: number;
  vacant: number;
  owed: string;
};
export type OwnerWorkOrder = {
  id: string;
  number: number;
  property: { id: string; name: string };
  unit: { id: string; name: string } | null;
  summary: string;
  opened_at: string;
  status: "open" | "assigned" | "scheduled" | "completed" | "cancelled";
  vendor: string | null;
  billed: string;
  priority: "low" | "normal" | "high" | "urgent";
  scheduled_for: string | null;
  visit_window: string | null;
  days_open: number;
};

const CELL = "text-left";
const AMOUNT = "text-right";

function words(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// A unit's line: who rents it and the term, or since when it is vacant.
export function unitStatus(unit: OwnedUnit): string {
  const lease = unit.lease;
  if (lease === null || lease.status !== "current") {
    const vacant = unit.vacant_since ? `Vacant since ${usDate(unit.vacant_since)}` : "Vacant";
    return lease ? `${vacant}; leased from ${usDate(lease.starts_on)}` : vacant;
  }
  const term = `${usDate(lease.starts_on)} – ${lease.ends_on ? usDate(lease.ends_on) : "Month to month"}`;
  return `${lease.tenants.join(", ")} · ${term}`;
}

export function OwnerPropertyCard({ propertyId }: { propertyId: string }) {
  const [found, setFound] = useState<OwnedProperty | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setFound(null);
    api.get<OwnedProperty>(`/api/owner/properties/${encodeURIComponent(propertyId)}`)
      .then(setFound).catch((f) => setError(words(f)));
  }, [propertyId]);
  if (error) return <Notice tone="error">{error}</Notice>;
  if (found === null) return <Notice tone="info">Loading</Notice>;
  return (
    <>
      <Card title={found.name}>
        <dl className="grid grid-cols-2 gap-x-8 gap-y-3 text-base sm:grid-cols-4">
          {[
            ["Address", found.address ?? "Not on file"],
            ["Held for you", money(found.balance)],
            ["Units", `${found.occupied} rented, ${found.vacant} vacant`],
            ["Rent owed today", money(found.owed)],
          ].map(([label, value]) => (
            <div key={label}>
              <dt className="text-sm text-ink-subtle">{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </Card>
      <Card title="Units">
        {found.units.length === 0 ? (
          <EmptyState icon={Building2} title="No units yet." body="Your manager adds the property's units." />
        ) : (
          <Wide>
            <table className="w-full text-base" aria-label="Units">
              <thead className="text-sm text-ink-subtle">
                <tr>
                  <th className={CELL}>Unit</th>
                  <th className={CELL}>Who rents it</th>
                  <th className={AMOUNT}>Rent</th>
                  <th className={AMOUNT}>Owed</th>
                </tr>
              </thead>
              <tbody>
                {found.units.map((unit) => {
                  const current = unit.lease?.status === "current" ? unit.lease : null;
                  return (
                    <tr key={unit.id} className="border-t border-line">
                      <td className={CELL}>{unit.name}</td>
                      <td className={CELL}>
                        <span className="flex flex-wrap items-center gap-2">
                          {unitStatus(unit)}
                          {!current && <Badge tone="caution">Vacant</Badge>}
                          {current?.leaving_on && (
                            <Badge tone="info">Moving out {usDate(current.leaving_on)}</Badge>
                          )}
                        </span>
                      </td>
                      <td className={AMOUNT}>{current ? money(current.monthly_rent) : ""}</td>
                      <td className={AMOUNT}>{current ? money(current.owed) : ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Wide>
        )}
        <p className="mt-4 text-sm text-ink-subtle">
          Tenants' names only: your manager keeps their contact details.
        </p>
      </Card>
    </>
  );
}

const STATUS: Record<OwnerWorkOrder["status"], string> = {
  open: "Open",
  assigned: "Vendor assigned",
  scheduled: "Visit scheduled",
  completed: "Done",
  cancelled: "Cancelled",
};
const TONE: Record<OwnerWorkOrder["status"], "neutral" | "info" | "positive" | "caution"> = {
  open: "caution",
  assigned: "info",
  scheduled: "info",
  completed: "positive",
  cancelled: "neutral",
};

export function OwnerRepairsCard() {
  const [found, setFound] = useState<OwnerWorkOrder[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.get<OwnerWorkOrder[]>("/api/owner/work-orders").then(setFound).catch((f) => setError(words(f)));
  }, []);
  return (
    <Card title="Repairs at your properties">
      <Notice tone="error">{error}</Notice>
      {found !== null && found.length === 0 && (
        <EmptyState icon={Wrench} title="No repairs yet." body="Work your manager opens at your properties shows here." />
      )}
      {found !== null && found.length > 0 && (
        <Wide>
          <table className="w-full text-base" aria-label="Repairs">
            <thead className="text-sm text-ink-subtle">
              <tr>
                <th className={CELL}>#</th>
                <th className={CELL}>What</th>
                <th className={CELL}>Where</th>
                <th className={CELL}>Status</th>
                <th className={CELL}>Vendor</th>
                <th className={AMOUNT}>Billed</th>
              </tr>
            </thead>
            <tbody>
              {found.map((w) => (
                <tr key={w.id} className="border-t border-line">
                  <td className={`${CELL} text-ink-muted`}>{w.number}</td>
                  <td className={CELL}>
                    {w.summary}
                    <span className="block text-sm text-ink-subtle">
                      Opened {usDate(w.opened_at)}
                      {w.scheduled_for && ` · visit ${usDate(w.scheduled_for)}${w.visit_window ? `, ${w.visit_window}` : ""}`}
                    </span>
                  </td>
                  <td className={CELL}>{w.unit ? `${w.property.name}, ${w.unit.name}` : w.property.name}</td>
                  <td className={CELL}><Badge tone={TONE[w.status]}>{STATUS[w.status]}</Badge></td>
                  <td className={CELL}>{w.vendor ?? ""}</td>
                  <td className={AMOUNT}>{w.billed === "0.00" ? "" : money(w.billed)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
    </Card>
  );
}
