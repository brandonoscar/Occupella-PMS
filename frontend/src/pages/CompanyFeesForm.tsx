import { useState } from "react";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { day, money, usDate } from "../money";
import type { CompanyFees } from "../payables";
import { companyFeesLabel } from "../payables";
import { requestId } from "../rent";
import type { Recorder } from "./useRecord";

// Transfer company fees (Banking, an operating trust bank account's ⋯ menu): the company's
// earned fees still in the account, taken out to the company's own account, all or some, after
// staff confirm, naming the amount. Left in trust they mix the company's money with its clients'.
export function CompanyFeesForm({ fees, record }: { fees: CompanyFees; record: Recorder }) {
  const [id, setId] = useState(requestId);
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState(day(new Date()));
  const { ask, prompt } = useConfirm();
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-ink-muted" data-testid="company-fees">{companyFeesLabel(fees)}</p>
      {fees.since !== null && (
        <Form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            const body = { request_id: id, bank_account_id: fees.bank_account_id, amount: amount || null, on };
            const taken = (out: { amount: string }) => `${money(out.amount)} of the company's fees taken out.`;
            const paying = amount ? money(amount) : `all ${money(fees.held)}`;
            ask(`Take ${paying} of the company's fees out of ${fees.bank_account} on ${usDate(on)}?`, async () => {
              if (await record("/api/payables/company-fees", body, taken)) {
                setId(requestId());
                setAmount("");
              }
            });
          }}
        >
          <Field label="Amount (leave empty for all)" id={`company-fees-${fees.bank_account_id}`}
            inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} />
          <Field label="Paid out on" id={`company-fees-on-${fees.bank_account_id}`} type="date"
            required value={on} onChange={(event) => setOn(event.target.value)} />
          <div><Button type="submit">Take out company fees</Button></div>
        </Form>
      )}
      {prompt}
    </div>
  );
}
