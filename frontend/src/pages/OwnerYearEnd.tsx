import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Button, LinkButton } from "../components/Button";
import { Field } from "../components/Field";
import { Notice } from "../components/Notice";
import { Card } from "../components/Shell";
import { money } from "../money";
import type { YearEnd, YearEndData } from "../yearEnd";
import { groupYearEnd, lastYear, partLabel } from "../yearEnd";

// Cells take the table style's padding (less of it on a phone); only their alignment is set here.
const CELL = "text-left";
const AMOUNT = "text-right";

// The signed-in owner's year-end package: the year per property and across them, the rents for
// their 1099-MISC, and the year's whole statement to download. The PMS files nothing.
export function OwnerYearEnd() {
  const [year, setYear] = useState(String(lastYear(new Date())));
  const [shown, setShown] = useState<YearEnd | null>(null);
  const [error, setError] = useState("");

  function load(which: string) {
    setError("");
    setShown(null);
    api
      .get<YearEndData>(`/api/owner/year-end?${new URLSearchParams({ year: which })}`)
      .then((data) => setShown(groupYearEnd(data)))
      .catch((failed) => setError(failed instanceof ApiError ? failed.detail : String(failed)));
  }
  useEffect(() => load(year), []); // last year's first; "Show" loads another

  const download = (format: string) => `/api/owner/year-end/download?${new URLSearchParams({ year, format })}`;
  const statement = `/api/owner/statement/download?${new URLSearchParams({
    start: `${year}-01-01`,
    end: `${year}-12-31`,
    format: "pdf",
  })}`;

  return (
    <Card title="Year-end package">
      <form
        className="mb-6 flex flex-wrap items-end gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          load(year);
        }}
      >
        <Field label="Year" id="year-end-year" type="number" min={1900} max={9998} required value={year}
          onChange={(e) => setYear(e.target.value)} />
        <Button type="submit">Show</Button>
        <div className="flex gap-2">
          <LinkButton href={download("pdf")}>PDF</LinkButton>
          <LinkButton href={download("csv")}>CSV</LinkButton>
          <LinkButton href={download("xlsx")}>Excel</LinkButton>
          <LinkButton href={statement}>The year&apos;s statement</LinkButton>
        </div>
      </form>
      <Notice tone="error">{error}</Notice>
      {shown && (
        <div className="flex flex-col gap-6">
          <p className="text-sm text-ink-subtle">{shown.year}</p>
          {[...shown.properties, { name: "All properties", parts: shown.totals }].map((section) => (
            <table key={section.name} className="w-full text-base">
              <caption className="pb-2 text-left text-md font-medium">{section.name}</caption>
              <tbody>
                {section.parts.map((part) => (
                  <tr key={part.item} className="border-t border-line">
                    <td className={CELL}>{partLabel(part.item)}</td>
                    <td className={AMOUNT}>{money(part.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
          <p className="border-t border-line-strong pt-4 text-base" data-testid="rents">
            Rents for your 1099-MISC (box 1): <span className="font-medium">{money(shown.rents)}</span>
          </p>
          <p className="text-sm text-ink-subtle">
            What tenants paid toward your properties in the year, less what bounced back. This
            summary files nothing.
          </p>
        </div>
      )}
    </Card>
  );
}
