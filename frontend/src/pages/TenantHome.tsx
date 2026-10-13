import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card, Shell } from "../components/Shell";
import type { Ledger } from "../ledger";
import { itemLabel, readLedger } from "../ledger";
import type { MaintenanceRequest } from "../maintenance";
import { statusLabel, when } from "../maintenance";
import { day, money } from "../money";
import type { StatementData } from "../statement";
import { RenewalsCard } from "./RenewalsCard";

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
  const from = item.starts_on > today ? `, from ${item.starts_on}` : "";
  const until = item.ends_on ? `, until ${item.ends_on}` : "";
  return `${item.what}: ${amount}${from}${until}`;
}

// "2026-10-23 Key replacement: $50.00" (the kind when it has no memo).
export function comingDueLabel(charge: ComingDue): string {
  const what = charge.memo ?? (charge.kind === "rent" ? "Rent" : "Fee");
  return `${charge.due_on} ${what}: ${money(charge.open)}`;
}

const CELL = "px-3 py-2 text-left";
const AMOUNT = "px-3 py-2 text-right font-mono";

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
      <h1 className="font-display text-xl font-medium">Welcome, {me.name}</h1>
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
                      {l.property}, {l.unit}, from {l.starts_on}
                      {l.in_effect ? "" : " (ended)"}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <dl className="grid grid-cols-2 gap-x-8 gap-y-3 text-base sm:grid-cols-4">
              {[
                ["Home", `${lease.property}, ${lease.unit}`],
                ["Term", `${lease.starts_on} to ${lease.ends_on ?? "month to month"}`],
                ["Rent", `${money(lease.monthly_rent)} a month`],
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
              <p className="font-mono text-xl" data-testid="balance-due">
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
                      <li key={`${charge.due_on}-${charge.memo}`}>{comingDueLabel(charge)}</li>
                    ))}
                  </ul>
                </>
              )}
            </Card>
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
              <table className="w-full text-base">
                <thead className="text-sm text-ink-subtle">
                  <tr>
                    <th className={CELL}>Date</th>
                    <th className={CELL}>What</th>
                    <th className="px-3 py-2 text-right">Charged</th>
                    <th className="px-3 py-2 text-right">Paid</th>
                    <th className="px-3 py-2 text-right">Balance</th>
                  </tr>
                </thead>
                <tbody>
                  {ledger.entries.map((entry, n) => (
                    <tr key={n} className="border-t border-line">
                      <td className={`${CELL} font-mono text-sm`}>{entry.on}</td>
                      <td className={CELL}>
                        {itemLabel(entry.item)}
                        {entry.detail && <span className="text-ink-muted">: {entry.detail}</span>}
                      </td>
                      <td className={AMOUNT}>{entry.charged === "0.00" ? "" : money(entry.charged)}</td>
                      <td className={AMOUNT}>{entry.paid === "0.00" ? "" : money(entry.paid)}</td>
                      <td className={AMOUNT}>{money(entry.balance)}</td>
                    </tr>
                  ))}
                  <tr className="border-t border-line-strong font-medium">
                    <td className={CELL} colSpan={2}>
                      Balance at the end of {ledger.through}
                    </td>
                    <td className={AMOUNT}>{money(ledger.totals.charged)}</td>
                    <td className={AMOUNT}>{money(ledger.totals.paid)}</td>
                    <td className={AMOUNT}>{money(ledger.totals.balance)}</td>
                  </tr>
                </tbody>
              </table>
            )}
          </Card>

          <Card title="Maintenance requests">
            {lease.in_effect ? (
              <form className="mb-6 flex flex-col gap-3" onSubmit={send}>
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
                  <Notice tone="info">{sent}</Notice>
                </div>
              </form>
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
    </Shell>
  );
}
