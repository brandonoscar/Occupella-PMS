import { Landmark } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import type { Authority, AuthorityPage } from "../assistance";
import { paymentLines, paymentTotal } from "../assistance";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { PageHeader, Shell } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import type { Keep } from "../latest";
import { keepAll, latest } from "../latest";
import { day, money, usDate } from "../money";
import type { PaymentMethod } from "../rent";
import { PAYMENT_METHODS, requestId } from "../rent";
import { navigate } from "../router";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
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
// it owes. Record payment takes its one payment (a check, or a deposit by EFT) for many leases,
// each lease's part paying what the authority owes on it, oldest first, into its owners' accounts.
export function HousingAuthorityPage({ me, id }: { me: Me; id: string }) {
  const [page, setPage] = useState<AuthorityPage | null>(null);
  const [error, setError] = useState("");
  const [paying, setPaying] = useState(false);
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
        <Button onClick={() => setPaying(true)} disabled={page.leases.length === 0}>Record payment</Button>
      } />
      <p className="text-sm text-ink-muted" data-testid="authority-owed">{`Owes ${money(page.owed)} in all.`}</p>
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
                  <td><GoLink href={`/leases/${lease.lease_id}`} className={LINK}>{lease.home}</GoLink></td>
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
      {paying && (
        <SidePanel title="Record payment" onClose={() => setPaying(false)}>
          <PaymentForm page={page} record={async (path, body, done) => {
            const recorded = await record(path, body, done);
            if (recorded) setPaying(false);
            return recorded;
          }} />
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Shell>
  );
}

function PaymentForm({ page, record }: { page: AuthorityPage; record: ReturnType<typeof useRecord>["record"] }) {
  const [request] = useState(requestId);
  const [on, setOn] = useState(day(new Date()));
  const [method, setMethod] = useState<PaymentMethod>("EFT");
  const [reference, setReference] = useState("");
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const owing = page.leases.filter((lease) => Number(lease.owed) > 0);
  const total = paymentTotal(amounts);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = {
        request_id: request, housing_authority_id: page.id, received_on: on, method,
        reference: reference.trim() || null, lines: paymentLines(amounts),
      };
      return record(`/api/assistance/payments`, body, () => `${money(total)} from ${page.name} recorded.`);
    }}>
      <p className="text-sm text-ink-muted">
        Each lease's part pays what the authority owes on it, oldest first, into its owners' accounts. No more than it owes.
      </p>
      <Field label="Received on" id="authority-pay-on" type="date" required value={on} onChange={(e) => setOn(e.target.value)} />
      <Select label="Method" id="authority-pay-method" value={method} onChange={(e) => setMethod(e.target.value as PaymentMethod)}>
        {PAYMENT_METHODS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
      </Select>
      <Field label="Check or reference" id="authority-pay-ref" maxLength={200} value={reference}
        onChange={(e) => setReference(e.target.value)} />
      {owing.length === 0 ? (
        <Notice tone="info">The authority owes nothing on its leases.</Notice>
      ) : (
        <>
          {owing.map((lease) => (
            <Field key={lease.lease_id} label={`${lease.home} (owes ${money(lease.owed)})`} id={`authority-pay-${lease.lease_id}`}
              inputMode="decimal" value={amounts[lease.lease_id] ?? ""}
              onChange={(e) => setAmounts({ ...amounts, [lease.lease_id]: e.target.value })} />
          ))}
          <div>
            <Button type="button" variant="secondary" onClick={() =>
              setAmounts(Object.fromEntries(owing.map((lease) => [lease.lease_id, lease.owed])))}>
              Fill in what it owes
            </Button>
          </div>
        </>
      )}
      <p className="text-sm text-ink" data-testid="authority-pay-total">{`Total ${money(total)}`}</p>
      <div><Button type="submit" disabled={total === "0.00"}>Record payment</Button></div>
    </Form>
  );
}
