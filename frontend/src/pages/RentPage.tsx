import { FileText } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Pager } from "../components/Pager";
import { Card, PageHeader, Shell } from "../components/Shell";
import { TenantLinks } from "../components/TenantLinks";
import { Wide } from "../components/Wide";
import { day, money } from "../money";
import type { RentRun } from "../rent";
import type { Paged, RentRollRow } from "../rentals";
import { termLabel } from "../rentals";
import { GoLink } from "./NeedsAttention";
import { owedLabel, owedTone, rentRunSummary } from "../rent";
import { STAFF_NAV } from "./staffNav";
import { ListBar } from "../components/ListBar";
import { scopeParams, useScope } from "../scope";
import { Said } from "../components/Said";
import { Form } from "../components/Form";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// Rentals → Rent roll (ADR 0005): the month's rent run and late fees for every lease, then the
// leases a page at a time (in effect, owing, or all; found by tenant, unit or property), each
// opening its own page, where its rent work is recorded. The PMS records what reached the bank;
// it takes no payments itself.
export function RentPage({ me }: { me: Me }) {
  const [page, setPage] = useState<Paged<RentRollRow> | null>(null);
  const [scope, setScope] = useScope();
  const [show, setShow] = useState<"current" | "owing" | "all">("current");
  const [number, setNumber] = useState(1);
  const [error, setError] = useState("");
  // Counts the rent runs, so the list shows what a run charged.
  const [runs, setRuns] = useState(0);

  useEffect(() => {
    const query = new URLSearchParams({ page: String(number), show, ...scopeParams(scope) });
    api
      .get<Paged<RentRollRow>>(`/api/leases?${query}`)
      .then(setPage)
      .catch((failed) => setError(detail(failed)));
  }, [scope, show, number, runs]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Rent roll" />
      <ListBar scope={scope} onScope={(next) => { setScope(next); setNumber(1); }} />
      <RunsCard refresh={() => setRuns((n) => n + 1)} />
      <Card title="Leases">
        <div className="mb-4 flex flex-wrap items-end gap-4">
          <Select label="Show" id="show-leases" value={show}
            onChange={(event) => { setShow(event.target.value as typeof show); setNumber(1); }}>
            <option value="current">In effect today</option>
            <option value="owing">Owing</option>
            <option value="all">All</option>
          </Select>
        </div>
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
                    <th className="text-right">Rent</th>
                    <th className="text-left">Term</th>
                    <th className="text-right">Owes</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((row) => (
                    <tr key={row.id} aria-label={`${row.property.name}, ${row.unit.name}`}>
                      <td>{row.property.name}</td>
                      <td>
                        <GoLink href={`/leases/${row.id}`} className="font-medium text-iris hover:text-iris-hover hover:underline">
                          {row.unit.name}
                        </GoLink>
                      </td>
                      <td><TenantLinks people={row.people} /></td>
                      <td className="text-right">{money(row.monthly_rent)}</td>
                      <td className="text-sm text-ink-muted">{termLabel(row)}</td>
                      <td className="text-right">
                        <Badge tone={owedTone(row.owed)} data-testid="owed">{owedLabel(row.owed)}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Wide>
            <Pager page={page} onPage={setNumber} />
          </>
        )}
      </Card>
    </Shell>
  );
}

function RunsCard({ refresh }: { refresh: () => void }) {
  const [due, setDue] = useState(day(new Date()));
  const [asOf, setAsOf] = useState(day(new Date()));
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");

  async function run<T>(path: string, on: string, say: (done: T) => string) {
    setError("");
    setSaid("");
    try {
      setSaid(say(await api.post<T>(`/api/rent/${path}`, { on })));
      refresh();
    } catch (failed) {
      setError(detail(failed));
    }
  }
  const lateFees = (done: { charged: number }) =>
    `${done.charged} late fee${done.charged === 1 ? "" : "s"} charged.`;

  return (
    <Card title="Rent run and late fees">
      <div className="flex flex-wrap items-end gap-6">
        <Form className="flex items-end gap-3" onSubmit={(e) => { e.preventDefault(); run<RentRun>("charge-rent", due, rentRunSummary); }}>
          <Field label="Rent due on" id="rent-due" type="date" required value={due}
            onChange={(e) => setDue(e.target.value)} />
          <Button type="submit" variant="secondary">Charge rent</Button>
        </Form>
        <Form className="flex items-end gap-3" onSubmit={(e) => { e.preventDefault(); run("late-fees", asOf, lateFees); }}>
          <Field label="Late fees as of" id="late-as-of" type="date" required value={asOf}
            onChange={(e) => setAsOf(e.target.value)} />
          <Button type="submit" variant="secondary">Assess late fees</Button>
        </Form>
      </div>
      <p className="mt-3 text-sm text-ink-subtle">
        Each lease is charged its rent and recurring charges once per due date, and each rent at
        most one late fee.
      </p>
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}
