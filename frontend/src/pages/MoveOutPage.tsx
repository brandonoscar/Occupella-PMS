import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button, LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { day, money } from "../money";
import type { MoveOut } from "../moveout";
import { chargeLabel, hasEnded, leaseLabel, moveOutStatus, preview } from "../moveout";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

// Staff's move-outs: end the lease, charge what the tenants owe for cleaning or damage, and
// return their deposits. The return keeps what the lease owes for the owner and gives the rest
// back; the statement for the tenants is a report. It records what the bank paid; the PMS moves
// no money itself.
export function MoveOutPage({ me }: { me: Me }) {
  const [leases, setLeases] = useState<MoveOut[] | null>(null);
  const [error, setError] = useState("");

  function refresh() {
    api
      .get<MoveOut[]>("/api/move-out")
      .then(setLeases)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(refresh, []);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Move-out" description="Return the deposits of a lease that has ended, with the itemized statement." />
      {leases === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : leases.length === 0 ? (
        <Notice tone="info">No lease holds a security deposit.</Notice>
      ) : (
        <Card title="Leases holding a deposit">
          <ul className="flex flex-col divide-y divide-line">
            {leases.map((lease) => (
              <LeaseRow key={lease.lease_id} lease={lease} refresh={refresh} />
            ))}
          </ul>
        </Card>
      )}
    </Shell>
  );
}

function LeaseRow({ lease, refresh }: { lease: MoveOut; refresh: () => void }) {
  const { said, error, record } = useRecord(refresh);
  const today = day(new Date());
  const id = lease.lease_id;
  const [ends, setEnds] = useState(lease.ends_on ?? "");
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState("");
  const [on, setOn] = useState(today);
  const [owner, setOwner] = useState(lease.owner_accounts[0]?.id ?? "");
  const [request] = useState(() => crypto.randomUUID());
  const next = preview(lease);
  const ended = hasEnded(lease, today);
  const status = moveOutStatus(lease, today);
  // The end and a damage charge: in view once the lease has ended, folded away while it runs.
  const endAndCharge = (
    <>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          record(`/api/move-out/${id}/end`, { ends_on: ends }, () => `The lease ends on ${ends}.`);
        }}
      >
        <Field label="Last day" id={`ends-${id}`} type="date" required value={ends}
          onChange={(e) => setEnds(e.target.value)} />
        <Button type="submit" variant="secondary">End the lease</Button>
      </form>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          const body = { amount, due_on: lease.ends_on ?? today, memo };
          if (await record(`/api/move-out/${id}/deductions`, body, () => `Charged ${money(amount)}: ${memo.trim()}.`)) {
            setAmount("");
            setMemo("");
          }
        }}
      >
        <Field label="Cleaning or damage" id={`memo-${id}`} required value={memo}
          onChange={(e) => setMemo(e.target.value)} />
        <Field label="Amount" id={`amount-${id}`} inputMode="decimal" required value={amount}
          onChange={(e) => setAmount(e.target.value)} />
        <Button type="submit" variant="secondary">Charge it</Button>
      </form>
    </>
  );
  return (
    <li>
      <section aria-label={leaseLabel(lease)} className="flex flex-col gap-3 py-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p>
            <span className="font-medium">{lease.property}, {lease.unit}</span>
            <span className="text-ink-muted"> · {lease.tenants.join(", ")}</span>
          </p>
          <Badge tone={status.tone}>{status.label}</Badge>
        </div>
        <p className="text-sm text-ink-muted">
          Lease {lease.starts_on} to {lease.ends_on ?? "month to month"} · owes {money(lease.owed)} ·
          deposits held {money(lease.held)}
        </p>
        {lease.settled ? (
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm" aria-label={`Returned, ${leaseLabel(lease)}`}>
              Returned on {lease.settled.returned_on}: kept {money(lease.settled.kept)} of{" "}
              {money(lease.settled.held)}, {money(lease.settled.returned)} back to the tenants.
            </p>
            <LinkButton href={`/api/reports/trust_report_deposit_return?arg=${id}&format=pdf`}>
              Statement for the tenants (PDF)
            </LinkButton>
          </div>
        ) : (
          <>
            {lease.open_charges.length > 0 && (
              <ul className="flex flex-col gap-1 text-sm" aria-label={`Owed, ${leaseLabel(lease)}`}>
                {lease.open_charges.map((c) => (
                  <li key={c.id}>
                    {chargeLabel(c)}: {money(c.open)}
                  </li>
                ))}
              </ul>
            )}
            {ended ? (
              endAndCharge
            ) : (
              <details>
                <summary>Move-out for {lease.unit}</summary>
                <div className="mt-3 flex flex-col gap-4">
                  {endAndCharge}
                  <p className="text-sm text-ink-subtle">The deposit goes back once the lease has ended.</p>
                </div>
              </details>
            )}
            {ended ? (
              <form
                className="flex flex-wrap items-end gap-3"
                onSubmit={(event) => {
                  event.preventDefault();
                  const body = {
                    request_id: request,
                    on,
                    owner_account: lease.owner_accounts.length > 1 ? owner : null,
                  };
                  record<{ returned: string }>(`/api/move-out/${id}/return`, body,
                    (done) => `Returned ${money(done.returned)} to the tenants; the rest paid what the lease owed.`);
                }}
              >
                <Field label="Paid on" id={`on-${id}`} type="date" required value={on}
                  onChange={(e) => setOn(e.target.value)} />
                {lease.owner_accounts.length > 1 && (
                  <Select label="Kept money to" id={`owner-${id}`} value={owner}
                    onChange={(e) => setOwner(e.target.value)}>
                    {lease.owner_accounts.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.owner}
                      </option>
                    ))}
                  </Select>
                )}
                <Button type="submit">Return the deposit</Button>
                <p className="w-full text-sm text-ink-subtle">
                  Keeps {money(next.kept)} for what the lease owes; {money(next.returned)} goes back.
                </p>
              </form>
            ) : null}
          </>
        )}
        <Notice tone="info">{said}</Notice>
        <Notice tone="error">{error}</Notice>
      </section>
    </li>
  );
}
