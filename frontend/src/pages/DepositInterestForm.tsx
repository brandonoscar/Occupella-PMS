import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import type { DepositInterest, InterestPaid, PaidAs } from "../depositInterest";
import { PAID_AS, paidLine, rateLine } from "../depositInterest";
import { day, money, usDate } from "../money";
import type { RentLease } from "../rent";
import { requestId } from "../rent";
import type { Recorder } from "./LeaseForms";

// What the lease's deposits earned since its last interest payment, through a day staff pick, at
// the property's rate (migration 0122), and paying it: credited against the rent while the lease
// is in effect, or added to the deposit from the owner's account. Each payment made is listed.
export function DepositInterestForm({ lease, record }: { lease: RentLease; record: Recorder }) {
  const [through, setThrough] = useState(day(new Date()));
  const [found, setFound] = useState<DepositInterest | null>(null);
  const [error, setError] = useState("");
  const [paidAs, setPaidAs] = useState<PaidAs>("rent_credit");
  const [account, setAccount] = useState("");
  const [id, setId] = useState(requestId);

  useEffect(() => {
    // An answer for a day picked before is dropped, not shown.
    let current = true;
    api.get<DepositInterest>(`/api/rent/leases/${lease.id}/deposit-interest?through=${through}`)
      .then((answer) => {
        if (!current) return;
        setFound(answer);
        if (!answer.in_effect) setPaidAs("deposit");
      })
      .catch((failed) => current && setError(failed instanceof ApiError ? failed.detail : String(failed)));
    return () => { current = false; };
  }, [lease.id, through]);

  if (found === null) return <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>;
  const terms = found.rate_from === null ? null : { starts_on: found.rate_from, annual_percent: found.annual_percent };
  const choices = PAID_AS.filter((c) => c.value === "deposit" || found.in_effect);
  const naming = paidAs === "deposit" && found.owner_accounts.length > 1;
  const owner = account || found.owner_accounts[0]?.id || "";
  return (
    <div className="flex flex-col gap-4">
      <p className="text-base" data-testid="interest-rate">{rateLine(terms)}</p>
      {found.returned ? (
        <Notice tone="info">The deposit went back: its interest was paid before it did.</Notice>
      ) : (
        <Form className="flex flex-col gap-3" onSubmit={async (event) => {
          event.preventDefault();
          const body = { request_id: id, through, paid_as: paidAs, on: day(new Date()), ...(naming ? { owner_account: owner } : {}) };
          if (await record<InterestPaid>(`/api/rent/leases/${lease.id}/deposit-interest`, body, (paid) => `${paidLine(paid)}.`)) {
            setId(requestId());
          }
        }}>
          <Field label="Interest through" id={`interest-through-${lease.id}`} type="date" required value={through}
            max={day(new Date())} onChange={(e) => setThrough(e.target.value)} />
          <ul aria-label="Deposits" className="flex flex-col gap-1 text-base">
            {found.deposits.map((d) => (
              <li key={d.tenant}>{d.tenant}: {money(d.held)} held, {money(d.interest)} earned</li>
            ))}
          </ul>
          <p className="text-base" data-testid="interest-due">
            Due for {usDate(found.from_on)} to {usDate(found.through)}: {money(found.due)}
          </p>
          <Select label="Pay it as" id={`interest-as-${lease.id}`} value={paidAs}
            onChange={(e) => setPaidAs(e.target.value as PaidAs)}>
            {choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </Select>
          {naming && (
            <Select label="From the owner's account" id={`interest-owner-${lease.id}`} value={owner}
              onChange={(e) => setAccount(e.target.value)}>
              {found.owner_accounts.map((a) => <option key={a.id} value={a.id}>{a.owner}</option>)}
            </Select>
          )}
          <div><Button type="submit" disabled={Number(found.due) <= 0}>Pay interest</Button></div>
        </Form>
      )}
      {found.paid.length > 0 && (
        <ul aria-label="Interest paid" className="flex flex-col gap-1 text-sm text-ink-muted">
          {found.paid.map((p) => <li key={p.from_on}>{paidLine(p)}, on {usDate(p.paid_on)}</li>)}
        </ul>
      )}
    </div>
  );
}
