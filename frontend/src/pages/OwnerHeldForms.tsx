import { useState } from "react";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Select } from "../components/Select";
import { companyDay, money } from "../money";
import type { OwnerHeld } from "../ownerHeld";
import { fromCents, toCents } from "../reconcile";
import type { DepositKind, RentLease } from "../rent";
import { DEPOSIT_KINDS } from "../rent";
import type { Recorder as LeaseRecorder } from "./LeaseForms";
import type { Recorder } from "./useRecord";

// The lease page: a tenant's deposit the owner keeps themselves, outside trust. No money moves.
export function OwnerHeldForm({ lease, record }: { lease: RentLease; record: LeaseRecorder }) {
  const [tenant, setTenant] = useState(lease.tenants[0]?.id ?? "");
  const [kind, setKind] = useState<DepositKind>("security");
  const [amount, setAmount] = useState("");
  const [since, setSince] = useState(companyDay());
  const [memo, setMemo] = useState("");
  const label = DEPOSIT_KINDS.find((k) => k.value === kind)?.label ?? "Deposit";
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record(`/api/rent/leases/${lease.id}/owner-held-deposits`,
        { tenant_id: tenant, kind, amount, held_since: since, memo: memo.trim() || null },
        () => `${label} of ${money(amount)} recorded as held by the owner.`);
    }}>
      <Select label="Whose deposit" id={`owner-held-tenant-${lease.id}`} value={tenant}
        onChange={(e) => setTenant(e.target.value)}>
        {lease.tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
      </Select>
      <Select label="Kind of deposit" id={`owner-held-kind-${lease.id}`} value={kind}
        onChange={(e) => setKind(e.target.value as DepositKind)}>
        {DEPOSIT_KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
      </Select>
      <Field label="Amount the owner holds" id={`owner-held-amount-${lease.id}`} inputMode="decimal" required
        value={amount} onChange={(e) => setAmount(e.target.value)} />
      <Field label="Held since" id={`owner-held-since-${lease.id}`} type="date" required max={companyDay()}
        value={since} onChange={(e) => setSince(e.target.value)} />
      <Field label="Memo (optional)" id={`owner-held-memo-${lease.id}`} value={memo}
        onChange={(e) => setMemo(e.target.value)} />
      <div>
        <Button type="submit" says="No money moves: the owner holds it, outside trust, and returns it at move-out.">
          Record what the owner holds
        </Button>
      </div>
    </Form>
  );
}

// Move-outs: what the owner returned to the tenant and kept of a deposit they hold, together what
// they held. The owner pays it; nothing moves in trust.
export function OwnerHeldOutcomeForm({ leaseId, held, record }: { leaseId: string; held: OwnerHeld; record: Recorder }) {
  const [kept, setKept] = useState("0.00");
  const [on, setOn] = useState(companyDay());
  // What is typed and isn't an amount yet keeps nothing back, until it is one.
  const keptCents = /^\d+(\.\d{1,2})?$/.test(kept.trim()) ? toCents(kept) : 0;
  const returned = fromCents(Math.max(toCents(held.amount) - keptCents, 0));
  return (
    <Form className="flex flex-wrap items-end gap-3" onSubmit={(event) => {
      event.preventDefault();
      return record(`/api/move-out/${leaseId}/owner-held-deposits/${held.id}/settle`,
        { returned, kept: kept || "0.00", on },
        () => `Recorded: the owner returned ${money(returned)} and kept ${money(kept || "0.00")}.`);
    }}>
      <Field label="Kept by the owner" id={`owner-kept-${held.id}`} inputMode="decimal" required value={kept}
        onChange={(e) => setKept(e.target.value)} />
      <Field label="On" id={`owner-kept-on-${held.id}`} type="date" required max={companyDay()} value={on}
        onChange={(e) => setOn(e.target.value)} />
      <Button type="submit" variant="secondary"
        says={`The owner returns ${money(returned)} to the tenant themselves; nothing moves in trust.`}>
        Record what the owner did
      </Button>
    </Form>
  );
}
