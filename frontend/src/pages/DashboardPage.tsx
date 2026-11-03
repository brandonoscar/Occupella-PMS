import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Notice } from "../components/Notice";
import { Card, PageHeader, Shell } from "../components/Shell";
import type { Dashboard } from "../dashboard";
import { tiles } from "../dashboard";
import type { Today } from "../today";
import { GoLink, NeedsAttention } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";

// Staff's first page (ADR 0005; ADR 0010 phase 3): the company's figures that change, each beside
// what it was a month before and opening its page, and what needs someone today, most urgent
// first, each with its action.
export function DashboardPage({ me }: { me: Me }) {
  const [figures, setFigures] = useState<Dashboard | null>(null);
  const [today, setToday] = useState<Today | null>(null);
  const [error, setError] = useState("");
  const [runs, setRuns] = useState(0);
  useEffect(() => {
    const failed = (reason: unknown) =>
      setError(reason instanceof ApiError ? reason.detail : String(reason));
    api.get<Dashboard>("/api/dashboard").then(setFigures).catch(failed);
    api.get<Today>("/api/today").then(setToday).catch(failed);
  }, [runs]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Dashboard" />
      <Notice tone="error">{error}</Notice>
      {figures && (
        <ul aria-label="Figures" className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {tiles(figures).map((tile) => (
            <li key={tile.label}>
              <GoLink
                href={tile.href}
                className="flex h-full flex-col gap-1 rounded-md border border-card-edge bg-canvas px-4 py-3 hover:bg-raised"
              >
                <span className="text-xs font-medium tracking-wider text-ink-subtle uppercase">
                  {tile.label}
                </span>
                <span className="font-display text-lg font-semibold text-ink">{tile.value}</span>
                <span className="text-sm text-ink-muted">{tile.detail}</span>
              </GoLink>
            </li>
          ))}
        </ul>
      )}
      <Card title="Needs attention">
        {today === null ? (
          !error && <Notice tone="info">Loading</Notice>
        ) : (
          <NeedsAttention today={today} me={me} refresh={() => setRuns((n) => n + 1)} />
        )}
      </Card>
    </Shell>
  );
}
