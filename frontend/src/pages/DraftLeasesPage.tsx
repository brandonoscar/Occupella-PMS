import { FileSignature } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { ListBar } from "../components/ListBar";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { Said } from "../components/Said";
import { PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Tabs } from "../components/Tabs";
import { Wide } from "../components/Wide";
import { money, usDate } from "../money";
import type { Offer } from "../offers";
import { offerBadge } from "../offers";
import type { Paged } from "../rentals";
import { navigate } from "../router";
import { scopeParams, useScope } from "../scope";
import { DraftLeaseForm } from "./DraftLeaseForm";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";
import { GoLink } from "./NeedsAttention";

type Show = "open" | "leased" | "void" | "all";
const TABS: { value: Show; label: string }[] = [
  { value: "open", label: "Waiting for signatures" },
  { value: "leased", label: "Leases made" },
  { value: "void", label: "Void" },
  { value: "all", label: "All" },
];
const NONE: Record<Show, string> = {
  open: "No draft lease is waiting.",
  leased: "No draft lease became a lease yet.",
  void: "No draft lease was voided.",
  all: "No draft lease yet.",
};

// Where New draft lease starts, from the page's address: an approved application's Create draft
// lease (?application=<id>) or a lease's renewal (?lease=<id>).
export function startedFrom(search: string): string {
  const asked = new URLSearchParams(search);
  const application = asked.get("application");
  const lease = asked.get("lease");
  return application ? `application:${application}` : lease ? `lease:${lease}` : "";
}

// Leasing → Draft leases (ADR 0006, Buildium's): lease offers for approved applicants and renewals,
// a page at a time in tabs, those waiting for signatures (drafts, sent, partly or fully signed),
// those that became leases, the void ones or all, maybe of a property group or property; each row
// opens its page. New draft lease opens a side panel, started on an application or a lease when
// the page was opened from one.
export function DraftLeasesPage({ me }: { me: Me }) {
  const from = startedFrom(window.location.search);
  const [show, setShow] = useState<Show>("open");
  const [scope, setScope] = useScope();
  const [number, setNumber] = useState(1);
  const [page, setPage] = useState<Paged<Offer> | null>(null);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(from !== "");
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));

  useEffect(() => {
    const query = new URLSearchParams({ show, page: String(number), ...scopeParams(scope) });
    api.get<Paged<Offer>>(`/api/leasing/offers?${query}`).then(setPage)
      .catch((why) => setError(why instanceof ApiError ? why.detail : String(why)));
  }, [show, scope, number, runs]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Draft leases" actions={<Button onClick={() => setAdding(true)}>New draft lease</Button>} />
      <ListBar scope={scope} onScope={(next) => { setScope(next); setNumber(1); }} />
      <div>
        <Tabs label="Draft leases" tabs={TABS} value={show} onChange={(next) => { setShow(next); setNumber(1); }} />
        <Said>{said}</Said>
        {page === null ? (
          <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
        ) : page.items.length === 0 ? (
          <EmptyState icon={FileSignature} title={NONE[show]} />
        ) : (
          <>
            <Wide>
              <table className="w-full text-base">
                <thead>
                  <tr>
                    <th className="text-left">Unit</th>
                    <th className="text-left">Tenants</th>
                    <th className="text-left">Start</th>
                    <th className="text-right">Rent</th>
                    <th className="text-left">Status</th>
                    <th className="text-left">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((offer) => {
                    const badge = offerBadge(offer.status);
                    const name = `${offer.property}, ${offer.unit}`;
                    return (
                      <tr key={offer.id} aria-label={`${offer.kind === "renewal" ? "Renewal" : "Lease"} for ${name}`}
                        className="cursor-pointer hover:bg-raised" onClick={() => navigate(`/draft-leases/${offer.id}`)}>
                        <td><GoLink href={`/draft-leases/${offer.id}`} className="font-medium text-iris hover:text-iris-hover hover:underline">{name}</GoLink></td>
                        <td>{offer.signers.map((s) => s.name).join(", ")}</td>
                        <td className="whitespace-nowrap">{usDate(offer.starts_on)}</td>
                        <td className="text-right">{money(offer.monthly_rent)}</td>
                        <td><Badge tone={badge.tone} data-testid="status">{badge.word}</Badge></td>
                        <td className="whitespace-nowrap">{usDate(offer.offered_at)}</td>
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
      {adding && (
        <SidePanel title="New draft lease" onClose={() => setAdding(false)}>
          <DraftLeaseForm from={from} record={record} onMade={(made) => navigate(`/draft-leases/${made.offer.id}`)} />
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}
