import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Select } from "../components/Select";
import { day, money, sumMoney } from "../money";
import { requestId } from "../rent";
import type { LeaseDetail } from "../rentals";
import { navigate } from "../router";
import type { Portfolio } from "../setup";
import type { Recorder } from "./LeaseForms";

// What a move answers (POST /api/move-out/{id}/transfer): the new lease, the tenants' prepaid rent
// that paid what the old lease owed, and what of the rest moved to the new home's owner's trust
// bank account (money staff move at the bank).
export type Transferred = { lease_id: string; prepaid_used: string; prepaid_moved: string };

// "Moved to Unit 7. Their prepaid rent paid $1,000.00 the old lease owed. ..."
export function transferredLine(done: Transferred, home: string): string {
  return [
    `Moved to ${home}.`,
    Number(done.prepaid_used) > 0 ? `Their prepaid rent paid ${money(done.prepaid_used)} the old lease owed.` : "",
    Number(done.prepaid_moved) > 0
      ? `Move ${money(done.prepaid_moved)} of their prepaid rent to the new home's trust bank account at the bank.`
      : "",
  ].filter(Boolean).join(" ");
}

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// The lease's tenants moving to another unit, maybe at another property (migration 0091): this
// lease ends the day before, a lease opens there for the tenants staying on it, and their
// deposits stay held for it once they agreed in writing. Their prepaid rent first pays what this
// lease owes; what it still owes stays owed to its owner, and the new lease's payments pay it
// first. The page opens the new lease.
export function TransferForm({ lease, record }: { lease: LeaseDetail; record: Recorder }) {
  const [books, setBooks] = useState<Portfolio | null>(null);
  const [error, setError] = useState("");
  const [unit, setUnit] = useState("");
  const [movedOn, setMovedOn] = useState(day(new Date()));
  const [endsOn, setEndsOn] = useState("");
  const [rent, setRent] = useState(lease.monthly_rent);
  const [dueDay, setDueDay] = useState(String(lease.rent_due_day));
  const [owner, setOwner] = useState("");
  const [agreed, setAgreed] = useState("");
  const [request] = useState(requestId);
  useEffect(() => {
    api.get<Portfolio>("/api/setup").then(setBooks).catch((failed) => setError(detail(failed)));
  }, []);

  if (books === null) return <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>;
  const deposit = sumMoney(lease.tenants.map((t) => t.deposit));
  const owners = books.accounts.filter((a) => a.property_id === lease.property_id);
  const homes = books.properties.flatMap((p) =>
    p.units.filter((u) => u.id !== lease.unit_id).map((u) => ({ id: u.id, name: `${p.name} · ${u.name}` })));
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      let moved: Transferred | null = null;
      const home = homes.find((h) => h.id === unit)?.name ?? "the unit";
      return record<Transferred>(`/api/move-out/${lease.id}/transfer`, {
        request_id: request,
        unit_id: unit,
        moved_on: movedOn,
        ends_on: endsOn || null,
        monthly_rent: rent,
        rent_due_day: Number(dueDay),
        owner_account: owners.length > 1 ? owner : null,
        // Asked only when the tenants hold a deposit.
        deposit_agreed_on: agreed || null,
      }, (done) => {
        moved = done;
        return transferredLine(done, home);
      }).then(() => {
        // Set only once the move is recorded.
        const done = moved as Transferred | null;
        if (done) navigate(`/leases/${done.lease_id}`);
      });
    }}>
      <Select label="Move to" id="move-unit" required value={unit} onChange={(e) => setUnit(e.target.value)}>
        <option value="">Choose a unit</option>
        {homes.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
      </Select>
      <Field label="First day there" id="move-on" type="date" required value={movedOn}
        onChange={(e) => setMovedOn(e.target.value)} />
      <Field label="Last day there (optional)" id="move-ends" type="date" value={endsOn}
        onChange={(e) => setEndsOn(e.target.value)} />
      <Field label="Monthly rent there" id="move-rent" inputMode="decimal" required value={rent}
        onChange={(e) => setRent(e.target.value)} />
      <Field label="Rent due day" id="move-due" type="number" min={1} max={28} required value={dueDay}
        onChange={(e) => setDueDay(e.target.value)} />
      {owners.length > 1 && (
        <Select label="What this lease still owes is paid to" id="move-owner" required value={owner}
          onChange={(e) => setOwner(e.target.value)}>
          <option value="">Choose the owner</option>
          {owners.map((a) => <option key={a.account} value={a.account}>{a.owner}</option>)}
        </Select>
      )}
      {Number(deposit) > 0 && (
        <Field label={`The tenants agreed in writing that their ${money(deposit)} deposit stays held, on`}
          id="move-agreed" type="date" required value={agreed} onChange={(e) => setAgreed(e.target.value)} />
      )}
      <p className="text-sm text-ink-muted">
        This lease ends the day before. Their prepaid rent first pays what it owes; whatever it still owes
        stays owed to its owner, and a payment on the new lease pays that first.
      </p>
      <div><Button type="submit">Move the tenants</Button></div>
    </Form>
  );
}
