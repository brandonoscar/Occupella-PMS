import { money, usDate } from "../money";
import type { Grouped } from "../statement";
import { describePosting, periodLabel } from "../statement";
import { Wide } from "./Wide";

const CELL = "text-left";
const AMOUNT = "text-right";

// An owner statement on screen (trust_report_owner_statement, grouped): each property's postings
// between its opening and closing balance, then the totals over all properties. The owner reads
// it in the portal; staff on the owner's page.
export function StatementView({ grouped }: { grouped: Grouped }) {
  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm text-ink-muted">{periodLabel(grouped.period)}</p>
      {grouped.properties.map((section) => (
        <Wide key={section.name}>
          <table className="w-full text-base">
            <caption className="pb-2 text-left text-md font-medium">{section.name}</caption>
            <thead className="text-sm text-ink-subtle">
              <tr>
                <th className={CELL}>Date</th>
                <th className={CELL}>Description</th>
                <th className="text-right">Amount</th>
                <th className="text-right">Balance</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-t border-line text-ink-muted">
                <td className={CELL} colSpan={3}>Opening balance</td>
                <td className={AMOUNT}>{money(section.opening)}</td>
              </tr>
              {section.postings.map((posting, n) => {
                const described = describePosting(posting.detail, posting.amount);
                return (
                <tr key={n} className="border-t border-line">
                  <td className={`${CELL} whitespace-nowrap text-ink-muted`}>{usDate(posting.postedOn)}</td>
                  <td className={CELL}>
                    {described.what}
                    {described.with && <span className="text-ink-muted"> · {described.with}</span>}
                  </td>
                  <td className={AMOUNT}>{money(posting.amount)}</td>
                  <td className={AMOUNT}>{money(posting.balance)}</td>
                </tr>
                );
              })}
              <tr className="border-t border-line-strong font-medium">
                <td className={CELL} colSpan={3}>Closing balance</td>
                <td className={AMOUNT}>{money(section.closing)}</td>
              </tr>
            </tbody>
          </table>
        </Wide>
      ))}
      <dl className="grid grid-cols-2 gap-x-8 gap-y-1 border-t border-line-strong pt-4 text-base sm:grid-cols-4">
        {[
          ["Opening", grouped.totals.opening],
          ["Money in", grouped.totals.moneyIn],
          ["Money out", grouped.totals.moneyOut],
          ["Closing", grouped.totals.closing],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="text-sm text-ink-muted">{label}, all properties</dt>
            <dd className="text-md font-medium">{money(value ?? null)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
