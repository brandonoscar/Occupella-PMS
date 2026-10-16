import { CircleCheck } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
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

type Group = { title: string; go: string; goLabel: string; lines: string[] };

// What is late or waits on someone today, one group for each kind with its count and the way to
// the page that does that work (the Dashboard's "Needs attention"; ADR 0005). A kind with
// nothing in it is left out. It changes nothing.
export function attentionGroups(today: Today): Group[] {
  return [
    { title: "Late rent", go: "/rent-roll", goLabel: "Rent roll",
      lines: today.late_rent.map((lease) => lateLine(lease, today.on)) },
    { title: "Bills due this week", go: "/bills", goLabel: "Bills",
      lines: today.bills_due.map((bill) => billLine(bill, today.on)) },
    { title: "Month-end", go: "/banking", goLabel: "Banking",
      lines: today.unreconciled.map(monthEndLine) },
    { title: "Applications waiting", go: "/applicants", goLabel: "Applicants",
      lines: today.applications.map(applicationLine) },
    { title: "Offers not yet signed", go: "/draft-leases", goLabel: "Draft leases",
      lines: today.unsigned_offers.map(offerLine) },
    { title: "Journal entries waiting", go: "/journal", goLabel: "Journal entries",
      lines: today.journal_waiting.map(journalLine) },
    { title: "Vendor insurance", go: "/vendors", goLabel: "Vendors",
      lines: today.vendor_insurance.map(insuranceLine) },
    { title: "Company fees in trust", go: "/owners", goLabel: "Owners",
      lines: today.company_fees.map((fees) => companyFeesLine(fees, today.on)) },
    { title: "Trust account signers", go: "/settings/staff", goLabel: "Staff and roles",
      lines: today.signers.map(signerNoteLine) },
    { title: "Renters insurance", go: "/rent-roll", goLabel: "Rent roll",
      lines: today.renters_insurance.map(uninsuredLine) },
  ].filter((group) => group.lines.length > 0);
}

export function NeedsAttention({ today }: { today: Today }) {
  const groups = attentionGroups(today);
  if (groups.length === 0) {
    return (
      <EmptyState icon={CircleCheck} title="Nothing is late or waiting."
        body="Late rent, bills due this week, month-end, applications, unsigned offers, journal entries waiting, vendor insurance running out, the company's fees left in trust, trust accounts' signers and renters insurance show here." />
    );
  }
  return (
    <div className="divide-y divide-line rounded-md border border-card-edge">
      {groups.map((group) => (
        <section key={group.title} aria-label={group.title}>
          <div className="flex items-center justify-between px-4 pt-3 pb-1">
            <h3 className="text-xs font-medium tracking-wider text-ink-subtle uppercase">
              {group.title}
              <span className="ml-2 text-iris">{group.lines.length}</span>
            </h3>
            <GoLink href={group.go}>Open {group.goLabel}</GoLink>
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
  );
}

// A link to another page of the portal, followed in place; a click for a new tab or window is
// the browser's.
export function GoLink({
  href,
  className = "text-sm text-iris hover:text-iris-hover hover:underline",
  children,
}: {
  href: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <a
      href={href}
      className={className}
      onClick={(event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
        event.preventDefault();
        navigate(href);
      }}
    >
      {children}
    </a>
  );
}
