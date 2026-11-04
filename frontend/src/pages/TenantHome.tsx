import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Notice } from "../components/Notice";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import { latest } from "../latest";
import type { Ledger, LedgerData, LedgerSource } from "../ledger";
import { entryWords, ledgerLines, readLedger } from "../ledger";
import { OpensSource, SourcePanel } from "../components/SourcePanel";
import type { ContentType } from "../documents";
import { MAX_BYTES, titleOf, toBase64, typeOf } from "../documents";
import type { MaintenanceRequest } from "../maintenance";
import { PHOTOS_PER_REQUEST, photosLabel, photosProblem, statusLabel, visitLabel, when } from "../maintenance";
import { day, money, sumMoney, usDate, usDatesIn } from "../money";
import type { DepositAsked, Paid, RentersInsurance } from "../rent";
import { dueDayLabel, insuranceLine, tenantDepositLine } from "../rent";
import { Badge } from "../components/Badge";
import { PolicyForm } from "./InsuranceForms";
import { RenewalsCard } from "./RenewalsCard";
import { useRecord } from "./useRecord";
import { Said } from "../components/Said";
import { Form } from "../components/Form";
import { ManagerCard } from "./ManagerCard";
import { TenantAutopayCard } from "./AutopayCards";
import { PayOnlineCard } from "./PayOnlineCard";
import { CalendarCard } from "./CalendarCard";
import { MyContactCard } from "../components/ContactCard";
import { MessagesCard } from "../components/MessagesCard";
import type { NavEntry } from "../components/Shell";
import { CreditCard, FileText, Home as HomeIcon, MessagesSquare, UserRound, Wrench } from "lucide-react";
import type { MoveOutNotice } from "./TenantPortalCards";
import { firstName } from "../directory";
import { AnnouncementsCard, LeaseUploadForm, MoveOutNoticeCard } from "./TenantPortalCards";

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
// The next month's charges not entered yet: the rent in force on its due day (a rent change to
// come included) and each monthly fee or credit, as the rent run will charge them.
export type NextCharge = {
  due_on: string;
  total: string;
  lines: { what: string; kind: "rent" | "fee" | "credit"; amount: string }[];
};

export type Lease = {
  lease_id: string;
  property: string;
  unit: string;
  // The home as the tenant reads it: "Elm Court, Unit 4", or "Cedar House" (src/pms/homes.py).
  home: string;
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
  next_charge: NextCharge | null;
  coming_due: ComingDue[];
  deposit_returned: boolean;
  // The security deposit the signed lease asked, what is held of it and what is still to give
  // the manager (q036); null when it asked none, or once the deposit was returned.
  deposit_asked: DepositAsked | null;
  // Each payment to it, newest first, each with its receipt to download.
  payments: Paid[];
  // The lease as signed, its signing certificate and the lease's documents (q120), the tenant's
  // own among them.
  files: {
    kind: "signed lease" | "signing certificate" | "lease document";
    title: string;
    url: string;
    from_tenant?: boolean;
  }[];
  // The latest notice to move out a tenant on it gave, if any.
  move_out_notice?: MoveOutNotice | null;
};

// The tenant portal's sections (the portals' second version): each a page of its own in the
// sidebar, all reading the same leases.
export type TenantSection = "overview" | "payments" | "lease" | "repairs" | "messages" | "profile";
const TENANT_NAV: NavEntry[] = [
  { href: "/", label: "Overview", icon: HomeIcon },
  { href: "/payments", label: "Payments", icon: CreditCard },
  { href: "/lease", label: "Lease", icon: FileText },
  { href: "/repairs", label: "Repairs", icon: Wrench },
  { href: "/messages", label: "Messages", icon: MessagesSquare },
  { href: "/profile", label: "Profile", icon: UserRound },
];
export const TENANT_SECTIONS: Record<string, TenantSection> = {
  "/": "overview",
  "/payments": "payments",
  "/lease": "lease",
  "/repairs": "repairs",
  "/messages": "messages",
  "/profile": "profile",
};
const HEADERS: Record<TenantSection, [string, string]> = {
  overview: ["", "Your home, what is due and what is coming up."],
  payments: ["Payments", "What you were charged and paid, paying online, and your receipts."],
  lease: ["Your lease", "Your lease, its files, renters insurance, renewals and moving out."],
  repairs: ["Repairs", "Ask for a repair and follow how it's going."],
  messages: ["Messages", "Write to your manager and read their answers."],
  profile: ["Your profile", "Your contact details, your calendar and your manager's."],
};


