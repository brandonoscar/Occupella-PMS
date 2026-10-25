import { Landmark } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import type { Authority, AuthorityPage, AuthorityPayment } from "../assistance";
import { lateFeeLine } from "../assistance";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { RowMenu } from "../components/RowMenu";
import { PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import type { Keep } from "../latest";
import { keepAll, latest } from "../latest";
import { day, money, usDate } from "../money";
import { methodLabel } from "../rent";
import { navigate } from "../router";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import { ReceivePayment } from "./ReceivePayment";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// Rentals → Housing authorities (Section 8; migration 0095, the founder's review q034): each
// authority the company works with, the leases it assists today and what it owes; a row opens
// its page. Add housing authority opens a side panel.
export function HousingAuthoritiesPage({ me }: { me: Me }) {
  const [rows, setRows] = useState<Authority[] | null>(null);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    api.get<Authority[]>("/api/assistance/authorities").then(setRows).catch((failed) => setError(detail(failed)));
  }, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Housing authorities"
        actions={<Button onClick={() => setAdding(true)}>Add housing authority</Button>} />
      <Notice tone="error">{error}</Notice>
      {rows === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : rows.length === 0 ? (
        <EmptyState icon={Landmark} title="No housing authorities yet" />
      ) : (
        <Wide>
          <table className="w-full text-base">
            <thead>
              <tr>
                <th className="text-left">Housing authority</th>
                <th className="text-right">Leases assisted</th>
                <th className="text-right">Owes</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} aria-label={row.name} className="cursor-pointer hover:bg-raised"
                  onClick={() => navigate(`/housing-authorities/${row.id}`)}>
                  <td><GoLink href={`/housing-authorities/${row.id}`} className={LINK}>{row.name}</GoLink></td>
                  <td className="text-right">{row.leases}</td>
                  <td className="text-right">{money(row.owed)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
      {adding && (
        <SidePanel title="Add housing authority" onClose={() => setAdding(false)}>
          <AddAuthorityForm added={(id) => navigate(`/housing-authorities/${id}`)} />
        </SidePanel>
      )}
    </Shell>
  );
}

function AddAuthorityForm({ added }: { added: (id: string) => void }) {
  const [name, setName] = useState("");
  const { error, record } = useRecord(() => undefined);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record<{ id: string }>("/api/assistance/authorities", { name }, (made) => {
        added(made.id);
        return `${name.trim()} added.`;
      });
    }}>
      <Field label="Housing authority's name" id="authority-name" required maxLength={200} value={name}
        onChange={(e) => setName(e.target.value)} />
      <div><Button type="submit">Add housing authority</Button></div>
      <Notice tone="error">{error}</Notice>
    </Form>
  );
}

