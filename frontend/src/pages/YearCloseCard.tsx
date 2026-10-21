import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { useConfirm } from "../components/Confirm";
import { Notice } from "../components/Notice";
import { Said } from "../components/Said";
import { Card } from "../components/Shell";
import { Wide } from "../components/Wide";
import { usDate } from "../money";
import { GoLink } from "./NeedsAttention";
import { useRecord } from "./useRecord";

export type YearClose = {
  closed: { year: number; closed_on: string; closed_by: string }[];
  closed_until: string | null;
  next: {
    year: number;
    accounts: { id: string; name: string; approved_through: string | null; in_the_year: boolean; ready: boolean }[];
    waiting_entries: number;
    ready: boolean;
  } | null;
};

const LINK = "font-medium text-iris hover:text-iris-hover hover:underline";

// Accounting → Banking, Year close (migration 0106, q116): last year closes once every trust bank
// account it touched is approved through December 31 and no journal entry dated in it waits.
// Closed, nothing is charged or billed with a day in it or before; it never reopens.
export function YearCloseCard({ refresh }: { refresh?: () => void }) {
  const [state, setState] = useState<YearClose | null>(null);
  const [error, setError] = useState("");
  const [runs, setRuns] = useState(0);
  const { said, error: refused, record } = useRecord(() => {
    setRuns((n) => n + 1);
    refresh?.();
  });
  const { ask, prompt } = useConfirm();
  useEffect(() => {
    api.get<YearClose>("/api/year-close").then(setState)
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }, [runs]);
  if (state === null) return <Card title="Year close"><Notice tone={error ? "error" : "info"}>{error || "Loading"}</Notice></Card>;
  const next = state.next;
  return (
    <Card title="Year close">
      {state.closed_until && (
        <p className="mb-3 text-base text-ink" data-testid="closed-until">
          Closed through {usDate(state.closed_until)}: nothing dated then or before is charged or billed.
        </p>
      )}
      {state.closed.length > 0 && (
        <ul className="mb-4 flex flex-col gap-1 text-sm text-ink-muted" aria-label="Years closed">
          {state.closed.map((c) => (
            <li key={c.year}>{c.year}, closed on {usDate(c.closed_on)} by {c.closed_by}</li>
          ))}
        </ul>
      )}
      {next === null ? (
        <p className="text-base text-ink-muted">Nothing more to close until this year ends.</p>
      ) : (
        <>
          <p className="mb-3 text-base text-ink-muted">
            {next.year} closes once every trust bank account it touched is approved through{" "}
            {usDate(`${next.year}-12-31`)}. Then nothing dated in {next.year} or before can be charged
            or billed, and it never reopens: a late fix is dated in the open year.
          </p>
          <Wide>
            <table className="mb-3 w-full text-base" aria-label={`Ready to close ${next.year}`}>
              <thead>
                <tr>
                  <th className="text-left">Trust bank account</th>
                  <th className="text-left">Approved through</th>
                  <th className="text-left">State</th>
                </tr>
              </thead>
              <tbody>
                {next.accounts.map((a) => (
                  <tr key={a.id} aria-label={`${a.name}, ${next.year}`}>
                    <td><GoLink href={`/bank-accounts/${a.id}`} className={LINK}>{a.name}</GoLink></td>
                    <td>{a.approved_through ? usDate(a.approved_through) : <span className="text-ink-subtle">Not yet</span>}</td>
                    <td>
                      {!a.in_the_year ? <Badge>Not in {next.year}</Badge>
                        : a.ready ? <Badge tone="positive">Ready</Badge>
                        : <Badge tone="caution">Approve December</Badge>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Wide>
          {next.waiting_entries > 0 && (
            <p className="mb-3 text-base text-ink" data-testid="waiting-entries">
              {next.waiting_entries === 1 ? "1 journal entry" : `${next.waiting_entries} journal entries`} dated
              in {next.year} or before {next.waiting_entries === 1 ? "waits" : "wait"} for a decision:{" "}
              <GoLink href="/journal" className={LINK}>post or decline</GoLink> first.
            </p>
          )}
          <Button disabled={!next.ready} onClick={() => ask(
            `Close ${next.year}? Nothing dated in ${next.year} or before can be charged or billed after this, and it can't be reopened.`,
            () => record("/api/year-close", { year: next.year }, () => `Closed ${next.year}.`),
            `Close ${next.year}`,
          )}>Close {next.year}</Button>
        </>
      )}
      <Said>{said}</Said>
      <Notice tone="error">{refused}</Notice>
      {prompt}
    </Card>
  );
}
