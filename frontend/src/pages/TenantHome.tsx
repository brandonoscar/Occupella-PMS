import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import type { Ledger } from "../ledger";
import { entryWords, readLedger } from "../ledger";
import type { MaintenanceRequest } from "../maintenance";
import { statusLabel, when } from "../maintenance";
import { day, money, usDate } from "../money";
import type { StatementData } from "../statement";
import type { RentersInsurance } from "../rent";
import { insuranceLine } from "../rent";
import { PolicyForm } from "./InsuranceForms";
import { RenewalsCard } from "./RenewalsCard";
import { useRecord } from "./useRecord";
import { Said } from "../components/Said";
import { Form } from "../components/Form";
import { ManagerCard } from "./ManagerCard";

// What the lease charges every month: its rent, and each recurring fee or credit not stopped
// (ends_on is the first due date it is no longer charged for).
export type Monthly = {
  what: string;
  kind: "rent" | "fee" | "credit";
  amount: string;
  starts_on: string;
  ends_on: string | null;
};
// A charge entered already and due after today, with what is still open of it.
export type ComingDue = { due_on: string; kind: "rent" | "fee"; memo: string | null; open: string };

export type Lease = {
  lease_id: string;
  property: string;
  unit: string;
  starts_on: string;
  ends_on: string | null;
  monthly_rent: string;
  // Rent changes to come, each from the first of a month, with the day notice was given.
  rent_changes: { effective_on: string; monthly_rent: string; notice_given_on: string }[];
  // A lease in effect: whether it requires renters insurance, and the policy on file.
  renters_insurance: RentersInsurance | null;
  tenants: string[];
  balance_due: string;
  in_effect: boolean;
  each_month: Monthly[];
  coming_due: ComingDue[];
  deposit_returned: boolean;
};

// "Pet rent: $35.00", "Parking: $25.00 off" for a credit, with when it starts or stops.
export function monthlyLabel(item: Monthly, today: string): string {
  const amount = item.kind === "credit" ? `${money(item.amount)} off` : money(item.amount);
  const from = item.starts_on > today ? `, from ${usDate(item.starts_on)}` : "";
  const until = item.ends_on ? `, until ${usDate(item.ends_on)}` : "";
  return `${item.what}: ${amount}${from}${until}`;
}

// "10/23/2026 Key replacement: $50.00" (the kind when it has no memo).
export function comingDueLabel(charge: ComingDue): string {
  const what = charge.memo ?? (charge.kind === "rent" ? "Rent" : "Fee");
  return `${usDate(charge.due_on)} ${what}: ${money(charge.open)}`;
}

// Cells take the table style's padding (less of it on a phone); only their alignment is set here.
const CELL = "text-left";
const AMOUNT = "text-right";

// The lease shown first: the one in effect, else the newest.
export function firstLease(leases: Lease[]): Lease | null {
  return leases.find((lease) => lease.in_effect) ?? leases[0] ?? null;
}

