import { useEffect, useState } from "react";
import type { Me } from "../api";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Card, Shell } from "../components/Shell";
import { lastMonth, money } from "../money";
import type { StatementData } from "../statement";
import { group } from "../statement";
import { OwnerDocuments } from "./OwnerDocuments";
import { OwnerYearEnd } from "./OwnerYearEnd";

type Property = { account_id: string; property_id: string; name: string; bank: string; balance: string };

const CELL = "px-3 py-2 text-left";
const AMOUNT = "px-3 py-2 text-right font-mono";

export function OwnerHome({ me }: { me: Me }) {
  const [properties, setProperties] = useState<Property[] | null>(null);
  const [period, setPeriod] = useState(lastMonth(new Date()));
  const [statement, setStatement] = useState<StatementData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<Property[]>("/api/owner/properties").then(setProperties).catch(show);
  }, []);

  useEffect(() => {
    load(period);
  }, []); // the first statement only; "Show" loads the next

  function show(failed: unknown) {
    setError(failed instanceof ApiError ? failed.detail : String(failed));
  }

  function load(range: { start: string; end: string }) {
    setError("");
    setStatement(null);
    const query = new URLSearchParams(range).toString();
    api.get<StatementData>(`/api/owner/statement?${query}`).then(setStatement).catch(show);
  }

  const download = (format: string) =>
    `/api/owner/statement/download?${new URLSearchParams({ ...period, format })}`;
  const grouped = statement ? group(statement) : null;

  return (
    <Shell me={me}>
      <h1 className="font-display text-xl font-medium">Your properties</h1>
      <Card title="What each property holds today">
        {properties === null ? (
          <Notice tone="info">Loading</Notice>
        ) : properties.length === 0 ? (
          <Notice tone="info">No properties are held for you in trust.</Notice>
        ) : (
          <table className="w-full text-base">
            <thead className="text-sm text-ink-subtle">
              <tr>
                <th className={CELL}>Property</th>
                <th className={CELL}>Trust account</th>
                <th className="px-3 py-2 text-right">Balance</th>
              </tr>
            </thead>
            <tbody>
              {properties.map((p) => (
                <tr key={p.account_id} className="border-t border-line">
                  <td className={CELL}>{p.name}</td>
                  <td className={`${CELL} text-ink-muted`}>{p.bank}</td>
                  <td className={AMOUNT}>{money(p.balance)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Statement">
        <form
          className="mb-6 flex flex-wrap items-end gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            load(period);
          }}
        >
          <Field label="From" id="start" type="date" value={period.start} required
            onChange={(e) => setPeriod({ ...period, start: e.target.value })} />
          <Field label="To" id="end" type="date" value={period.end} required
            onChange={(e) => setPeriod({ ...period, end: e.target.value })} />
          <Button type="submit">Show</Button>
          <div className="flex gap-2">
            <LinkButton href={download("pdf")}>PDF</LinkButton>
            <LinkButton href={download("csv")}>CSV</LinkButton>
            <LinkButton href={download("xlsx")}>Excel</LinkButton>
          </div>
        </form>
        <Notice tone="error">{error}</Notice>
        {grouped && (
          <div className="flex flex-col gap-6">
            <p className="text-sm text-ink-subtle">{grouped.period}</p>
            {grouped.properties.map((section) => (
              <table key={section.name} className="w-full text-base">
                <caption className="pb-2 text-left text-md font-medium">{section.name}</caption>
                <thead className="text-sm text-ink-subtle">
                  <tr>
                    <th className={CELL}>Date</th>
                    <th className={CELL}>Description</th>
                    <th className="px-3 py-2 text-right">Amount</th>
                    <th className="px-3 py-2 text-right">Balance</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-t border-line text-ink-muted">
                    <td className={CELL} colSpan={3}>Opening balance</td>
                    <td className={AMOUNT}>{money(section.opening)}</td>
                  </tr>
                  {section.postings.map((posting, n) => (
                    <tr key={n} className="border-t border-line">
                      <td className={`${CELL} font-mono text-sm`}>{posting.postedOn}</td>
                      <td className={CELL}>{posting.detail}</td>
                      <td className={AMOUNT}>{money(posting.amount)}</td>
                      <td className={AMOUNT}>{money(posting.balance)}</td>
                    </tr>
                  ))}
                  <tr className="border-t border-line-strong font-medium">
                    <td className={CELL} colSpan={3}>Closing balance</td>
                    <td className={AMOUNT}>{money(section.closing)}</td>
                  </tr>
                </tbody>
              </table>
            ))}
            <dl className="grid grid-cols-2 gap-x-8 gap-y-1 border-t border-line-strong pt-4 text-base sm:grid-cols-4">
              {[
                ["Opening", grouped.totals.opening],
                ["Money in", grouped.totals.moneyIn],
                ["Money out", grouped.totals.moneyOut],
                ["Closing", grouped.totals.closing],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-sm text-ink-subtle">{label}, all properties</dt>
                  <dd className="font-mono">{money(value ?? null)}</dd>
                </div>
              ))}
            </dl>
          </div>
        )}
      </Card>
      <OwnerYearEnd />
      <OwnerDocuments />
    </Shell>
  );
}