// A housing authority: each lease it assists or owes on, what it pays of the rent today and what
// it owes. Receive payment (ADR 0010 phase 1c: the same panel as the rent roll's, the authority
// chosen) takes its one payment (a check, or a deposit by EFT) for many leases, each lease's part
// paying what the authority owes on it, oldest first, into its owners' accounts.
// Since migration 0116 (q034): its late payment fee (Set late fee), its payments with It bounced
// in each one's ⋯ menu, and a lease on a payment hold takes a payment only past the hold.
export function HousingAuthorityPage({ me, id }: { me: Me; id: string }) {
  const [page, setPage] = useState<AuthorityPage | null>(null);
  const [error, setError] = useState("");
  const [paying, setPaying] = useState(false);
  const [settingFee, setSettingFee] = useState(false);
  const { said, error: refused, record } = useRecord(refresh);

  function refresh(keep: Keep = keepAll) {
    api.get<AuthorityPage>(`/api/assistance/authorities/${id}`).then(keep(setPage))
      .catch(keep((failed) => setError(detail(failed))));
  }
  useEffect(() => {
    const run = latest();
    refresh(run.keep);
    return run.stop;
  }, [id]);

  if (page === null) {
    return (
      <Shell me={me} nav={STAFF_NAV}>
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      </Shell>
    );
  }
  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title={page.name} actions={
        <>
          <Button variant="secondary" onClick={() => setSettingFee(true)}>Set late fee</Button>
          <Button onClick={() => setPaying(true)} disabled={page.leases.length === 0}>Receive payment</Button>
        </>
      } />
      <p className="text-sm text-ink-muted" data-testid="authority-owed">{`Owes ${money(page.owed)} in all.`}</p>
      <p className="text-sm text-ink-muted" data-testid="authority-late-fee">{lateFeeLine(page.late_fee)}</p>
      <Said>{said}</Said>
      {page.leases.length === 0 ? (
        <EmptyState icon={Landmark} title="No leases yet"
          body="Set a lease's housing assistance from its page, on its Summary tab." />
      ) : (
        <Wide>
          <table className="w-full text-base" aria-label="Leases">
            <thead>
              <tr>
                <th className="text-left">Lease</th>
                <th className="text-left">Contract</th>
                <th className="text-right">Pays a month</th>
                <th className="text-right">Owes</th>
                <th className="text-left">Oldest open</th>
              </tr>
            </thead>
            <tbody>
              {page.leases.map((lease) => (
                <tr key={lease.lease_id} aria-label={lease.home}>
                  <td>
                    <GoLink href={`/leases/${lease.lease_id}`} className={LINK}>{lease.home}</GoLink>
                    {lease.hold_since && (
                      <span className="block text-sm text-ink-muted">
                        {`On a payment hold since ${usDate(lease.hold_since)}: ${lease.hold_reason ?? ""}`}
                      </span>
                    )}
                  </td>
                  <td className="text-ink-muted">{lease.contract ?? ""}</td>
                  <td className="text-right">{lease.monthly === null ? "None today" : money(lease.monthly)}</td>
                  <td className="text-right">{money(lease.owed)}</td>
                  <td className="text-ink-muted">{usDate(lease.open_charges[0]?.due_on ?? null)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
      {page.payments.length > 0 && (
        <Wide>
          <table className="w-full text-base" aria-label="Payments">
            <thead>
              <tr>
                <th className="text-left">Received</th>
                <th className="text-left">How</th>
                <th className="text-right">Amount</th>
                <th className="text-right"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {page.payments.map((payment) => (
                <AuthorityPaymentRow key={payment.transfer_id} payment={payment} record={record} />
              ))}
            </tbody>
          </table>
        </Wide>
      )}
      {settingFee && (
        <SidePanel title="Set late fee" onClose={() => setSettingFee(false)}>
          <LateFeeForm authority={page.id} record={async (path, body, done) => {
            const recorded = await record(path, body, done);
            if (recorded) setSettingFee(false);
            return recorded;
          }} />
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
      {paying && (
        <SidePanel title="Receive payment" onClose={() => setPaying(false)}>
          <ReceivePayment record={record} authority={page.id} close={() => setPaying(false)} />
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

// A payment as a row: the bank returning it is in its ⋯ menu, confirmed naming the amount, since
// its money moves back out of the owners' accounts and the authority owes it again.
function AuthorityPaymentRow({ payment, record }: { payment: AuthorityPayment; record: ReturnType<typeof useRecord>["record"] }) {
  const how = [payment.method && methodLabel(payment.method), payment.reference].filter(Boolean).join(" · ");
  return (
    <tr aria-label={`${usDate(payment.received_on)} payment of ${money(payment.amount)}`}>
      <td className="whitespace-nowrap">{usDate(payment.received_on)}</td>
      <td className="text-ink-muted">{how}</td>
      <td className="text-right">{money(payment.amount)}</td>
      <td className="text-right">
        {payment.bounced_on ? (
          <Badge tone="danger">{`Bounced ${usDate(payment.bounced_on)}`}</Badge>
        ) : (
          <RowMenu label={`${usDate(payment.received_on)} payment of ${money(payment.amount)}`} actions={[{
            label: "It bounced", danger: true,
            confirm: `Mark the ${money(payment.amount)} payment bounced today? Its money moves back out of the owners' accounts and the authority owes it again.`,
            onSelect: () => record(`/api/assistance/payments/${encodeURIComponent(payment.transfer_id)}/bounce`,
              { on: day(new Date()) }, () => `The ${money(payment.amount)} payment bounced; the authority owes it again.`),
          }]} />
        )}
      </td>
    </tr>
  );
}

// The authority's late payment fee from a day, as its HAP contract provides: its part of a rent
// still open after the grace days is charged the fee, on the authority, once. 0 charges none.
function LateFeeForm({ authority, record }: { authority: string; record: ReturnType<typeof useRecord>["record"] }) {
  const [from, setFrom] = useState(day(new Date()));
  const [grace, setGrace] = useState("5");
  const [fee, setFee] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record(`/api/assistance/authorities/${authority}/late-fees`,
        { starts_on: from, grace_days: Number(grace), flat_fee: fee.trim() || "0" },
        () => `Late payment fee of ${money(fee.trim() || "0")} from ${usDate(from)}.`);
    }}>
      <p className="text-sm text-ink-muted">
        Only as its HAP contract provides. The fee is charged to the housing authority, never the tenant.
      </p>
      <Field label="In force from" id="authority-fee-from" type="date" required value={from} onChange={(e) => setFrom(e.target.value)} />
      <Field label="Grace days" id="authority-fee-grace" inputMode="numeric" required value={grace} onChange={(e) => setGrace(e.target.value)} />
      <Field label="Late payment fee" id="authority-fee" inputMode="decimal" value={fee} onChange={(e) => setFee(e.target.value)} />
      <div><Button type="submit">Set late fee</Button></div>
    </Form>
  );
}
