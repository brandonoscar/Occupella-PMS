import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { Card } from "../components/Shell";
import { Wide } from "../components/Wide";
import { Landmark } from "lucide-react";
import type { BankDeposit, Breakdown, RegisterLine, Undeposited } from "../banking";
import { Badge } from "../components/Badge";
import { Button, LinkButton } from "../components/Button";
import { Form } from "../components/Form";
import { Said } from "../components/Said";
import { UpdatedStamp, useAgainOnFocus } from "../components/Updated";
import { useRecord } from "./useRecord";
import { latest } from "../latest";
import { day, money, usDate } from "../money";
import type { Paged } from "../rentals";
import { navigate } from "../router";
import { GoLink } from "./NeedsAttention";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// Transactions: the account's register over days (the last ninety at first), newest first, a page
// at a time; each move of its cash with what it was, who paid or was paid, its memo, the property
// and the balance after it, opening its lease, bill, owner or tenant (ADR 0010 phase 3, part M).
// Export CSV downloads every move of the days, oldest first. It is read again when the browser tab
// comes back into view, and says when it was.
export function RegisterTab({ bankId }: { bankId: string }) {
  const today = new Date();
  const [start, setStart] = useState(day(new Date(today.getTime() - 90 * 86_400_000)));
  const [end, setEnd] = useState(day(today));
  const [number, setNumber] = useState(1);
  const [page, setPage] = useState<Paged<RegisterLine> | null>(null);
  const [error, setError] = useState("");
  const [at, setAt] = useState<Date | null>(null);
  const again = useAgainOnFocus();

  useEffect(() => {
    const run = latest();
    setError("");
    const query = new URLSearchParams({ start, end, page: String(number) });
    api.get<Paged<RegisterLine>>(`/api/bank-accounts/${bankId}/register?${query}`)
      .then(run.keep((found: Paged<RegisterLine>) => { setPage(found); setAt(new Date()); }))
      .catch(run.keep((why) => { setPage(null); setError(detail(why)); }));
    return run.stop;
  }, [bankId, start, end, number, again]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="From" id="register-start" type="date" required value={start}
          onChange={(event) => { setStart(event.target.value); setNumber(1); }} />
        <Field label="To" id="register-end" type="date" required value={end}
          onChange={(event) => { setEnd(event.target.value); setNumber(1); }} />
        <LinkButton href={`/api/bank-accounts/${bankId}/register?${new URLSearchParams({ start, end, format: "csv" })}`}>
          Export CSV
        </LinkButton>
        <UpdatedStamp at={at} />
      </div>
      {page === null ? (
        <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>
      ) : page.items.length === 0 ? (
        <EmptyState icon={Landmark} title="No money moved in these days." />
      ) : (
        <>
          <Wide>
            <table className="w-full text-base" aria-label="Register">
              <thead>
                <tr>
                  <th className="text-left">Date</th>
                  <th className="text-left">Type</th>
                  <th className="text-left">Payer or payee</th>
                  <th className="text-left">Memo</th>
                  <th className="text-left">Property</th>
                  <th className="text-right">Deposit</th>
                  <th className="text-right">Withdrawal</th>
                  <th className="text-right">Balance</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((line) => (
                  <tr key={line.transfer_id} className={line.opens ? "cursor-pointer hover:bg-raised" : undefined}
                    onClick={(event) => {
                      // A click on the type's own link is the link's.
                      if (line.opens && !(event.target as HTMLElement).closest("a")) navigate(line.opens);
                    }}>
                    <td className="whitespace-nowrap">{usDate(line.on)}</td>
                    <td>{line.opens ? <GoLink href={line.opens} className={LINK}>{line.type}</GoLink> : line.type}</td>
                    <td>{line.party}</td>
                    <td className="text-ink-muted">{line.memo}</td>
                    <td className="text-ink-muted">{line.property}</td>
                    <td className="text-right">{money(line.deposit)}</td>
                    <td className="text-right">{money(line.withdrawal)}</td>
                    <td className="text-right">{money(line.balance)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
          <Pager page={page} onPage={setNumber} />
        </>
      )}
    </div>
  );
}

// Balance breakdown: what the account holds today, by kind (together its book cash), and each
// owner's property account in it with what it holds, keeps back and may pay out.
export function BreakdownTab({ bankId }: { bankId: string }) {
  const [found, setFound] = useState<Breakdown | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const run = latest();
    api.get<Breakdown>(`/api/bank-accounts/${bankId}/breakdown`)
      .then(run.keep(setFound)).catch(run.keep((why) => setError(detail(why))));
    return run.stop;
  }, [bankId]);
  if (found === null) return <Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice>;
  return (
    <div className="flex flex-col gap-6">
      <Card title="What it holds">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-base md:grid-cols-3" aria-label="What it holds">
          {found.held.map((held) => (
            <div key={held.kind}>
              <dt className="text-sm text-ink-muted">{held.label}</dt>
              <dd>{money(held.amount)}</dd>
            </div>
          ))}
          <div>
            <dt className="text-sm text-ink-muted">Book cash</dt>
            <dd className="font-medium" data-testid="breakdown-cash">{money(found.book_cash)}</dd>
          </div>
        </dl>
      </Card>
      {found.owners.length > 0 && (
        <Wide>
          <table className="w-full text-base" aria-label="By owner">
            <thead>
              <tr>
                <th className="text-left">Owner</th>
                <th className="text-left">Property</th>
                <th className="text-right">Held</th>
                <th className="text-right">Reserve</th>
                <th className="text-right">Unpaid bills</th>
                <th className="text-right">Available</th>
              </tr>
            </thead>
            <tbody>
              {found.owners.map((owner) => (
                <tr key={owner.account} aria-label={`${owner.owner}, ${owner.property}`}>
                  <td><GoLink href={`/owners/${owner.owner_id}`} className={LINK}>{owner.owner}</GoLink></td>
                  <td><GoLink href={`/properties/${owner.property_id}`} className={LINK}>{owner.property}</GoLink></td>
                  <td className="text-right">{money(owner.balance)}</td>
                  <td className="text-right">{money(owner.reserve)}</td>
                  <td className="text-right">{money(owner.unpaid_bills)}</td>
                  <td className="text-right font-medium">{money(owner.available)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Wide>
      )}
    </div>
  );
}

// Deposits (the founder's review, q025): what came into the account and is in no deposit yet,
// each payment once; staff tick what they take to the bank together and record the deposit with
// its day, then print its slip. At month-end the deposit's one bank line matches all of it.
export function DepositsTab({ bankId }: { bankId: string }) {
  const [waiting, setWaiting] = useState<Undeposited[] | null>(null);
  const [done, setDone] = useState<BankDeposit[] | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [on, setOn] = useState(day(new Date()));
  const [error, setError] = useState("");
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => setRuns((n) => n + 1));

  useEffect(() => {
    const run = latest();
    api.get<Undeposited[]>(`/api/bank-accounts/${bankId}/undeposited`)
      .then(run.keep(setWaiting)).catch(run.keep((why) => setError(detail(why))));
    api.get<BankDeposit[]>(`/api/bank-accounts/${bankId}/deposits`)
      .then(run.keep(setDone)).catch(run.keep((why) => setError(detail(why))));
    return run.stop;
  }, [bankId, runs]);

  const picked = (waiting ?? []).filter((w) => chosen.includes(w.id));
  const total = picked.reduce((cents, w) => cents + Math.round(Number(w.amount) * 100), 0);
  const totalText = (total / 100).toFixed(2);
  const toggle = (id: string) => setChosen(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id]);

  return (
    <div className="flex flex-col gap-6">
      <Said>{said}</Said>
      <Notice tone="error">{error || refused}</Notice>
      <Card title="Not deposited yet">
        {waiting === null ? (
          <Notice tone="info">Loading</Notice>
        ) : waiting.length === 0 ? (
          <p className="text-sm text-ink-subtle">Everything that came in is in a deposit.</p>
        ) : (
          <Form className="flex flex-col gap-3" onSubmit={async (event) => {
            event.preventDefault();
            const body = { deposited_on: on, transfer_ids: picked.flatMap((w) => w.transfer_ids) };
            if (await record<BankDeposit>(`/api/bank-accounts/${bankId}/deposits`, body,
              (made) => `Deposit of ${money(made.total)} recorded for ${usDate(made.deposited_on)}.`)) {
              setChosen([]);
            }
          }}>
            <ul className="flex flex-col divide-y divide-line rounded-md border border-card-edge text-sm" aria-label="Not deposited yet">
              {waiting.map((w) => (
                <li key={w.id}>
                  <label className="flex items-center gap-2.5 px-3 py-2 hover:bg-raised">
                    <input type="checkbox" checked={chosen.includes(w.id)} onChange={() => toggle(w.id)} />
                    {`${usDate(w.on)} ${w.type}${w.party ? `, ${w.party}` : ""}${w.memo ? ` (${w.memo})` : ""}: ${money(w.amount)}`}
                  </label>
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Deposited on" id={`deposited-on-${bankId}`} type="date" required value={on}
                onChange={(event) => setOn(event.target.value)} />
              <Button type="submit" disabled={picked.length === 0}>
                {picked.length === 0 ? "Record deposit" : `Record deposit of ${money(totalText)}`}
              </Button>
            </div>
          </Form>
        )}
      </Card>
      <Card title="Deposits">
        {done === null ? (
          <Notice tone="info">Loading</Notice>
        ) : done.length === 0 ? (
          <p className="text-sm text-ink-subtle">No deposit recorded yet.</p>
        ) : (
          <Wide>
            <table className="w-full text-base" aria-label="Deposits">
              <thead>
                <tr>
                  <th className="text-left">Deposited</th>
                  <th className="text-right">Items</th>
                  <th className="text-right">Total</th>
                  <th className="text-left">Recorded by</th>
                  <th className="text-left">Month-end</th>
                  <th><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {done.map((d) => (
                  <tr key={d.id}>
                    <td className="whitespace-nowrap">{usDate(d.deposited_on)}</td>
                    <td className="text-right">{d.items}</td>
                    <td className="text-right">{money(d.total)}</td>
                    <td className="text-ink-muted">{d.recorded_by}</td>
                    <td>{d.matched ? <Badge tone="positive">Matched</Badge> : <Badge tone="info">Not matched yet</Badge>}</td>
                    <td className="text-right">
                      <a href={`/api/bank-accounts/deposits/${d.id}/slip`} className={LINK}>Slip (PDF)</a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
        )}
      </Card>
    </div>
  );
}
