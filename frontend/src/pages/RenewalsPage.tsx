import { CalendarClock } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { Notice } from "../components/Notice";
import { PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import { money, usDate } from "../money";
import type { Expiring } from "../workOrders";
import { daysLeftLabel, RENEWAL } from "../workOrders";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// Leasing → Renewals (ADR 0005): the leases ending in the next 90 days, soonest first, with where
// each one's renewal stands. A lease is renewed from its own page or its tenant's (Renew lease; ADR
// 0010 phase 3, part L).
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
      <PageHeader title="Renewals" />
      <p className="text-sm text-ink-subtle">Open a lease to renew it: Renew lease, on its page or its tenant's.</p>
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
                <tr key={`${lease.property} ${lease.unit} ${usDate(lease.ends_on)}`} aria-label={`${lease.property}, ${lease.unit}`}>
                  <td>
                    {lease.lease_id ? (
                      <GoLink href={`/leases/${lease.lease_id}`} className={LINK}>{lease.unit}, {lease.property}</GoLink>
                    ) : (
                      `${lease.unit}, ${lease.property}`
                    )}
                  </td>
                  <td className="text-ink-muted">{lease.tenants}</td>
                  <td>{usDate(lease.ends_on)} <span className="text-ink-muted">({daysLeftLabel(lease.days_left)})</span></td>
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
