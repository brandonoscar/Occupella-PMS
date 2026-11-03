import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import type { PayoutMethod } from "../payables";
import type { Payee } from "../payouts";
import { bankLine, methodWord } from "../payouts";
import { useRecord } from "./useRecord";

// How an owner or a vendor is paid (ADR 0010 phase 1b, src/pms/api/payouts.py): an owner by ACH
// or check, and the bank account an ACH file pays them into. The account number is sealed by the
// server before it is kept and never comes back: the page shows its last four only. A new bank
// account replaces the one before from then on.
export function PayeeCard({ kind, id, name }: { kind: "owner" | "vendor"; id: string; name: string }) {
  const path = `/api/payouts/${kind}s/${id}`;
  const [payee, setPayee] = useState<Payee | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    api.get<Payee>(path).then(setPayee)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [path, runs]);

  return (
    <Card title="Payouts">
      <Said>{said}</Said>
      {payee === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <div className="flex flex-col gap-3">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-base" aria-label={`Payouts, ${name}`}>
            {kind === "owner" && (
              <>
                <dt className="text-ink-muted">Paid by</dt>
                <dd data-testid="payout-method">{methodWord(payee.method ?? "check")}</dd>
              </>
            )}
            <dt className="text-ink-muted">Bank account</dt>
            <dd data-testid="payout-bank">{payee.bank ? bankLine(payee.bank) : "None on file"}</dd>
          </dl>
          {kind === "owner" && payee.method === "EFT" && payee.bank === null && (
            <Notice tone="info">Paid by ACH with no bank account on file: an ACH file can't pay {name} yet.</Notice>
          )}
          <div className="flex flex-wrap gap-2">
            {kind === "owner" && (
              <MethodPicker method={payee.method ?? "check"} save={(method) =>
                void record(`${path}/method`, { method }, () => `${name} is paid by ${methodWord(method)}.`)} />
            )}
            <Button variant="secondary" onClick={() => setEditing(true)}>
              {payee.bank ? "Change bank account" : "Add bank account"}
            </Button>
          </div>
          <Notice tone="error">{editing ? "" : refused}</Notice>
          {editing && (
            <SidePanel title={`Bank account, ${name}`} onClose={() => setEditing(false)}>
              <BankForm save={(body) =>
                record(`${path}/bank-account`, body, () => `Saved ${name}'s bank account.`).then((ok) => {
                  if (ok) setEditing(false);
                })} />
              <Notice tone="error">{refused}</Notice>
            </SidePanel>
          )}
        </div>
      )}
    </Card>
  );
}

function MethodPicker({ method, save }: { method: PayoutMethod; save: (method: PayoutMethod) => void }) {
  return (
    <Select label="Paid by" id="payout-method-pick" value={method}
      onChange={(event) => save(event.target.value as PayoutMethod)}>
      <option value="check">Check</option>
      <option value="EFT">ACH</option>
    </Select>
  );
}

function BankForm({ save }: { save: (body: unknown) => Promise<void> }) {
  const [routing, setRouting] = useState("");
  const [number, setNumber] = useState("");
  const [type, setType] = useState<"checking" | "savings">("checking");
  const [holder, setHolder] = useState("");
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({ routing_number: routing, account_number: number, account_type: type, holder_name: holder });
    }}>
      <Field label="Routing number" id="payee-routing" required inputMode="numeric" pattern="[0-9]{9}"
        maxLength={9} autoComplete="off" value={routing} onChange={(e) => setRouting(e.target.value)} />
      <Field label="Account number" id="payee-account" required inputMode="numeric" pattern="[0-9]{4,17}"
        maxLength={17} autoComplete="off" value={number} onChange={(e) => setNumber(e.target.value)} />
      <Select label="Account type" id="payee-type" value={type}
        onChange={(e) => setType(e.target.value as "checking" | "savings")}>
        <option value="checking">Checking</option>
        <option value="savings">Savings</option>
      </Select>
      <Field label="Name on the account" id="payee-holder" required maxLength={100} value={holder}
        onChange={(e) => setHolder(e.target.value)} />
      <p className="text-sm text-ink-muted">
        The account number is kept sealed and shows by its last four only. It goes into an ACH file and nowhere else.
      </p>
      <div>
        <Button type="submit" says="ACH files from now on pay into it; the account before stays in the history. Nothing is paid now.">
          Save
        </Button>
      </div>
    </Form>
  );
}
