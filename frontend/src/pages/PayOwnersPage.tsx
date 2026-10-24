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
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import { latest } from "../latest";
import { day, money, sumMoney, usDate } from "../money";
import type { OwnerMoney, OwnerPaid, PayoutMethod } from "../payables";
import { paidSummary } from "../payables";
import type { DrawRun } from "../payouts";
import { drawRunLine, methodWord } from "../payouts";
import { requestId } from "../rent";
import { scopeParams, useScope } from "../scope";
import { GoLink } from "./NeedsAttention";
import { PayoutFiles } from "./PayoutFiles";
import { STAFF_NAV } from "./staffNav";
import { useRecord } from "./useRecord";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// How Pay owners sends the payouts chosen: each the way its owner is paid, or all one way.
export type SentBy = PayoutMethod | "each";
// Whether the PMS writes the payouts' files (an ACH file for the bank, checks to print) or only
// records payouts made outside it.
export type Output = "record" | "file";

// The draws prepared on the draw day, as Pay owners chooses them: what each had available then,
// or less if it has less now; one with nothing available now is left out.
export function drawChoices(run: DrawRun): Record<string, string> {
  const chosen: Record<string, string> = {};
  for (const line of run.lines) {
    const now = Number(line.available_now) < Number(line.amount) ? line.available_now : line.amount;
    if (Number(now) > 0) chosen[line.account] = now;
  }
  return chosen;
}

