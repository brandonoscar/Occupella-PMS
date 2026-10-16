import { Receipt } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Form } from "../components/Form";
import { ListBar } from "../components/ListBar";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Select } from "../components/Select";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import { day, money, sumMoney, usDate } from "../money";
import type { Bill, BillPaid, OwnerMoney, PayoutMethod } from "../payables";
import { billBadge, billsPaidSummary } from "../payables";
import type { Paged } from "../rentals";
import { scopeParams, useScope } from "../scope";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// What a property's owner account holds against the bills chosen from it: those not set aside yet
// come out of it when paid, so what is left after is what it holds less them.
export type Cash = { account: string; owner: string; property: string; holds: string; chosen: string; after: string };

export function cashOf(bills: Bill[], chosen: Set<string>, owners: OwnerMoney[]): Cash[] {
  const cash: Cash[] = [];
  for (const owner of owners) {
    const picked = bills.filter((b) => chosen.has(b.id) && b.account === owner.account && b.status === "to set aside");
    if (picked.length === 0) continue;
    const taken = sumMoney(picked.map((b) => b.amount));
    cash.push({
      account: owner.account, owner: owner.owner, property: owner.property, holds: owner.balance,
      chosen: taken, after: sumMoney([owner.balance, `-${taken}`]),
    });
  }
  return cash;
}

// Accounting → Bills → Pay bills (ADR 0006, Buildium's): the bills ready to pay (approved, or set
// aside already), maybe of one property group or property, each chosen to pay; beside them what
// each property's owner account holds and what it would hold after. Staff say how and when they
// were sent and pay them all at once: each set aside from its owner's money if it wasn't and paid,
// every one or none. The PMS records the payments; it sends no money and writes no bank file.
export function PayBillsPage({ me }: { me: Me }) {
  const [scope, setScope] = useScope();
  const [bills, setBills] = useState<Bill[] | null>(null);
  const [owners, setOwners] = useState<OwnerMoney[]>([]);
  const [error, setError] = useState("");
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [method, setMethod] = useState<PayoutMethod>("check");
  const [on, setOn] = useState(day(new Date()));
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  const { ask, prompt } = useConfirm();

  useEffect(() => {
    const query = new URLSearchParams({ show: "open", per_page: "200", ...scopeParams(scope) });
    api.get<Paged<Bill>>(`/api/payables/bills?${query}`)
      .then((page) => setBills(page.items.filter((b) => b.status === "to set aside" || b.status === "set aside")))
      .catch((why) => setError(why instanceof ApiError ? why.detail : String(why)));
    const owned = new URLSearchParams(scopeParams(scope)).toString();
    api.get<OwnerMoney[]>(owned ? `/api/payables/owners?${owned}` : "/api/payables/owners").then(setOwners).catch(() => setOwners([]));
  }, [scope, runs]);

  const payable = bills ?? [];
  const picked = payable.filter((b) => chosen.has(b.id));
  const total = sumMoney(picked.map((b) => b.amount));
  const cash = cashOf(payable, chosen, owners);
  const allChosen = payable.length > 0 && payable.every((b) => chosen.has(b.id));

  function choose(ids: string[], yes: boolean) {
    setChosen((was) => {
      const next = new Set(was);
      for (const id of ids) {
        if (yes) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Pay bills" />
      <p className="text-base"><GoLink href="/bills" className={LINK}>Bills</GoLink></p>
      <ListBar scope={scope} onScope={(next) => { setScope(next); setChosen(new Set()); }} />
      <Said>{said}</Said>
      <Notice tone="error">{refused}</Notice>
      {bills === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : payable.length === 0 ? (
        <EmptyState icon={Receipt} title="No bill is ready to pay." />
      ) : (
        <Form className="flex flex-col gap-4" onSubmit={(event) => {
          event.preventDefault();
          const ids = picked.map((b) => b.id);
          ask(`Pay ${money(total)} for ${ids.length} bill${ids.length === 1 ? "" : "s"} by ${method} on ${usDate(on)}?`, async () => {
            if (await record<BillPaid[]>("/api/payables/bills/pay", { on, method, bill_ids: ids },
              (paid) => billsPaidSummary(paid, method))) {
              setChosen(new Set());
            }
          });
        }}>
          <Wide>
            <table className="w-full text-base">
              <thead>
                <tr>
                  <th className="w-8 text-left">
                    <input type="checkbox" aria-label="Choose every bill" checked={allChosen}
                      onChange={(event) => choose(payable.map((b) => b.id), event.target.checked)} />
                  </th>
                  <th className="text-left">Due</th>
                  <th className="text-left">Vendor</th>
                  <th className="text-left">Owner and property</th>
                  <th className="text-left">Ref #</th>
                  <th className="text-right">Amount</th>
                  <th className="text-left">Status</th>
                </tr>
              </thead>
              <tbody>
                {payable.map((bill) => {
                  const badge = billBadge(bill);
                  return (
                    <tr key={bill.id} aria-label={`${bill.vendor} ${bill.reference}`}>
                      <td>
                        <input type="checkbox" aria-label={`Pay ${bill.vendor} ${bill.reference}`} checked={chosen.has(bill.id)}
                          onChange={(event) => choose([bill.id], event.target.checked)} />
                      </td>
                      <td className="whitespace-nowrap">{usDate(bill.due_on)}</td>
                      <td>{bill.vendor}</td>
                      <td>{bill.owner}, {bill.property}</td>
                      <td><GoLink href={`/bills/${bill.id}`} className={LINK}>{bill.reference}</GoLink></td>
                      <td className="text-right">{money(bill.amount)}</td>
                      <td><Badge tone={badge.tone}>{badge.word}</Badge></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Wide>
          {cash.length > 0 && (
            <Card title="Each property's cash">
              <Wide>
                <table className="w-full text-base" aria-label="Each property's cash">
                  <thead>
                    <tr>
                      <th className="text-left">Owner and property</th>
                      <th className="text-right">Holds</th>
                      <th className="text-right">These bills</th>
                      <th className="text-right">Left after</th>
                      <th><span className="sr-only">Short</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {cash.map((c) => (
                      <tr key={c.account} aria-label={`${c.owner}, ${c.property}`}>
                        <td>{c.owner}, {c.property}</td>
                        <td className="text-right">{money(c.holds)}</td>
                        <td className="text-right">{money(c.chosen)}</td>
                        <td className="text-right font-medium">{money(c.after)}</td>
                        <td>{c.after.startsWith("-") && <Badge tone="danger">Short</Badge>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Wide>
            </Card>
          )}
          <div className="flex flex-wrap items-end gap-3">
            <Select label="Sent by" id="bills-method" value={method} onChange={(e) => setMethod(e.target.value as PayoutMethod)}>
              <option value="check">Check</option>
              <option value="EFT">EFT</option>
            </Select>
            <Field label="Paid on" id="bills-on" type="date" required value={on} onChange={(e) => setOn(e.target.value)} />
            <Button type="submit" disabled={picked.length === 0}>
              {picked.length === 0 ? "Choose bills to pay" : `Pay ${money(total)}`}
            </Button>
          </div>
        </Form>
      )}
      {prompt}
    </Shell>
  );
}
