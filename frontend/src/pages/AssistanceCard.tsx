import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type { AssistanceTerms, Authority, AuthorityCharge, LeaseAssistance } from "../assistance";
import { nextFirst, splitLine, termsLine } from "../assistance";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { RowMenu } from "../components/RowMenu";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { Wide } from "../components/Wide";
import { day, money, sumMoney, usDate } from "../money";
import type { Recorder } from "./useRecord";
import { useRecord } from "./useRecord";

type Panel = { kind: "terms" } | { kind: "charge" } | { kind: "credit"; charge: AuthorityCharge };
const TITLES = { terms: "Set housing assistance", charge: "Charge the housing authority", credit: "Credit the housing authority" };

// A lease's housing assistance (Section 8; migration 0095, the founder's review q034): what its
// housing authority pays of the rent from each month, and what the authority owes for the lease.
// The tenant's balance, late fees and deposit are the tenant's part alone; the authority's
// payments are recorded on its own page.
export function AssistanceCard({ leaseId, unit }: { leaseId: string; unit: string }) {
  const [assistance, setAssistance] = useState<LeaseAssistance | null>(null);
  const [authorities, setAuthorities] = useState<Authority[]>([]);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState<Panel | null>(null);
  const { said, error: refused, record } = useRecord(refresh);

  function refresh() {
    api.get<LeaseAssistance>(`/api/assistance/leases/${leaseId}`).then(setAssistance)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
    api.get<Authority[]>("/api/assistance/authorities").then(setAuthorities).catch(() => setAuthorities([]));
  }
  useEffect(refresh, [leaseId]);

  const recordThenClose: Recorder = async (path, body, done) => {
    const recorded = await record(path, body, done);
    if (recorded) setPanel(null);
    return recorded;
  };

  return (
    <Card title="Housing assistance">
      <div className="flex flex-col gap-3">
        <Notice tone="error">{error}</Notice>
        <Said>{said}</Said>
        {assistance === null ? (
          !error && <p className="text-sm text-ink-subtle">Loading</p>
        ) : (
          <>
            <p className="text-base" data-testid="assistance-split">{splitLine(assistance)}.</p>
            {assistance.terms.length > 0 && (
              <ul className="text-sm text-ink-muted" aria-label={`Housing assistance, ${unit}`}>
                {assistance.terms.map((t: AssistanceTerms) => <li key={t.id}>{termsLine(t)}</li>)}
              </ul>
            )}
            {assistance.charges.length > 0 && (
              <Wide>
                <table className="w-full text-base" aria-label="What the housing authority owes">
                  <thead>
                    <tr>
                      <th className="text-left">Due</th>
                      <th className="text-left">For</th>
                      <th className="text-right">Charged</th>
                      <th className="text-right">Paid</th>
                      <th className="text-right">Open</th>
                      <th><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {assistance.charges.map((c) => (
                      <tr key={c.id} aria-label={`${c.memo}, ${usDate(c.due_on)}`}>
                        <td>{usDate(c.due_on)}</td>
                        <td className="text-ink-muted">{c.memo}</td>
                        <td className="text-right">{money(sumCharged(c))}</td>
                        <td className="text-right">{money(c.paid)}</td>
                        <td className="text-right">{money(c.open)}</td>
                        <td className="text-right">
                          <RowMenu label={c.memo} actions={[
                            { label: "Credit the authority", onSelect: () => setPanel({ kind: "credit", charge: c }) },
                          ]} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Wide>
            )}
            {assistance.charges.length > 0 && (
              <p className="text-sm text-ink-muted" data-testid="authority-owes">
                {`The housing authority owes ${money(assistance.owed)} for this lease.`}
              </p>
            )}
          </>
        )}
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setPanel({ kind: "terms" })}>Set housing assistance</Button>
          <Button variant="secondary" onClick={() => setPanel({ kind: "charge" })}>Charge the authority</Button>
        </div>
      </div>
      {panel !== null && (
        <SidePanel title={TITLES[panel.kind]} onClose={() => setPanel(null)}>
          {panel.kind === "terms" && <TermsForm leaseId={leaseId} authorities={authorities} record={recordThenClose} />}
          {panel.kind === "charge" && <ChargeForm leaseId={leaseId} authorities={authorities} record={recordThenClose} />}
          {panel.kind === "credit" && <CreditForm leaseId={leaseId} charge={panel.charge} record={recordThenClose} />}
          <Notice tone="error">{refused}</Notice>
        </SidePanel>
      )}
    </Card>
  );
}

// What an authority's charge comes to, with what a lease's end or a credit changed.
function sumCharged(charge: AuthorityCharge): string {
  return sumMoney([charge.charged, charge.adjusted]);
}

function AuthorityPicker({ id, authorities, value, onChange }: {
  id: string; authorities: Authority[]; value: string; onChange: (id: string) => void;
}) {
  if (authorities.length === 0) {
    return <Notice tone="info">Add the housing authority first, under Rentals, Housing authorities.</Notice>;
  }
  return (
    <Select label="Housing authority" id={id} value={value} required onChange={(e) => onChange(e.target.value)}>
      {authorities.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
    </Select>
  );
}

function TermsForm({ leaseId, authorities, record }: { leaseId: string; authorities: Authority[]; record: Recorder }) {
  const [authority, setAuthority] = useState(authorities[0]?.id ?? "");
  const [startsOn, setStartsOn] = useState(nextFirst(new Date()));
  const [amount, setAmount] = useState("");
  const [contract, setContract] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = { housing_authority_id: authority, starts_on: startsOn, amount, contract: contract.trim() || null };
      return record(`/api/assistance/leases/${leaseId}/terms`, body, () =>
        Number(amount) === 0
          ? `Housing assistance ends from ${usDate(startsOn)}.`
          : `The housing authority pays ${money(amount)} a month from ${usDate(startsOn)}.`);
    }}>
      <p className="text-sm text-ink-muted">
        From the first of a month whose rent isn't charged yet. Each month's rent is then split as it is
        charged: the authority's part is its own, and the tenant owes the rest. 0 ends it.
      </p>
      <AuthorityPicker id="assist-authority" authorities={authorities} value={authority} onChange={setAuthority} />
      <Field label="From (the first of a month)" id="assist-from" type="date" required value={startsOn}
        onChange={(e) => setStartsOn(e.target.value)} />
      <Field label="The authority pays each month" id="assist-amount" inputMode="decimal" required value={amount}
        onChange={(e) => setAmount(e.target.value)} />
      <Field label="HAP contract (optional)" id="assist-contract" maxLength={100} value={contract}
        onChange={(e) => setContract(e.target.value)} />
      <div><Button type="submit" disabled={authorities.length === 0}>Save assistance</Button></div>
    </Form>
  );
}

