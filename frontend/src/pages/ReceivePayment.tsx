import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api";
import type { Authority, AuthorityPage } from "../assistance";
import { paymentLines, paymentTotal } from "../assistance";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { latest } from "../latest";
import { day, money } from "../money";
import type { PaymentMethod } from "../rent";
import { PAYMENT_METHODS, requestId } from "../rent";
import type { LeaseDetail } from "../rentals";
import type { Recorder } from "./LeaseForms";
import { PaymentForm } from "./LeaseForms";
import { LeaseFinder } from "./LeaseFinder";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

type From = "tenant" | "authority";

// Receive payment (ADR 0010 phase 1c), as the rent roll and a housing authority's page open it:
// a tenant's payment to a lease found by its tenant, unit or property, or a housing authority's
// check, one check split across the leases it pays (it replaced the authority page's Record
// payment). Record payment closes the panel (`close`); Save and add another starts the next one.
export function ReceivePayment({ record, close, authority }: {
  record: Recorder;
  close: () => void;
  authority?: string;
}) {
  const [from, setFrom] = useState<From>(authority ? "authority" : "tenant");
  const [lease, setLease] = useState<LeaseDetail | null>(null);
  // Counts the checks recorded, so Save and add another starts a fresh form.
  const [checks, setChecks] = useState(0);

  return (
    <div className="flex flex-col gap-4">
      <Select label="From" id="receive-from" value={from} onChange={(e) => {
        setFrom(e.target.value as From);
        setLease(null);
      }}>
        <option value="tenant">A tenant</option>
        <option value="authority">A housing authority's check</option>
      </Select>
      {from === "authority" ? (
        <AuthorityCheckForm key={checks} record={record} authority={authority}
          onRecorded={(another) => (another ? setChecks((n) => n + 1) : close())} />
      ) : lease !== null ? (
        <div className="flex flex-col gap-4">
          <p className="flex flex-wrap items-center justify-between gap-2 text-base">
            <span>{lease.property} · {lease.unit} · {lease.tenants.map((t) => t.name).join(", ")}</span>
            <Button variant="ghost" size="sm" onClick={() => setLease(null)}>Another lease</Button>
          </p>
          <p className="text-sm text-ink-muted">Balance due {money(lease.owed)}</p>
          <PaymentForm lease={lease} record={record}
            onRecorded={(another) => (another ? setLease(null) : close())} />
        </div>
      ) : (
        <LeaseFinder id="pay-find-lease" onLease={setLease} />
      )}
    </div>
  );
}

// A housing authority's one payment (a check, or a deposit by EFT) for many leases: each lease's
// part pays what the authority owes on it, oldest first, into its owners' accounts, never more
// than it owes; all or none.
function AuthorityCheckForm({ record, authority, onRecorded }: {
  record: Recorder;
  authority?: string;
  onRecorded: (another: boolean) => void;
}) {
  const [authorities, setAuthorities] = useState<Authority[] | null>(null);
  const [chosen, setChosen] = useState(authority ?? "");
  const [page, setPage] = useState<AuthorityPage | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const run = latest();
    api.get<Authority[]>("/api/assistance/authorities")
      .then(run.keep((found) => {
        setAuthorities(found);
        setChosen((was) => was || found[0]?.id || "");
      }))
      .catch(run.keep((failed) => setError(detail(failed))));
    return run.stop;
  }, []);
  useEffect(() => {
    if (!chosen) return;
    const run = latest();
    setPage(null);
    api.get<AuthorityPage>(`/api/assistance/authorities/${chosen}`).then(run.keep(setPage))
      .catch(run.keep((failed) => setError(detail(failed))));
    return run.stop;
  }, [chosen]);

  if (authorities === null) return <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>;
  if (authorities.length === 0) {
    return <Notice tone="info">No housing authority yet: one is added when a lease's housing assistance is set, on the lease's page.</Notice>;
  }
  return (
    <div className="flex flex-col gap-3">
      <Select label="Housing authority" id="authority-pay-who" value={chosen} onChange={(e) => setChosen(e.target.value)}>
        {authorities.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
      </Select>
      {page === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <AuthorityLines key={page.id} page={page} record={record} onRecorded={onRecorded} />
      )}
    </div>
  );
}

function AuthorityLines({ page, record, onRecorded }: {
  page: AuthorityPage;
  record: Recorder;
  onRecorded: (another: boolean) => void;
}) {
  const [request] = useState(requestId);
  const [pastHold, setPastHold] = useState(false);
  const [on, setOn] = useState(day(new Date()));
  const [method, setMethod] = useState<PaymentMethod>("check");
  const [reference, setReference] = useState("");
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const another = useRef(false);
  const owing = page.leases.filter((lease) => Number(lease.owed) > 0);
  const held = owing.some((lease) => lease.hold_since !== null);
  const total = paymentTotal(amounts);
  return (
    <Form className="flex flex-col gap-3" onSubmit={async (event) => {
      event.preventDefault();
      const body = {
        request_id: request, housing_authority_id: page.id, received_on: on, method,
        reference: reference.trim() || null, lines: paymentLines(amounts),
        ...(held && pastHold ? { past_hold: true } : {}),
      };
      if (await record(`/api/assistance/payments`, body, () => `${money(total)} from ${page.name} recorded.`)) {
        onRecorded(another.current);
      }
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
      {held && (
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={pastHold} onChange={(e) => setPastHold(e.target.checked)} />
          Accept this payment past the payment hold
        </label>
      )}
      <p className="text-sm text-ink" data-testid="authority-pay-total">{`Total ${money(total)}`}</p>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={total === "0.00"} onClick={() => { another.current = false; }}
          says={`Records ${money(total)} from ${page.name}. The tenants' balances stay as they are, and nothing is sent.`}>
          Record payment
        </Button>
        <Button type="submit" variant="secondary" disabled={total === "0.00"} onClick={() => { another.current = true; }}
          says="Records it the same way, then starts another check.">
          Save and add another
        </Button>
      </div>
    </Form>
  );
}
