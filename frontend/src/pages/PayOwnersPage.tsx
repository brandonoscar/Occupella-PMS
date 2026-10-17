import { Wallet } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { ListBar } from "../components/ListBar";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import { day, money, sumMoney, usDate } from "../money";
import type { OwnerMoney, OwnerPaid, PayoutMethod } from "../payables";
import { paidSummary } from "../payables";
import { requestId } from "../rent";
import { scopeParams, useScope } from "../scope";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// Accounting → Pay owners (ADR 0006, Buildium's owner draw): every owner's property account,
// maybe of one group or property, with what it holds, keeps back and may pay out today. Staff
// choose the owners, change an amount, say how and when it was sent, and pay them all at once:
// every draw or none, each at most what is available. The PMS records the payouts; it sends no
// money and writes no bank file.
export function PayOwnersPage({ me }: { me: Me }) {
  const [scope, setScope] = useScope();
  const [owners, setOwners] = useState<OwnerMoney[] | null>(null);
  const [error, setError] = useState("");
  // The accounts chosen, each with the amount to pay it.
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [method, setMethod] = useState<PayoutMethod>("check");
  const [on, setOn] = useState(day(new Date()));
  const [id, setId] = useState(requestId);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  const { ask, prompt } = useConfirm();

  useEffect(() => {
    const query = new URLSearchParams(scopeParams(scope)).toString();
    api
      .get<OwnerMoney[]>(query ? `/api/payables/owners?${query}` : "/api/payables/owners")
      .then(setOwners)
      .catch((why) => setError(why instanceof ApiError ? why.detail : String(why)));
  }, [scope, runs]);

  const payable = (owners ?? []).filter((o) => Number(o.available) > 0);
  const payouts = Object.entries(chosen).map(([account, amount]) => ({ account, amount }));
  const total = sumMoney(payouts.map((p) => p.amount || "0"));
  const allChosen = payable.length > 0 && payable.every((o) => o.account in chosen);

  function choose(owner: OwnerMoney, yes: boolean) {
    setChosen((was) => {
      const next = { ...was };
      if (yes) next[owner.account] = owner.available;
      else delete next[owner.account];
      return next;
    });
  }

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Pay owners" />
      <ListBar scope={scope} onScope={(next) => { setScope(next); setChosen({}); }} />
      <Said>{said}</Said>
      <Notice tone="error">{refused}</Notice>
      {owners === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : owners.length === 0 ? (
        <EmptyState icon={Wallet} title="No owner has a property account here."
          body="Open one from a property's page, Add owner." />
      ) : (
        <Form className="flex flex-col gap-4" onSubmit={(event) => {
          event.preventDefault();
          const how = method === "EFT" ? "by EFT" : "by check";
          ask(`Pay ${money(total)} to ${payouts.length} owner account${payouts.length === 1 ? "" : "s"} ${how} on ${usDate(on)}?`, async () => {
            if (await record<OwnerPaid[]>("/api/payables/draws/batch",
              { request_id: id, on, method, payouts }, (paid) => paidSummary(paid, method))) {
              setId(requestId());
              setChosen({});
            }
          });
        }}>
          <Wide>
            <table className="w-full text-base">
              <thead>
                <tr>
                  <th className="w-8 text-left">
                    <input type="checkbox" aria-label="Choose every owner with money available"
                      checked={allChosen} disabled={payable.length === 0}
                      onChange={(event) => payable.forEach((o) => choose(o, event.target.checked))} />
                  </th>
                  <th className="text-left">Owner</th>
                  <th className="text-left">Property</th>
                  <th className="text-right">Holds</th>
                  <th className="text-right">Reserve</th>
                  <th className="text-right">Unpaid bills</th>
                  <th className="text-right">Available</th>
                  <th className="text-right">Pay</th>
                </tr>
              </thead>
              <tbody>
                {owners.map((row) => (
                  <tr key={row.account} aria-label={`${row.owner}, ${row.property}`}>
                    <td>
                      <input type="checkbox" aria-label={`Pay ${row.owner}, ${row.property}`}
                        checked={row.account in chosen} disabled={Number(row.available) <= 0}
                        onChange={(event) => choose(row, event.target.checked)} />
                    </td>
                    <td><GoLink href={`/owners/${row.owner_id}`} className={LINK}>{row.owner}</GoLink></td>
                    <td><GoLink href={`/properties/${row.property_id}`} className={LINK}>{row.property}</GoLink></td>
                    <td className="text-right">{money(row.balance)}</td>
                    <td className="text-right">{money(row.reserve)}</td>
                    <td className="text-right">{money(row.unpaid_bills)}</td>
                    <td className="text-right font-medium">{money(row.available)}</td>
                    <td className="text-right">
                      {row.account in chosen && (
                        <input aria-label={`Amount to pay ${row.owner}, ${row.property}`} inputMode="decimal"
                          required value={chosen[row.account]}
                          onChange={(event) => setChosen((was) => ({ ...was, [row.account]: event.target.value }))}
                          className="h-8 w-28 rounded-sm border border-line-strong bg-sunken px-2 text-right text-sm text-ink" />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
          <div className="flex flex-wrap items-end gap-3">
            <Select label="Sent by" id="pay-method" value={method}
              onChange={(event) => setMethod(event.target.value as PayoutMethod)}>
              <option value="check">Check</option>
              <option value="EFT">EFT</option>
            </Select>
            <Field label="Paid out on" id="pay-on" type="date" required value={on}
              onChange={(event) => setOn(event.target.value)} />
            <Button type="submit" disabled={payouts.length === 0}>
              {payouts.length === 0 ? "Choose owners to pay" : `Pay ${money(total)}`}
            </Button>
          </div>
        </Form>
      )}
      {prompt}
    </Shell>
  );
}