function ChargeForm({ leaseId, authorities, record }: { leaseId: string; authorities: Authority[]; record: Recorder }) {
  const [authority, setAuthority] = useState(authorities[0]?.id ?? "");
  const [amount, setAmount] = useState("");
  const [dueOn, setDueOn] = useState(day(new Date()));
  const [memo, setMemo] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = { kind: "charge", housing_authority_id: authority, amount, due_on: dueOn, memo };
      return record(`/api/assistance/leases/${leaseId}/charges`, body, () => `${money(amount)} charged to the housing authority.`);
    }}>
      <p className="text-sm text-ink-muted">
        What the authority owes that the rent run didn't charge it, a month before its assistance was on file, say.
      </p>
      <AuthorityPicker id="authority-charge-authority" authorities={authorities} value={authority} onChange={setAuthority} />
      <Field label="Amount" id="authority-charge-amount" inputMode="decimal" required value={amount}
        onChange={(e) => setAmount(e.target.value)} />
      <Field label="Due on" id="authority-charge-due" type="date" required value={dueOn}
        onChange={(e) => setDueOn(e.target.value)} />
      <Field label="What it is for" id="authority-charge-memo" required maxLength={200} value={memo}
        onChange={(e) => setMemo(e.target.value)} />
      <div><Button type="submit" disabled={authorities.length === 0}>Charge the authority</Button></div>
    </Form>
  );
}

function CreditForm({ leaseId, charge, record }: { leaseId: string; charge: AuthorityCharge; record: Recorder }) {
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      const body = { kind: "credit", charge_id: charge.id, amount, due_on: day(new Date()), memo };
      return record(`/api/assistance/leases/${leaseId}/charges`, body, () => `${money(amount)} credited off ${charge.memo}.`);
    }}>
      <p className="text-sm text-ink-muted" data-testid="credit-of">
        {`${charge.memo}, due ${usDate(charge.due_on)}: ${money(charge.open)} open. A credit (an abatement, say) takes no more than that off it.`}
      </p>
      <Field label="Amount" id="authority-credit-amount" inputMode="decimal" required value={amount}
        onChange={(e) => setAmount(e.target.value)} />
      <Field label="Why" id="authority-credit-memo" required maxLength={200} value={memo}
        onChange={(e) => setMemo(e.target.value)} />
      <div><Button type="submit">Credit the authority</Button></div>
    </Form>
  );
}
