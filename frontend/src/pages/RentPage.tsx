import { FileText } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { ListBar } from "../components/ListBar";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { TenantLinks } from "../components/TenantLinks";
import { Wide } from "../components/Wide";
import { latest } from "../latest";
import { day, money, usDate } from "../money";
import type { RentRun, RentRunLine } from "../rent";
import { rentRunSummary } from "../rent";
import type { LeaseDetail, Paged, RentRollRow } from "../rentals";
import { navigate } from "../router";
import { scopeParams, useScope } from "../scope";
import type { Recorder } from "./LeaseForms";
import { GoLink } from "./NeedsAttention";
import { PaymentForm } from "./LeaseForms";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

type Show = "current" | "future" | "past" | "owing" | "all";
const SHOWN: { value: Show; label: string }[] = [
  { value: "current", label: "Current" },
  { value: "future", label: "Future" },
  { value: "past", label: "Past" },
  { value: "owing", label: "Owing" },
  { value: "all", label: "All" },
];

function firstOfThisMonth(): string {
  return `${day(new Date()).slice(0, 8)}01`;
}

// Rentals → Rent roll (ADR 0006): every lease a page at a time, current, to come, ended, owing
// or all, each row opening its lease. Rent, recurring charges and late fees post by themselves
// every night (pms.nightly); an admin's ⋯ menu has Run rent now for a catch-up. Receive payment
// records a tenant's payment to any lease. The PMS records what reached the bank; it takes no
// payments itself.
export function RentPage({ me }: { me: Me }) {
  const [page, setPage] = useState<Paged<RentRollRow> | null>(null);
  const [scope, setScope] = useScope();
  const [show, setShow] = useState<Show>("current");
  const [number, setNumber] = useState(1);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState<"payment" | "run" | null>(null);
  // Counts what was recorded here, so the list shows it.
  const [changes, setChanges] = useState(0);
  const { said, error: refused, record } = useRecord(() => setChanges((n) => n + 1));
  const admin = me.roles?.includes("admin") ?? false;

  useEffect(() => {
    const run = latest();
    const query = new URLSearchParams({ page: String(number), show, ...scopeParams(scope) });
    api
      .get<Paged<RentRollRow>>(`/api/leases?${query}`)
      .then(run.keep(setPage))
      .catch(run.keep((failed) => setError(detail(failed))));
    return run.stop;
  }, [scope, show, number, changes]);

  // A form in a side panel closes once what it recorded is recorded.
  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setPanel(null);
    return recorded;
  };

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Rent roll" actions={
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => setPanel("payment")}>Receive payment</Button>
          {admin && (
            <RowMenu label="the rent roll" actions={[{ label: "Run rent now", onSelect: () => setPanel("run") }]} />
          )}
        </div>
      } />
      <div className="flex flex-wrap items-end justify-between gap-4">
        <ListBar scope={scope} onScope={(next) => { setScope(next); setNumber(1); }} />
        <Select label="Status" id="show-leases" value={show}
          onChange={(event) => { setShow(event.target.value as Show); setNumber(1); }}>
          {SHOWN.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </Select>
      </div>
      <Said>{said}</Said>
      {panel === null && <Notice tone="error">{refused}</Notice>}
      {page === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : page.items.length === 0 ? (
        <EmptyState icon={FileText} title={scope.group || scope.property || show !== "current" ? "No leases found." : "No lease is in effect today."} />
      ) : (
        <>
          <Wide>
            <table className="w-full text-base">
              <thead>
                <tr>
                  <th className="text-left">Property</th>
                  <th className="text-left">Unit</th>
                  <th className="text-left">Tenants</th>
                  <th className="text-left">Lease start</th>
                  <th className="text-left">Lease end</th>
                  <th className="text-right">Days left</th>
                  <th className="text-right">Rent</th>
                  <th className="text-right">Deposit</th>
                  <th className="text-right">Balance</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((row) => (
                  <tr key={row.id} aria-label={`${row.property.name}, ${row.unit.name}`}
                    className="cursor-pointer hover:bg-raised" onClick={() => navigate(`/leases/${row.id}`)}>
                    <td>{row.property.name}</td>
                    <td onClick={(event) => event.stopPropagation()}>
                      <GoLink href={`/leases/${row.id}`} className="font-medium text-iris hover:text-iris-hover hover:underline">
                        {row.unit.name}
                      </GoLink>
                    </td>
                    <td onClick={(event) => event.stopPropagation()}><TenantLinks people={row.people} /></td>
                    <td className="whitespace-nowrap">{usDate(row.starts_on)}</td>
                    <td className="whitespace-nowrap">{row.ends_on ? usDate(row.ends_on) : "Month to month"}</td>
                    <td className="text-right">{row.days_left ?? ""}</td>
                    <td className="text-right">{money(row.monthly_rent)}</td>
                    <td className="text-right">{money(row.deposit)}</td>
                    <td data-testid="balance" className={"text-right " + (Number(row.owed) > 0 ? "text-danger" : "")}>
                      {money(row.owed)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
          <Pager page={page} onPage={setNumber} />
        </>
      )}
      {panel === "payment" && (
        <SidePanel title="Receive payment" onClose={() => setPanel(null)}>
          <ReceivePayment record={recordThenClose} />
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
      {panel === "run" && (
        <SidePanel title="Run rent now" onClose={() => setPanel(null)}>
          <RunRent record={recordThenClose} />
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

// Receive payment: find the lease by its tenant, unit or property, then the payment.
function ReceivePayment({ record }: { record: Recorder }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<RentRollRow[]>([]);
  const [lease, setLease] = useState<LeaseDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (q.trim().length < 2) {
      setFound([]);
      return;
    }
    let live = true;
    const query = new URLSearchParams({ q: q.trim(), show: "all", per_page: "10" });
    api.get<Paged<RentRollRow>>(`/api/leases?${query}`)
      .then((answer) => { if (live) setFound(answer.items); })
      .catch((failed) => { if (live) setError(detail(failed)); });
    return () => { live = false; };
  }, [q]);

  function choose(id: string) {
    setError("");
    api.get<LeaseDetail>(`/api/leases/${id}`).then(setLease).catch((failed) => setError(detail(failed)));
  }

  if (lease !== null) {
    return (
      <div className="flex flex-col gap-4">
        <p className="flex flex-wrap items-center justify-between gap-2 text-base">
          <span>{lease.property} · {lease.unit} · {lease.tenants.map((t) => t.name).join(", ")}</span>
          <Button variant="ghost" size="sm" onClick={() => setLease(null)}>Another lease</Button>
        </p>
        <p className="text-sm text-ink-muted">Balance due {money(lease.owed)}</p>
        <PaymentForm lease={lease} record={record} />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <Field label="Lease" id="pay-find-lease" placeholder="Tenant, unit or property" value={q}
        onChange={(event) => setQ(event.target.value)} />
      <Notice tone="error">{error}</Notice>
      {found.length > 0 && (
        <ul aria-label="Leases found" className="flex flex-col divide-y divide-line">
          {found.map((row) => (
            <li key={row.id}>
              <button type="button" onClick={() => choose(row.id)}
                className="w-full py-2 text-left text-base text-ink hover:bg-raised">
                {row.property.name} · {row.unit.name} · {row.tenants.join(", ")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Run rent now (admins): what the nightly run does, for a due date and a day of late fees, and
// the latest runs, nightly or by hand.
function RunRent({ record }: { record: Recorder }) {
  const [due, setDue] = useState(firstOfThisMonth());
  const [asOf, setAsOf] = useState(day(new Date()));
  const [runs, setRuns] = useState<RentRunLine[] | null>(null);

  useEffect(() => {
    api.get<RentRunLine[]>("/api/rent/runs").then(setRuns).catch(() => setRuns([]));
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <Form className="flex flex-col gap-3" onSubmit={async (event) => {
        event.preventDefault();
        await record<RentRun>("/api/rent/run", { due_on: due, late_fees_as_of: asOf }, rentRunSummary);
      }}>
        <Field label="Rent due on" id="rent-due" type="date" required value={due}
          onChange={(event) => setDue(event.target.value)} />
        <Field label="Late fees as of" id="late-as-of" type="date" required value={asOf}
          onChange={(event) => setAsOf(event.target.value)} />
        <div><Button type="submit">Run rent now</Button></div>
      </Form>
      {runs !== null && runs.length > 0 && (
        <ul aria-label="Latest rent runs" className="flex flex-col gap-1 text-sm text-ink-muted">
          {runs.map((run) => (
            <li key={run.at}>
              {usDate(run.at.slice(0, 10))} {run.by ?? "nightly"}: {rentRunSummary(run)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