// Accounting → Pay owners (ADR 0006, Buildium's owner draw; ADR 0010 phase 1b): every owner's
// property account, maybe of one group or property, with what it holds, keeps back and may pay
// out today, and how its owner is paid. The draws the nightly run prepared on the company's draw
// day wait at the top until staff use them. Staff choose the owners, change an amount, say how
// and when they were sent, and pay them all at once: every draw or none, each at most what is
// available. The PMS records the payouts and, when asked, writes their ACH file for the bank and
// prints their checks; it sends no money itself.
export function PayOwnersPage({ me }: { me: Me }) {
  const [scope, setScope] = useScope();
  const [owners, setOwners] = useState<OwnerMoney[] | null>(null);
  const [drawRun, setDrawRun] = useState<DrawRun | null>(null);
  const [usingRun, setUsingRun] = useState<string | null>(null);
  const [error, setError] = useState("");
  // The accounts chosen, each with the amount to pay it.
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [sentBy, setSentBy] = useState<SentBy>("each");
  const [output, setOutput] = useState<Output>("record");
  const [on, setOn] = useState(day(new Date()));
  const [id, setId] = useState(requestId);
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));
  const { ask, prompt } = useConfirm();

  useEffect(() => {
    const run = latest();
    const query = new URLSearchParams(scopeParams(scope)).toString();
    api
      .get<OwnerMoney[]>(query ? `/api/payables/owners?${query}` : "/api/payables/owners")
      .then(run.keep(setOwners))
      .catch(run.keep((why) => setError(why instanceof ApiError ? why.detail : String(why))));
    return run.stop;
  }, [scope, runs]);
  // The draws prepared on the draw day are the company's, whatever the list is narrowed to.
  useEffect(() => {
    const run = latest();
    api.get<DrawRun | null>("/api/payouts/draw-runs/waiting")
      .then(run.keep(setDrawRun)).catch(run.keep(() => setDrawRun(null)));
    return run.stop;
  }, [runs]);

  const payable = (owners ?? []).filter((o) => Number(o.available) > 0);
  const methodOf = (account: string): PayoutMethod =>
    sentBy !== "each" ? sentBy : (owners ?? []).find((o) => o.account === account)?.payout_method
      ?? drawRun?.lines.find((l) => l.account === account)?.method ?? "check";
  const payouts = Object.entries(chosen).map(([account, amount]) => ({ account, amount, method: methodOf(account) }));
  const methods = [...new Set(payouts.map((p) => p.method))];
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

  function chooseDraws(run: DrawRun) {
    if (scope.group || scope.property) setScope({});
    setChosen(drawChoices(run));
    setSentBy("each");
    setUsingRun(run.id);
  }

  const how = methods.length === 0 ? "" : methods.map((m) => (m === "EFT" ? "ACH" : "check")).join(" and ");
  const files = output === "file"
    ? [methods.includes("EFT") && "writes the ACH file", methods.includes("check") && "prints the checks"].filter(Boolean).join(" and ")
    : "";

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="Pay owners" />
      <ListBar scope={scope} onScope={(next) => { setScope(next); setChosen({}); setUsingRun(null); }} />
      <Said>{said}</Said>
      <Notice tone="error">{refused}</Notice>
      {drawRun && drawRun.lines.length > 0 && (
        <Card title="Owner draws ready">
          <p className="text-base" data-testid="draws-ready">{drawRunLine(drawRun)}</p>
          <ul className="mt-2 flex flex-col gap-1 text-base" aria-label="Owner draws ready">
            {drawRun.lines.map((l) => (
              <li key={l.account}>
                {l.owner}, {l.property}: {money(l.amount)} by {methodWord(l.method)}
                {Number(l.available_now) < Number(l.amount) && (
                  <span className="text-ink-muted"> (has {money(l.available_now)} available now)</span>
                )}
              </li>
            ))}
          </ul>
          <div className="mt-3">
            {usingRun === drawRun.id ? (
              <p className="text-sm text-ink-muted">Chosen below. Paying them records them as paid; nothing is paid before.</p>
            ) : (
              <Button variant="secondary" onClick={() => chooseDraws(drawRun)}>Use these draws</Button>
            )}
          </div>
        </Card>
      )}
      {owners === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : owners.length === 0 ? (
        <EmptyState icon={Wallet} title="No owner has a property account here."
          body="Open one from a property's page, Add owner." />
      ) : (
        <Form className="flex flex-col gap-4" onSubmit={(event) => {
          event.preventDefault();
          const question = `Pay ${money(total)} to ${payouts.length} owner account${payouts.length === 1 ? "" : "s"} by ${how} on ${usDate(on)}?`
            + (files ? ` The PMS ${files}; the bank moves the money.` : "");
          ask(question, async () => {
            const body = {
              request_id: id, on, method: sentBy === "each" ? "check" : sentBy, payouts, output,
              ...(usingRun ? { draw_run_id: usingRun } : {}),
            };
            if (await record<OwnerPaid[]>("/api/payables/draws/batch", body,
              (paid) => paidSummary(paid, methods) + (output === "file" ? " Their files are under Payout files below." : ""))) {
              setId(requestId());
              setChosen({});
              setUsingRun(null);
            }
          }, "Pay");
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
                  <th className="text-left">Paid by</th>
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
                    <td>{methodWord(row.payout_method)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
          <div className="flex flex-wrap items-end gap-3">
            <Select label="Sent by" id="pay-method" value={sentBy}
              onChange={(event) => setSentBy(event.target.value as SentBy)}>
              <option value="each">Each owner's way</option>
              <option value="check">Check</option>
              <option value="EFT">ACH</option>
            </Select>
            <Select label="Payout file" id="pay-output" value={output}
              onChange={(event) => setOutput(event.target.value as Output)}>
              <option value="record">None: record payouts made outside the PMS</option>
              <option value="file">ACH file and checks to print</option>
            </Select>
            <Field label="Paid out on" id="pay-on" type="date" required value={on}
              onChange={(event) => setOn(event.target.value)} />
            <Button type="submit" disabled={payouts.length === 0}>
              {payouts.length === 0 ? "Choose owners to pay" : `Pay ${money(total)}`}
            </Button>
          </div>
          <p className="text-sm text-ink-subtle">
            The PMS records the payouts, all of them or none. With a payout file it also writes the ACH file to
            upload to the bank and prints the checks; the bank moves the money.
          </p>
        </Form>
      )}
      <PayoutFiles query={{ kind: "owners" }} runs={runs} />
      {prompt}
    </Shell>
  );
}
