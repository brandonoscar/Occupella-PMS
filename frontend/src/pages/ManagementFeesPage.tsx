import { Percent } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import { latest } from "../latest";
import { day, money, sumMoney, usDate } from "../money";
import type { Fee, FeeDue } from "../payables";
import { lastMonthPeriod } from "../payables";
import type { AutomaticFees } from "./AutomaticFeesCard";
import { AUTOMATIC_FEES, automaticFeesLine } from "./AutomaticFeesCard";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

// Accounting → Management fees (ADR 0006, decision 4, as the founder amended it on 2026-10-18):
// each owner's account with a management agreement, and the period's fee: taken already, what
// Collect fees would take (worked out by the database and rolled back, so looking moves nothing),
// or why it can't be taken. A company may have the nightly run take last month's by itself
// (Settings, Company setup); the page says which.
export function ManagementFeesPage({ me }: { me: Me }) {
  const month = lastMonthPeriod(new Date());
  const [start, setStart] = useState(month.start);
  const [end, setEnd] = useState(month.end);
  const [fees, setFees] = useState<FeeDue[] | null>(null);
  const [error, setError] = useState("");
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  const { ask, prompt } = useConfirm();

  useEffect(() => {
    const run = latest();
    setError("");
    if (!start || !end) return;
    const query = new URLSearchParams({ period_start: start, period_end: end });
    api
      .get<FeeDue[]>(`/api/payables/management-fees?${query}`)
      .then(run.keep(setFees))
      .catch(run.keep((why) => { setFees(null); setError(why instanceof ApiError ? why.detail : String(why)); }));
    return run.stop;
  }, [start, end, runs]);
  // How the company takes its fees: by hand here, or each month by themselves.
  const [automatic, setAutomatic] = useState<AutomaticFees | null>(null);
  useEffect(() => {
    api.get<AutomaticFees>(AUTOMATIC_FEES).then(setAutomatic).catch(() => setAutomatic(null));
  }, []);

  const due = (fees ?? []).filter((f) => !f.taken && f.fee !== null);
  const total = sumMoney(due.map((f) => f.fee ?? "0"));

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Management fees" actions={
        <Button disabled={due.length === 0} onClick={() =>
          ask(`Collect ${money(total)} of management fees for ${usDate(start)} to ${usDate(end)}?`, async () => {
            await record<Fee[]>("/api/payables/management-fees",
              { period_start: start, period_end: end, on: day(new Date()) },
              (taken) => {
                const collected = taken.filter((f) => f.fee !== null);
                return `${money(sumMoney(collected.map((f) => f.fee ?? "0")))} collected from ${collected.length} account${collected.length === 1 ? "" : "s"}.`;
              });
          })
        }>
          Collect fees
        </Button>
      } />
      {automatic !== null && (
        <p className="text-sm text-ink-muted" data-testid="automatic-fees">
          {automaticFeesLine(automatic)}
          {automatic.automatic ? "" : ", or by themselves each month once turned on in Settings, Company setup"}.
        </p>
      )}
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Period from" id="fee-start" type="date" required value={start}
          onChange={(event) => setStart(event.target.value)} />
        <Field label="Up to (not including)" id="fee-end" type="date" required value={end}
          onChange={(event) => setEnd(event.target.value)} />
      </div>
      <Said>{said}</Said>
      <Notice tone="error">{refused}</Notice>
      {fees === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : fees.length === 0 ? (
        <EmptyState icon={Percent} title="No management agreement in force for this period." />
      ) : (
        <Wide>
          <table className="w-full text-base" aria-label="Management fees">
            <thead>
              <tr>
                <th className="text-left">Owner</th>
                <th className="text-left">Property</th>
                <th className="text-right">Fee</th>
                <th className="text-left">Status</th>
              </tr>
            </thead>
            <tbody>
              {fees.map((fee) => (
                <tr key={fee.account} aria-label={`${fee.owner}, ${fee.property}`}>
                  <td>{fee.owner}</td>
                  <td>{fee.property}</td>
                  <td className="text-right">{fee.fee === null ? "" : money(fee.fee)}</td>
                  <td>
                    {fee.taken ? (
                      <Badge tone="positive">Collected</Badge>
                    ) : fee.fee !== null ? (
                      <Badge tone="caution">To collect</Badge>
                    ) : (
                      <span className="text-sm text-danger">{fee.refused}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
      {prompt}
    </Shell>
  );
}

