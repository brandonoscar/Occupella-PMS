import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import { latest } from "../latest";
import type { Ledger } from "../ledger";
import { entryWords, readLedger } from "../ledger";
import type { ContentType } from "../documents";
import { MAX_BYTES, titleOf, toBase64, typeOf } from "../documents";
import type { MaintenanceRequest } from "../maintenance";
import { PHOTOS_PER_REQUEST, photosLabel, photosProblem, statusLabel, visitLabel, when } from "../maintenance";
import { day, money, usDate } from "../money";
import type { StatementData } from "../statement";
import type { DepositAsked, Paid, RentersInsurance } from "../rent";
import { dueDayLabel, insuranceLine, methodLabel, tenantDepositLine } from "../rent";
import { Badge } from "../components/Badge";
import { PolicyForm } from "./InsuranceForms";
import { RenewalsCard } from "./RenewalsCard";
import { useRecord } from "./useRecord";
import { Said } from "../components/Said";
import { Form } from "../components/Form";
import { ManagerCard } from "./ManagerCard";
import { CalendarCard } from "./CalendarCard";
import { MyContactCard } from "../components/ContactCard";

// What the lease charges every month: its rent from its next due date (and each rent change to
// come, until the next), and each recurring fee or credit not stopped (ends_on is the first due
// date it is no longer charged for).
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
  // The day of the month rent is due this month.
  rent_due_day: number;
  // A lease in effect: whether it requires renters insurance, and the policy on file.
  renters_insurance: RentersInsurance | null;
  tenants: string[];
  balance_due: string;
  in_effect: boolean;
  each_month: Monthly[];
  coming_due: ComingDue[];
  deposit_returned: boolean;
  // The security deposit the signed lease asked, what is held of it and what is still to give
  // the manager (q036); null when it asked none, or once the deposit was returned.
  deposit_asked: DepositAsked | null;
  // Each payment to it, newest first, each with its receipt to download.
  payments: Paid[];
  // The lease as signed, its signing certificate and the lease's documents (q120).
  files: { kind: "signed lease" | "signing certificate" | "lease document"; title: string; url: string }[];
};

// The day before a YYYY-MM-DD day.
function dayBefore(iso: string): string {
  return day(new Date(Date.parse(`${iso}T00:00:00Z`) - 86_400_000));
}

// "Pet rent: $35.00", "Parking: $25.00 off" for a credit, with when it starts or stops: a rent
// change to come reads "Rent: $1,500.00, through 11/30/2026" then "Rent: $1,575.00, from
// 12/01/2026".
export function monthlyLabel(item: Monthly, today: string): string {
  const amount = item.kind === "credit" ? `${money(item.amount)} off` : money(item.amount);
  const from = item.starts_on > today ? `, from ${usDate(item.starts_on)}` : "";
  const through = item.ends_on ? `, through ${usDate(dayBefore(item.ends_on))}` : "";
  return `${item.what}: ${amount}${from}${through}`;
}

// The memo the rent run gives a month's rent ("Rent due 2026-11-01", maybe with the days it is
// for or ", the tenant's part"): the books keep it as written.
const RUN_RENT = /^Rent due (\d{4}-\d{2}-\d{2})/;

// "10/23/2026 Key replacement: $50.00" (the kind when it has no memo). The day leads, so a rent
// run's memo reads "11/01/2026 Rent: $2,300.00", not "11/01/2026 Rent due 2026-11-01", and any
// day in the rest of it MM/DD/YYYY.
export function comingDueLabel(charge: ComingDue): string {
  const run = charge.kind === "rent" && charge.memo !== null ? RUN_RENT.exec(charge.memo) : null;
  const what =
    run && charge.memo !== null && run[1] === charge.due_on
      ? `Rent${charge.memo.slice(run[0].length).replace(/\d{4}-\d{2}-\d{2}/g, (iso) => usDate(iso))}`
      : (charge.memo ?? (charge.kind === "rent" ? "Rent" : "Fee"));
  return `${usDate(charge.due_on)} ${what}: ${money(charge.open)}`;
}

// Cells take the table style's padding (less of it on a phone); only their alignment is set here.
const CELL = "text-left";
const AMOUNT = "text-right";

// A lease as the picker names it: its home and first day, and whether it has ended or is to come.
export function leaseLabel(lease: Lease, today: string): string {
  const when = lease.in_effect ? "" : lease.starts_on > today ? " (to come)" : " (ended)";
  return `${lease.property}, ${lease.unit}, from ${usDate(lease.starts_on)}${when}`;
}

// The lease shown first: the one in effect, else the newest.
export function firstLease(leases: Lease[]): Lease | null {
  return leases.find((lease) => lease.in_effect) ?? leases[0] ?? null;
}

// A photo chosen to go with a request: null type for a file a document can't be.
type Photo = { title: string; type: ContentType | null; size: number; base64: string };

