import { UserSearch } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { ListBar } from "../components/ListBar";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { PageHeader, Shell } from "../components/Shell";
import { Tabs } from "../components/Tabs";
import { Wide } from "../components/Wide";
import { latest } from "../latest";
import type { Application } from "../leasing";
import { applicantNames, applicationBadge, incomeMultiple, latestScreening } from "../leasing";
import { usDate } from "../money";
import type { Paged } from "../rentals";
import { navigate } from "../router";
import { scopeParams, useScope } from "../scope";
import { STAFF_NAV } from "./staffNav";
import { GoLink } from "./NeedsAttention";

type Show = "open" | "rejected" | "withdrawn" | "all";
const TABS: { value: Show; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "rejected", label: "Rejected" },
  { value: "withdrawn", label: "Withdrawn" },
  { value: "all", label: "All" },
];
const NONE: Record<Show, string> = {
  open: "No application is waiting.",
  rejected: "No application was rejected.",
  withdrawn: "No application was withdrawn.",
  all: "No one has applied yet.",
};

// Leasing → Applicants (ADR 0006, Buildium's): the applications that came through the company's
// links and listings, newest first, a page at a time: open ones (new, screening, or approved and
// not a lease yet), rejected, withdrawn or all; maybe of a property group or property. Each row
// opens the application's page, where staff screen it, decide it and make it a draft lease.
export function ApplicantsPage({ me }: { me: Me }) {
  const [show, setShow] = useState<Show>("open");
  const [scope, setScope] = useScope();
  const [number, setNumber] = useState(1);
  const [page, setPage] = useState<Paged<Application> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const run = latest();
    const query = new URLSearchParams({ show, page: String(number), ...scopeParams(scope) });
    api.get<Paged<Application>>(`/api/leasing/applications?${query}`).then(run.keep(setPage))
      .catch(run.keep((why) => setError(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
  }, [show, scope, number]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Applicants" />
      <ListBar scope={scope} onScope={(next) => { setScope(next); setNumber(1); }} />
      <div>
        <Tabs label="Applicants" tabs={TABS} value={show} onChange={(next) => { setShow(next); setNumber(1); }} />
        {page === null ? (
          <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
        ) : page.items.length === 0 ? (
          <EmptyState icon={UserSearch} title={NONE[show]} />
        ) : (
          <>
            <Wide>
              <table className="w-full text-base">
                <thead>
                  <tr>
                    <th className="text-left">Applicant</th>
                    <th className="text-left">Unit</th>
                    <th className="text-left">Applied</th>
                    <th className="text-left">Status</th>
                    <th className="text-left">Screening</th>
                    <th className="text-right">Income to rent</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((application) => {
                    const badge = applicationBadge(application.status);
                    const names = applicantNames(application);
                    return (
                      <tr key={application.id} aria-label={`Application from ${names}`}
                        className="cursor-pointer hover:bg-raised"
                        onClick={() => navigate(`/applicants/${application.id}`)}>
                        <td><GoLink href={`/applicants/${application.id}`} className="font-medium text-iris hover:text-iris-hover hover:underline">{names}</GoLink></td>
                        <td>{application.property}, {application.unit}</td>
                        <td className="whitespace-nowrap">{usDate(application.submitted_at)}</td>
                        <td><Badge tone={badge.tone} data-testid="status">{badge.word}</Badge></td>
                        <td className="text-ink-muted">{latestScreening(application) || "Not screened"}</td>
                        <td className="text-right">{incomeMultiple(application.monthly_income, application.monthly_rent)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Wide>
            <Pager page={page} onPage={setNumber} />
          </>
        )}
      </div>
    </Shell>
  );
}
