import { CircleCheck } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Notice } from "../components/Notice";
import { PageHeader, Shell } from "../components/Shell";
import { navigate } from "../router";
import type { Today } from "../today";
import {
  applicationLine,
  billLine,
  companyFeesLine,
  insuranceLine,
  journalLine,
  lateLine,
  monthEndLine,
  offerLine,
  signerNoteLine,
  uninsuredLine,
} from "../today";
import { STAFF_NAV } from "./staffNav";

type Group = { title: string; go: string; goLabel: string; lines: string[] };

// Staff's day at a glance, laid out as Occupella's Inbox: one list of what is late or waits on
// someone, a group for each kind with its count and the way to the page that does that work. A
// kind with nothing in it is left out. It changes nothing.
export function TodayPage({ me }: { me: Me }) {
  const [today, setToday] = useState<Today | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api
      .get<Today>("/api/today")
      .then(setToday)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, []);

  const groups: Group[] = today
    ? [
        { title: "Late rent", go: "/rent", goLabel: "Rent",
          lines: today.late_rent.map((lease) => lateLine(lease, today.on)) },
        { title: "Bills due this week", go: "/bills", goLabel: "Bills",
          lines: today.bills_due.map((bill) => billLine(bill, today.on)) },
        { title: "Month-end", go: "/reconcile", goLabel: "Month-end",
          lines: today.unreconciled.map(monthEndLine) },
        { title: "Applications waiting", go: "/", goLabel: "Overview",
          lines: today.applications.map(applicationLine) },
        { title: "Offers not yet signed", go: "/", goLabel: "Overview",
          lines: today.unsigned_offers.map(offerLine) },
        { title: "Journal entries waiting", go: "/journal", goLabel: "Journal",
          lines: today.journal_waiting.map(journalLine) },
        { title: "Vendor insurance", go: "/vendors", goLabel: "Vendors",
          lines: today.vendor_insurance.map(insuranceLine) },
        { title: "Company fees in trust", go: "/owners", goLabel: "Owners",
          lines: today.company_fees.map((fees) => companyFeesLine(fees, today.on)) },
        { title: "Trust account signers", go: "/staff", goLabel: "Staff",
          lines: today.signers.map(signerNoteLine) },
        { title: "Renters insurance", go: "/rent", goLabel: "Rent",
          lines: today.renters_insurance.map(uninsuredLine) },
      ].filter((group) => group.lines.length > 0)
    : [];
  const count = groups.reduce((sum, group) => sum + group.lines.length, 0);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader
        title="Today"
        description={
          today === null || count === 0
            ? "What is late or waits on someone."
            : `${count} ${count === 1 ? "item needs" : "items need"} someone today.`
        }
      />
      <Notice tone="error">{error}</Notice>
      {today === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : groups.length === 0 ? (
        <EmptyState icon={CircleCheck} title="Nothing is late or waiting."
          body="Late rent, bills due this week, month-end, applications, unsigned offers, journal entries waiting, vendor insurance running out, the company's fees left in trust, trust accounts' signers and renters insurance show here." />
      ) : (
        <div className="divide-y divide-line rounded-md border border-card-edge">
          {groups.map((group) => (
            <section key={group.title} aria-label={group.title}>
              <div className="flex items-center justify-between px-4 pt-3 pb-1">
                <h2 className="text-xs font-medium tracking-wider text-ink-subtle uppercase">
                  {group.title}
                  <span className="ml-2 text-iris">{group.lines.length}</span>
                </h2>
                <a
                  href={group.go}
                  className="text-sm text-iris hover:text-iris-hover hover:underline"
                  onClick={(event) => {
                    // A click for a new tab or window is the browser's.
                    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
                    event.preventDefault();
                    navigate(group.go);
                  }}
                >
                  Open {group.goLabel}
                </a>
              </div>
              <ul className="flex flex-col pb-2">
                {group.lines.map((line) => (
                  <li key={line} className="px-4 py-2 text-base">
                    {line}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </Shell>
  );
}
