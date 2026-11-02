import { Users } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { latest } from "../latest";
import { money, usDate } from "../money";
import type { Autopay } from "../payments";
import { autopayRule } from "../payments";
import { balanceDue, ordinal } from "../rent";
import type { LeaseDetail, LeaseLine } from "../rentals";
import { LEASE_STATUS } from "../rentals";
import { EndLeaseForm } from "./LeasePeople";
import type { Recorder } from "./LeaseForms";
import { PaymentForm } from "./LeaseForms";
import { GoLink } from "./NeedsAttention";
import { RenewLeaseForm } from "./RenewLeaseForm";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// The lease a tenant's page shows: the one in effect, else the next to start, else the last.
export function leaseShown<L extends Pick<LeaseLine, "status" | "starts_on">>(leases: L[]): L | null {
  const by = (status: LeaseLine["status"]) => leases.filter((l) => l.status === status);
  const latestFirst = (list: L[]) => [...list].sort((a, b) => (a.starts_on < b.starts_on ? 1 : -1));
  return by("current")[0] ?? latestFirst(by("future")).at(-1) ?? latestFirst(by("ended"))[0] ?? null;
}

// "On: the balance due, up to $2,000.00", or "Not set up": the tenant's autopay of the lease.
export function autopayWord(autopays: Autopay[], tenant: string): string {
  const on = autopays.find((a) => a.tenant === tenant && a.stopped_at === null);
  return on ? `On: ${autopayRule(on)}` : "Not set up";
}

type Panel = "payment" | "end" | "renew";
const PANELS: Record<Panel, string> = { payment: "Receive payment", end: "Change end date", renew: "Renew lease" };

// A tenant's lease on their page (ADR 0010 phase 3, part I): its rent, due day, term, the deposit
// held for them, their autopay and the balance, with Receive payment, Change end date and Renew
// lease right there. Everything else of the lease is on its own page.
export function TenantLeaseCard({ line, tenant, deposit, refresh }: {
  line: LeaseLine & { unit: { id: string; name: string }; property: { id: string; name: string } };
  tenant: string;
  deposit: string;
  refresh: () => void;
}) {
  const [lease, setLease] = useState<LeaseDetail | null>(null);
  const [autopays, setAutopays] = useState<Autopay[] | null>(null);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState<Panel | null>(null);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => { setRuns((n) => n + 1); refresh(); });
  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setPanel(null);
    return recorded;
  };
  useEffect(() => {
    const run = latest();
    api.get<LeaseDetail>(`/api/leases/${line.id}`).then(run.keep(setLease))
      .catch(run.keep((why) => setError(why instanceof ApiError ? why.detail : String(why))));
    api.get<Autopay[]>(`/api/payments/autopay?lease=${encodeURIComponent(line.id)}`).then(run.keep(setAutopays))
      .catch(run.keep(() => setAutopays([])));
    return run.stop;
  }, [line.id, runs]);

  const open = line.status !== "ended";
  return (
    <Card title="Lease">
      <Said>{said}</Said>
      {panel === null && <Notice tone="error">{refused}</Notice>}
      {lease === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <div className="flex flex-col gap-4">
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-base">
            <Badge tone={LEASE_STATUS[line.status].tone}>{LEASE_STATUS[line.status].word}</Badge>
            <GoLink href={`/leases/${line.id}`} className={LINK}>{line.unit.name}, {line.property.name}</GoLink>
          </p>
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-base" aria-label="The lease">
            <dt className="text-ink-muted">Rent</dt>
            <dd data-testid="lease-rent">{money(lease.monthly_rent)} a month, due on the {ordinal(lease.rent_due_day)}</dd>
            <dt className="text-ink-muted">Starts</dt>
            <dd>{usDate(lease.starts_on)}</dd>
            <dt className="text-ink-muted">Ends</dt>
            <dd className="flex flex-wrap items-center gap-3">
              <span data-testid="lease-ends">{lease.ends_on ? usDate(lease.ends_on) : "Month to month"}</span>
              {open && (
                <Button variant="ghost" size="sm" onClick={() => setPanel("end")}>Change end date</Button>
              )}
            </dd>
            <dt className="text-ink-muted">Deposit held</dt>
            <dd data-testid="lease-deposit">{money(deposit)}</dd>
            <dt className="text-ink-muted">Autopay</dt>
            <dd data-testid="lease-autopay">{autopays === null ? "" : autopayWord(autopays, tenant)}</dd>
            <dt className="text-ink-muted">Balance due</dt>
            <dd data-testid="lease-balance" className={Number(balanceDue(lease)) > 0 ? "text-danger" : ""}>
              {money(balanceDue(lease))}
            </dd>
          </dl>
          {open && (
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => setPanel("payment")}>Receive payment</Button>
              <Button variant="secondary" onClick={() => setPanel("renew")}>Renew lease</Button>
            </div>
          )}
          <p className="text-sm text-ink-subtle">
            Receive payment records what the tenant paid; the PMS takes no money. Its charges, ledger and the rest are on
            the lease's page.
          </p>
        </div>
      )}
      {panel !== null && lease !== null && (
        <SidePanel title={PANELS[panel]} onClose={() => setPanel(null)}>
          {panel === "payment" && (
            <PaymentForm lease={lease} record={record} onRecorded={(another) => { if (!another) setPanel(null); }} />
          )}
          {panel === "end" && <EndLeaseForm lease={lease} record={recordThenClose} />}
          {panel === "renew" && <RenewLeaseForm leaseId={lease.id} record={recordThenClose} />}
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Card>
  );
}

// No lease yet: the tenant's page says so.
export function NoLeaseCard() {
  return (
    <Card title="Lease">
      <EmptyState icon={Users} title="No lease yet." body="A lease comes through Leasing, Draft leases." />
    </Card>
  );
}