// A tenant's lease, what they owe, its charges and payments, and their maintenance requests.
// Rent is paid to the PMC as before: the portal takes no payments.
export function TenantHome({ me }: { me: Me }) {
  const [leases, setLeases] = useState<Lease[] | null>(null);
  const [chosen, setChosen] = useState<string>("");
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [requests, setRequests] = useState<MaintenanceRequest[]>([]);
  const [summary, setSummary] = useState("");
  const [entryAllowed, setEntryAllowed] = useState(false);
  const [sent, setSent] = useState("");
  const [error, setError] = useState("");

  function show(failed: unknown) {
    setError(failed instanceof ApiError ? failed.detail : String(failed));
  }

  useEffect(() => {
    api
      .get<Lease[]>("/api/tenant/leases")
      .then((found) => {
        setLeases(found);
        setChosen(firstLease(found)?.lease_id ?? "");
      })
      .catch(show);
    api.get<MaintenanceRequest[]>("/api/tenant/requests").then(setRequests).catch(show);
  }, []);

  useEffect(() => {
    if (!chosen) return;
    setLedger(null);
    api
      .get<StatementData>(`/api/tenant/leases/${chosen}/ledger`)
      .then((data) => setLedger(readLedger(data)))
      .catch(show);
  }, [chosen]);

  async function send(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setSent("");
    try {
      const filed = await api.post<MaintenanceRequest>("/api/tenant/requests", {
        lease_id: chosen,
        summary,
        entry_allowed: entryAllowed,
      });
      setRequests([filed, ...requests]);
      setSummary("");
      setEntryAllowed(false);
      setSent(`Sent to ${me.pmc_name}.`);
    } catch (failed) {
      show(failed);
    }
  }

  const lease = leases?.find((l) => l.lease_id === chosen) ?? null;
  // Built from the lease the API sent, not from the page's own select.
  const download = (format: string) =>
    lease ? `/api/tenant/leases/${encodeURIComponent(lease.lease_id)}/ledger/download?format=${format}` : "";

  return (
    <Shell me={me}>
      <PageHeader title={`Welcome, ${me.name}`} description="Your lease, what comes up, and what you have been charged and paid." />
      <Notice tone="error">{error}</Notice>
      {leases === null ? (
        <Notice tone="info">Loading</Notice>
      ) : lease === null ? (
        <Card title="Your lease">
          <Notice tone="info">No lease is on file for you. Ask {me.pmc_name}.</Notice>
        </Card>
      ) : (
        <>
          <Card title="Your lease">
            {leases.length > 1 && (
              <label className="mb-4 flex flex-col gap-1.5 text-sm text-ink-muted">
                Lease
                <select
                  className="h-9 rounded-sm border border-line-strong bg-sunken px-3 text-base text-ink"
                  value={chosen}
                  onChange={(e) => setChosen(e.target.value)}
                >
                  {leases.map((l) => (
                    <option key={l.lease_id} value={l.lease_id}>
                      {l.property}, {l.unit}, from {usDate(l.starts_on)}
                      {l.in_effect ? "" : " (ended)"}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <dl className="grid grid-cols-2 gap-x-8 gap-y-3 text-base sm:grid-cols-4">
              {[
                ["Home", `${lease.property}, ${lease.unit}`],
                ["Term", `${usDate(lease.starts_on)} to ${lease.ends_on ? usDate(lease.ends_on) : "month to month"}`],
                ["Rent", `${money(lease.monthly_rent)} a month` + lease.rent_changes
                  .map((c) => `; ${money(c.monthly_rent)} from ${usDate(c.effective_on)} (notice given ${usDate(c.notice_given_on)})`)
                  .join("")],
                ["On the lease", lease.tenants.join(", ")],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-sm text-ink-subtle">{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-6 border-t border-line-strong pt-4">
              <p className="text-sm text-ink-subtle">Balance due today</p>
              <p className="text-xl leading-tight font-semibold" data-testid="balance-due">
                {money(lease.balance_due)}
              </p>
              <p className="mt-1 text-sm text-ink-subtle">
                Pay {me.pmc_name} the way you always have; payments can't be made here yet.
              </p>
            </div>
            {lease.deposit_returned && (
              <div className="mt-4">
                <LinkButton
                  href={`/api/tenant/leases/${encodeURIComponent(lease.lease_id)}/deposit-return/download?format=pdf`}
                >
                  Deposit statement (PDF)
                </LinkButton>
              </div>
            )}
          </Card>

          {(lease.each_month.length > 0 || lease.coming_due.length > 0) && (
            <Card title="Coming up">
              {lease.each_month.length > 0 && (
                <>
                  <p className="text-sm text-ink-subtle">Each month</p>
                  <ul className="mb-4 flex flex-col gap-1 text-base">
                    {lease.each_month.map((item) => (
                      <li key={`${item.what}-${item.starts_on}`}>{monthlyLabel(item, day(new Date()))}</li>
                    ))}
                  </ul>
                </>
              )}
              {lease.coming_due.length > 0 && (
                <>
                  <p className="text-sm text-ink-subtle">Charged already, due later</p>
                  <ul className="flex flex-col gap-1 text-base">
                    {lease.coming_due.map((charge) => (
                      <li key={`${usDate(charge.due_on)}-${charge.memo}`}>{comingDueLabel(charge)}</li>
                    ))}
                  </ul>
                </>
              )}
            </Card>
          )}

          {lease.renters_insurance && (
            <InsuranceCard leaseId={lease.lease_id} insurance={lease.renters_insurance}
              refresh={() => api.get<Lease[]>("/api/tenant/leases").then(setLeases).catch(show)} />
          )}

          <RenewalsCard me={me} />

          <Card title="Charges and payments">
            <div className="mb-4 flex flex-wrap gap-2">
              <LinkButton href={download("pdf")}>PDF</LinkButton>
              <LinkButton href={download("csv")}>CSV</LinkButton>
              <LinkButton href={download("xlsx")}>Excel</LinkButton>
            </div>
            {ledger === null ? (
              <Notice tone="info">Loading</Notice>
            ) : (
              <Wide>
                <table className="w-full text-base">
                  <thead className="text-sm text-ink-subtle">
                    <tr>
                      <th className={CELL}>Date</th>
                      <th className={CELL}>What</th>
                      <th className="text-right">Charged</th>
                      <th className="text-right">Paid</th>
                      <th className="text-right">Balance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ledger.entries.map((entry, n) => {
                      const words = entryWords(entry);
                      return (
                      <tr key={n} className="border-t border-line">
                        <td className={`${CELL} whitespace-nowrap text-ink-muted`}>{usDate(entry.on)}</td>
                        <td className={CELL}>
                          {words.what}
                          {words.detail && <span className="text-ink-muted">: {words.detail}</span>}
                        </td>
                        <td className={AMOUNT}>{entry.charged === "0.00" ? "" : money(entry.charged)}</td>
                        <td className={AMOUNT}>{entry.paid === "0.00" ? "" : money(entry.paid)}</td>
                        <td className={AMOUNT}>{money(entry.balance)}</td>
                      </tr>
                      );
                    })}
                    <tr className="border-t border-line-strong font-medium">
                      <td className={CELL} colSpan={2}>
                        Balance at the end of {usDate(ledger.through)}
                      </td>
                      <td className={AMOUNT}>{money(ledger.totals.charged)}</td>
                      <td className={AMOUNT}>{money(ledger.totals.paid)}</td>
                      <td className={AMOUNT}>{money(ledger.totals.balance)}</td>
                    </tr>
                  </tbody>
                </table>
              </Wide>
            )}
          </Card>

          <Card title="Maintenance requests">
            {lease.in_effect ? (
              <Form className="mb-6 flex flex-col gap-3" onSubmit={send}>
                <label htmlFor="summary" className="flex flex-col gap-1.5 text-sm text-ink-muted">
                  What needs fixing?
                  <textarea
                    id="summary"
                    required
                    maxLength={2000}
                    rows={3}
                    value={summary}
                    onChange={(e) => setSummary(e.target.value)}
                    className="rounded-sm border border-line-strong bg-sunken px-3 py-2 text-base text-ink"
                  />
                </label>
                <label className="flex items-center gap-2 text-sm text-ink-muted">
                  <input
                    type="checkbox"
                    checked={entryAllowed}
                    onChange={(e) => setEntryAllowed(e.target.checked)}
                  />
                  Someone may come in while I'm out
                </label>
                <div className="flex items-center gap-4">
                  <Button type="submit">Send request</Button>
                  <Said>{sent}</Said>
                </div>
              </Form>
            ) : (
              <Notice tone="info">This lease has ended; requests go under a current one.</Notice>
            )}
            {requests.length === 0 ? (
              <Notice tone="info">No requests yet.</Notice>
            ) : (
              <ul className="flex flex-col divide-y divide-line">
                {requests.map((request) => (
                  <li key={request.id} className="py-3">
                    <p>{request.summary}</p>
                    <p className="text-sm text-ink-subtle">
                      {request.unit}, sent by {request.filed_by} {when(request.submitted_at)}.{" "}
                      <span className="text-ink-muted">{statusLabel(request.status)}</span>
                      {request.status !== "received" && ` (${when(request.status_at)})`}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
      <ManagerCard />
    </Shell>
  );
}

// The lease's renters insurance: what it asks and the policy on file, and the tenant's own policy
// recorded here, with whatever insurer they chose.
function InsuranceCard({ leaseId, insurance, refresh }: {
  leaseId: string;
  insurance: RentersInsurance;
  refresh: () => void;
}) {
  const { said, error, record } = useRecord(refresh);
  return (
    <Card title="Renters insurance">
      <p className="mb-4 text-base" data-testid="renters-insurance">
        {insuranceLine(insurance)}
        {insurance.policy_number !== null && ` (${insurance.standing})`}
      </p>
      <p className="mb-2 text-sm text-ink-subtle">Record your policy, from any insurer you choose.</p>
      <PolicyForm path={`/api/tenant/leases/${leaseId}/renters-insurance`} id={leaseId} record={record} />
      <Said>{said}</Said>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}
