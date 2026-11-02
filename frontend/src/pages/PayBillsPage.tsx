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
import { latest } from "../latest";
import { day, money, sumMoney, usDate } from "../money";
import type { Bill, BillPaid, OwnerMoney, PayoutMethod } from "../payables";
import { billBadge, billsPaidSummary } from "../payables";
import type { Paged } from "../rentals";
import { requestId } from "../rent";
import { scopeParams, useScope } from "../scope";
import { GoLink } from "./NeedsAttention";
import type { Output } from "./PayOwnersPage";
import { PayoutFiles } from "./PayoutFiles";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// What a property's owner account holds against the bills chosen from it: those whose money
// hasn't left it yet come out of it when paid, each with its markup, so what is left after is
// what it holds less them.
export type Cash = { account: string; owner: string; property: string; holds: string; chosen: string; after: string };

export function cashOf(bills: Bill[], chosen: Set<string>, owners: OwnerMoney[]): Cash[] {
  const cash: Cash[] = [];
  for (const owner of owners) {
    const picked = bills.filter((b) => chosen.has(b.id) && b.account === owner.account && b.held_since === null);
    if (picked.length === 0) continue;
    const taken = sumMoney(picked.flatMap((b) => [b.amount, b.markup ?? "0"]));
    cash.push({
      account: owner.account, owner: owner.owner, property: owner.property, holds: owner.balance,
      chosen: taken, after: sumMoney([owner.balance, `-${taken}`]),
    });
  }
  return cash;
}

