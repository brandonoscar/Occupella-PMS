import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { Card } from "../components/Shell";
import { Wide } from "../components/Wide";
import { Landmark } from "lucide-react";
import type { Breakdown, RegisterLine } from "../banking";
import { day, money, usDate } from "../money";
import type { Paged } from "../rentals";
import { GoLink } from "./NeedsAttention";

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

function detail(failed: unknown): string {
  return failed instanceof ApiError ? failed.detail : String(failed);
}

// Transactions: the account's register over days (the last ninety at first), newest first, a page
// at a time; each move of its cash with what it was, who paid or was paid, its memo, the property
// and the balance after it.
export function RegisterTab({ bankId }: { bankId: string }) {
  const today = new Date();
  const [start, setStart] = useState(day(new Date(today.getTime() - 90 * 86_400_000)));
  const [end, setEnd] = useState(day(today));
  const [number, setNumber] = useState(1);
  const [page, setPage] = useState<Paged<RegisterLine> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setError("");
    const query = new URLSearchParams({ start, end, page: String(number) });
    api.get<Paged<RegisterLine>>(`/api/bank-accounts/${bankId}/register?${query}`)
      .then(setPage)
      .catch((why) => { setPage(null); setError(detail(why)); });
  }, [bankId, start, end, number]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="From" id="register-start" type="date" required value={start}
          onChange={(event) => { setStart(event.target.value); setNumber(1); }} />
        <Field label="To" id="register-end" type="date" required value={end}
          onChange={(event) => { setEnd(event.target.value); setNumber(1); }} />
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
                  <tr key={line.transfer_id}>
                    <td className="whitespace-nowrap">{usDate(line.on)}</td>
                    <td>{line.type}</td>
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
    api.get<Breakdown>(`/api/bank-accounts/${bankId}/breakdown`).then(setFound).catch((why) => setError(detail(why)));
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
