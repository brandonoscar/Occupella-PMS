import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { day, money } from "../money";
import type { OwnerMoney } from "../payables";
import type { LeaseDetail } from "../rentals";
import type { Recorder } from "./LeaseForms";

// What taking a lease's leasing fee did. An agreement that takes no leasing fee charges none:
// the lease's one leasing fee is then $0.00, not a fee to take later.
export function leasingFeeSaid(amount: string): string {
  return Number(amount) > 0
    ? `A leasing fee of ${money(amount)} is taken.`
    : "No leasing fee: the owner's agreement in force takes none, so none is due for this lease.";
}

// Take leasing fee (a lease's ⋯ menu, ADR 0006 decision 4): once per lease, from an owner's
// account of its property, at the percent their agreement says; only when staff ask.
export function LeasingFeeForm({ lease, record }: { lease: LeaseDetail; record: Recorder }) {
  const [accounts, setAccounts] = useState<OwnerMoney[] | null>(null);
  const [error, setError] = useState("");
  const [account, setAccount] = useState("");
  const [on, setOn] = useState(day(new Date()));

  useEffect(() => {
    api.get<OwnerMoney[]>(`/api/payables/owners?${new URLSearchParams({ property: lease.property_id })}`)
      .then(setAccounts)
      .catch((why) => setError(why instanceof ApiError ? why.detail : String(why)));
  }, [lease.property_id]);

  if (accounts === null) return <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>;
  if (accounts.length === 0) return <Notice tone="info">No owner holds an account for {lease.property} yet.</Notice>;
  const chosen = accounts.some((a) => a.account === account) ? account : accounts[0]!.account;
  return (
    <Form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        record<{ amount: string }>("/api/payables/leasing-fees", { lease_id: lease.id, account: chosen, on },
          (taken) => leasingFeeSaid(taken.amount));
      }}
    >
      <Select label="From owner" id="leasing-account" value={chosen} onChange={(event) => setAccount(event.target.value)}>
        {accounts.map((a) => <option key={a.account} value={a.account}>{a.owner}</option>)}
      </Select>
      <Field label="Taken on" id="leasing-on" type="date" required value={on}
        onChange={(event) => setOn(event.target.value)} />
      <div><Button type="submit">Take leasing fee</Button></div>
    </Form>
  );
}