// "Pet rent: $35.00", "Parking: $25.00 off" for a credit, with when it starts or stops.
export function monthlyLabel(item: Monthly, today: string): string {
  const amount = item.kind === "credit" ? `${money(item.amount)} off` : money(item.amount);
  const from = item.starts_on > today ? `, from ${usDate(item.starts_on)}` : "";
  const until = item.ends_on ? `, until ${usDate(item.ends_on)}` : "";
  return `${item.what}: ${amount}${from}${until}`;
}

// "$2,300.00 due 12/01/2026", and what makes it up when it is more than the rent:
// "(Rent $2,300.00 · Pet rent $25.00 · Autopay credit $10.00 off)".
export function nextChargeLabel(next: NextCharge): string {
  const total = `${money(next.total)} due ${usDate(next.due_on)}`;
  if (next.lines.length < 2) return total;
  const parts = next.lines.map((l) => `${l.what} ${money(l.amount)}${l.kind === "credit" ? " off" : ""}`);
  return `${total} (${parts.join(" · ")})`;
}

// "Rent: $2,250.00; $2,300.00 from 12/01/2026": each month's rent with the changes to come.
export function eachMonthLabel(item: Monthly, lease: Pick<Lease, "rent_changes">, today: string): string {
  const label = monthlyLabel(item, today);
  if (item.kind !== "rent") return label;
  return label + lease.rent_changes.map((c) => `; ${money(c.monthly_rent)} from ${usDate(c.effective_on)}`).join("");
}

const MONTH_DAY = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const MONTH_DAY_YEAR = new Intl.DateTimeFormat("en-US", {
  month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
});

// "Your rent goes up to $2,300 on Dec 1." for the first rent change to come (its year when it
// isn't this year's); null with none, or one that changes nothing.
export function rentChangeNotice(lease: Pick<Lease, "rent_changes" | "monthly_rent">, today: string): string | null {
  const next = lease.rent_changes[0];
  if (!next) return null;
  const change = sumMoney([next.monthly_rent, negated(lease.monthly_rent)]);
  if (change === "0.00") return null;
  const day = new Date(`${next.effective_on}T00:00:00Z`);
  const when = (next.effective_on.slice(0, 4) === today.slice(0, 4) ? MONTH_DAY : MONTH_DAY_YEAR).format(day);
  const amount = money(next.monthly_rent).replace(/\.00$/, "");
  return `Your rent goes ${change.startsWith("-") ? "down" : "up"} to ${amount} on ${when}.`;
}

function negated(amount: string): string {
  return amount.startsWith("-") ? amount.slice(1) : `-${amount}`;
}

// "10/23/2026 Key replacement: $50.00" (the kind when it has no memo).
export function comingDueLabel(charge: ComingDue): string {
  const what = usDatesIn(charge.memo ?? (charge.kind === "rent" ? "Rent" : "Fee"));
  return `${usDate(charge.due_on)} ${what}: ${money(charge.open)}`;
}

// Cells take the table style's padding (less of it on a phone); only their alignment is set here.
const CELL = "text-left";
const AMOUNT = "text-right";

// A lease as the picker names it: its home and first day, and whether it has ended or is to come.
export function leaseLabel(lease: Lease, today: string): string {
  const when = lease.in_effect ? "" : lease.starts_on > today ? " (to come)" : " (ended)";
  return `${lease.home}, from ${usDate(lease.starts_on)}${when}`;
}

// A lease's term as the tenant reads it: "Month-to-month since 12/01/2025", or its two days.
export function leaseTermLabel(startsOn: string, endsOn: string | null): string {
  return endsOn ? `${usDate(startsOn)} to ${usDate(endsOn)}` : `Month-to-month since ${usDate(startsOn)}`;
}

// The lease shown first: the one in effect, else the newest.
export function firstLease(leases: Lease[]): Lease | null {
  return leases.find((lease) => lease.in_effect) ?? leases[0] ?? null;
}

// A photo chosen to go with a request: null type for a file a document can't be.
type Photo = { title: string; type: ContentType | null; size: number; base64: string };

