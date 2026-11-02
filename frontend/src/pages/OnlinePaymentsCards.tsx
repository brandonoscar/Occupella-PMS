import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { Form } from "../components/Form";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card } from "../components/Shell";
import { SidePanel } from "../components/SidePanel";
import { money } from "../money";
import type { FeePayer, PaymentSettings, Waiting } from "../payments";
import { PAYMENT_SETTINGS, settingsLine, waitingLine } from "../payments";
import { requestId } from "../rent";
import { useRecord } from "./useRecord";
import { AskFirst } from "../components/Confirm";

// Company setup: whether tenants may pay online, and who pays the processing fee on a bank
// payment (the founder, 2026-10-23: the company chooses). Off until a company turns it on.
export function OnlinePaymentsCard() {
  const [settings, setSettings] = useState<PaymentSettings | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  useEffect(() => {
    api.get<PaymentSettings>(PAYMENT_SETTINGS).then(setSettings)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [runs]);

  return (
    <Card title="Online payments">
      <Said>{said}</Said>
      {settings === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : (
        <>
          <p className="text-base" data-testid="online-payments">{settingsLine(settings)}</p>
          <p className="mt-1 text-sm text-ink-muted">
            Tenants pay from their lease in the portal, on the payments partner's page. A payment reaches the books
            once the bank pays it. The partner's fee comes out of the company's fee account; what that can't cover
            waits on Banking.
          </p>
          {settings.partner !== null && (
            <div className="mt-3">
              <Button variant="secondary" onClick={() => setEditing(true)}>Edit online payments</Button>
            </div>
          )}
          {editing && (
            <SidePanel title="Online payments" onClose={() => setEditing(false)}>
              <SettingsForm settings={settings} save={(body) =>
                record(PAYMENT_SETTINGS, body, () => "Saved online payments.").then((ok) => {
                  if (ok) setEditing(false);
                })} />
              <Notice tone="error">{refused}</Notice>
            </SidePanel>
          )}
        </>
      )}
    </Card>
  );
}

function SettingsForm({ settings, save }: { settings: PaymentSettings; save: (body: unknown) => Promise<void> }) {
  const [online, setOnline] = useState(settings.online);
  const [payer, setPayer] = useState<FeePayer>(settings.ach_fee_payer);
  return (
    <Form className="flex flex-col gap-3" onSubmit={(event) => {
      event.preventDefault();
      return save({ online, ach_fee_payer: payer });
    }}>
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={online} onChange={(e) => setOnline(e.target.checked)} />
        Tenants may pay online
      </label>
      <Select label="Who pays the processing fee on a bank payment" id="fee-payer" value={payer}
        onChange={(e) => setPayer(e.target.value as FeePayer)}>
        <option value="company">The company, from its fees in trust (free for tenants)</option>
        <option value="tenant">The tenant, shown before they pay</option>
      </Select>
      <p className="text-sm text-ink-muted">
        Some states limit what a tenant may be charged to pay, and every tenant must still be able to pay by check
        or money order for free. Check your state's rules before passing the fee on.
      </p>
      <div>
        <Button type="submit" says={online
          ? "Saves it; tenants can pay online from their lease. Payments already started keep the fee they had."
          : "Saves it; tenants can't start a payment online, and autopay starts none. Payments already made stay as they are."}>
          Save
        </Button>
      </div>
    </Form>
  );
}

// Banking: what online payments left for staff. A payment paid while its lease was on a hold
// (accept it), a return the owner's account can't cover yet (try again once it can), and what the
// company's fee account can't cover (the company deposits it from its own account, then Record).
export function OnlinePaymentsWaiting({ refresh }: { refresh: number }) {
  const [items, setItems] = useState<Waiting[]>([]);
  const [runs, setRuns] = useState(0);
  const { said, error, record } = useRecord(() => setRuns((n) => n + 1));
  const { ask, prompt } = useConfirm();
  useEffect(() => {
    api.get<Waiting[]>("/api/payments/waiting").then(setItems).catch(() => setItems([]));
  }, [runs, refresh]);
  const banks = [...new Map(items.filter((w) => w.company_owes !== null)
    .map((w) => [w.bank_account_id, w.cover_needed])).entries()].filter(([, needed]) => needed !== "0.00");

  // What the last save did stays said after it empties the card, so it is announced.
  return (
    <>
      <Said>{said}</Said>
      {items.length > 0 && (
    <Card title="Online payments waiting">
      <Notice tone="error">{error}</Notice>
      <ul className="flex flex-col divide-y divide-line text-base" aria-label="Online payments waiting">
        {items.map((item) => (
          <li key={`${item.payment_id}-${item.what}`} className="flex flex-wrap items-center justify-between gap-2 py-2">
            <span>
              {item.memo} · {money(item.amount)}
              <span className="block text-sm text-ink-muted">{waitingLine(item)}</span>
            </span>
            {item.what === "payment" && (
              <Button variant="secondary" onClick={() => ask(
                `Record ${money(item.amount)} paid online for ${item.memo}, accepting it past the lease's payment hold?`
                  + " The bank already paid it; this puts it on the lease's balance. The hold stays for other payments.",
                () => record(`/api/payments/${item.payment_id}/record`, { past_hold: true },
                  () => `Recorded ${money(item.amount)} paid online.`),
                "Record it")}>Accept past the hold</Button>
            )}
            {item.what === "return" && (
              <AskFirst variant="secondary"
                says="Records the return if the owner's balance now covers it: the payment comes off the lease and any NSF fee is charged."
                question={`Record that ${money(item.amount)} for ${item.memo} came back? The payment comes off the lease, and the property's NSF fee, if any, is charged.`}
                onConfirm={() => record(`/api/payments/${item.payment_id}/record`, {},
                () => "Recorded what came back.")}>Try again</AskFirst>
            )}
          </li>
        ))}
      </ul>
      {banks.map(([bank, needed]) => (
        <div key={bank} className="mt-3">
          <Button onClick={() => ask(
            `Record the company's deposit of ${money(needed)} from its own account into this trust bank account, to pay what online payments cost?`
              + " This only records it; make the deposit at the bank yourself.",
            () => record("/api/payments/partner-fees/cover", { bank_account_id: bank, amount: needed, request_id: requestId() },
              () => `Recorded the company's ${money(needed)}.`),
            "Record the deposit")}>
            Record the company's deposit of {money(needed)}
          </Button>
        </div>
      ))}
      {prompt}
    </Card>
      )}
    </>
  );
}