// Accounting → Bills → Pay bills (ADR 0006, Buildium's; ADR 0010): the bills ready to pay (or
// partly paid), maybe of one property group or property, each chosen to pay what is left of it;
// beside them what each property's owner account holds and what it would hold after; below them
// the bills that can't be paid yet and why (short, or waiting for the owner). Staff say how and
// when they were sent, with each check's number, and pay them all at once, every one or none. The
// PMS records the payments and, when asked, writes their ACH file for the bank or prints their
// checks, one to each vendor, numbered on from the account's next; it sends no money itself.
export function PayBillsPage({ me }: { me: Me }) {
  const [scope, setScope] = useScope();
  const [bills, setBills] = useState<Bill[] | null>(null);
  const [owners, setOwners] = useState<OwnerMoney[]>([]);
  const [error, setError] = useState("");
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [method, setMethod] = useState<PayoutMethod>("check");
  const [output, setOutput] = useState<Output>("record");
  const [id, setId] = useState(requestId);
  const [on, setOn] = useState(day(new Date()));
  const [numbers, setNumbers] = useState<Record<string, string>>({});
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  const { ask, prompt } = useConfirm();

  useEffect(() => {
    const run = latest();
    const query = new URLSearchParams({ show: "open", per_page: "200", ...scopeParams(scope) });
    api.get<Paged<Bill>>(`/api/payables/bills?${query}`)
      .then(run.keep((page) => setBills(page.items)))
      .catch(run.keep((why) => setError(why instanceof ApiError ? why.detail : String(why))));
    const owned = new URLSearchParams(scopeParams(scope)).toString();
    api.get<OwnerMoney[]>(owned ? `/api/payables/owners?${owned}` : "/api/payables/owners")
      .then(run.keep(setOwners)).catch(run.keep(() => setOwners([])));
    return run.stop;
  }, [scope, runs]);

  const payable = (bills ?? []).filter((b) => b.status === "ready to pay" || b.status === "partly paid");
  // Not payable yet, and why: the owner's balance is short, or the bill waits for their approval.
  const blocked = (bills ?? []).filter((b) => b.status === "short" || b.status === "waiting for owner");
  const picked = payable.filter((b) => chosen.has(b.id));
  const total = sumMoney(picked.map((b) => b.left));
  const how = method === "check" ? "check" : "ACH";
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
        <>
          <EmptyState icon={Receipt} title="No bill is ready to pay." />
          <Blocked bills={blocked} />
        </>
      ) : (
        <Form className="flex flex-col gap-4" onSubmit={(event) => {
          event.preventDefault();
          const ids = picked.map((b) => b.id);
          const checks = method === "check" && output === "record"
            ? Object.fromEntries(ids.filter((bill) => numbers[bill]).map((bill) => [bill, numbers[bill]]))
            : {};
          const file = output === "file"
            ? ` The PMS ${method === "EFT" ? "writes the ACH file" : "prints the checks"}; the bank moves the money.`
            : "";
          ask(`Pay ${money(total)} for ${ids.length} bill${ids.length === 1 ? "" : "s"} by ${how} on ${usDate(on)}? It comes out of each property's money in trust.${file}`, async () => {
            const body = { on, method, bill_ids: ids, check_numbers: checks, output, request_id: id };
            if (await record<BillPaid[]>("/api/payables/bills/pay", body,
              (paid) => billsPaidSummary(paid, method) + (output === "file" ? " Their file is under Payout files below." : ""))) {
              setChosen(new Set());
              setNumbers({});
              setId(requestId());
            }
          }, "Pay");
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
                  <th className="text-right">To pay</th>
                  <th className="text-left">Status</th>
                  {method === "check" && output === "record" && <th className="text-left">Check #</th>}
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
                      <td className="text-right">
                        {money(bill.left)}
                        {bill.markup !== null && bill.held_since === null && (
                          <span className="block text-sm text-ink-muted">+ {money(bill.markup)} markup</span>
                        )}
                      </td>
                      <td><Badge tone={badge.tone}>{badge.word}</Badge></td>
                      {method === "check" && output === "record" && (
                        <td>
                          <input aria-label={`Check number, ${bill.vendor} ${bill.reference}`} inputMode="numeric"
                            maxLength={15} pattern="[0-9]*" value={numbers[bill.id] ?? ""}
                            onChange={(e) => setNumbers((was) => ({ ...was, [bill.id]: e.target.value }))}
                            className="h-8 w-24 rounded-sm border border-line-strong bg-sunken px-2 text-sm text-ink" />
                        </td>
                      )}
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
              <option value="EFT">ACH</option>
            </Select>
            <Select label="Payout file" id="bills-output" value={output} onChange={(e) => setOutput(e.target.value as Output)}>
              <option value="record">None: record payments made outside the PMS</option>
              <option value="file">{method === "EFT" ? "ACH file" : "Checks to print"}</option>
            </Select>
            <Field label="Paid on" id="bills-on" type="date" required value={on} onChange={(e) => setOn(e.target.value)} />
            <Button type="submit" disabled={picked.length === 0}
              says={output === "file"
                ? `Records them paid and ${method === "EFT" ? "writes the ACH file to upload" : "prints the checks to mail"}. The PMS sends no money; the bank does.`
                : "Records them paid, all or none, from each owner's money in trust. The PMS sends no money."}>
              {picked.length === 0 ? "Choose bills to pay" : `Pay ${money(total)}`}
            </Button>
          </div>
          <p className="text-sm text-ink-subtle">
            Records what was paid, all of them or none. With a payout file the PMS also writes the ACH file to upload
            to the bank, or prints the checks, numbered on from the account's next; the bank moves the money.
          </p>
          <Blocked bills={blocked} />
        </Form>
      )}
      <PayoutFiles query={{ kind: "bills" }} runs={runs} />
      {prompt}
    </Shell>
  );
}

// The bills that can't be paid yet, each with why.
function Blocked({ bills }: { bills: Bill[] }) {
  if (bills.length === 0) return null;
  return (
    <Card title="Not ready to pay">
      <ul className="flex flex-col gap-1 text-base" aria-label="Not ready to pay">
        {bills.map((bill) => (
          <li key={bill.id}>
            <GoLink href={`/bills/${bill.id}`} className={LINK}>{bill.vendor} {bill.reference}</GoLink>, {money(bill.amount)}:{" "}
            {bill.status === "short"
              ? `${bill.owner}'s balance is ${money(bill.short ?? "0")} short; they need to send ${money(bill.needed ?? "0")}.`
              : `waits for ${bill.owner}'s approval.`}
          </li>
        ))}
      </ul>
    </Card>
  );
}
