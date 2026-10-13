import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { Card, Shell } from "../components/Shell";
import { day, money } from "../money";
import type { MoveOut } from "../moveout";
import { chargeLabel, hasEnded, leaseLabel, preview } from "../moveout";
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
      <h1 className="font-display text-xl font-medium">Move-out</h1>
      {leases === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : leases.length === 0 ? (
        <Notice tone="info">No lease holds a security deposit.</Notice>
      ) : (
        leases.map((lease) => <LeaseCard key={lease.lease_id} lease={lease} refresh={refresh} />)
      )}
    </Shell>
  );
}

function LeaseCard({ lease, refresh }: { lease: MoveOut; refresh: () => void }) {
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
  return (
    <Card title={leaseLabel(lease)}>
      <p className="mb-3 text-sm text-ink-muted">
        Lease {lease.starts_on} to {lease.ends_on ?? "month to month"}. Owes {money(lease.owed)};
        deposits held {money(lease.held)}.
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
            <ul className="mb-4 flex flex-col gap-1 text-sm" aria-label={`Owed, ${leaseLabel(lease)}`}>
              {lease.open_charges.map((c) => (
                <li key={c.id}>
                  {chargeLabel(c)}: {money(c.open)}
                </li>
              ))}
            </ul>
          )}
          <form
            className="mb-4 flex flex-wrap items-end gap-3"
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
            className="mb-4 flex flex-wrap items-end gap-3"
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
          {hasEnded(lease, today) ? (
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
          ) : (
            <p className="text-sm text-ink-subtle">The deposit goes back once the lease has ended.</p>
          )}
        </>
      )}
      <Notice tone="info">{said}</Notice>
      <Notice tone="error">{error}</Notice>
    </Card>
  );
}
