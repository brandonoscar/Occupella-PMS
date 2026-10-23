import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { latest } from "../latest";
import { usDate } from "../money";
import { useRecord } from "../pages/useRecord";
import { Button } from "./Button";
import { ConfirmDialog } from "./Confirm";
import { Field } from "./Field";
import { Form } from "./Form";
import { Notice } from "./Notice";
import { Said } from "./Said";
import { Select } from "./Select";
import { Card } from "./Shell";
import { SidePanel } from "./SidePanel";

// Where a vendor or an owner is paid by bank transfer (migration 0155, the founder 2026-10-23):
// the name on the account, the routing number, checking or savings and the account number's last
// four digits. The whole number is typed twice and sealed by the server; it is never shown again.
// Changing or removing it takes the disburse role (the server says so if it is missing).

export type BankAccount = {
  holder_name: string;
  routing_number: string;
  account_type: "checking" | "savings";
  account_last4: string;
  recorded_by: string | null;
  recorded_at: string;
};

const KIND = { checking: "Checking", savings: "Savings" } as const;

// The account as staff see it: "Checking ••••6789, routing 001000009, in the name of …".
export function bankAccountLine(account: BankAccount): string {
  return `${KIND[account.account_type]} ••••${account.account_last4}, routing ${account.routing_number}, in the name of ${account.holder_name}`;
}

// An answer that is an account on file; anything else (null) is none.
function isAccount(answer: unknown): answer is BankAccount {
  return typeof answer === "object" && answer !== null && typeof (answer as BankAccount).account_last4 === "string";
}

export function BankAccountCard({ path, name }: { path: string; name: string }) {
  const [account, setAccount] = useState<BankAccount | null | undefined>(undefined);
  const [unread, setUnread] = useState("");
  const [runs, setRuns] = useState(0);
  const [editing, setEditing] = useState(false);
  const [asking, setAsking] = useState(false);
  const { said, error, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    const run = latest();
    api.get<unknown>(path)
      .then(run.keep((answer: unknown) => setAccount(isAccount(answer) ? answer : null)))
      .catch(run.keep((failed) => setUnread(failed instanceof ApiError ? failed.detail : String(failed))));
    return run.stop;
  }, [path, runs]);
  if (account === undefined) return <Notice tone={unread ? "error" : "info"}>{unread || "Loading"}</Notice>;
  return (
    <Card title="Bank account">
      <div role="group" aria-label={`Bank account, ${name}`} className="flex flex-col gap-3">
        {account === null ? (
          <p className="text-base text-ink-muted">No bank account on file. Add one to pay {name} by bank transfer (ACH).</p>
        ) : (
          <>
            <p className="text-base" data-testid="bank-account">{bankAccountLine(account)}</p>
            <p className="text-sm text-ink-muted">
              Set {usDate(account.recorded_at.slice(0, 10))}{account.recorded_by ? ` by ${account.recorded_by}` : ""}.
            </p>
          </>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
            {account === null ? "Add bank account" : "Change bank account"}
          </Button>
          {account !== null && <Button variant="ghost" size="sm" onClick={() => setAsking(true)}>Remove</Button>}
        </div>
        <Said>{said}</Said>
        {!editing && <Notice tone="error">{error}</Notice>}
      </div>
      {editing && (
        <BankAccountPanel name={name} changing={account !== null} error={error} onClose={() => setEditing(false)}
          save={async (body) => {
            if (await record<BankAccount>(path, body, (kept) => `${name}'s bank account is set: ••••${kept.account_last4}.`)) {
              setEditing(false);
            }
          }} />
      )}
      {asking && (
        <ConfirmDialog
          question={`Remove ${name}'s bank account? They can't be paid by bank transfer until one is added again.`}
          confirmLabel="Remove"
          danger
          onCancel={() => setAsking(false)}
          onConfirm={() => {
            setAsking(false);
            void record(`${path}/remove`, {}, () => `${name}'s bank account is removed.`);
          }}
        />
      )}
    </Card>
  );
}

type NewBankAccount = {
  holder_name: string;
  routing_number: string;
  account_type: "checking" | "savings";
  account_number: string;
  account_number_again: string;
};

function BankAccountPanel({ name, changing, error, onClose, save }: {
  name: string;
  changing: boolean;
  error: string;
  onClose: () => void;
  save: (body: NewBankAccount) => Promise<void>;
}) {
  const [form, setForm] = useState<NewBankAccount>({
    holder_name: name.slice(0, 22), routing_number: "", account_type: "checking", account_number: "", account_number_again: "",
  });
  const set = (key: keyof NewBankAccount) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [key]: e.target.value });
  return (
    <SidePanel title={changing ? "Change bank account" : "Add bank account"} onClose={onClose}>
      <Form className="flex flex-col gap-3" onSubmit={(event) => {
        event.preventDefault();
        return save(form);
      }}>
        <Field label="Name on the account" id="bank-holder" required maxLength={22} value={form.holder_name} onChange={set("holder_name")} />
        <Field label="Routing number" id="bank-routing" required inputMode="numeric" autoComplete="off" value={form.routing_number}
          onChange={set("routing_number")} />
        <Select label="Account type" id="bank-type" value={form.account_type} onChange={set("account_type")}>
          <option value="checking">Checking</option>
          <option value="savings">Savings</option>
        </Select>
        <Field label="Account number" id="bank-number" required inputMode="numeric" autoComplete="off" value={form.account_number}
          onChange={set("account_number")} />
        <Field label="Account number again" id="bank-number-again" required inputMode="numeric" autoComplete="off"
          value={form.account_number_again} onChange={set("account_number_again")} />
        <p className="text-sm text-ink-muted">The number is kept encrypted; after saving, only its last four digits show.</p>
        <div><Button type="submit">{changing ? "Change bank account" : "Add bank account"}</Button></div>
        <Notice tone="error">{error}</Notice>
      </Form>
    </SidePanel>
  );
}
