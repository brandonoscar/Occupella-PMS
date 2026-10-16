import { CalendarClock } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { Notice } from "../components/Notice";
import { PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import { money } from "../money";
import type { Expiring } from "../workOrders";
import { daysLeftLabel, RENEWAL } from "../workOrders";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";
const BUTTON =
  "inline-flex h-9 items-center rounded-sm bg-iris px-3.5 text-base font-medium text-canvas hover:bg-iris-hover";

// Leasing → Renewals (ADR 0005): the leases ending in the next 90 days, soonest first, with where
// each one's renewal stands; a renewal is drafted on Draft leases.
export function RenewalsPage({ me }: { me: Me }) {
  const [leases, setLeases] = useState<Expiring[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api
      .get<Expiring[]>("/api/leasing/renewals")
      .then(setLeases)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, []);
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Renewals" actions={<GoLink href="/draft-leases" className={BUTTON}>Draft a renewal</GoLink>} />
      {leases === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : leases.length === 0 ? (
        <EmptyState icon={CalendarClock} title="No lease ends in the next 90 days." />
      ) : (
        <Wide>
          <table className="w-full text-base">
            <thead>
              <tr>
                <th className="text-left">Lease</th>
                <th className="text-left">Tenants</th>
                <th className="text-left">Ends</th>
                <th className="text-right">Rent</th>
                <th className="text-left">Renewal</th>
              </tr>
            </thead>
            <tbody>
              {leases.map((lease) => (
                <tr key={`${lease.property} ${lease.unit} ${lease.ends_on}`} aria-label={`${lease.property}, ${lease.unit}`}>
                  <td>
                    {lease.lease_id ? (
                      <GoLink href={`/leases/${lease.lease_id}`} className={LINK}>{lease.unit}, {lease.property}</GoLink>
                    ) : (
                      `${lease.unit}, ${lease.property}`
                    )}
                  </td>
                  <td className="text-ink-muted">{lease.tenants}</td>
                  <td>{lease.ends_on} <span className="text-ink-muted">({daysLeftLabel(lease.days_left)})</span></td>
                  <td className="text-right">{money(lease.monthly_rent)}</td>
                  <td><Badge tone={RENEWAL[lease.renewal].tone}>{RENEWAL[lease.renewal].word}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
    </Shell>
  );
}