// A tenant's lease, what they owe, its charges and payments, and their maintenance requests.
// Rent is paid to the PMC as before: the portal takes no payments.
export function TenantHome({ me }: { me: Me }) {
  const [leases, setLeases] = useState<Lease[] | null>(null);
  const [chosen, setChosen] = useState<string>("");
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [requests, setRequests] = useState<MaintenanceRequest[]>([]);
  const [summary, setSummary] = useState("");
  const [entryAllowed, setEntryAllowed] = useState(false);
  const [emergency, setEmergency] = useState(false);
  const [photos, setPhotos] = useState<Photo[]>([]);
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
    const run = latest();
    if (!chosen) return;
    setLedger(null);
    api
      .get<StatementData>(`/api/tenant/leases/${chosen}/ledger`)
      .then(run.keep((data) => setLedger(readLedger(data))))
      .catch(run.keep(show));
    return run.stop;
  }, [chosen]);

  async function send(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setSent("");
    const problem = photosProblem(photos, MAX_BYTES);
    if (problem) {
      setError(problem);
      return;
    }
    try {
      const filed = await api.post<MaintenanceRequest>("/api/tenant/requests", {
        lease_id: chosen,
        summary,
        entry_allowed: entryAllowed,
        emergency,
        photos: photos.map((p) => ({ title: p.title, content_type: p.type, content_base64: p.base64 })),
      });
      setRequests([filed, ...requests]);
      setSummary("");
      setEntryAllowed(false);
      setEmergency(false);
      setPhotos([]);
      setSent(
        emergency ? `Sent to ${me.pmc_name} as an emergency. If anyone is in danger, call 911.` : `Sent to ${me.pmc_name}.`,
      );
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
                    <option key={l.lease_id} value={l.lease_id}>{leaseLabel(l, day(new Date()))}</option>
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
                ["Rent due", dueDayLabel(lease.rent_due_day)],
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
            {lease.files.length > 0 && (
              <ul className="mt-4 flex flex-col gap-1 text-base" aria-label="Your lease's files">
                {lease.files.map((f) => (
                  <li key={f.url}>
                    <a className="font-medium text-iris hover:text-iris-hover hover:underline" href={f.url}>
                      {f.kind === "signed lease" ? `${f.title} (as signed, PDF)` : f.title}
                    </a>
                  </li>
                ))}
              </ul>
            )}
            {lease.deposit_asked && (
              <p className="mt-4 text-base" data-testid="deposit-asked">
                {tenantDepositLine(lease.deposit_asked)}
              </p>
            )}
            {lease.deposit_returned && (
              <div className="mt-4">
                <LinkButton
                  href={`/api/tenant/leases/${encodeURIComponent(lease.lease_id)}/deposit-statement`}
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

          <Card title="Payments">
            {lease.payments.length === 0 ? (
              <p className="text-sm text-ink-subtle">No payment recorded yet.</p>
            ) : (
              <ul className="flex flex-col divide-y divide-line text-base" aria-label="Payments">
                {lease.payments.map((payment) => (
                  <li key={payment.transfer_id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                    <span>
                      {usDate(payment.received_on)} · {money(payment.amount)}
                      {(payment.method || payment.reference) && (
                        <span className="text-ink-muted">
                          {" · "}{[payment.method && methodLabel(payment.method), payment.reference].filter(Boolean).join(" ")}
                        </span>
                      )}
                    </span>
                    <span className="flex items-center gap-3">
                      {payment.bounced && <Badge tone="danger">Bounced</Badge>}
                      <a href={`/api/tenant/payments/${encodeURIComponent(payment.transfer_id)}/receipt`}
                        className="text-sm text-iris hover:text-iris-hover hover:underline">Receipt (PDF)</a>
                    </span>
                  </li>
                ))}
              </ul>
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
                <label className="flex items-center gap-2 text-sm text-ink-muted">
                  <input type="checkbox" checked={emergency} onChange={(e) => setEmergency(e.target.checked)} />
                  This is an emergency (no heat, no water, a leak, a gas smell)
                </label>
                <label htmlFor="request-photos" className="flex flex-col gap-1.5 text-sm text-ink-muted">
                  Photos (up to {PHOTOS_PER_REQUEST}, JPEG, PNG or PDF)
                  <input
                    id="request-photos"
                    type="file"
                    multiple
                    accept=".jpg,.jpeg,.png,.pdf"
                    className="text-sm text-ink"
                    onChange={async (e) => {
                      const picked = Array.from(e.target.files ?? []);
                      setPhotos(
                        await Promise.all(
                          picked.map(async (file) => ({
                            title: titleOf(file.name),
                            type: typeOf(file),
                            size: file.size,
                            base64: toBase64(new Uint8Array(await file.arrayBuffer())),
                          })),
                        ),
                      );
                    }}
                  />
                </label>
                {photos.length > 0 && (
                  <p className="text-sm text-ink-subtle" data-testid="photos-chosen">
                    Photos: {photos.map((p) => p.title).join(", ")}
                  </p>
                )}
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
                    <p className="flex flex-wrap items-center gap-2">
                      {request.summary}
                      {request.emergency && <Badge tone="danger">Emergency</Badge>}
                    </p>
                    <p className="text-sm text-ink-subtle">
                      {request.unit}, sent by {request.filed_by} {when(request.submitted_at)}.{" "}
                      {photosLabel(request.photos) && `${photosLabel(request.photos)}. `}
                      <span className="text-ink-muted">{statusLabel(request.status)}</span>
                      {request.status !== "received" && ` (${when(request.status_at)})`}
                    </p>
                    {visitLabel(request) && (
                      <p className="text-sm text-ink" data-testid="visit">{visitLabel(request)}</p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
      <MyContactCard name={me.name} />
      <CalendarCard path="/api/tenant/calendar"
        intro="Your rent due days, repair visits and your lease's last day, in your own calendar app (Google, Apple, Outlook). It updates by itself." />
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
