import { BookOpen } from "lucide-react";
import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { LinkButton } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Pager } from "../components/Pager";
import { Card, PageHeader, Shell } from "../components/Shell";
import { Wide } from "../components/Wide";
import type { LedgerAccount } from "../generalLedger";
import { ledgerHref, monthToDate, nonZero } from "../generalLedger";
import { day, money } from "../money";
import type { Paged } from "../rentals";
import { GoLink } from "./NeedsAttention";
import { STAFF_NAV } from "./staffNav";

const BUTTON =
  "inline-flex h-9 items-center rounded-sm bg-iris px-3.5 text-base font-medium text-canvas hover:bg-iris-hover";

// Accounting → General ledger (ADR 0005): for the days chosen, each account's prior balance, its
// entries with a running balance, and its totals, as Buildium lays it out; a page of accounts at
// a time, found by account or bank. Cash basis. "Journal entry" corrects the books.
export function GeneralLedgerPage({ me }: { me: Me }) {
  const start = monthToDate(day(new Date()));
  const [from, setFrom] = useState(start.from);
  const [to, setTo] = useState(start.to);
  const [q, setQ] = useState("");
  const [number, setNumber] = useState(1);
  const [page, setPage] = useState<Paged<LedgerAccount> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!from || !to) return;
    setError("");
    const query = new URLSearchParams({ from, to, page: String(number) });
    if (q.trim()) query.set("q", q.trim());
    api
      .get<Paged<LedgerAccount>>(`/api/general-ledger?${query}`)
      .then(setPage)
      .catch((failed) => {
        setPage(null);
        setError(failed instanceof ApiError ? failed.detail : String(failed));
      });
  }, [from, to, q, number]);

  return (
    <Shell me={me} nav={STAFF_NAV}>
      <PageHeader title="General ledger" actions={<GoLink href="/journal" className={BUTTON}>Journal entry</GoLink>} />
      <div className="flex flex-wrap items-end gap-4">
        <Field label="From" id="ledger-from" type="date" required value={from}
          onChange={(event) => { setFrom(event.target.value); setNumber(1); }} />
        <Field label="To" id="ledger-to" type="date" required value={to}
          onChange={(event) => { setTo(event.target.value); setNumber(1); }} />
        <Field label="Find an account" id="ledger-find" type="search" value={q}
          onChange={(event) => { setQ(event.target.value); setNumber(1); }} />
        <div className="flex gap-2 pb-0.5">
          <LinkButton href={ledgerHref(from, to, "pdf")}>PDF</LinkButton>
          <LinkButton href={ledgerHref(from, to, "csv")}>CSV</LinkButton>
          <LinkButton href={ledgerHref(from, to, "xlsx")}>Excel</LinkButton>
        </div>
      </div>
      <Notice tone="error">{error}</Notice>
      {page === null ? (
        !error && <Notice tone="info">Loading</Notice>
      ) : page.items.length === 0 ? (
        <EmptyState icon={BookOpen} title="No accounts found." />
      ) : (
        <>
          {page.items.map((account) => (
            <AccountCard key={`${account.bank} ${account.account}`} account={account} />
          ))}
          <Pager page={page} onPage={setNumber} />
        </>
      )}
    </Shell>
  );
}

function AccountCard({ account }: { account: LedgerAccount }) {
  return (
    <Card title={account.account}>
      <p className="mb-2 text-sm text-ink-muted">{account.bank}</p>
      <Wide>
        <table className="w-full text-base" aria-label={`${account.account}, ${account.bank}`}>
          <thead>
            <tr>
              <th className="text-left">Date</th>
              <th className="text-left">Description</th>
              <th className="text-right">Debit</th>
              <th className="text-right">Credit</th>
              <th className="text-right">Balance</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td colSpan={4} className="text-ink-muted">Prior balance</td>
              <td className="text-right">{money(account.prior_balance)}</td>
            </tr>
            {account.entries.map((entry, n) => (
              <tr key={n}>
                <td className="whitespace-nowrap">{entry.posted_at.slice(0, 10)}</td>
                <td>{entry.description}</td>
                <td className="text-right">{nonZero(entry.debit) && money(entry.debit)}</td>
                <td className="text-right">{nonZero(entry.credit) && money(entry.credit)}</td>
                <td className="text-right">{money(entry.balance)}</td>
              </tr>
            ))}
            <tr className="font-medium">
              <td colSpan={2}>Total</td>
              <td className="text-right">{money(account.debits)}</td>
              <td className="text-right">{money(account.credits)}</td>
              <td className="text-right">{money(account.balance)}</td>
            </tr>
          </tbody>
        </table>
      </Wide>
    </Card>
  );
}