// A tenant's lease, what they owe, its charges and payments, and their maintenance requests.
// Rent is paid to the PMC as before: the portal takes no payments.
export function TenantHome({ me, section = "overview" }: { me: Me; section?: TenantSection }) {
  const [leases, setLeases] = useState<Lease[] | null>(null);
  const [chosen, setChosen] = useState<string>("");
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [opened, setOpened] = useState<LedgerSource | null>(null);
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
      .get<LedgerData>(`/api/tenant/leases/${chosen}/ledger`)
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

  const refreshLeases = () => api.get<Lease[]>("/api/tenant/leases").then(setLeases).catch(show);
  const [heading, description] = HEADERS[section];
  const leaseSection = section === "overview" || section === "payments" || section === "lease" || section === "repairs";

  return (
    <Shell me={me} nav={TENANT_NAV}>
      <PageHeader title={heading || `Welcome, ${firstName(me.name)}`} description={description} />
      <Notice tone="error">{error}</Notice>
      {section === "overview" && lease && rentChangeNotice(lease, day(new Date())) && (
        <Notice tone="info">{rentChangeNotice(lease, day(new Date()))}</Notice>
      )}
      {section === "overview" && <AnnouncementsCard />}
      {!leaseSection ? null : leases === null ? (
        <Notice tone="info">Loading</Notice>
      ) : lease === null ? (
        <Card title="Your lease">
          <Notice tone="info">No lease is on file for you. Ask {me.pmc_name}.</Notice>
        </Card>
      ) : (
        <>
          {(section === "overview" || section === "lease") && (
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
                ["Home", lease.home],
                ["Term", leaseTermLabel(lease.starts_on, lease.ends_on)],
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
            {section === "overview" && (
            <div className="mt-6 border-t border-line-strong pt-4">
              <p className="text-sm text-ink-subtle">Balance due today</p>
              <p className="text-xl leading-tight font-semibold" data-testid="balance-due">
                {money(lease.balance_due)}
              </p>
              <p className="mt-1 text-sm text-ink-subtle">
                See what makes it up, and pay online when {me.pmc_name} takes payments online, on{" "}
                <a className="text-iris underline hover:text-iris-hover" href="/payments">Payments</a>.
              </p>
              {lease.move_out_notice && (
                <p className="mt-2 text-sm text-ink" data-testid="leaving">
                  You gave notice: leaving {usDate(lease.move_out_notice.leaving_on)}.
                </p>
              )}
            </div>
            )}
            {section === "lease" && lease.files.length > 0 && (
              <ul className="mt-4 flex flex-col gap-1 text-base" aria-label="Your lease's files">
                {lease.files.map((f) => (
                  <li key={f.url}>
                    <a className="font-medium text-iris hover:text-iris-hover hover:underline" href={f.url}>
                      {f.kind === "signed lease" ? `${f.title} (as signed, PDF)` : f.title}
                    </a>
                    {f.from_tenant && <span className="text-sm text-ink-subtle"> (sent by you)</span>}
                  </li>
                ))}
              </ul>
            )}
            {section === "lease" && lease.deposit_asked && (
              <p className="mt-4 text-base" data-testid="deposit-asked">
                {tenantDepositLine(lease.deposit_asked)}
              </p>
            )}
            {section === "lease" && lease.deposit_returned && (
              <div className="mt-4">
                <LinkButton
                  href={`/api/tenant/leases/${encodeURIComponent(lease.lease_id)}/deposit-statement`}
                >
                  Deposit statement (PDF)
                </LinkButton>
              </div>
            )}
            {section === "lease" && lease.in_effect && (
              <LeaseUploadForm leaseId={lease.lease_id} company={me.pmc_name} onFiled={refreshLeases} />
            )}
          </Card>
          )}

          {section === "overview" && (lease.next_charge || lease.each_month.length > 0 || lease.coming_due.length > 0) && (
            <Card title="Coming up">
              {lease.next_charge && (
                <>
                  <p className="text-sm text-ink-subtle">Next charge</p>
                  <p className="mb-4 text-base" data-testid="next-charge">{nextChargeLabel(lease.next_charge)}</p>
                </>
              )}
              {lease.each_month.length > 0 && (
                <>
                  <p className="text-sm text-ink-subtle">Each month</p>
                  <ul className="mb-4 flex flex-col gap-1 text-base">
                    {lease.each_month.map((item) => (
                      <li key={`${item.what}-${item.starts_on}`}>{eachMonthLabel(item, lease, day(new Date()))}</li>
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

          {section === "lease" && lease.renters_insurance && (
            <InsuranceCard leaseId={lease.lease_id} insurance={lease.renters_insurance}
              refresh={refreshLeases} />
          )}

          {/* A renewal waiting to be signed shows on the Overview too; with none it shows nothing. */}
          {(section === "lease" || section === "overview") && <RenewalsCard me={me} />}

          {section === "lease" && (lease.ends_on === null || lease.ends_on >= day(new Date())) && (
            <MoveOutNoticeCard leaseId={lease.lease_id} company={me.pmc_name}
              notice={lease.move_out_notice ?? null}
              onGiven={(given) => setLeases((all) => all && all.map((l) =>
                l.lease_id === lease.lease_id ? { ...l, move_out_notice: given } : l))} />
          )}

          {(section === "payments" || section === "repairs") && leases.length > 1 && (
            <label className="mb-4 flex max-w-md flex-col gap-1.5 text-sm text-ink-muted">
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

          {section === "payments" && (
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
                    <tr className="border-t border-line text-ink-muted">
                      <td className={`${CELL} whitespace-nowrap`}>{usDate(lease.starts_on)}</td>
                      <td className={CELL} colSpan={3}>Opening balance</td>
                      <td className={AMOUNT}>{money(ledger.opening)}</td>
                    </tr>
                    {ledgerLines(ledger).map((line, n) => {
                      if ("held" in line) {
                        const { held, balance } = line;
                        return (
                          <tr key={n} className="border-t border-line">
                            <td className={`${CELL} whitespace-nowrap text-ink-muted`}>{usDate(held.on)}</td>
                            <td className={CELL}>
                              {held.what}
                              <span className="text-ink-muted">
                                {held.how && ` · ${held.how}`}, {money(held.amount)} held as prepaid rent
                                {held.returned && " · came back unpaid"}
                              </span>
                              <a href={held.receipt} className="ml-3 text-sm text-iris hover:text-iris-hover hover:underline">
                                Receipt (PDF)
                              </a>
                            </td>
                            <td className={AMOUNT}>{""}</td>
                            <td className={AMOUNT}>{""}</td>
                            <td className={AMOUNT}>{money(balance)}</td>
                          </tr>
                        );
                      }
                      const entry = line.entry;
                      const words = entryWords(entry);
                      const source = entry.source;
                      const receipt = entry.payment?.receipt;
                      return (
                      <tr key={n} className="border-t border-line">
                        <td className={`${CELL} whitespace-nowrap text-ink-muted`}>{usDate(entry.on)}</td>
                        <td className={CELL}>
                          <OpensSource onOpen={source ? () => setOpened(source) : null}>
                            {words.what}
                            {words.detail && (
                              <span className="text-ink-muted">{entry.payment ? " · " : ": "}{words.detail}</span>
                            )}
                          </OpensSource>
                          {receipt && (
                            <a href={receipt} className="ml-3 text-sm text-iris hover:text-iris-hover hover:underline">
                              Receipt (PDF)
                            </a>
                          )}
                        </td>
                        <td className={AMOUNT}>{entry.charged === "0.00" ? "" : money(entry.charged)}</td>
                        <td className={AMOUNT}>{entry.paid === "0.00" ? "" : money(entry.paid)}</td>
                        <td className={AMOUNT}>{money(entry.balance)}</td>
                      </tr>
                      );
                    })}
                    <tr className="border-t border-line-strong font-medium">
                      <td className={CELL} colSpan={2}>Totals</td>
                      <td className={AMOUNT}>{money(ledger.totals.charged)}</td>
                      <td className={AMOUNT}>{money(ledger.totals.paid)}</td>
                      <td className={AMOUNT}>{money(ledger.totals.balance)}</td>
                    </tr>
                  </tbody>
                </table>
              </Wide>
            )}
            {opened && (
              <SourcePanel
                url={`/api/tenant/leases/${encodeURIComponent(lease.lease_id)}/ledger/sources/${opened.kind}/${encodeURIComponent(opened.id)}`}
                onClose={() => setOpened(null)} />
            )}
          </Card>
          )}

          {section === "payments" && <PayOnlineCard leaseId={lease.lease_id} />}
          {section === "payments" && <TenantAutopayCard leaseId={lease.lease_id} />}

          {section === "repairs" && (
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
                  OK to enter if I'm not home
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
                  <Button type="submit" says={emergency
                    ? `Sends it to ${me.pmc_name} as an emergency. You can't change it after; if anyone is in danger, call 911.`
                    : `Sends it to ${me.pmc_name}; how it's going shows below. You can't change it after it's sent.`}>
                    Send request
                  </Button>
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
          )}
        </>
      )}
      {section === "messages" && (
        <MessagesCard path="/api/tenant/messages" company={me.pmc_name} person={me.name} />
      )}
      {section === "profile" && (
        <>
          <MyContactCard name={me.name} />
          <CalendarCard path="/api/tenant/calendar"
            intro="Your rent due days, repair visits and your lease's last day, in your own calendar app (Google, Apple, Outlook). It updates by itself." />
        </>
      )}
      {(section === "profile" || section === "overview") && <ManagerCard />}
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
