import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { LinkButton } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card, Shell } from "../components/Shell";
import type { Today } from "../today";
import { applicationLine, billLine, lateLine, monthEndLine, offerLine } from "../today";
import { STAFF_NAV } from "./staffNav";

// Staff's day at a glance: what is late or waits on someone, each part with a way to the page
// that does that work. It changes nothing.
export function TodayPage({ me }: { me: Me }) {
  const [today, setToday] = useState<Today | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api
      .get<Today>("/api/today")
      .then(setToday)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <h1 className="font-display text-xl font-medium">Today</h1>
      <Notice tone="error">{error}</Notice>
      {today === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : (
        <>
          <Part title="Late rent" go="/rent" goLabel="Rent" none="No rent is past due."
            lines={today.late_rent.map((lease) => lateLine(lease, today.on))} />
          <Part title="Bills due this week" go="/bills" goLabel="Bills" none="No bill is due this week."
            lines={today.bills_due.map((bill) => billLine(bill, today.on))} />
          <Part title="Month-end" go="/reconcile" goLabel="Month-end" none="Every month ended is reconciled."
            lines={today.unreconciled.map(monthEndLine)} />
          <Part title="Applications waiting" go="/" goLabel="Overview" none="No application waits for a decision."
            lines={today.applications.map(applicationLine)} />
          <Part title="Offers not yet signed" go="/" goLabel="Overview" none="No offer waits for signatures."
            lines={today.unsigned_offers.map(offerLine)} />
        </>
      )}
    </Shell>
  );
}

function Part({ title, lines, none, go, goLabel }: {
  title: string;
  lines: string[];
  none: string;
  go: string;
  goLabel: string;
}) {
  return (
    <Card title={`${title} (${lines.length})`}>
      {lines.length ? (
        <ul className="mb-4 flex flex-col gap-1 text-base">
          {lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      ) : (
        <p className="mb-4 text-sm text-ink-subtle">{none}</p>
      )}
      <LinkButton href={go}>Go to {goLabel}</LinkButton>
    </Card>
  );
}
