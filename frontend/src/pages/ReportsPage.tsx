import { ChevronRight } from "lucide-react";
import type { Me } from "../api";
import { Card, PageHeader, Shell } from "../components/Shell";
import { GROUPS, REPORTS } from "../reports";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";

// Reports (ADR 0006, step 11): every report in its group; each opens its own page with its
// filters, a preview and Export. The whole ledger is under Settings, Data export.
export function ReportsPage({ me }: { me: Me }) {
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Reports" />
      <div className="grid gap-6 md:grid-cols-2">
        {GROUPS.map((group) => (
          <Card key={group} title={group}>
            <ul aria-label={group} className="divide-y divide-line rounded-md border border-card-edge">
              {REPORTS.filter((r) => r.group === group).map((report) => (
                <li key={report.name}>
                  <GoLink href={`/reports/${report.name}`}
                    className="flex items-center justify-between px-4 py-3 text-base text-ink hover:bg-raised">
                    {report.label}
                    <ChevronRight size={16} strokeWidth={1.5} aria-hidden="true" className="text-ink-subtle" />
                  </GoLink>
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </Shell>
  );
}
